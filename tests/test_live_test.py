#!/usr/bin/env python3
"""Tests for controlled live application testing (brains/live_test.py, Phase 1J)."""

import os
import subprocess

import pytest

from brains.live_test import (
    _run_cmd,
    _http_get,
    _sha256_file,
    _redact_env,
    _find_executable,
    run_live_test,
)


def test_run_cmd_no_shell_true():
    r = _run_cmd(["echo", "hello"], timeout=5)
    assert "hello" in r["stdout"]
    assert r["exit_code"] == 0


def test_run_cmd_timeout_handling():
    r = _run_cmd(["sleep", "10"], timeout=1)
    assert r["timed_out"]


def test_run_cmd_missing_executable():
    r = _run_cmd(["/nonexistent/cmd"], timeout=5)
    assert r["error"]


def test_http_get_invalid_url():
    r = _http_get("http://127.0.0.1:19999/health", timeout=2)
    assert r["error"]


def test_sha256_file_deterministic(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    h1 = _sha256_file(str(f))
    h2 = _sha256_file(str(f))
    assert h1 == h2


def test_sha256_file_missing():
    assert _sha256_file("/nonexistent/path") == ""


def test_redact_env_secret():
    assert _redact_env("DEEPSEEK_API_KEY", "sk-abc") == "[REDACTED]"
    assert _redact_env("SECRET_KEY", "val") == "[REDACTED]"
    assert _redact_env("ACCESS_TOKEN", "tok") == "[REDACTED]"


def test_redact_env_non_secret():
    r = _redact_env("PATH", "/usr/bin")
    assert r != "[REDACTED]"


def test_find_executable_exists():
    assert _find_executable("python") is not None


def test_find_executable_missing():
    assert _find_executable("nonexistent_cmd_xyz") is None


def test_live_test_invalid_repo(tmp_path):
    report = run_live_test(str(tmp_path))
    assert report.overall_status in ("UNSAFE_TO_LAUNCH", "INCONCLUSIVE", "STARTUP_FAILED")


def test_process_cleanup_in_run_cmd():
    r = _run_cmd(["python", "-c", "import time; time.sleep(0.1)"], timeout=5)
    assert r["exit_code"] is not None
