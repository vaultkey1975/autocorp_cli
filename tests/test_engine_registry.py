#!/usr/bin/env python3
"""
Engine registry tests  (AutoCorp CLI - Phase 8A RED)
====================================================

Drives the design of `brains/engine_registry.py`: a single source of truth that
maps an engine name to a factory, so adding a new model (e.g. DeepSeek, later) is
a one-line `register()` instead of edits to an if-ladder plus argparse choices.

These tests are RED on purpose: the registry module does not exist yet, and the
CLI wiring (`autocorp._make_engine`, `--engine` choices) does not yet source from
it. The registry is imported lazily inside each test so every test fails
individually with a clear message rather than collapsing into one collection
error.

Fully offline: no engine is ever asked to generate; only construction/selection
is exercised, and the dummy factories return inert stand-ins.
"""

import pytest

from brains.base_engine import BaseEngine
from brains.local_engine import LocalEngine
from brains.claude_engine import ClaudeEngine


def _reg():
    """Lazy import so a missing module surfaces as a per-test failure (RED)."""
    from brains import engine_registry
    return engine_registry


class _DummyEngine(BaseEngine):
    name = "dummy"

    def generate(self, prompt, system=""):
        return "dummy-output"


# --------------------------------------------------------------------------- #
# Built-in resolution (group C)
# --------------------------------------------------------------------------- #
def test_create_local_returns_local_engine():
    assert isinstance(_reg().create("local"), LocalEngine)


def test_create_claude_returns_claude_engine():
    assert isinstance(_reg().create("claude"), ClaudeEngine)


def test_create_returns_baseengine_instances():
    assert isinstance(_reg().create("local"), BaseEngine)


def test_create_unknown_name_raises_valueerror():
    with pytest.raises(ValueError):
        _reg().create("no-such-engine")


def test_unknown_error_message_lists_valid_names():
    with pytest.raises(ValueError) as ei:
        _reg().create("no-such-engine")
    assert "local" in str(ei.value)


def test_create_is_case_insensitive():
    assert isinstance(_reg().create("LOCAL"), LocalEngine)


# --------------------------------------------------------------------------- #
# Listing (group C)
# --------------------------------------------------------------------------- #
def test_available_engines_includes_builtins():
    names = _reg().available_engines()
    assert "local" in names
    assert "claude" in names


def test_available_engines_is_sorted():
    names = _reg().available_engines()
    assert names == sorted(names)


def test_available_engines_returns_fresh_list():
    reg = _reg()
    names = reg.available_engines()
    names.append("mutation")
    assert "mutation" not in reg.available_engines()


# --------------------------------------------------------------------------- #
# Registration (group C) - proves DeepSeek-readiness without implementing it
# --------------------------------------------------------------------------- #
def test_register_adds_new_engine():
    reg = _reg()
    reg.register("dummy_add", lambda **opts: _DummyEngine())
    assert "dummy_add" in reg.available_engines()


def test_create_resolves_registered_engine():
    reg = _reg()
    reg.register("dummy_resolve", lambda **opts: _DummyEngine())
    assert isinstance(reg.create("dummy_resolve"), _DummyEngine)


def test_register_duplicate_name_rejected():
    reg = _reg()
    reg.register("dummy_dup", lambda **opts: _DummyEngine())
    with pytest.raises(ValueError):
        reg.register("dummy_dup", lambda **opts: _DummyEngine())


def test_register_rejects_blank_name():
    with pytest.raises(ValueError):
        _reg().register("", lambda **opts: _DummyEngine())


# --------------------------------------------------------------------------- #
# Option pass-through (group C)
# --------------------------------------------------------------------------- #
def test_create_passes_options_to_factory():
    reg = _reg()
    captured = {}

    def factory(**opts):
        captured.update(opts)
        return _DummyEngine()

    reg.register("dummy_capture", factory)
    reg.create("dummy_capture", model="x", temperature=0.0)
    assert captured.get("model") == "x"
    assert captured.get("temperature") == 0.0


def test_create_local_passes_model_option():
    assert _reg().create("local", model="zzz").model == "zzz"


def test_create_claude_passes_timeout_option():
    assert _reg().create("claude", timeout=7).timeout == 7


# --------------------------------------------------------------------------- #
# CLI wiring (group D) - autocorp must source engines from the registry
# --------------------------------------------------------------------------- #
def test_make_engine_local_returns_local_engine():
    import autocorp
    assert isinstance(autocorp._make_engine("local"), LocalEngine)


def test_make_engine_unknown_raises_valueerror():
    # RED: today _make_engine silently returns LocalEngine for any non-"claude"
    # name. GREEN routes through the registry, which raises on unknown names.
    import autocorp
    with pytest.raises(ValueError):
        autocorp._make_engine("no-such-engine")


def test_parser_accepts_all_registered_engines():
    import autocorp
    parser = autocorp.build_parser()
    for name in _reg().available_engines():
        args = parser.parse_args(["build", "req", "--engine", name])
        assert args.engine == name


def test_parser_rejects_unknown_engine():
    import autocorp
    parser = autocorp.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["build", "req", "--engine", "no-such-engine"])
