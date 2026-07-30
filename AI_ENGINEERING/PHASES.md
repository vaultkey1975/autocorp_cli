# Master Phase Document

This document records every phase of work in this repository that is
supported by direct evidence: a commit, a module docstring, a test file, or
a generated report. Phase boundaries and numbering below are taken directly
from the repository's own conventions (module docstrings such as
`[Phase 1A]`, commit subjects such as `Phase 8K`, `Phase DS9`) — they are
not an AI-invented taxonomy layered on top.

Evidence commands used to build this document (reproducible by anyone):
`git log --oneline --all`, `git for-each-ref --format='%(refname:short) ->
%(*objectname:short) %(*subject)' refs/tags`, `git log --follow
--diff-filter=A --format='%h %s' -- <file>`, `grep -rn "\[Phase" brains/*.py`,
and direct inspection of module docstrings.

**Important naming note (see also `PROJECT_MEMORY.md`):** this repository
has used the label "Phase 1" for two entirely unrelated efforts at
different points in its history — the tagged "SQLite Generation Phase 1–7"
(a code-generation template feature) and the untagged "Phase 1A–1Y" series
(AutoCorp's own scanning/repair/CloneCast-validation infrastructure). They
are distinguished below by era; do not conflate them.

---

## ERA 1 — Foundation & SQLite Generation Template (tagged `v0.1.0`–`v0.10.5`)

### Phase: Initial Commit (v0.1.0)
- **Phase ID:** `v0.1.0`
- **Name:** AutoCorp CLI v0.1.0 — local terminal AI coding assistant
- **Purpose:** Establish the original four-brain architecture (Planner,
  Builder, Tester, Memory) described in `README.md`.
- **Goals:** Ollama-backed plan→build→test→explain loop; SQLite-backed
  memory of past builds and lessons.
- **Requirements:** Local Ollama with a `llama3.2` model pulled.
- **Dependencies:** None (foundation commit).
- **Deliverables:** `autocorp.py`, `core/`, `brains/planner.py`,
  `brains/builder.py`, `brains/tester.py`, `memory/store.py`,
  `safety/gate.py`, `safety/executor.py`.
- **Testing:** Unable to determine test coverage at this specific commit
  from repository evidence beyond what the next phase adds explicitly.
- **Verification:** Unable to determine from repository evidence (no
  report exists for this commit specifically).
- **Exit Criteria:** Unable to determine from repository evidence.
- **Completion Evidence:** Committed (`e068f45`) and tagged `v0.1.0`.
- **Notes:** `config.py`'s `APP_VERSION` constant still reads `"0.1.0"` as
  of the current `HEAD` — it was never updated across the following 63
  commits and 14 further tags. See `PROJECT_MEMORY.md`.

### Phase: Self-Test Suite (v0.2.0)
- **Phase ID:** `v0.2.0`
- **Purpose:** Add AutoCorp CLI's own self-test suite (46 tests per commit
  subject).
- **Completion Evidence:** Committed (`c21a4b2`), tagged `v0.2.0`.
- **Notes:** Establishes the pattern, continued ever since, of AutoCorp
  testing itself rather than only the code it generates.

### SQLite Generation Phase 1 — SQLite-backed desktop app template (v0.3.0)
- **Purpose:** Add a deterministic code-generation template
  (`brains/templates/sqlite_desktop.py`) so AutoCorp can scaffold a
  SQLite-backed desktop application for a user, rather than generating one
  from scratch via the model each time.
- **Completion Evidence:** Committed (`78d6198`), tagged `v0.3.0`.

### SQLite Generation Phase 2 — multi-table schema, FKs, search, CRUD UI (v0.4.0)
- **Completion Evidence:** Committed (`d08462e`), tagged `v0.4.0`.

### SQLite Generation Phase 3 — master-detail UI + child counts (v0.5.0)
- **Completion Evidence:** Committed (`9a97897`), tagged `v0.5.0`.

### SQLite Generation Phase 4 — CSV export (v0.6.0)
- **Completion Evidence:** Committed (`e1eeef6`), tagged `v0.6.0`.

### SQLite Generation Phase 5 — deterministic UI framework (v0.7.0)
- **Completion Evidence:** Committed (`864b97b`), tagged `v0.7.0`.

### SQLite Generation Phase 6 — inline child-row editing (v0.8.0)
- **Completion Evidence:** Committed (`c6028a5`), tagged `v0.8.0`.

### SQLite Generation Phase 7 — reporting, dashboard, charts, exports (v0.9.0–v0.10.5)
- **Purpose:** Build out reporting/dashboard capability (steps 4.0 through
  4.9 per commit subjects) for generated SQLite desktop apps: tabbed app
  shell, dashboard polish, charts, CSV/metrics export buttons, chart FK
  averages.
- **Deliverables:** `brains/templates/sqlite_support.py` and associated
  template logic (exact file boundaries not independently re-verified
  line-by-line for this document; see `git log -- brains/templates/` for
  the full diff history if a precise breakdown is needed).
- **Completion Evidence:** Committed across ten commits (`25062b0` through
  `b646dc6`), tagged incrementally `v0.9.0` through `v0.10.5`.
- **Notes:** This is the **last tagged release** in the repository's
  history as of the current `HEAD`. Everything below is committed but
  untagged, or uncommitted.

*(`ef80b5d "AutoCorp CLI checkpoint"` sits immediately after this era in
`git log` with no further detail in its own message; unable to determine
additional scope from repository evidence beyond the commit diff.)*

---

## ERA 2 — Engine Abstraction, Repair Engine & Self-Healing (untagged)

### Phase 8A–8F — Engine abstraction, Reviewer, Model Router
- **Purpose:** Introduce the `BaseEngine` abstraction so code generation is
  not hardcoded to one model/provider, add a deterministic static Reviewer
  Brain, and add rule-based Model Router routing.
- **Deliverables:** `brains/base_engine.py`, `brains/local_engine.py`,
  `brains/claude_engine.py`, `brains/engine_registry.py`,
  `brains/reviewer.py`, `brains/model_router.py`.
- **Completion Evidence:** Committed (`84787e5 "Phase 8A-8F complete"`).

### Phase 8G–8J — DeepSeek routing + Acceptance planning pipeline
- **Purpose:** Add the DeepSeek engine (dual local/API transport — see
  `brains/deepseek_engine.py`), rule-based routing to it, and the
  Acceptance→Fix-Feedback planning pipeline (`brains/acceptance.py`,
  `brains/acceptance_brain.py`).
- **Completion Evidence:** Committed (`c9df575`).
- **Notes:** `brains/acceptance_brain.py`'s docstring was found, during a
  later hardening pass, to still describe itself as "RED / STUB" long
  after its methods were fully implemented and wired into
  `core/orchestrator.py` — corrected in a subsequent hardening commit. See
  `PROJECT_MEMORY.md` for why this class of drift matters.

### Phase 8K — Fixer handoff pipeline
- **Completion Evidence:** Committed (`7c88dd6`).

### Phase 8M — Fixer executor and retry controller
- **Deliverables:** `brains/fixer_executor.py`, `brains/retry_controller.py`.
- **Completion Evidence:** Committed (`313d138`).

### Phase 8N, 8S, 8T — Self-healing pipeline integration
- **Purpose:** Wire the fixer/retry/acceptance pieces into a working
  self-healing repair loop.
- **Completion Evidence:** Committed (`4e6994f`, `34267a7`, `668512e`).

### Phase 8Z, 8AA — Repair Content Provider Factory
- **Purpose:** Add a pluggable repair-content-generation seam
  (`brains/repair_content_generator.py`) with a real, model-backed
  provider (`TesterBackedRepairContentProvider`).
- **Completion Evidence:** Committed (`1d807a7`, `e1554b6`).

### Phase DS5–DS10 — Tester engine routing and self-heal CLI wiring
- **Purpose:** Route the Tester Brain's repair generation through the
  engine abstraction, expose engine selection on the CLI, and add the
  `--self-heal` build flag with target resolution.
- **Deliverables:** CLI flags on `autocorp.py`'s `build` subcommand
  (`--tester-engine`, `--self-heal`), `brains/gated_repair_fixer.py`,
  `brains/dependency_analyzer.py`.
- **Completion Evidence:** Committed across six commits (`0197af1` through
  `d8fe0f3`), plus a follow-up fix anchoring self-heal repair writes to the
  workspace (`53b470b`).

### Hardening: default model change, test suite warning elimination
- **Purpose:** Change the default Ollama model to `qwen2.5:14b`
  (`aae9507`), then (later, interleaved with Era 3) eliminate pytest
  collection warnings and fix sqlite3 connection leaks
  (`12d1d94`, `f2c3961`, `aaae100`).
- **Completion Evidence:** Committed.

---

## ERA 3 — Repository Intelligence & CloneCast-Validation Infrastructure (Phase 1A–1Y, untagged)

This era is AutoCorp CLI turning its own capabilities toward analyzing
itself and, later, safely validating an external target repository
(CloneCast, at `/home/larry/clonecast`) without ever mutating its
production state. Phase IDs below are taken verbatim from each module's own
docstring header.

### Phase 1A — Repository Scanner
- **Purpose:** Read-only repository scan (git branch/status, Python
  version, file counts, TODO/FIXME/pass/NotImplementedError markers), with
  every value computed at run time — no hardcoded numbers.
- **Deliverables:** `brains/scanner.py`, `scan` CLI subcommand.
- **Testing:** `tests/test_scanner.py`, `tests/test_scan_cli.py`.
- **Completion Evidence:** Committed (`ab43dd9`).

### Phase 1B — Project Intelligence Engine (Analyzer)
- **Purpose:** Go beyond raw counts to project-type detection, entry-point
  and dependency-file detection, test-framework detection, directory
  layout, code statistics, and an overall health/confidence assessment —
  reusing Phase 1A's scanner rather than duplicating its logic.
- **Deliverables:** `brains/analyzer.py`, `analyze` CLI subcommand.
- **Testing:** `tests/test_analyzer.py`, `tests/test_analyze_cli.py`.
- **Completion Evidence:** Committed — but notably shipped inside
  `aaae100 "chore: harden test suite and eliminate collection warnings"`
  rather than its own dedicated commit. This is accurate but unusual;
  recorded here so the git history isn't mistaken for missing this phase.

### Phase 1C — Project Action Planner
- **Purpose:** Convert Scanner + Analyzer evidence into a deterministic,
  prioritized, evidence-cited action plan (no model call, no randomness —
  action IDs are SHA-256 hashes of `priority:category:title`).
- **Deliverables:** `brains/project_planner.py`, `plan-project` CLI
  subcommand.
- **Completion Evidence:** Committed (`b32bc74`).

### Phase 1D — Safe Repair Executor
- **Purpose:** A deliberately narrow, deterministic repair executor — the
  only executable repair in this phase is creating an empty
  `requirements.txt` when no third-party imports are detected. Dry-run by
  default; requires `--approve` to write; atomic writes; automatic
  rollback on validation failure.
- **Deliverables:** `brains/repair_executor.py`, `repair` CLI subcommand.
- **Completion Evidence:** Committed (`fd01754`), corrected (`12d1d94
  "Phase 1D corrections - invalid action ID exit code, sqlite3 connection
  leaks, gitignore reports"`).
- **Notes:** No "Phase 1E" was found anywhere in commit history or module
  docstrings — the sequence goes directly from 1D to 1F in this
  repository's own evidence. Unable to determine why 1E is absent or what
  it might have covered.

### Phase 1F — Workspace Resolution (safe external repository targeting)
- **Purpose:** Resolve a `--repo` argument against safety rules (must be an
  absolute path, must exist, must resolve inside a Git working tree,
  symlink-loop-guarded) so later phases can safely target an external
  repository like CloneCast without ever silently falling back to
  AutoCorp's own repository.
- **Deliverables:** `brains/workspace.py`, `--repo` flag added to `scan`,
  `analyze`, `plan-project`, `repair`.
- **Completion Evidence:** Committed (`30ca9df`).

### Phase 1G — Provider Abstraction + AI Repair Proposal Engine
- **Purpose:** A thin provider layer over the existing engine registry for
  structured-JSON repair-proposal generation, plus the full proposal
  pipeline: evidence collection, secret-file exclusion, inline-secret
  redaction, prompt construction, strict response validation (path
  traversal / absolute paths / shell-command / git-command injection all
  rejected), SHA-256 cross-checks, and atomic JSON output — review-only,
  never applies a change.
- **Deliverables:** `brains/providers.py`, `brains/repair_proposal.py`,
  `propose-repair` CLI subcommand.
- **Completion Evidence:** Committed (`ce4d614`), plus fixes (`1339aaf
  "accept --provider ollama alias"`, `ea71d54 "harden proposal secret and
  provider safety"`).
- **Known gaps (from a real audit, not invented) — status corrected
  2026-07-29:** an AI-generated safety audit of this phase
  (`claude_phase_1g_audit.txt`, present but untracked in the repository)
  found and documented five issues. Re-verifying each against current
  `HEAD` (rather than trusting the untracked report's claims, which had
  gone stale — see `PROJECT_MEMORY.md`'s "Lessons learned" for why this
  matters) found: (1) the secret-file exclusion gap (compound filenames
  like `db_credentials.json`) — **already fixed** by `ea71d54`; (2) inline
  secret redaction gaps — **partially already fixed** by `ea71d54`
  (`api_key`/`AUTH_TOKEN`/`client_secret`/`AWS_SECRET_ACCESS_KEY`-style
  keys), with the two remaining gaps (`DB_PASSWORD`-style compound keys,
  JSON-quoted `"Authorization": "Bearer ..."`) **fixed this session**; (3)
  `--provider claude`'s `TypeError` at construction — **already fixed** by
  `ea71d54`; (4) `--provider deepseek`'s model-tag conflation — **already
  fixed** by `ea71d54` (the no-key path now returns a clean blocked result
  before ever constructing the engine); (5)
  `tests/test_provider_contracts.py::test_no_silent_fallback` — **no
  longer exists**; `ea71d54` replaced it with four narrower regression
  tests that all pass even with an ambient `DEEPSEEK_API_KEY`. See
  `NEXT_STEPS.md` "Known bugs" for the full, per-item evidence trail (git
  commit, direct verification command, and test name) — none of these five
  audit findings represent a currently-open bug as of this session's end.

### Phase 1H + 1I — Live Application Readiness Scanner
- **Purpose:** Determine whether a target application is ready to be
  live-tested (dependencies, service reachability) before ever starting it,
  requiring real production evidence for its findings rather than static
  assumptions.
- **Deliverables:** `brains/live_readiness.py`, `live-readiness` CLI
  subcommand.
- **Completion Evidence:** Committed (`8a76073`), hardened (`c6c7cc8
  "harden readiness scanning against binary dependencies"`, `8316a80
  "require production evidence for readiness findings"`).
- **Notes:** An untracked report (`clonecast_live_readiness_report.txt`)
  exists in the working tree from a real run of this phase against
  CloneCast; not independently reproduced for this document.

### Phase 1J–1L — Controlled Live Application Test
- **Purpose:** Safely start a target FastAPI application (CloneCast),
  poll for readiness, retrieve its full OpenAPI schema, classify every
  route by mutation risk, and propose a disposable end-to-end test plan —
  all without ever calling a mutating route, verified by SHA-256-comparing
  the target's production database before and after.
- **Deliverables:** `brains/live_test.py`, `live-test` CLI subcommand.
- **Completion Evidence:** Committed (`43c34d6`), fixed (`5678688 "resolve
  verified web server launch targets"`).

### Phase 1M–1S — Disposable Workflow Test
- **Purpose:** Execute a real, end-to-end CloneCast episode-production
  workflow (studio → character → episode → session → conversation →
  dialogue generation → voice rendering → conversation assembly → episode
  assembly) inside a fully disposable copy of CloneCast's database and
  runtime directories, driven through CloneCast's real HTTP routes and
  services (OpenAPI-schema-driven request construction; no direct SQL, no
  mocks), requiring `--disposable` to run at all.
- **Deliverables:** `brains/workflow_test.py`, `workflow-test` CLI
  subcommand.
- **Testing:** `tests/test_workflow_character_id_propagation.py`.
- **Completion Evidence:** Committed across thirteen commits (`8ebee4c`
  through `307d2f5`), the last of which is the current `HEAD`'s
  great-grandparent-equivalent in the repair-focused commit sequence.
- **Notes:** Real verified runs of this phase (see `CHANGELOG_AI.md` for
  the session-level account) produced a real ~56-second episode MP3, with
  every generated WAV and both MP3 outputs independently re-verified via
  `ffprobe` and freshly computed SHA-256 (not just trusted from the
  database), `PRAGMA integrity_check`/`PRAGMA foreign_key_check` run
  against the disposable database, and the disposable directory confirmed
  removed afterward. One real, pre-existing, unrelated finding surfaced
  during this verification: CloneCast's production database currently has
  9 foreign-key constraint violations in its legacy chapter-script tables,
  present before and unaffected by this phase's own tables.

### Phase 1X — CloneCast Production Episode Validation
- **Purpose:** Extend Phase 1M–1S with independent SHA-256 + `ffprobe`
  verification of every generated artifact (not just the final episode
  MP3), `PRAGMA integrity_check`/`PRAGMA foreign_key_check` database
  verification with expected-table row checks, and actual disposable-
  directory cleanup with verification (the prior implementation created
  temp directories but never removed them, and several early-failure exit
  paths skipped cleanup entirely).
- **Deliverables:** Additions to `brains/workflow_test.py` (new
  `AudioArtifactRecord.sha256`/`DatabaseVerification` fields, `_verify_artifact`,
  `_verify_database` helpers) and to `autocorp.py`'s `cmd_workflow_test`
  (new report sections, `phase_1x_report.txt` output).
- **Testing:** No dedicated new unit tests were added for the new helpers
  in this specific phase beyond what Phase 1M–1S's test file already
  covers; verification was performed via real runs (see below).
- **Verification:** Two independent real runs against CloneCast both
  reached `DISPOSABLE_WORKFLOW_COMPLETE` with all artifacts verified,
  production database/git state independently confirmed unchanged, and the
  disposable directory confirmed removed.
- **Completion Evidence:** **Committed** as `ecf6a11 "feat: complete Phase
  1X/1Y CloneCast production/publishing validation"` (2026-07-29),
  by explicit repository-owner decision, after this session re-reviewed the
  full diff for accidental/unrelated content (none found) and completeness
  (all new helpers exercised by tests, both CLI commands correctly refuse
  to run without `--disposable`). A generated report (`phase_1x_report.txt`)
  exists on disk but is excluded from git by `.gitignore`'s
  `phase_*_report.txt` pattern, so it will never appear in `git status`
  regardless of whether the underlying code is committed.
- **Notes:** Per `PHASE_COMPLETION_POLICY.md`, this phase's AutoCorp-side
  code is now committed and its real-run verification is documented above.
  It has never reached a full end-to-end PASS, but that is solely because
  of the external CloneCast audio-clipping defect (see Phase 1Y below and
  `NEXT_STEPS.md`), not an AutoCorp-side gap.

### Phase 1Y — Production Publishing Validation
- **Purpose:** Reuse Phase 1X's disposable workflow to produce a real
  completed episode, then continue through every publishing stage
  CloneCast supports (QC, human review, release readiness, release
  packaging, local publication, and platform "export" for all 5 named
  destinations: Spotify RSS, Rumble, YouTube, TikTok, Facebook/Instagram)
  up to but never past the real external-upload boundary, with a
  structured PASS/WARNING/FAIL publishing-readiness verdict and an
  external-dependency (credentials/endpoint) check that makes no network
  call.
- **Deliverables:** Further additions to `brains/workflow_test.py`
  (`_create_and_run_qc`, `_evaluate_qc_checks`,
  `_check_external_publishing_dependencies`, `PublishingFinding`,
  `ExternalDependencyStatus`, 15 new stages) and to `autocorp.py`'s
  `cmd_publish_test` (new, plus a shared report renderer), `publish-test`
  CLI subcommand.
- **Verification:** Confirmed, by direct source inspection during this
  phase's own research, that CloneCast's `destination_type` column is
  database-CHECK-constrained to the literal string `'local'` — there is no
  code path in CloneCast, committed or otherwise, capable of a real
  external upload. Two independent real runs both reached the QC stage and
  were correctly blocked there by CloneCast's own QC logic, which detected
  real audio peak-clipping (`ConversationAssemblyError: master conversation
  audio has severe clipping` in the quick-podcast case; a blocking
  `wav_peak_clipping` QC check failure in this phase's own runs) — a
  reproducible, CloneCast-side finding confirmed four times across two
  different phases and four separate real runs. No run in this phase has
  yet reached a fully successful PASS through packaging/publication.
- **Completion Evidence:** **Committed**, same commit as Phase 1X
  (`ecf6a11`). A generated report (`phase_1y_report.txt`) exists but is
  gitignored.
- **Notes:** Per `PHASE_COMPLETION_POLICY.md`, this phase's AutoCorp-side
  code is committed, tested, and verified by real runs. It has real, if
  incomplete (blocked by a genuine external CloneCast finding, not an
  AutoCorp defect), end-to-end verification — a full PASS through
  packaging/publication has never been observed, and won't be until
  CloneCast's own audio-clipping issue is fixed on that side.

---

## ERA 4 — Quick Podcast (committed module, uncommitted integration)

### Quick Podcast — real disposable episode generation for local listening
- **Purpose:** A `quick-podcast` command that produces a complete,
  real, disposable CloneCast episode a user can actually listen to, using
  the same disposable-workspace safety model as Phase 1M–1S/1X.
- **Completion Evidence:** The original implementation (an embedded
  `python -c "<thousands of lines>"` subprocess) was never committed to
  git at all — it existed only as an untracked working-tree file before
  this session's refactor.
- **Notes:** See the next entry — this phase's *implementation* and its
  *observability refactor* are, in this repository's evidence, effectively
  the same event, since no prior committed version exists to compare
  against.

### Quick Podcast Observability Refactor
- **Purpose:** Move the embedded `python -c` worker into a real, importable
  module (`brains/quick_podcast_runner.py`, invoked as `python -m
  brains.quick_podcast_runner`), and add structured, immediately-flushed
  progress reporting (phase transitions, blueprint-section progress,
  Ollama retry reporting, per-turn voice-rendering progress via a
  background thread polling the disposable database read-only) plus a
  persistent, `tail -f`-able log file at `/tmp/autocorp_quick_podcast.log`
  — explicitly *not* a change to generation behavior, quality, or
  validation.
- **Deliverables:** `brains/quick_podcast.py` (thin orchestrator),
  `brains/quick_podcast_runner.py` (the worker), `tests/test_quick_podcast.py`,
  `tests/test_quick_podcast_runner.py`.
- **Testing:** 33 new unit tests, all passing; full suite 933 passed / 1
  xfailed / 0 failed at time of commit.
- **Verification:** Two real end-to-end runs against CloneCast (topics
  "Nikola Tesla" and "Marie Curie") both confirmed live progress printing
  correctly for 8 of 12 phases, the persistent log file updating
  continuously during execution (checked mid-run, not just after), and
  correct, clean failure reporting (not a crash) when both runs hit the
  same real CloneCast-side audio-clipping error described under Phase 1Y.
  Production database and git state were independently confirmed unchanged
  after both runs.
- **Completion Evidence:** **Committed** as `143825a "refactor: modularize
  quick podcast runner and add live progress reporting"` — module-level
  code only, at the time of that commit. The `quick-podcast` CLI
  subcommand registration was uncommitted for five further days until the
  repository owner explicitly authorized committing it (2026-07-29); it is
  now committed separately as `53f0d7d "feat: wire quick-podcast CLI
  subcommand"`. As of `53f0d7d`, a fresh `git clone` of this repository at
  `HEAD` DOES expose `quick-podcast` as a runnable command.
- **Notes:** This was, for five days, the concrete example cited
  throughout this documentation system of "committed, but partially wired"
  (Tier 3 in `PHASE_COMPLETION_POLICY.md`); it has since moved to Tier 2
  ("committed, untagged"). Kept here as a record of that history, per
  `DOCUMENTATION_POLICY.md`'s "do not delete a phase's entry once written;
  correct it in place."

---

## ERA 5 — Reliability Engine (entirely uncommitted)

### Reliability Engine
- **Purpose:** No commit references this subsystem (still true as of
  2026-07-29). A full read-only investigation of its actual source was
  performed this date, at the repository owner's request, to produce an
  integration proposal. Confirmed purpose (from reading the code, not
  inferring from filenames): a second, parallel build-and-repair
  orchestration pipeline, structurally analogous to
  `core/orchestrator.py::Session` but not composed with it — see
  `ARCHITECTURE.md`'s "Reliability Engine" section for the full
  architecture, risk findings, and staged integration plan. It reimplements
  (rather than reuses) the existing pipeline's self-heal/retry/repair-budget
  logic, and its own true end-to-end entry point
  (`ReliabilityOrchestrator.run()`) is never exercised by its own test
  suite — the 59 (now 60) passing tests prove unit-level correctness of
  individual modules, not integrated behavior. **Update, same day:** every
  finding above was independently re-verified against the source (not
  re-stated at face value) and two were fixed in the working tree — a
  worktree-ID collision that could destroy a preserved blocked-subtask's
  diagnostic state, and the `model_router.py`/`brains/model_router.py`
  filename collision (renamed to `model_availability.py`). A third
  finding, that a missing lint/type-check tool blocks every edit, was
  re-verified and found **incorrect** for the actual code path used. See
  `ARCHITECTURE.md` and `PROJECT_MEMORY.md` for full detail. The subsystem
  remains entirely uncommitted; integration remains unauthorized.
- **Deliverables (working tree only):** `reliability_engine/` (16 modules,
  2,346 lines), `reliability_config.yaml`, `mypy.ini`, `ruff.toml`,
  `tests/test_reliability_engine.py` (1,304 lines), plus supporting,
  uncommitted changes to `brains/builder.py` (an `EDIT_DIFF_SYSTEM_PROMPT`
  and `generate_edit_diff`/`edit_diff_prompt` methods), `memory/store.py`
  (new `subtasks`, `attempts`, and `known_issues` tables), and
  `requirements.txt`/`requirements-dev.txt` (adding `chromadb`, `PyYAML`,
  `mypy`, `ruff`).
- **Testing:** `tests/test_reliability_engine.py` passes as part of the
  current full test suite run (it is collected automatically by
  `pytest.ini`'s `testpaths = tests`).
- **Completion Evidence:** **Entirely uncommitted.** `grep` for
  `reliability_engine` across every tracked and untracked `.py` file in
  this repository finds exactly one reference outside the package itself:
  its own test file. It is not imported by `autocorp.py`, not imported by
  any `brains/*.py` module, and has no CLI entry point. It has passing
  tests but is not integrated into the production application in any way.
- **Notes:** Two branches, `reliability/subtask-1` and
  `reliability/subtask-2`, exist and share the name, both pointing at the
  same old commit (`1615cf8`, dated 2026-07-24) with zero commits of their
  own ahead of `main`. **Corrected 2026-07-29:** these are not unrelated —
  `reliability_engine/worktree_sandbox.py` itself creates real git worktree
  branches named exactly `reliability/subtask-{id}`. These two branches are
  almost certainly leftover artifacts from an earlier real run of this
  subsystem's code (consistent with `data/chroma/` also existing on disk —
  `rag_index.py`'s default Chroma persist path), where `rollback()`'s
  branch cleanup was not reached. They are stale and behind `main`, not a
  separate origin for the working-tree subsystem, but they are evidence
  this code has been executed live at least once outside the test suite.

---

## FUTURE PLANNING REQUIRED

No phase beyond Phase 1Y, the Quick Podcast CLI-wiring commit, and the
Reliability Engine's integration is described anywhere in this repository
— no docstring, no commit, no branch, no report. Specifically:

- What comes after Phase 1Y (assuming CloneCast's audio-clipping issue is
  someday resolved and a full publish-pipeline PASS is achieved) is not
  defined anywhere in the repository.
- Whether/how the Reliability Engine is meant to be wired into
  `autocorp.py` is not defined anywhere in the repository.
- Whether AI Repair Proposal's known gaps (Phase 1G, above) are intended to
  be fixed before or independent of any other future phase is not stated.

Do not invent phases to fill these gaps. See `ROADMAP.md`'s
`FUTURE PLANNING REQUIRED` section and `NEXT_STEPS.md` for what can
honestly be said about immediate next actions instead.
