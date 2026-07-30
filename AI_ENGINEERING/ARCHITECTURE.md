# Architecture

This document describes the system as it exists in the repository today —
committed and uncommitted state both, clearly distinguished — not as it was
originally designed or as a future phase might redesign it. See
`PHASES.md` for how each piece came to exist.

---

## Directory structure (tracked, via `git ls-files`)

```
autocorp.py           CLI entry point (argparse) - 16 subcommands in the
                       working tree, 16 committed before this audit
config.py              Single source of truth for model/endpoint/timeouts/
                       paths; APP_VERSION frozen at "0.1.0" since the first
                       commit (see PROJECT_MEMORY.md)
pytest.ini             testpaths=tests; explicitly excludes workspace/,
                       .venv, data, .git, egg-info, __pycache__
core/                  console.py, llm.py (Ollama client), orchestrator.py
brains/                38 tracked .py files (via `git ls-files "brains/*.py"`)
                       - see "brains/ inventory" below
memory/                store.py - SQLite build/lesson memory
safety/                executor.py, gate.py, watchdog_gate.py
scripts/               repository verification helpers
reliability_engine/     17 tracked modules - surgical edit/repair
                       orchestration with worktree isolation
tests/                 96 tracked test_*.py files before this audit
```

Present in the working tree but **not tracked by git** (verify with `git
status` before trusting any of this as shipped):

```
data/                   runtime SQLite (data/autocorp.db) + a Chroma vector
                       store (data/chroma/)
```

## CLI architecture

`autocorp.py` builds one `argparse` parser with subcommands, each mapped to
a `cmd_*` handler function, following a consistent pattern: resolve the
target repository (via `_resolve_repo`, which delegates to
`brains/workspace.py`'s `resolve_workspace` for any command that accepts
`--repo`), call into the relevant `brains/` module for all actual logic,
then format and print the result — the CLI layer itself does not contain
business logic. This pattern is stated explicitly in multiple modules'
docstrings ("keep the CLI thin") and held consistently across the Phase
1A–1Y command set.

Subcommands committed before this audit (`git show 27b138d:autocorp.py`):
`build`, `plan`, `test`, `explain`, `memory`, `scan`, `analyze`,
`plan-project`, `repair`, `propose-repair`, `live-readiness`, `live-test`,
`workflow-test`, `publish-test`, `quick-podcast`, `chat`.

Gate selection (`_make_gate`) chooses between `AllowAllGate` (`--auto`),
`WatchdogGate` (`--watchdog`), and the default interactive `ConfirmGate` —
this is the seam through which an external "Agent Watchdog" tool could
approve or block file writes/commands; `safety/watchdog_gate.py` loads it
optionally at runtime and falls back to `ConfirmGate` if unavailable.

`autocorp chat` is a committed repository-aware conversational interface.
Its business logic lives in `brains/chat.py`; the CLI handler only resolves
the repository, creates an `AutoCorpChatSession`, prints one-shot
responses, or runs an interactive loop. The chat routes natural-language
requests to existing repository capabilities (`scanner`, `analyzer`,
`project_planner`, git inspection, workflow-test/publish-test command
guidance, repair-plan guidance, and `AI_ENGINEERING/` documentation reads)
rather than calling a generic model. The production-hardening working tree
normalizes Ctrl+C handling to exit code 130 in both interactive chat and
top-level command dispatch.

## `brains/` inventory (tracked)

Grouped by the era that introduced them (see `PHASES.md` for detail):

- **Original four brains:** `planner.py`, `builder.py`, `tester.py` (the
  fourth, memory, lives in `memory/store.py`, not `brains/`).
- **Engine abstraction:** `base_engine.py`, `local_engine.py`,
  `claude_engine.py`, `deepseek_engine.py`, `engine_registry.py`,
  `model_router.py`.
- **Repair / self-healing pipeline:** `acceptance.py`,
  `acceptance_brain.py`, `acceptance_repair_adapter.py`,
  `repair_content_generator.py`, `repair_executor.py`,
  `gated_repair_fixer.py`, `fixer_executor.py`, `retry_controller.py`,
  `dependency_analyzer.py`, `reviewer.py`, `self_healing_orchestrator.py`,
  `project_plan.py`.
- **Phase 1A–1Y repository-intelligence / CloneCast-validation
  infrastructure:** `scanner.py`, `analyzer.py`, `project_planner.py`,
  `workspace.py`, `providers.py`, `repair_proposal.py`, `live_test.py`,
  `live_readiness.py`, `workflow_test.py`, `publish_test.py`, `chat.py`.
- **Quick Podcast:** `quick_podcast.py` (thin orchestrator),
  `quick_podcast_runner.py` (the actual CloneCast-service-calling worker —
  see "Workflow engine" below for why this one runs as a separate process).
- **Code-generation templates (for apps AutoCorp builds, not AutoCorp
  itself):** `templates/sqlite_desktop.py`, `templates/pyside6_desktop.py`,
  `templates/sqlite_support.py`.

## Memory system

`memory/store.py` is a small SQLite knowledge base (`data/autocorp.db` by
default, path from `config.DB_PATH`). Committed schema: `builds` (every
project planned/built and its outcome), `lessons` (reusable
successes/mistakes/fixes), `reviews` (Reviewer Brain reports), `routes`
(Model Router decisions). Recall is a simple `LIKE`-based keyword match —
no embeddings, explicitly by design (module docstring: "fully local, no
embeddings, no extra dependencies").

**Reliability Engine addition:** three further tables — `subtasks`,
`attempts`, `known_issues` — support durable Reliability Engine state.
They are real SQLite `CREATE TABLE IF NOT EXISTS` statements in
`memory/store.py`'s `init_db()` and are used by
`reliability_engine/state_store.py`.

Chroma (`data/chroma/`) appears in the working tree as a second, vector-
based runtime store for Reliability Engine RAG. `chromadb` is a tracked
dependency in `requirements.txt`; `data/chroma/` itself remains runtime
data and is ignored.

## Repair engine

Two related but distinct repair concepts exist in this repository; do not
conflate them:

1. **Self-healing repair loop** (Era 2): triggered by `--self-heal` on
   `build`, driven by `TesterBrain`/`GatedRepairFixer`/`FixerExecutor`/
   `RetryController`, generating real fix content via the engine
   abstraction and re-testing until success or exhaustion.
2. **Safe Repair Executor** (Phase 1D) + **AI Repair Proposal Engine**
   (Phase 1G): a deliberately narrow, deterministic executable repair (one
   category: missing `requirements.txt`) plus a separate, review-only,
   never-applies-anything proposal generator that produces a structured
   JSON proposal (with strict validation against path traversal, shell/git
   command injection, and SHA-256 evidence checks) for a human to review.

The second category never writes to a target repository without explicit
`--approve`, and the proposal generator never writes to the target
repository at all — only to an explicit `--output` path.

## Workflow engine

`brains/workflow_test.py` (Phase 1M–1S, extended by Phase 1X/1Y) drives a
real, disposable, end-to-end validation of an external
target application (CloneCast): it copies the target's production database
into a temporary directory, points a battery of `CLONECAST_*` environment
variables at that disposable copy, launches the target's own `uvicorn`
server as a subprocess against it, and drives the entire workflow through
real HTTP requests constructed from the target's own live OpenAPI schema —
never direct SQL, never a mock. Two operations without an HTTP route
(research ingestion and QC request creation) are invoked via a short-lived
`python -c` subprocess calling the target's own service classes directly,
in the target's own venv — this is the one place raw code-as-string is
still used, and only for two single-call operations, not the entire
workflow (contrast with Quick Podcast, below).

`brains/quick_podcast.py` / `brains/quick_podcast_runner.py` (the "Quick
Podcast Observability Refactor," see `PHASES.md`) is architecturally
similar but runs as a genuinely separate module rather than an inline
string: because the worker must import the target's own `clonecast.*`
Python package, it is invoked as `<target-venv-python> -m
brains.quick_podcast_runner <args>`, with `PYTHONPATH` set to include both
the target's `src/` directory and this repository's own root (so
`brains.quick_podcast_runner` resolves as a package member). The module
deliberately imports nothing from AutoCorp's own `brains`/`core` packages
beyond the Python standard library, since the target's venv does not have
AutoCorp's own third-party dependencies (`rich`, `requests`, etc.)
installed — this is a hard constraint, not a style choice.

Both the workflow engine and Quick Podcast share the same safety
invariants: `--disposable`/`--test` is mandatory (the command refuses
without it), the target's git working tree must be clean before starting
(workflow-test) or the disposable copy is used regardless (quick-podcast,
which itself doesn't gate on a dirty target tree — verify this yourself if
depending on it), the target's production database SHA-256 and git status
are checked before and after, and the disposable directory is removed
(with the removal itself verified, as of Phase 1X) when the run ends,
success or failure.

## Planner

Two unrelated planners exist:

1. **`brains/planner.py`** — the original build planner: breaks a natural-
   language request into steps/files/a test command before any code is
   written, recalling relevant lessons from memory first.
2. **`brains/project_planner.py`** (Phase 1C) — a read-only, deterministic
   planner that converts Scanner + Analyzer evidence about an existing
   repository into a prioritized action plan. It calls no model and uses
   no randomness (action IDs are SHA-256 hashes of `priority:category:title`).

Do not conflate "planning a build" with "planning repository actions" —
they solve different problems and share no code.

## Reliability Engine (committed, not exposed by a dedicated CLI command)

Investigated in full on 2026-07-29 at the repository owner's request, then
committed in later production-readiness work. `reliability_engine/` is a
**second, parallel build-and-repair orchestration pipeline**, structurally
analogous to `core/orchestrator.py::Session` but not composed with it. Its
entry point, `ReliabilityOrchestrator.run()`
(`reliability_engine/orchestrator.py`), independently recalls lessons from
`memory.store`, picks a builder model, classifies a request as "edit"
(touches an existing tracked file, found via a filepath-token regex or,
absent an explicit path, an optional Chroma-backed RAG lookup over the repo)
or "greenfield" (delegates to the existing
`PlannerBrain`/`BuilderBrain.build()` unmodified), and for edit-mode work:

1. Builds a whole-repo `DependencyGraph` (AST import/call-edge walk) to
   compute blast radius and whether "core" paths (`core/`, `memory/`,
   `safety/`, `brains/`, `reliability_engine/` itself) are touched.
2. Refuses to start if the target repository has a dirty `git status`.
   This production-hardening preflight prevents merge-capable worktrees
   from being created against ambiguous target state.
3. Runs each subtask inside a real, isolated `git worktree` (`git worktree
   add -B reliability/subtask-<id> ...`), using the new
   `BuilderBrain.generate_edit_diff` to get FIND/REPLACE blocks instead of
   a raw diff, verified and applied by `patch_apply.py` before anything
   touches the file.
4. Gates every change through a static gate (`py_compile` + `ruff`/`mypy`,
   absolute or delta-only), a bounded test-fix loop with several distinct
   repair strategies (format/grounding/relevance/test-coverage), and — for
   core-touching or high-blast-radius subtasks — N-sample self-consistency
   voting instead of iterative repair.
5. Runs a full regression suite in the worktree before merging back, and
   persists subtask/attempt/known-issue history to SQLite.
6. Normalizes unexpected subtask exceptions, including merge failures, into
   blocked subtask results, records a known issue, and preserves the
   diagnostic worktree instead of letting the exception escape without
   durable state.

**It does not import or use** `brains/acceptance.py`, `brains/fixer_executor.py`,
`brains/retry_controller.py`, `brains/gated_repair_fixer.py`, or the
committed `brains/model_router.py` — its self-heal/retry logic and its model
routing are both reimplemented from scratch, not composed from the existing
pipeline's equivalents. Net assessment: a materially more capable
surgical-edit/repair engine (verified per-file patching, blast-radius-aware
strategy switching, durable attempt history, real worktree isolation) that
currently **duplicates rather than composes with** `Session`'s plan/build/
test/fix/record loop.

**Confirmed not imported by the CLI**: no dedicated `autocorp reliability`
subcommand exists. The subsystem is imported by its tests and support
modules, and AutoCorp Chat can report Reliability Engine status, but it
does not execute `ReliabilityOrchestrator.run()` directly.
The two stale branches `reliability/subtask-1`/`reliability/subtask-2`
(pointing at old commit `1615cf8`) are themselves worktree-sandbox branches
this subsystem's own naming convention (`worktree_sandbox.py`'s
`reliability/subtask-{id}` pattern) would produce — i.e. leftover artifacts
from an earlier real run of this code, left behind by `rollback()` not being
reached, not evidence of a separate git-history origin.

**Name collision — FIXED 2026-07-29.** `brains/model_router.py::ModelRouter`
(a deterministic, rule-based *engine* selector — local/deepseek/claude) and
the former `reliability_engine/model_router.py::ReliabilityModelRouter` (a
narrow Ollama-liveness/fallback checker) were unrelated in function despite
the identical filename and class-naming convention. No live import collision
ever existed (both were proper subpackages), but the ambiguity was a real
hazard for a future maintainer. Renamed to
`reliability_engine/model_availability.py` (class name unchanged — only the
file collided); the two call sites (`reliability_engine/orchestrator.py`,
`tests/test_reliability_engine.py`) were updated to match. Verified: the
full `reliability_engine` test suite (60/60, including the one new test
below) and a direct `from reliability_engine.orchestrator import
ReliabilityOrchestrator` import both pass after the rename.

**Test coverage proves unit-level correctness, not integrated behavior —
confirmed a third time, independently, on 2026-07-30.**
`tests/test_reliability_engine.py`'s tests each exercise one module in
isolation (often with a fake engine/tester), or construct a
`ReliabilityOrchestrator` and call only its private helpers
(`_dependency_context`, `_edit_plan`, `_test_targets_for`).
**`ReliabilityOrchestrator.run()` now has end-to-end regression coverage.**
`tests/test_reliability_engine.py` constructs a
disposable git repository, calls the production entry point with a
realistic edit request, uses a deterministic engine at the model boundary,
and verifies the request -> worktree -> planning -> analysis -> patch ->
static validation -> pytest -> regression -> merge -> cleanup pipeline.
The test also records AutoCorp's own git status before and after the run
and asserts it is unchanged, proving the merge applies only to the
disposable target repository.

**Concrete risks found by reading the code, each independently
re-verified across two sessions (2026-07-29, 2026-07-30) — not just
re-stated from a prior investigation or a prior session's own findings
(see `PROJECT_MEMORY.md` on why that distinction matters, twice over now):**

1. ~~`chromadb`/`PyYAML` are only in uncommitted requirements files~~ —
   **resolved before this audit.** `requirements.txt` now contains
   `chromadb>=0.5.0` and `PyYAML>=6.0`; `mypy.ini`, `ruff.toml`, and
   `reliability_config.yaml` are tracked.
2. **A missing `ruff`/`flake8`/`mypy` binary blocking a static-gate check —
   more nuanced than either prior pass concluded, and a genuine bug was
   found and fixed 2026-07-30.** `StaticGate.run()` (the absolute mode) does
   treat a missing tool as a real, blocking issue — confirmed empirically.
   Two of its three call sites already avoided this correctly by using
   `collect_issues()` + `run_delta()` instead (a missing-tool marker appears
   identically before and after a delta comparison, so it's never flagged as
   "new"): `ReliabilityTestLoop` in `test_loop.py`, and
   `SelfConsistencyRunner.choose_edit()`. **The third call site,
   `SelfConsistencyRunner.choose()` (the greenfield-mode self-consistency
   path — used precisely for the high-blast-radius/core-touching changes
   this voting mechanism exists to protect), called the unsafe `run()`
   directly**, meaning every candidate would be rejected in any environment
   lacking these tools, silently defeating self-consistency voting for the
   highest-stakes edits. This was missed by the 2026-07-29 investigation and
   by this same document's own "corrected, not a live bug" claim written
   that day — both checked only the `test_loop.py` call site. **Fixed
   2026-07-30**: `choose()` now uses `collect_issues()` + `run_delta()`,
   matching its sibling `choose_edit()`. Verified via a new regression test
   (`test_self_consistency_choose_does_not_block_on_missing_static_tools`)
   confirmed to fail against the pre-fix code and pass against the fix.
   This bug — sitting in a core safety path, undetected across two rounds
   of "independent verification" until a third pass specifically re-checked
   every call site of `StaticGate.run()` rather than only the one already
   known — is concrete evidence for why the new
   `ReliabilityOrchestrator.run()` end-to-end test is required before this
   subsystem is trusted with real edits.
3. `DependencyGraph.build()` and `CodebaseRAGIndex.rebuild()` both do a
   full, uncached repo rescan on every single `run()` call. **Confirmed,
   not fixed** (a caching layer is a real design change, not a safe,
   isolated patch — deferred to the staged integration plan below).
4. ~~`WorktreeSandbox` reuses SQLite-autoincrement subtask IDs... silently
   destroyed~~ — **FIXED 2026-07-29.** Independently confirmed by reading
   `memory/store.py`'s schema (`id INTEGER PRIMARY KEY` with no
   `AUTOINCREMENT` keyword, so SQLite reuses ROWIDs starting at 1 after
   `state_store.reset_subtasks()` empties the table) alongside
   `worktree_sandbox.py`'s unconditional `rollback(..., keep=False)` on any
   path collision. Fixed by giving every `WorktreeSandbox` instance its own
   random `run_id` (one per `ReliabilityOrchestrator`, i.e. one per `run()`
   call), folded into every worktree path/branch name
   (`subtask-{run_id}-{subtask_id}`), so a reused subtask id can no longer
   collide across separate runs. A new regression test,
   `test_reused_subtask_id_across_runs_does_not_destroy_preserved_worktree`,
   proves a worktree preserved by one `WorktreeSandbox` instance survives a
   second instance creating a worktree for the same subtask id.

**Staged integration plan, if the repository owner decides to proceed**
(steps 2, 3, 4, 5, and 6 are now complete on their own merits as general
code-quality fixes and verification; completing them is not the same as
authorizing a dedicated CLI integration, which remains a separate,
still-open owner decision): ~~(1)
triage/commit-or-discard the unrelated Phase 1X/1Y and repair-redaction
changes~~ — done, they no longer share this working tree with
reliability_engine (see `CURRENT_PHASE.md`); ~~(2) rename
`reliability_engine/model_router.py`~~ — **done**, see above; ~~(3) commit
the three purely-additive, low-risk pieces first and separately — the
`brains/builder.py` diff, the `memory/store.py` diff, and the
`chromadb`/`PyYAML`/`mypy`/`ruff` requirements additions~~ — **done before
this audit**; ~~(4) fix the
worktree-ID-collision-destroys-blocked-state issue~~ — **done**, see above;
~~(5) the missing-tool-vs-real-issue distinction in `StaticGate`~~ — **done,
2026-07-30**, see item 2 above (this one turned out to need an actual code
fix, not just documentation, once the third call site was found); ~~(6) add
a true end-to-end test of `ReliabilityOrchestrator.run()` before trusting it
with real edits~~ — **done, 2026-07-30**; (7) only then
add a dedicated Reliability Engine CLI subcommand (following the
`cmd_workflow_test`/`cmd_build` pattern, confirming the existing
`console.confirm("Proceed with this plan?")` gate at
`orchestrator.py:172-174/199-200` stays intact and is not bypassed by
`--auto`-style defaults without the owner's explicit intent). See
`NEXT_STEPS.md` for the live status of this decision.

### Production-readiness evidence update (2026-07-30)

Requested explicitly this date: add real end-to-end verification for
`ReliabilityOrchestrator.run()` and implement AutoCorp Chat. The new
repository evidence:

- Architecture is internally consistent and complete for the paths it
  implements: no dead files (every module is imported by at least one
  other file or its test), no dead APIs, no TODOs/FIXMEs/`NotImplementedError`
  stubs, no mock/fake implementations presented as real (confirmed by
  grepping the source itself, not just its tests).
- It does duplicate existing functionality: a second, parallel build/repair
  orchestration pipeline alongside `core/orchestrator.py::Session`, with its
  own reimplemented self-heal/retry logic. Whether to merge, replace, or
  keep both as separate modes is a product decision for the repository
  owner (see step 8 of the original investigation's recommendation,
  unchanged) — not something resolved by this review.
- `ReliabilityOrchestrator.run()` is now exercised by
  `tests/test_reliability_engine.py::TestReliabilityOrchestratorEndToEnd::
  test_run_executes_disposable_edit_workflow_and_cleans_worktree`.
  Focused verification in this session passed:
  `.venv/bin/python -m pytest -W error -q tests/test_reliability_engine.py
  tests/test_autocorp_chat.py` -> exit code 0, 69 passed.
- The E2E test covers a real temporary git repository and verifies
  worktree creation, planning, dependency analysis/blast-radius handling,
  patch application, static validation, pytest validation, full regression
  execution, merge behavior, cleanup, and no mutation of AutoCorp's own
  repository status during the test.
- The production-hardening audit adds focused tests for dirty target repo
  refusal before worktree creation and merge-failure diagnostic
  preservation.
- Packaging dependencies are now tracked: `requirements.txt` includes
  `chromadb>=0.5.0` and `PyYAML>=6.0`.

**What remains:** finish full required verification for the
production-hardening working tree and commit it if it passes. A dedicated
Reliability Engine CLI entry point remains an owner/product decision; no
such command is added in this working tree.

## Data flow (build loop, original architecture, still current)

```
request → recall_lessons (memory) → Planner.plan → [confirm via gate] →
Builder.build (writes via Executor+CommandGate) → Tester.test →
[self-heal loop if --self-heal] → record_build + record_lesson (memory)
```

## Data flow (CloneCast-validation infrastructure, Phase 1A–1Y)

```
--repo → workspace.resolve_workspace (safety-checked) →
scanner.run_scan + analyzer.run_analysis (read-only) →
project_planner.run_project_plan (deterministic, evidence-cited) →
[repair_executor.build_repair_plan (--approve to execute) |
 repair_proposal.build_repair_proposal (review-only, never applies)]

--disposable workflow-test/publish-test/quick-podcast:
copy target DB to /tmp → point CLONECAST_* env at the copy →
launch target's own server against the copy → drive real HTTP requests →
verify artifacts (ffprobe + SHA-256) → verify DB integrity →
verify target production DB/git unchanged → remove disposable directory
```

`quick-podcast` writes its default generated episode output outside the
target repository under
`/tmp/autocorp_quick_podcast_output/<repo>/test_episode`; an explicit
`--output` path is still honored.

## Data flow (AutoCorp Chat)

```
chat prompt -> AutoCorpChatSession.handle ->
[scanner | analyzer + project_planner | git | AI_ENGINEERING docs |
 workflow-test/publish-test command guidance | repair-plan guidance] ->
ChatResponse(text, optional commands, session history)
```

The chat session remembers only in-process conversation state and the most
recent scan/analysis/plan objects. It does not persist chat memory to
SQLite and does not call an LLM provider.

## Verification Scope

Maintained-source compile verification is performed by
`scripts/verify_compileall.py`. It compiles exactly the Python files that
are either tracked by git or non-ignored untracked files in the working
tree. This intentionally follows repository ownership evidence:

- `.gitignore` ignores `.venv/`, `workspace/`, `data/`, generated reports,
  caches, and build outputs.
- `pytest.ini` sets `testpaths = tests` and `norecursedirs = workspace
  .venv data .git *.egg-info __pycache__`.
- `brains/analyzer.py` excludes `workspace/` and `data/` from AutoCorp's
  architecture-level analysis because `workspace/` contains generated
  output rather than AutoCorp's own source.

The bare command `python -m compileall .` is not a valid repository-level
quality gate here: it recurses into ignored virtualenv packages, generated
workspace apps, disposable Reliability worktrees, runtime data, and build
artifacts. Failures in those areas are classified separately and do not
represent syntax errors in maintained AutoCorp source.

## Extension points

- **New code-generation engine:** implement `BaseEngine`, register in
  `brains/engine_registry.py`. This is the established, documented pattern
  (`local_engine.py`/`claude_engine.py`/`deepseek_engine.py` are the three
  existing examples).
- **New CLI subcommand:** add a `cmd_*` handler in `autocorp.py` and a
  `sub.add_parser(...)` block, keeping all logic in `brains/`.
- **New disposable-target validation:** follow the `workflow_test.py` /
  `quick_podcast_runner.py` pattern — disposable copy of production data,
  environment-variable-driven redirection of every output path, real HTTP
  or real service calls (no mocks), independent post-hoc verification, and
  mandatory cleanup.
- **Agent Watchdog:** `safety/watchdog_gate.py` is a documented, currently
  unfilled plug-in point (loads an external tool if present, falls back to
  interactive confirmation otherwise). No evidence in this repository of
  that external tool's own implementation.

## Performance considerations

- Real runs against CloneCast are dominated by two stages, consistently,
  across every real run performed in this repository's history: Ollama
  dialogue generation and Chatterbox voice synthesis (both scale with
  requested episode duration). The Quick Podcast progress system's
  "estimated remaining time" weighting reflects this directly (`Script` and
  `Voice` carry the largest weights in `brains/quick_podcast_runner.py`'s
  `_PHASE_WEIGHTS`).
- The workflow/quick-podcast subprocess model means each real run launches
  a full second Python process (the target's own venv) plus, for
  quick-podcast, a background thread polling the disposable SQLite database
  over its own read-only connection — this is deliberate (WAL mode,
  per `clonecast.db.connect_database`, supports concurrent readers safely)
  rather than an oversight.

## Security considerations

- Secret handling in the AI Repair Proposal Engine (Phase 1G) has
  documented, real gaps (see `PHASES.md`, Phase 1G, and `NEXT_STEPS.md`) —
  do not treat its secret-file exclusion or inline-redaction as complete
  coverage until those are fixed.
- Every target-system-facing tool in this repository defaults to refusing
  unless an explicit flag is present (`--approve`, `--disposable`,
  `--test`), and every one that could theoretically reach a real external
  network destination has been verified — by reading the target's own
  source, not by assumption — to be structurally incapable of doing so
  (Phase 1Y's finding that CloneCast's `destination_type` column is
  database-CHECK-constrained to `'local'` is the concrete example).
- `DEEPSEEK_API_KEY`, if present in the ambient environment, has been shown
  (Phase 1G's audit, and independently during Reliability-adjacent test
  hardening) to cause at least one test to silently make a real network
  call instead of exercising its intended offline path. Treat any ambient
  API-key environment variable as a real test-isolation risk, not a
  theoretical one, in this repository.
