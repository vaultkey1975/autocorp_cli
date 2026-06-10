#!/usr/bin/env python3
"""
Engine-layer unit tests  (AutoCorp CLI - Phase 8A RED)
======================================================

Direct unit coverage for the code-generation engines that sit behind the
BaseEngine seam: LocalEngine (Ollama) and ClaudeEngine (the `claude -p` CLI).
This layer was previously exercised only indirectly via a fake engine in
test_verbatim_content.py; these tests pin its real behaviour.

Everything here runs FULLY OFFLINE:
  * LocalEngine tests patch `core.llm.generate` - Ollama is never contacted.
  * ClaudeEngine tests patch `subprocess.run` and `shutil.which` - the Claude
    CLI is never invoked and need not be installed.

RED note (Phase 8A): most of these characterise behaviour the engines already
implement, so they pass immediately and act as regression locks on a
previously-untested layer. The genuinely-RED additions are the uniform
`available()` contract tests, which fail until GREEN adds `available()` to the
BaseEngine contract (and thus to LocalEngine).
"""

import subprocess
from unittest.mock import patch

import pytest

from core import llm
from brains.base_engine import BaseEngine, EngineError
from brains.local_engine import LocalEngine
from brains.claude_engine import ClaudeEngine


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _Proc:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --------------------------------------------------------------------------- #
# LocalEngine (group A) - patches core.llm.generate, no Ollama
# --------------------------------------------------------------------------- #
def test_local_engine_default_model_is_llm_default():
    assert LocalEngine().model == llm.MODEL


def test_local_engine_honors_custom_model():
    assert LocalEngine(model="custom-model").model == "custom-model"


def test_local_engine_delegates_prompt_system_and_params():
    with patch("core.llm.generate", return_value="GENERATED") as g:
        engine = LocalEngine(model="m", temperature=0.5)
        out = engine.generate("THE PROMPT", system="THE SYSTEM")
    assert out == "GENERATED"
    g.assert_called_once_with(
        "THE PROMPT", system="THE SYSTEM", model="m", temperature=0.5
    )


def test_local_engine_returns_generated_text():
    with patch("core.llm.generate", return_value="hello world"):
        assert LocalEngine().generate("p") == "hello world"


def test_local_engine_wraps_ollama_error_as_engine_error():
    with patch("core.llm.generate", side_effect=llm.OllamaError("server down")):
        with pytest.raises(EngineError):
            LocalEngine().generate("p")


# --------------------------------------------------------------------------- #
# ClaudeEngine (group B) - patches subprocess.run + shutil.which, no CLI
# --------------------------------------------------------------------------- #
def test_claude_available_true_when_on_path():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"):
        assert ClaudeEngine().available() is True


def test_claude_available_false_when_missing():
    with patch("brains.claude_engine.shutil.which", return_value=None):
        assert ClaudeEngine().available() is False


def test_claude_missing_cli_raises_engine_error():
    with patch("brains.claude_engine.shutil.which", return_value=None):
        with pytest.raises(EngineError):
            ClaudeEngine().generate("p")


def test_claude_builds_args_with_print_flag():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="out")) as run:
        ClaudeEngine().generate("p")
    args = run.call_args.args[0]
    assert args[0] == "claude"
    assert "-p" in args


def test_claude_appends_system_prompt_when_given():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="out")) as run:
        ClaudeEngine().generate("p", system="SYSTEM-INSTRUCTION")
    args = run.call_args.args[0]
    assert "--append-system-prompt" in args
    assert "SYSTEM-INSTRUCTION" in args


def test_claude_omits_system_flag_when_blank():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="out")) as run:
        ClaudeEngine().generate("p")
    args = run.call_args.args[0]
    assert "--append-system-prompt" not in args


def test_claude_pipes_prompt_on_stdin():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="out")) as run:
        ClaudeEngine().generate("MY-PROMPT")
    assert run.call_args.kwargs.get("input") == "MY-PROMPT"


def test_claude_nonzero_exit_raises_with_detail():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(2, stdout="", stderr="boom detail")):
        with pytest.raises(EngineError) as ei:
            ClaudeEngine().generate("p")
    assert "boom detail" in str(ei.value)


def test_claude_timeout_raises_engine_error():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=180)):
        with pytest.raises(EngineError):
            ClaudeEngine().generate("p")


def test_claude_oserror_raises_engine_error():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               side_effect=OSError("cannot exec")):
        with pytest.raises(EngineError):
            ClaudeEngine().generate("p")


def test_claude_empty_output_raises_engine_error():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="   \n  ")):
        with pytest.raises(EngineError):
            ClaudeEngine().generate("p")


def test_claude_returns_stripped_stdout_on_success():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/claude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="  hello \n")):
        assert ClaudeEngine().generate("p") == "hello"


def test_claude_honors_custom_command_and_timeout():
    with patch("brains.claude_engine.shutil.which", return_value="/usr/bin/myclaude"), \
         patch("brains.claude_engine.subprocess.run",
               return_value=_Proc(0, stdout="out")) as run:
        ClaudeEngine(command="myclaude", timeout=5).generate("p")
    args = run.call_args.args[0]
    assert args[0] == "myclaude"
    assert run.call_args.kwargs.get("timeout") == 5


def test_claude_default_command_and_timeout():
    engine = ClaudeEngine()
    assert engine.command == "claude"
    assert engine.timeout == 180


# --------------------------------------------------------------------------- #
# Uniform contract (RED until GREEN adds available() to BaseEngine)
# --------------------------------------------------------------------------- #
def test_engine_error_is_runtimeerror_subclass():
    assert issubclass(EngineError, RuntimeError)


def test_claude_engine_is_a_base_engine():
    assert isinstance(ClaudeEngine(), BaseEngine)


def test_base_engine_exposes_available_default():
    # RED: BaseEngine has no available() yet. GREEN adds a default returning True
    # so every engine answers the same "are you usable?" question.
    assert BaseEngine().available() is True


def test_local_engine_exposes_available():
    # RED: LocalEngine inherits no available() today. GREEN: True (local model is
    # always considered reachable; transport errors surface at generate()).
    assert LocalEngine().available() is True
