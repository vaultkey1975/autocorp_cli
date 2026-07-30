#!/usr/bin/env python3
"""N-sample patch voting for high-blast-radius subtasks."""

from dataclasses import dataclass

from reliability_engine import grammar_constraints, patch_apply, state_store
from reliability_engine.env_isolation import isolated_test_environment
from reliability_engine.static_gate import StaticGate
from reliability_engine.test_loop import (
    BLOCK_FORMAT_REPAIR_PROMPT,
    EDIT_BLOCK_SYSTEM_PROMPT,
    RELEVANCE_REPAIR_PROMPT,
    TEST_COVERAGE_REPAIR_PROMPT,
    blocks_include_identifier,
    blocks_include_test_coverage,
    requested_identifier,
    test_coverage_expected,
)


@dataclass
class CandidateResult:
    ok: bool
    diff: str = ""
    error: str = ""
    attempts: int = 0


@dataclass
class GeneratedCandidate:
    raw: str
    prompt: str = ""


class SelfConsistencyRunner:
    def __init__(self, tester, builder_engine, n_samples: int = 3,
                 trigger_blast_radius: int = 5, static_gate: StaticGate | None = None):
        self.tester = tester
        self.builder_engine = builder_engine
        self.n_samples = int(n_samples)
        self.trigger_blast_radius = int(trigger_blast_radius)
        self.static_gate = static_gate or StaticGate()

    def should_run(self, subtask: dict, touches_core: bool = False) -> bool:
        return touches_core or int(subtask.get("blast_radius") or 0) >= self.trigger_blast_radius

    def choose(self, workspace: str, plan: dict, prompts: list[str]) -> CandidateResult:
        """Try each candidate diff in turn, keeping the first that passes the
        static gate and the real test command. Uses run_delta (not the
        absolute run()) for the same reason choose_edit() below does: an
        environment missing ruff/flake8/mypy must not reject every candidate
        outright - only issues newly introduced by the candidate itself
        should count."""
        last_error = ""
        static_before = self.static_gate.collect_issues(workspace)
        for prompt in prompts[: self.n_samples]:
            try:
                diff = grammar_constraints.validate_builder_patch(self.builder_engine.generate(prompt))
            except Exception as exc:  # noqa: BLE001 - candidate failed validation
                last_error = str(exc)
                continue
            applied = patch_apply.apply_diff(workspace, diff)
            if not applied.ok:
                last_error = applied.stderr or applied.stdout
                continue
            static = self.static_gate.run_delta(workspace, [], static_before)
            if not static.ok:
                last_error = static.output
                patch_apply.reverse_diff(workspace, diff)
                continue
            with isolated_test_environment():
                result = self.tester.test(workspace, plan)
            if result.ok:
                return CandidateResult(True, diff, attempts=len(prompts[: self.n_samples]))
            last_error = result.output
            patch_apply.reverse_diff(workspace, diff)
        return CandidateResult(False, error=last_error, attempts=len(prompts[: self.n_samples]))

    def choose_edit(self, workspace: str, plan: dict, subtask: dict, candidate_generator,
                    model_used: str = "") -> CandidateResult:
        last_error = ""
        files = [str(path) for path in subtask.get("files_touched", [])]
        target = files[0] if files else ""
        identifier = requested_identifier(str(subtask.get("description", "")))
        subtask_id = int(subtask.get("id", 0))
        for index in range(self.n_samples):
            reset = patch_apply.reset_paths(workspace, files)
            if not reset.ok:
                last_error = reset.stderr or reset.stdout
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, "", "not_run", "reset_failed", last_error,
                    )
                continue
            raw = ""
            prompt = ""
            try:
                generated = candidate_generator(index + 1, last_error)
                if isinstance(generated, GeneratedCandidate):
                    raw = generated.raw
                    prompt = generated.prompt
                else:
                    raw = str(generated)
                try:
                    blocks = patch_apply.parse_replace_blocks(raw)
                except ValueError:
                    repair_prompt = (
                        f"{prompt}\n\n{BLOCK_FORMAT_REPAIR_PROMPT}\n\nLAST OUTPUT:\n{raw}"
                    )
                    raw = self.builder_engine.generate(repair_prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)
                    blocks = patch_apply.parse_replace_blocks(raw)
            except Exception as exc:  # noqa: BLE001 - candidate failed validation
                last_error = str(exc)
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, "", "not_run", "not_run",
                        last_error, raw_output=raw, prompt=prompt,
                    )
                continue
            grounding = patch_apply.verify_replace_blocks(workspace, target, blocks)
            if not grounding.ok:
                last_error = grounding.message
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, "", "not_run", "not_run",
                        last_error, raw_output=raw, prompt=prompt,
                    )
                continue
            if not blocks_include_identifier(blocks, identifier):
                relevance_prompt = (
                    f"{prompt}\n\n"
                    f"{RELEVANCE_REPAIR_PROMPT.format(identifier=identifier)}\n\n"
                    f"LAST OUTPUT:\n{raw}"
                )
                try:
                    raw = self.builder_engine.generate(relevance_prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)
                    blocks = patch_apply.parse_replace_blocks(raw)
                    grounding = patch_apply.verify_replace_blocks(workspace, target, blocks)
                    if not grounding.ok:
                        raise ValueError(grounding.message)
                    if not blocks_include_identifier(blocks, identifier):
                        raise ValueError(
                            f"task asked for identifier named {identifier}, but replacement blocks do not include it"
                        )
                except Exception as exc:  # noqa: BLE001 - relevance repair failed validation
                    last_error = str(exc)
                    if subtask_id:
                        state_store.increment_attempts(subtask_id)
                        state_store.record_attempt(
                            subtask_id, index + 1, model_used, "", "not_run", "not_run",
                            last_error, raw_output=raw, prompt=prompt,
                        )
                    continue
            if test_coverage_expected(str(subtask.get("description", ""))) and not blocks_include_test_coverage(
                blocks, target, identifier
            ):
                target_name = identifier or "requested change"
                repair_prompt = (
                    f"{prompt}\n\n"
                    f"{TEST_COVERAGE_REPAIR_PROMPT.format(target=target_name)}"
                    + f"\n\nLAST OUTPUT:\n{raw}"
                )
                try:
                    raw = self.builder_engine.generate(repair_prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)
                    blocks = patch_apply.parse_replace_blocks(raw)
                    grounding = patch_apply.verify_replace_blocks(workspace, target, blocks)
                    if not grounding.ok:
                        raise ValueError(grounding.message)
                    if identifier and not blocks_include_identifier(blocks, identifier):
                        raise ValueError(
                            f"task asked for identifier named {identifier}, but replacement blocks do not include it"
                        )
                    if not blocks_include_test_coverage(blocks, target, identifier):
                        raise ValueError(
                            f"task asked for a test, but replacement blocks do not include a test for {target_name}"
                        )
                except Exception as exc:  # noqa: BLE001 - coverage repair failed validation
                    last_error = str(exc)
                    if subtask_id:
                        state_store.increment_attempts(subtask_id)
                        state_store.record_attempt(
                            subtask_id, index + 1, model_used, "", "not_run", "not_run",
                            last_error, raw_output=raw, prompt=prompt,
                        )
                    continue
            static_before = self.static_gate.collect_issues(workspace, files)
            applied = patch_apply.apply_replace_blocks(workspace, target, blocks)
            if not applied.ok:
                last_error = applied.stderr or applied.stdout
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, raw, "not_run", "patch_failed",
                        last_error, raw_output=raw, prompt=prompt,
                    )
                patch_apply.reset_paths(workspace, files)
                continue
            static = self.static_gate.run_delta(workspace, files, static_before)
            for issue in static.details.get("pre_existing", []):
                if subtask_id:
                    state_store.record_known_issue(
                        subtask_id,
                        "pre-existing static issue, not introduced by this change",
                        issue.render(),
                    )
            if not static.ok:
                last_error = static.output
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, raw,
                        f"{static.stage}:failed", "not_run", last_error, raw_output=raw, prompt=prompt,
                    )
                patch_apply.reset_paths(workspace, files)
                continue
            with isolated_test_environment():
                result = self.tester.test(workspace, plan)
            if result.ok:
                if subtask_id:
                    state_store.increment_attempts(subtask_id)
                    state_store.record_attempt(
                        subtask_id, index + 1, model_used, raw,
                        "passed", "passed", "", raw_output=raw, prompt=prompt,
                    )
                return CandidateResult(True, raw, attempts=index + 1)
            last_error = result.output
            if subtask_id:
                state_store.increment_attempts(subtask_id)
                state_store.record_attempt(
                    subtask_id, index + 1, model_used, raw,
                    "passed", "failed", last_error, raw_output=raw, prompt=prompt,
                )
            patch_apply.reset_paths(workspace, files)
        patch_apply.reset_paths(workspace, files)
        return CandidateResult(False, error=last_error, attempts=self.n_samples)
