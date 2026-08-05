#!/usr/bin/env python3
"""
Provider Coverage Audit  (AutoCorp CLI - brains)  [Phase 2B]
==============================================================

A deterministic, static, model-free audit of AutoCorp's own tracked source.
It never executes a model, never contacts a paid API, never contacts
Ollama - it only reads `.py` files and compares what it finds against a
maintained registry of known model-capable call sites.

Two things can go wrong that this audit is built to catch:

  1. A file already known to hold a model-capable call site (registered as
     "routed" in `KNOWN_CALL_SITES`) stops referencing
     `brains.provider_policy` - i.e. someone added a raw, ungoverned
     `engine.generate(...)` call back into a file this repository already
     promised was policy-covered.
  2. A brand new file under `brains/`, `core/`, or `reliability_engine/`
     starts calling a model but was never registered at all.

Both are reported as "uncovered" defects, not silently passed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Substrings that indicate a line invokes a generation-capable call. Kept
# intentionally simple (substring match, not full AST) so the audit stays
# fast, dependency-free, and trivially auditable itself.
_GENERATE_CALL_PATTERNS = (
    ".generate(",
    "llm.generate(",
    "llm.generate_json(",
    "llm.generate_with_usage(",
    "llm._generate_raw(",
    "provider_policy.invoke(",
    "provider_policy.record_operation(",
)

# repo-relative file -> status.
#   "routed"            - expected to invoke real generation through
#                          brains.provider_policy (checked below).
#   "excluded"           - a known model-capable file deliberately out of
#                          Phase 2B scope, with EXCLUSION_REASON recorded.
#   "not_model_capable"  - contains generate-like calls (per the substring
#                          patterns above) but is transport-only: it has no
#                          independent routing/authorization decision of its
#                          own (the engines themselves, invoked only via
#                          provider_policy/engine_registry).
KNOWN_CALL_SITES: dict[str, str] = {
    "brains/builder.py": "routed",
    "brains/tester.py": "routed",
    "brains/planner.py": "routed",
    "brains/providers.py": "routed",
    "brains/repair_content_generator.py": "routed",
    "core/orchestrator.py": "routed",
    "brains/provider_policy.py": "not_model_capable",
    "brains/provider_coverage_audit.py": "not_model_capable",
    "brains/base_engine.py": "not_model_capable",
    "brains/local_engine.py": "not_model_capable",
    "brains/deepseek_engine.py": "not_model_capable",
    "brains/claude_engine.py": "not_model_capable",
    "brains/gated_repair_fixer.py": "not_model_capable",
    "core/llm.py": "not_model_capable",
    "reliability_engine/orchestrator.py": "excluded",
    "reliability_engine/self_consistency.py": "excluded",
    "reliability_engine/planner_spec.py": "excluded",
    "reliability_engine/test_loop.py": "excluded",
}

EXCLUSION_REASON = (
    "reliability_engine/ is a separate, not-CLI-integrated orchestration "
    "pipeline; whether/how to compose it with the main build/repair "
    "pipeline is a documented, still-open owner decision "
    "(AI_ENGINEERING/ROADMAP.md item 3). Excluded from Phase 2B coverage by "
    "explicit scope decision, not a silent gap - see "
    "AI_ENGINEERING/CURRENT_PHASE.md."
)

_SCAN_DIRS = ("brains", "core", "reliability_engine")


@dataclass
class CallSiteFinding:
    file: str
    status: str  # "covered" | "excluded" | "uncovered" | "not_model_capable"
    reason: str = ""
    lines: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"file": self.file, "status": self.status, "reason": self.reason,
                "lines": list(self.lines)}


@dataclass
class CoverageReport:
    findings: list = field(default_factory=list)
    known_call_sites: int = 0
    covered_call_sites: int = 0
    excluded_call_sites: int = 0
    uncovered_call_sites: list = field(default_factory=list)
    coverage_percentage: float | None = None

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "known_call_sites": self.known_call_sites,
            "covered_call_sites": self.covered_call_sites,
            "excluded_call_sites": self.excluded_call_sites,
            "uncovered_call_sites": list(self.uncovered_call_sites),
            "coverage_percentage": self.coverage_percentage,
        }


def _call_lines(text: str) -> list[int]:
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if any(pattern in line for pattern in _GENERATE_CALL_PATTERNS):
            hits.append(i)
    return hits


def _references_policy(text: str) -> bool:
    return "provider_policy" in text


def _discover_candidate_files(repo_root: str) -> set[str]:
    """Walk the AutoCorp-own source directories for any .py file containing
    a generate-call pattern, regardless of whether it is registered. This is
    what lets the audit catch a genuinely new, unregistered call site."""
    found: set[str] = set()
    for base in _SCAN_DIRS:
        base_dir = os.path.join(repo_root, base)
        if not os.path.isdir(base_dir):
            continue
        for root, dirs, files in os.walk(base_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, repo_root).replace(os.sep, "/")
                try:
                    with open(full, encoding="utf-8") as fh:
                        text = fh.read()
                except OSError:
                    continue
                if _call_lines(text):
                    found.add(rel)
    return found


def run_audit(repo_root: str, *, search_paths: list[str] | None = None) -> CoverageReport:
    """Statically scan known/discovered call sites and cross-reference
    against the registry. Deterministic; reads files only; never executes
    anything.

    `search_paths`, when given, replaces auto-discovery with an explicit
    file list (repo-relative) - used by tests to point the audit at a
    controlled fixture tree instead of AutoCorp's own real source.
    """
    repo_root = os.path.abspath(repo_root)
    report = CoverageReport()

    discovered = _discover_candidate_files(repo_root) if search_paths is None else set(search_paths)
    all_paths = sorted(set(KNOWN_CALL_SITES) | discovered)

    for rel in all_paths:
        full = os.path.join(repo_root, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue

        call_lines = _call_lines(text)
        registered_status = KNOWN_CALL_SITES.get(rel)

        if registered_status is None:
            # Not in the registry at all. Only a defect if it actually
            # contains a model-capable call - an unrelated new file is not
            # flagged just for existing.
            if call_lines:
                report.uncovered_call_sites.append(rel)
                report.findings.append(CallSiteFinding(
                    rel, "uncovered",
                    "model-capable call found but this file is not registered "
                    "in provider_coverage_audit.KNOWN_CALL_SITES",
                    call_lines,
                ))
            continue

        report.known_call_sites += 1

        if registered_status == "excluded":
            report.excluded_call_sites += 1
            report.findings.append(CallSiteFinding(rel, "excluded", EXCLUSION_REASON, call_lines))
            continue

        if registered_status == "not_model_capable":
            report.findings.append(CallSiteFinding(
                rel, "not_model_capable",
                "transport-only engine/client code with no independent "
                "routing or authorization decision of its own", call_lines,
            ))
            continue

        # registered_status == "routed"
        if call_lines and _references_policy(text):
            report.covered_call_sites += 1
            report.findings.append(CallSiteFinding(rel, "covered", "", call_lines))
        else:
            report.uncovered_call_sites.append(rel)
            reason = (
                "registered as routed but no generation call found in this file"
                if not call_lines else
                "registered as routed but this file does not reference "
                "brains.provider_policy"
            )
            report.findings.append(CallSiteFinding(rel, "uncovered", reason, call_lines))

    denom = report.known_call_sites - report.excluded_call_sites - sum(
        1 for f in report.findings if f.status == "not_model_capable"
    )
    if denom > 0:
        report.coverage_percentage = round(100.0 * report.covered_call_sites / denom, 2)
    return report
