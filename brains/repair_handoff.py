#!/usr/bin/env python3
"""
Repair Handoff Generator  (AutoCorp CLI - brains)  [Phase 2B extension]
=========================================================================

Deterministic, model-free transformation of VERIFIED AutoCorp evidence
(Fast Pytest Engine `EngineReport` JSON - the same JSON `test-focused`/
`test-full --json` already produce) into paste-ready repair prompts for
Codex and/or Claude, with provenance, SHA-256 hashing, secret redaction,
and optional VS Code opening.

Design principles:
  * Never invents a defect. `classify_evidence` only returns VERIFIED_BROKEN
    when the evidence contains a concrete failing test with a node_id; a
    generic "failed > 0" count with no detail is INCONCLUSIVE, not broken.
  * No model, no network, no Ollama, no paid API. Generation is pure string
    templating over already-verified evidence plus real, local `git`
    inspection of the target repository (deterministic, not a model call).
  * Reuses existing architecture rather than inventing a parallel one:
    `brains.repair_proposal._redact_inline_secrets` (the same hardened,
    Phase-1G-audited redaction used by the AI Repair Proposal Engine) and
    `brains.provider_policy.record_deterministic` (the Phase 2B usage
    ledger) record every handoff generation as a real, auditable,
    no-model-call operation.
  * Writes are atomic (temp file + os.replace) and never silently overwrite
    an existing handoff.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from brains import provider_policy
from brains.repair_proposal import _redact_inline_secrets

VERIFIED_BROKEN = "VERIFIED_BROKEN"
INCONCLUSIVE = "INCONCLUSIVE"
PASSED = "PASSED"

AGENTS = ("codex", "claude")

PROJECT_RULES = (
    "no placeholders",
    "no mock production implementation",
    "no stubs",
    "no fake success",
    "no simulated output",
    "no fabricated results",
    "no silent fallback",
    "no weakening tests",
    "no deleting legitimate coverage",
    "no unrelated refactoring",
    "use real error handling",
    "add regression tests for the verified defect",
    "run AutoCorp test-plan and test-focused during development",
    "treat zero selected or zero collected tests as NOT VERIFIED",
    "run the complete strict repository suite exactly once as the final approval gate",
    "do not push unless explicitly authorized",
)


class RepairHandoffNotVerified(RuntimeError):
    """Raised when the supplied evidence is not VERIFIED_BROKEN - AutoCorp
    refuses to fabricate a repair task from passing or inconclusive
    evidence."""


# --------------------------------------------------------------------------- #
# Evidence classification
# --------------------------------------------------------------------------- #
@dataclass
class FailingTest:
    node_id: str
    path: str
    message: str = ""


@dataclass
class Verdict:
    status: str  # VERIFIED_BROKEN | INCONCLUSIVE | PASSED
    reason: str
    failing_tests: list[FailingTest] = field(default_factory=list)
    exit_code: int | None = None
    command: str = ""
    collected: int = 0
    passed: int = 0
    failed: int = 0


def classify_evidence(evidence: dict[str, Any]) -> Verdict:
    """Classify Fast Pytest Engine `EngineReport` JSON into
    VERIFIED_BROKEN / INCONCLUSIVE / PASSED. Never invents a root cause or a
    failure that isn't concretely present in the evidence; warnings alone
    (uncertainty_warnings, performance_warnings) never become a confirmed
    defect."""
    if not isinstance(evidence, dict):
        return Verdict(INCONCLUSIVE, "evidence is not a valid AutoCorp test-report JSON object")

    collected = int(evidence.get("collected") or 0)
    passed = int(evidence.get("passed") or 0)
    failed = int(evidence.get("failed") or 0)
    exit_code = evidence.get("exit_code")
    commands = evidence.get("commands") or []
    command = " ".join(commands[-1]) if commands and isinstance(commands[-1], list) else ""

    common = dict(exit_code=exit_code, command=command, collected=collected, passed=passed, failed=failed)

    if collected == 0:
        return Verdict(INCONCLUSIVE, "zero tests were collected; no verification evidence exists", **common)

    results = evidence.get("results") or []
    failing = [
        FailingTest(
            node_id=r.get("test", ""),
            path=(r.get("test") or "").split("::")[0],
            message=r.get("message", "") or r.get("failure_type", "") or "",
        )
        for r in results
        if isinstance(r, dict) and r.get("outcome") == "failed" and r.get("test")
    ]

    if evidence.get("blocked"):
        return Verdict(INCONCLUSIVE, f"run was blocked: {evidence.get('blocked_reason', '(no reason given)')}", **common)

    if failed > 0 and failing:
        return Verdict(VERIFIED_BROKEN, f"{failed} test(s) failed with concrete evidence", failing_tests=failing, **common)

    if failed > 0 and not failing:
        # The counters disagree with the detail available - do not fabricate
        # specifics for a failure this evidence doesn't actually describe.
        return Verdict(INCONCLUSIVE,
                       "the report claims failures but no concrete failing-test detail is present", **common)

    if exit_code not in (0, None):
        return Verdict(INCONCLUSIVE, f"exit code {exit_code} with no captured failing tests", **common)

    return Verdict(PASSED, "all collected tests passed", **common)


# --------------------------------------------------------------------------- #
# Repository context (real, local, deterministic git inspection - no model)
# --------------------------------------------------------------------------- #
@dataclass
class RepositoryContext:
    repo_path: str
    name: str
    branch: str
    commit: str
    working_tree_status: str


def _git(repo_path: str, args: list[str]) -> str:
    try:
        proc = subprocess.run(["git", "-C", repo_path] + args, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def gather_repository_context(repo_path: str) -> RepositoryContext:
    repo_path = os.path.abspath(repo_path)
    name = os.path.basename(repo_path.rstrip(os.sep)) or repo_path
    branch = _git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
    commit = _git(repo_path, ["rev-parse", "HEAD"]) or "unknown"
    status_output = _git(repo_path, ["status", "--porcelain=v1", "--untracked-files=no"])
    working_tree_status = "clean" if status_output == "" else "dirty"
    return RepositoryContext(repo_path, name, branch, commit, working_tree_status)


# --------------------------------------------------------------------------- #
# Repair scope (unknown permissions default to prohibited)
# --------------------------------------------------------------------------- #
@dataclass
class RepairScope:
    implicated_files: list[str] = field(default_factory=list)
    inspect_files: list[str] = field(default_factory=list)
    prohibited_features: list[str] = field(default_factory=list)
    allow_database_migrations: bool = False
    allow_dependency_changes: bool = False
    allow_network_or_model_calls: bool = False
    require_gpu_models_unloaded: bool = True
    allow_production_data_access: bool = False


def default_scope(verdict: Verdict) -> RepairScope:
    return RepairScope(implicated_files=sorted({ft.path for ft in verdict.failing_tests if ft.path}))


# --------------------------------------------------------------------------- #
# Filename / identifier helpers
# --------------------------------------------------------------------------- #
def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text or "").strip("_").lower()
    return slug[:max_len] or "defect"


def defect_identifier(verdict: Verdict) -> str:
    base = verdict.failing_tests[0].node_id if verdict.failing_tests else verdict.reason
    return _slugify(base)


def handoff_filename(ctx: RepositoryContext, verdict: Verdict, agent: str, *, timestamp: str) -> str:
    return f"{timestamp}_{_slugify(ctx.name, 30)}_{defect_identifier(verdict)}_{agent}.md"


# --------------------------------------------------------------------------- #
# Redaction (reuses the existing, hardened Phase 1G redaction - no second
# implementation of secret detection)
# --------------------------------------------------------------------------- #
def redact(text: str) -> tuple[str, int]:
    return _redact_inline_secrets(text)


# --------------------------------------------------------------------------- #
# Prompt rendering (deterministic templating - no model call)
# --------------------------------------------------------------------------- #
def _repo_specific_rules_note(repo_path: str) -> str:
    ai_eng_dir = os.path.join(repo_path, "AI_ENGINEERING")
    if not os.path.isdir(ai_eng_dir):
        return "No repository-specific AI_ENGINEERING/ documentation was found; follow the rules above only."
    found = sorted(f for f in os.listdir(ai_eng_dir) if f.endswith(".md"))
    if not found:
        return "No repository-specific AI_ENGINEERING/ documentation was found; follow the rules above only."
    listing = ", ".join(found)
    return (f"Repository-specific engineering documentation exists and MUST be consulted before editing: "
            f"{listing} (under {ai_eng_dir})")


def _render_evidence_section(ctx: RepositoryContext, verdict: Verdict, run_id: str,
                              detected_by: str, log_path: str, timestamp: str) -> str:
    lines = [
        "## Verified Evidence",
        "",
        f"- Status: {verdict.status}",
        f"- Repository: {ctx.name}",
        f"- Absolute path: {ctx.repo_path}",
        f"- Branch: {ctx.branch}",
        f"- Commit: {ctx.commit}",
        f"- Working tree: {ctx.working_tree_status}",
        f"- Detection timestamp (UTC): {timestamp}",
        f"- AutoCorp run identifier: {run_id}",
        f"- Detected by: {detected_by}",
        f"- Exact command that failed: {verdict.command or '(not captured in evidence)'}",
        f"- Exit code: {verdict.exit_code if verdict.exit_code is not None else 'unknown'}",
        f"- Collected / passed / failed: {verdict.collected} / {verdict.passed} / {verdict.failed}",
        f"- Full original log location: {log_path or '(not provided)'}",
        "",
        "### Failing tests",
    ]
    if verdict.failing_tests:
        for ft in verdict.failing_tests:
            redacted_msg, _n = redact(ft.message or "(no error detail captured)")
            lines.append(f"- `{ft.node_id}` (file: `{ft.path}`) — {redacted_msg[:1500]}")
    else:
        lines.append("- (none captured)")
    return "\n".join(lines)


def _render_scope_section(scope: RepairScope) -> str:
    lines = [
        "## Repair Scope",
        "",
        "### Files known to be involved",
    ]
    lines += [f"- {p}" for p in scope.implicated_files] or ["- (none identified by evidence)"]
    lines += ["", "### Files that may need inspection"]
    lines += [f"- {p}" for p in scope.inspect_files] or ["- (none specified)"]
    lines += ["", "### Explicitly out of scope"]
    lines += [f"- {p}" for p in scope.prohibited_features] or [
        "- Any file not listed above as involved or needing inspection."
    ]
    lines += [
        "",
        "### Permissions (unknown/unstated permissions are PROHIBITED)",
        f"- Database migrations: {'ALLOWED' if scope.allow_database_migrations else 'PROHIBITED'}",
        f"- Dependency changes: {'ALLOWED' if scope.allow_dependency_changes else 'PROHIBITED'}",
        f"- Network or model/API calls: {'ALLOWED' if scope.allow_network_or_model_calls else 'PROHIBITED'}",
        f"- GPU models must remain unloaded: {'YES' if scope.require_gpu_models_unloaded else 'NO'}",
        f"- Production data access: {'ALLOWED' if scope.allow_production_data_access else 'PROHIBITED'}",
    ]
    return "\n".join(lines)


def _render_rules_section(repo_path: str) -> str:
    lines = ["## Required Project Rules (apply to every change)", ""]
    lines += [f"- {rule}" for rule in PROJECT_RULES]
    lines += ["", "## Repository-Specific Rules", "", f"- {_repo_specific_rules_note(repo_path)}"]
    return "\n".join(lines)


def _render_reproduction_section(ctx: RepositoryContext, verdict: Verdict) -> str:
    return "\n".join([
        "## Reproduction Steps",
        "",
        f"1. `cd {ctx.repo_path}`",
        f"2. `{verdict.command or '(exact command not captured in evidence)'}`",
        "",
        "## Expected vs Actual Behavior",
        "",
        "- Expected: the command above exits 0 with every listed test passing.",
        f"- Actual: exit code {verdict.exit_code}, with the failing test(s) listed under Verified Evidence.",
    ])


def render_codex_prompt(ctx: RepositoryContext, verdict: Verdict, scope: RepairScope,
                        run_id: str, detected_by: str, log_path: str, timestamp: str) -> str:
    parts = [
        f"# AutoCorp Repair Handoff — Codex — {ctx.name}",
        "",
        _render_evidence_section(ctx, verdict, run_id, detected_by, log_path, timestamp),
        "",
        _render_reproduction_section(ctx, verdict),
        "",
        "## Verified Facts",
        f"- {verdict.reason}",
        "",
        "## Hypotheses (NOT verified facts - Codex must determine root cause from evidence)",
        "- AutoCorp does not assert a root cause. Treat any suspected cause you form while "
        "investigating as a hypothesis until confirmed by the evidence.",
        "",
        _render_scope_section(scope),
        "",
        _render_rules_section(ctx.repo_path),
        "",
        "## Codex Workflow",
        "",
        "1. Inspect the listed evidence first.",
        "2. Reproduce the verified failure using the exact command above.",
        "3. Identify the smallest production-safe correction.",
        "4. Edit only files listed in scope as involved.",
        "5. Avoid unrelated refactoring.",
        "6. Add or update real regression tests for the verified defect.",
        "7. Run AutoCorp test-plan and test-focused during development; zero selected or "
        "zero collected tests means NOT VERIFIED.",
        "8. Run the complete strict repository suite exactly once as the final approval gate.",
        "9. Report exact files changed, tests run, exit codes, and remaining uncertainty.",
        "10. Stop before pushing unless explicitly authorized.",
        "",
        "## Completion and Stopping Conditions",
        "",
        "- Complete when the verified failure above no longer reproduces and the complete "
        "strict suite passes.",
        "- Stop and report plainly if the root cause cannot be confirmed from real evidence.",
        "- Stop before pushing unless explicitly authorized.",
    ]
    return "\n".join(parts) + "\n"


def render_claude_prompt(ctx: RepositoryContext, verdict: Verdict, scope: RepairScope,
                         run_id: str, detected_by: str, log_path: str, timestamp: str) -> str:
    parts = [
        f"# AutoCorp Repair Handoff — Claude — {ctx.name}",
        "",
        _render_evidence_section(ctx, verdict, run_id, detected_by, log_path, timestamp),
        "",
        _render_reproduction_section(ctx, verdict),
        "",
        "## Verified Facts",
        f"- {verdict.reason}",
        "",
        "## Hypotheses (NOT verified facts - label any suspected cause explicitly as a hypothesis)",
        "- AutoCorp does not assert a root cause. Distinguish confirmed facts above from any "
        "hypothesis you form during diagnosis, and say which is which in your report.",
        "",
        _render_scope_section(scope),
        "",
        _render_rules_section(ctx.repo_path),
        "",
        "## Claude Workflow",
        "",
        "1. Audit the verified evidence above.",
        "2. Distinguish facts from hypotheses explicitly.",
        "3. Inspect the existing architecture and implementation before editing.",
        "4. Determine the root cause supported by evidence - do not guess.",
        "5. Reject fake, placeholder, or test-only production fixes.",
        "6. Implement only the verified repair.",
        "7. Add regression coverage for the verified defect.",
        "8. Review the final diff for unintended behavior.",
        "9. Run focused verification (AutoCorp test-plan / test-focused); zero selected or "
        "zero collected tests means NOT VERIFIED.",
        "10. Run the complete strict repository suite exactly once as the final approval gate.",
        "11. Report exact findings, changes, tests, risks, and commit status.",
        "12. Stop before pushing unless explicitly authorized.",
        "",
        "## Completion and Stopping Conditions",
        "",
        "- Complete when the verified failure above no longer reproduces, the diff has been "
        "reviewed for unintended behavior, and the complete strict suite passes.",
        "- Stop and report plainly if the root cause cannot be confirmed from real evidence.",
        "- Stop before pushing unless explicitly authorized.",
    ]
    return "\n".join(parts) + "\n"


_RENDERERS = {"codex": render_codex_prompt, "claude": render_claude_prompt}


# --------------------------------------------------------------------------- #
# Provenance + atomic write
# --------------------------------------------------------------------------- #
@dataclass
class HandoffRecord:
    handoff_id: str
    created_at: float
    repo_path: str
    branch: str
    commit: str
    agent: str
    run_id: str
    source_findings: str
    source_log_path: str
    prompt_path: str
    prompt_sha256: str
    generation_method: str
    vscode_requested: bool = False
    vscode_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handoff_id": self.handoff_id,
            "created_at": self.created_at,
            "repository_path": self.repo_path,
            "branch": self.branch,
            "commit": self.commit,
            "target_agent": self.agent,
            "source_run_identifier": self.run_id,
            "source_findings": self.source_findings,
            "source_log_path": self.source_log_path,
            "generated_prompt_path": self.prompt_path,
            "generated_prompt_sha256": self.prompt_sha256,
            "generation_method": self.generation_method,
            "vscode_open_requested": self.vscode_requested,
            "vscode_open_result": self.vscode_result,
        }


def _atomic_write(path: str, content: str) -> None:
    tmp = f"{path}.tmp-{uuid.uuid4().hex}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _provenance_path(prompt_path: str) -> str:
    return prompt_path + ".provenance.json"


def _write_provenance(prompt_path: str, record: HandoffRecord) -> None:
    _atomic_write(_provenance_path(prompt_path), json.dumps(record.to_dict(), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- #
# Top-level generation
# --------------------------------------------------------------------------- #
def generate_handoff(repo_path: str, evidence: dict[str, Any], *, agent: str,
                     run_id: str | None = None, detected_by: str = "autocorp test-focused",
                     log_path: str = "", scope: RepairScope | None = None,
                     now: float | None = None) -> HandoffRecord:
    """Generate exactly one repair handoff for `agent` from VERIFIED_BROKEN
    evidence. Raises RepairHandoffNotVerified for PASSED/INCONCLUSIVE
    evidence - never fabricates a repair task. Deterministic and model-free:
    no Ollama, no Claude, no Codex, no DeepSeek, no paid API call is made by
    this function."""
    if agent not in AGENTS:
        raise ValueError(f"Unknown agent '{agent}'. Use one of: {', '.join(AGENTS)}")

    ctx = gather_repository_context(repo_path)
    verdict = classify_evidence(evidence)
    if verdict.status != VERIFIED_BROKEN:
        raise RepairHandoffNotVerified(
            f"Refusing to generate a repair handoff: evidence status is {verdict.status} "
            f"({verdict.reason}). A handoff may only be generated from VERIFIED_BROKEN evidence."
        )

    scope = scope or default_scope(verdict)
    run_id = run_id or str(uuid.uuid4())
    ts_struct = time.gmtime(now) if now is not None else time.gmtime()
    timestamp = time.strftime("%Y-%m-%dT%H%M%S", ts_struct)

    renderer = _RENDERERS[agent]
    content = renderer(ctx, verdict, scope, run_id, detected_by, log_path, timestamp)
    redacted_content, _redaction_count = redact(content)

    out_dir = os.path.join(ctx.repo_path, "AI_ENGINEERING", "REPAIR_HANDOFFS")
    os.makedirs(out_dir, exist_ok=True)
    filename = handoff_filename(ctx, verdict, agent, timestamp=timestamp)
    path = os.path.join(out_dir, filename)
    if os.path.exists(path):
        raise FileExistsError(f"Repair handoff already exists and will not be overwritten: {path}")

    _atomic_write(path, redacted_content)
    digest = hashlib.sha256(redacted_content.encode("utf-8")).hexdigest()

    record = HandoffRecord(
        handoff_id=str(uuid.uuid4()), created_at=(now if now is not None else time.time()),
        repo_path=ctx.repo_path, branch=ctx.branch, commit=ctx.commit, agent=agent, run_id=run_id,
        source_findings=verdict.reason, source_log_path=log_path,
        prompt_path=path, prompt_sha256=digest, generation_method="deterministic-template",
    )
    _write_provenance(path, record)

    # Real, truthful usage-ledger evidence: deterministic, no model call.
    provider_policy.record_deterministic(
        "repair-handoff-generation", repo_path=ctx.repo_path, target_path=path,
        reason="deterministic template-based repair handoff generation; no model call",
    )
    return record


def generate_handoffs(repo_path: str, evidence: dict[str, Any], *, agents: list[str],
                      **kwargs: Any) -> list[HandoffRecord]:
    """Generate one handoff per agent in `agents` (e.g. ["codex", "claude"]
    for --agent both). Each call is independent and produces its own
    distinct file; this never merges agents into one combined prompt."""
    return [generate_handoff(repo_path, evidence, agent=a, **kwargs) for a in agents]


# --------------------------------------------------------------------------- #
# VS Code integration
# --------------------------------------------------------------------------- #
def open_in_vscode(path: str, *, code_command: str = "code") -> dict[str, Any]:
    """Open `path` in the current VS Code window via `code --reuse-window`.
    Never simulates keyboard input, never depends on private extension
    internals, never submits the prompt to an agent. Reports success only
    when the real subprocess exits 0; if `code` is not on PATH, the prompt
    file itself was already written by generate_handoff and remains usable
    - this function only ever attempts to open it."""
    if not os.path.isfile(path):
        return {"attempted": False, "success": False, "detail": f"prompt file not found: {path}"}

    binary = shutil.which(code_command)
    if not binary:
        return {
            "attempted": False, "success": False,
            "detail": (
                f"'{code_command}' command not found on PATH. The handoff was created; "
                f"open it manually: {path}"
            ),
        }

    try:
        proc = subprocess.run([binary, "--reuse-window", path], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return {"attempted": True, "success": False, "detail": f"failed to launch '{code_command}': {e}"}

    if proc.returncode == 0:
        return {"attempted": True, "success": True, "detail": f"opened {path} in VS Code"}
    return {
        "attempted": True, "success": False,
        "detail": f"'{code_command}' exited {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:300]}",
    }
