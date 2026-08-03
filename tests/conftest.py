"""Pytest bootstrap for the AutoCorp CLI self-test suite.

Puts the project root on sys.path so the tests can import the project packages
(brains, core, safety, memory) no matter which directory pytest is invoked from.
This is belt-and-suspenders alongside `pythonpath = .` in pytest.ini.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402 - must come after the sys.path insert above


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Every test gets its own throwaway data directory by default, so a test
    that saves a session/upload/report can never write into the real
    production data/ tree. Test files that need the isolated path themselves
    can still define their own `isolated_data_dir` fixture; a same-named
    fixture in the test module shadows this one.

    This closes a real, evidenced bug: a prior test run left
    data/guided_clonecast_episode_sessions/session_hb_progress.json in the
    live session store because that test file never opted into isolation.
    """
    data = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    return data
