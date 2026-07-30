# Current Phase

**Last verified against the repository:** 2026-07-30 Autonomous
Engineering Manager implementation on `main`. The base commit before this
work was `99db951 fix: harden production readiness paths`.

---

## Current Phase

There is no single numbered phase currently defined as active. Repository
evidence shows the current work is an Autonomous Engineering Manager:
`autocorp manage`, backed by a new `brains/manager.py` coordinator.

This work is not another scanner, planner, or chat feature. It coordinates
existing evidence sources:

- `brains/scanner.py`
- `brains/analyzer.py`
- `brains/project_planner.py`
- `brains/live_readiness.py`
- existing workflow/publish-test CLI commands
- git inspection
- `AI_ENGINEERING/` documents in the target repository
- existing repair/propose-repair command paths
- AutoCorp Chat routing
- Reliability Engine availability evidence

## Status

**Implemented and locally verified.** Verification run for this manager
work:

```
git diff --check
```

Result: exit code 0.

```
.venv/bin/python scripts/verify_compileall.py
```

Result: exit code 0, 167 maintained Python files compiled.

```
.venv/bin/python -m pytest -W error -q tests/test_manager.py tests/test_autocorp_chat.py
```

Result: exit code 0, 21 passed.

```
.venv/bin/python -m pytest -W error -q
```

Result: exit code 0. Pytest collected 967 tests; the strict run completed
successfully with the existing xfail visible in progress output.

Manual CLI smoke checks against `/home/larry/autocorp_cli` passed for:

```
.venv/bin/python autocorp.py manage --repo /home/larry/autocorp_cli --summary
.venv/bin/python autocorp.py manage --repo /home/larry/autocorp_cli --roadmap
.venv/bin/python autocorp.py manage --repo /home/larry/autocorp_cli --next-task
.venv/bin/python autocorp.py manage --repo /home/larry/autocorp_cli --production
```

Each exited 0.

Baseline before edits:

```
.venv/bin/python -m pytest -q
```

Result: exit code 0 with the existing xfail visible in progress output.

Full verification required before commit remains:

```
git diff --check
.venv/bin/python scripts/verify_compileall.py
.venv/bin/python -m pytest -W error -q
```

## Objective

- Provide a read-only engineering manager that tells an engineer what is
  broken, what is healthy, what changed recently, current phase evidence,
  production readiness, highest-priority blockers, highest-risk code, next
  task, AI recommendation, and safety posture.
- Build a live roadmap with Critical, High, Medium, Low, Completed,
  Blocked, Waiting on Owner, and Future Ideas sections.
- Explain release-readiness scores through deterministic deductions from
  repository evidence, not arbitrary unexplained numbers.
- Teach AutoCorp Chat to answer manager-backed roadmap, production
  readiness, next-task, blockers, release-status, and engineering-summary
  requests.

## Known Blockers

- **Owner-only completion authority:** per `PHASE_COMPLETION_POLICY.md`, an
  AI engineer may update evidence and create requested commits, but may not
  declare a phase officially complete unless the owner accepts that state.
- **CloneCast audio clipping remains external to AutoCorp:** previous
  CloneCast validation work repeatedly found CloneCast-side audio QC
  failures. That evidence is unrelated to the manager implementation.

## Next Phase

Unable to determine from repository evidence. No document or commit defines
a numbered phase after the current Autonomous Engineering Manager work.
