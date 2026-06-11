#!/usr/bin/env python3
"""
Engine Registry  (AutoCorp CLI - brains)  [Model Abstraction Phase 1]
====================================================================

A single source of truth that maps an engine NAME to a FACTORY that builds it.
The Builder only ever talks to the BaseEngine interface; this registry decides
WHICH engine answers to a given `--engine <name>`.

Why this exists:
  * Adding a new model (e.g. DeepSeek, later) becomes a one-line `register()`
    instead of edits scattered across an if-ladder plus argparse `choices`.
  * The CLI sources both engine construction (`create`) and the valid `--engine`
    choices (`available_engines`) from here, so the two can never drift.

Public API:
    create(name, **opts) -> BaseEngine     # build the named engine
    register(name, factory)                # add/replace-prohibited: new names only
    available_engines() -> list[str]       # sorted, fresh list of known names

Names are normalised to lowercase, so `--engine LOCAL` and `--engine local` are
the same. `create` raises ValueError (listing the valid names) on an unknown
name; `register` raises ValueError on a blank or already-registered name.

A factory is any callable `factory(**opts) -> BaseEngine`. The built-ins forward
the same options the engines accepted before (model/temperature for local,
command/timeout for claude), so behaviour is unchanged.
"""

from brains.base_engine import BaseEngine
from brains.local_engine import LocalEngine
from brains.claude_engine import ClaudeEngine
from brains.deepseek_engine import DeepSeekEngine


# name -> callable(**opts) -> BaseEngine
_FACTORIES = {}


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def register(name: str, factory) -> None:
    """Register a NEW engine name. Raises ValueError on a blank name or one that
    is already registered (registration is add-only, so a typo can't silently
    shadow a built-in)."""
    key = _normalize(name)
    if not key:
        raise ValueError("Engine name must be a non-empty string.")
    if not callable(factory):
        raise ValueError(f"Engine factory for '{key}' must be callable.")
    if key in _FACTORIES:
        raise ValueError(f"Engine '{key}' is already registered.")
    _FACTORIES[key] = factory


def create(name: str, **opts) -> BaseEngine:
    """Build the engine registered under `name`, forwarding `**opts` to its
    factory. Raises ValueError (listing valid names) if the name is unknown."""
    key = _normalize(name)
    factory = _FACTORIES.get(key)
    if factory is None:
        valid = ", ".join(available_engines()) or "(none)"
        raise ValueError(f"Unknown engine '{name}'. Available engines: {valid}.")
    return factory(**opts)


def available_engines() -> list:
    """Return a fresh, sorted list of registered engine names. Callers may mutate
    the returned list without affecting the registry."""
    return sorted(_FACTORIES)


# --------------------------------------------------------------------------- #
# Built-in engines (identical behaviour to the previous _make_engine if-ladder)
# --------------------------------------------------------------------------- #
register("local", lambda **opts: LocalEngine(**opts))
register("claude", lambda **opts: ClaudeEngine(**opts))
# Shadow-mode (Week 1): discoverable + selectable via --engine deepseek, but the
# Model Router has NO rule naming it, so no build is ever routed here.
register("deepseek", lambda **opts: DeepSeekEngine(**opts))
