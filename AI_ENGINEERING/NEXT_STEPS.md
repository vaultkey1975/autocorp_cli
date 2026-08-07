# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item - see `DOCUMENTATION_POLICY.md`.

---

## Immediate Work

1. **Owner review of Phase 2A and Phase 2B.**
   Both phases are committed to `main` (`34e7a4a` and `b3fb054`
   respectively). Unit, focused, warnings-as-errors, compile, diff, and
   full strict suite verification passed for both. The prior
   "commit pending" note was stale — both commits predate this audit.

2. **Owner review of AutoCorp Brain Integration plan.**
   A planning-only audit (2026-08-07, corrected 2026-08-07) on this branch
   evaluated whether AutoCorp CLI is ready to integrate with AutoCorp Brain
   through the production v1 HTTP API. AutoCorp CLI communicates over pure
   HTTP only (standard-library `http.client`); it must not import the
   `autocorp_brain` package or SDK. See `ARCHITECTURE.md` "AutoCorp Brain
   Integration Findings" for the full plan, scope, exclusions, and
   acceptance criteria. Implementation is NOT authorized.

3. **Owner review of the Live Application Inspector.**

   Focused verification has passed:

   ```
   .venv/bin/python -m pytest -W error -q tests/test_live_inspector.py tests/test_manager.py tests/test_discovery.py tests/test_autocorp_chat.py
   ```

   Result: exit code 0, 56 passed.

   Manual `inspect --json` smoke checks against `/home/larry/autocorp_cli`
   and `/home/larry/clonecast` each produced parseable JSON. The CloneCast
   smoke launched `clonecast.web_app:create_app --factory`, discovered 126
   routes, and preserved CloneCast's pre-existing dirty git status
   unchanged. It reported CloneCast production readiness as
   `NEEDS_ATTENTION` due to 9 read-only SQLite foreign-key violations in
   `db/cloneshow.db`.

   Required final verification has passed:
   `git diff --check` -> exit code 0;
   `.venv/bin/python scripts/verify_compileall.py` -> exit code 0, 171
   maintained Python files compiled; `.venv/bin/python -m pytest -W error
   -q` -> exit code 0, 1002 tests collected with the existing xfail
   visible in progress output.

3. **Do not push without owner instruction.**
   The owner requested commits if verification passes and explicitly said
   not to push. Do not include unrelated untracked artifacts, generated
   runtime reports, uploaded files, session data, or workspace files.

4. **Reliability Engine integration decision.** Repository evidence now
   includes an end-to-end test for `ReliabilityOrchestrator.run()` against
   a disposable git repository, plus production-hardening coverage for
   dirty-target refusal and merge-failure diagnostics. Discovery and the
   manager report this status but do not add a dedicated Reliability Engine
   CLI command.

## Technical Debt

- `config.py`'s `APP_VERSION` still reads `"0.1.0"` despite later tags and
  many later commits. Either start updating it or remove it if it is not a
  meaningful release indicator.
- Two unrelated phase-numbering schemes exist in history: tagged SQLite
  Generation "Phase 1-7" work and untagged repository-intelligence
  "Phase 1A-1Y" work. New documentation should avoid adding another
  overloaded phase label.
- The Reliability Engine performs full repository scans in
  `DependencyGraph.build()` and `CodebaseRAGIndex.rebuild()` during run
  setup. No caching layer is evidenced in the repository.
- Cross-platform Windows behavior is not evidenced by the local Linux-only
  verification runs. The CLI uses `pathlib` broadly, but production
  release claims about Windows support require dedicated Windows
  verification.
- Discovery currently uses deterministic manifest/config/source evidence.
  It does not execute package managers or CI commands; real build/test
  execution remains a separate workflow stage.
- Live Application Inspector safely launches detected apps from a
  disposable source copy, but it only performs non-mutating HTTP GET
  diagnostics. Mutating workflow assertions still require existing
  workflow-test/publish-test disposable infrastructure.

## Known Bugs

- CloneCast's production database, discovered through AutoCorp's
  validation tooling, has 9 pre-existing foreign-key violations in legacy
  chapter-script tables. This is external to AutoCorp.
- CloneCast audio generation has repeatedly failed CloneCast-side QC due
  to peak clipping during real disposable runs. This blocks CloneCast
  workflow/publishing validation from reaching a full successful PASS, but
  it is not an AutoCorp implementation defect.
- No other current AutoCorp implementation bug is documented here after
  the workflow-engine reliability correction currently in the working
  tree.

## Future Improvements

- **AutoCorp Brain Integration — Core Client Boundary** (proposed, NOT authorized). A
  planning-only audit (2026-08-07, corrected 2026-08-07) evaluated integration
  readiness. AutoCorp Brain exposes a production v1 HTTP API at
  `127.0.0.1:8420` (the SDK proves the contract is viable; AutoCorp CLI does
  not import it). The recommended architecture: pure HTTP integration with a
  `brains/autocorp_brain_adapter.py` module in this repository (health checks,
  version negotiation, error mapping, timeout/cancellation handling). Brain
  must already be running — AutoCorp CLI does NOT start or stop it in this
  milestone. AutoCorp CLI retains ownership of provider selection, usage
  ledger, CloneCast orchestration, desktop UI, and operator workflows. Full
  scope, exclusions, and acceptance criteria are in `ARCHITECTURE.md`.
- Add a dedicated Reliability Engine CLI entry point only if the owner
  approves production integration after reviewing the E2E and
  production-hardening evidence.
- Consider whether AutoCorp Chat should gain provider-backed natural
  language synthesis later. The first production version intentionally
  stays repository-backed and deterministic instead of acting as a generic
  LLM wrapper.
- Add CI coverage for the repository-approved compile verifier if this
  repository is connected to a CI system. No CI configuration is evidenced
  in the repository.
- Expand discovery confidence with real command execution only when a safe,
  explicit verification mode is designed.
- Extend project-specific feature inspectors beyond CloneCast when
  repository evidence identifies stable, safe diagnostic routes for other
  application domains.

## Blocked Work

- Official phase completion remains owner-gated by
  `PHASE_COMPLETION_POLICY.md`.
- External CloneCast audio/QC defects remain outside this repository.

## Missing Dependencies

- The direct `python` executable is unavailable in this shell through
  pyenv unless a version is selected. Repository verification uses
  `.venv/bin/python`, matching the test-suite command used throughout
  `AI_ENGINEERING/`.
