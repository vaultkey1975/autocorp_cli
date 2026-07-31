"""GPU, database, and parallelism safety decisions.

Classifies selected tests for GPU/database/network involvement and decides
whether pytest-xdist parallelism is safe to use. Never kills processes,
never runs GPU inference, and never assumes parallel execution is safe.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

_UNSAFE_PARALLEL_SIGNALS = (
    "sqlite3.connect", ".db\"", ".db'", "fixed port", "8000", "localhost:",
    "os.environ[", "singleton", "global ", "tmp/autocorp", "/tmp/",
)


@dataclass
class TestClassification:
    gpu: bool = False
    database: bool = False
    network: bool = False
    reasons: list[str] = None

    def __post_init__(self):
        if self.reasons is None:
            self.reasons = []

    def to_dict(self) -> dict[str, Any]:
        return {"gpu": self.gpu, "database": self.database, "network": self.network, "reasons": self.reasons}


def classify_test(text_lower: str, config) -> TestClassification:
    cls = TestClassification()
    gpu_hits = [k for k in _gpu_keywords() if k in text_lower]
    db_hits = [k for k in _db_keywords() if k in text_lower]
    net_hits = [k for k in _net_keywords() if k in text_lower]
    if gpu_hits:
        cls.gpu = True
        cls.reasons.append(f"GPU keyword(s): {', '.join(gpu_hits[:3])}")
    if db_hits:
        cls.database = True
        cls.reasons.append(f"database keyword(s): {', '.join(db_hits[:3])}")
    if net_hits:
        cls.network = True
        cls.reasons.append(f"network keyword(s): {', '.join(net_hits[:3])}")
    return cls


def _gpu_keywords():
    return ("cuda", "torch.cuda", "chatterbox", "gpu", "nvidia", "ollama", "video_lab", "diffusers")


def _db_keywords():
    return ("sqlite3", ".db\"", ".db'", "postgres", "psycopg2", "mysql", "sqlalchemy", "migrations")


def _net_keywords():
    return ("requests.get", "requests.post", "http://", "https://", "urllib", "socket.socket", "aiohttp")


def decide_parallelism(
    *,
    has_xdist: bool,
    selected_texts: dict[str, str],
    cpu_count: int,
) -> dict[str, Any]:
    if not has_xdist:
        return {
            "enabled": False,
            "workers": 1,
            "reason": "pytest-xdist is not installed in the target environment",
        }
    unsafe_hits = []
    for test_rel, text in selected_texts.items():
        lowered = text.lower()
        hits = [sig for sig in _UNSAFE_PARALLEL_SIGNALS if sig in lowered]
        if hits:
            unsafe_hits.append((test_rel, hits))
    if unsafe_hits:
        examples = ", ".join(f"{rel} ({hits[0]})" for rel, hits in unsafe_hits[:3])
        return {
            "enabled": False,
            "workers": 1,
            "reason": f"selected tests share unsafe state (SQLite paths, fixed ports, or globals): {examples}",
        }
    workers = max(1, min(4, (cpu_count or 1) // 2 or 1))
    if workers <= 1:
        return {
            "enabled": False,
            "workers": 1,
            "reason": "not enough CPU headroom to safely parallelize (needs at least 2 logical CPUs)",
        }
    return {
        "enabled": True,
        "workers": workers,
        "reason": f"no shared-state signals detected across {len(selected_texts)} selected test file(s); "
                   f"using a conservative worker count based on CPU count",
    }


def gpu_deferral_note(classified: dict[str, TestClassification]) -> list[str]:
    deferred = [rel for rel, c in classified.items() if c.gpu]
    if not deferred:
        return []
    return [
        f"Deferred GPU-involving test to FULL/manual verification: {rel}" for rel in deferred
    ]


@dataclass
class ProductionDbGuardResult:
    checked: bool
    path: str | None
    sha256_before: str | None
    size_before: int | None
    sha256_after: str | None = None
    size_after: int | None = None

    @property
    def changed_unexpectedly(self) -> bool:
        if not self.checked or self.sha256_after is None:
            return False
        return self.sha256_after != self.sha256_before

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "path": self.path,
            "sha256_before": self.sha256_before,
            "size_before": self.size_before,
            "sha256_after": self.sha256_after,
            "size_after": self.size_after,
            "changed_unexpectedly": self.changed_unexpectedly,
        }


def snapshot_production_db(path: str | None) -> ProductionDbGuardResult:
    if not path or not os.path.isfile(path):
        return ProductionDbGuardResult(checked=False, path=path, sha256_before=None, size_before=None)
    import hashlib
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return ProductionDbGuardResult(checked=True, path=path, sha256_before=digest, size_before=os.path.getsize(path))


def verify_production_db(snapshot: ProductionDbGuardResult) -> ProductionDbGuardResult:
    if not snapshot.checked or not snapshot.path or not os.path.isfile(snapshot.path):
        return snapshot
    import hashlib
    with open(snapshot.path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    snapshot.sha256_after = digest
    snapshot.size_after = os.path.getsize(snapshot.path)
    return snapshot
