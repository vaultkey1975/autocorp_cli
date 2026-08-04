# Current Phase

**Last verified against the repository:** 2026-08-04 on branch
`phase-2a-local-first-credit-guard`, base commit
`911701d Add speech preview and GPU lifecycle safeguards`.

---

## Phase 2A: Local-First Context Budget and Usage Ledger

Phase 2A adds local-first provider guardrails, bounded context manifests,
generated-path exclusion, and a SQLite usage ledger. The scope is AutoCorp
CLI only.

## Status

Implemented and locally verified in the working tree; commit pending.
Official phase completion remains owner-gated.

## Architecture

- `brains/repo_policy.py` is the shared deterministic path classifier used
  by scanners, readiness, Fast Pytest discovery/mapping/change detection,
  and context collection.
- `brains/context_budget.py` builds repair contexts in priority order:
  target file, failure text, target diff, direct dependencies, and related
  tests. Token counts use the documented estimate `ceil(bytes / 4)` and
  are labeled estimates.
- `brains/usage_ledger.py` stores provider attempts in
  `data/autocorp_usage_ledger.sqlite3` with explicit schema versioning.
- `autocorp usage-report --repo PATH [--json]` reports ledger evidence.
  Empty reports truthfully state that no evidence exists; no command
  prints or implies the 77% target as an achieved result.
- `BaseEngine` and `RepairContentProvider` are abstract interfaces.
  Readiness ignores `NotImplementedError` only in real abstract methods;
  concrete production raises still fail readiness.
- `LocalRepairContentProvider` now performs a real local-engine call for
  explicit local repair-content work, validates complete replacement
  content, records ledger evidence, and requests Ollama model unload after
  success, failure, or validation error. The production factory rejects
  `mock`.

## Provider Rules

- Routing remains deterministic and model-free.
- Local deterministic code is preferred before generation.
- Paid providers are never called automatically and do not receive fallback
  traffic after local failures.
- DeepSeek and Claude remain explicit selections only.
- Local Ollama is used only during explicit generation stages and cleanup
  is requested immediately after use.

## Verification Evidence

Read-only Fast Pytest plan:

```
.venv/bin/python autocorp.py test-plan --repo /home/larry/autocorp_cli --feature phase-2a-local-first-credit-guard --json
```

Result: exit code 0. Selected 65 fast tests and 72 focused tests.

Warnings-as-errors relevant suite:

```
.venv/bin/python -m pytest -q -W error tests/test_phase_2a_context_ledger.py tests/test_live_readiness.py tests/test_repair_content_provider.py tests/test_repair_content_provider_factory.py tests/test_provider_contracts.py tests/test_engines.py tests/test_scanner.py tests/test_analyzer.py tests/test_model_router.py tests/test_discovery.py tests/test_fast_pytest_engine.py
```

Result: exit code 0.

Fast Pytest focused explicit relevant paths:

```
.venv/bin/python autocorp.py test-focused --repo /home/larry/autocorp_cli --path tests/test_phase_2a_context_ledger.py --path tests/test_analyzer.py --path tests/test_engines.py --path tests/test_provider_contracts.py --path tests/test_scanner.py --path tests/test_live_readiness.py --path tests/test_repair_content_provider.py --path tests/test_repair_content_provider_factory.py --path tests/test_tester_backed_repair_provider.py --path tests/test_repair_proposal.py --path tests/test_repair_proposal_quality.py --path tests/test_repair_proposals.py --path tests/test_discovery.py --path tests/test_fast_pytest_engine.py --path tests/test_model_router.py --json
```

Result: exit code 0, selected 15 files, collected 288 tests, 288 passed,
duration 37.39s, no uncertainty warnings. Performance warnings identified
the explicit selection as including integration/media-heavy/not-expected-
fast paths; this was expected because final focused verification included
all modified Phase 2A-adjacent modules.

Required static checks:

```
git diff --check
.venv/bin/python scripts/verify_compileall.py --repo /home/larry/autocorp_cli
```

Results: both exit code 0; compile verifier compiled 214 maintained Python
files.

Changed-module compile validation:

```
.venv/bin/python -m py_compile autocorp.py autocorp_testing/change_detection.py autocorp_testing/discovery.py autocorp_testing/mapping.py brains/analyzer.py brains/base_engine.py brains/live_readiness.py brains/providers.py brains/repair_content_generator.py brains/repair_proposal.py brains/scanner.py brains/context_budget.py brains/repo_policy.py brains/usage_ledger.py core/llm.py
```

Result: exit code 0.

CLI parser validation:

```
.venv/bin/python autocorp.py --help
.venv/bin/python autocorp.py usage-report --help
```

Result: exit code 0 for both commands; `usage-report --repo PATH [--json]`
is registered.

Real local Ollama smoke:

```
LocalRepairContentProvider against disposable /tmp repository
```

Result: Ollama available; model `qwen2.5:14b` ready; provider returned
valid replacement content `x = 2`; ledger recorded 1 local successful
operation; paid provider called: false; cleanup requested: true; cleanup
verified: true; model loaded after smoke: false; disposable repository
deleted: true. Audit report:
`/tmp/autocorp_phase2a_ollama_smoke_20260804_183331.txt`.

Complete strict suite:

```
.venv/bin/python -m pytest -W error -q
```

Result: exit code 0. Pytest cache recorded 1301 collected tests; progress
output reached 100% and showed one expected xfail marker.

## Final Audit Defect Fixed

- `brains/repair_content_generator.py`: local repair cleanup verification
  previously requested Ollama unload even when deterministic pre-generation
  validation rejected an unsafe path before any model invocation. Cleanup is
  now requested only after local generation starts.
- `tests/test_phase_2a_context_ledger.py`: added coverage proving an unsafe
  path rejection does not call Ollama cleanup, while successful local
  generation still records cleanup evidence.

## Known Blockers

- Official phase completion remains owner-gated by
  `PHASE_COMPLETION_POLICY.md`.
