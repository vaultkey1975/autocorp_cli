# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item — see `DOCUMENTATION_POLICY.md`.

---

## Immediate work

1. **Decide the fate of the uncommitted Phase 1X/1Y work**
   (`brains/workflow_test.py`, `autocorp.py`'s `workflow-test`/
   `publish-test` wiring, `tests/test_workflow_character_id_propagation.py`).
   It is implemented and has real, if incomplete, verification (see
   "Blocked work" below) — the repository owner needs to review and either
   commit it or direct further changes.
2. **Decide the fate of the uncommitted `quick-podcast` CLI wiring** in
   `autocorp.py`. The module it depends on is already committed
   (`143825a`) and is otherwise unreachable.
3. **Decide the fate of the Reliability Engine.** It is untested-in-
   production (no CLI entry point), but has its own passing unit tests and
   represents 2,346+ lines of real, working code sitting disconnected from
   the application. Either integrate it deliberately (with its own
   `ARCHITECTURE.md` section once its actual design is reviewed) or
   explicitly defer/remove it — leaving it in this state indefinitely
   means every future session has to re-discover it.
4. **Fix the missing `README.md` update.** It describes only the original
   `v0.1.0` architecture. At minimum, it should mention that the CLI now
   has 13–15 subcommands, not 5, and point to `AI_ENGINEERING/` for the
   rest.

## Technical debt

- `config.py`'s `APP_VERSION` has never been updated from `"0.1.0"` across
  14 subsequent tags and 60+ further commits. Either start updating it or
  remove it if it's not meaningful to keep.
- Two "Phase 1" numbering schemes exist in this repository's history (see
  `PROJECT_MEMORY.md`) with no disambiguating label anywhere except this
  documentation set. Consider renaming one era's references (in docstrings
  and commit-message search results, going forward) to avoid future
  confusion — this cannot retroactively fix commit messages, but new work
  should not add a third overloaded "Phase 1."
- `brains/model_router.py` and `reliability_engine/model_router.py` share a
  filename. If the Reliability Engine is ever integrated, this collision
  needs to be resolved (rename one, or ensure import paths never ambiguity
  between them) before it's wired in.

## Known bugs

All five items below are documented in detail in `PHASES.md` under
"Phase 1G" and were found by a real, adversarial-input audit
(`claude_phase_1g_audit.txt`), not by static review:

1. `--provider claude` on `propose-repair` raises a `TypeError` at engine
   construction and never actually works.
2. `--provider deepseek` on `propose-repair`, without an API key
   configured, resolves the DeepSeek *API* model name into the field used
   by the *local* Ollama transport — a field-conflation bug in
   `brains/providers.py`'s `_resolve_model`.
3. Secret-file exclusion in `brains/repair_proposal.py` misses compound
   filenames such as `db_credentials.json` or `user_auth.py` — the pattern
   requires the sensitive keyword to start a path component.
4. Inline secret redaction in the same module misses plain `password =`,
   bare `SECRET =`, `client_secret`, and credentials embedded in
   connection-string URLs.
5. `tests/test_provider_contracts.py::test_no_silent_fallback` fails (makes
   a real network call instead of testing the intended offline path) in
   any environment with an ambient `DEEPSEEK_API_KEY` — confirmed
   reproducible in this repository's own development environment.

Additionally:

6. CloneCast's production database (external system, not this repository,
   but discovered via this repository's own verification tooling) has 9
   pre-existing foreign-key constraint violations in its legacy
   chapter-script tables. Not this repository's bug to fix, but worth
   surfacing to whoever maintains CloneCast.

## Future improvements

Unable to determine any repository-evidenced future improvement beyond
fixing the items already listed above. See `ROADMAP.md`'s
`FUTURE PLANNING REQUIRED` section. Do not add speculative improvements to
this list without a specific piece of evidence (a docstring, a comment, a
report) to ground them.

## Blocked work

- **Phase 1Y (Production Publishing Validation) cannot reach a full
  successful PASS** because CloneCast's own QC logic reliably rejects the
  synthesized audio for peak clipping. Confirmed reproducible across two
  independent real runs in Phase 1Y and two more under Quick Podcast
  verification (four total, 100% reproduction rate on every real attempt
  made). This is external to AutoCorp; per
  `AI_ENGINEERING_CONSTITUTION.md` §10, AutoCorp's correct behavior here is
  to detect and report it accurately, which it already does. Unblocking
  this requires a fix on the CloneCast side (likely peak
  limiting/normalization in its Chatterbox output handling — see
  `PHASES.md`, Phase 1Y, for the exact error text and measured values), not
  an AutoCorp change.
- **`quick-podcast` has never been observed completing successfully
  end-to-end**, for the identical reason.

## Missing dependencies

- The Reliability Engine's uncommitted `requirements.txt` changes add
  `chromadb` and `PyYAML`. These are not installed by the currently
  committed `requirements.txt`, so a fresh `HEAD` checkout following only
  committed instructions would not have them available even if the
  Reliability Engine code were later wired in without also committing the
  dependency change.
- `mypy` and `ruff` are added to the uncommitted `requirements-dev.txt` but
  are not part of any committed tooling configuration or CI (`.github/`
  does not exist in this repository as of this writing) — if these are
  meant to be enforced, that enforcement does not yet exist anywhere
  committed.
