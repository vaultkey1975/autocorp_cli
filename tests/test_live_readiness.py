#!/usr/bin/env python3
"""Tests for Live Readiness Scanner (brains/live_readiness.py, Phase 1H).

Covers: valid repo produces report, launch candidates, health routes,
API routes, placeholder routes, workflow stages, external services,
production blockers, static-only inspection, immutable types.
"""

import os
import subprocess

import pytest

from brains.live_readiness import (
    LiveReadinessReport,
    ReadinessCheck,
    run_live_readiness,
    _id,
)


def _write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _init_git(repo_path):
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path,
                   capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"],
                   cwd=repo_path, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "i"], cwd=repo_path,
                   capture_output=True)


def test_valid_repository_produces_report(tmp_path):
    _write(tmp_path / "main.py", "import argparse\nprint('hi')\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    assert isinstance(report, LiveReadinessReport)
    assert report.repo_path == str(tmp_path)
    assert report.checks
    assert report.overall_status in ("ready", "needs_attention", "not_ready")


def test_repository_checks_exist(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    repo_checks = [c for c in report.checks if c.category == "repository"]
    assert len(repo_checks) >= 1


def test_launch_framework_detected(tmp_path):
    _write(tmp_path / "main.py",
            "from fastapi import FastAPI\napp = FastAPI()\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    launch = [c for c in report.checks if c.category == "launchability"]
    combined = " ".join(
        " ".join(str(e) for e in (c.evidence or ()))
        for c in launch
    )
    assert "fastapi" in combined.lower() or any(
        c.status == "pass" for c in launch if "framework" in c.title.lower()
    )


def test_health_routes_detected(tmp_path):
    _write(tmp_path / "main.py",
            "from fastapi import FastAPI\napp = FastAPI()\n"
            "@app.get('/health')\ndef health():\n    return {'status':'ok'}\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    health = [c for c in report.checks
              if c.title == "Health endpoints" and c.status == "pass"]
    assert len(health) == 1


def test_placeholder_route_detected(tmp_path):
    _write(tmp_path / "main.py",
            "from flask import Flask\napp = Flask(__name__)\n"
            "@app.route('/ping')\ndef ping():\n"
            "    return {'status': 'ok'}\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    placeholder = [c for c in report.checks if c.title == "Placeholder/stub routes"]
    assert len(placeholder) >= 1


def test_ui_fetch_detected(tmp_path):
    _write(tmp_path / "index.html",
            '<script>fetch("/api/data").then(r=>r.json())</script>')
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    ui = [c for c in report.checks if c.title == "UI fetch/api targets"]
    assert len(ui) == 1
    assert "fetch('/api/data')" in str(ui[0].evidence)


def test_workflow_stages_present(tmp_path):
    _write(tmp_path / "main.py",
            "import ffmpeg\ndef render():\n    pass\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    assert report.workflow_stages
    implemented = [s for s in report.workflow_stages if s["status"] == "implemented"]
    assert len(implemented) >= 1


def test_production_blockers_reported(tmp_path):
    _write(tmp_path / "main.py",
            "raise NotImplementedError\n# FIXME: fix this\n")
    _init_git(tmp_path)
    report = run_live_readiness(str(tmp_path))
    ni = [c for c in report.checks if "NotImplemented" in c.title]
    fm = [c for c in report.checks if "FIXME" in c.title]
    assert len(ni) >= 1 or len(fm) >= 1


def test_static_inspection_only(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    before = set(os.listdir(str(tmp_path)))
    run_live_readiness(str(tmp_path))
    after = set(os.listdir(str(tmp_path)))
    assert before == after


def test_check_is_immutable():
    c = ReadinessCheck(check_id="abc", category="repo", title="T",
                        status="pass", reason="ok", confidence=100)
    with pytest.raises((AttributeError, TypeError)):
        c.status = "fail"


def test_deterministic_check_ids():
    id1 = _id("test-check")
    id2 = _id("test-check")
    assert id1 == id2
    assert len(id1) == 12


def test_dirty_tree_warning(tmp_path):
    _write(tmp_path / "main.py", "import argparse\n")
    _init_git(tmp_path)
    _write(tmp_path / "dirty.py", "x=1\n")
    report = run_live_readiness(str(tmp_path))
    clean = [c for c in report.checks if c.title == "Clean working tree"]
    assert clean
