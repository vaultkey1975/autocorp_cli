# Architecture

This document describes the system as it exists in the repository today —
committed and uncommitted state both, clearly distinguished — not as it was
originally designed or as a future phase might redesign it. See
`PHASES.md` for how each piece came to exist.

---

## Directory structure (tracked, via `git ls-files`)

```
autocorp.py           CLI entry point (argparse) - 15 subcommands in the
                       working tree, 13 committed at HEAD (see below)
config.py              Single source of truth for model/endpoint/timeouts/
                       paths; APP_VERSION frozen at "0.1.0" since the first
                       commit (see PROJECT_MEMORY.md)
pytest.ini             testpaths=tests; explicitly excludes workspace/,
                       .venv, data, .git, egg-info, __pycache__
core/                  console.py, llm.py (Ollama client), orchestrator.py
brains/                37 tracked .py files (via `git ls-files "brains/*.py"`)
                       - see "brains/ inventory" below
memory/                store.py - SQLite build/lesson memory
safety/                executor.py, gate.py, watchdog_gate.py
tests/                 93 tracked test_*.py files (94 total on disk,
                       including one uncommitted: test_reliability_engine.py)
```

Present in the working tree but **not tracked by git** (verify with `git
status` before trusting any of this as shipped):

```
reliability_engine/     16 modules, 2,346 lines - unintegrated (see below)
data/                   runtime SQLite (data/autocorp.db) + a Chroma vector
                       store (data/chroma/) - the Chroma dependency itself
                       (chromadb) is only in the uncommitted requirements.txt
mypy.ini, ruff.toml,
reliability_config.yaml  tooling config for the Reliability Engine
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

Subcommands committed at `HEAD` (`git show HEAD:autocorp.py`): `build`,
`plan`, `test`, `explain`, `memory`, `scan`, `analyze`, `plan-project`,
`repair`, `propose-repair`, `live-readiness`, `live-test`, `workflow-test`.

Subcommands present only in the uncommitted working tree: `publish-test`,
`quick-podcast`.

Gate selection (`_make_gate`) chooses between `AllowAllGate` (`--auto`),
`WatchdogGate` (`--watchdog`), and the default interactive `ConfirmGate` —
this is the seam through which an external "Agent Watchdog" tool could
approve or block file writes/commands; `safety/watchdog_gate.py` loads it
optionally at runtime and falls back to `ConfirmGate` if unavailable.

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
  `live_readiness.py`, `workflow_test.py`.
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

**Uncommitted addition:** three further tables — `subtasks`, `attempts`,
`known_issues` — added by the (also uncommitted) Reliability Engine work.
These are real, working SQLite `CREATE TABLE IF NOT EXISTS` statements
already in `memory/store.py`'s `init_db()`, but nothing in committed code
writes to or reads from them, since the code that would (Reliability
Engine) is itself uncommitted and unintegrated.

Chroma (`data/chroma/`) appears in the working tree as a second, vector-
based store, but `chromadb` is only a dependency in the uncommitted
`requirements.txt` — its presence on disk is real, its integration status
is not evidenced in any committed code.

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

`brains/workflow_test.py` (Phase 1M–1S, extended uncommitted by Phase
1X/1Y) drives a real, disposable, end-to-end validation of an external
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

## Reliability Engine (uncommitted, unintegrated)

`reliability_engine/` contains 16 modules (`self_consistency.py`,
`state_store.py`, `patch_apply.py`, `dep_graph.py`, `config_loader.py`,
`orchestrator.py`, `rag_index.py`, `planner_spec.py`, `edit_router.py`,
`env_isolation.py`, `test_loop.py`, `static_gate.py`,
`regression_runner.py`, `grammar_constraints.py`, `worktree_sandbox.py`,
`model_router.py` — note the name collision with `brains/model_router.py`,
a **different, existing, committed file**; confirm which one any given
import statement resolves to before relying on either). It has its own
passing test file (`tests/test_reliability_engine.py`) but:

- Is not imported by `autocorp.py`.
- Is not imported by any file under `brains/`.
- Has no CLI subcommand.
- Is not referenced in `ARCHITECTURE.md`'s CLI architecture section above,
  because it has no place in it yet.

Document what this subsystem actually does by reading its own source
directly if you need to work on it — this document deliberately does not
describe its internal design, because doing so here without the module
being integrated or reviewed would risk stating intent as if it were
verified architecture.

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
