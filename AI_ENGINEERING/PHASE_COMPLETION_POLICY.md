# Phase Completion Policy

This document defines the only conditions under which a phase of work in
this repository may be marked complete, and who has the authority to do so.

---

## The core rule

**A phase is complete only when the repository owner says it is.**

Everything else in this document defines the evidence an AI engineer must
assemble and present before that approval can reasonably be requested. No
AI engineer marks a phase complete unilaterally, regardless of how much
evidence has been gathered — evidence supports a recommendation, it is not
itself the approval.

## Evidentiary tiers

Repository evidence for a phase falls into one of these tiers. Be explicit
about which tier applies — do not round up.

1. **Committed and tagged.** The code is in `git log`, and a release tag
   covers it. In this repository, this tier currently covers only the
   "SQLite Generation" phases (`v0.1.0` through `v0.10.5` — see
   `PHASES.md`). No AI session has extended tagging past `v0.10.5`; that is
   an owner decision.
2. **Committed, untagged.** The code is merged to `main` via a real commit,
   the full test suite passes against it, but no release tag marks it. Most
   of this repository's current functionality (the repair/self-healing
   pipeline, the Phase 1A–1S CloneCast-testing infrastructure, the
   quick-podcast observability refactor) is in this tier.
3. **Committed, but partially wired.** The underlying module is committed,
   but its CLI entry point or another integration point is not (verified
   by diffing `git show HEAD:autocorp.py` against the working tree — this
   repository has at least one concrete current example: `quick_podcast.py`
   and `quick_podcast_runner.py` are committed as of `143825a`, but the
   `quick-podcast` CLI subcommand registration in `autocorp.py` is not).
   Do not describe this tier as "shipped."
4. **Uncommitted, working tree only.** Real files exist and may even have
   passing tests, but nothing has been committed. This repository currently
   has two examples: the Phase 1X/1Y extensions to
   `brains/workflow_test.py` (and the corresponding CLI wiring), and the
   entire `reliability_engine/` subsystem plus its supporting changes to
   `brains/builder.py`, `memory/store.py`, and the requirements files.
   **Nothing in this tier may be described as complete or shipped, no
   matter how thoroughly it was verified in-session.**
5. **Verified by a report, not reproduced.** A report file (e.g.
   `claude_phase_1g_audit.txt`, `phase_1x_report.txt`) claims a result. This
   is evidence that *someone* ran a check and recorded an outcome — it is
   not evidence that the outcome still holds unless you reproduce it
   yourself in the current session. Treat an unreproduced report the way
   you would treat any other second-hand claim.
6. **Claimed only.** A commit message, a docstring, or a task description
   says something is done. This is not evidence on its own.

## What "complete" requires, concretely

For an AI engineer to recommend that a phase be marked complete, all of the
following must be true and stated explicitly:

- The relevant code is committed (tier 1 or 2 above). If it is not, say
  "uncommitted" plainly rather than describing the feature as finished.
- The full test suite passes, and you report the exact numbers you
  observed by running it yourself in the current session — not a number
  copied from an old report.
- Every capability claimed for the phase has a corresponding, currently
  passing test, or an explicit note that a given capability could not be
  automatically tested and why (e.g. it requires a real GPU/external
  service, and describe what real-run verification was performed instead).
- Any real-world verification the phase depends on (a live run against
  CloneCast, a real Ollama/Chatterbox invocation) was actually performed in
  a way you can point to (a report you generated, not one you're trusting),
  and any failures it surfaced are disclosed, not omitted.
- Production safety was independently verified where applicable (target
  system's database hash and git status unchanged, checked by you, not
  assumed from the code's intent).

If any of these is missing, the correct statement is: "This phase is
implemented but not yet verified as complete," or "This phase is
uncommitted," or "This phase's real-world verification is blocked by
[specific, named reason]" — not "complete."

## Handling conflicting evidence

If a commit message, a report, and the actual test/run results disagree,
do not resolve the conflict by picking the most favorable one. Report all
three, say which you were able to independently verify, and let the
repository owner decide what it means for phase status.

Concrete example already present in this repository: multiple independent
real runs of the Phase 1X/1Y workflow and the quick-podcast verification
all encountered the same CloneCast-side audio-clipping error
(`ConversationAssemblyError: master conversation audio has severe
clipping`) rather than reaching a full successful completion. The correct
report is exactly that — a real, reproducible, external blocker — not a
claim that the AutoCorp-side work is "done" because everything up to that
point worked, and not a claim that the work is "broken" because the target
system currently rejects the audio it produces. Both facts are true at once
and both must be stated.

## Phases with no future scope defined

If a phase has been completed (per the tiers above) and there is no
repository evidence — no docstring, no commit, no report, no branch — for
what comes next, do not invent the next phase. Place it under
`FUTURE PLANNING REQUIRED` in `ROADMAP.md` and say plainly that the
repository does not yet define it.
