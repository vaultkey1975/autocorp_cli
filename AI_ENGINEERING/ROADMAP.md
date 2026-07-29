# Roadmap

For full per-phase detail (Purpose, Goals, Requirements, Dependencies,
Deliverables, Testing, Verification, Exit Criteria, Completion Evidence,
Notes), see `PHASES.md`. This document is the at-a-glance summary.

---

## Vision

Per `README.md` (accurate for the original architecture, though the
repository has grown well beyond it — see `PROJECT_MEMORY.md`): AutoCorp
CLI is a local, terminal-first AI coding assistant that plans, builds,
tests, and explains code, learns from past builds, and is architected so
that command approval can later be delegated to an external gate ("Agent
Watchdog," referenced in `safety/watchdog_gate.py` and the README, with no
further evidence in this repository of that integration's own
implementation — it is a plug-in seam, not a built subsystem here).

The repository's evidenced trajectory since that original vision has been:
1. Make the code-generation engine pluggable (multiple model backends).
2. Build a real repair/self-healing loop around generated projects.
3. Turn AutoCorp's own analysis tooling (scanner, analyzer, planner)
   inward on itself, then outward at a real external target repository
   (CloneCast) — always read-only or disposable-workspace-isolated.
4. Prove, via real runs against that real external target, that AutoCorp
   can safely drive a complete content-production pipeline (a podcast
   episode) end-to-end, and validate its publishing pipeline up to (never
   past) a real external upload boundary.

## Architecture (summary)

See `ARCHITECTURE.md` for full detail. In brief:

```
autocorp.py (CLI / argparse)
  ├── core/            orchestrator, console, Ollama client
  ├── brains/          37 tracked .py files: original 4 brains, the engine
  │                    abstraction + repair/self-healing pipeline, and the
  │                    Phase 1A–1Y repository-intelligence /
  │                    CloneCast-validation infrastructure
  ├── memory/          SQLite-backed build/lesson memory (store.py)
  ├── safety/          Executor + CommandGate + WatchdogGate seam
  └── reliability_engine/   UNCOMMITTED, unintegrated — see PHASES.md Era 5
```

## Completed phases

Tagged and released (`v0.1.0`–`v0.10.5`): the original four-brain
architecture and the full SQLite Generation code-template feature
(Phases 1–7). See `PHASES.md`, Era 1.

Committed, untagged, with passing tests: the engine abstraction and
repair/self-healing pipeline (Era 2); Phases 1A through 1M–1S of the
repository-intelligence and CloneCast-validation infrastructure, including
real verified runs against CloneCast (Era 3); the Quick Podcast module
itself (Era 4, module-level only — see below).

**None of Era 2, 3, or 4 is tagged as a release.** Whether that matters is
an owner decision, not one this document makes.

## Current phase

See `CURRENT_PHASE.md` for the live snapshot. In summary as of this
document's writing: the repository's working tree has uncommitted changes
spanning at least three distinct, unrelated efforts (Phase 1X/1Y
publishing-validation extensions, the Quick Podcast CLI wiring, and the
entirely separate, unintegrated Reliability Engine). **The current phase
is not complete and should not be described as such.**

## Remaining phases (repository evidence only)

The only "remaining" work with direct repository evidence is what is
already sitting, unfinished, in the working tree:

1. **Commit or discard the Phase 1X/1Y workflow-test extensions** and the
   matching `autocorp.py` CLI wiring (`workflow-test`'s new report
   sections, the `publish-test` subcommand).
2. **Commit or discard the `quick-podcast` CLI wiring** in `autocorp.py` —
   the module it depends on is already committed and inert without it.
3. **Decide the fate of the Reliability Engine** — integrate it (wire it
   into `autocorp.py`, document its purpose properly, add it to
   `ARCHITECTURE.md`) or explicitly defer/remove it. It currently has
   passing tests but no production entry point and no commit.
4. **Address the five documented Phase 1G gaps** (secret-file exclusion,
   inline-secret redaction, the `--provider claude` constructor bug, the
   `--provider deepseek` model-tag conflation, and the
   `test_no_silent_fallback` environment-isolation gap) — all recorded
   with specifics in `PHASES.md` and `NEXT_STEPS.md`.
5. **Investigate the CloneCast audio-clipping finding** — reproduced four
   times across two phases and four real runs
   (`ConversationAssemblyError: master conversation audio has severe
   clipping` / a blocking `wav_peak_clipping` QC check). This blocks any
   full end-to-end PASS of Phase 1Y and of `quick-podcast`. Per this
   repository's own rules, this is CloneCast's issue to fix, not
   AutoCorp's — AutoCorp's job here is limited to detecting and reporting
   it accurately, which the evidence shows it already does correctly.

## FUTURE PLANNING REQUIRED

Beyond the five items above, this repository contains **no evidence** —
no docstring, no commit, no branch, no report — describing what comes
next. Specifically unknown from repository evidence:

- What a "Phase 1Z," a "Phase 2," or any successor to Phase 1Y would cover.
- Whether the SQLite Generation template feature (Era 1) is expected to
  receive further phases, or is considered finished as of `v0.10.5`.
- Whether Agent Watchdog (referenced as a future integration point in
  `README.md` and `safety/watchdog_gate.py`) is still an intended near-term
  target or a long-deferred one.
- Any roadmap item beyond fixing what already exists but is broken,
  uncommitted, or unintegrated.

Do not invent phases to fill this gap. If asked to plan future work, say so
explicitly and ask the repository owner, rather than presenting invented
roadmap items as if they were repository-evidenced.
