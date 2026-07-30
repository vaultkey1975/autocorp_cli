#!/usr/bin/env python3
"""Core test-fail-fix loop for a single reliability subtask."""

import re
from dataclasses import dataclass

from brains.base_engine import EngineError
from reliability_engine import grammar_constraints, patch_apply, state_store
from reliability_engine.env_isolation import isolated_test_environment
from reliability_engine.static_gate import StaticGate


PATCH_SYSTEM_PROMPT = """You are the Builder Brain inside AutoCorp's Reliability Engine.
Return ONLY a unified diff that fixes the requested subtask. No markdown fences."""

EDIT_BLOCK_SYSTEM_PROMPT = """You are the Builder Brain inside AutoCorp's Reliability Engine.
Return ONLY FIND/REPLACE blocks that edit exact existing file text. No markdown fences."""

FORMAT_REPAIR_PROMPT = """Your last output was not a raw unified diff.
Return ONLY the unified diff, no markdown fences, no commentary."""

BLOCK_FORMAT_REPAIR_PROMPT = """Your last output was not valid FIND/REPLACE blocks.
Return ONLY blocks in this exact form, no markdown fences, no commentary:
<<<<<<< FIND
exact existing code
=======
replacement code
>>>>>>> REPLACE"""

GROUNDING_REPAIR_PROMPT = """Your diff's context does not match the real file at this location.
Here is the actual current content around the mismatch. Generate a new unified
diff against this exact text. Return ONLY the diff, no markdown, no commentary."""

BLOCK_GROUNDING_REPAIR_PROMPT = """Your FIND block does not match the real file.
The FIND section must be an exact, character-for-character copy of existing code
from the actual file. Here is the closest real excerpt. Generate new FIND/REPLACE
blocks against this exact text. Return ONLY the blocks, no markdown, no commentary."""

RELEVANCE_REPAIR_PROMPT = """The task asked for an identifier named {identifier}, but your change does not include it.
Regenerate FIND/REPLACE blocks that actually implement the requested change and
include {identifier} in the replacement code. Return ONLY the blocks, no markdown,
no commentary."""

TEST_COVERAGE_REPAIR_PROMPT = """The task asked for a test, but your change does not include one that tests {target}.
Add or update a test file for {target}. Return FIND/REPLACE blocks only, no
markdown, no commentary."""


@dataclass
class LoopResult:
    ok: bool
    attempts: int
    diff: str = ""
    error: str = ""
    blocked: bool = False


def requested_identifier(text: str) -> str:
    patterns = [
        r"\bnamed\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
        r"\bcalled\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
        r"\b(?:function|method|class|variable)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def test_coverage_expected(text: str) -> bool:
    patterns = [
        r"\badd(?:\s+or\s+update)?\s+(?:a\s+)?(?:real\s+)?tests?\b",
        r"\bwrite\s+(?:a\s+)?tests?\b",
        r"\bwith\s+tests?\b",
        r"\binclude\s+(?:a\s+)?tests?\b",
        r"\bupdate\s+(?:a\s+)?tests?\b",
    ]
    return any(re.search(pattern, text or "", re.IGNORECASE) for pattern in patterns)


setattr(test_coverage_expected, "__test__", False)


def is_repo_test_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1]
    return normalized.startswith("tests/") and (
        name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")
    )


def block_paths(blocks: list[patch_apply.ReplaceBlock], default_path: str) -> list[str]:
    paths = []
    for block in blocks:
        path = block.path or default_path
        if path not in paths:
            paths.append(path)
    return paths


def blocks_include_identifier(blocks: list[patch_apply.ReplaceBlock], identifier: str) -> bool:
    if not identifier:
        return True
    return any(identifier in block.replace for block in blocks)


def blocks_include_test_coverage(blocks: list[patch_apply.ReplaceBlock], default_path: str,
                                 identifier: str) -> bool:
    test_blocks = [
        block for block in blocks
        if is_repo_test_file(block.path or default_path)
    ]
    if not test_blocks:
        return False
    if not identifier:
        return True
    return any(identifier in block.replace for block in test_blocks)


def build_patch_prompt(subtask: dict, context: list[dict], error_trace: str = "") -> str:
    files = ", ".join(subtask.get("files_touched") or [])
    prompt = (
        f"SUBTASK: {subtask.get('description', '')}\n"
        f"FILES TO TOUCH: {files}\n\n"
        "RELEVANT CONTEXT:\n"
    )
    for item in context:
        meta = item.get("metadata") or {}
        prompt += f"\n--- {meta.get('path', 'context')}:{meta.get('start_line', 1)} ---\n"
        prompt += str(item.get("document", ""))[:3000] + "\n"
    if error_trace:
        prompt += f"\nPREVIOUS FAILURE:\n{error_trace[:5000]}\n"
    return prompt + "\n" + grammar_constraints.prompt_suffix()


class ReliabilityTestLoop:
    def __init__(self, tester, builder_engine, max_attempts: int = 5,
                 static_gate: StaticGate | None = None, model_used: str = "",
                 patch_generator=None):
        self.tester = tester
        self.builder_engine = builder_engine
        self.max_attempts = int(max_attempts)
        self.static_gate = static_gate or StaticGate()
        self.model_used = model_used or getattr(builder_engine, "model", getattr(builder_engine, "name", "unknown"))
        self.patch_generator = patch_generator

    def generate_patch_raw(self, subtask: dict, context: list[dict], error_trace: str = "") -> str:
        if self.patch_generator is not None:
            return self.patch_generator(subtask, context, error_trace)
        return self.builder_engine.generate(
            build_patch_prompt(subtask, context, error_trace),
            system=PATCH_SYSTEM_PROMPT,
        )

    def reprompt_raw_diff(self, raw_output: str) -> str:
        prompt = f"{FORMAT_REPAIR_PROMPT}\n\nLAST OUTPUT:\n{raw_output}"
        return self.builder_engine.generate(prompt, system=PATCH_SYSTEM_PROMPT)

    def reprompt_raw_blocks(self, raw_output: str) -> str:
        prompt = f"{BLOCK_FORMAT_REPAIR_PROMPT}\n\nLAST OUTPUT:\n{raw_output}"
        return self.builder_engine.generate(prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)

    def reprompt_grounded_diff(self, raw_output: str, grounding_message: str) -> str:
        prompt = (
            f"{GROUNDING_REPAIR_PROMPT}\n\n"
            f"MISMATCH:\n{grounding_message}\n\n"
            f"LAST OUTPUT:\n{raw_output}"
        )
        return self.builder_engine.generate(prompt, system=PATCH_SYSTEM_PROMPT)

    def reprompt_grounded_blocks(self, raw_output: str, grounding_message: str) -> str:
        prompt = (
            f"{BLOCK_GROUNDING_REPAIR_PROMPT}\n\n"
            f"MISMATCH:\n{grounding_message}\n\n"
            f"LAST OUTPUT:\n{raw_output}"
        )
        return self.builder_engine.generate(prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)

    def reprompt_relevant_blocks(self, raw_output: str, identifier: str) -> str:
        prompt = (
            RELEVANCE_REPAIR_PROMPT.format(identifier=identifier)
            + f"\n\nLAST OUTPUT:\n{raw_output}"
        )
        return self.builder_engine.generate(prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)

    def reprompt_test_coverage_blocks(self, raw_output: str, identifier: str) -> str:
        target = identifier or "the requested change"
        prompt = (
            TEST_COVERAGE_REPAIR_PROMPT.format(target=target)
            + f"\n\nLAST OUTPUT:\n{raw_output}"
        )
        return self.builder_engine.generate(prompt, system=EDIT_BLOCK_SYSTEM_PROMPT)

    def generate_patch(self, subtask: dict, context: list[dict], error_trace: str = "") -> str:
        raw = self.generate_patch_raw(subtask, context, error_trace)
        return grammar_constraints.validate_builder_patch(raw)

    def generate_patch_with_format_repair(self, subtask: dict, context: list[dict],
                                          error_trace: str = "") -> tuple[str, str]:
        raw = self.builder_engine.generate(
            build_patch_prompt(subtask, context, error_trace),
            system=PATCH_SYSTEM_PROMPT,
        ) if self.patch_generator is None else self.patch_generator(subtask, context, error_trace)
        try:
            return grammar_constraints.validate_builder_patch(raw), raw
        except ValueError:
            repaired_raw = self.reprompt_raw_diff(raw)
            return grammar_constraints.validate_builder_patch(repaired_raw), repaired_raw

    def generate_blocks_with_format_repair(self, subtask: dict, context: list[dict],
                                           error_trace: str = "") -> tuple[list[patch_apply.ReplaceBlock], str]:
        raw = self.generate_patch_raw(subtask, context, error_trace)
        try:
            return patch_apply.parse_replace_blocks(raw), raw
        except ValueError:
            repaired_raw = self.reprompt_raw_blocks(raw)
            return patch_apply.parse_replace_blocks(repaired_raw), repaired_raw

    def generate_grounded_patch(self, workspace: str, subtask: dict, context: list[dict],
                                error_trace: str = "") -> tuple[str, str, str]:
        raw_output = ""
        try:
            diff, raw_output = self.generate_patch_with_format_repair(subtask, context, error_trace)
        except (EngineError, ValueError) as exc:
            return "", raw_output, str(exc)

        grounding = patch_apply.verify_diff_grounding(workspace, diff)
        if grounding.ok:
            return diff, raw_output, ""

        repaired_raw = self.reprompt_grounded_diff(raw_output, grounding.message)
        try:
            repaired_diff = grammar_constraints.validate_builder_patch(repaired_raw)
        except ValueError as exc:
            return "", repaired_raw, str(exc)
        repaired_grounding = patch_apply.verify_diff_grounding(workspace, repaired_diff)
        if not repaired_grounding.ok:
            return repaired_diff, repaired_raw, repaired_grounding.message
        return repaired_diff, repaired_raw, ""

    def generate_grounded_blocks(self, workspace: str, subtask: dict, context: list[dict],
                                 error_trace: str = "") -> tuple[list[patch_apply.ReplaceBlock], str, str]:
        target = str((subtask.get("files_touched") or [""])[0])
        raw_output = ""
        try:
            blocks, raw_output = self.generate_blocks_with_format_repair(subtask, context, error_trace)
        except (EngineError, ValueError) as exc:
            return [], raw_output, str(exc)

        grounding = patch_apply.verify_replace_blocks(workspace, target, blocks)
        if grounding.ok:
            return blocks, raw_output, ""

        repaired_raw = self.reprompt_grounded_blocks(raw_output, grounding.message)
        try:
            repaired_blocks = patch_apply.parse_replace_blocks(repaired_raw)
        except ValueError as exc:
            return [], repaired_raw, str(exc)
        repaired_grounding = patch_apply.verify_replace_blocks(workspace, target, repaired_blocks)
        if not repaired_grounding.ok:
            return repaired_blocks, repaired_raw, repaired_grounding.message
        return repaired_blocks, repaired_raw, ""

    def generate_grounded_relevant_blocks(self, workspace: str, subtask: dict, context: list[dict],
                                          error_trace: str = "") -> tuple[list[patch_apply.ReplaceBlock], str, str]:
        blocks, raw_output, error = self.generate_grounded_blocks(workspace, subtask, context, error_trace)
        if error:
            return blocks, raw_output, error
        identifier = requested_identifier(str(subtask.get("description", "")))
        if blocks_include_identifier(blocks, identifier):
            relevance_error = ""
        else:
            relevance_error = f"task asked for identifier named {identifier}, but replacement blocks do not include it"
            repaired_raw = self.reprompt_relevant_blocks(raw_output, identifier)
            try:
                blocks = patch_apply.parse_replace_blocks(repaired_raw)
            except ValueError as exc:
                return [], repaired_raw, str(exc)
            raw_output = repaired_raw
            target = str((subtask.get("files_touched") or [""])[0])
            grounding = patch_apply.verify_replace_blocks(workspace, target, blocks)
            if not grounding.ok:
                return blocks, raw_output, grounding.message
            if not blocks_include_identifier(blocks, identifier):
                return blocks, raw_output, relevance_error

        request = str(subtask.get("description", ""))
        if not test_coverage_expected(request):
            return blocks, raw_output, ""
        target = str((subtask.get("files_touched") or [""])[0])
        if blocks_include_test_coverage(blocks, target, identifier):
            return blocks, raw_output, ""

        repaired_raw = self.reprompt_test_coverage_blocks(raw_output, identifier)
        try:
            repaired_blocks = patch_apply.parse_replace_blocks(repaired_raw)
        except ValueError as exc:
            return [], repaired_raw, str(exc)
        grounding = patch_apply.verify_replace_blocks(workspace, target, repaired_blocks)
        if not grounding.ok:
            return repaired_blocks, repaired_raw, grounding.message
        if identifier and not blocks_include_identifier(repaired_blocks, identifier):
            return repaired_blocks, repaired_raw, relevance_error
        if not blocks_include_test_coverage(repaired_blocks, target, identifier):
            target_name = identifier or "requested change"
            return repaired_blocks, repaired_raw, (
                f"task asked for a test, but replacement blocks do not include a test for {target_name}"
            )
        return repaired_blocks, repaired_raw, ""

    def run(self, workspace: str, plan: dict, subtask: dict, context: list[dict]) -> LoopResult:
        last_error = ""
        diff = ""
        edit_mode = bool(subtask.get("edit_mode"))
        touched_files = [str(path) for path in subtask.get("files_touched", [])]
        for attempt in range(1, self.max_attempts + 1):
            reset = patch_apply.reset_paths(workspace, touched_files)
            if not reset.ok:
                last_error = reset.stderr or reset.stdout
                state_store.increment_attempts(subtask["id"])
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, "", "not_run", "reset_failed",
                    last_error, raw_output="",
                )
                continue
            raw_output = ""
            blocks: list[patch_apply.ReplaceBlock] = []
            if edit_mode:
                blocks, raw_output, preapply_error = self.generate_grounded_relevant_blocks(
                    workspace, subtask, context, last_error
                )
                diff = raw_output
            else:
                diff, raw_output, preapply_error = self.generate_grounded_patch(
                    workspace, subtask, context, last_error
                )
            state_store.increment_attempts(subtask["id"])
            if preapply_error:
                last_error = preapply_error
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, "", "not_run", "not_run",
                    last_error, raw_output=raw_output,
                )
                continue

            static_before = self.static_gate.collect_issues(workspace, touched_files)
            if edit_mode:
                target = str((subtask.get("files_touched") or [""])[0])
                patch_result = patch_apply.apply_replace_blocks(workspace, target, blocks)
            else:
                patch_result = patch_apply.apply_diff(workspace, diff)
            if not patch_result.ok:
                last_error = patch_result.stderr or patch_result.stdout
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, diff, "not_run", "patch_failed",
                    last_error, raw_output=raw_output,
                )
                continue

            gate_result = self.static_gate.run_delta(workspace, touched_files, static_before)
            for issue in gate_result.details.get("pre_existing", []):
                state_store.record_known_issue(
                    subtask["id"],
                    "pre-existing static issue, not introduced by this change",
                    issue.render(),
                )
            if not gate_result.ok:
                last_error = gate_result.output
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, diff,
                    f"{gate_result.stage}:failed", "not_run", last_error, raw_output=raw_output
                )
                patch_apply.reset_paths(workspace, touched_files)
                continue

            with isolated_test_environment():
                test_result = self.tester.test(workspace, plan)
            if test_result.ok:
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, diff,
                    "passed", "passed", "", raw_output=raw_output
                )
                return LoopResult(True, attempt, diff)
            if getattr(test_result, "blocked", False):
                last_error = getattr(test_result, "reason", "") or "test command blocked"
                state_store.record_attempt(
                    subtask["id"], attempt, self.model_used, diff,
                    "passed", "blocked", last_error, raw_output=raw_output
                )
                patch_apply.reset_paths(workspace, touched_files)
                return LoopResult(False, attempt, diff, last_error, blocked=True)

            last_error = test_result.output
            state_store.record_attempt(
                subtask["id"], attempt, self.model_used, diff,
                "passed", "failed", last_error, raw_output=raw_output
            )
            patch_apply.reset_paths(workspace, touched_files)

        patch_apply.reset_paths(workspace, touched_files)
        return LoopResult(False, self.max_attempts, diff, last_error)
