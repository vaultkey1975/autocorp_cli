"""Real production-path verification for long pasted research ingestion.

This intentionally uses the real AutoCorp chat controller and the real
CloneCast CLI. It isolates CloneCast with a disposable CLONECAST_ROOT and
database, but does not fake CloneCast command results.
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from app import chat_controller as controller  # noqa: E402
from app import session_store as store  # noqa: E402
from brains import guided_clonecast_episode as episode  # noqa: E402


def _sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _clonecast(
    clonecast_repo: Path,
    env: dict[str, str],
    *args: str,
    timeout: int = 120,
) -> tuple[int, str, str]:
    completed = subprocess.run(
        [sys.executable, "-m", "clonecast.cli", *args],
        cwd=str(clonecast_repo),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"clonecast {' '.join(args)} failed rc={completed.returncode}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed.returncode, completed.stdout, completed.stderr


def _make_research_text() -> str:
    return "\n".join(
        [
            "# Paranormal Bigfoot-Type Phenomena Around the World: Evidence, Folklore, and Scientific Assessment",
            "",
            "## Executive summary",
            "Bigfoot-type claims include Sasquatch, Yowie, Yeti, Almasty, Orang Pendek, and high-strangeness variants.",
            "",
            "| Region | Claim | Evidence note | Citation |",
            "|---|---|---|---|",
            (
                "| Pacific Northwest | Sasquatch | footprints, eyewitness reports, disputed film | "
                "[1]; \\ue200cite\\ue202turn14search5\\ue201 |"
            ),
            (
                "| Himalaya | Yeti | tested relics map mostly to bears | "
                "Sykes et al. 2014; \\ue200cite\\ue202turn15search1\\ue201 |"
            ),
            "| Australia | Yowie | settler and Indigenous traditions are often conflated | [2] |",
            "",
            (
                "Unicode control line: \u201ccurly quotes\u201d, \u00e9, na\u00efve, \u65e5\u672c\u8a9e, "
                "\U0001f9b6, and literal citation markers \\ue200cite\\ue202turn22search9\\ue201."
            ),
            "",
            *[
                (
                    f"Paragraph {i:03d}: This long pasted Markdown paragraph preserves line breaks, "
                    f"citations [A{i}], table context, and exact spacing. "
                    + (
                        "Bigfoot research should remain text and never be passed to a filesystem API "
                        "as a filename. "
                    )
                    * 5
                )
                for i in range(120)
            ],
        ]
    )


def _inspect_failed_session(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "recoverable_without_repaste": False}
    app = store.AppSession.from_dict(json.loads(path.read_text(encoding="utf-8")))
    recovered = controller._extract_failed_pasted_research(app)
    return {
        "path": str(path),
        "exists": True,
        "session_id": app.session_id,
        "status": app.status,
        "episode_session_id": app.episode_session_id,
        "recoverable_without_repaste": bool(recovered),
        "recovered_chars": len(recovered or ""),
        "recovered_bytes": len((recovered or "").encode("utf-8")),
        "contains_errno36": any("File name too long" in (m.technical_detail or "") for m in app.messages),
    }


def verify(args: argparse.Namespace) -> dict[str, Any]:
    autocorp_repo = args.autocorp_repo.expanduser().resolve()
    clonecast_repo = args.clonecast_repo.expanduser().resolve()
    failed_session_path = args.failed_session.expanduser().resolve()
    owner_paths = [
        failed_session_path,
        clonecast_repo / "data" / "cloneshow.db",
        clonecast_repo / "cloneshow.db",
    ]
    owner_before = {str(path): _sha256_file(path) for path in owner_paths}
    failed_session_readonly = _inspect_failed_session(failed_session_path)

    temp_root = Path(tempfile.mkdtemp(prefix="autocorp_real_clonecast_research_"))
    disposable_root = temp_root / "clonecast-disposable-root"
    disposable_root.mkdir(parents=True, exist_ok=True)
    db_path = disposable_root / "db" / "cloneshow.sqlite3"
    research_root = disposable_root / "research"
    app_data = temp_root / "autocorp-data"
    for path in (db_path.parent, research_root, app_data):
        path.mkdir(parents=True, exist_ok=True)

    env_updates = {
        "CLONECAST_ROOT": str(disposable_root),
        "CLONECAST_MIGRATIONS_PATH": str(clonecast_repo / "migrations"),
        "CLONECAST_DB_PATH": str(db_path),
        "CLONECAST_RESEARCH_ROOT": str(research_root),
        "CLONECAST_OLLAMA_ENABLED": "false",
    }
    os.environ.update(env_updates)
    config.DATA_DIR = str(app_data)
    config.GPU_GUARD_ENABLED = False

    clonecast_env = os.environ.copy()
    clonecast_env["PYTHONPATH"] = str(clonecast_repo / "src") + os.pathsep + clonecast_env.get("PYTHONPATH", "")

    _clonecast(clonecast_repo, clonecast_env, "db-upgrade")
    _, studio_stdout, _ = _clonecast(
        clonecast_repo,
        clonecast_env,
        "radio-studio-create",
        "--stable-name",
        "real-research-verification",
        "--display-name",
        "Real Research Verification",
        "--show-format",
        "solo_host",
        "--idempotency-key",
        "autocorp-real-research-verification-studio",
        "--description",
        "Disposable production-path verification studio",
    )
    studio_id = json.loads(studio_stdout)["studio_id"]
    _clonecast(clonecast_repo, clonecast_env, "radio-studio-validate", studio_id)
    _clonecast(
        clonecast_repo,
        clonecast_env,
        "radio-studio-review",
        "--studio-id",
        studio_id,
        "--reviewer",
        "verification",
        "--decision",
        "accepted",
        "--notes",
        "Disposable verification setup",
    )
    _clonecast(
        clonecast_repo,
        clonecast_env,
        "radio-studio-approve",
        "--studio-id",
        studio_id,
        "--approver",
        "verification",
    )

    text = _make_research_text()
    original_bytes = text.encode("utf-8")
    expected_sha = episode.checksum_bytes(original_bytes)

    path_probe_hits: list[str] = []
    original_exists = Path.exists
    original_is_file = Path.is_file
    original_stat = Path.stat
    original_resolve = Path.resolve
    original_open = builtins.open

    def guard_path(value: object) -> None:
        if str(value) == text:
            path_probe_hits.append(str(value)[:120])
            raise AssertionError("pasted research body was passed to filesystem API as a path")

    def guarded_exists(self: Path) -> bool:
        guard_path(self)
        return original_exists(self)

    def guarded_is_file(self: Path) -> bool:
        guard_path(self)
        return original_is_file(self)

    def guarded_stat(self: Path, *stat_args: Any, **stat_kwargs: Any) -> os.stat_result:
        guard_path(self)
        return original_stat(self, *stat_args, **stat_kwargs)

    def guarded_resolve(self: Path, *resolve_args: Any, **resolve_kwargs: Any) -> Path:
        guard_path(self)
        return original_resolve(self, *resolve_args, **resolve_kwargs)

    def guarded_open(file: object, *open_args: Any, **open_kwargs: Any) -> Any:
        guard_path(file)
        return original_open(file, *open_args, **open_kwargs)

    try:
        Path.exists = guarded_exists  # type: ignore[method-assign]
        Path.is_file = guarded_is_file  # type: ignore[method-assign]
        Path.stat = guarded_stat  # type: ignore[method-assign]
        Path.resolve = guarded_resolve  # type: ignore[method-assign]
        builtins.open = guarded_open  # type: ignore[assignment]
        app = controller.start_session(
            str(clonecast_repo),
            "Create a Real Research Verification episode. 10 minutes. No guests. Audio only.",
        )
        if app.status != "awaiting_input" or app.pending_question.get("field") != "research":
            raise RuntimeError(f"expected research prompt, got status={app.status} question={app.pending_question}")
        app = controller.submit_answer(app.session_id, {"text": text})
    finally:
        Path.exists = original_exists  # type: ignore[method-assign]
        Path.is_file = original_is_file  # type: ignore[method-assign]
        Path.stat = original_stat  # type: ignore[method-assign]
        Path.resolve = original_resolve  # type: ignore[method-assign]
        builtins.open = original_open  # type: ignore[assignment]

    if app.status != "awaiting_input" or app.pending_question.get("field") != "script":
        raise RuntimeError(
            f"session did not advance beyond research: status={app.status} question={app.pending_question}"
        )

    ep = episode.load_session(app.episode_session_id)
    managed_path = Path(ep.artifact_paths["managed_research"])
    managed_body = json.loads(managed_path.read_text(encoding="utf-8"))["body"]
    accepted_id = ep.clonecast_episode_identifiers.get("research_id")
    if not accepted_id:
        raise RuntimeError("missing accepted research_id")
    _, show_stdout, _ = _clonecast(clonecast_repo, clonecast_env, "research-show", accepted_id)
    research_show = json.loads(show_stdout)

    resumed = controller.resume_session(app.session_id)
    resumed_ep = episode.load_session(resumed.episode_session_id)
    ingest_commands = [
        c for c in resumed_ep.clonecast_commands if c["command"] and c["command"][0] == "research-ingest"
    ]
    show_commands = [c for c in resumed_ep.clonecast_commands if c["command"] and c["command"][0] == "research-show"]

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        research_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT research_id,lifecycle_state,content_hash,current_path FROM research_items ORDER BY created_at"
            )
        ]

    owner_after = {str(path): _sha256_file(path) for path in owner_paths}
    result = {
        "autocorp_repo": str(autocorp_repo),
        "clonecast_repo": str(clonecast_repo),
        "disposable_root": str(temp_root),
        "disposable_clonecast_root": str(disposable_root),
        "disposable_db": str(db_path),
        "disposable_research_root": str(research_root),
        "app_data_dir": str(app_data),
        "app_session_id": app.session_id,
        "episode_session_id": ep.session_id,
        "advanced_to": app.pending_question["field"],
        "resumed_status": resumed.status,
        "resumed_pending_field": resumed.pending_question["field"] if resumed.pending_question else None,
        "research_chars": len(text),
        "research_bytes": len(original_bytes),
        "source_type": ep.research_source.get("source_type"),
        "exact_text_preserved_in_session": ep.research_source.get("text") == text,
        "exact_text_preserved_in_managed_json": managed_body == text,
        "session_sha256": ep.research_source.get("sha256"),
        "expected_sha256": expected_sha,
        "managed_body_sha256": episode.checksum_bytes(managed_body.encode("utf-8")),
        "accepted_research_id": accepted_id,
        "research_import_status": ep.research_import_status,
        "real_research_show_state": research_show.get("lifecycle_state") or research_show.get("status"),
        "real_research_show_content_hash": research_show.get("content_hash"),
        "clonecast_research_rows": research_rows,
        "research_ingest_command_count_after_resume": len(ingest_commands),
        "research_show_command_count_after_resume": len(show_commands),
        "path_probe_hits_for_body": len(path_probe_hits),
        "errno36_seen": any("File name too long" in (message.technical_detail or "") for message in app.messages),
        "publishing_lock_status": resumed_ep.owner_approval_status,
        "publishing_lock_reason": resumed_ep.publishing_lock_reason,
        "owner_paths_before": owner_before,
        "owner_paths_after": owner_after,
        "owner_paths_unchanged": owner_before == owner_after,
        "failed_shadow_frequency_session_readonly": failed_session_readonly,
        "env_used": env_updates,
        "clonecast_commands": [command["command"] for command in resumed_ep.clonecast_commands],
    }
    result["checks"] = {
        "no_path_probe": result["path_probe_hits_for_body"] == 0,
        "no_errno36": not result["errno36_seen"],
        "accepted": result["research_import_status"] == "accepted" and result["real_research_show_state"] == "accepted",
        "advanced": result["advanced_to"] == "script",
        "exact": result["exact_text_preserved_in_session"] and result["exact_text_preserved_in_managed_json"],
        "checksum": result["session_sha256"] == expected_sha == result["managed_body_sha256"],
        "single_ingest_after_resume": result["research_ingest_command_count_after_resume"] == 1,
        "resume_no_duplicate": len(result["clonecast_research_rows"]) == 1,
        "publishing_locked": result["publishing_lock_status"] == "publishing_locked",
        "owner_untouched": result["owner_paths_unchanged"],
        "failed_session_recoverable_without_repaste": failed_session_readonly["recoverable_without_repaste"],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autocorp-repo", type=Path, default=ROOT)
    parser.add_argument("--clonecast-repo", type=Path, default=Path("/home/larry/clonecast"))
    parser.add_argument(
        "--failed-session",
        type=Path,
        default=ROOT / "data" / "autocorp_app_sessions" / "appsess_ee3ea9489e2d4857bbd60e3954458998.json",
    )
    args = parser.parse_args()
    result = verify(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
