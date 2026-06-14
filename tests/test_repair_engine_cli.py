#!/usr/bin/env python3
"""
Repair engine CLI selection  (AutoCorp CLI - Phase DS8 RED)
===========================================================

Drives Phase DS8: expose the already-built tester/repair engine seam to CLI
users. DS5-DS7 made the library path complete - `Session(tester_engine=...)` ->
TesterBrain(engine) -> the DS6 repair chain - but `autocorp.py` never lets a user
choose it: `cmd_build` constructs `Session(...)` WITHOUT `tester_engine`, and the
`build` subparser has no `--tester-engine` flag. So a CLI user can never run
DeepSeek-powered repairs even though the engine is wired and registered.

This phase is CLI EXPOSURE ONLY. It does NOT touch DeepSeekEngine, ModelRouter,
Builder routing, the DS6 repair wiring, or DS7 tester construction.

Pinned design (RED until GREEN implements it):
  * The `build` subparser gains `--tester-engine` (choices from
    `engine_registry.available_engines()`, default "local").
  * `cmd_build` reads `getattr(args, "tester_engine", "local")` and passes it as
    `Session(..., tester_engine=<value>)`.
  * Default stays "local" (offline); DeepSeek only when explicitly requested.
  * The existing `--engine` flag (builder engine) is UNCHANGED.

RED mechanisms:
  * parser tests: `--tester-engine` is unknown today -> argparse SystemExit; and
    `args.tester_engine` is missing on a default parse -> AttributeError.
  * wiring tests: a captured fake `Session` shows `cmd_build` passes no
    `tester_engine` today -> KeyError/None assertion failure.

Fully offline: no real build runs. `_require_ollama`, `_make_gate`, `_make_engine`
and `Session` are monkeypatched; nothing reaches Ollama, the network, or disk.
No production code is changed in this phase.
"""

import argparse
import types

import pytest

import autocorp


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _install_fake_session(monkeypatch):
    """Replace autocorp.Session with a kwarg-capturing fake. Returns the dict."""
    captured = {}

    class _FakeSession:
        def __init__(self, gate, **kwargs):
            captured.update(kwargs)
            self.builder = types.SimpleNamespace(engine=None)

        def run(self, request):
            return {"status": "passed"}

    monkeypatch.setattr(autocorp, "Session", _FakeSession)
    monkeypatch.setattr(autocorp, "_require_ollama", lambda: True)
    monkeypatch.setattr(autocorp, "_make_gate", lambda auto=False, watchdog=False: object())
    monkeypatch.setattr(autocorp, "_make_engine",
                        lambda name="local": types.SimpleNamespace(name=name))
    return captured


def _build_args(**overrides):
    """A Namespace with the attributes cmd_build reads. `tester_engine` is only
    present when explicitly passed in overrides (mirrors getattr defaulting)."""
    base = dict(
        request="build a thing", engine="local", auto=False, watchdog=False,
        review=False, accept=False, accept_strict=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
# A/C. Parser accepts --tester-engine with explicit engine names
# --------------------------------------------------------------------------- #
def test_parser_accepts_tester_engine_deepseek():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req", "--tester-engine", "deepseek"])
    assert args.tester_engine == "deepseek"      # RED: unknown flag -> SystemExit


def test_parser_accepts_tester_engine_local():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req", "--tester-engine", "local"])
    assert args.tester_engine == "local"         # RED: unknown flag -> SystemExit


def test_parser_accepts_tester_engine_claude():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req", "--tester-engine", "claude"])
    assert args.tester_engine == "claude"        # RED: unknown flag -> SystemExit


# --------------------------------------------------------------------------- #
# B. Default tester engine is local (DeepSeek never implicit)
# --------------------------------------------------------------------------- #
def test_parser_default_tester_engine_is_local():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req"])
    assert args.tester_engine == "local"         # RED: attribute missing today


# --------------------------------------------------------------------------- #
# D. cmd_build forwards an explicit tester_engine into Session
# --------------------------------------------------------------------------- #
def test_cmd_build_forwards_tester_engine(monkeypatch):
    captured = _install_fake_session(monkeypatch)
    autocorp.cmd_build(_build_args(tester_engine="deepseek"))
    assert captured.get("tester_engine") == "deepseek"   # RED: not passed today


# --------------------------------------------------------------------------- #
# D/B. cmd_build defaults tester_engine to local when the arg is absent
# --------------------------------------------------------------------------- #
def test_cmd_build_defaults_tester_engine_local(monkeypatch):
    captured = _install_fake_session(monkeypatch)
    autocorp.cmd_build(_build_args())             # no tester_engine attr
    assert captured.get("tester_engine") == "local"      # RED: not passed today


# --------------------------------------------------------------------------- #
# E. Backward compatibility: existing build flags/behaviour unchanged
#    (guard test - must stay GREEN)
# --------------------------------------------------------------------------- #
def test_existing_build_flags_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req"])
    assert args.engine == "local"                # --engine default preserved
    assert args.review is False
    assert args.accept is False
    assert args.accept_strict is False
