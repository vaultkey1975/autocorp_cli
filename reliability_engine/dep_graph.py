#!/usr/bin/env python3
"""Import/call graph and blast-radius calculation."""

import ast
import os
from collections import defaultdict


PY_SUFFIX = ".py"


def _module_name(root: str, path: str) -> str:
    rel = os.path.relpath(path, root)
    if rel.endswith(PY_SUFFIX):
        rel = rel[:-3]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


def _resolve_import(name: str, modules: dict[str, str]) -> str | None:
    if name in modules:
        return modules[name]
    parts = name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return modules[candidate]
        parts.pop()
    return None


class DependencyGraph:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.imports: dict[str, set[str]] = defaultdict(set)
        self.calls: dict[str, set[str]] = defaultdict(set)
        self.modules: dict[str, str] = {}

    def build(self) -> "DependencyGraph":
        paths = []
        for dirpath, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", ".venv", "venv"}]
            for filename in files:
                if filename.endswith(PY_SUFFIX):
                    path = os.path.join(dirpath, filename)
                    paths.append(path)
                    self.modules[_module_name(self.root, path)] = os.path.relpath(path, self.root)
        for path in paths:
            rel = os.path.relpath(path, self.root)
            try:
                with open(path, encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=rel)
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = _resolve_import(alias.name, self.modules)
                        if target:
                            self.imports[rel].add(target)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for alias in node.names:
                            target = _resolve_import(f"{node.module}.{alias.name}", self.modules)
                            if target is None:
                                target = _resolve_import(node.module, self.modules)
                            if target:
                                self.imports[rel].add(target)
                elif isinstance(node, ast.Call):
                    name = ""
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    if name:
                        self.calls[rel].add(name)
        return self

    def dependents(self, relpath: str) -> set[str]:
        wanted = relpath.replace("/", os.sep)
        return {src for src, imports in self.imports.items() if wanted in imports}

    def blast_radius(self, files: list[str]) -> int:
        seen = set()
        queue = [f.replace("/", os.sep) for f in files]
        while queue:
            current = queue.pop(0)
            for dep in self.dependents(current):
                if dep not in seen:
                    seen.add(dep)
                    queue.append(dep)
        return len(seen)

    def touches_core(self, files: list[str]) -> bool:
        markers = ("core/", "memory/", "safety/", "brains/", "reliability_engine/")
        return any(f.replace(os.sep, "/").startswith(markers) for f in files)
