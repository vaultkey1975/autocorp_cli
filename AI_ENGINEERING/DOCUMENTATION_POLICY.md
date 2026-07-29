# Documentation Policy

This document explains every file in `AI_ENGINEERING/`: its purpose, who
owns it, when it must be updated, and what it must contain. If you change
something in the repository that makes one of these documents inaccurate,
updating that document is part of finishing the work — not a follow-up task.

---

## BOOTSTRAP_PROMPT.md

- **Purpose:** the exact prompt a human pastes to onboard a new AI session
  onto this repository.
- **Owner:** repository owner; AI engineers may propose edits but should
  not unilaterally rewrite its instructions.
- **Update when:** the required reading order changes, a new document is
  added to `AI_ENGINEERING/`, or a past failure mode (like the ones
  currently listed) is resolved and a new one is discovered.
- **Must contain:** the literal prompt text, and a short "why this exists"
  note grounded in a real, specific incident from this repository's history.

## AI_ENGINEERING_CONSTITUTION.md

- **Purpose:** the permanent, session-independent rules every AI engineer
  operates under in this repository.
- **Owner:** repository owner. This document changes rarely and
  deliberately — it is not a place to record task-specific decisions.
- **Update when:** a genuinely new, permanent rule is established (e.g. a
  new class of safety check becomes mandatory), not for one-off findings.
- **Must contain:** Mission, Repository Safety, Source of Truth, Evidence
  Requirements, Definition of Done, Testing Standards, Documentation
  Standards, Git Standards, Regression Prevention, Architecture Rules,
  Coding Standards, Production Rules, Future AI Compatibility, and
  Documentation Maintenance sections.

## ENGINEERING_CHECKLIST.md

- **Purpose:** the mandatory, mechanical checklist completed before any
  unit of work is claimed finished.
- **Owner:** shared — any AI engineer should tighten it when they discover
  a way work was claimed "done" without actually being verified.
- **Update when:** a new failure mode is discovered that the existing
  checklist would not have caught (this is exactly how several of its
  current items were derived — from real gaps found in this repository's
  own recent work, e.g. resource leaks only visible under `-W error`).
- **Must contain:** checkable items grouped by before/during/after work,
  plus a "red flags" section naming the specific rationalizations that
  precede an inaccurate "done" claim.

## PHASE_COMPLETION_POLICY.md

- **Purpose:** the exact evidentiary bar a phase must clear before it can
  be marked complete, and who has authority to mark it so.
- **Owner:** repository owner has final authority; this document defines
  the criteria, not the approval itself.
- **Update when:** the evidence bar itself needs to change (rare) — not
  every time a phase is evaluated against it.
- **Must contain:** the definition of "complete" vs. "implemented but
  unverified" vs. "uncommitted," and the explicit rule that only the
  repository owner approves phase completion.

## ROADMAP.md

- **Purpose:** vision, architecture summary, and phase-level status at a
  glance — completed, current, and repository-evidenced remaining phases.
- **Owner:** shared; must be updated whenever a phase's status changes.
- **Update when:** a phase completes (per `PHASE_COMPLETION_POLICY.md`), a
  new phase begins, or repository evidence changes what "remaining" means.
- **Must contain:** Vision, Architecture (summary — detail lives in
  `ARCHITECTURE.md`), Completed Phases, Current Phase, Remaining Phases
  (only if repository-evidenced), and a `FUTURE PLANNING REQUIRED` section
  for anything not yet evidenced.

## PHASES.md

- **Purpose:** the detailed, per-phase historical and structural record —
  the "master phase document."
- **Owner:** shared; append/update as phases progress. Do not delete a
  phase's entry once written; correct it in place if evidence changes.
- **Update when:** a phase starts, its scope changes, or it completes.
- **Must contain:** for every repository-supported phase — Phase ID, Name,
  Purpose, Goals, Requirements, Dependencies, Deliverables, Testing,
  Verification, Exit Criteria, Completion Evidence, and Notes (including
  any conflicting evidence found).

## CURRENT_PHASE.md

- **Purpose:** a single, always-current snapshot of what is actually being
  worked on right now, and what is blocking it.
- **Owner:** shared; this is the document most likely to go stale, so
  updating it is a required step of finishing any task, not optional.
- **Update when:** every single session that changes repository state.
- **Must contain:** Current Phase, Status, Objective, Known Blockers, Next
  Phase.

## ARCHITECTURE.md

- **Purpose:** how the system is actually built today — not how it was
  originally designed, not how a future phase might redesign it.
- **Owner:** shared; update whenever a structural change is made (a new
  module added, a subsystem wired in or disconnected, a data flow changed).
- **Update when:** any change to directory structure, CLI surface, data
  flow, or a named subsystem (repair engine, memory system, workflow
  engine, planner, reliability engine).
- **Must contain:** system architecture, directory structure, CLI
  architecture, memory system, repair engine, workflow engine, planner,
  reliability engine, data flow, extension points, performance
  considerations, security considerations.

## PROJECT_MEMORY.md

- **Purpose:** the institutional memory that would otherwise be lost
  between sessions — decisions made and why, mistakes made and their cost,
  patterns proven to work and patterns proven not to.
- **Owner:** shared; every AI engineer who learns something the hard way
  should add it here so the next one doesn't re-learn it the hard way too.
- **Update when:** a non-obvious decision is made, a real bug or regression
  is found and fixed, or a pattern is validated or invalidated by evidence.
- **Must contain:** lessons learned, engineering decisions, architecture
  decisions, patterns to reuse, patterns never to repeat, database
  decisions, production rules, known pitfalls.

## ENGINEERING_RULES.md

- **Purpose:** concrete, enforceable coding/testing/documentation standards
  — the "how we write code here" reference.
- **Owner:** shared; tighten when a violation is found in review.
- **Update when:** a new class of defect is found that the rules didn't
  already cover.
- **Must contain:** no-placeholders/no-TODO/no-FIXME/no-fake-implementation
  rules, no-fake-scan/no-fake-verification/no-fake-success rules,
  repository safety, coding standards, testing standards, documentation
  standards, Definition of Done.

## CHANGELOG_AI.md

- **Purpose:** a chronological, evidence-based summary of engineering
  history, grounded in git log — a readable index into `git log`, not a
  replacement for it.
- **Owner:** shared; append, never rewrite history.
- **Update when:** any session that changes repository state, whether or
  not it results in a commit.
- **Must contain:** dated entries summarizing what changed and citing the
  commits/evidence, including entries for uncommitted work in progress.

## NEXT_STEPS.md

- **Purpose:** the live punch list — what to do next, what's broken, what's
  blocked, and what's missing.
- **Owner:** shared; this is the second most likely document to go stale
  (after `CURRENT_PHASE.md`) and must be updated in the same session that
  resolves or discovers an item.
- **Update when:** every session.
- **Must contain:** immediate work, technical debt, known bugs, future
  improvements, blocked work, missing dependencies.

---

## General rules for all documents

1. Every factual claim must be traceable to repository evidence: a file, a
   commit, a test, a report. If it isn't, write "Unable to determine from
   repository evidence."
2. Do not describe uncommitted work as complete, and do not describe a
   disconnected/unintegrated subsystem (present in the working tree but not
   imported by production code) as part of the shipped feature set.
3. When two documents would disagree after your change, fix both in the
   same session. Cross-document contradictions are exactly the failure mode
   this system exists to prevent.
