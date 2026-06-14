#!/usr/bin/env python3
"""
Self-heal CLI flag  (AutoCorp CLI - Phase DS9 RED)
==================================================

Drives Phase DS9: expose the already-wired DS6 self-heal path to CLI users. The
internal capability is complete - `Session(self_heal=True)` drives the DS6 repair
chain through the (DS7/DS8) tester engine - but `autocorp.py` can't enable it:
the `build` subparser has no `--self-heal` flag and `cmd_build` never forwards
`self_heal`, so it always defaults to `False`. This is the final missing switch
for the DeepSeek self-heal repair path.

This phase is CLI EXPOSURE ONLY. It does NOT touch DeepSeekEngine, ModelRouter,
Builder routing, the DS6 repair wiring, DS7 tester construction, or DS8
tester-engine selection.

Pinned design (RED until GREEN implements it):
  * The `build` subparser gains `--self-heal` (action="store_true",
    default False).
  * `cmd_build` reads `getattr(args, "self_heal", False)` and passes it as
    `Session(..., self_heal=<value>)`.
  * Default stays False (advisory acceptance only; no repair cycle).
  * `--self-heal` composes with `--tester-engine` so a user can run
    DeepSeek-powered self-heal repairs.

RED mechanisms:
  * parser tests: `--self-heal` is unknown today -> argparse SystemExit; and
    `args.self_heal` is missing on a default parse -> AttributeError.
  * wiring tests: a captured fake `Session` shows `cmd_build` passes no
    `self_heal` today -> None assertion failure.

Fully offline: no real build runs. `_require_ollama`, `_make_gate`,
`_make_engine` and `Session` are monkeypatched; nothing reaches Ollama, the
network, or disk. No production code is changed in this phase.
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
    """A Namespace with the attributes cmd_build reads. `self_heal` /
    `tester_engine` are only present when explicitly passed (mirrors getattr
    defaulting)."""
    base = dict(
        request="build a thing", engine="local", auto=False, watchdog=False,
        review=False, accept=False, accept_strict=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
# A. Parser accepts --self-heal (store_true)
# --------------------------------------------------------------------------- #
def test_parser_accepts_self_heal_flag():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req", "--self-heal"])
    assert args.self_heal is True                 # RED: unknown flag -> SystemExit


# --------------------------------------------------------------------------- #
# B. Default self_heal is False (no implicit repair cycle)
# --------------------------------------------------------------------------- #
def test_parser_default_self_heal_is_false():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req"])
    assert args.self_heal is False               # RED: attribute missing today


# --------------------------------------------------------------------------- #
# C. cmd_build forwards self_heal=True into Session
# --------------------------------------------------------------------------- #
def test_cmd_build_forwards_self_heal_true(monkeypatch):
    captured = _install_fake_session(monkeypatch)
    autocorp.cmd_build(_build_args(self_heal=True))
    assert captured.get("self_heal") is True     # RED: not passed today


# --------------------------------------------------------------------------- #
# C/B. cmd_build defaults self_heal to False when the arg is absent
# --------------------------------------------------------------------------- #
def test_cmd_build_defaults_self_heal_false(monkeypatch):
    captured = _install_fake_session(monkeypatch)
    autocorp.cmd_build(_build_args())            # no self_heal attr
    assert captured.get("self_heal") is False    # RED: not passed today


# --------------------------------------------------------------------------- #
# D. --tester-engine deepseek --self-heal parse together
# --------------------------------------------------------------------------- #
def test_parser_combines_tester_engine_and_self_heal():
    parser = autocorp.build_parser()
    args = parser.parse_args(
        ["build", "req", "--tester-engine", "deepseek", "--self-heal"])
    assert args.tester_engine == "deepseek"      # RED: unknown flags -> SystemExit
    assert args.self_heal is True


# --------------------------------------------------------------------------- #
# D. cmd_build forwards BOTH tester_engine and self_heal together
# --------------------------------------------------------------------------- #
def test_cmd_build_forwards_tester_engine_and_self_heal(monkeypatch):
    captured = _install_fake_session(monkeypatch)
    autocorp.cmd_build(_build_args(tester_engine="deepseek", self_heal=True))
    assert captured.get("tester_engine") == "deepseek"
    assert captured.get("self_heal") is True     # RED: self_heal not passed today


# --------------------------------------------------------------------------- #
# E. Backward compatibility: existing build flags/behaviour unchanged
#    (guard test - must stay GREEN)
# --------------------------------------------------------------------------- #
def test_existing_build_flags_unchanged():
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req"])
    assert args.engine == "local"
    assert args.review is False
    assert args.accept is False
    assert args.accept_strict is False
