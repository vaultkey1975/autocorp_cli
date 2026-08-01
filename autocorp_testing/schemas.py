"""Stable data shapes for the Fast Pytest Engine.

`EngineReport.to_dict()` is the JSON contract consumed by Claude, Codex, and
other callers (see `test-fast --json`). Field names here must stay stable;
add new fields rather than renaming existing ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class SelectedTest:
    """One test selected for execution, with recorded reasons."""

    node_id: str
    path: str
    reasons: tuple[str, ...] = ()
    category: str = "direct"  # previously_failed | syntax | import | direct |
    # safety | integration | slow | dependency | feature | explicit
    estimated_duration_seconds: float | None = None
    estimate_is_stale: bool = False
    classification: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test": self.node_id,
            "path": self.path,
            "reasons": list(self.reasons),
            "category": self.category,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "estimate_is_stale": self.estimate_is_stale,
            "classification": dict(self.classification),
        }


@dataclass
class TestPlan:
    """Output of `test-plan`. Never runs tests."""

    repository: str
    repository_fingerprint: str
    branch: str
    commit_sha: str
    changed_files: list[str]
    discovered_config: dict[str, Any]
    strict_full_command: list[str] | None
    strict_full_command_confidence: str
    compile_command: list[str]
    fast_tests: list[SelectedTest]
    focused_tests: list[SelectedTest]
    deferred_to_full: list[str]
    selection_reasons: dict[str, list[str]]
    estimated_duration_seconds: float
    confidence: str
    parallelism: dict[str, Any]
    gpu_safety: dict[str, Any]
    database_safety: dict[str, Any]
    uncertainty_warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "plan",
            "repository": self.repository,
            "repository_fingerprint": self.repository_fingerprint,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "changed_files": list(self.changed_files),
            "discovered_config": self.discovered_config,
            "strict_full_command": self.strict_full_command,
            "strict_full_command_confidence": self.strict_full_command_confidence,
            "compile_command": self.compile_command,
            "fast_tests": [t.to_dict() for t in self.fast_tests],
            "focused_tests": [t.to_dict() for t in self.focused_tests],
            "deferred_to_full": list(self.deferred_to_full),
            "selection_reasons": self.selection_reasons,
            "estimated_duration_seconds": self.estimated_duration_seconds,
            "confidence": self.confidence,
            "parallelism": self.parallelism,
            "gpu_safety": self.gpu_safety,
            "database_safety": self.database_safety,
            "uncertainty_warnings": list(self.uncertainty_warnings),
            "full_suite_required": True,
            "production_approval_allowed": False,
        }


@dataclass
class TestResult:
    node_id: str
    outcome: str  # passed | failed | error | skipped
    duration_seconds: float
    failure_type: str | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "test": self.node_id,
            "outcome": self.outcome,
            "duration_seconds": self.duration_seconds,
            "failure_type": self.failure_type,
            "message": self.message,
        }


@dataclass
class EngineReport:
    mode: str  # fast | focused | full
    repository: str
    repository_fingerprint: str
    changed_files: list[str]
    selected_tests: list[SelectedTest]
    commands: list[list[str]]
    results: list[TestResult]
    duration_seconds: float
    deferred_checks: list[str]
    parallelism: dict[str, Any]
    gpu_safety: dict[str, Any]
    database_safety: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    exit_code: int = 0
    blocked: bool = False
    blocked_reason: str = ""
    compile_result: dict[str, Any] = field(default_factory=dict)
    import_check_results: list[dict[str, Any]] = field(default_factory=list)
    uncertainty_warnings: list[str] = field(default_factory=list)
    requested_paths: list[str] = field(default_factory=list)
    normalized_paths: list[str] = field(default_factory=list)
    accepted_paths: list[str] = field(default_factory=list)
    rejected_paths: list[str] = field(default_factory=list)
    duplicate_paths: list[str] = field(default_factory=list)
    selection_mode: str = ""
    collected: int = 0
    skipped: int = 0
    deselected: int = 0
    represented_test_files: list[str] = field(default_factory=list)
    slowest_tests: list[dict[str, Any]] = field(default_factory=list)
    collection_duration_seconds: float | None = None
    performance_warnings: list[str] = field(default_factory=list)

    def selection_reasons(self) -> dict[str, list[str]]:
        return {t.node_id: list(t.reasons) for t in self.selected_tests}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "repository": self.repository,
            "repository_fingerprint": self.repository_fingerprint,
            "changed_files": list(self.changed_files),
            "selected_tests": [t.node_id for t in self.selected_tests],
            "selection_reasons": self.selection_reasons(),
            "commands": [list(c) for c in self.commands],
            "results": [r.to_dict() for r in self.results],
            "duration_seconds": self.duration_seconds,
            "deferred_checks": list(self.deferred_checks),
            "full_suite_required": self.mode != "full",
            "production_approval_allowed": self.mode == "full" and self.exit_code == 0 and not self.blocked,
            "parallelism": self.parallelism,
            "gpu_safety": self.gpu_safety,
            "database_safety": self.database_safety,
            "errors": list(self.errors),
            "passed": self.passed,
            "failed": self.failed,
            "exit_code": self.exit_code,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "compile_result": self.compile_result,
            "import_check_results": self.import_check_results,
            "uncertainty_warnings": list(self.uncertainty_warnings),
            "requested_paths": list(self.requested_paths),
            "normalized_paths": list(self.normalized_paths),
            "accepted_paths": list(self.accepted_paths),
            "rejected_paths": list(self.rejected_paths),
            "duplicate_paths": list(self.duplicate_paths),
            "selection_mode": self.selection_mode,
            "collected": self.collected,
            "skipped": self.skipped,
            "deselected": self.deselected,
            "represented_test_files": list(self.represented_test_files),
            "slowest_tests": list(self.slowest_tests),
            "collection_duration_seconds": self.collection_duration_seconds,
            "performance_warnings": list(self.performance_warnings),
        }
