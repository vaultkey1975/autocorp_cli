# AI Engineering Changelog

A chronological, evidence-based summary of this repository's history. This
is an index into `git log`, not a replacement for it — dates and hashes
below are taken directly from `git log --format='%ci %h %s'` and are
reproducible by anyone. Uncommitted work is included, dated by direct
observation (file mtimes, session context) where no commit timestamp
exists, and explicitly marked as uncommitted.

---

### 2026-06-05 — Foundation
`e068f45` Initial commit: AutoCorp CLI v0.1.0. The four-brain architecture
(Planner/Builder/Tester/Memory) and the Executor+CommandGate safety seam.
Tagged `v0.1.0`.

`c21a4b2` Self-test suite added (46 tests). Tagged `v0.2.0`.

### 2026-06-05 to 2026-06-08 — SQLite Generation Template (Era 1)
Seven phases building a deterministic SQLite-backed desktop-app
code-generation template: schema/CRUD (`d08462e`), master-detail UI
(`9a97897`), CSV export (`e1eeef6`), a deterministic UI framework
(`864b97b`), inline editing (`c6028a5`), and a ten-commit reporting/
dashboard build-out (`25062b0` through `b646dc6`). Tagged incrementally
`v0.3.0` through `v0.10.5`. **`v0.10.5` is the last tag in this
repository's history.**

### 2026-06-09 to 2026-06-14 — Engine Abstraction & Repair Pipeline (Era 2)
`84787e5` (2026-06-09) Phase 8A-8F complete: `BaseEngine` abstraction,
Reviewer Brain, Model Router.

`c9df575` (2026-06-11) DeepSeek API + Phase 8G routing + Phase 8H-8J
acceptance planning pipeline.

`7c88dd6`, `313d138`, `4e6994f`, `34267a7`, `668512e` — fixer handoff,
fixer executor + retry controller, self-healing pipeline integration
(Phases 8K, 8M, 8N, 8S, 8T).

`1d807a7` (2026-06-13) Phase 8Z: Repair Content Provider Factory.
`e1554b6` Phase 8AA: `TesterBackedRepairContentProvider`.

`0197af1` through `d8fe0f3` (2026-06-14) Phase DS5 through DS10: Tester
engine routing, self-heal wiring into the repair generator, engine CLI
selection, `--self-heal` build flag, repair target resolution.

`53b470b` (2026-06-22) Anchor self-heal repair writes to workspace.

*(Gap: no commits between 2026-06-22 and 2026-07-23 — roughly a month with
no repository activity by any evidence available here.)*

### 2026-07-23 — Repository Intelligence & CloneCast-Validation Begins (Era 3)
A single, dense day of activity:

`aae9507` (15:13) Default model changed to `qwen2.5:14b`.

`ab43dd9` (15:33) Phase 1A: Repository Scanner.

`aaae100` Hardening pass eliminating pytest collection warnings — Phase 1B
(Project Intelligence Engine / Analyzer) shipped inside this commit rather
than its own dedicated one; see `PHASES.md` for why this is noted
explicitly.

`b32bc74` Phase 1C: Project Action Planner.

`fd01754`, `12d1d94` Phase 1D: Safe Repair Executor, with corrections.

`30ca9df` Phase 1F: Workspace Resolution / safe external repository
targeting.

`ce4d614` (17:49) Phase 1G: Provider Abstraction + AI Repair Proposal
Engine. `1339aaf`, `ea71d54` — `--provider ollama` alias fix, secret/
provider-safety hardening.

`8a76073`, `c6c7cc8`, `8316a80` Phase 1H+1I: Live Application Readiness
Scanner, hardened against binary dependencies, requiring production
evidence for its findings.

`43c34d6`, `5678688` Phase 1J-1L: Controlled Live Application Test.

`8ebee4c` (21:08) Phase 1M-1S begins: Disposable Episode Workflow Testing
against CloneCast.

### 2026-07-23 through 2026-07-28 — Disposable Workflow Hardening
Twelve further commits (`b8e7e91` through `307d2f5`, latest 2026-07-28
16:41) progressively hardened the disposable workflow test: grounding
requests in the live OpenAPI schema, constructing requests from real
schemas rather than assumptions, preserving verified session state,
validating redirects, propagating real identifiers, discovering and
completing studio/character activation prerequisites, resolving
reviewer identities and decision enums from evidence, satisfying
disposable-host prerequisites, and — the final commit in this run —
recovering and preserving canonical character IDs correctly.

### 2026-07-29 — Quick Podcast Refactor (current HEAD)
`143825a` (14:52:58) Modularize quick podcast runner and add live progress
reporting. Replaced an embedded, silent `python -c "<thousands of
lines>"` subprocess with a real, importable module
(`brains/quick_podcast_runner.py`, invoked via `-m`) with structured,
immediately-flushed progress output and a persistent log file at
`/tmp/autocorp_quick_podcast.log`. 1,628 lines added across four new
files, zero deletions (no prior committed version existed to replace).
Verified via two real end-to-end runs against CloneCast; both correctly
reported a real, external CloneCast audio-clipping error rather than
silently failing.

**This is the current `HEAD` of `main`.**

### Uncommitted work present at the time of this changelog entry
Not dated by commit (none exists); observed directly in the current
working tree:

- **Phase 1X — CloneCast Production Episode Validation.** Extended
  `brains/workflow_test.py` with independent SHA-256/`ffprobe` artifact
  verification, `PRAGMA integrity_check`/`PRAGMA foreign_key_check`
  database verification, and actual (verified) disposable-directory
  cleanup. Verified via two real runs, both reaching
  `DISPOSABLE_WORKFLOW_COMPLETE`. Discovered, as a side effect of the new
  database verification, that CloneCast's production database has 9
  pre-existing foreign-key violations in its legacy chapter-script tables
  — unrelated to and unaffected by this phase's own work. A report
  (`phase_1x_report.txt`) exists on disk but is gitignored.
- **Phase 1Y — Production Publishing Validation.** Further extended
  `brains/workflow_test.py` with QC, release-readiness, packaging, local
  publication, and platform-export stages, plus a `publish-test` CLI
  subcommand (also uncommitted, in `autocorp.py`). Confirmed, by direct
  source inspection, that CloneCast has no code path capable of a real
  external upload (`destination_type` is database-CHECK-constrained to
  `'local'`). Two real runs both correctly stopped at CloneCast's own QC
  gate due to the same audio-clipping issue found under Phase 1X/Quick
  Podcast — a reproducible, external, non-AutoCorp finding. A report
  (`phase_1y_report.txt`) exists on disk but is gitignored.
- **Quick Podcast CLI wiring.** The `quick-podcast` subcommand registration
  in `autocorp.py` (the module it depends on is committed as `143825a`;
  the CLI registration is not).
- **Reliability Engine.** A 16-module, 2,346-line subsystem
  (`reliability_engine/`) plus supporting, uncommitted changes to
  `brains/builder.py` (an edit-diff generation mode) and `memory/store.py`
  (three new tables: `subtasks`, `attempts`, `known_issues`), plus
  `requirements.txt`/`requirements-dev.txt` additions (`chromadb`, `PyYAML`,
  `mypy`, `ruff`) and tooling config (`mypy.ini`, `ruff.toml`,
  `reliability_config.yaml`). Has its own passing test file
  (`tests/test_reliability_engine.py`) but is not imported by `autocorp.py`
  or any `brains/*.py` module and has no CLI entry point. Origin and
  authorship relative to any specific dated session could not be
  determined from repository evidence; two branches sharing the
  "reliability" name (`reliability/subtask-1`, `reliability/subtask-2`)
  exist but both point at an unrelated older commit (`1615cf8`,
  2026-07-24) with no unique commits of their own, and do not appear to
  contain or explain this working-tree content.
- Several report files exist untracked and unexplained by any commit:
  `claude_phase_1g_audit.txt` (a real safety audit of Phase 1G, finding
  five distinct gaps — see `PHASES.md`), `clonecast_live_readiness_report.txt`,
  and `phase_1q_runtime_output.txt` (runtime output from an earlier,
  pre-rewrite version of the disposable workflow test — its
  `DISPOSABLE_RECORD_FLOW_COMPLETE` status string does not match any
  status the current `workflow_test.py` produces, confirming it predates
  the current implementation).

## Full test suite status at time of writing

`.venv/bin/python -m pytest -q` (rerun to produce this entry, not copied
from an old report): **933 passed, 1 xfailed, 0 failed**, exit code 0 —
against the full working tree, uncommitted changes included.
