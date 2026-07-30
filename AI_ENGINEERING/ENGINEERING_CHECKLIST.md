# Engineering Checklist

Complete this checklist before claiming any unit of work is finished. It is
mandatory, not advisory. If you cannot check an item honestly, the work is
not done — say so, and say what's blocking it.

---

## Before starting work

- [ ] Ran `git status --porcelain` and recorded what is currently modified
      or untracked, before touching anything.
- [ ] Ran `git log --oneline -10` and confirmed the current `HEAD` matches
      what `CURRENT_PHASE.md` claims.
- [ ] Ran the full test suite (`.venv/bin/python -m pytest -q`) and recorded
      the exact baseline: pass count, fail count, xfail count, exit code.
- [ ] Read `AI_ENGINEERING_CONSTITUTION.md` and `PHASE_COMPLETION_POLICY.md`
      if this is the first task of the session.
- [ ] Confirmed the scope of the task against `CURRENT_PHASE.md` and
      `NEXT_STEPS.md` — if the task asks for something `CURRENT_PHASE.md`
      lists as blocked or not-yet-reached, flagged that before proceeding.

## While working

- [ ] Every new claim about repository/system state is derived from a
      command you ran yourself in this session, not assumed from a prior
      document.
- [ ] No placeholder, stub, `TODO`, or `FIXME` was introduced into
      production code (`brains/`, `core/`, `memory/`, `safety/`,
      `autocorp.py`, `config.py`).
- [ ] No hardcoded value stands in for something that should be computed
      from real repository or target-system state.
- [ ] Any code touching a target system outside this repository (currently
      CloneCast) operates on a disposable copy, never production data.
- [ ] Any code capable of an external network side effect (upload, publish,
      paid API call) defaults to refusing without an explicit safety flag,
      and that refusal was verified, not assumed.
- [ ] Existing tests still pass; you did not silently work around a failing
      test instead of fixing the root cause.
- [ ] If you touched a helper function used by more than one module (grep
      for callers first), you confirmed every caller still behaves
      correctly.

## Before claiming "done"

- [ ] Ran the FULL test suite again (not just the tests for the file you
      changed) and recorded the exact pass/fail/xfail counts and exit code.
- [ ] Ran the maintained-source compile verifier
      (`.venv/bin/python scripts/verify_compileall.py`) and recorded the
      exact exit code. Do not use `python -m compileall .` as the
      repository-level gate: it traverses `.venv/`, `workspace/`, `data/`,
      and other ignored artifacts that are intentionally outside maintained
      source ownership.
- [ ] Ran `git status --porcelain` again and reported precisely what is
      committed vs. uncommitted — do not describe uncommitted work as
      "complete."
- [ ] If the task required a real end-to-end verification (a real run
      against a live dependency, not just unit tests), you performed it and
      are reporting the actual outcome, including partial or blocked
      outcomes, not just the parts that succeeded.
- [ ] If any real, external, or pre-existing issue was discovered during
      verification that is not caused by your change, it is reported
      explicitly and not glossed over, and you did not attempt to "fix" it
      if doing so was outside the task's stated scope.
- [ ] Updated `CURRENT_PHASE.md` to reflect the true state (not "complete"
      unless the repository owner has approved it — see
      `PHASE_COMPLETION_POLICY.md`).
- [ ] Updated `NEXT_STEPS.md` to remove finished items and add any new ones
      discovered.
- [ ] Added an entry to `CHANGELOG_AI.md` describing what actually changed,
      grounded in the diff/commits, not in the original task description.
- [ ] If you found a stale or incorrect statement in any `AI_ENGINEERING/`
      document while working, you corrected it rather than leaving it.
- [ ] Did not create a commit or push unless explicitly instructed to.
- [ ] Your final report to the user states, in plain terms: what changed,
      whether it's committed, whether tests pass (with numbers), and
      whether anything remains unverified or blocked.

## Red flags that mean you are not actually done

- You are about to write "should work" instead of "verified to work."
- You are about to report a phase complete because a report file says so,
  without having reproduced its result yourself.
- You are about to skip re-running the full test suite because "only a
  small change" was made.
- You are about to leave a document in `AI_ENGINEERING/` inconsistent with
  what you just did, planning to fix it "next time."
- You are about to mark uncommitted work as a completed phase.
