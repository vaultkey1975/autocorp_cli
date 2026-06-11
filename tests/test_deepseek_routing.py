#!/usr/bin/env python3
"""
DeepSeek routing tests  (AutoCorp CLI - Phase 8G RED)
=====================================================

Drives Phase 8G: routing suitable work to DeepSeek while reserving Claude for
explicit architecture work - WITHOUT changing the Model Router, the engines, the
registry, or any gate. The integration is config-only:

  * config.DEEPSEEK_ROUTE_RULES - an ordered, named ruleset (first-match-wins).
  * a routing TOGGLE (env AUTOCORP_DEEPSEEK_ROUTING) that decides whether
    config.DEFAULT_ROUTE_RULES is populated from DEEPSEEK_ROUTE_RULES or stays
    empty. Default OFF => DEFAULT_ROUTE_RULES == [] => behaviour unchanged.

These tests are RED on purpose and for EXACTLY TWO reasons:
  1. `config.DEEPSEEK_ROUTE_RULES` does not exist yet (AttributeError).
  2. the routing toggle is not honoured yet (DEFAULT_ROUTE_RULES ignores the env).

Everything is offline: routing is pure predicate evaluation. Claude availability
is mocked via `shutil.which` (the CLI is never invoked); DeepSeek's availability
is always True and routing never calls generate(), so no network and no API key
are used.

Thresholds under test (exactly as approved):
  * architecture-to-claude : request_contains
        ["architecture","design system","framework","plugin system",
         "refactor","migrate"]
  * large-build-to-claude  : min_files 8
  * small-python-to-deepseek : language python, max_files 5
  * simple-types-to-deepseek : project_type [cli, script, sqlite]
"""

import importlib
from unittest.mock import patch

import pytest

import config


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _files(n):
    return [{"path": f"f{i}.py", "purpose": "p"} for i in range(n)]


def _ctx(request="build a thing", project_type="api", language="python", files=None):
    from brains.model_router import context_from
    plan = {
        "project_name": "demo", "project_type": project_type, "language": language,
        "files": files if files is not None else _files(1),
    }
    return context_from(request, plan)


def _router_from_deepseek_rules(default_engine="local"):
    """Build a ModelRouter from the proposed config.DEEPSEEK_ROUTE_RULES.
    RED: raises AttributeError until GREEN adds DEEPSEEK_ROUTE_RULES to config."""
    from brains.model_router import ModelRouter, Rule
    rules = [Rule(**r) for r in config.DEEPSEEK_ROUTE_RULES]
    return ModelRouter(rules, default_engine=default_engine)


def _claude_present():
    """Mock the Claude CLI as installed so claude rules resolve (don't fall back)."""
    return patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude")


def _reload_config(monkeypatch, routing_on):
    if routing_on is None:
        monkeypatch.delenv("AUTOCORP_DEEPSEEK_ROUTING", raising=False)
    else:
        monkeypatch.setenv("AUTOCORP_DEEPSEEK_ROUTING", routing_on)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore_config():
    """Re-sync the config module after any test that reloaded it under a custom
    env, so module globals never leak into other tests."""
    yield
    importlib.reload(config)


# --------------------------------------------------------------------------- #
# Ruleset shape + engine-name integrity
# --------------------------------------------------------------------------- #
def test_deepseek_route_rules_exist():
    assert isinstance(config.DEEPSEEK_ROUTE_RULES, list)
    assert config.DEEPSEEK_ROUTE_RULES  # non-empty
    for r in config.DEEPSEEK_ROUTE_RULES:
        assert {"name", "engine", "match"} <= set(r)


def test_every_rule_names_registered_engine():
    from brains import engine_registry
    valid = set(engine_registry.available_engines())
    for r in config.DEEPSEEK_ROUTE_RULES:
        assert r["engine"] in valid


# --------------------------------------------------------------------------- #
# DeepSeek routing (low-cost generation)
# --------------------------------------------------------------------------- #
def test_small_python_build_routes_to_deepseek():
    # python, 2 files, non-simple project_type, no architecture keyword.
    decision = _router_from_deepseek_rules().route(
        _ctx(request="build a small tool", project_type="api",
             language="python", files=_files(2))
    )
    assert decision.engine == "deepseek"
    assert decision.rule == "small-python-to-deepseek"


def test_simple_project_type_routes_to_deepseek():
    # cli project, non-python (so small-python rule does NOT match first).
    decision = _router_from_deepseek_rules().route(
        _ctx(request="build a cli", project_type="cli",
             language="javascript", files=_files(2))
    )
    assert decision.engine == "deepseek"
    assert decision.rule == "simple-types-to-deepseek"


# --------------------------------------------------------------------------- #
# Claude routing (difficult architecture work)
# --------------------------------------------------------------------------- #
def test_architecture_request_routes_to_claude():
    with _claude_present():
        decision = _router_from_deepseek_rules().route(
            _ctx(request="design system for billing", project_type="api",
                 language="python", files=_files(3))
        )
    assert decision.engine == "claude"
    assert decision.rule == "architecture-to-claude"


def test_large_build_routes_to_claude():
    with _claude_present():
        decision = _router_from_deepseek_rules().route(
            _ctx(request="build a big app", project_type="api",
                 language="python", files=_files(8))
        )
    assert decision.engine == "claude"
    assert decision.rule == "large-build-to-claude"


def test_architecture_precedes_deepseek():
    # A build that WOULD match small-python-to-deepseek, but the request also
    # contains an architecture keyword -> architecture rule wins (first-match).
    with _claude_present():
        decision = _router_from_deepseek_rules().route(
            _ctx(request="refactor the payments module", project_type="api",
                 language="python", files=_files(2))
        )
    assert decision.rule == "architecture-to-claude"


# --------------------------------------------------------------------------- #
# Fallback (everything else stays on the free local engine)
# --------------------------------------------------------------------------- #
def test_unmatched_build_falls_back_to_local():
    decision = _router_from_deepseek_rules(default_engine="local").route(
        _ctx(request="build something", project_type="api",
             language="go", files=_files(3))
    )
    assert decision.engine == "local"
    assert decision.rule == "fallback"


# --------------------------------------------------------------------------- #
# Toggle support (default OFF preserves current behaviour)
# --------------------------------------------------------------------------- #
def test_toggle_off_keeps_default_rules_empty(monkeypatch):
    cfg = _reload_config(monkeypatch, routing_on=None)
    assert cfg.DEEPSEEK_ROUTE_RULES          # rules are defined...
    assert cfg.DEFAULT_ROUTE_RULES == []     # ...but inactive while toggle is off


def test_toggle_on_activates_deepseek_rules(monkeypatch):
    cfg = _reload_config(monkeypatch, routing_on="1")
    assert cfg.DEFAULT_ROUTE_RULES == cfg.DEEPSEEK_ROUTE_RULES
    assert cfg.DEFAULT_ROUTE_RULES           # non-empty when toggle is on
