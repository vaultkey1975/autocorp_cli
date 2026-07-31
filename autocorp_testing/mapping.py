"""Source-to-test mapping.

Combines filename matching, import analysis, symbol/feature matching,
dependency expansion, and explicit `.autocorp/test-profile.json` mappings.
Every returned test path carries the list of reasons it was selected for.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any

_IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "workspace",
    "data", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".tox", ".nox",
}


@dataclass
class SourceFileIndex:
    rel: str
    module: str
    symbols: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)


@dataclass
class TestFileIndex:
    rel: str
    functions: list[str] = field(default_factory=list)
    classes: dict[str, list[str]] = field(default_factory=dict)
    imports: set[str] = field(default_factory=set)
    text_lower: str = ""


def _read(repo_path: str, rel: str) -> str:
    try:
        with open(os.path.join(repo_path, rel), encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def module_name(rel: str) -> str:
    rel = rel.replace(os.sep, "/")
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


def is_test_file(rel: str) -> bool:
    base = os.path.basename(rel)
    return rel.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def all_python_files(repo_path: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                rel = os.path.relpath(os.path.join(root, name), repo_path).replace(os.sep, "/")
                out.append(rel)
    return sorted(out)


def index_source_file(repo_path: str, rel: str) -> SourceFileIndex:
    text = _read(repo_path, rel)
    idx = SourceFileIndex(rel=rel, module=module_name(rel))
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return idx
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            idx.symbols.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                idx.imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            idx.imports.add(node.module)
            for alias in node.names:
                idx.imports.add(f"{node.module}.{alias.name}")
    return idx


def index_test_file(repo_path: str, rel: str) -> TestFileIndex:
    text = _read(repo_path, rel)
    idx = TestFileIndex(rel=rel, text_lower=text.lower())
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return idx
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            idx.functions.append(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            methods = [
                child.name for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test")
            ]
            idx.classes[node.name] = methods
        elif isinstance(node, ast.Import):
            for alias in node.names:
                idx.imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            idx.imports.add(node.module)
            for alias in node.names:
                idx.imports.add(f"{node.module}.{alias.name}")
    return idx


def node_ids(idx: TestFileIndex) -> list[str]:
    ids = [f"{idx.rel}::{name}" for name in idx.functions]
    for cls, methods in idx.classes.items():
        ids.extend(f"{idx.rel}::{cls}::{m}" for m in methods)
    return ids or [idx.rel]


def build_reverse_dependency_graph(sources: dict[str, SourceFileIndex]) -> dict[str, set[str]]:
    """module -> set of modules that import it (one hop)."""
    graph: dict[str, set[str]] = {m: set() for m in sources}
    for rel, idx in sources.items():
        for imported in idx.imports:
            for candidate_module, candidate_idx in sources.items():
                if candidate_module == idx.module:
                    continue
                if imported == candidate_idx.module or imported.startswith(candidate_idx.module + "."):
                    graph.setdefault(candidate_idx.module, set()).add(idx.module)
    return graph


@dataclass
class MappingResult:
    reasons: dict[str, list[str]] = field(default_factory=dict)  # test rel -> reasons
    categories: dict[str, str] = field(default_factory=dict)  # test rel -> best category
    uncertainty_warnings: list[str] = field(default_factory=list)

    def add(self, test_rel: str, reason: str, category: str) -> None:
        self.reasons.setdefault(test_rel, [])
        if reason not in self.reasons[test_rel]:
            self.reasons[test_rel].append(reason)
        # First category wins unless upgrading from a weaker default.
        self.categories.setdefault(test_rel, category)


def _basename_no_ext(rel: str) -> str:
    return os.path.splitext(os.path.basename(rel))[0]


def map_changed_files_to_tests(
    repo_path: str,
    changed_files: list[str],
    *,
    feature: str | None = None,
    profile: dict[str, Any] | None = None,
) -> tuple[MappingResult, dict[str, TestFileIndex], dict[str, SourceFileIndex]]:
    profile = profile or {}
    all_files = all_python_files(repo_path)
    test_files = [rel for rel in all_files if is_test_file(rel)]
    source_files = [rel for rel in all_files if not is_test_file(rel)]

    test_index = {rel: index_test_file(repo_path, rel) for rel in test_files}
    source_index = {rel: index_source_file(repo_path, rel) for rel in source_files}
    reverse_deps = build_reverse_dependency_graph(source_index)

    result = MappingResult()
    changed_source = [rel for rel in changed_files if rel in source_index]
    changed_modules = {rel: source_index[rel].module for rel in changed_source}

    for changed_rel, changed_module in changed_modules.items():
        stem = _basename_no_ext(changed_rel).lower()
        last_segment = changed_module.rsplit(".", 1)[-1].lower()
        matched_any = False

        for test_rel, tidx in test_index.items():
            test_stem = _basename_no_ext(test_rel).lower()
            # 1. Filename matching.
            if last_segment and last_segment in test_stem.replace("test_", "", 1):
                result.add(test_rel, f"matches changed module {os.path.basename(changed_rel)}", "direct")
                matched_any = True
            # 2. Import analysis.
            if any(changed_module == imp or imp.startswith(changed_module + ".") for imp in tidx.imports):
                result.add(test_rel, f"imports the changed module {changed_module}", "direct")
                matched_any = True

        # 4. Dependency expansion (one hop): tests of modules that import the
        # changed module.
        for dependent_module in reverse_deps.get(changed_module, set()):
            dependent_rel = next((r for r, i in source_index.items() if i.module == dependent_module), None)
            if not dependent_rel:
                continue
            dep_stem = _basename_no_ext(dependent_rel).lower()
            dep_last = dependent_module.rsplit(".", 1)[-1].lower()
            for test_rel, tidx in test_index.items():
                test_stem = _basename_no_ext(test_rel).lower()
                hit = dep_last in test_stem.replace("test_", "", 1) or any(
                    dependent_module == imp or imp.startswith(dependent_module + ".") for imp in tidx.imports
                )
                if hit:
                    result.add(
                        test_rel,
                        f"dependency expansion: {dependent_rel} imports changed module {changed_module}",
                        "dependency",
                    )
                    matched_any = True

        if not matched_any:
            result.uncertainty_warnings.append(
                f"No direct or import-based test match found for changed file {changed_rel}; "
                "review manually before relying on FAST/FOCUSED alone."
            )

    # 3. Feature/symbol matching.
    if feature:
        needle = feature.strip().lower()
        if needle:
            for test_rel, tidx in test_index.items():
                hit_reason = None
                if needle in os.path.basename(test_rel).lower():
                    hit_reason = f"test file name matches feature '{feature}'"
                elif any(needle in name.lower() for name in tidx.functions):
                    hit_reason = f"test function name matches feature '{feature}'"
                elif any(needle in cls.lower() for cls in tidx.classes):
                    hit_reason = f"test class name matches feature '{feature}'"
                elif needle in tidx.text_lower:
                    hit_reason = f"test body/docstring mentions feature '{feature}'"
                if hit_reason:
                    result.add(test_rel, hit_reason, "feature")
            for src_rel, sidx in source_index.items():
                if needle in sidx.module.lower() or any(needle in s.lower() for s in sidx.symbols):
                    for test_rel, tidx in test_index.items():
                        if any(sidx.module == imp or imp.startswith(sidx.module + ".") for imp in tidx.imports):
                            result.add(
                                test_rel,
                                f"imports source module '{sidx.module}' matching feature '{feature}'",
                                "feature",
                            )

    # 5. Explicit profile mappings.
    explicit = profile.get("explicit_mappings", {}) if isinstance(profile, dict) else {}
    for src, tests in explicit.items():
        if src in changed_files or src == feature:
            for test_rel in tests:
                result.add(test_rel, f"explicit mapping from .autocorp/test-profile.json for {src}", "explicit")

    feature_map = profile.get("feature_to_test_mappings", {}) if isinstance(profile, dict) else {}
    if feature and feature in feature_map:
        for test_rel in feature_map[feature]:
            result.add(test_rel, f"explicit feature mapping from .autocorp/test-profile.json for '{feature}'", "explicit")

    return result, test_index, source_index


def mandatory_safety_tests(
    test_index: dict[str, TestFileIndex],
    profile: dict[str, Any] | None,
) -> dict[str, list[str]]:
    profile = profile or {}
    out: dict[str, list[str]] = {}
    configured = profile.get("always_run_safety_tests", [])
    for test_rel in configured:
        out.setdefault(test_rel, []).append("configured as always-run safety test in .autocorp/test-profile.json")
    for test_rel in test_index:
        base = os.path.basename(test_rel).lower()
        if "safety" in base or "_gate" in base or base.startswith("test_gate"):
            out.setdefault(test_rel, []).append("mandatory safety test (heuristic: safety/gate in file name)")
    return out
