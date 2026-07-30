# Current Phase

**Last verified against the repository:** 2026-07-30 Live Application
Inspector implementation on `main`. The base commit before this work was
`cc89467 feat: add Universal Repository Discovery Engine`.

---

## Current Phase

There is no single numbered phase currently defined as active. Repository
evidence shows the current work is the Live Application Inspector:
`autocorp inspect`, backed by `brains/live_inspector.py`.

This work extends AutoCorp from repository understanding to runtime
application understanding:

- `brains/live_inspector.py` reuses the Discovery Engine and Live
  Readiness Scanner, then launches detected applications from a disposable
  copy of the target repository.
- `autocorp inspect` renders text, detailed text, or JSON reports.
- `brains/manager.py` uses Live Inspector results so actual startup and
  endpoint failures can outrank static repository heuristics.
- `brains/chat.py` routes "what actually works" / live-inspection prompts
  through the Live Inspector.

## Status

**Implemented and locally verified. Official phase completion remains
owner-gated.**

Baseline before edits:

```
.venv/bin/python -m pytest -q
```

Result: exit code 0 with the existing xfail visible in progress output.

Verification:

```
.venv/bin/python -m pytest -W error -q tests/test_live_inspector.py tests/test_manager.py tests/test_discovery.py tests/test_autocorp_chat.py
```

Result: exit code 0, 56 passed.

Manual smoke checks so far:

```
.venv/bin/python autocorp.py inspect --repo /home/larry/autocorp_cli --json --timeout 5
```

Result: exit code 0; JSON parsed successfully with
`.venv/bin/python -m json.tool`.

```
.venv/bin/python autocorp.py inspect --repo /home/larry/clonecast --json --timeout 8
```

Result: exit code 0; JSON parsed successfully. The run launched
`clonecast.web_app:create_app` via uvicorn with `--factory`, discovered
126 routes, reported `running_application=PASS`, and reported
`production_readiness=NEEDS_ATTENTION` because read-only SQLite inspection
found `db/cloneshow.db` has 9 foreign-key violations. CloneCast's
pre-existing dirty git status was unchanged before/after the run.

```
git diff --check
```

Result: exit code 0.

```
.venv/bin/python scripts/verify_compileall.py
```

Result: exit code 0, 171 maintained Python files compiled.

```
.venv/bin/python -m pytest -W error -q
```

Result: exit code 0. Pytest collected 1002 tests; the strict run
completed successfully with the existing xfail visible in progress output.

## Objective

- Determine whether the application can actually run, not only whether
  files exist.
- Detect project entry points: FastAPI, Flask, Django, CLI main blocks,
  console scripts, uvicorn/gunicorn targets, and package entry points.
- Launch applications only from a disposable copy of the target repository
  with timeout-protected subprocess handling and captured stdout/stderr.
- Query safe HTTP endpoints (`/`, `/health`, `/docs`, `/openapi.json`) and
  safe OpenAPI-discovered GET routes.
- Inspect SQLite databases read-only for openability, integrity,
  foreign-key status, schema version, and migration evidence.
- Report CloneCast-style feature states as PASS, FAIL, NOT CONFIGURED, or
  UNKNOWN without faking success.
- Separate Repository Quality, Running Application, Production Readiness,
  and Developer Workspace signals so a dirty working tree affects only the
  workspace category.

## Known Blockers

- **Owner-only completion authority:** per `PHASE_COMPLETION_POLICY.md`, an
  AI engineer may update evidence and create requested commits, but may not
  declare a phase officially complete unless the owner accepts that state.
- **Do not push:** the owner requested a commit after verification passes
  and explicitly said not to push.
- **CloneCast target state is externally dirty:** the real CloneCast smoke
  run observed pre-existing uncommitted CloneCast changes. The inspector
  preserved that state unchanged, but CloneCast release conclusions remain
  outside AutoCorp.

## Next Phase

Unable to determine from repository evidence. No document or commit defines
a numbered phase after the current Live Application Inspector work.
