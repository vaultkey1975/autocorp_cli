#!/usr/bin/env python3
"""
Model Router core tests  (AutoCorp CLI - Phase 8C RED)
======================================================

Drives the design of `brains/model_router.py`: a deterministic, rule-based router
that picks WHICH registered engine handles a build, without any model call.

RED: `brains/model_router.py` does not exist yet, so every test that touches it
fails. The router is imported lazily inside helpers/tests so each fails
individually with a clear message rather than as one collection error.

Fully offline: routing decisions are pure predicate evaluation. Engine
availability is probed via the registry's engines (LocalEngine is always
available; ClaudeEngine's availability is `shutil.which`, which we mock - the
Claude CLI is never invoked, no network).
"""

from unittest.mock import patch

import pytest

from brains.base_engine import BaseEngine


# --------------------------------------------------------------------------- #
# Lazy import helpers (per-test RED failures)
# --------------------------------------------------------------------------- #
def _router(ruleset, default_engine="local"):
    from brains.model_router import ModelRouter
    return ModelRouter(ruleset, default_engine=default_engine)


def _rule(**kwargs):
    from brains.model_router import Rule
    return Rule(**kwargs)


def _files(n):
    return [{"path": f"f{i}.py", "purpose": "p"} for i in range(n)]


def _ctx(request="build a thing", project_type="cli", language="python", files=None):
    from brains.model_router import context_from
    plan = {
        "project_name": "demo", "project_type": project_type, "language": language,
        "files": files if files is not None else [{"path": "a.py", "purpose": "p"}],
    }
    return context_from(request, plan)


# --------------------------------------------------------------------------- #
# RoutingContext
# --------------------------------------------------------------------------- #
def test_routingcontext_from_plan_fields():
    ctx = _ctx(request="build an api", project_type="api", language="python",
               files=_files(3))
    assert ctx.project_type == "api"
    assert ctx.language == "python"
    assert ctx.file_count == 3


def test_routingcontext_has_verbatim_true():
    from brains.model_router import context_from
    plan = {"project_name": "d", "files": [{"path": "db.py", "content": "X = 1\n"}]}
    assert context_from("r", plan).has_verbatim is True


def test_routingcontext_has_verbatim_false():
    assert _ctx(files=_files(2)).has_verbatim is False


def test_routingcontext_to_dict_schema():
    d = _ctx().to_dict()
    for key in ("request", "project_name", "project_type", "language",
                "file_count", "has_verbatim", "tags"):
        assert key in d


# --------------------------------------------------------------------------- #
# Rule / RouteDecision schema
# --------------------------------------------------------------------------- #
def test_rule_to_dict_schema():
    d = _rule(name="r1", engine="claude", reason="why",
              match={"language": "python"}).to_dict()
    for key in ("name", "engine", "reason", "match"):
        assert key in d


def test_routedecision_to_dict_schema():
    decision = _router([]).route(_ctx())
    d = decision.to_dict()
    for key in ("engine", "rule", "reason", "fallback_used"):
        assert key in d


def test_route_returns_routedecision_instance():
    from brains.model_router import RouteDecision
    assert isinstance(_router([]).route(_ctx()), RouteDecision)


# --------------------------------------------------------------------------- #
# Routing behaviour
# --------------------------------------------------------------------------- #
def test_empty_ruleset_passthrough_to_default():
    decision = _router([], default_engine="local").route(_ctx())
    assert decision.engine == "local"
    assert decision.rule == "fallback"


def test_fallback_when_no_rule_matches():
    rules = [_rule(name="web-only", engine="claude", reason="x",
                   match={"project_type": ["web"]})]
    decision = _router(rules, default_engine="local").route(_ctx(project_type="cli"))
    assert decision.engine == "local"
    assert decision.rule == "fallback"


def test_first_match_wins():
    rules = [
        _rule(name="r1", engine="local", reason="first", match={"project_type": ["api"]}),
        _rule(name="r2", engine="claude", reason="second", match={"language": "python"}),
    ]
    decision = _router(rules).route(_ctx(project_type="api", language="python"))
    assert decision.engine == "local"
    assert decision.rule == "r1"


def test_match_request_contains():
    rules = [_rule(name="api-words", engine="local", reason="x",
                   match={"request_contains": ["api", "service"]})]
    decision = _router(rules).route(_ctx(request="build a payments service"))
    assert decision.rule == "api-words"


def test_no_match_request_contains():
    rules = [_rule(name="api-words", engine="local", reason="x",
                   match={"request_contains": ["api", "service"]})]
    decision = _router(rules).route(_ctx(request="build a calculator"))
    assert decision.rule == "fallback"


def test_match_project_type_any_of():
    rules = [_rule(name="apis", engine="local", reason="x",
                   match={"project_type": ["api", "web"]})]
    assert _router(rules).route(_ctx(project_type="web")).rule == "apis"


def test_match_min_files():
    rules = [_rule(name="big", engine="local", reason="x", match={"min_files": 4})]
    assert _router(rules).route(_ctx(files=_files(5))).rule == "big"


def test_no_match_min_files():
    rules = [_rule(name="big", engine="local", reason="x", match={"min_files": 4})]
    assert _router(rules).route(_ctx(files=_files(2))).rule == "fallback"


def test_match_max_files():
    rules = [_rule(name="small", engine="local", reason="x", match={"max_files": 3})]
    assert _router(rules).route(_ctx(files=_files(2))).rule == "small"


def test_match_language_equality():
    rules = [_rule(name="py", engine="local", reason="x", match={"language": "python"})]
    assert _router(rules).route(_ctx(language="python")).rule == "py"


def test_no_match_language():
    rules = [_rule(name="py", engine="local", reason="x", match={"language": "python"})]
    assert _router(rules).route(_ctx(language="javascript")).rule == "fallback"


def test_deterministic_routing():
    rules = [_rule(name="py", engine="local", reason="x", match={"language": "python"})]
    router = _router(rules)
    ctx = _ctx(language="python")
    assert router.route(ctx).to_dict() == router.route(ctx).to_dict()


# --------------------------------------------------------------------------- #
# Defensive fallback
# --------------------------------------------------------------------------- #
def test_unknown_engine_falls_back():
    rules = [_rule(name="bad", engine="no-such-engine", reason="x",
                   match={"language": "python"})]
    decision = _router(rules, default_engine="local").route(_ctx(language="python"))
    assert decision.engine == "local"
    assert decision.fallback_used is True


def test_unavailable_engine_falls_back():
    rules = [_rule(name="to-claude", engine="claude", reason="x",
                   match={"language": "python"})]
    with patch("brains.claude_engine.shutil.which", return_value=None):
        decision = _router(rules, default_engine="local").route(_ctx(language="python"))
    assert decision.engine == "local"
    assert decision.fallback_used is True


# --------------------------------------------------------------------------- #
# No model calls during routing
# --------------------------------------------------------------------------- #
def test_route_makes_no_model_calls():
    from brains import engine_registry

    created = []

    class _RecordingEngine(BaseEngine):
        name = "rec_router"

        def __init__(self):
            self.calls = []

        def generate(self, prompt, system=""):
            self.calls.append(prompt)
            return "x"

        def available(self):
            return True

    def factory(**opts):
        e = _RecordingEngine()
        created.append(e)
        return e

    if "rec_router" not in engine_registry.available_engines():
        engine_registry.register("rec_router", factory)
    try:
        rules = [_rule(name="rec", engine="rec_router", reason="x",
                       match={"language": "python"})]
        _router(rules).route(_ctx(language="python"))

        assert created  # the engine was constructed (for the availability probe)
        assert all(e.calls == [] for e in created)  # but generate() was never called
    finally:
        # Don't pollute the global registry for other tests (e.g. parser choices).
        engine_registry._FACTORIES.pop("rec_router", None)
