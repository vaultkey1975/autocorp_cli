#!/usr/bin/env python3
"""
Model Router  (AutoCorp CLI - brains)  [Model Abstraction Phase 8C]
==================================================================

A deterministic, rule-based router that decides WHICH registered engine handles
a build. It produces a RouteDecision from a RoutingContext using an ordered
ruleset (first match wins), with a safe fallback to a default engine.

Design principles:
  * DETERMINISTIC + MODEL-FREE: routing is pure predicate evaluation over a
    RoutingContext. No engine is asked to generate anything; no Ollama, no Claude
    CLI, no network is touched to DECIDE a route.
  * REGISTRY-NATIVE: rules name engines; the router validates names against
    `engine_registry.available_engines()` and resolves via `engine_registry.create`.
    When a new engine (e.g. DeepSeek, later) is registered, a rule naming it works
    with no router changes.
  * SAFE: an unknown engine name, or an engine that reports it isn't usable right
    now (`available()` is False - e.g. the Claude CLI isn't installed), falls back
    to `default_engine` BEFORE the build starts, so routing can never break a
    build mid-stream. `fallback_used` records that this happened.

This phase is request/plan-level routing only. Per-file routing, JSON rule
loading, and model-assisted routing are intentionally out of scope.
"""

import re
from dataclasses import dataclass, field

from brains import engine_registry

# Words too generic to be useful routing tags (kept tiny and local).
_TAG_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclass
class RoutingContext:
    """The deterministic signal a routing decision is made from. Built from the
    request and the (already-produced) plan - no planner/Builder changes."""
    request: str = ""
    project_name: str = ""
    project_type: str = ""
    language: str = ""
    file_count: int = 0
    has_verbatim: bool = False
    tags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "project_name": self.project_name,
            "project_type": self.project_type,
            "language": self.language,
            "file_count": self.file_count,
            "has_verbatim": self.has_verbatim,
            "tags": list(self.tags),
        }


@dataclass
class Rule:
    """A declarative routing rule. `match` is a dict of conditions; ALL present
    conditions must hold (AND) for the rule to apply. Omitted conditions are
    ignored. Supported conditions:
        request_contains : list[str]  - any substring present in the request (OR)
        project_type     : list[str]  - project_type is any-of
        language         : str         - exact language match
        min_files        : int         - file_count >= min_files
        max_files        : int         - file_count <= max_files
    """
    name: str
    engine: str
    reason: str = ""
    match: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "engine": self.engine,
            "reason": self.reason,
            "match": dict(self.match or {}),
        }

    def matches(self, ctx: "RoutingContext") -> bool:
        m = self.match or {}

        contains = m.get("request_contains")
        if contains:
            req = (ctx.request or "").lower()
            if not any(str(sub).lower() in req for sub in contains):
                return False

        ptypes = m.get("project_type")
        if ptypes is not None and ctx.project_type not in ptypes:
            return False

        language = m.get("language")
        if language is not None and ctx.language != language:
            return False

        min_files = m.get("min_files")
        if min_files is not None and ctx.file_count < min_files:
            return False

        max_files = m.get("max_files")
        if max_files is not None and ctx.file_count > max_files:
            return False

        return True


@dataclass
class RouteDecision:
    """The router's verdict: which engine to use, which rule chose it, and whether
    a defensive fallback had to be taken."""
    engine: str
    rule: str = "fallback"
    reason: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "rule": self.rule,
            "reason": self.reason,
            "fallback_used": self.fallback_used,
        }


# --------------------------------------------------------------------------- #
# Context builder
# --------------------------------------------------------------------------- #
def context_from(request: str, plan: dict) -> RoutingContext:
    """Build a RoutingContext from the request and a structured plan. `has_verbatim`
    is True when any plan file carries a deterministic "content" field (i.e. the
    plan is template-driven)."""
    plan = plan or {}
    files = [f for f in (plan.get("files") or []) if isinstance(f, dict)]
    has_verbatim = any(
        isinstance(f.get("content"), str) and f.get("content").strip() for f in files
    )
    tags = _tags(request)
    return RoutingContext(
        request=request or "",
        project_name=plan.get("project_name", ""),
        project_type=plan.get("project_type", ""),
        language=plan.get("language", ""),
        file_count=len(plan.get("files") or []),
        has_verbatim=has_verbatim,
        tags=tags,
    )


def _tags(request: str) -> list:
    seen, out = set(), []
    for w in _TAG_RE.findall((request or "").lower()):
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
class ModelRouter:
    def __init__(self, ruleset=None, default_engine: str = "local"):
        self.ruleset = list(ruleset or [])
        self.default_engine = default_engine

    def route(self, ctx: RoutingContext) -> RouteDecision:
        """Pick an engine for `ctx`. Deterministic, first-match-wins, with a safe
        fallback to `default_engine`. Never calls a model."""
        for rule in self.ruleset:
            try:
                if rule.matches(ctx):
                    return self._resolve(rule.engine, rule.name, rule.reason)
            except Exception:  # noqa: BLE001 - a broken rule must not break routing
                continue
        return RouteDecision(
            engine=self.default_engine, rule="fallback",
            reason=f"no rule matched; using default engine '{self.default_engine}'",
        )

    def _resolve(self, engine: str, rule_name: str, reason: str) -> RouteDecision:
        """Validate + availability-probe `engine`; fall back to default if it is
        unknown or not usable right now. The probe constructs the engine but never
        calls generate()."""
        if engine not in engine_registry.available_engines():
            return self._fallback(rule_name, f"engine '{engine}' is not registered")
        try:
            if not engine_registry.create(engine).available():
                return self._fallback(rule_name, f"engine '{engine}' is unavailable")
        except Exception as e:  # noqa: BLE001 - probe failure -> safe fallback
            return self._fallback(rule_name, f"engine '{engine}' probe failed ({e})")
        return RouteDecision(engine=engine, rule=rule_name, reason=reason)

    def _fallback(self, rule_name: str, why: str) -> RouteDecision:
        return RouteDecision(
            engine=self.default_engine, rule=rule_name,
            reason=f"{why}; falling back to '{self.default_engine}'",
            fallback_used=True,
        )
