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
import time
from dataclasses import dataclass, field

from brains import context_budget, engine_registry, provider_policy, usage_ledger
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
    repo_path: str | None = None,
    operation_name: str = "repair-proposal",
    target_path: str = "",
    routing_reason: str = "",
    explicit_user_selection: bool = False,
    context_manifest: dict | None = None,
) -> ProviderResult:
    """Generate a structured repair proposal by calling the selected AI
    provider. Returns a ProviderResult with the parsed JSON or error.

    Uses the existing engine_registry / BaseEngine infrastructure."""
    resolved_model = _resolve_model(provider, model)
    start = time.monotonic()
    status = "failed"
    validation = "not_validated"
    error_type = ""
    error_summary = ""
    raw_text = ""
    cleanup_requested = False
    cleanup_verified = False

    def finish(result: ProviderResult) -> ProviderResult:
        nonlocal cleanup_requested, cleanup_verified
        if provider == "local":
            cleanup_requested, cleanup_verified = _request_and_verify_unload(resolved_model)
        if repo_path:
            prompt_bytes = len(prompt.encode("utf-8")) + len(system.encode("utf-8"))
            estimated_input = context_budget.estimate_tokens_from_bytes(prompt_bytes)
            estimated_output = context_budget.estimate_tokens_from_bytes(len(raw_text.encode("utf-8")))
            baseline = estimated_input
            paid_used = 0 if provider == "local" else estimated_input
            avoided, pct = usage_ledger.calculate_savings(baseline, paid_used)
            usage_ledger.record(usage_ledger.LedgerEntry(
                repository_path=repo_path,
                repository_stable_identifier=context_budget.repository_fingerprint(repo_path),
                commit_sha=(context_manifest or {}).get("git_commit", ""),
                working_tree_fingerprint=(context_manifest or {}).get("working_tree_fingerprint", ""),
                operation_name=operation_name,
                target_path=target_path,
                provider=provider,
                model=resolved_model,
                routing_reason=routing_reason or f"explicit provider '{provider}' selected",
                explicit_user_selection=explicit_user_selection,
                context_fingerprint=(context_manifest or {}).get("context_fingerprint", ""),
                selected_context_manifest=context_manifest or {},
                prompt_bytes=prompt_bytes,
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=estimated_output,
                provider_reported_usage_available=False,
                cache_status="miss",
                result_status=status,
                validation_status=validation,
                error_type=error_type,
                error_summary=error_summary or result.error,
                elapsed_time_seconds=time.monotonic() - start,
                local_model_cleanup_requested=cleanup_requested,
                local_model_cleanup_verified=cleanup_verified,
                estimated_paid_baseline_tokens=baseline,
                estimated_paid_tokens_used=paid_used,
                estimated_paid_tokens_avoided=avoided,
                estimated_savings_percentage=pct,
                uncertainty_warnings=["actual provider token usage unavailable; token values are estimates"],
            ))
        return result

    # Phase 2B: route the prohibited-name / paid-authorization decision
    # through the single authoritative policy before any engine is touched.
    # A denial here short-circuits with the SAME ledger-recording path
    # (`finish`) every other outcome uses, so a policy denial is recorded
    # exactly like any other blocked outcome.
    decision = provider_policy.decide(
        operation_name, provider, explicit_user_selection=explicit_user_selection,
    )
    if not decision.permitted:
        status = "blocked"
        error_type = "ProviderPolicyDenied"
        error_summary = decision.denial_reason
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error=decision.denial_reason,
            blocked=True,
        ))

    try:
        if provider == "claude":
            engine = engine_registry.create(provider)
        elif provider == "deepseek":
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key:
                status = "blocked"
                error_type = "MissingCredentials"
                error_summary = "DeepSeek API key not configured."
                return finish(ProviderResult(
                    provider=provider,
                    model=resolved_model,
                    error="DeepSeek API key not configured. Set DEEPSEEK_API_KEY "
                          "environment variable.",
                    blocked=True,
                ))
            engine = engine_registry.create(provider, model=resolved_model,
                                             api_key=api_key)
        else:
            engine = engine_registry.create(provider, model=resolved_model)
    except Exception as exc:
        status = "blocked"
        error_type = exc.__class__.__name__
        error_summary = str(exc)
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider unavailable: {exc}",
            blocked=True,
        ))

    if not engine.available():
        status = "blocked"
        error_type = "ProviderUnavailable"
        error_summary = f"Provider '{provider}' is not available."
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider '{provider}' is not available.",
            blocked=True,
        ))

    try:
        raw = engine.generate(prompt, system)
        raw_text = raw
    except EngineError as exc:
        status = "blocked"
        error_type = exc.__class__.__name__
        error_summary = str(exc)
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Provider error: {exc}",
            blocked=True,
        ))
    except Exception as exc:
        status = "failed"
        error_type = exc.__class__.__name__
        error_summary = str(exc)
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error=f"Unexpected error: {exc}",
            blocked=True,
        ))

    try:
        data = llm.extract_json(raw)
    except Exception:
        status = "failed"
        validation = "failed"
        error_type = "InvalidProviderJSON"
        error_summary = "Failed to parse provider response as valid JSON."
        return finish(ProviderResult(
            provider=provider,
            model=resolved_model,
            error="Failed to parse provider response as valid JSON.",
            blocked=True,
        ))

    status = "success"
    validation = "parsed_json"
    return finish(ProviderResult(
        provider=provider,
        model=resolved_model,
        raw_json=data,
    ))


# Phase 2B: cleanup logic now lives once in provider_policy and is shared by
# every call site instead of being duplicated per module.
_request_and_verify_unload = provider_policy.request_and_verify_unload
