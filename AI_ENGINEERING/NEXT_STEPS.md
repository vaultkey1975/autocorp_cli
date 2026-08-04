# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item - see `DOCUMENTATION_POLICY.md`.

---

## Immediate Work

1. **Owner review of Phase 2A.**

   Unit, focused, warnings-as-errors, compile, diff, Fast Pytest focused,
   full strict suite, and real local Ollama smoke verification have passed.
   The smoke used a disposable temporary repository, invoked the production
   `LocalRepairContentProvider`, returned valid replacement content,
   recorded one local ledger row, proved no paid provider path was used,
   requested and verified model unload, confirmed the model was not loaded
   afterward, deleted the disposable repository, and saved the audit report
   at `/tmp/autocorp_phase2a_ollama_smoke_20260804_183331.txt`.

2. **Owner review of the Live Application Inspector.**

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
