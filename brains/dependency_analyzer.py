#!/usr/bin/env python3
"""
Dependency Analyzer  (AutoCorp CLI - brains)  [Dependency Awareness Phase 1]
===========================================================================

Works out the order in which a Python project's files should be built, by
analyzing the imports BETWEEN the project's own files.

Public API:
    derive_build_order(files) -> list[str]
    build_dependency_graph(files) -> (paths, deps)   # used for inspection/tests

`files` is flexible:
    * list of dicts  [{"path": ..., "purpose": ..., "content": ...}, ...]
      (content optional - if absent, the file is read from disk when it exists)
    * list of path strings
    * dict {path: content}

Behaviour (per spec):
    * A file that imports another PROJECT file is built AFTER it.
    * Imports that are not project files (os, sys, requests, ...) are ignored.
    * Deterministic + stable: the same project always yields the same order
      (ties are broken by the original file order).
    * Circular dependency  -> log a warning, fall back to the original order.
    * Invalid syntax        -> log a warning, fall back to the original order.
    * The build always continues; this never raises.
"""

import ast
import heapq
import os

from core import console


# --------------------------------------------------------------------------- #
# Input normalisation
# --------------------------------------------------------------------------- #
def _norm_files(files) -> list:
    """Return an ordered list of {"path", "content"} dicts (content may be None)."""
    out = []
    if isinstance(files, dict):
        for path, content in files.items():
            if path:
                out.append({"path": str(path), "content": content})
        return out
    for f in files or []:
        if isinstance(f, dict):
            path = f.get("path")
            if path:
                out.append({"path": str(path), "content": f.get("content")})
        elif isinstance(f, str) and f:
            out.append({"path": f, "content": None})
    return out


def _module_name(path: str) -> str:
    """'pkg/calculator.py' -> 'calculator'."""
    base = os.path.basename(path)
    return base[:-3] if base.endswith(".py") else base


def _load_content(path: str, content):
    """Use provided content; otherwise read from disk if the file exists."""
    if content is not None:
        return content
    try:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        pass
    return None  # unknown - treated as "no imports"


# --------------------------------------------------------------------------- #
# Import detection
# --------------------------------------------------------------------------- #
def _import_roots(content: str) -> set:
    """Top-level module names imported by `content`.

    Supports:  import calculator        -> 'calculator'
               import pkg.mod            -> 'pkg'
               from calculator import x  -> 'calculator'
               from calculator import *  -> 'calculator'
               from . import calculator  -> 'calculator'   (relative)

    Raises SyntaxError if `content` is not valid Python.
    """
    roots = set()
    tree = ast.parse(content)  # may raise SyntaxError
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                roots.add(node.module.split(".")[0])
            if node.level:  # relative import: from . import calculator
                for alias in node.names:
                    if alias.name:
                        roots.add(alias.name.split(".")[0])
    return roots


# --------------------------------------------------------------------------- #
# Graph
# --------------------------------------------------------------------------- #
def build_dependency_graph(files):
    """Return (paths, deps).

    paths : the project file paths, in original order.
    deps  : {path: set(project paths that this file imports)}.

    Raises SyntaxError if a project .py file cannot be parsed (the caller
    decides how to recover)."""
    norm = _norm_files(files)
    paths = [f["path"] for f in norm]

    # Map a project module name -> its file path (first wins, stable).
    module_map = {}
    for f in norm:
        if f["path"].endswith(".py"):
            module_map.setdefault(_module_name(f["path"]), f["path"])

    deps = {p: set() for p in paths}
    for f in norm:
        path = f["path"]
        if not path.endswith(".py"):
            continue  # only Python files import each other
        content = _load_content(path, f["content"])
        if not content:
            continue
        for root in _import_roots(content):  # may raise SyntaxError
            dep_path = module_map.get(root)
            if dep_path and dep_path != path:  # ignore self + non-project imports
                deps[path].add(dep_path)
    return paths, deps


def _topo_sort(paths, deps):
    """Deterministic Kahn topological sort: a file's project dependencies come
    before it. Ties are broken by original file index, so the result is stable
    and reproducible. Returns the ordered list, or None if a cycle exists."""
    index = {p: i for i, p in enumerate(paths)}
    in_degree = {p: len(deps[p]) for p in paths}

    dependents = {p: [] for p in paths}
    for p in paths:
        for d in deps[p]:
            dependents[d].append(p)

    heap = [index[p] for p in paths if in_degree[p] == 0]
    heapq.heapify(heap)

    order = []
    while heap:
        p = paths[heapq.heappop(heap)]
        order.append(p)
        for m in dependents[p]:
            in_degree[m] -= 1
            if in_degree[m] == 0:
                heapq.heappush(heap, index[m])

    return order if len(order) == len(paths) else None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def derive_build_order(files) -> list:
    """Determine the build order for a set of project files from their imports.

    Always returns a list of every file path. Never raises - any problem
    degrades to the original file order with a logged warning."""
    norm = _norm_files(files)
    original = [f["path"] for f in norm]
    if len(original) <= 1:
        return list(original)  # single-file (or empty) - nothing to order

    try:
        paths, deps = build_dependency_graph(files)
    except SyntaxError as e:
        console.warn(
            f"Dependency analysis: invalid syntax ({e.msg} in "
            f"{getattr(e, 'filename', None) or 'a project file'}); "
            "falling back to original file order."
        )
        return list(original)

    order = _topo_sort(paths, deps)
    if order is None:
        console.warn(
            "Dependency analysis: circular dependency detected; "
            "falling back to original file order."
        )
        return list(original)
    return order
