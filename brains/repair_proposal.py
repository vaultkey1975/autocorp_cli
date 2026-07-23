#!/usr/bin/env python3
"""
AI Repair Proposal Engine  (AutoCorp CLI - brains)  [Phase 1G]
================================================================

Generates structured, review-only AI repair proposals for Phase 1C
actions. Never applies changes, never commits, never pushes.

Public API:
    build_repair_proposal(repo_path, action_id, provider, model) -> RepairProposal
    write_repair_proposal(proposal, output_path, overwrite=False) -> str

Design:
    - Collects evidence from scanner, analyzer, and project_planner
    - Excludes secret-bearing files
    - Redacts inline secrets
    - Constructs a controlled prompt
    - Calls the selected AI provider
    - Validates the response against strict contracts
    - Writes atomic JSON output when requested
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field

from brains import (
    analyzer,
    project_planner,
    providers,
    scanner,
)

# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #

_MAX_SOURCE_FILES = 12
_MAX_TEST_FILES = 6
_MAX_PROMPT_BYTES = 200 * 1024
_MAX_FILE_BYTES = 100 * 1024
_MAX_PROPOSED_FILES = 5

# --------------------------------------------------------------------------- #
# Secret file patterns
# --------------------------------------------------------------------------- #

_SECRET_FILE_PATTERNS = [
    re.compile(p) for p in [
        r"(^|/)\.env(\..*)?$",
        r".*\.pem$",
        r".*\.key$",
        r"(^|/|_)id_rsa",
        r"(^|/|_)id_ed25519",
        r"(^|[/_])(credentials|secrets|token|auth|keys)([_.]|\.\w+$|$)",
    ]
]

_INLINE_SECRET_RE = re.compile(
    r'('
    r'api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token'
    r'|auth[_-]?token|private[_-]?key|client[_-]?secret'
    r'|aws[_-]?secret[_-]?access[_-]?key'
    r')\s*[:=]\s*\S+|'
    r'\b(password|secret)\s*[:=]\s*\S+|'
    r'authorization\s*:\s*bearer\s+\S+|'
    r'(postgres|mysql|mongodb|redis)://[^@]*:[^@]*@[^\s]+',
    re.IGNORECASE,
)

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RepairProposalRequest:
    repo_path: str
    action_id: str
    provider: str
    model: str
    output_path: str | None = None


@dataclass(frozen=True)
class RepairProposalFile:
    path: str
    purpose: str = ""
    current_sha256: str = ""
    proposed_change_summary: str = ""
    proposed_patch: str = ""
    confidence: int = 0


@dataclass
class RepairProposal:
    repo_path: str
    action_id: str
    action_title: str = ""
    provider: str = ""
    model: str = ""
    summary: str = ""
    reasoning_summary: str = ""
    files: tuple[RepairProposalFile, ...] = ()
    validation_plan: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    safe_to_apply: bool = False
    confidence: int = 0
    redaction_summary: str = ""
    redactions: int = 0
    provider_error: str = ""


# --------------------------------------------------------------------------- #
# Secret detection
# --------------------------------------------------------------------------- #


def _is_secret_file(file_path: str) -> bool:
    basename = os.path.basename(file_path)
    for pat in _SECRET_FILE_PATTERNS:
        if pat.search(basename) or pat.search(file_path):
            return True
    return False


def _redact_inline_secrets(content: str) -> tuple[str, int]:
    count = len(_INLINE_SECRET_RE.findall(content))

    def _replace(m: re.Match) -> str:
        matched = m.group(0)
        if matched.startswith("authorization") or matched.startswith("Authorization"):
            return "Authorization: Bearer [REDACTED]"
        if "://" in matched and "@" in matched:
            proto_end = matched.index("://") + 3
            at_idx = matched.rindex("@")
            return matched[:proto_end] + "[REDACTED]" + matched[at_idx:]
        for sep in ("=", ":"):
            if sep in matched:
                return matched.split(sep)[0] + sep + " [REDACTED]"
        return "[REDACTED]"

    redacted = _INLINE_SECRET_RE.sub(_replace, content)
    return redacted, count


# --------------------------------------------------------------------------- #
# Evidence collection
# --------------------------------------------------------------------------- #


def _count_secret_files(repo_path: str) -> int:
    """Walk ALL repository entries (not just Python source) and count files
    whose name matches secret-bearing patterns. Never reads file contents."""
    count = 0
    try:
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__",
                          ".venv", ".pytest_cache")]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, repo_path)
                if _is_secret_file(rel):
                    count += 1
    except OSError:
        pass
    return count


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_file(path: str) -> tuple[str, str]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        sha = _sha256_bytes(data)
        return data.decode("utf-8", errors="replace"), sha
    except OSError:
        return "", ""


def _collect_evidence(repo_path: str, action_id: str) -> dict:
    """Collect scanner + analyzer + planner evidence for prompt construction.
    Returns a dict of evidence snippets. Read-only throughout."""
    evidence = {
        "action": None,
        "scan_summary": "",
        "analyzer_summary": "",
        "source_files": [],
        "test_files": [],
        "secret_files_excluded": 0,
        "binary_files_excluded": 0,
        "large_files_excluded": 0,
        "omitted_source_count": 0,
        "omitted_test_count": 0,
        "redactions": 0,
        "total_prompt_bytes": 0,
    }

    try:
        plan = project_planner.run_project_plan(repo_path)
    except Exception:
        return evidence

    matching = [a for a in plan.actions if a.action_id == action_id]
    if not matching:
        return evidence
    evidence["action"] = matching[0]

    scan = scanner.run_scan(repo_path)
    analysis = analyzer.run_analysis(repo_path)

    evidence["scan_summary"] = (
        f"Python files: {scan.python_file_count}, "
        f"Test files: {scan.test_file_count}, "
        f"TODO: {scan.todo_count}, FIXME: {scan.fixme_count}, "
        f"NotImplementedError: {scan.not_implemented_count}"
    )
    evidence["analyzer_summary"] = (
        f"Project type: {analysis.project_type}, "
        f"Test framework: {analysis.test_framework}, "
        f"Health: {analysis.overall_health}"
    )

    evidence["secret_files_excluded"] = _count_secret_files(repo_path)

    source_budget = _MAX_SOURCE_FILES
    test_budget = _MAX_TEST_FILES
    prompt_bytes = 0

    for full_path, name in scanner.iter_python_files(repo_path):
        rel = os.path.relpath(full_path, repo_path)

        is_test = scanner.is_test_file(name)
        if is_test and test_budget <= 0:
            evidence["omitted_test_count"] += 1
            continue
        if not is_test and source_budget <= 0:
            evidence["omitted_source_count"] += 1
            continue

        try:
            file_size = os.path.getsize(full_path)
        except OSError:
            continue

        if file_size > _MAX_FILE_BYTES:
            evidence["large_files_excluded"] += 1
            continue

        content, sha = _read_file(full_path)
        if not content:
            evidence["binary_files_excluded"] += 1
            continue

        redacted_content, redacts = _redact_inline_secrets(content)
        evidence["redactions"] += redacts

        entry = {
            "path": rel,
            "sha256": sha,
            "content": redacted_content,
        }

        new_bytes = len(json.dumps(entry))
        if prompt_bytes + new_bytes > _MAX_PROMPT_BYTES:
            if is_test:
                evidence["omitted_test_count"] += 1
            else:
                evidence["omitted_source_count"] += 1
            continue

        if is_test:
            evidence["test_files"].append(entry)
            test_budget -= 1
        else:
            evidence["source_files"].append(entry)
            source_budget -= 1
        prompt_bytes += new_bytes

    evidence["total_prompt_bytes"] = prompt_bytes
    return evidence


# --------------------------------------------------------------------------- #
# Prompt construction
# --------------------------------------------------------------------------- #


_SYSTEM_PROMPT = """You are a code repair assistant. Generate a structured JSON
repair proposal for a specific project action.

Rules:
- Output ONLY valid JSON. No markdown fences, no commentary outside JSON.
- Every file path must be relative to the repository root.
- Every proposed_patch must use unified diff format.
- Every file must have its current_sha256 verified.
- Set safe_to_apply=false whenever any blocker exists.
- Do not include git commit or git push in any patch or instruction.
- Do not include shell commands in proposed_patch.
- Confidence must be 0-100.

Output schema:
{
  "summary": "one-line summary",
  "reasoning_summary": "why this approach",
  "files": [
    {
      "path": "relative/path.py",
      "purpose": "what this file does in the fix",
      "current_sha256": "sha256-of-current-file",
      "proposed_change_summary": "what changed",
      "proposed_patch": "unified diff",
      "confidence": 80
    }
  ],
  "validation_plan": ["step 1", "step 2"],
  "risks": ["risk 1"],
  "blockers": ["blocker if any"],
  "safe_to_apply": true,
  "confidence": 85
}"""


def _build_prompt(evidence: dict) -> str:
    parts = []
    parts.append("Repository scan summary:")
    parts.append(evidence.get("scan_summary", ""))
    parts.append("")
    parts.append("Project analysis summary:")
    parts.append(evidence.get("analyzer_summary", ""))
    parts.append("")

    action = evidence.get("action")
    if action:
        parts.append("Action to repair:")
        parts.append(f"  Title: {action.title}")
        parts.append(f"  Category: {action.category}")
        parts.append(f"  Priority: {action.priority}")
        parts.append(f"  Reason: {action.reason}")
        parts.append("")

    parts.append("Source files:")
    for sf in evidence.get("source_files", []):
        parts.append(f"--- {sf['path']} (SHA-256: {sf['sha256']}) ---")
        parts.append(sf["content"])
        parts.append("")

    parts.append("Test files:")
    for tf in evidence.get("test_files", []):
        parts.append(f"--- {tf['path']} (SHA-256: {tf['sha256']}) ---")
        parts.append(tf["content"])
        parts.append("")

    if evidence.get("omitted_source_count"):
        parts.append(f"({evidence['omitted_source_count']} additional source files omitted due to limits.)")
    if evidence.get("omitted_test_count"):
        parts.append(f"({evidence['omitted_test_count']} additional test files omitted due to limits.)")
    if evidence.get("secret_files_excluded"):
        parts.append(f"({evidence['secret_files_excluded']} secret-bearing files excluded.)")

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Response validation
# --------------------------------------------------------------------------- #


def _validate_proposal_json(data: dict, evidence: dict,
                            repo_path: str) -> list[str]:
    errors: list[str] = []
    allowed_keys = {
        "summary", "reasoning_summary", "files", "validation_plan",
        "risks", "blockers", "safe_to_apply", "confidence",
    }
    unknown = set(data.keys()) - allowed_keys
    if unknown:
        errors.append(f"Unknown top-level fields: {', '.join(sorted(unknown))}")

    for key in ("summary", "reasoning_summary"):
        if key not in data:
            errors.append(f"Missing required field: {key}")
        elif not isinstance(data[key], str):
            errors.append(f"Field '{key}' must be a string.")

    if "confidence" not in data:
        errors.append("Missing required field: confidence")
    elif not isinstance(data["confidence"], (int, float)):
        errors.append("confidence must be a number.")
    elif not (0 <= data["confidence"] <= 100):
        errors.append("confidence must be 0-100.")

    if "safe_to_apply" not in data:
        errors.append("Missing required field: safe_to_apply")
    elif not isinstance(data["safe_to_apply"], bool):
        errors.append("safe_to_apply must be a boolean.")

    if "files" not in data:
        errors.append("Missing required field: files")
    elif not isinstance(data["files"], list):
        errors.append("files must be a list.")
    else:
        if len(data["files"]) > _MAX_PROPOSED_FILES:
            errors.append(
                f"Maximum {_MAX_PROPOSED_FILES} files allowed; got {len(data['files'])}."
            )

        context_paths = {f["path"] for f in evidence.get("source_files", [])}
        context_paths |= {f["path"] for f in evidence.get("test_files", [])}

        for i, fe in enumerate(data["files"]):
            if not isinstance(fe, dict):
                errors.append(f"files[{i}] must be an object.")
                continue
            fe_keys = {
                "path", "purpose", "current_sha256", "proposed_change_summary",
                "proposed_patch", "confidence",
            }
            for key in fe_keys:
                if key not in fe:
                    errors.append(f"files[{i}] missing required field: {key}")

            rel_path = fe.get("path", "")
            if rel_path:
                if os.path.isabs(rel_path):
                    errors.append(f"files[{i}] path must be relative: {rel_path}")
                elif ".." in rel_path.split(os.sep):
                    errors.append(f"files[{i}] path traversal rejected: {rel_path}")
                elif rel_path not in context_paths:
                    errors.append(
                        f"files[{i}] path '{rel_path}' was not included in the "
                        "context. Only in-context files may be patched."
                    )

                if not _is_text_path(rel_path):
                    errors.append(f"files[{i}] binary file rejected: {rel_path}")

            sha = fe.get("current_sha256", "")
            if sha and rel_path:
                _, actual = _read_file(os.path.join(repo_path, rel_path))
                if actual and actual != sha:
                    errors.append(
                        f"files[{i}] SHA-256 mismatch for {rel_path}: "
                        f"expected {sha}, actual {actual}"
                    )

            patch = fe.get("proposed_patch", "")
            if patch:
                if _contains_shell_command(patch):
                    errors.append(f"files[{i}] proposed_patch contains shell commands.")
                if _contains_git_command(patch):
                    errors.append(f"files[{i}] proposed_patch contains git commands.")

    for key in ("validation_plan", "risks", "blockers"):
        if key in data and not isinstance(data[key], list):
            errors.append(f"Field '{key}' must be a list.")

    if data.get("blockers"):
        data["safe_to_apply"] = False

    return errors


# --------------------------------------------------------------------------- #
# Patch safety
# --------------------------------------------------------------------------- #


def _is_text_path(path: str) -> bool:
    text_exts = {
        ".py", ".txt", ".md", ".json", ".yaml", ".yml", ".toml",
        ".cfg", ".ini", ".csv", ".html", ".css", ".js", ".ts",
        ".xml", ".rst", ".sh", ".bat", ".ps1",
    }
    return os.path.splitext(path)[1].lower() in text_exts


def _contains_shell_command(text: str) -> bool:
    dangerous = {"rm -rf", "sudo ", "chmod", "chown", "wget", "curl",
                  "eval ", "exec(", "__import__(", "os.system(", "subprocess.",
                  "&& rm", "&& sudo", "; rm", "| sh", "| bash"}
    return any(d in text.lower() for d in dangerous)


def _contains_git_command(text: str) -> bool:
    return any(cmd in text for cmd in ("git commit", "git push", "git add"))


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def build_repair_proposal(
    repo_path: str,
    action_id: str,
    provider: str = "local",
    model: str | None = None,
) -> RepairProposal:
    """Collect evidence, build a prompt, call the AI provider, validate the
    response, and return a RepairProposal. Read-only: never writes to the
    repository."""
    repo_path = os.path.abspath(repo_path)

    result = RepairProposal(
        repo_path=repo_path,
        action_id=action_id,
        provider=provider,
        model=providers._resolve_model(provider, model),
    )

    evidence = _collect_evidence(repo_path, action_id)
    result.redactions = evidence.get("redactions", 0)
    redaction_parts = []
    if evidence.get("secret_files_excluded"):
        redaction_parts.append(
            f"{evidence['secret_files_excluded']} secret-bearing files excluded."
        )
    if evidence.get("redactions"):
        redaction_parts.append(
            f"{evidence['redactions']} inline secret(s) redacted."
        )
    result.redaction_summary = " ".join(redaction_parts) or "No secrets detected."

    action = evidence.get("action")
    if action is None:
        result.provider_error = f"Action ID '{action_id}' not found."
        result.blockers = (result.provider_error,)
        return result

    result.action_title = action.title

    prompt = _build_prompt(evidence)

    provider_result = providers.generate_proposal_json(
        prompt, _SYSTEM_PROMPT, provider=provider, model=model,
    )

    if provider_result.blocked:
        result.provider_error = provider_result.error
        result.blockers = (provider_result.error,)
        return result

    data = provider_result.raw_json or {}

    validation_errors = _validate_proposal_json(data, evidence, repo_path)
    if validation_errors:
        result.provider_error = (
            "Provider response failed validation:\n" +
            "\n".join(f"- {e}" for e in validation_errors)
        )
        result.blockers = tuple(validation_errors)
        return result

    result.summary = data.get("summary", "")
    result.reasoning_summary = data.get("reasoning_summary", "")
    result.safe_to_apply = data.get("safe_to_apply", False)
    result.confidence = int(data.get("confidence", 0))

    files = []
    for fe in data.get("files", []):
        files.append(RepairProposalFile(
            path=fe.get("path", ""),
            purpose=fe.get("purpose", ""),
            current_sha256=fe.get("current_sha256", ""),
            proposed_change_summary=fe.get("proposed_change_summary", ""),
            proposed_patch=fe.get("proposed_patch", ""),
            confidence=int(fe.get("confidence", 0)),
        ))
    result.files = tuple(files)

    result.validation_plan = tuple(data.get("validation_plan", []))
    result.risks = tuple(data.get("risks", []))
    result.blockers = tuple(data.get("blockers", [])) or result.blockers

    return result


def write_repair_proposal(
    proposal: RepairProposal,
    output_path: str,
    overwrite: bool = False,
) -> str:
    """Write a RepairProposal to an absolute JSON file path. Uses atomic
    write (temp file + os.replace). Returns output_path on success.
    Raises FileExistsError if the file exists and overwrite is False."""
    if not os.path.isabs(output_path):
        raise ValueError("Output path must be absolute.")

    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {output_path}. Use --overwrite to replace."
        )

    out = {
        "schema_version": "1.0",
        "generated_by": "AutoCorp CLI Phase 1G",
        "provider": proposal.provider,
        "model": proposal.model,
        "repository": proposal.repo_path,
        "action": {
            "id": proposal.action_id,
            "title": proposal.action_title,
        },
        "proposal": {
            "summary": proposal.summary,
            "reasoning_summary": proposal.reasoning_summary,
            "safe_to_apply": proposal.safe_to_apply,
            "confidence": proposal.confidence,
            "files": [
                {
                    "path": f.path,
                    "purpose": f.purpose,
                    "current_sha256": f.current_sha256,
                    "proposed_change_summary": f.proposed_change_summary,
                    "proposed_patch": f.proposed_patch,
                    "confidence": f.confidence,
                }
                for f in proposal.files
            ],
            "validation_plan": list(proposal.validation_plan),
            "risks": list(proposal.risks),
            "blockers": list(proposal.blockers),
        },
        "redaction_summary": proposal.redaction_summary,
        "source_file_hashes": {},
    }

    if proposal.provider_error:
        out["provider_error"] = proposal.provider_error

    json_bytes = json.dumps(out, indent=2, ensure_ascii=False).encode("utf-8")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(output_path) or ".",
        prefix=".autocorp_proposal_",
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(json_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise

    return output_path
