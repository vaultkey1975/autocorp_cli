#!/usr/bin/env python3
"""
Repair Content Generator  (AutoCorp CLI - brains)  [Phase 8Y]
=============================================================

The pluggable content-synthesis seam behind the 8X `generator` slot on
GatedRepairFixer. A RepairContentGenerator holds a PROVIDER and DELEGATES the
actual content synthesis to it, returning the provider's output unchanged. This
keeps the gated fixer, the orchestrator, and the self-healing loop ignorant of
*how* content is produced - a future model-backed provider (DeepSeek / Claude /
Tester) can be injected without touching any of them.

PURE DELEGATION: this module performs no model call, no network, no subprocess,
no shell, no retry, and adds no extra abstraction. `RepairContentProvider` is the
interface concrete providers implement; `RepairContentGenerator` simply forwards
`generate(path, description)` to the injected provider.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod

from brains import context_budget, engine_registry, repo_policy, usage_ledger
from brains.base_engine import EngineError
from core import llm


class RepairContentProvider(ABC):
    """Interface for a concrete repair-content provider.

    A provider turns a target `path` plus a failure `description` into the new
    file content (a string). Concrete implementations are a separate, later phase;
    the base raises NotImplementedError."""

    @abstractmethod
    def generate(self, path: str, description: str) -> str:
        raise NotImplementedError


class RepairContentGenerator:
    """A generator that delegates content synthesis to an injected provider.

    Plugs into the 8X `generator` seam (it exposes `generate(path, description)`)
    and forwards every call to the provider, returning its output unchanged."""

    def __init__(self, provider):
        self.provider = provider

    def generate(self, path: str, description: str) -> str:
        return self.provider.generate(path, description)


class LocalRepairContentProvider(RepairContentProvider):
    """Real local Ollama-backed repair provider.

    The provider is read-only: it builds bounded context, invokes only the
    selected local engine, validates complete replacement content, records
    usage evidence, and requests immediate model unload in all outcomes.
    """

    _SYSTEM_PROMPT = (
        "You are AutoCorp's local repair content provider. Return only complete "
        "replacement file content for the requested target file. Do not return "
        "markdown fences, explanations, shell commands, or patches."
    )

    def __init__(self, repo_path: str, model: str | None = None, engine=None):
        self.repo_path = os.path.abspath(repo_path)
        self.model = model or llm.MODEL
        self.engine = engine
        self.last_evidence: dict = {}

    def _validate_output(self, text: str) -> str:
        if not text or not text.strip():
            raise ValueError("local provider returned empty content")
        stripped = text.strip()
        if stripped.startswith("```"):
            raise ValueError("local provider returned markdown fences")
        lines = [line for line in stripped.splitlines() if line.strip()]
        if lines and all(line.lstrip().startswith(("#", "//", "/*", "*")) for line in lines):
            raise ValueError("local provider returned explanatory comments instead of replacement content")
        lowered = stripped.lower()
        if any(token in lowered for token in ("rm -rf", "sudo ", "git commit", "git push", "subprocess.", "os.system(")):
            raise ValueError("local provider returned shell/git command content")
        return text.rstrip() + "\n"

    def generate(self, path, description):
        start = time.monotonic()
        cleanup_requested = False
        cleanup_verified = False
        manifest = None
        prompt = ""
        status = "failed"
        validation = "not_validated"
        error_type = ""
        error_summary = ""
        generation_started = False
        try:
            rel = repo_policy.normalize_rel_path(self.repo_path, path)
            if repo_policy.is_excluded(self.repo_path, rel):
                raise ValueError(f"target path is excluded by repository policy: {rel}")
            prompt, manifest = context_budget.build_repair_context(
                self.repo_path,
                rel,
                description,
                provider="local",
                model=self.model,
                operation="repair-content",
                system_prompt=self._SYSTEM_PROMPT,
            )
            engine = self.engine or engine_registry.create("local", model=self.model)
            if not engine.available():
                status = "blocked"
                raise EngineError("local engine is not available")
            generation_started = True
            raw = engine.generate(prompt, self._SYSTEM_PROMPT)
            output = self._validate_output(raw)
            status = "success"
            validation = "validated"
            return output
        except Exception as exc:
            error_type = exc.__class__.__name__
            error_summary = str(exc)
            if isinstance(exc, (ValueError, EngineError)):
                status = "blocked" if isinstance(exc, ValueError) else status
            raise
        finally:
            if generation_started:
                cleanup_requested, cleanup_verified = _request_and_verify_unload(self.model)
            manifest_dict = manifest.to_dict() if manifest else {}
            estimated_output = context_budget.estimate_tokens_from_bytes(len((locals().get("raw", "") or "").encode("utf-8")))
            baseline = max(manifest.estimated_input_tokens if manifest else 0, 0)
            avoided, pct = usage_ledger.calculate_savings(baseline, 0)
            entry = usage_ledger.LedgerEntry(
                repository_path=self.repo_path,
                repository_stable_identifier=context_budget.repository_fingerprint(self.repo_path),
                commit_sha=manifest.git_commit if manifest else "",
                working_tree_fingerprint=manifest.working_tree_fingerprint if manifest else "",
                operation_name="repair-content",
                target_path=manifest.target_path if manifest else str(path),
                provider="local",
                model=self.model,
                routing_reason="explicit local repair-content provider selection",
                explicit_user_selection=True,
                context_fingerprint=manifest.context_fingerprint if manifest else "",
                selected_context_manifest=manifest_dict,
                prompt_bytes=manifest.total_prompt_bytes if manifest else len(prompt.encode("utf-8")),
                estimated_input_tokens=manifest.estimated_input_tokens if manifest else context_budget.estimate_tokens_from_bytes(len(prompt.encode("utf-8"))),
                estimated_output_tokens=estimated_output,
                provider_reported_usage_available=False,
                cache_status="miss",
                result_status=status,
                validation_status=validation,
                error_type=error_type,
                error_summary=error_summary,
                elapsed_time_seconds=time.monotonic() - start,
                local_model_cleanup_requested=cleanup_requested,
                local_model_cleanup_verified=cleanup_verified,
                estimated_paid_baseline_tokens=baseline,
                estimated_paid_tokens_used=0,
                estimated_paid_tokens_avoided=avoided,
                estimated_savings_percentage=pct,
                uncertainty_warnings=["actual provider token usage unavailable; token values are estimates"],
            )
            usage_ledger.record(entry)
            self.last_evidence = {
                "operation_id": entry.operation_id,
                "context_fingerprint": entry.context_fingerprint,
                "cleanup_requested": cleanup_requested,
                "cleanup_verified": cleanup_verified,
                "status": status,
            }


def _request_and_verify_unload(model: str, *, attempts: int = 3, delay_seconds: float = 0.5) -> tuple[bool, bool]:
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


class TesterBackedRepairContentProvider(RepairContentProvider):
    """Produces REAL fix content by delegating to the model-backed TesterBrain.

    generate(path, description) calls
    `tester.suggest_fix(workspace, path, description, plan)` and returns the
    result's "new_content". Any failure - an empty/keyless result OR a Tester
    exception - yields "" so the 8X seam falls back to the description and nothing
    dangerous is written. The provider never lets a Tester exception escape."""

    # Not a pytest test suite: pytest's default collector matches any class
    # named "Test*", which this production class incidentally does.
    __test__ = False

    def __init__(self, tester, workspace, plan=None):
        self.tester = tester
        self.workspace = workspace
        self.plan = plan

    def generate(self, path, description):
        try:
            result = self.tester.suggest_fix(
                self.workspace, path, description, self.plan
            )
        except Exception:  # noqa: BLE001 - never let a Tester failure escape
            return ""
        if not result:
            return ""
        return result.get("new_content") or ""


class RepairContentProviderFactory:
    """Maps a provider NAME to a concrete RepairContentProvider.

    Production never returns fake repair content. The legacy name "mock" is
    rejected here; deterministic fakes belong in tests only."""

    @classmethod
    def create(cls, provider_name, **kwargs):
        if provider_name == "mock":
            raise ValueError(
                "The 'mock' repair content provider is test-only and cannot be selected in production."
            )

        if provider_name == "local":
            repo_path = kwargs.get("repo_path") or kwargs.get("workspace")
            if not repo_path:
                raise ValueError("repo_path is required for the local repair content provider.")
            return LocalRepairContentProvider(repo_path=repo_path, model=kwargs.get("model"), engine=kwargs.get("engine"))

        if provider_name == "tester":
            return TesterBackedRepairContentProvider(
                kwargs["tester"], kwargs["workspace"], kwargs.get("plan"),
            )

        raise ValueError(
            f"Unknown repair content provider: {provider_name}"
        )
