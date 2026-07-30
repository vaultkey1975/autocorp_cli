#!/usr/bin/env python3
"""Route Reliability Engine requests between greenfield and existing-file edit mode."""

import os
import re
import subprocess
from dataclasses import dataclass, field


EDIT_WORDS = {
    "add", "change", "edit", "update", "modify", "fix", "remove", "rename",
    "refactor", "implement", "return", "count",
}


@dataclass
class EditRoute:
    mode: str
    targets: list[str] = field(default_factory=list)
    inferred_target: bool = False

    @property
    def is_edit(self) -> bool:
        return self.mode == "edit"


def tracked_files(repo_root: str) -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def filepath_tokens(request: str) -> list[str]:
    tokens = re.findall(r"(?<![\w/.-])[\w./-]+\.[A-Za-z0-9_]+(?![\w/.-])", request or "")
    cleaned = []
    for token in tokens:
        rel = token.strip("`'\".,;:()[]{}")
        if rel.startswith("./"):
            rel = rel[2:]
        cleaned.append(rel)
    return cleaned


def _looks_like_edit_request(request: str) -> bool:
    words = set(re.findall(r"[A-Za-z_]+", (request or "").lower()))
    return bool(words & EDIT_WORDS)


def classify_request(repo_root: str, request: str, inferred_target: str | None = None) -> EditRoute:
    tracked = tracked_files(repo_root)
    matches = []
    for token in filepath_tokens(request):
        normalized = token.replace(os.sep, "/")
        if normalized in tracked and normalized not in matches:
            matches.append(normalized)
    if matches:
        return EditRoute("edit", matches, inferred_target=False)
    if inferred_target and _looks_like_edit_request(request):
        return EditRoute("edit", [inferred_target], inferred_target=True)
    return EditRoute("greenfield")


def read_targets(repo_root: str, targets: list[str]) -> dict[str, str]:
    contents = {}
    root = os.path.abspath(repo_root)
    for target in targets:
        path = os.path.abspath(os.path.join(root, target))
        if not path.startswith(root + os.sep) and path != root:
            raise ValueError(f"target escapes repository: {target}")
        with open(path, encoding="utf-8") as fh:
            contents[target] = fh.read()
    return contents
