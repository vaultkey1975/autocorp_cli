# Current Phase

**Last verified against the repository:** 2026-08-04 on branch
`phase-2b-local-first-provider-routing`, base commit
`34e7a4a Implement Phase 2A local-first usage guard` (merged to `main`).

---

## Phase 2B: Local-First Provider Routing and Usage Coverage

**Authorization.** Phase 2A left no repository-evidenced Phase 2B scope
(see `ROADMAP.md`'s "FUTURE PLANNING REQUIRED" section and
`PHASE_COMPLETION_POLICY.md`'s rule against inventing a next phase). The
repository owner explicitly authorized and defined this scope directly; this
document records that authorization truthfully rather than presenting it as
repository-discovered.

### Objective

Phase 2A recorded real usage-ledger evidence for exactly two call
sites (`brains/providers.py::generate_proposal_json`,
`brains/repair_content_generator.py::LocalRepairContentProvider.generate`).
Every other production path capable of invoking a language model — the main
build path (`brains/builder.py`), the self-heal fix loop
(`brains/tester.py`), the planner's LLM path (`brains/planner.py`), and
`explain` (`core/orchestrator.py`) — had zero routing-policy enforcement and
zero usage-ledger coverage. In particular, `autocorp build --engine auto`
combined with the existing, deliberately-opt-in `AUTOCORP_DEEPSEEK_ROUTING`
ruleset could route a build to the Claude CLI based only on keyword matching
in the request text, with no explicit-authorization check and no evidence
recorded anywhere.

Phase 2B closes that gap: every model-capable production call site routes
through one authoritative, testable policy
(`brains/provider_policy.py`) that rejects prohibited/unauthorized
providers before any engine is touched, and every real invocation (success,
failure, or denial) is recorded in the Phase 2A usage ledger. A static,
model-free coverage audit (`brains/provider_coverage_audit.py`) makes
uncovered call sites visible instead of assumed-covered.

### Acceptance criteria

1. A single central policy module exists and is the only place that decides
   whether a requested provider may be used; `brains/providers.py` and
   `brains/repair_content_generator.py` are extended to use its shared
   denial/cleanup logic rather than duplicating it, and every newly-covered
   call site (`brains/builder.py`, `brains/tester.py`, `brains/planner.py`,
   `core/orchestrator.py::explain`) routes new generation calls through it.
2. The `mock` provider name is rejected in every production path (already
   true for repair-content; now enforced centrally).
3. A paid provider (`claude`, `deepseek`) is refused unless the call carries
   `explicit_user_selection=True` — set only when the CLI operator named the
   engine directly (`--engine claude`/`--engine deepseek`, or
   `--provider claude`/`--provider deepseek` for repair-proposal) or, for
   auto-routing, when a non-fallback rule matched under the
   already-deliberately-opt-in `AUTOCORP_DEEPSEEK_ROUTING` ruleset. Fallback
   or unmatched auto-routing may never reach a paid provider.
4. Every real invocation — permitted, denied, attempted-and-failed, or
   attempted-and-succeeded — is recorded in the usage ledger; a policy
   denial or a pre-generation validation failure is never recorded with
   `result_status="success"`.
5. Local-model unload is requested only when generation was genuinely
   attempted (never on a policy denial or an engine-unavailable short
   circuit).
6. Where a provider reports real token usage (Ollama's own
   `prompt_eval_count`/`eval_count`, or DeepSeek's OpenAI-compatible
   `usage` field), the ledger records it as provider-reported, not as a
   byte-based estimate; where no such report exists, the entry is marked
   estimated or unavailable, never presented as exact.
7. `autocorp usage-report` gains a coverage section (not a new command)
   showing known model-capable call sites, how many are policy/ledger
   covered, how many are explicitly excluded (with a reason), and a
   coverage percentage — computed only from real static inspection of
   AutoCorp's own tracked source, never fabricated.
8. `brains/provider_coverage_audit.py` statically discovers any `.py` file
   under `brains/`, `core/`, or `reliability_engine/` containing a
   generation call pattern and flags it as an uncovered defect if it is not
   in the audit's known-call-site registry — so a new, unregistered
   call site fails the audit instead of silently passing.
9. Full strict suite (`pytest -W error -q`) passes; focused Phase 2B tests
   pass; `git diff --check`, changed-module `py_compile`, and
   `scripts/verify_compileall.py` pass.
10. AutoCorp can take VERIFIED_BROKEN evidence from another application and
    generate a complete, paste-ready Codex prompt, a complete Claude prompt,
    or both — each with real provenance and a SHA-256 hash, secrets
    redacted, saved under the target repository's own
    `AI_ENGINEERING/REPAIR_HANDOFFS/`, and optionally opened in the current
    VS Code window — entirely deterministically, with zero Ollama/Claude/
    Codex/DeepSeek/paid-API calls in the normal generation path. AutoCorp
    must refuse to generate a handoff from passing or inconclusive evidence.

### In-scope modules

`brains/provider_policy.py` (new), `brains/provider_coverage_audit.py`
(new), `brains/repair_handoff.py` (new), `core/llm.py`,
`brains/base_engine.py`, `brains/local_engine.py`,
`brains/deepseek_engine.py`, `brains/usage_ledger.py`, `brains/providers.py`,
`brains/repair_content_generator.py`, `brains/builder.py`,
`brains/tester.py`, `brains/planner.py`, `core/orchestrator.py`,
`autocorp.py` (CLI wiring, including the new `repair-handoff` subcommand),
plus new/updated tests.

### Explicitly excluded from Phase 2B

- **`reliability_engine/`** (`orchestrator.py`, `self_consistency.py`,
  `planner_spec.py`, `test_loop.py`). This subsystem is a second, parallel,
  not-CLI-integrated orchestration pipeline whose composition with the main
  pipeline is a separate, still-open owner decision
  (`ROADMAP.md` item 3; `ARCHITECTURE.md`'s Reliability Engine section).
  Wiring it through the new routing policy would be exactly the kind of
  "broad unrelated refactoring" this phase is instructed to avoid, into a
  subsystem whose own integration status is undecided. The coverage audit
  registers these files as **excluded, with this reason**, not silently
  passing and not silently missing.
- Any Reliability Engine integration decision, any Phase 2C-style feature
  work beyond the two capabilities explicitly authorized for this phase
  (provider routing/usage coverage; the VS Code repair handoff generator).
- Model-assisted rewriting of a generated handoff's prose. The handoff
  generator's normal path is fully deterministic (string templating over
  already-verified evidence); if model-assisted wording is ever added, it
  is a separate, explicitly authorized operation and is out of scope here.
- Automatic submission of a generated prompt to Codex or Claude, or any
  keyboard-input simulation into a VS Code extension panel. The generator
  only ever writes a file and, optionally, asks the real `code` binary to
  open it — it never acts as, or drives, an agent.

### VS Code Repair Handoff Generator

Authorized as part of Phase 2B (not Phase 2C) after the original Phase 2B
scope above was already implemented and committed. `brains/repair_handoff.py`
transforms VERIFIED_BROKEN AutoCorp evidence into paste-ready Codex/Claude
prompts:

- **Evidence input**: a Fast Pytest Engine `EngineReport` JSON file (the
  same JSON `autocorp test-focused`/`test-full --json` already produce) via
  `autocorp repair-handoff --repo PATH --evidence PATH --agent
  {codex,claude,both} [--open-vscode]`. This reuses the existing test-report
  format rather than inventing a second evidence schema.
- **Classification** (`classify_evidence`): VERIFIED_BROKEN only when at
  least one concrete failing test (a real `node_id`) is present;
  zero-collected, blocked runs, or a nonzero failure count with no captured
  detail are INCONCLUSIVE; a clean run is PASSED. Warnings alone never
  become a confirmed defect. `generate_handoff`/`generate_handoffs` raise
  `RepairHandoffNotVerified` for anything but VERIFIED_BROKEN — no repair
  task is ever fabricated.
- **Prompts** are genuinely distinct per agent (Codex: narrow
  implementation workflow; Claude: diagnosis/review workflow), both
  embedding real evidence (repository/branch/commit/working-tree state via
  real local `git` inspection, exact failed command, exit code, failing
  test IDs, condensed error text), an explicit Hypotheses-vs-Facts section,
  a repair scope where every unstated permission defaults to PROHIBITED,
  and the full Phase 2B project-rules list plus a pointer to the target
  repository's own `AI_ENGINEERING/*.md` when present.
- **Secrets** are redacted via the existing, Phase-1G-hardened
  `brains.repair_proposal._redact_inline_secrets` — reused, not
  reimplemented.
- **Writes** are atomic (temp file + `os.replace`) under the target
  repository's own `AI_ENGINEERING/REPAIR_HANDOFFS/`, filesystem-safe
  filenames, never silently overwritten, each with a sidecar
  `<prompt>.provenance.json` (handoff id, timestamps, repo/branch/commit,
  agent, run id, source evidence path, prompt SHA-256, VS Code result) and
  a real Phase 2B usage-ledger entry via
  `provider_policy.record_deterministic` — genuinely reusing the Phase 2B
  ledger rather than a disconnected store.
- **VS Code**: `open_in_vscode` shells out to the real `code --reuse-window
  <path>` only; reports success only on a real zero exit code; leaves the
  handoff file usable and prints manual-open instructions when `code` isn't
  on `PATH`; never pipes prompt content anywhere and never touches an
  extension.

### Relationship to the 77% paid-credit-reduction target

Phase 2A already refuses to print the 77% figure as an achieved result when
insufficient ledger evidence exists. Phase 2B does not change that rule and
does not claim the target is met. What Phase 2B adds is *coverage*: once
every model-capable path records real evidence, `usage-report`'s existing
`measured_savings_percentage` calculation reflects the whole pipeline's
real local-vs-paid mix instead of only the two Phase 2A-covered call sites.
Whether that measured percentage approaches 77% is an empirical outcome of
real future usage, not a claim made by this phase.

### Implementation summary

`brains/provider_policy.py` is the new central routing policy: `decide()`
rejects the prohibited `mock` name and any paid provider (`claude`,
`deepseek`) without `explicit_user_selection=True`; `invoke()` constructs
or reuses the permitted engine, calls its existing `generate(prompt,
system)` contract exactly once (preserving every existing engine mock/test
convention in the repository), records a ledger entry for every outcome
(denied, blocked-before-attempt, failed-after-attempt, succeeded), and
requests local-model cleanup only when generation genuinely began.
`record_deterministic()` and `record_operation()` cover, respectively,
no-model-call paths and the two call sites (`brains/planner.py`'s LLM path,
`brains/tester.py`'s engine-less fallback) that must keep calling
`core.llm.generate_json` directly to preserve Ollama's `json_mode`
constraint and their existing `llm.generate_json` mocking contracts.

`brains/builder.py` (`_gen_file`, `generate_edit_diff`) and
`brains/tester.py` (`suggest_fix`, engine branch) now route real generation
through `provider_policy.invoke()`; both gained `repo_path` (defaulting to
AutoCorp's own installation root, since `build`/self-heal write into
`workspace/`, not an external `--repo` target) and
`engine_explicit_selection`, set to `True` only by an explicit CLI
`--engine`/`--tester-engine` flag, or by a real (non-fallback) auto-router
rule match under the already operator-gated `AUTOCORP_DEEPSEEK_ROUTING`
toggle. `core/orchestrator.py::explain` is routed the same way.

Real provider-reported usage capture (Ollama's `prompt_eval_count`/
`eval_count`, DeepSeek's OpenAI-compatible `usage` field) was attempted for
every engine, then partially reverted: `LocalEngine`/`DeepSeekEngine`'s
local-Ollama transport must keep calling `core.llm.generate` exactly
(several existing tests, e.g. `tests/test_engines.py`, patch
`core.llm.generate` directly and would otherwise silently stop being
exercised - a genuine "hidden Ollama call" risk this phase exists to
prevent, discovered and fixed during implementation, not shipped). DeepSeek
API-mode usage IS captured for real (`DeepSeekEngine._generate_api`, via
`self.last_usage`), since it has no such constraint. Local Ollama usage is
honestly reported as *estimated*, never *exact*.

`brains/usage_ledger.py::report()`/`render_human()` now classify
operations by deterministic/local/paid/denied and by
exact/estimated/unavailable usage, and append a coverage section computed
by the new `brains/provider_coverage_audit.py` - a static, model-free
scanner that walks `brains/`, `core/`, `reliability_engine/` for generation
call patterns and cross-references a maintained registry
(`KNOWN_CALL_SITES`), flagging any unregistered call site as a defect.
Real run against AutoCorp's own source: 18 known call sites, 6 covered
(builder, tester, planner, providers, repair_content_generator,
orchestrator), 4 explicitly excluded (`reliability_engine/*`, documented
reason), 0 uncovered, 100.00% coverage.

### Verification evidence (provider routing/usage coverage)

Fast Pytest test-plan:
```
.venv/bin/python autocorp.py test-plan --repo /home/larry/autocorp_cli --feature phase-2b-local-first-provider-routing --json
```
Result: exit code 0. 59 fast tests, 65 focused tests selected, confidence
`high`, no uncertainty warnings.

Fast Pytest test-focused (29 explicit Phase 2B-relevant paths):
```
.venv/bin/python autocorp.py test-focused --repo /home/larry/autocorp_cli --path tests/test_phase_2b_provider_routing.py [... 28 more explicit paths] --json
```
Result: exit code 0, 29 files selected, 473 tests collected, 473 passed, 0
failed, duration 107.71s, not blocked, no uncertainty warnings.

Required static checks:
```
git diff --check
.venv/bin/python scripts/verify_compileall.py --repo /home/larry/autocorp_cli
```
Results: both exit code 0; compile verifier compiled 217 maintained Python
files.

Changed-module compile validation (`py_compile` on all 15 changed/new
files): exit code 0.

CLI validation: `autocorp.py --help`, `autocorp.py usage-report --help`,
`autocorp.py build --help` all exit code 0. Real `usage-report` smoke
against `/home/larry/autocorp_cli` itself: 100.00% coverage, 6/6 in-scope
call sites covered, 0 uncovered.

### Verification evidence (VS Code repair handoff generator)

Fast Pytest test-plan:
```
.venv/bin/python autocorp.py test-plan --repo /home/larry/autocorp_cli --feature vscode-repair-handoff-generator --json
```
Result: exit code 0. 35 fast tests, 37 focused tests selected, confidence
`high`, no uncertainty warnings.

Fast Pytest test-focused (8 explicit relevant paths):
```
.venv/bin/python autocorp.py test-focused --repo /home/larry/autocorp_cli --path tests/test_repair_handoff.py --path tests/test_phase_2b_provider_routing.py --path tests/test_phase_2a_context_ledger.py --path tests/test_repair_proposal.py --path tests/test_provider_contracts.py --path tests/test_repo_target_cli.py --path tests/test_repair_engine_cli.py --path tests/test_fast_pytest_engine.py --json
```
Result: exit code 0, 8 files selected, 212 tests collected, 212 passed, 0
failed, duration 34.55s, not blocked, no uncertainty warnings.

Required static checks: `git diff --check` and
`scripts/verify_compileall.py` both exit code 0 (219 maintained Python
files compiled). `py_compile` on all 4 changed/new files for this
extension: exit code 0. CLI validation: top-level `--help`,
`repair-handoff --help`, `usage-report --help` all exit code 0.
Real end-to-end smoke (disposable git repo, real secret string in the
evidence): both Codex and Claude handoffs generated, distinct content,
secret redacted from both files and both provenance sidecars, SHA-256
verified, PASSED evidence correctly refused (exit 1, no files created,
no ledger entry fabricated), real `usage-report` showed the generation
recorded as `deterministic`/no model call.

Coverage-audit regression check
(`tests/test_phase_2b_provider_routing.py::test_coverage_audit_against_real_autocorp_source_has_no_unregistered_call_sites`):
still passes - `brains/repair_handoff.py` contains no generation call
pattern (verified by grep of its own imports: no `core.llm`, no
`engine_registry`, no `requests`) and is correctly invisible to the
routing coverage audit rather than needing a registry entry.

### Complete strict suite (final, after both capabilities)

```
.venv/bin/python -m pytest -W error -q
```
Result: exit code 0. 1365 collected tests (pytest cache `nodeids`); one
expected xfail visible in progress output; zero failures. (An earlier,
intermediate full run after only the provider-routing work also passed at
exit code 0 with 1331 collected tests; this final run supersedes it after
the repair-handoff extension added 34 more tests.)

### Final defects found and fixed during implementation

- `provider_policy.decide()` initially rejected any engine name outside a
  fixed 3-name allowlist. This broke `BuilderBrain.generate_edit_diff`'s
  legitimate reuse by `reliability_engine`'s own tests and by
  `tests/test_verbatim_content.py`, both of which construct `BuilderBrain`
  with custom-named `BaseEngine`-like objects that are not one of
  AutoCorp's three registered engines. Fixed: only the prohibited `mock`
  name and the two real paid provider names are restricted; any other
  caller-supplied engine identifier is permitted, since this module's job
  is keeping AutoCorp's own paid-provider names honest, not acting as a
  second engine registry.
- `provider_policy.invoke()` initially called a new `generate_with_usage()`
  method as the primary generation call. This silently bypassed every
  existing test's `monkeypatch.setattr(engine, "generate", ...)`, causing
  several tests to attempt a real, unmocked Ollama call. Fixed: `invoke()`
  calls only `generate()` (the one method every existing engine/test
  double already honors); real usage, when an engine captures any as a
  side effect of that exact call, is read afterward from a `last_usage`
  attribute instead of a second, separate, potentially-unmocked call.
- `provider_policy._base_entry()` initially called
  `context_budget.repository_fingerprint()` (a full source-tree walk) on
  every ledger write. Against AutoCorp's own repo this triggered real `git
  ls-files` subprocess calls on every build/self-heal ledger entry,
  breaking `tests/test_self_heal_activation.py::test_on_live_repair_uses_no_subprocess`,
  which explicitly asserts self-heal never shells out. Fixed: dropped the
  full fingerprint from the shared helper; `repository_path` alone already
  satisfies the "repository identity" requirement for these high-frequency
  call sites.
- `brains/providers.py`'s prohibited/paid-authorization checks now route
  through the shared `provider_policy.decide()` instead of relying on
  `engine_registry.create()` implicitly rejecting an unregistered name; its
  and `brains/repair_content_generator.py`'s local-model cleanup helpers
  were deduplicated into `provider_policy.request_and_verify_unload()`
  (pure extraction, zero behavior change - both are now a one-line alias).

### Known scope boundary

`reliability_engine/` (`orchestrator.py`, `self_consistency.py`,
`planner_spec.py`, `test_loop.py`) is explicitly excluded from Phase 2B
routing/ledger coverage - see "Explicitly excluded from Phase 2B" above.
The coverage audit records this as a documented exclusion, not a silent
gap, and the audit's own production gate test
(`tests/test_phase_2b_provider_routing.py::test_coverage_audit_against_real_autocorp_source_has_no_unregistered_call_sites`)
fails loudly if any *other* file under `brains/`, `core/`, or
`reliability_engine/` ever adds an unregistered model-capable call site.

### Status

Implemented and locally verified in the working tree; commit pending owner
review, same as Phase 2A's original status note. Official phase completion
remains owner-gated by `PHASE_COMPLETION_POLICY.md`.

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
