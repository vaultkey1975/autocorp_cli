#!/usr/bin/env python3
"""Tests for brains/quick_podcast_runner.py - the real module that replaced
the previous embedded `python -c "<thousands of lines>"` worker.

This module is designed to run inside CloneCast's own venv (it imports
`clonecast.*` lazily, inside _run()), but everything importable/testable
here - argument parsing, progress/logging, the long-form conversation
provider's validation logic, and the sha256 helper - has no CloneCast
dependency and is exercised directly in AutoCorp's own test environment.
"""

import json
import os

import pytest

from brains import quick_podcast_runner as qpr


@pytest.fixture
def make_progress():
    """Factory for qpr.Progress instances that closes every one it creates
    at teardown - Progress opens a real file handle, and pytest runs many
    tests in one long-lived process, so leaving them open accumulates
    unclosed-file ResourceWarnings that -W error turns into failures."""
    created = []

    def _make(log_path, phase_index_start=0):
        progress = qpr.Progress(str(log_path), phase_index_start=phase_index_start)
        created.append(progress)
        return progress

    yield _make
    for progress in created:
        progress.close()


# --------------------------------------------------------------------------- #
# CLI argument parsing
# --------------------------------------------------------------------------- #
def test_build_parser_requires_core_arguments():
    parser = qpr.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_accepts_full_argument_set(tmp_path):
    parser = qpr.build_parser()
    args = parser.parse_args([
        "--repo", "/repo", "--disp", "/disp", "--db", "/disp/db.sqlite",
        "--research", "/disp/research.md", "--output", "/disp/output",
        "--show", "CloneCast", "--topic", "Test Topic",
        "--duration-seconds", "600", "--voice", "voice_abc",
        "--log-file", str(tmp_path / "log.txt"), "--phase-offset", "1",
    ])
    assert args.repo == "/repo"
    assert args.duration_seconds == 600
    assert args.voice == "voice_abc"
    assert args.phase_offset == 1


def test_build_parser_phase_offset_defaults_to_zero(tmp_path):
    parser = qpr.build_parser()
    args = parser.parse_args([
        "--repo", "/repo", "--disp", "/disp", "--db", "/disp/db.sqlite",
        "--research", "/disp/research.md", "--output", "/disp/output",
        "--show", "CloneCast", "--topic", "Test Topic",
        "--duration-seconds", "600", "--log-file", str(tmp_path / "log.txt"),
    ])
    assert args.phase_offset == 0
    assert args.voice == ""


# --------------------------------------------------------------------------- #
# Progress / logging (line-buffered, flushed, tee'd to stdout + log file)
# --------------------------------------------------------------------------- #
def test_progress_emit_writes_to_log_file(tmp_path, capsys, make_progress):
    log_path = tmp_path / "quick_podcast.log"
    progress = make_progress(log_path)

    progress.emit("hello world")

    captured = capsys.readouterr()
    assert "hello world" in captured.out
    assert log_path.read_text(encoding="utf-8") == "hello world\n"


def test_progress_phase_start_and_done_use_running_index(tmp_path, capsys, make_progress):
    log_path = tmp_path / "quick_podcast.log"
    progress = make_progress(log_path)

    progress.phase_start("CloneCast Health")
    progress.phase_done("CloneCast Health")
    progress.phase_start("Research")

    out = capsys.readouterr().out
    assert "[1/12] CloneCast Health" in out
    assert "[2/12] Research" in out


def test_progress_phase_offset_continues_a_single_sequence(tmp_path, capsys, make_progress):
    """The parent process (brains/quick_podcast.py) already prints
    "[1/12] Repository" before launching this module - phase_index_start
    lets this process's own numbering continue from there instead of
    restarting at 1, so the two processes' output reads as one sequence."""
    log_path = tmp_path / "quick_podcast.log"
    progress = make_progress(log_path, phase_index_start=1)

    progress.phase_start("CloneCast Health")

    out = capsys.readouterr().out
    assert "[2/12] CloneCast Health" in out
    assert "[1/12]" not in out


def test_progress_remaining_estimate_only_shown_after_first_phase(tmp_path, capsys, make_progress):
    log_path = tmp_path / "quick_podcast.log"
    progress = make_progress(log_path)

    progress.phase_start("Repository")
    out_before = capsys.readouterr().out
    assert "Estimated Remaining" not in out_before

    progress.phase_done("Repository")
    progress.phase_start("CloneCast Health")
    out_after = capsys.readouterr().out
    assert "Estimated Remaining" in out_after


def test_progress_log_file_is_line_buffered_and_flushed_immediately(tmp_path, make_progress):
    """Simulates `tail -f`: content must be on disk right after emit(),
    without needing the Progress object to be closed."""
    log_path = tmp_path / "quick_podcast.log"
    progress = make_progress(log_path)

    progress.emit("first line")
    on_disk_after_first = log_path.read_text(encoding="utf-8")
    progress.emit("second line")
    on_disk_after_second = log_path.read_text(encoding="utf-8")

    assert on_disk_after_first == "first line\n"
    assert on_disk_after_second == "first line\nsecond line\n"


# --------------------------------------------------------------------------- #
# LongformConversationProvider - pure validation logic (no real Ollama)
# --------------------------------------------------------------------------- #
class _FakeInnerProvider:
    model_name = "qwen2.5:14b"
    endpoint_identifier = "http://127.0.0.1:11434"

    def check(self):
        return {"available": True}

    def generate(self, system, user, *, response_schema=None):
        raise AssertionError("not used by the _valid/_first_json_object tests")


def _provider(tmp_path, make_progress, target_seconds=600):
    progress = make_progress(tmp_path / "log.txt")
    return qpr.LongformConversationProvider(inner=_FakeInnerProvider(), target_seconds=target_seconds, progress=progress)


def test_valid_rejects_non_json(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress)
    ok, reason = provider._valid("not json")
    assert not ok
    assert "not JSON" in reason


def test_valid_rejects_missing_turns(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress)
    ok, reason = provider._valid(json.dumps({"turns": []}))
    assert not ok
    assert "no turns" in reason


def test_valid_rejects_under_minimum_words(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress, target_seconds=600)  # minimum_words = 1500
    turns = [{"spoken_text": "only a few words here", "turn_type": "closing",
              "estimated_duration_seconds": 600}]
    ok, reason = provider._valid(json.dumps({"turns": turns, "estimated_duration_seconds": 600}))
    assert not ok
    assert "minimum is 1500" in reason


def test_valid_rejects_non_closing_final_turn(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress, target_seconds=10)  # minimum_words = 80
    text = " ".join(["word"] * 90)
    turns = [{"spoken_text": text, "turn_type": "dialogue", "estimated_duration_seconds": 10}]
    ok, reason = provider._valid(json.dumps({"turns": turns, "estimated_duration_seconds": 10}))
    assert not ok
    assert "closing" in reason


def test_valid_rejects_duration_mismatch(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress, target_seconds=10)
    text = " ".join(["word"] * 90)
    turns = [{"spoken_text": text, "turn_type": "closing", "estimated_duration_seconds": 5}]
    ok, reason = provider._valid(json.dumps({"turns": turns, "estimated_duration_seconds": 20}))
    assert not ok
    assert "disagrees" in reason


def test_valid_accepts_well_formed_response(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress, target_seconds=10)
    text = " ".join(["word"] * 90)
    turns = [{"spoken_text": text, "turn_type": "closing", "estimated_duration_seconds": 10}]
    ok, reason = provider._valid(json.dumps({"turns": turns, "estimated_duration_seconds": 10}))
    assert ok
    assert reason == ""


def test_first_json_object_ignores_trailing_garbage(tmp_path, make_progress):
    provider = _provider(tmp_path, make_progress)
    value = provider._first_json_object('{"a": 1}\nnot part of the json')
    assert value == {"a": 1}


# --------------------------------------------------------------------------- #
# sha256 helper
# --------------------------------------------------------------------------- #
def test_sha256_matches_hashlib(tmp_path):
    import hashlib
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"quick podcast test content")

    assert qpr._sha256(path) == hashlib.sha256(b"quick podcast test content").hexdigest()


# --------------------------------------------------------------------------- #
# Voice-render progress poller (background thread, read-only DB polling)
# --------------------------------------------------------------------------- #
def test_poll_voice_render_progress_reports_completed_turns(tmp_path, make_progress):
    """Runs the poller in a real background thread (as production code
    does) for a short window, then signals it to stop - exercising the
    actual read-only polling loop rather than short-circuiting it."""
    import sqlite3
    import threading

    db_path = tmp_path / "disposable.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE ai_conversation_turns (generation_id TEXT)")
    conn.execute("CREATE TABLE conversation_voice_turn_assets (conversation_id TEXT, status TEXT)")
    conn.executemany("INSERT INTO ai_conversation_turns VALUES (?)", [("gen1",)] * 3)
    conn.executemany(
        "INSERT INTO conversation_voice_turn_assets VALUES (?, ?)",
        [("conv1", "completed"), ("conv1", "completed"), ("conv1", "pending")],
    )
    conn.commit()
    conn.close()

    progress = make_progress(tmp_path / "log.txt")
    stop_event = threading.Event()
    thread = threading.Thread(
        target=qpr._poll_voice_render_progress,
        args=(db_path, "conv1", "gen1", progress, stop_event),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=2)
    stop_event.set()
    thread.join(timeout=2)

    log_content = (tmp_path / "log.txt").read_text(encoding="utf-8")
    assert "Turn 2 / 3 complete" in log_content


def test_poll_voice_render_progress_never_raises_on_missing_tables(tmp_path, make_progress):
    """Progress reporting must never crash or interfere with the real
    render_conversation() call running on the main thread, even if the
    tables it polls don't exist yet (a real sqlite3.OperationalError)."""
    import threading

    db_path = tmp_path / "empty.sqlite"
    db_path.touch()  # valid SQLite file, but with none of the expected tables
    progress = make_progress(tmp_path / "log.txt")
    stop_event = threading.Event()
    errors: list[BaseException] = []

    def _target():
        try:
            qpr._poll_voice_render_progress(db_path, "conv1", "gen1", progress, stop_event)
        except BaseException as exc:  # pragma: no cover - must never happen
            errors.append(exc)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=2)
    stop_event.set()
    thread.join(timeout=2)

    assert errors == []
