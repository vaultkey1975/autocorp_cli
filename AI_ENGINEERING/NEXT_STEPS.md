# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item - see `DOCUMENTATION_POLICY.md`.

---

## Immediate Work

1. **Owner review of the Universal Repository Discovery Engine.**

   Required verification has passed:

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

   Manual `discover` smoke checks for text, `--full`, and `--json` modes
   against `/home/larry/autocorp_cli` each exited 0. The JSON output was
   parsed successfully with `.venv/bin/python -m json.tool`.

   ```
   .venv/bin/python -m pytest -W error -q
   ```

   Result: exit code 0, 985 tests collected, strict run completed
   successfully with the existing xfail visible in progress output.

2. **Do not push without owner instruction.**
   The owner requested commits if verification passes and explicitly said
   not to push. Do not include unrelated untracked artifacts
   (`claude_phase_1g_audit.txt`, `clonecast_live_readiness_report.txt`,
   `data/`, `phase_1q_runtime_output.txt`).

3. **Reliability Engine integration decision.** Repository evidence now
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

## Known Bugs

- CloneCast's production database, discovered through AutoCorp's
  validation tooling, has 9 pre-existing foreign-key violations in legacy
  chapter-script tables. This is external to AutoCorp.
- CloneCast audio generation has repeatedly failed CloneCast-side QC due
  to peak clipping during real disposable runs. This blocks CloneCast
  workflow/publishing validation from reaching a full successful PASS, but
  it is not an AutoCorp implementation defect.
- No current AutoCorp implementation bug is documented here after the
  production-hardening commit `99db951`, manager commit `ff31c1a`, and the
  discovery focused tests run in this session.

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

## Blocked Work

- Official phase completion remains owner-gated by
  `PHASE_COMPLETION_POLICY.md`.
- External CloneCast audio/QC defects remain outside this repository.

## Missing Dependencies

- The direct `python` executable is unavailable in this shell through
  pyenv unless a version is selected. Repository verification uses
  `.venv/bin/python`, matching the test-suite command used throughout
  `AI_ENGINEERING/`.
