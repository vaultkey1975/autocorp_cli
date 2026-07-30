# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item - see `DOCUMENTATION_POLICY.md`.

---

## Immediate Work

1. **Complete required verification for the current working tree.**
   Commands required by the owner request:

   ```
   git diff --check
   .venv/bin/python scripts/verify_compileall.py
   .venv/bin/python -m pytest -W error -q
   ```

   Verification completed in this session:
   `.venv/bin/python -m pytest -W error -q tests/test_reliability_engine.py
   tests/test_autocorp_chat.py` -> exit code 0, 69 passed.

   `git diff --check` -> exit code 0.

   `.venv/bin/python scripts/verify_compileall.py` -> exit code 0, 165
   maintained Python files compiled.

   `.venv/bin/python -m pytest -W error -q` -> exit code 0; 947 tests
   collected, strict run completed successfully with the existing xfail.

   The former `python -m compileall .` gate was investigated and classified
   as a verification policy bug: it recurses into ignored `.venv/`,
   `workspace/`, `data/`, temporary Reliability worktrees, and build
   artifacts rather than maintained source. See `ENGINEERING_RULES.md` and
   `ARCHITECTURE.md`.

2. **Create focused commits only if required verification passes.**
   The owner requested commits for the Reliability Engine E2E verification
   and AutoCorp Chat, and explicitly said not to push.

3. **Reliability Engine integration decision.** Repository evidence now
   includes an end-to-end test for `ReliabilityOrchestrator.run()` against
   a disposable git repository. The remaining decision is whether/how the
   owner wants this subsystem exposed beyond tests. No dedicated
   Reliability Engine CLI command exists in this working tree.

## Technical Debt

- `config.py`'s `APP_VERSION` still reads `"0.1.0"` despite later tags and
  many later commits. Either start updating it or remove it if it is not a
  meaningful release indicator.
- Two unrelated phase-numbering schemes exist in history: tagged SQLite
  Generation "Phase 1-7" work and untagged repository-intelligence
  "Phase 1A-1Y" work. New documentation should avoid adding a third
  overloaded phase label.
- The Reliability Engine performs full repository scans in
  `DependencyGraph.build()` and `CodebaseRAGIndex.rebuild()` during run
  setup. No caching layer is evidenced in the repository.

## Known Bugs

- CloneCast's production database, discovered through AutoCorp's
  validation tooling, has 9 pre-existing foreign-key violations in legacy
  chapter-script tables. This is external to AutoCorp.
- CloneCast audio generation has repeatedly failed CloneCast-side QC due
  to peak clipping during real disposable runs. This blocks CloneCast
  workflow/publishing validation from reaching a full successful PASS, but
  it is not an AutoCorp implementation defect.

## Future Improvements

- Add a dedicated Reliability Engine CLI entry point only if the owner
  approves production integration after reviewing the new E2E evidence.
- Consider whether AutoCorp Chat should gain provider-backed natural
  language synthesis later. The first production version intentionally
  stays repository-backed and deterministic instead of acting as a generic
  LLM wrapper.

## Blocked Work

- Official phase completion remains owner-gated by
  `PHASE_COMPLETION_POLICY.md`.
- External CloneCast audio/QC defects remain outside this repository.

## Missing Dependencies

- The Reliability Engine requires the dependency changes currently present
  in the working tree (`chromadb`, `PyYAML`, `mypy`, `ruff`) to be committed
  with the subsystem before a fresh checkout can run the same code paths.
- The direct `python` executable is unavailable in this shell through
  pyenv unless a version is selected. Repository verification uses
  `.venv/bin/python`, matching the test-suite command used throughout
  `AI_ENGINEERING/`.
