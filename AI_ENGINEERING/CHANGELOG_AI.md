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

### 2026-07-29 — Owner decisions on the three uncommitted efforts; quick-podcast wired; Phase 1G re-verified
The repository owner reviewed the three independent uncommitted efforts
recorded in the prior entry and directed, per effort: keep Phase 1X/1Y
iterating uncommitted; commit the Quick Podcast CLI wiring now; investigate
the Reliability Engine and propose integration, do not wire it in; and
proceed with the README fix and Phase 1G bug fixes without further
sign-off.

**Quick Podcast CLI wiring committed as `53f0d7d`.** `autocorp.py`'s working
diff had the quick-podcast wiring and the Phase 1X/1Y changes interleaved in
one region (both touch code adjacent to `cmd_workflow_test`). Isolated by
reconstructing a `HEAD`-plus-quick-podcast-only version of the file (import
line, `cmd_quick_podcast`, the `quick-podcast` subparser — verified via
`py_compile` and a direct diff against both `HEAD` and the full working
tree before committing), committing that, then restoring the Phase 1X/1Y
portions back into the working tree from a backup taken before the split.
Full suite re-run after both the split and the restore: 933 passed, 1
xfailed, unchanged.

**Phase 1G's five audit findings re-verified against current `HEAD` rather
than trusted from the untracked report — four of five were already fixed.**
`claude_phase_1g_audit.txt` was written against `1339aaf`; commit `ea71d54
"fix: harden proposal secret and provider safety"` (already in this
repository's history) fixed the `--provider claude` `TypeError`, the
`--provider deepseek` model-tag conflation, the secret-file-exclusion
compound-filename gap, and replaced
`test_provider_contracts.py::test_no_silent_fallback` with four narrower
regression tests that pass regardless of an ambient `DEEPSEEK_API_KEY`
(confirmed: this session's environment has a real key set, and the full
`test_provider_contracts.py` file — 12/12 — passes). This repository's own
prior `NEXT_STEPS.md`/`PHASES.md`/`CURRENT_PHASE.md` had carried the
audit's stale claims forward without re-verification — exactly the failure
mode `AI_ENGINEERING_CONSTITUTION.md` §3 warns against, this time inside
the documentation system meant to prevent it. Corrected in all three files
this session.

**Inline secret redaction — the one genuinely remaining gap — fixed.** Two
of the audit's nine adversarial cases were still unredacted: compound
`DB_PASSWORD`-style keys (the old pattern's `\b` doesn't cross an
underscore) and JSON-quoted `{"Authorization": "Bearer ..."}` headers.
Fixed in `brains/repair_proposal.py`'s `_INLINE_SECRET_RE`/
`_redact_inline_secrets`: the plain password/secret alternative now treats
`_` as a boundary (catching `DB_PASSWORD` without matching `secretary_name`
— verified both ways), and the Authorization/Bearer alternative allows
optional surrounding quotes. Three new regression tests added to
`tests/test_repair_proposal.py`. All nine of the audit's original
adversarial lines now redact correctly (verified directly, not just via the
new unit tests).

**README.md updated** with a "Current commands" section (all fifteen
current subcommands, noting which are committed vs. not) and pointers to
`AI_ENGINEERING/` for the parts of the system the original four-brains
description predates.

**Reliability Engine investigated in full (read-only), not integrated.**
Confirmed: a second, parallel build/repair orchestration pipeline that
duplicates rather than composes with `core/orchestrator.py::Session`; its
own true end-to-end entry point is never exercised by its 59 passing unit
tests; the two stale `reliability/subtask-*` branches are almost certainly
leftover artifacts from an earlier real run of this same code (its
`worktree_sandbox.py` produces branches with that exact naming pattern),
not evidence of a separate origin. Real, unaddressed risks found by reading
the code (worktree-ID collision destroying preserved diagnostic state for
blocked subtasks; missing-tool-vs-real-issue conflation in the static gate;
uncached full-repo rescans per call) are recorded in `ARCHITECTURE.md`
along with a 7-step staged integration plan. Integration itself remains
unauthorized.

**This session's changes remain uncommitted, pending owner review:**
`README.md`, `brains/repair_proposal.py`, `tests/test_repair_proposal.py`,
and the `AI_ENGINEERING/*.md` corrections above. Only the quick-podcast CLI
wiring was committed, per explicit owner instruction to do so.

Full suite after all of this session's changes: **936 passed, 1 xfailed, 0
failed**, exit code 0 (933 plus 3 new tests for the inline-redaction fix).

### 2026-07-29 — README/Phase 1G fixes committed after re-review; Phase 1X/1Y reviewed and committed; two Reliability Engine bugs fixed
A follow-up instruction asked for one more independent, skeptical review of
the previous entry's uncommitted work before committing it — explicitly not
to assume it was correct just because tests had passed.

**README.md + Phase 1G inline-redaction fix — re-reviewed and committed as
`fad85a8`.** Re-verified all 9 of the original audit's adversarial redaction
cases directly (not re-run from memory), checked for false positives
(`secretary_name`, `tokenizer`, `password_reset_enabled` all correctly stay
unredacted), and confirmed the README's "15 total / 14 committed" command
counts against live `--help` output. Found and fixed one real contradiction
this review was meant to catch: `PHASE_COMPLETION_POLICY.md` (not in the
original file list, but factually entangled with it) still cited the
quick-podcast CLI wiring as its example of "committed, but partially
wired" — false since it had already been committed as `53f0d7d`. Corrected
and included in the commit (11 files, not 10). Noted but did not fix
(pre-existing, unrelated to this diff): `README.md`'s
`#agent-watchdog-integration-future` anchor doesn't match its actual header.

**A further instruction requested completing the remaining Phase 1X/1Y work
and resolving the Reliability Engine's investigation findings.** Reviewed
the full Phase 1X/1Y diff (`autocorp.py`, `brains/workflow_test.py`,
`tests/test_workflow_character_id_propagation.py`) for accidental or
unrelated content (none found) and completeness (all new helpers exercised
by tests; both `workflow-test` and `publish-test` correctly refuse to run
without `--disposable`). Found the implementation genuinely complete,
blocked only by the external CloneCast audio-clipping defect already
documented — committed as `ecf6a11`.

**Every Reliability Engine investigation finding was independently
re-verified against the actual source before acting on it**, per explicit
instruction not to assume the prior investigation's conclusions were
correct:
- The worktree-ID-collision claim was **confirmed** by reading
  `memory/store.py`'s schema (`id INTEGER PRIMARY KEY`, no `AUTOINCREMENT`,
  so SQLite reuses ROWIDs after `state_store.reset_subtasks()` empties the
  table) alongside `worktree_sandbox.py`'s unconditional
  `rollback(..., keep=False)` on any path collision — and **fixed**: every
  `WorktreeSandbox` now gets its own random `run_id` folded into every
  worktree path/branch name, with a new regression test proving a preserved
  worktree survives a second instance reusing the same subtask id.
- The `model_router.py` naming-collision claim was **confirmed** (no live
  import collision, but a real hazard from two unrelated modules sharing a
  filename and class-naming convention) and **fixed**: renamed to
  `reliability_engine/model_availability.py`, with its two call sites
  (`orchestrator.py`, `tests/test_reliability_engine.py`) updated.
- The "a missing `ruff`/`flake8`/`mypy` binary blocks every edit" claim was
  **re-verified empirically and found incorrect**: monkeypatching tool
  detection to simulate all three missing and calling the actual code path
  used by any real caller (`StaticGate.run_delta`, via
  `ReliabilityTestLoop`) showed the missing-tool marker appears identically
  in both the before/after snapshots `run_delta` compares, so it is never
  flagged as "new" and never blocks. Documented the correction rather than
  implementing a fix for a problem that doesn't actually occur in the real
  code path.

**The entire `reliability_engine/` tree remains uncommitted.** Fixing
internal bugs is a different decision from authorizing integration or even
committing the subsystem to git history for the first time — neither has
been authorized, and the standing "integration NOT authorized" decision was
more absolute and more recently reinforced than Phase 1X/1Y's "keep
iterating," which is why these two items were treated differently this
session (one committed, one not).

Full suite after this entry's changes: **937 passed, 1 xfailed, 0 failed**,
exit code 0 (one further new regression test, for the worktree-ID-collision
fix).

## Full test suite status at time of writing

`.venv/bin/python -m pytest -q` (rerun to produce this entry, not copied
from an old report): **937 passed, 1 xfailed, 0 failed**, exit code 0 —
against the full working tree, uncommitted changes included (933 passed
before this day's 4 new regression tests: 3 for the inline-redaction fix,
1 for the worktree-ID-collision fix).
