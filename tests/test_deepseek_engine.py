#!/usr/bin/env python3
"""
DeepSeek engine tests  (AutoCorp CLI - Phase 8G/Week-1 RED, SHADOW)
==================================================================

Drives the design of a SHADOW-MODE DeepSeek engine: a code-generation engine
that sits behind the same BaseEngine seam as LocalEngine and ClaudeEngine, is
discoverable through the Engine Registry, and is therefore selectable via
`--engine deepseek` - but is NOT routed to by the Model Router and NOT wired
into any build. This is a discovery-only deployment.

Design intent (mirrors LocalEngine, the lowest-risk shape):
  * DeepSeekEngine talks to Ollama via `core.llm.generate`, exactly like
    LocalEngine, but defaults to a DeepSeek model tag instead of llama3.2.
  * No new network/subprocess/secret surface; availability follows the local
    pattern (always considered reachable - transport errors surface at
    generate() time and are wrapped as EngineError).

These tests are RED on purpose: `brains/deepseek_engine.py` does not exist yet
and the registry does not know the name "deepseek". The engine class is imported
lazily inside `_dse()` (the same convention test_engine_registry.py uses) so each
test fails individually with a clear message rather than collapsing into one
collection error.

Fully offline: the engine path patches `core.llm.generate`, so Ollama is never
contacted; registry/parser tests only construct or list, never generate.
"""

from unittest.mock import patch

import pytest

from core import llm
from brains.base_engine import BaseEngine, EngineError


def _dse():
    """Lazy import so a missing module surfaces as a per-test failure (RED)."""
    from brains.deepseek_engine import DeepSeekEngine
    return DeepSeekEngine


def _reg():
    from brains import engine_registry
    return engine_registry


# --------------------------------------------------------------------------- #
# Engine-level behaviour (mirrors the LocalEngine group) - no Ollama
# --------------------------------------------------------------------------- #
def test_deepseek_engine_is_a_base_engine():
    assert isinstance(_dse()(), BaseEngine)


def test_deepseek_default_model_is_a_deepseek_tag():
    # Default must be a DeepSeek model, not llama3.2 - so shadow runs are
    # unmistakably DeepSeek. Exact tag is intentionally not pinned.
    assert "deepseek" in _dse()().model.lower()


def test_deepseek_honors_custom_model():
    assert _dse()(model="deepseek-custom").model == "deepseek-custom"


def test_deepseek_delegates_prompt_system_and_params(monkeypatch):
    # Pin the LOCAL transport regardless of whether the ambient environment
    # happens to export DEEPSEEK_API_KEY - otherwise the engine would silently
    # take the API transport (see brains/deepseek_engine.py) and this mock
    # would never be consulted. api_key="" alone can't do this: the
    # constructor resolves `api_key or os.environ.get(...)`, so an explicit
    # blank string still falls through to a present env var.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch("core.llm.generate", return_value="GENERATED") as g:
        engine = _dse()(model="m", temperature=0.5)
        out = engine.generate("THE PROMPT", system="THE SYSTEM")
    assert out == "GENERATED"
    g.assert_called_once_with(
        "THE PROMPT", system="THE SYSTEM", model="m", temperature=0.5
    )


def test_deepseek_returns_generated_text(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch("core.llm.generate", return_value="hello world"):
        assert _dse()().generate("p") == "hello world"


def test_deepseek_wraps_ollama_error_as_engine_error(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch("core.llm.generate", side_effect=llm.OllamaError("server down")):
        with pytest.raises(EngineError):
            _dse()().generate("p")


def test_deepseek_exposes_available_true():
    # Local-backed: always considered reachable (errors surface at generate()).
    assert _dse()().available() is True


# --------------------------------------------------------------------------- #
# Registry discovery (mirrors group C) - proves selectable, NOT routed
# --------------------------------------------------------------------------- #
def test_create_deepseek_returns_deepseek_engine():
    assert isinstance(_reg().create("deepseek"), _dse())


def test_create_deepseek_returns_baseengine_instance():
    assert isinstance(_reg().create("deepseek"), BaseEngine)


def test_available_engines_includes_deepseek():
    assert "deepseek" in _reg().available_engines()


def test_create_deepseek_is_case_insensitive():
    assert isinstance(_reg().create("DEEPSEEK"), _dse())


def test_create_deepseek_passes_model_option():
    assert _reg().create("deepseek", model="zzz").model == "zzz"


# --------------------------------------------------------------------------- #
# CLI discovery (mirrors group D) - `--engine deepseek` is accepted
# --------------------------------------------------------------------------- #
def test_parser_accepts_deepseek_engine():
    import autocorp
    parser = autocorp.build_parser()
    args = parser.parse_args(["build", "req", "--engine", "deepseek"])
    assert args.engine == "deepseek"
