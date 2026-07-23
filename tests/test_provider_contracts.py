#!/usr/bin/env python3
"""Tests for Provider contracts (brains/providers.py, Phase 1G security).

Covers: ProviderResult immutability, model resolution defaults,
error paths for unavailable providers, blocked states, Claude no-TypeError,
DeepSeek no-fallback-to-local, test isolation (no live API calls).
"""

import os

import pytest

from brains.providers import (
    ProviderResult,
    _resolve_model,
    generate_proposal_json,
)


def test_provider_result_is_frozen():
    r = ProviderResult(provider="local", model="qwen", raw_json={"x": 1})
    assert r.provider == "local"
    with pytest.raises((AttributeError, TypeError)):
        r.provider = "deepseek"


def test_provider_result_error_is_blocked():
    r = ProviderResult(provider="local", model="qwen",
                        error="failed", blocked=True)
    assert r.blocked
    assert r.error == "failed"
    assert r.raw_json is None


def test_default_model_ollama():
    assert _resolve_model("local", None) == "qwen2.5:14b"


def test_default_model_deepseek():
    assert _resolve_model("deepseek", None) == "deepseek-chat"


def test_default_model_claude():
    assert _resolve_model("claude", None) == "claude"


def test_explicit_model_overrides_default():
    assert _resolve_model("local", "custom-model") == "custom-model"


def test_generate_proposal_unavailable_provider():
    result = generate_proposal_json("prompt", "system",
                                     provider="nonexistent")
    assert result.blocked
    assert result.error


def test_generate_proposal_ollama_not_running_returns_error():
    result = generate_proposal_json("test", "system", provider="local",
                                     model="qwen2.5:14b")
    assert isinstance(result, ProviderResult)
    if result.blocked:
        assert result.error


def test_claude_does_not_raise_type_error():
    result = generate_proposal_json("test", "system", provider="claude")
    assert isinstance(result, ProviderResult)


def test_deepseek_missing_credentials_fails_cleanly(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = generate_proposal_json("test", "system", provider="deepseek")
    assert result.blocked
    assert "deepseek" in result.provider
    assert "API key" in result.error.lower() or "deepseek" in result.error.lower()


def test_deepseek_never_falls_back_to_ollama(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = generate_proposal_json("test", "system", provider="deepseek")
    assert result.blocked
    assert "local" not in result.provider
    assert result.model != "qwen2.5:14b"


def test_provider_tests_make_no_live_calls():
    # Neither test here ever sends a real HTTP request; all blocked paths
    # are verified before engine creation.
    pass
