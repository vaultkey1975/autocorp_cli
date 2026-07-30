"""Quick CloneCast podcast generation in a disposable workspace.

The actual CloneCast-service-calling worker lives in a real, importable
module - brains/quick_podcast_runner.py - invoked as a normal Python module
(`<clonecast-venv-python> -m brains.quick_podcast_runner ...`) rather than
being embedded as a giant `python -c "<thousands of lines>"` string. This
module is now a thin orchestrator: it prepares the disposable workspace,
launches the worker, streams its live progress to the console and to the
shared persistent log file (/tmp/autocorp_quick_podcast.log), and renders
the final summary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from brains import scanner, workflow_test

# Persistent log file: everything printed to the console during a run is
# also written here, line-buffered and flushed immediately, so it stays
# usable with `tail -f` even while a run is in progress.
LOG_PATH = "/tmp/autocorp_quick_podcast.log"
DEFAULT_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "autocorp_quick_podcast_output"

# The directory containing this file's own package root (autocorp_cli/) -
# needed on PYTHONPATH so `-m brains.quick_podcast_runner` resolves inside
# CloneCast's separate venv/interpreter.
_AUTOCORP_ROOT = Path(__file__).resolve().parents[1]


class QuickPodcastError(RuntimeError):
    def __init__(self, phase: str, reason: str, next_steps: list[str] | None = None):
        super().__init__(reason)
        self.phase = phase
        self.reason = reason
        self.next_steps = next_steps or []


@dataclass
class PhaseResult:
    name: str
    status: str = "PENDING"
    detail: str = ""


@dataclass
class QuickPodcastReport:
    repo: str
    output_dir: str
    phases: list[PhaseResult] = field(default_factory=list)
    overall: str = "FAIL"
    failure_phase: str = ""
    failure_reason: str = ""
    next_steps: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0


PHASES = (
    "Repository",
    "CloneCast Health",
    "Research",
    "Script",
    "Storytelling",
    "Host Performance",
    "Voice",
    "Artwork",
    "Assembly",
    "Audio QC",
    "Package",
    "Publishing",
)


def parse_duration(value: str | None) -> int:
    raw = (value or "10m").strip().lower()
    if raw.endswith("m"):
        seconds = int(float(raw[:-1]) * 60)
    elif raw.endswith("s"):
        seconds = int(float(raw[:-1]))
    else:
        seconds = int(float(raw) * 60)
    if seconds <= 0:
        raise QuickPodcastError("Repository", "duration must be positive")
    return seconds


def _phase_map() -> dict[str, PhaseResult]:
    return {name: PhaseResult(name) for name in PHASES}


def _set_phase(phases: dict[str, PhaseResult], name: str, status: str, detail: str = "") -> None:
    phases[name].status = status
    phases[name].detail = detail


def _require_repo(repo: Path) -> None:
    if not repo.is_dir():
        raise QuickPodcastError("Repository", f"CloneCast repository does not exist: {repo}")
    if not (repo / ".git").exists():
        raise QuickPodcastError("Repository", f"not a Git repository: {repo}")
    for required in ("src/clonecast", "migrations", ".venv/bin/python"):
        if not (repo / required).exists():
            raise QuickPodcastError("Repository", f"required CloneCast path is missing: {required}")
    if not (repo / "db" / "cloneshow.db").is_file():
        raise QuickPodcastError("Repository", "production database is missing: db/cloneshow.db")


def _fetch_research(topic: str, destination: Path) -> None:
    title = topic.strip().replace(" ", "_")
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "AutoCorpQuickPodcast/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8"))
    except Exception as exc:
        raise QuickPodcastError(
            "Research",
            f"could not retrieve real research from Wikipedia for topic {topic!r}: {exc}",
            ["Provide network access or choose a topic with a public Wikipedia summary."],
        ) from exc
    extract = str(payload.get("extract") or "").strip()
    page_url = str(payload.get("content_urls", {}).get("desktop", {}).get("page") or url)
    timestamp = datetime.now(timezone.utc).isoformat()
    if not extract:
        raise QuickPodcastError("Research", f"Wikipedia returned no research summary for topic {topic!r}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(
            [
                f"Title: {topic}",
                f"Source URL: {page_url}",
                "Source Name: Wikipedia",
                f"Published At: {timestamp}",
                f"Collected At: {timestamp}",
                "Tags: autocorp, quick-podcast, production-validation",
                f"External ID: wikipedia:{title}",
                "",
                extract,
                "",
                "Additional production angle: treat extraordinary claims cautiously, separate documented record from folklore, and identify uncertainty for listeners.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _clonecast_env(repo: Path, disp: Path, disp_db: Path) -> dict[str, str]:
    env = workflow_test._clonecast_env(str(repo), str(disp), str(disp_db))
    # So `-m brains.quick_podcast_runner` resolves from inside CloneCast's
    # own venv/interpreter, alongside CloneCast's own `clonecast` package.
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + str(_AUTOCORP_ROOT)
    return env


def _default_output_dir(repo: Path) -> Path:
    return DEFAULT_OUTPUT_ROOT / repo.name / "test_episode"


class _TeeLog:
    """Writes every line to stdout (flushed) AND to the shared persistent
    log file (line-buffered, flushed immediately) - usable with `tail -f`
    while a run is in progress."""

    def __init__(self, path: str, mode: str):
        self._fh = open(path, mode, buffering=1, encoding="utf-8")

    def emit(self, line: str = "") -> None:
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _run_clonecast(repo: Path, disp: Path, disp_db: Path, research: Path, output: Path,
                   show: str, topic: str, duration: int, voice: str, test: bool,
                   log: _TeeLog) -> dict:
    if not test:
        raise QuickPodcastError("Publishing", "quick-podcast refuses to publish unless --test is present",
                                ["Run with --test for local validation output."])
    env = _clonecast_env(repo, disp, disp_db)
    python = repo / ".venv" / "bin" / "python"
    args = [
        str(python), "-m", "brains.quick_podcast_runner",
        "--repo", str(repo), "--disp", str(disp), "--db", str(disp_db),
        "--research", str(research), "--output", str(output),
        "--show", show, "--topic", topic, "--duration-seconds", str(duration),
        "--voice", voice or "", "--log-file", LOG_PATH,
        # Phase 1 ("Repository") was already completed and printed by this
        # parent process before the worker was launched - the worker's own
        # [N/12] progress continues the same single sequence from phase 2.
        "--phase-offset", "1",
    ]
    timeout_seconds = max(1800, duration * 12)
    proc = subprocess.Popen(
        args, cwd=repo, env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
    )
    # subprocess.Popen has no built-in timeout while streaming output line by
    # line, so a background timer enforces the same overall timeout the
    # previous subprocess.run(..., timeout=...) call used.
    timer = threading.Timer(timeout_seconds, proc.kill)
    timer.start()
    payload: dict = {}
    stdout_tail: list[str] = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\n")
            log.emit(line)
            stdout_tail.append(line)
            if len(stdout_tail) > 200:
                stdout_tail.pop(0)
            try:
                candidate = json.loads(line)
                if isinstance(candidate, dict) and "ok" in candidate:
                    payload = candidate
            except json.JSONDecodeError:
                continue
        proc.wait()
    finally:
        timer.cancel()
    if proc.returncode != 0 or not payload.get("ok"):
        phase = payload.get("phase") or "CloneCast Health"
        reason = payload.get("reason") or "\n".join(stdout_tail[-40:])[:2000] or f"CloneCast subprocess exited {proc.returncode}"
        raise QuickPodcastError(
            phase, reason,
            [
                "Inspect CloneCast logs under the disposable workspace if it was retained.",
                "Verify Ollama, Chatterbox, ffmpeg, and approved voice assets are available.",
                f"Full run log: {LOG_PATH}",
            ],
        )
    return payload


def run(args: argparse.Namespace) -> QuickPodcastReport:
    t0 = time.time()
    repo = Path(args.repo).expanduser().resolve()
    duration = parse_duration(args.duration)
    output = Path(args.output).expanduser().resolve() if args.output else _default_output_dir(repo)
    phases = _phase_map()
    report = QuickPodcastReport(repo=str(repo), output_dir=str(output), phases=list(phases.values()))
    disp: Path | None = None
    log = _TeeLog(LOG_PATH, "w")
    log.emit("========================================")
    log.emit("AutoCorp Quick Podcast")
    log.emit("========================================")
    log.emit("")
    try:
        _require_repo(repo)
        if scanner._git_info(str(repo))[0] == "not-a-repo":
            raise QuickPodcastError("Repository", "CloneCast Git repository could not be inspected")
        _set_phase(phases, "Repository", "PASS")

        disp = Path(tempfile.mkdtemp(prefix="acqp-"))
        disp_db = disp / "db" / "cloneshow.db"
        disp_db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / "db" / "cloneshow.db", disp_db)
        workflow_test._prepare_disposable_voice_assets(str(disp_db), str(disp))
        research_path = disp / "inputs" / "research.md"
        _fetch_research(args.topic, research_path)
        _set_phase(phases, "Research", "PASS")

        log.emit(f"[1/{len(PHASES)}] Repository {'.' * 16} PASS")
        log.emit("")

        payload = _run_clonecast(repo, disp, disp_db, research_path, output, args.show or "CloneCast",
                                 args.topic, duration, args.voice or "", bool(args.test), log)
        for name in ("CloneCast Health", "Script", "Storytelling", "Host Performance", "Voice", "Artwork", "Assembly", "Audio QC", "Package"):
            _set_phase(phases, name, "PASS")
        _set_phase(phases, "Publishing", "SKIPPED (TEST MODE)" if args.test else "FAIL")
        report.overall = "PASS"
        report.output_dir = payload["output_dir"]
    except QuickPodcastError as exc:
        _set_phase(phases, exc.phase, "FAIL", exc.reason)
        report.failure_phase = exc.phase
        report.failure_reason = exc.reason
        report.next_steps = exc.next_steps
        log.emit("")
        log.emit(f"FAILURE in phase {exc.phase}: {exc.reason}")
    finally:
        if disp is not None:
            shutil.rmtree(disp, ignore_errors=True)
        report.runtime_seconds = time.time() - t0
        report.phases = list(phases.values())
        log.close()
    return report


def _format_mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}m {s}s" if m else f"{s}s"


def print_report(report: QuickPodcastReport) -> None:
    log = _TeeLog(LOG_PATH, "a")
    try:
        log.emit("")
        log.emit("================================================")
        log.emit("Quick Podcast Complete" if report.overall == "PASS" else "Quick Podcast FAILED")
        log.emit("================================================")
        log.emit("")
        for phase in report.phases:
            log.emit(f"{phase.name}:")
            log.emit(phase.status)
            if phase.status == "FAIL" and phase.detail:
                log.emit(f"  Reason: {phase.detail}")
            log.emit("")

        log.emit("Output:")
        log.emit("")
        output_dir = Path(report.output_dir)
        if output_dir.is_dir():
            for name in ("episode.mp3", "episode.mp4", "thumbnail.png", "script.md",
                        "research.md", "qc_report.txt", "package.zip"):
                if (output_dir / name).is_file():
                    log.emit(name)
        else:
            log.emit("(none - run did not reach output generation)")
        log.emit("")

        log.emit("Elapsed Time:")
        log.emit("")
        log.emit(_format_mmss(report.runtime_seconds))
        log.emit("")

        if report.overall != "PASS":
            log.emit(f"Failing Phase: {report.failure_phase}")
            log.emit(f"Reason: {report.failure_reason}")
            if report.next_steps:
                log.emit("Recommended next steps:")
                for step in report.next_steps:
                    log.emit(f"  - {step}")
            log.emit("")

        log.emit("================================================")
    finally:
        log.close()
