#!/usr/bin/env python3
"""Apply model-generated patches and guarded full-file replacements."""

import difflib
import os
import re
import subprocess
from dataclasses import dataclass


@dataclass
class PatchResult:
    ok: bool
    mode: str
    stdout: str = ""
    stderr: str = ""


@dataclass
class GroundingMismatch:
    path: str
    hunk_header: str
    expected_context: list[str]
    actual_excerpt: list[str]


@dataclass
class GroundingResult:
    ok: bool
    mismatches: list[GroundingMismatch]

    @property
    def message(self) -> str:
        if self.ok:
            return ""
        chunks = []
        for mismatch in self.mismatches:
            expected_label = "FIND TEXT" if mismatch.hunk_header.startswith("FIND/REPLACE") else "DIFF CONTEXT"
            chunks.append(
                f"{mismatch.path} {mismatch.hunk_header}\n"
                f"{expected_label}:\n"
                + "\n".join(mismatch.expected_context)
                + "\nACTUAL FILE EXCERPT:\n"
                + "\n".join(mismatch.actual_excerpt)
            )
        return "\n\n".join(chunks)


@dataclass
class ReplaceBlock:
    find: str
    replace: str
    path: str = ""


def _line_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except FileNotFoundError:
        return 0


def _strip_outer_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip() + "\n"
    return text


def parse_replace_blocks(text: str) -> list[ReplaceBlock]:
    raw = _strip_outer_fence(text)
    canonical = re.compile(
        r"^(?:<<<<<<< FILE[ \t]+(.+?)[ \t]*\r?\n)?<<<<<<< FIND\r?\n"
        r"(.*?)^=======\r?\n(.*?)^>>>>>>> REPLACE[ \t]*$",
        re.MULTILINE | re.DOTALL,
    )
    shorthand = re.compile(
        r"^<<<<<<<[ \t]+(?!FIND\b|FILE\b)(.+?)[ \t]*\r?\n"
        r"(.*?)^=======\r?\n(.*?)^>>>>>>> REPLACE[ \t]*$",
        re.MULTILINE | re.DOTALL,
    )
    pattern = canonical if canonical.search(raw) else shorthand
    blocks = [
        ReplaceBlock(match.group(2), match.group(3), (match.group(1) or "").strip())
        for match in pattern.finditer(raw)
    ]
    if not blocks:
        raise ValueError("builder output was not FIND/REPLACE blocks")
    for block in blocks:
        if block.find == "":
            raise ValueError("FIND block must not be empty")
    covered = pattern.sub("", raw).strip()
    if covered:
        raise ValueError("builder output contains text outside FIND/REPLACE blocks")
    return blocks


def _closest_excerpt(content: str, needle: str) -> list[str]:
    lines = content.splitlines()
    needle_lines = [line for line in needle.splitlines() if line.strip()]
    if not lines:
        return ["file is empty"]
    target = needle_lines[0] if needle_lines else needle.strip()
    best = 0
    best_score = -1.0
    for idx, line in enumerate(lines):
        score = difflib.SequenceMatcher(None, target, line).ratio()
        if score > best_score:
            best = idx
            best_score = score
    start = max(best - 5, 0)
    end = min(best + max(len(needle_lines), 1) + 6, len(lines))
    return [f"{line_no}: {text}" for line_no, text in enumerate(lines[start:end], start + 1)]


def _safe_target(workspace: str, relpath: str) -> tuple[str, str]:
    target = os.path.abspath(os.path.join(workspace, relpath))
    root = os.path.abspath(workspace)
    return target, root


def verify_replace_blocks(workspace: str, relpath: str, blocks: list[ReplaceBlock]) -> GroundingResult:
    mismatches: list[GroundingMismatch] = []
    working_by_path: dict[str, str] = {}
    for index, block in enumerate(blocks, 1):
        block_path = block.path or relpath
        target, root = _safe_target(workspace, block_path)
        if not target.startswith(root + os.sep) and target != root:
            mismatches.append(GroundingMismatch(block_path, "FIND/REPLACE", [], ["target escapes workspace"]))
            continue
        if block_path not in working_by_path:
            try:
                with open(target, encoding="utf-8") as fh:
                    working_by_path[block_path] = fh.read()
            except OSError as exc:
                mismatches.append(GroundingMismatch(block_path, "FIND/REPLACE", [], [str(exc)]))
                continue
        working = working_by_path[block_path]
        if block.find not in working:
            mismatches.append(
                GroundingMismatch(
                    block_path,
                    f"FIND/REPLACE block {index}",
                    block.find.splitlines(),
                    _closest_excerpt(working, block.find),
                )
            )
            continue
        working_by_path[block_path] = working.replace(block.find, block.replace, 1)
    return GroundingResult(not mismatches, mismatches)


def _diff_path(line: str) -> str:
    path = line[4:].strip().split("\t", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def _hunk_start(header: str) -> int:
    match = re.search(r"@@ -(\d+)", header)
    return int(match.group(1)) if match else 1


def _in_order(needles: list[str], haystack: list[str]) -> bool:
    pos = 0
    for needle in needles:
        found = False
        while pos < len(haystack):
            if haystack[pos] == needle:
                pos += 1
                found = True
                break
            pos += 1
        if not found:
            return False
    return True


def verify_diff_grounding(workspace: str, diff: str) -> GroundingResult:
    """Verify unchanged hunk context lines exist in the target file in order."""
    current_path = ""
    mismatches: list[GroundingMismatch] = []
    lines = diff.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("+++ "):
            current_path = _diff_path(line)
            idx += 1
            continue
        if not line.startswith("@@ "):
            idx += 1
            continue

        header = line
        context: list[str] = []
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("@@ ") and not lines[idx].startswith("--- "):
            hunk_line = lines[idx]
            if hunk_line.startswith(" ") and hunk_line != r"\ No newline at end of file":
                context.append(hunk_line[1:])
            idx += 1

        if not current_path or not context:
            continue
        target = os.path.abspath(os.path.join(workspace, current_path))
        root = os.path.abspath(workspace)
        if not target.startswith(root + os.sep) and target != root:
            mismatches.append(GroundingMismatch(current_path, header, context, ["target escapes workspace"]))
            continue
        try:
            with open(target, encoding="utf-8") as fh:
                actual_lines = fh.read().splitlines()
        except OSError as exc:
            mismatches.append(GroundingMismatch(current_path, header, context, [str(exc)]))
            continue
        if not _in_order(context, actual_lines):
            start = max(_hunk_start(header) - 4, 0)
            end = min(start + max(len(context) + 8, 12), len(actual_lines))
            excerpt = [f"{line_no}: {text}" for line_no, text in enumerate(actual_lines[start:end], start + 1)]
            mismatches.append(GroundingMismatch(current_path, header, context, excerpt))
        continue
    return GroundingResult(not mismatches, mismatches)


def apply_diff(workspace: str, diff: str) -> PatchResult:
    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=diff,
        text=True,
        capture_output=True,
    )
    return PatchResult(proc.returncode == 0, "diff", proc.stdout, proc.stderr)


def apply_replace_blocks(workspace: str, relpath: str, blocks: list[ReplaceBlock]) -> PatchResult:
    grounding = verify_replace_blocks(workspace, relpath, blocks)
    if not grounding.ok:
        return PatchResult(False, "find_replace", stderr=grounding.message)
    content_by_path: dict[str, str] = {}
    for block in blocks:
        block_path = block.path or relpath
        target, root = _safe_target(workspace, block_path)
        if not target.startswith(root + os.sep) and target != root:
            return PatchResult(False, "find_replace", stderr=f"{block_path}: target escapes workspace")
        if block_path not in content_by_path:
            with open(target, encoding="utf-8") as fh:
                content_by_path[block_path] = fh.read()
        content_by_path[block_path] = content_by_path[block_path].replace(block.find, block.replace, 1)
    for block_path, content in content_by_path.items():
        target, _root = _safe_target(workspace, block_path)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content)
    return PatchResult(True, "find_replace")


def reset_paths(workspace: str, paths: list[str]) -> PatchResult:
    if not paths:
        return PatchResult(True, "reset")
    tracked = []
    for path in paths:
        check = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path],
            cwd=workspace,
            text=True,
            capture_output=True,
        )
        if check.returncode == 0:
            tracked.append(path)
    if not tracked:
        return PatchResult(True, "reset")
    proc = subprocess.run(
        ["git", "checkout", "--", *tracked],
        cwd=workspace,
        text=True,
        capture_output=True,
    )
    return PatchResult(proc.returncode == 0, "reset", proc.stdout, proc.stderr)


def reverse_diff(workspace: str, diff: str) -> PatchResult:
    proc = subprocess.run(
        ["git", "apply", "-R", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=diff,
        text=True,
        capture_output=True,
    )
    return PatchResult(proc.returncode == 0, "diff_reverse", proc.stdout, proc.stderr)


def apply_full_file(workspace: str, relpath: str, content: str, threshold: int) -> PatchResult:
    target = os.path.abspath(os.path.join(workspace, relpath))
    root = os.path.abspath(workspace)
    if not target.startswith(root + os.sep) and target != root:
        return PatchResult(False, "full_file", stderr="target escapes workspace")
    existing_lines = _line_count(target)
    new_lines = len(content.splitlines())
    if max(existing_lines, new_lines) > int(threshold):
        return PatchResult(
            False,
            "full_file",
            stderr=f"full-file replacement exceeds threshold {threshold}",
        )
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(content if content.endswith("\n") else content + "\n")
    return PatchResult(True, "full_file")
