#!/usr/bin/env python3
"""
Provider Abstraction  (AutoCorp CLI - brains)  [Phase 1G]
============================================================

A thin provider layer wrapping the existing BaseEngine / engine_registry
infrastructure. Provides a narrow interface for AI repair proposal generation
with structured JSON output.

Public API:
    generate_proposal_json(prompt, system, provider, model) -> ProviderResult
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from brains import engine_registry
from brains.base_engine import EngineError
from core import llm

_DEFAULT_OLLAMA_MODEL = llm.MODEL
_DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    model: str
    raw_json: dict | None = None
    error: str = ""
    blocked: bool = False
    redactions: int = 0


def _resolve_model(provider: str, model: str | None) -> str:
    if model:
        return model
    if provider == "deepseek":
        return _DEFAULT_DEEPSEEK_MODEL
    if provider == "local":
        return _DEFAULT_OLLAMA_MODEL
    if provider == "claude":
        return "claude"
    return _DEFAULT_OLLAMA_MODEL


def generate_proposal_json(
    prompt: str,
    system: str,
    provider: str = "local",
    model: str | None = None,
) -> ProviderResult:
    """Generate a structured repair proposal by calling the selected AI
    provider. Returns a ProviderResult with the parsed JSON or error.

    Uses the existing engine_registry / BaseEngine infrastructure."""
    resolved_model = _resolve_model(provider, model)

    try:
        if provider == "claude":
            engine = engine_registry.create(provider)
        elif provider == "deepseek":
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key:
                return ProviderResult(
                    provider=provider,
                    model=resolved_model,
                    error="DeepSeek API key not configured. Set DEEPSEEK_API_KEY "
                          "environment variable.",
                    blocked=True,
                )
            engine = engine_registry.create(provider, model=resolved_model,
                                             api_key=api_key)
        else:
            engine = engine_registry.create(provider, model=resolved_model)
    except Exception as exc:
        return ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider unavailable: {exc}",
            blocked=True,
        )

    if not engine.available():
        return ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider '{provider}' is not available.",
            blocked=True,
        )

    try:
        raw = engine.generate(prompt, system)
    except EngineError as exc:
        return ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider error: {exc}",
            blocked=True,
        )
    except Exception as exc:
        return ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Unexpected error: {exc}",
            blocked=True,
        )

    try:
        data = llm.extract_json(raw)
    except Exception:
        return ProviderResult(
            provider=provider,
            model=resolved_model,
            error="Failed to parse provider response as valid JSON.",
            blocked=True,
        )

    return ProviderResult(
        provider=provider,
        model=resolved_model,
        raw_json=data,
    )
