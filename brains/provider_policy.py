#!/usr/bin/env python3
"""
Provider Routing Policy  (AutoCorp CLI - brains)  [Phase 2B]
==============================================================

The single authoritative policy every model-capable production call site
routes through. It does not duplicate engine construction or ledger
persistence - it composes the existing Phase 2A/Era-2 architecture
(`brains.engine_registry`, `brains.usage_ledger`, `brains.context_budget`,
`core.llm`) into one place that:

  1. decides, deterministically and without ever calling a model, whether a
     requested provider may be used for a given operation;
  2. rejects the prohibited "mock" provider name in production;
  3. refuses a paid provider (claude, deepseek) unless the caller carries
     explicit user/CLI authorization - so an auto-routed build can never
     silently reach a paid provider;
  4. invokes the permitted engine at most once, records real usage-ledger
     evidence for every outcome (denied, blocked, failed, succeeded), and
     requests local-model cleanup only when generation was genuinely
     attempted.

Every production call site (`brains.builder`, `brains.tester`,
`brains.planner`, `brains.providers`, `brains.repair_content_generator`,
`core.orchestrator`) is expected to route real generation calls through
`invoke()` rather than calling `engine_registry.create(...).generate(...)`
directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from brains import context_budget, engine_registry, usage_ledger
from brains.base_engine import EngineError
from core import llm

PAID_PROVIDERS = ("claude", "deepseek")
PROHIBITED_PROVIDERS = ("mock",)


class ProviderPolicyError(RuntimeError):
    """Raised when the routing policy denies a provider-capable operation
    before any engine is touched (prohibited name, unregistered name, or a
    paid provider without explicit authorization)."""


@dataclass(frozen=True)
class RoutingDecision:
    operation_name: str
    provider: str
    permitted: bool
    explicit_user_selection: bool
    routing_reason: str = ""
    denial_reason: str = ""


def decide(operation_name: str, provider: str, *,
           explicit_user_selection: bool = False) -> RoutingDecision:
    """Deterministic, model-free routing decision. Never calls a model,
    never contacts a paid API, never contacts Ollama.

    Only two restrictions are enforced here: the "mock" name is always
    prohibited in production, and the two registered PAID providers
    (claude, deepseek) require explicit authorization. Every other name -
    including "local" and any caller-supplied engine identifier that is not
    one of AutoCorp's own registered engines (e.g. a custom BaseEngine
    reliability_engine or a test constructs directly) - is permitted. This
    module's job is to keep AutoCorp's own paid-provider names honest, not
    to become a second engine registry that has to know every legitimate
    BaseEngine implementation in the codebase.
    """
    name = (provider or "").strip().lower()

    if name in PROHIBITED_PROVIDERS:
        return RoutingDecision(
            operation_name, name, False, explicit_user_selection,
            denial_reason=f"provider '{name}' is prohibited in production",
        )

    if name in PAID_PROVIDERS and not explicit_user_selection:
        return RoutingDecision(
            operation_name, name, False, explicit_user_selection,
            denial_reason=(
                f"paid provider '{name}' requires explicit user authorization "
                "and none was recorded for this call"
            ),
        )

    reason = (
        "explicit CLI/user provider selection" if explicit_user_selection
        else "deterministic default routing to the local provider"
    )
    return RoutingDecision(operation_name, name, True, explicit_user_selection,
                            routing_reason=reason)


def default_model(provider: str) -> str:
    if provider == "deepseek":
        return "deepseek-chat"
    if provider == "claude":
        return "claude"
    return llm.MODEL


def _base_entry(*, repo_path: str, operation_name: str, provider: str,
                 model: str, target_path: str = "") -> dict:
    # Deliberately does NOT call context_budget.repository_fingerprint()
    # here: that walks and hashes the entire maintained source tree (real,
    # appropriate cost for a single repair-proposal/repair-content
    # evidence bundle), which is far too expensive - and, worse, triggers
    # real `git` subprocess calls - to run on every build/self-heal/plan
    # ledger write, several of which can happen per build. `repository_path`
    # alone already satisfies "repository identity or safe repository path
    # reference"; a full content fingerprint is left to callers (like
    # brains.providers/repair_content_generator) that already compute one
    # for their own evidence needs.
    return dict(
        repository_path=repo_path,
        operation_name=operation_name,
        target_path=target_path,
        provider=provider,
        model=model,
    )


def record_denied(decision: RoutingDecision, *, repo_path: str, model: str = "",
                   target_path: str = "") -> str:
    """Record a policy denial. No engine is ever constructed or invoked for
    a denied call - this is the only ledger write it produces."""
    entry = usage_ledger.LedgerEntry(
        **_base_entry(repo_path=repo_path, operation_name=decision.operation_name,
                      provider=decision.provider, model=model or default_model(decision.provider),
                      target_path=target_path),
        routing_reason=decision.denial_reason,
        explicit_user_selection=decision.explicit_user_selection,
        prompt_bytes=0,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        provider_reported_usage_available=False,
        result_status="blocked",
        validation_status="not_attempted",
        error_type="ProviderPolicyDenied",
        error_summary=decision.denial_reason,
        elapsed_time_seconds=0.0,
        estimated_paid_baseline_tokens=0,
        estimated_paid_tokens_used=0,
        estimated_paid_tokens_avoided=0,
        estimated_savings_percentage=None,
        uncertainty_warnings=[],
    )
    return usage_ledger.record(entry)


def record_deterministic(operation_name: str, *, repo_path: str, target_path: str = "",
                          reason: str = "deterministic execution; no model call required") -> str:
    """Record that an operation completed with no provider invoked at all.
    Never records a fabricated token count or savings figure - baseline and
    paid-used both stay zero, so `estimated_savings_percentage` is honestly
    left unset by `usage_ledger.calculate_savings`."""
    avoided, pct = usage_ledger.calculate_savings(0, 0)
    entry = usage_ledger.LedgerEntry(
        **_base_entry(repo_path=repo_path, operation_name=operation_name,
                      provider="deterministic", model="", target_path=target_path),
        routing_reason=reason,
        explicit_user_selection=False,
        prompt_bytes=0,
        estimated_input_tokens=0,
        estimated_output_tokens=0,
        provider_reported_usage_available=False,
        result_status="success",
        validation_status="not_applicable",
        elapsed_time_seconds=0.0,
        estimated_paid_baseline_tokens=0,
        estimated_paid_tokens_used=0,
        estimated_paid_tokens_avoided=avoided,
        estimated_savings_percentage=pct,
        uncertainty_warnings=[],
    )
    return usage_ledger.record(entry)


def record_operation(operation_name: str, provider: str, prompt: str, output_text: str, *,
                      repo_path: str, model: str, target_path: str = "",
                      explicit_user_selection: bool = False, status: str = "success",
                      validation: str = "validated", error_type: str = "", error_summary: str = "",
                      elapsed_time_seconds: float = 0.0, actual_usage: dict | None = None) -> str:
    """Record ledger evidence for a generation call that a caller already
    made through a transport feature `invoke()` does not expose (Ollama's
    `json_mode` structured-output constraint, used by the legacy
    `llm.generate_json` call sites that predate the engine abstraction and
    whose tests mock `llm.generate_json`/`llm.generate` directly).

    This performs no generation and no cleanup of its own - it is pure
    bookkeeping for a call site that must keep calling `core.llm` directly
    to stay byte-for-byte compatible with its existing tests, while still
    getting full usage-ledger coverage."""
    prompt_bytes = len(prompt.encode("utf-8"))
    estimated_input = context_budget.estimate_tokens_from_bytes(prompt_bytes)
    estimated_output = context_budget.estimate_tokens_from_bytes(len(output_text.encode("utf-8")))
    actual_input = (actual_usage or {}).get("input_tokens")
    actual_output = (actual_usage or {}).get("output_tokens")
    usage_available = actual_input is not None or actual_output is not None
    baseline = estimated_input
    paid_used = 0 if provider == "local" else (actual_input if actual_input is not None else estimated_input)
    avoided, pct = usage_ledger.calculate_savings(baseline, paid_used)
    entry = usage_ledger.LedgerEntry(
        **_base_entry(repo_path=repo_path, operation_name=operation_name, provider=provider,
                      model=model, target_path=target_path),
        routing_reason=(
            "explicit CLI/user provider selection" if explicit_user_selection
            else "deterministic default routing to the local provider"
        ),
        explicit_user_selection=explicit_user_selection,
        prompt_bytes=prompt_bytes,
        estimated_input_tokens=estimated_input,
        actual_input_tokens=actual_input,
        estimated_output_tokens=estimated_output,
        actual_output_tokens=actual_output,
        provider_reported_usage_available=usage_available,
        result_status=status,
        validation_status=validation,
        error_type=error_type,
        error_summary=error_summary,
        elapsed_time_seconds=elapsed_time_seconds,
        estimated_paid_baseline_tokens=baseline,
        estimated_paid_tokens_used=paid_used,
        estimated_paid_tokens_avoided=avoided,
        estimated_savings_percentage=pct,
        uncertainty_warnings=[] if usage_available else [
            "actual provider token usage unavailable; token values are estimates"
        ],
    )
    return usage_ledger.record(entry)


def request_and_verify_unload(model: str, *, attempts: int = 3,
                               delay_seconds: float = 0.5) -> tuple[bool, bool]:
    """Shared local-model cleanup helper. Only meaningful after a genuine
    local-engine invocation; callers must not call this on a denied or
    never-attempted operation."""
    ok, _msg = llm.unload_model(model)
    if not ok:
        return False, False
    for idx in range(max(1, attempts)):
        loaded, _verify_msg = llm.model_loaded(model)
        if loaded is False:
            return True, True
        if loaded is None:
            return True, False
        if idx < attempts - 1:
            time.sleep(delay_seconds)
    return True, False


def invoke(operation_name: str, provider: str, prompt: str, system: str = "", *,
           repo_path: str, model: str | None = None, target_path: str = "",
           explicit_user_selection: bool = False, engine=None) -> str:
    """Route a real generation call through the authoritative policy.

    Denies prohibited/unauthorized providers before any engine is
    constructed (raising `ProviderPolicyError`, recorded as a "blocked"
    ledger entry). Otherwise constructs (or reuses) the permitted engine,
    invokes it at most once, records ledger evidence for every outcome, and
    requests local-model cleanup only when generation genuinely began.
    Re-raises the engine's own `EngineError` on a real provider/transport
    failure so callers can degrade exactly as before.
    """
    decision = decide(operation_name, provider, explicit_user_selection=explicit_user_selection)
    resolved_model = model or default_model(decision.provider)

    if not decision.permitted:
        record_denied(decision, repo_path=repo_path, model=resolved_model, target_path=target_path)
        raise ProviderPolicyError(decision.denial_reason)

    start = time.monotonic()
    generation_attempted = False
    status = "failed"
    validation = "not_attempted"
    error_type = ""
    error_summary = ""
    raw_text = ""
    cleanup_requested = False
    cleanup_verified = False
    actual_input = None
    actual_output = None
    usage_available = False

    def _record():
        prompt_bytes = len(prompt.encode("utf-8")) + len(system.encode("utf-8"))
        estimated_input = context_budget.estimate_tokens_from_bytes(prompt_bytes)
        estimated_output = context_budget.estimate_tokens_from_bytes(len(raw_text.encode("utf-8")))
        baseline = estimated_input
        paid_used = 0 if decision.provider == "local" else (actual_input if actual_input is not None else estimated_input)
        avoided, pct = usage_ledger.calculate_savings(baseline, paid_used)
        warnings = [] if usage_available else [
            "actual provider token usage unavailable; token values are estimates"
        ]
        usage_ledger.record(usage_ledger.LedgerEntry(
            **_base_entry(repo_path=repo_path, operation_name=operation_name,
                          provider=decision.provider, model=resolved_model, target_path=target_path),
            routing_reason=decision.routing_reason,
            explicit_user_selection=explicit_user_selection,
            prompt_bytes=prompt_bytes,
            estimated_input_tokens=estimated_input,
            actual_input_tokens=actual_input,
            estimated_output_tokens=estimated_output,
            actual_output_tokens=actual_output,
            provider_reported_usage_available=usage_available,
            result_status=status,
            validation_status=validation,
            error_type=error_type,
            error_summary=error_summary,
            elapsed_time_seconds=time.monotonic() - start,
            local_model_cleanup_requested=cleanup_requested,
            local_model_cleanup_verified=cleanup_verified,
            estimated_paid_baseline_tokens=baseline,
            estimated_paid_tokens_used=paid_used,
            estimated_paid_tokens_avoided=avoided,
            estimated_savings_percentage=pct,
            uncertainty_warnings=warnings,
        ))

    try:
        eng = engine or engine_registry.create(decision.provider, model=resolved_model)
        # Duck-typed like generate_with_usage() below: BaseEngine provides a
        # default available()==True, but plain caller-supplied engine
        # objects (test doubles, reliability_engine's own builder engines)
        # may not subclass BaseEngine at all. Treat "no available() method"
        # the same way BaseEngine's own default does.
        available_fn = getattr(eng, "available", None)
        if callable(available_fn) and not available_fn():
            error_type = "ProviderUnavailable"
            error_summary = f"Provider '{decision.provider}' is not available."
            raise EngineError(error_summary)
        generation_attempted = True
        # Always call generate() - the one method every BaseEngine
        # implementation, test double, and existing monkeypatch in this
        # codebase already honors. Real usage, when the engine captured any
        # as a side effect of this exact call, is read afterward from
        # last_usage; it is never fetched through a second, separate call
        # that a mocked generate() could silently bypass.
        eng.last_usage = None
        raw_text = eng.generate(prompt, system)
        usage = getattr(eng, "last_usage", None)
        if usage:
            actual_input = usage.get("input_tokens")
            actual_output = usage.get("output_tokens")
            usage_available = actual_input is not None or actual_output is not None
        status = "success"
        validation = "validated"
        return raw_text
    except EngineError as exc:
        if not error_type:
            error_type = exc.__class__.__name__
            error_summary = str(exc)
        status = "failed" if generation_attempted else "blocked"
        validation = "failed" if generation_attempted else "not_attempted"
        raise
    except Exception as exc:  # noqa: BLE001 - must still record evidence before re-raising
        error_type = exc.__class__.__name__
        error_summary = str(exc)
        status = "failed" if generation_attempted else "blocked"
        validation = "failed" if generation_attempted else "not_attempted"
        raise
    finally:
        if generation_attempted and decision.provider == "local":
            cleanup_requested, cleanup_verified = request_and_verify_unload(resolved_model)
        _record()
