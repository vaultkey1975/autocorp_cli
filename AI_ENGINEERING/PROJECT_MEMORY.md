# Project Memory

Institutional memory this repository would otherwise lose between AI
sessions. Every entry here is grounded in a specific, checkable event —
not general software-engineering advice.

---

## Lessons learned

**Documentation drifts faster than code, and nothing catches it
automatically.** `README.md` still describes the original four-brain,
`llama3.2`-only architecture from the first commit. It has not been updated
across 63 subsequent commits that added the entire engine abstraction,
repair pipeline, and Phase 1A–1Y infrastructure. Nobody's job was to keep
it current, so nobody did. `AI_ENGINEERING/` exists specifically to not
repeat this — see `DOCUMENTATION_POLICY.md`'s ownership rules.

**A version tag is not a proxy for "up to date."** Tags stop at `v0.10.5`,
49 commits before current `HEAD`. `config.py`'s `APP_VERSION` constant has
read `"0.1.0"` since the very first commit and was never bumped even once,
including at every one of the 14 subsequent tag points. If you need to know
what's actually in the repository, read the repository — not the version
string, not the tags.

**A module's own docstring claiming "STUB" or "RED phase" can go stale
just like anything else.** `brains/acceptance_brain.py`'s docstring said
"STUB: the behavioural methods below are intentionally unimplemented"
long after every one of its methods was fully implemented and wired into
`core/orchestrator.py` as `self.acceptance_brain`. This was only caught
during an unrelated hardening pass that happened to grep for
`NotImplementedError` across the repository. Docstrings describing
implementation status need the same scrutiny as any other claim.

**A real audit will find real bugs that no amount of code review alone
would surface, and they are worth writing down precisely.** The Phase 1G
safety audit (`claude_phase_1g_audit.txt`) found, by actually calling the
code with adversarial inputs rather than reading it and reasoning about it:
`--provider claude` throws a `TypeError` at construction (a constructor
signature mismatch that static reading alone did not surface until an
actual call was made); `--provider deepseek` without an API key resolves
the *API* model name into the field used by the *local* transport, a
conflation between two similarly-named-but-distinct fields; and one test
silently makes a real network call to a paid API instead of testing the
offline path it claims to test, purely because of an ambient environment
variable. None of these were visible from reading the code casually.
**Prefer running adversarial inputs through real code over reasoning about
what it "should" do.**

**Real end-to-end runs surface things unit tests cannot, and they are
worth the real time they cost.** Four separate, real, non-mocked runs
against CloneCast (two under Phase 1Y, two under Quick Podcast
verification) all independently hit the identical real failure —
Chatterbox-synthesized audio consistently exceeds CloneCast's own peak-
level QC threshold. No unit test in this repository could have found this,
because it requires a real GPU, a real model, and real generated audio.
The pattern that surfaced it — actually running the thing against a real,
disposable copy of the target, not a mock — is the whole reason the
disposable-workflow infrastructure (Phase 1M–1S onward) exists. **Do not
substitute a plausible-looking mock for a real disposable run when the
task explicitly needs to prove real behavior.**

**This documentation system itself fell into the exact trap it warns
against, within days of being written.** `NEXT_STEPS.md`/`PHASES.md`/
`CURRENT_PHASE.md` carried forward all five findings of
`claude_phase_1g_audit.txt` (an untracked report against an old commit,
`1339aaf`) as currently-open bugs, without re-running the audit's own
adversarial checks against current `HEAD`. Re-verifying on 2026-07-29 found
four of the five had already been fixed by `ea71d54`, a commit that
predates the audit's own report timestamp being trusted at face value. The
fix was mechanical once caught (re-run the exact checks the audit
described, correct the three documents), but the lesson is the one
`AI_ENGINEERING_CONSTITUTION.md` §3 already states in the abstract: a
report is evidence to check against the repository, not a substitute for
checking it — and that applies with full force to this documentation
system's own prior entries, not just to external reports like the audit
file. **Before restating any "known bug"/"known gap" from an existing
`AI_ENGINEERING/` document, re-run the specific check that originally found
it.** Do not assume a document written last week is still accurate just
because it was carefully evidence-based when written.

**A second, independent instance of the same lesson: an investigation report's claim can be wrong even when it was carefully evidence-based and written minutes earlier — always re-verify empirically before acting on it, including your own prior session's findings.** The Reliability Engine investigation (this same day, 2026-07-29) claimed a missing `ruff`/`flake8`/`mypy` binary would be "treated as a blocking static-gate issue" for every edit. Re-verifying by actually monkeypatching tool detection to simulate the missing-tool case and calling the real code path (`StaticGate.run_delta`, the only method any caller actually invokes) showed it does NOT block — the missing-tool marker appears identically in both the before and after snapshots `run_delta` diffs, so it's never flagged as "new." The claim was true of `StaticGate.run()` (an unused alternate method) but not of the code path actually exercised. Nothing was "fixed" for this one — the correct action, per the task that requested this re-validation, was to document why the original finding didn't hold up. **Treat "an investigation already covered this" as a reason to re-verify quickly, not a reason to skip verification** — see also the Phase 1G audit entry above, a separate concrete instance of the identical pattern.

**A resource leak can hide until a specific test flag surfaces it.** New
unit tests for the Quick Podcast refactor's `Progress`/`_TeeLog` classes
passed individually, then failed only when run as part of the full suite
under `-W error` — unclosed file handles, held across dozens of tests in
one long-lived pytest process, only became a `ResourceWarning`-turned-error
at garbage-collection time, which happens unpredictably relative to any
one test. **Always run the full suite, with the strictest flags the
project uses (`-W error` is this repository's convention — see
`pytest`/CI usage across recent sessions), before declaring new tests
clean.**

**Prior AI sessions can leave uncommitted work in the tree, and it is not
yours to discard.** At more than one point in this repository's recent
history, a session found the working tree already dirty with unrelated,
substantial, in-progress work (once: a separate CloneCast-side
YouTube-publication feature, confirmed by file mtimes to predate the
current session by hours; once: the Reliability Engine, of unknown origin
relative to any given session). In both cases, the correct response was to
identify it, avoid touching it, and note its existence — not to delete it,
not to assume it was garbage, and not to silently fold it into an unrelated
commit.

## Engineering decisions (and why)

**Reuse the scanner's file-walk and marker-counting logic rather than
duplicate it.** `brains/scanner.py` exposes `iter_python_files`,
`is_test_file`, and `count_markers` as public functions specifically so
`brains/analyzer.py`, `brains/project_planner.py`, and
`brains/repair_proposal.py` don't reimplement the same ignore-directory
walk and TODO/FIXME/pass/NotImplementedError counting rules four times.
When one of those rules needs to change, it changes in one place.

**Exclude `workspace/` (and `data/`) from architecture-level analysis, but
not from raw scan totals.** `brains/analyzer.py` deliberately uses a wider
ignore set than `brains/scanner.py` for anything that forms a judgment
about "what kind of project is this" — because `workspace/` holds hundreds
of AI-generated demo applications (at one point measured at 326 of the
repository's 441 total Python files) that would otherwise swamp the
analysis and produce answers like "this is a PySide6 desktop application"
for a project that is, in fact, a CLI tool. Raw counts (Quality Indicators)
still cover the whole repository, reusing the scanner verbatim, so `scan`
and `analyze` agree on the numbers they share.

**Use AST parsing, not text/regex matching, wherever a false positive from
a string literal or comment would be embarrassing.** `scanner.py` counts
`pass` statements via `ast.Pass`, not a text search, specifically so a
variable named `password` doesn't count. This exact class of bug recurred
later and was caught the same way: an early version of a test-framework
detector matched the literal text `"unittest.TestCase"` even when it only
appeared inside a Python string being written as *test fixture data* in
another test file — because that file also happened to match the
`test_*.py` naming convention the detector was scanning. Switching from
text matching to `ast.parse` + walking real `Import`/`ImportFrom`/`ClassDef`
nodes fixed it, because fixture strings aren't real syntax nodes. **Any
new "does this file do X" detector should default to AST inspection, not
substring search, unless there's a specific reason substring search is
actually correct** (TODO/FIXME counting is the documented exception — those
are meant to be literal textual markers, matching how every common
grep-based TODO scanner works, and is intentional, not an oversight).

**Trust an entry point's own imports over what appears anywhere else in the
tree, when classifying "what kind of project is this."** An early version
of the project-type detector classified this very repository as a
"Desktop application" because a template file the repository *generates
for users* (`brains/templates/pyside6_desktop.py`) imports PySide6. The
fix was to give the entry point's own direct imports priority over a
tree-wide scan, falling back to the tree-wide scan only when no entry point
exists at all. A generator that emits GUI code is not itself a GUI
application.

**When a constructor argument's presence and its emptiness must mean
different things, `argument or fallback` is the wrong pattern.**
`brains/deepseek_engine.py`'s `self.api_key = api_key or
os.environ.get("DEEPSEEK_API_KEY") or ""` cannot distinguish "caller didn't
pass anything" from "caller explicitly passed an empty string to force
local mode," because both are falsy. This was discovered while trying to
write a test that passed `api_key=""` expecting it to override an ambient
environment variable — it silently didn't. The correct fix in that
specific case was to isolate the test's environment (`monkeypatch.delenv`)
rather than change the production `or`-chain, since changing production
behavior was out of scope for that task — but the underlying pattern is a
real trap worth remembering: **`or`-chains cannot express "explicitly
falsy on purpose."** Use a sentinel (e.g. `None` vs. `""`) if that
distinction matters.

**Disposable-target subprocess workers should be real modules, not
embedded `python -c` strings, once they exceed a few dozen lines.** The
original Quick Podcast implementation was a single ~400-line string passed
to `python -c`, with no progress output until the entire multi-minute
process finished, no way to attach a debugger, and (discovered during the
refactor) a code path with no exception handler at all that would have
crashed with a raw traceback instead of a clean, classified failure. The
fix — a real, `-m`-invokable module — required understanding that the
target's venv lacks AutoCorp's own third-party dependencies, so the worker
module must avoid importing anything beyond the Python standard library at
its own top level (its target-specific imports are all deferred, inside
the function that actually runs, so the module is still importable —
and thus unit-testable — from AutoCorp's own venv even though it's
designed to run in the target's).

**To commit one logical change out of a working tree where multiple
unrelated efforts share the same file's diff, reconstruct rather than
`git add -p`.** `autocorp.py`'s working-tree diff had the Quick Podcast CLI
wiring and the Phase 1X/1Y `cmd_workflow_test`/`cmd_publish_test` work
physically interleaved in the same region of the file (both edit code near
each other), producing one large replace-style diff hunk that `git add -p`
could not cleanly split by hunk. The reliable approach: take `git show
HEAD:<file>`, manually apply only the target change on top of it (verified
by diffing the result against both `HEAD` and the full working tree to
confirm the two diffs partition cleanly with no overlap), `py_compile` it,
copy it over the real file, commit, then restore the original full
working-tree file from a backup taken before starting. Keep the backup
until the restore is verified — it's easy to forget the restore step after
committing and leave the working tree short of the other, still-uncommitted
work.

## Patterns to reuse

- Deterministic, non-model-driven action IDs via SHA-256 of a stable string
  (`brains/project_planner.py`'s `sha256("priority:category:title")`) —
  reproducible across runs, no UUID/timestamp/counter state to manage.
- Independent post-hoc verification of anything a target service claims —
  don't trust a database's own recorded SHA-256/duration/size; recompute
  it from the actual file and compare (`_verify_artifact` in
  `brains/workflow_test.py`).
- Read-only, mode=ro SQLite connections for anything that only needs to
  observe a target's state (`_db_one`/`_db_all` in `workflow_test.py`;
  `connect_readonly_database` in CloneCast's own `db.py`) — guarantees no
  write is possible even if the calling code has a bug.
- A background thread polling a read-only database connection to report
  progress on a long-running synchronous call, when the call itself cannot
  be modified to report progress directly (the voice-rendering per-turn
  progress poller in `quick_podcast_runner.py` — safe specifically because
  the target database is in WAL mode, which supports concurrent readers).
- Verifying a safety property by checking a live database schema constraint
  (`destination_type CHECK IN ('local')`) rather than only by reading
  application code, since a schema-level constraint holds even if the
  application code above it has a bug.

## Patterns never to repeat

- **Reusing a phase number/label for two unrelated efforts.** "Phase 1" has
  meant both the SQLite Generation template feature (tagged, Era 1) and
  the repository-intelligence/CloneCast-validation infrastructure (untagged,
  Era 3) at different points in this repository's history. Anyone searching
  for "Phase 1" without era context will get confused. Use distinct
  identifiers for genuinely unrelated efforts.
- **A `try` block with no `except` clause, "because nothing here should
  fail."** Found in the original Quick Podcast script's final output-
  copying step — the one place in that ~400-line script without the
  `fail(phase, reason)` pattern used everywhere else, and the one place
  that would have crashed with a raw traceback instead of a clean,
  classified error. If every other code path in a module has an exception
  handler, the one that doesn't is a bug, not an exception to the rule.
- **Letting a `.gitignore` pattern silently hide real evidence.**
  `phase_*_report.txt` is gitignored, so `phase_1x_report.txt` and
  `phase_1y_report.txt` never appear in `git status` even though they are
  real verification artifacts from real runs. This is not wrong on its own,
  but it means "check `git status` for evidence" is not sufficient by
  itself — some evidence only shows up via `ls` or `git check-ignore -v`.
  Documentation referencing report files should say plainly whether they
  are tracked, gitignored, or fully untracked.
- **Assuming a stale git branch reflects current uncommitted work just
  because the names sound related.** `reliability/subtask-1` and
  `reliability/subtask-2` share a name with the uncommitted
  `reliability_engine/` directory but are unrelated — both branches point
  at the same several-days-old commit with zero unique history. Verify
  with `git log <branch> --oneline` and compare dates before assuming a
  branch explains or contains a piece of working-tree state.

## Database decisions

- AutoCorp's own memory store (`memory/store.py`) is deliberately simple:
  `LIKE`-based keyword recall, no embeddings, no vector index, stated as an
  explicit design choice in its own module docstring ("fully local, no
  embeddings, no extra dependencies. Good enough... easy to upgrade
  later"). The uncommitted Chroma vector store (`data/chroma/`) and
  `chromadb` dependency appear to be exactly that upgrade being attempted,
  but as of `HEAD` this is uncommitted and unintegrated — do not describe
  AutoCorp's memory system as vector-backed until that lands and is wired
  in.
- Every disposable-workflow database interaction copies the target's real
  production database file first (`shutil.copy2`), rather than running
  fresh migrations against an empty schema — this was a deliberate choice
  so that "approved voice profiles" and other reference data that only
  exists in production (and would be expensive/impossible to fabricate
  safely) are available to the disposable run without ever risking a write
  back to the original file.

## Production rules

- Every tool that could write to a target repository defaults to refusing
  without an explicit flag (`--approve`, `--disposable`, `--test`), and
  every one of those flags has been observed, in this repository's own
  verification history, to be checked before any write occurs — not merely
  documented as a convention.
- Every report and log this repository's Phase 1A–1Y/Quick Podcast tooling
  produces is built from real values captured during the run it describes
  — never a static template with numbers filled in after the fact. This is
  stated as a hard requirement in multiple module docstrings
  (`scanner.py`, `analyzer.py`) and has held in every verification
  performed in this repository's history to date.

## Known pitfalls

- An ambient `DEEPSEEK_API_KEY` (or similarly-named credential) in the
  shell environment will cause at least one existing test
  (`test_no_silent_fallback`) to make a real network call instead of
  testing the intended offline path — check for this before trusting a
  "clean" test run in an unfamiliar environment.
- `git for-each-ref`'s `%(objectname)` returns an **annotated tag object's
  own hash**, not the commit it points to — use `%(*objectname)` (with the
  `*`) to dereference to the actual commit, or use `git log -1 <tag>`,
  which dereferences automatically. This tripped up evidence-gathering for
  this very documentation set before being caught.
- A leftover `/tmp/acqp-*` or `/tmp/acwf-*` disposable directory does not
  automatically mean the current session's cleanup logic is broken — this
  repository's disposable-workspace tooling has, at least once, left
  behind a directory from an *earlier, unrelated, interrupted* run (traced
  by comparing conversation-turn counts inside it against what the current
  session's own short-duration runs would have produced). Investigate
  before concluding a cleanup regression exists.
