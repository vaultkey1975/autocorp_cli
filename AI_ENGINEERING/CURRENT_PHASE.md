# Current Phase

**Last verified against the repository:** 2026-07-30 Universal Repository
Discovery Engine implementation on `main`. The base commit before this
work was `ff31c1a feat: add Autonomous Engineering Manager`.

---

## Current Phase

There is no single numbered phase currently defined as active. Repository
evidence shows the current work is the Universal Repository Discovery
Engine: `autocorp discover`, backed by a new `brains/discovery.py`
profiler and AutoCorp metadata storage in `memory/store.py`.

This work makes discovery the first stage for unknown repositories. It
does not replace scanner, analyzer, planner, manager, or Chat:

- `brains/discovery.py` reuses `scanner.run_scan()` and
  `analyzer.run_analysis()` for existing repository evidence.
- `autocorp discover` renders the profile as text, detailed text, or JSON.
- `memory/store.py` stores discovery profiles in AutoCorp's own SQLite
  metadata database, never in the target repository.
- `brains/manager.py` automatically runs discovery for repositories with
  no stored profile.
- `brains/chat.py` routes repository-profile, architecture, frameworks,
  languages, build-system, testing, deployment, and engineering-maturity
  prompts through discovery.

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
git diff --check
```

Result: exit code 0.

```
.venv/bin/python scripts/verify_compileall.py
```

Result: exit code 0, 169 maintained Python files compiled.

```
.venv/bin/python -m pytest -W error -q tests/test_discovery.py tests/test_manager.py tests/test_autocorp_chat.py
```

Result: exit code 0, 39 passed.

Manual smoke checks:

```
.venv/bin/python autocorp.py discover --repo /home/larry/autocorp_cli
.venv/bin/python autocorp.py discover --repo /home/larry/autocorp_cli --full
.venv/bin/python autocorp.py discover --repo /home/larry/autocorp_cli --json
.venv/bin/python autocorp.py manage --repo /home/larry/autocorp_cli --summary
```

Each exited 0. The JSON output was parsed successfully with
`.venv/bin/python -m json.tool`.

```
.venv/bin/python -m pytest -W error -q
```

Result: exit code 0. Pytest collected 985 tests; the strict run completed
successfully with the existing xfail visible in progress output.

## Objective

- Determine repository profile evidence for unknown repositories: language,
  frameworks, package managers, build/test/lint/format/type tools,
  database technology, containerization, CI/CD, supported operating
  systems, structure, documentation quality, license, architecture style,
  application type, production readiness, maturity, risks, unknowns, and
  confidence.
- Store reusable AutoCorp metadata: preferred test/build/lint commands,
  package manager, entry point, and documentation location.
- Integrate discovery into `autocorp manage` automatically for unseen
  repositories.
- Teach AutoCorp Chat profile and discovery-specific routes.

## Known Blockers

- **Owner-only completion authority:** per `PHASE_COMPLETION_POLICY.md`, an
  AI engineer may update evidence and create requested commits, but may not
  declare a phase officially complete unless the owner accepts that state.
- **CloneCast audio clipping remains external to AutoCorp:** previous
  CloneCast validation work repeatedly found CloneCast-side audio QC
  failures. That evidence is unrelated to discovery.

## Next Phase

Unable to determine from repository evidence. No document or commit defines
a numbered phase after the current Universal Repository Discovery Engine
work.
