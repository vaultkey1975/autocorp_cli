#!/usr/bin/env python3
"""
Reviewer Brain  (AutoCorp CLI - brains)  [Quality Review Phase 8B]
=================================================================

A deterministic, fully-offline static reviewer that inspects the generated .py
files of a build BEFORE the tests run, and produces structured findings plus a
0-100 quality score.

Design principles:
  * DETERMINISTIC: pure static analysis over Python's `ast` (the same approach
    the dependency_analyzer uses). The same code always yields the same findings
    in the same order. No model, no randomness.
  * OFFLINE + MODEL-FREE: the default review path never contacts Ollama or any
    engine. `ReviewerBrain` accepts an optional `engine` purely as a seam for a
    FUTURE model-review pass (any registry engine, incl. DeepSeek later) - it is
    stored but never called here.
  * NON-DESTRUCTIVE: the reviewer only READS files. Repairing code stays the Fix
    Loop's job.
  * NEVER CRASHES: an unparseable file yields a single `syntax_error` finding and
    the review continues with the other files.

Detectors implemented in this phase:
  * missing_import  - a name used (Load) that is never imported, defined, an
                      argument, or a builtin. Suppressed when the file does a
                      `from x import *` (we can't know what that pulls in).
  * large_function  - a function whose line span exceeds the configured limit.
  * syntax_error    - the file does not parse.

(duplicate_code and model review are intentionally NOT implemented here.)
"""

import ast
import builtins
import datetime
import os
from dataclasses import dataclass, field

from config import REVIEW_LARGE_FUNCTION_LINES, REVIEW_SCORE_WEIGHTS

# Names that are always available; never flag these as missing imports.
_BUILTINS = set(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__all__", "__package__",
    "__spec__", "__loader__", "__builtins__",
}

# category -> severity (drives the score weighting).
_SEVERITY = {
    "syntax_error": "error",
    "missing_import": "error",
    "large_function": "warning",
}


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
@dataclass
class Finding:
    """One issue found in one file."""
    file: str
    line: int
    severity: str
    category: str
    symbol: str = ""
    message: str = ""
    source: str = "static"  # reserved: "model" for a future LLM review pass

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "category": self.category,
            "symbol": self.symbol,
            "message": self.message,
            "source": self.source,
        }

    def _sort_key(self):
        return (self.file, self.line, self.category, self.symbol, self.message)


@dataclass
class ReviewReport:
    """The result of reviewing a workspace."""
    project_name: str
    workspace: str
    ts: str
    files_reviewed: int
    score: int
    findings: list = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "workspace": self.workspace,
            "ts": self.ts,
            "files_reviewed": self.files_reviewed,
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
        }


# --------------------------------------------------------------------------- #
# Reviewer
# --------------------------------------------------------------------------- #
class ReviewerBrain:
    def __init__(self, engine=None, max_function_lines: int = None):
        # `engine` is a seam for a future model-review pass; it is never used by
        # the deterministic static review and is never called here.
        self.engine = engine
        self.max_function_lines = (
            REVIEW_LARGE_FUNCTION_LINES if max_function_lines is None
            else max_function_lines
        )

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def review(self, workspace: str, plan: dict = None) -> ReviewReport:
        """Statically review every non-empty .py file under `workspace`. Returns
        a ReviewReport. Reads only; never writes; never calls a model."""
        plan = plan or {}
        findings = []
        files_reviewed = 0

        for rel, content in self._iter_python_files(workspace):
            files_reviewed += 1
            findings.extend(self._review_source(rel, content))

        findings.sort(key=lambda f: f._sort_key())
        score = self._score(findings)
        summary = self._summarize(findings, score)

        return ReviewReport(
            project_name=plan.get("project_name", ""),
            workspace=workspace,
            ts=datetime.datetime.now().isoformat(),
            files_reviewed=files_reviewed,
            score=score,
            findings=findings,
            summary=summary,
        )

    # ------------------------------------------------------------------ #
    # File discovery
    # ------------------------------------------------------------------ #
    @staticmethod
    def _iter_python_files(workspace: str):
        """Yield (relative_path, content) for non-empty .py files, in a stable
        (sorted) order. Non-.py and empty files are skipped."""
        collected = []
        for root, _dirs, files in os.walk(workspace):
            for name in files:
                if not name.endswith(".py"):
                    continue
                full = os.path.join(root, name)
                try:
                    with open(full, encoding="utf-8") as fh:
                        content = fh.read()
                except OSError:
                    continue
                if not content.strip():
                    continue
                rel = os.path.relpath(full, workspace)
                collected.append((rel.replace(os.sep, "/"), content))
        collected.sort(key=lambda pc: pc[0])
        return collected

    # ------------------------------------------------------------------ #
    # Per-file detectors
    # ------------------------------------------------------------------ #
    def _review_source(self, rel: str, content: str) -> list:
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return [self._finding(rel, e.lineno or 0, "syntax_error", "",
                                  f"File does not parse: {e.msg}.")]

        findings = []
        findings.extend(self._missing_imports(rel, tree))
        findings.extend(self._large_functions(rel, tree))
        return findings

    def _missing_imports(self, rel: str, tree: ast.AST) -> list:
        # A `from x import *` may pull in anything; we can't reason about names,
        # so suppress this detector entirely for the file.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(a.name == "*" for a in node.names):
                    return []

        defined = set()
        used_lines = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    defined.add(node.id)
                elif isinstance(node.ctx, ast.Load):
                    used_lines.setdefault(node.id, node.lineno)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)
                defined.update(self._arg_names(node.args))
            elif isinstance(node, ast.ClassDef):
                defined.add(node.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    defined.add(alias.asname or alias.name)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    defined.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                defined.update(node.names)

        missing = used_lines.keys() - defined - _BUILTINS
        out = []
        for name in sorted(missing):
            out.append(self._finding(
                rel, used_lines[name], "missing_import", name,
                f"Name '{name}' is used but never imported or defined.",
            ))
        return out

    def _large_functions(self, rel: str, tree: ast.AST) -> list:
        out = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", None) or node.lineno
                span = end - node.lineno + 1
                if span > self.max_function_lines:
                    out.append(self._finding(
                        rel, node.lineno, "large_function", node.name,
                        f"Function '{node.name}' is {span} lines "
                        f"(> {self.max_function_lines}); consider splitting it.",
                    ))
        return out

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _arg_names(args: ast.arguments) -> set:
        names = set()
        for a in list(getattr(args, "posonlyargs", [])) + list(args.args) + list(args.kwonlyargs):
            names.add(a.arg)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
        return names

    @staticmethod
    def _finding(file: str, line: int, category: str, symbol: str, message: str) -> Finding:
        return Finding(
            file=file, line=line or 0, severity=_SEVERITY.get(category, "info"),
            category=category, symbol=symbol, message=message, source="static",
        )

    @staticmethod
    def _score(findings: list) -> int:
        penalty = sum(REVIEW_SCORE_WEIGHTS.get(f.severity, 0) for f in findings)
        return max(0, min(100, 100 - penalty))

    @staticmethod
    def _summarize(findings: list, score: int) -> str:
        if not findings:
            return f"0 findings · score {score}/100"
        by_sev = {}
        for f in findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        parts = ", ".join(f"{by_sev[s]} {s}" for s in ("error", "warning", "info") if s in by_sev)
        return f"{len(findings)} findings ({parts}) · score {score}/100"
