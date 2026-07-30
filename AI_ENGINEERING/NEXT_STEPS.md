# Next Steps

Live punch list. Update this in the same session that resolves or
discovers any item — see `DOCUMENTATION_POLICY.md`.

---

## Immediate work

1. **Phase 1X/1Y work stays uncommitted for now, by owner decision
   (2026-07-29).** The repository owner reviewed the three independent
   uncommitted efforts recorded in `CURRENT_PHASE.md` and directed: keep
   iterating on Phase 1X/1Y (`brains/workflow_test.py`, `autocorp.py`'s
   `publish-test` wiring, `tests/test_workflow_character_id_propagation.py`)
   uncommitted rather than committing it now. Do not commit this work
   without a fresh, explicit instruction to do so.
2. ~~Decide the fate of the uncommitted `quick-podcast` CLI wiring~~ —
   **Done (2026-07-29).** Committed as `53f0d7d "feat: wire quick-podcast
   CLI subcommand"`, by owner decision. `quick-podcast` is now reachable
   from a fresh checkout of `main`.
3. **Reliability Engine: production-readiness review complete (2026-07-30)
   — VERDICT: NOT READY.** Full findings are in `ARCHITECTURE.md`'s
   "Reliability Engine" section, including a dated "Production-readiness
   verdict" subsection. Summary: it is a second, parallel build/repair
   orchestration pipeline (duplicates rather than composes with
   `core/orchestrator.py::Session`, a product decision for the owner, not
   resolved here), architecturally complete and internally consistent (no
   dead files, no dead APIs, no TODOs, no placeholders, no mocks), but its
   true end-to-end entry point (`ReliabilityOrchestrator.run()`) has never
   been exercised by any test in this repository's history — confirmed
   directly a third time this session. That gap is not theoretical: a third
   independent pass (2026-07-30) found and fixed a genuine bug missed by
   two prior "independent verification" passes —
   `SelfConsistencyRunner.choose()` called the unsafe `StaticGate.run()`
   instead of the safe `run_delta()` its sibling `choose_edit()` already
   used correctly, meaning self-consistency voting for core-touching/
   high-blast-radius changes would silently fail in any environment missing
   `ruff`/`mypy`. Three bugs fixed total across two sessions (worktree-ID
   collision, `model_router.py` naming collision, this self-consistency
   bug); `chromadb`/`PyYAML` packaging and uncached full-repo rescans remain
   unaddressed by design. **A real end-to-end test of `run()` is the single
   remaining precondition before integration could be recommended.** Until
   that exists, do not import or register any `reliability_engine/` module
   and do not add a CLI subcommand — a capability that can write real
   changes to the user's repository (`WorktreeSandbox.merge_to_main`)
   should not ship through a path that has never once completed
   successfully end-to-end.
4. ~~Fix the missing `README.md` update~~ — **Done (2026-07-29).** Added a
   "Current commands" section listing all fifteen subcommands (fourteen
   committed as of `53f0d7d`, `publish-test` still uncommitted per item 1
   above), and pointers to `AI_ENGINEERING/ARCHITECTURE.md`/`CURRENT_PHASE.md`
   for the parts of the system the original four-brains description predates.

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
- ~~`brains/model_router.py::ModelRouter` and
  `reliability_engine/model_router.py::ReliabilityModelRouter` share a
  filename~~ — **fixed 2026-07-29**, the latter is now
  `reliability_engine/model_availability.py`.

## Known bugs

The five items below were originally documented here verbatim from
`claude_phase_1g_audit.txt` without being re-verified against current
`HEAD` — exactly the anti-pattern `AI_ENGINEERING_CONSTITUTION.md` §3 warns
against ("a prior AI-generated report... is evidence to be checked against
the repository, never a substitute for checking it"). Re-verified directly
against current source on 2026-07-29; corrected below. See `PHASES.md`
Phase 1G for the full, corrected account.

1. ~~`--provider claude` raises a `TypeError` at construction~~ — **Already
   fixed**, by `ea71d54 "fix: harden proposal secret and provider safety"`,
   which predates this session. `brains/providers.py` no longer passes
   `model=` when constructing `ClaudeEngine`. Verified directly: calling
   `generate_proposal_json(..., provider="claude")` now reaches
   `engine.generate()` (no `TypeError`); regression test
   `test_claude_does_not_raise_type_error` in
   `tests/test_provider_contracts.py` passes.
2. ~~`--provider deepseek` without an API key resolves the wrong local
   model tag~~ — **Already fixed**, same commit. `generate_proposal_json`
   now returns a clean `blocked=True, error="DeepSeek API key not
   configured..."` result *before* ever constructing `DeepSeekEngine` when
   no key is present — the conflated-field code path the audit found is no
   longer reachable via the no-key case. Verified directly with
   `DEEPSEEK_API_KEY` unset; regression tests
   `test_deepseek_missing_credentials_fails_cleanly` and
   `test_deepseek_never_falls_back_to_ollama` pass.
3. ~~Secret-file exclusion misses compound filenames~~ — **Already fixed**,
   same commit. `_SECRET_FILE_PATTERNS` in `brains/repair_proposal.py` now
   anchors on `(^|[/_])` instead of `(^|/)`, so `db_credentials.json`,
   `app_secrets.py`, `user_auth.py`, and `my_token_store.py` are all
   correctly excluded. Verified directly against all four filenames named
   in the original audit.
4. **Inline secret redaction — partially fixed already, two real gaps
   remained and were fixed this session (2026-07-29).** `ea71d54` had
   already added redaction for `api_key`/`AUTH_TOKEN`/`client_secret`/
   `AWS_SECRET_ACCESS_KEY`-style compound keys. Two of the audit's nine
   adversarial cases were still genuinely unredacted as of this session's
   start: `DB_PASSWORD = "..."` (a compound key — the old pattern's `\b`
   word boundary doesn't cross an underscore, so it only matched bare
   `password`/`secret`) and `{"Authorization": "Bearer ..."}` (JSON-quoted
   form — the old pattern only matched the unquoted `Authorization: Bearer
   ...` shape). Both are fixed in `brains/repair_proposal.py`'s
   `_INLINE_SECRET_RE`/`_redact_inline_secrets` (this session): the plain
   password/secret alternative now treats `_` as a boundary the same way
   the file-pattern fix does (catching `DB_PASSWORD` without false-
   positive-matching `secretary_name`), and the Authorization/Bearer
   alternative now allows optional surrounding quotes. All nine of the
   audit's original adversarial lines, plus a `secretary_name` false-
   positive check, are now covered by tests
   (`test_compound_password_key_redacted`,
   `test_compound_secret_key_does_not_match_secretary`,
   `test_quoted_json_authorization_bearer_redacted` in
   `tests/test_repair_proposal.py`).
5. ~~`tests/test_provider_contracts.py::test_no_silent_fallback` fails with
   an ambient `DEEPSEEK_API_KEY`~~ — **No longer applicable.** That specific
   test no longer exists; `ea71d54` replaced it with
   `test_claude_does_not_raise_type_error`,
   `test_deepseek_missing_credentials_fails_cleanly`,
   `test_deepseek_never_falls_back_to_ollama`, and
   `test_provider_tests_make_no_live_calls`. Verified directly: this
   session's development environment has a real `DEEPSEEK_API_KEY` set
   (the same condition that used to trigger the failure), and
   `tests/test_provider_contracts.py` passes in full (12/12) regardless.

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
