#!/usr/bin/env python3
"""Deterministic context budget builder for provider-backed repair work."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import re
from dataclasses import dataclass, field
from typing import Any

from brains import repo_policy

SCHEMA_VERSION = "2a.context.v1"

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
    r'(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token'
    r'|auth[_-]?token|private[_-]?key|client[_-]?secret'
    r'|aws[_-]?secret[_-]?access[_-]?key)\s*[:=]\s*\S+|'
    r'(?:[a-z0-9]*_)?(?:password|secret)(?:_[a-z0-9]*)?\s*[:=]\s*\S+|'
    r'"?authorization"?\s*:\s*"?bearer\s+[^"\s,}]+|'
    r'(postgres|mysql|mongodb|redis)://[^@]*:[^@]*@[^\s]+',
    re.IGNORECASE,
)


def _is_secret_file(file_path: str) -> bool:
    basename = os.path.basename(file_path)
    return any(pat.search(basename) or pat.search(file_path) for pat in _SECRET_FILE_PATTERNS)


def _redact_inline_secrets(content: str) -> tuple[str, int]:
    count = len(_INLINE_SECRET_RE.findall(content))

    def repl(match: re.Match) -> str:
        text = match.group(0)
        if "://" in text and "@" in text:
            return text.split("://", 1)[0] + "://[REDACTED]@" + text.rsplit("@", 1)[1]
        for sep in ("=", ":"):
            if sep in text:
                return text.split(sep, 1)[0] + sep + " [REDACTED]"
        return "[REDACTED]"

    return _INLINE_SECRET_RE.sub(repl, content), count


def estimate_tokens_from_bytes(byte_count: int) -> int:
    """Deterministic estimate: ceil(bytes / 4).

    This is an estimate only. It is not provider-reported token usage.
    """
    return (max(byte_count, 0) + 3) // 4


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_commit(repo_path: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_diff(repo_path: str, rel: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, "diff", "--", rel],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def repository_fingerprint(repo_path: str) -> str:
    repo_path = os.path.abspath(repo_path)
    items: list[tuple[str, str]] = []
    for full, rel, _cls in repo_policy.walk_source_files(repo_path):
        try:
            with open(full, "rb") as fh:
                items.append((rel, _sha_bytes(fh.read())))
        except OSError:
            items.append((rel, "unreadable"))
    return _sha_bytes(json.dumps(items, sort_keys=True).encode("utf-8"))


def working_tree_fingerprint(repo_path: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain=v1", "--untracked-files=no"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        text = ""
    else:
        text = proc.stdout if proc.returncode == 0 else ""
    return _sha_bytes(text.encode("utf-8"))


@dataclass
class ContextItem:
    path: str
    start_line: int
    end_line: int
    selection_reason: str
    content_sha256: str
    selected_byte_count: int
    kind: str
    truncated: bool
    content: str = field(repr=False)

    def safe_manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "selection_reason": self.selection_reason,
            "content_sha256": self.content_sha256,
            "selected_byte_count": self.selected_byte_count,
            "kind": self.kind,
            "truncated": self.truncated,
        }


@dataclass
class ContextManifest:
    schema_version: str
    repository_fingerprint: str
    git_commit: str
    working_tree_fingerprint: str
    operation: str
    target_path: str
    provider: str
    model: str
    selected_item_count: int
    omitted_candidate_count: int
    total_source_bytes: int
    system_prompt_bytes: int
    user_prompt_bytes: int
    total_prompt_bytes: int
    estimated_input_tokens: int
    truncation_warnings: list[str]
    uncertainty_warnings: list[str]
    context_fingerprint: str
    items: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _read_text(path: str, max_bytes: int) -> tuple[str, str, bool]:
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    return data.decode("utf-8", errors="replace"), _sha_bytes(data), truncated


def _line_count(text: str) -> int:
    return max(1, len(text.splitlines()))


def _imports_from_python(text: str) -> set[str]:
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def _related_tests(repo_path: str, target_rel: str) -> list[str]:
    stem = os.path.splitext(os.path.basename(target_rel))[0].lower()
    out = []
    for _full, rel, cls in repo_policy.walk_source_files(repo_path, suffixes=(".py",), include_tests=True):
        if cls.category != "tests":
            continue
        base = os.path.basename(rel).lower()
        if stem in base or stem in rel.lower():
            out.append(rel)
    return sorted(out)


def _direct_dependencies(repo_path: str, target_text: str) -> list[str]:
    imports = _imports_from_python(target_text)
    if not imports:
        return []
    out = []
    for _full, rel, cls in repo_policy.walk_source_files(repo_path, suffixes=(".py",), include_tests=False):
        if cls.category == "tests":
            continue
        module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel
        root = module.split(".")[0]
        if root in imports:
            out.append(rel)
    return sorted(out)


def build_repair_context(
    repo_path: str,
    target_path: str,
    failure_description: str,
    *,
    provider: str,
    model: str,
    operation: str = "repair",
    system_prompt: str = "",
    max_prompt_bytes: int = 60 * 1024,
    max_file_bytes: int = 24 * 1024,
) -> tuple[str, ContextManifest]:
    repo_path = os.path.abspath(repo_path)
    target_rel = repo_policy.normalize_rel_path(repo_path, target_path)
    target_cls = repo_policy.classify_path(repo_path, target_rel)
    if target_cls.excluded:
        raise ValueError(f"target path is excluded by repository policy: {target_rel}")
    if _is_secret_file(target_rel):
        raise ValueError(f"target path is secret-bearing and cannot be sent: {target_rel}")

    items: list[ContextItem] = []
    warnings: list[str] = []
    omitted = 0

    def add_item(rel: str, reason: str, kind: str = "full_file") -> None:
        nonlocal omitted
        if any(i.path == rel and i.kind == kind for i in items):
            return
        if _is_secret_file(rel):
            omitted += 1
            return
        full = os.path.join(repo_path, rel)
        if not os.path.isfile(full):
            omitted += 1
            return
        text, sha, truncated = _read_text(full, max_file_bytes)
        text, _redactions = _redact_inline_secrets(text)
        if truncated:
            warnings.append(f"{rel} truncated to {max_file_bytes} bytes")
        items.append(ContextItem(
            path=rel,
            start_line=1,
            end_line=_line_count(text),
            selection_reason=reason,
            content_sha256=sha,
            selected_byte_count=len(text.encode("utf-8")),
            kind=kind,
            truncated=truncated,
            content=text,
        ))

    add_item(target_rel, "exact target file")
    target_text = items[0].content if items else ""

    diff = _git_diff(repo_path, target_rel)
    if diff:
        data = diff.encode("utf-8")
        truncated = len(data) > max_file_bytes
        if truncated:
            diff = data[:max_file_bytes].decode("utf-8", errors="replace")
            warnings.append(f"{target_rel} diff truncated to {max_file_bytes} bytes")
        items.append(ContextItem(
            path=target_rel,
            start_line=1,
            end_line=_line_count(diff),
            selection_reason="changed hunks touching target",
            content_sha256=_sha_bytes(diff.encode("utf-8")),
            selected_byte_count=len(diff.encode("utf-8")),
            kind="diff",
            truncated=truncated,
            content=diff,
        ))

    for rel in _direct_dependencies(repo_path, target_text)[:4]:
        add_item(rel, "direct import/dependency")
    for rel in _related_tests(repo_path, target_rel)[:6]:
        add_item(rel, "closely related test")

    prompt_parts = [
        f"Operation: {operation}",
        f"Target path: {target_rel}",
        "Failure description:",
        failure_description,
        "",
        "Selected context follows. Use only these files.",
    ]
    selected: list[ContextItem] = []
    total_source_bytes = 0
    for item in items:
        block = f"\n--- {item.path} [{item.kind}; {item.selection_reason}; sha256={item.content_sha256}] ---\n{item.content}\n"
        if len(("\n".join(prompt_parts)).encode("utf-8")) + len(block.encode("utf-8")) > max_prompt_bytes:
            omitted += 1
            continue
        prompt_parts.append(block)
        selected.append(item)
        total_source_bytes += item.selected_byte_count

    prompt_parts.append(
        "\nOutput contract: return exactly one complete replacement file content for the target path. "
        "Do not include markdown fences, explanations, shell commands, or edits for other files."
    )
    user_prompt = "\n".join(prompt_parts)
    system_bytes = len(system_prompt.encode("utf-8"))
    user_bytes = len(user_prompt.encode("utf-8"))
    context_payload = {
        "schema_version": SCHEMA_VERSION,
        "repo": repository_fingerprint(repo_path),
        "target": target_rel,
        "failure": failure_description,
        "provider": provider,
        "model": model,
        "system_sha": _sha_bytes(system_prompt.encode("utf-8")),
        "items": [i.safe_manifest() for i in selected],
    }
    context_fp = _sha_bytes(json.dumps(context_payload, sort_keys=True).encode("utf-8"))
    manifest = ContextManifest(
        schema_version=SCHEMA_VERSION,
        repository_fingerprint=context_payload["repo"],
        git_commit=_repo_commit(repo_path),
        working_tree_fingerprint=working_tree_fingerprint(repo_path),
        operation=operation,
        target_path=target_rel,
        provider=provider,
        model=model,
        selected_item_count=len(selected),
        omitted_candidate_count=omitted,
        total_source_bytes=total_source_bytes,
        system_prompt_bytes=system_bytes,
        user_prompt_bytes=user_bytes,
        total_prompt_bytes=system_bytes + user_bytes,
        estimated_input_tokens=estimate_tokens_from_bytes(system_bytes + user_bytes),
        truncation_warnings=warnings,
        uncertainty_warnings=["token counts are deterministic byte/4 estimates, not provider-reported usage"],
        context_fingerprint=context_fp,
        items=[i.safe_manifest() for i in selected],
    )
    return user_prompt, manifest
