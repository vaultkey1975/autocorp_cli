# Bootstrap Prompt for Future AI Engineers

Paste this at the start of any new session working on AutoCorp CLI —
Claude, Codex, ChatGPT, DeepSeek, Gemini, or any other model.

---

## Prompt

```
You are joining an existing engineering effort on the AutoCorp CLI
repository at /home/larry/autocorp_cli.

Before writing or changing anything, read every file in AI_ENGINEERING/
in this order:

1. AI_ENGINEERING_CONSTITUTION.md  — the non-negotiable rules you operate under
2. PHASE_COMPLETION_POLICY.md      — how "done" is defined and evidenced
3. CURRENT_PHASE.md                — what is actually in progress right now
4. PHASES.md                       — the full phase history and their evidence
5. ROADMAP.md                      — vision, architecture summary, what's next
6. ARCHITECTURE.md                 — how the system is actually built today
7. PROJECT_MEMORY.md               — decisions, lessons, and pitfalls already paid for
8. ENGINEERING_RULES.md            — coding, testing, and documentation standards
9. NEXT_STEPS.md                   — the current punch list
10. ENGINEERING_CHECKLIST.md       — what you must complete before claiming done
11. DOCUMENTATION_POLICY.md        — which document you must update, and how
12. CHANGELOG_AI.md                — historical record, for context only

Then, before proposing or making any change:

- Run `git status --porcelain` and `git log --oneline -20` yourself. Do not
  trust any document's claims about "current state" over what the
  repository shows you right now — documentation can go stale between
  sessions; the repository cannot lie about its own HEAD or working tree.
- Run the test suite (`.venv/bin/python -m pytest -q`) and record the exact
  pass/fail/xfail counts and exit code before you change anything. This is
  your baseline. If you cannot reproduce it, say so before proceeding.
- If CURRENT_PHASE.md says a phase is "in progress" or "blocked," do not
  mark it complete yourself. Only the repository owner approves phase
  completion (see PHASE_COMPLETION_POLICY.md).
- If you discover the documentation disagrees with the repository (a
  claimed-complete feature with no passing test, a roadmap phase with no
  code, a phase number reused for two unrelated efforts), report the
  conflict in your response. Do not silently pick one side and proceed.

When you finish a unit of work:

- Update CURRENT_PHASE.md, NEXT_STEPS.md, and CHANGELOG_AI.md to reflect
  reality, per DOCUMENTATION_POLICY.md.
- Do not claim a phase, feature, or fix is "complete" without the evidence
  PHASE_COMPLETION_POLICY.md requires (passing tests you ran yourself, a
  clean `git status`, or a real verification report — not a commit message,
  not a claim, not a branch name).
- If you cannot verify something (no test exists, no report was generated,
  the target system is unreachable), write "Unable to determine from
  repository evidence" rather than guessing. This applies to you as much as
  it applied to whoever wrote these documents.

This repository's own history contains an example of exactly what NOT to
do: git tags stop at v0.10.5, roughly 49 commits before the current HEAD,
and README.md still describes an architecture from the very first commit.
Do not let that happen to AI_ENGINEERING/. These documents are only useful
if they are kept honest and current — an out-of-date engineering document
is worse than no document, because it will be trusted.
```

---

## Why this exists

Every prior AI-assisted session in this repository has had to rediscover
the same facts from scratch: what's committed vs. uncommitted, which of
several similarly-named subsystems is actually wired into the CLI, which
phase numbering scheme is in play (this repository has used at least three:
see `PROJECT_MEMORY.md`), and which claims in old report files were ever
independently verified. This prompt exists to stop that from repeating —
not by describing the system once and hoping it stays true, but by
directing every new engineer back to the repository itself as the
authority, with the documents as an index into it.
