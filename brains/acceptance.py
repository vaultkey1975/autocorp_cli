#!/usr/bin/env python3
"""
Acceptance Gate  (AutoCorp CLI - brains)  [Quality Phase 8F]
============================================================

A deterministic, fully-offline gate that verifies a finished build against a
team profile's acceptance criteria (8E TEAM_PROFILE.acceptance) AFTER the tests
have run.

Design principles:
  * DETERMINISTIC + OFFLINE: each acceptance string is mapped to a NAMED check
    that is a pure function of an AcceptanceContext (files on disk + the already
    computed test result + the 8B review findings). No engine, no model, no
    network, no new subprocess.
  * ADVISORY BY DEFAULT: the gate only REPORTS. The orchestrator decides whether
    to enforce (strict mode). The gate never edits files and never blocks.
  * NON-BLOCKING: an unknown criterion is `unverified` (never blocks); a check
    that raises is caught and recorded as `unverified` - evaluate never raises.

Criteria -> check mapping is by normalized substring, so the human-readable 8E
acceptance strings resolve to checks without changing the TEAM_PROFILE schema.
"""

import ast
import os
import re
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Context + report
# --------------------------------------------------------------------------- #
@dataclass
class AcceptanceContext:
    """Everything a check needs - all already available at the end of a run."""
    workspace: str
    plan: dict = field(default_factory=dict)
    request: str = ""
    test_passed: bool = False
    review_findings: list = field(default_factory=list)


@dataclass
class AcceptanceReport:
    accepted: bool
    total: int
    passed: int
    failed: int
    unverified: int
    results: list = field(default_factory=list)   # list[dict]
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "unverified": self.unverified,
            "results": [dict(r) for r in self.results],
            "summary": self.summary,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _iter_python_files(workspace: str):
    for root, _dirs, files in os.walk(workspace or ""):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(root, name)


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _finding_category(f):
    if isinstance(f, dict):
        return f.get("category", "")
    return getattr(f, "category", "")


# --------------------------------------------------------------------------- #
# Deterministic checks: ctx -> (ok: bool, detail: str)
# --------------------------------------------------------------------------- #
def _check_tests_pass(ctx: AcceptanceContext):
    if ctx.test_passed:
        return True, "test command reported success"
    return False, "test command did not pass"


def _check_imports_parse_clean(ctx: AcceptanceContext):
    # Every .py must parse, and the review (when present) must report no
    # syntax_error / missing_import. Falls back to parse-only when review is off.
    for path in _iter_python_files(ctx.workspace):
        content = _read(path)
        if content is None:
            continue
        try:
            ast.parse(content)
        except SyntaxError as e:
            return False, f"{os.path.basename(path)} does not parse: {e.msg}"
    blocking = {"syntax_error", "missing_import"}
    for f in ctx.review_findings or []:
        if _finding_category(f) in blocking:
            return False, f"review reported {_finding_category(f)}"
    return True, "all .py files parse; no blocking review findings"


_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+\w+\s*\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL,
)


def _check_crud_placeholders_match(ctx: AcceptanceContext):
    crud = os.path.join(ctx.workspace or "", "crud.py")
    content = _read(crud)
    if content is None:
        return False, "crud.py not found"
    matches = _INSERT_RE.findall(content)
    if not matches:
        return False, "no INSERT statement found in crud.py"
    for columns, values in matches:
        n_cols = len([c for c in columns.split(",") if c.strip()])
        n_placeholders = values.count("?")
        if n_cols != n_placeholders:
            return False, f"INSERT has {n_cols} columns but {n_placeholders} placeholders"
    return True, "INSERT placeholder counts match column counts"


def _check_exports_present(ctx: AcceptanceContext):
    ws = ctx.workspace or ""
    required = ("export.py", "reports.py")
    missing = [name for name in required if not os.path.isfile(os.path.join(ws, name))]
    if missing:
        return False, f"missing export file(s): {', '.join(missing)}"
    return True, "export and report modules present"


def _check_entry_point_ok(ctx: AcceptanceContext):
    main = os.path.join(ctx.workspace or "", "main.py")
    content = _read(main)
    if content is None:
        return False, "main.py not found"
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return False, f"main.py does not parse: {e.msg}"
    has_main_func = any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
        for n in ast.walk(tree)
    )
    has_dunder = "__main__" in content
    if has_main_func or has_dunder:
        return True, "main.py exposes an entry point"
    return False, "main.py has no main()/__main__ entry point"


CHECKS = {
    "tests_pass": _check_tests_pass,
    "imports_parse_clean": _check_imports_parse_clean,
    "crud_placeholders_match": _check_crud_placeholders_match,
    "exports_present": _check_exports_present,
    "entry_point_ok": _check_entry_point_ok,
}

# Criterion-string (normalized substring) -> check id. First match wins.
_CRITERION_MAP = [
    ("tests passing", "tests_pass"),
    ("imports without error", "imports_parse_clean"),
    ("crud insert placeholders", "crud_placeholders_match"),
    ("exports are present", "exports_present"),
    ("entry point", "entry_point_ok"),
]


def _check_for(criterion: str):
    text = (criterion or "").lower()
    for needle, check_id in _CRITERION_MAP:
        if needle in text:
            return check_id
    return None


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
class AcceptanceGate:
    def evaluate(self, criteria, context: AcceptanceContext) -> AcceptanceReport:
        """Evaluate each criterion against its check. Unknown criteria and checks
        that raise are recorded as `unverified` (never block). `accepted` is True
        unless a matched check FAILED. Deterministic; results in input order."""
        results = []
        passed = failed = unverified = 0
        for criterion in (criteria or []):
            check_id = _check_for(criterion)
            if check_id is None:
                results.append({"criterion": criterion, "check": "",
                                "status": "unverified",
                                "detail": "no deterministic check for this criterion"})
                unverified += 1
                continue
            try:
                ok, detail = CHECKS[check_id](context)
            except Exception as e:  # noqa: BLE001 - a check must never break the gate
                results.append({"criterion": criterion, "check": check_id,
                                "status": "unverified",
                                "detail": f"check error: {e}"})
                unverified += 1
                continue
            status = "pass" if ok else "fail"
            results.append({"criterion": criterion, "check": check_id,
                            "status": status, "detail": detail})
            if ok:
                passed += 1
            else:
                failed += 1

        accepted = failed == 0
        total = len(results)
        summary = (f"acceptance {'met' if accepted else 'NOT met'}: "
                   f"{passed} passed, {failed} failed, {unverified} unverified "
                   f"of {total}")
        return AcceptanceReport(
            accepted=accepted, total=total, passed=passed, failed=failed,
            unverified=unverified, results=results, summary=summary,
        )
