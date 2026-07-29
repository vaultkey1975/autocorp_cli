# Engineering Rules

Concrete, enforceable standards for this repository. Where a rule exists
because of a specific incident, the incident is cited — these are not
generic best practices, they are this repository's own accumulated cost of
getting things wrong once already.

---

## No placeholders

Production code (`brains/`, `core/`, `memory/`, `safety/`, `autocorp.py`,
`config.py`) must not contain placeholder implementations presented as
real ones. Where a genuinely intentional stub exists (an abstract method
meant to be overridden, e.g. `brains/base_engine.py`'s `BaseEngine.generate`
and `brains/repair_content_generator.py`'s `RepairContentProvider.generate`,
both of which `raise NotImplementedError` on purpose as an interface
contract), it must be clearly an interface definition, not a claimed
working feature. `brains/acceptance_brain.py` is the cautionary example:
its docstring called it a "STUB" long after every method was fully
implemented and in production use — do not let a docstring claim
"unimplemented" after implementation lands, and do not let a real stub's
docstring claim it's finished either.

## No TODOs in production

No `TODO` comments in `brains/`, `core/`, `memory/`, `safety/`,
`autocorp.py`, or `config.py`. As of this document's writing, a repository
scan (`grep -rn "TODO\|FIXME"` across those paths) finds zero genuine
leftover `TODO`/`FIXME` markers — every textual match is one of three
legitimate, non-leftover cases:
1. `brains/scanner.py`'s own docstring/labels describing its *counting
   feature* for these markers.
2. `brains/live_readiness.py` and `brains/project_planner.py` containing
   the literal substrings `TODO`/`FIXME` as part of their own production
   output — e.g. a readiness finding titled `"FIXME markers in production
   code"`, or a planner recommendation string like `"Address FIXME
   markers"`. These are data these tools *report about other code*, not
   unfinished work of their own.
3. Test fixture data (`tests/`) exercising the above.

Keep it that way: if you need to track future work, use `NEXT_STEPS.md`,
not a code comment. If you add a new module, re-run the grep above — a
match that isn't one of the three cases above is a real leftover marker.

## No FIXMEs in production

Same rule and same current state as TODOs, above.

## No mock implementations presented as real

Do not write a function that returns a fabricated success value instead of
doing the work, and do not present a mocked-out code path as if it were
exercising real behavior. Where mocking is legitimate (unit-testing pure
logic without a live GPU/model/network — see `tests/`'s extensive use of
fake `inner` providers for `LongformConversationProvider` in
`tests/test_quick_podcast_runner.py`, for example), it must be clearly a
unit test of deterministic logic, not a substitute for the real
end-to-end verification a task actually requires. This repository's own
history shows the cost of skipping the real verification: unit tests alone
would never have surfaced the real, reproducible CloneCast audio-clipping
issue found across four separate real runs.

## No fake repository scans

Every scanning/analysis tool in this repository (`scanner.py`,
`analyzer.py`, `project_planner.py`, `live_readiness.py`) computes every
number it reports from the actual target at run time. This is stated
explicitly in multiple module docstrings and enforced by their own test
suites (e.g. `tests/test_scanner.py` verifies counts against a known
temporary-directory fixture, not a hardcoded expectation). Do not add a
"quick" scan that estimates or hardcodes a value instead of computing it.

## No fake verification

A verification step that cannot actually run (missing dependency,
unreachable service, blocked external system) must report that plainly —
not report a synthetic pass. `brains/workflow_test.py` and
`brains/quick_podcast_runner.py` both classify failures explicitly
(`CLONECAST_WORKFLOW_PRECONDITION`, `MISSING_EXTERNAL_MODEL_OR_DEPENDENCY`,
`CLONECAST_APPLICATION_DEFECT`, etc.) specifically so a blocked
verification is never confused with a passed one.

## No fake success

Do not report a task, phase, or run as successful because most of it
worked. Four real runs in this repository's history reached 8 of 12 (or
equivalent) phases successfully and then hit a real, external blocking
issue — every one of them was reported as a real, partial result with the
specific blocking phase and reason named, not rounded up to "success" or
down to "failure" in a way that hides what actually happened.

## Repository safety

- Never modify a target system outside this repository except through its
  own public services/routes/CLI, on a disposable copy of its data, with
  independent before/after verification (SHA-256 of its production
  database, `git status` of its working tree) that you perform yourself.
- Never delete, rename, or move a file without first checking whether it
  represents someone else's in-progress work (`git log`, file mtimes) —
  this repository has concrete precedent for both a stale-but-real
  unrelated feature branch and an uncommitted-but-real unrelated subsystem
  sitting in the tree at the same time as active work.
- Never create a commit or push unless explicitly instructed.
- Never run a destructive git operation without explicit instruction.

## Coding standards

- Reuse existing helpers rather than duplicate logic — grep for an
  existing implementation before writing a new one. This repository has an
  explicit, working precedent for exactly this
  (`brains/scanner.py` exposing `iter_python_files`/`is_test_file`/
  `count_markers` specifically so other modules didn't reimplement them).
- Prefer AST-based inspection over text/regex matching for any "does this
  code do X" question, unless the marker being searched for is
  intentionally textual (TODO/FIXME counting is the documented exception).
  Text matching against source that might contain the same text inside a
  string literal or test fixture is a proven, recurring bug class in this
  repository — see `PROJECT_MEMORY.md`.
- When classifying "what kind of system is this" from evidence, trust the
  system's own entry point over anything found elsewhere in the tree
  first, and only fall back to a wider search when no entry point exists.
- Do not use an `argument or fallback` pattern where the argument being
  explicitly falsy (e.g. an intentional empty string) needs to mean
  something different from the argument being absent. Use an explicit
  sentinel instead.
- Any subprocess worker that must import a target system's own package
  (as opposed to calling it over HTTP) should be a real, `-m`-invokable
  module, not an embedded string passed to `python -c`, once it exceeds
  a trivial size — see the Quick Podcast Observability Refactor in
  `PHASES.md` for the concrete before/after and why it mattered
  (no progress visibility, no debugger access, and a code path with no
  exception handler that would have crashed with a raw traceback).

## Testing standards

- Every new module gets real unit tests for its deterministic/pure logic,
  following this repository's established pattern (93+ tracked test files,
  one test file per module in almost every case).
- Run the FULL test suite, not just the tests for the file you changed,
  before reporting anything as done. This repository has a concrete,
  recent example of a regression (unclosed file handles) that only
  surfaced under the full suite with `-W error`, not when the new test
  file was run alone.
- Where a task requires proving real end-to-end behavior against a real
  external dependency, perform the real run — inside a disposable,
  isolated environment following this repository's established pattern —
  rather than substituting a mock and calling the task verified.
- Report exact pass/fail/xfail counts and exit codes, not summaries like
  "tests pass."

## Documentation standards

See `DOCUMENTATION_POLICY.md` for the full per-document specification. In
brief: every factual claim in any engineering document must cite or be
traceable to repository evidence; uncommitted or unintegrated work must
never be described as shipped or complete; and a document you make stale
by your own change is your responsibility to fix in the same session.

## Definition of Done

Work is done only when all of the following are true, per
`AI_ENGINEERING_CONSTITUTION.md` §5 and `PHASE_COMPLETION_POLICY.md`:

1. The change is committed (or the task explicitly forbids committing, in
   which case "done" means "ready for owner review," not "complete").
2. The full test suite passes, with exact numbers reported from a run you
   performed yourself in the current session.
3. Every new capability has a test that would fail if the capability broke
   — or an explicit, honest statement of why it couldn't be tested and
   what real-run verification was performed instead.
4. Any affected document under `AI_ENGINEERING/` has been updated.
5. `git status` has been checked and its state accurately reported.

"Done" and "uncommitted" are not mutually exclusive descriptions of the
same work — both facts, if both are true, must be stated together, not
one substituted for the other.
