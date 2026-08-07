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
5. Coordinate all of the above through a read-only Autonomous Engineering
   Manager that tells an engineer what to do next from repository evidence.
6. Discover unknown repositories before management/planning by building an
   evidence-backed repository profile even when no `AI_ENGINEERING/`
   documents exist.
7. Inspect running applications safely, from disposable source copies, so
   AutoCorp can answer what actually launches, responds, and fails at
   runtime.

## Architecture (summary)

See `ARCHITECTURE.md` for full detail. In brief:

```
autocorp.py (CLI / argparse)
  ├── core/            orchestrator, console, Ollama client
  ├── brains/          41 tracked .py files after the Live Inspector change:
  │                    original
  │                    brains, the engine
  │                    abstraction + repair/self-healing pipeline, and the
  │                    Phase 1A–1Y repository-intelligence /
  │                    CloneCast-validation infrastructure, Chat, the
  │                    Autonomous Engineering Manager, Universal
  │                    Repository Discovery, and Live Application Inspector
  ├── memory/          SQLite-backed build/lesson memory (store.py)
  ├── safety/          Executor + CommandGate + WatchdogGate seam
  └── reliability_engine/   committed source/tests; dedicated CLI
                            integration remains owner-defined
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

See `CURRENT_PHASE.md` for the live snapshot. Both Phase 2A (`34e7a4a`)
and Phase 2B (`b3fb054`) are committed and merged to `main`. Official
phase completion remains owner-gated by `PHASE_COMPLETION_POLICY.md`.

A planning-only audit (2026-08-07) evaluated AutoCorp Brain integration
readiness and proposed the next milestone: "AutoCorp Brain Integration —
Core Client Boundary." See `ARCHITECTURE.md` for full scope, exclusions,
and acceptance criteria. Implementation is NOT authorized.

The current working-tree task is this documentation audit — no code
changes are in progress.

## Remaining phases (repository evidence only)

The only "remaining" work with direct repository evidence:

1. ~~**Phase 1X/1Y**: continue iterating uncommitted~~ — **committed** as
   `ecf6a11 feat: complete Phase 1X/1Y CloneCast production/publishing
   validation`.
2. ~~Commit or discard the `quick-podcast` CLI wiring~~ — **done**,
   committed as `53f0d7d`.
3. **Reliability Engine integration** — staged steps through E2E
   verification and production hardening are committed. A dedicated
   Reliability Engine CLI command remains an owner/product decision; no
   such command exists yet.
4. ~~Address the five documented Phase 1G gaps~~ — **corrected 2026-07-29:
   four of five were already fixed** by a commit predating the audit
   report being trusted at face value; the fifth (inline-redaction) was
   fixed in `fad85a8`. See `PROJECT_MEMORY.md` for the full,
   re-verified account.
5. **Investigate the CloneCast audio-clipping finding** — reproduced four
   times across two phases and four real runs
   (`ConversationAssemblyError: master conversation audio has severe
   clipping` / a blocking `wav_peak_clipping` QC check). This blocks any
   full end-to-end PASS of Phase 1Y and of `quick-podcast`. Per this
   repository's own rules, this is CloneCast's issue to fix, not
   AutoCorp's — AutoCorp's job here is limited to detecting and reporting
   it accurately, which the evidence shows it already does correctly.
6. **Autonomous Engineering Manager** — implemented by `autocorp manage`
   and `brains/manager.py`, with tests for summary, roadmap, next-task,
   production readiness, AI recommendation, failure handling, CLI wiring,
   and Chat routing. Full local verification passed.
7. **Universal Repository Discovery Engine** — implemented by `autocorp
   discover`, `brains/discovery.py`, AutoCorp metadata storage for
   repository profiles, automatic manager discovery for unseen
   repositories, and Chat profile routes. Focused verification passed;
   full local verification passed. Official phase completion remains
   owner-gated by `PHASE_COMPLETION_POLICY.md`.
8. **Live Application Inspector** — implemented by `autocorp inspect`,
   `brains/live_inspector.py`, runtime startup from
   disposable source copies, safe HTTP/OpenAPI diagnostics, read-only
   SQLite integrity checks, CloneCast feature-state reporting, manager
   runtime prioritization, and Chat live-inspection routes. Focused
   verification, full local verification, and real CloneCast smoke
   verification have passed. Official phase completion remains owner-gated
   by `PHASE_COMPLETION_POLICY.md`.

## FUTURE PLANNING REQUIRED

Beyond the eight items above, this repository contains **no evidence** —
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
