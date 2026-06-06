"""Tests for the Executor — the single gated choke point for all file writes
and shell commands (safety/executor)."""

from safety.executor import Executor
from safety.gate import AllowAllGate, CommandGate, Decision


class DenyGate(CommandGate):
    """A gate that blocks everything — used to prove the Executor never acts when
    the gate says no."""

    def review_write(self, path, content):
        return Decision.block("denied")

    def review_command(self, command, cwd):
        return Decision.block("denied")


def test_write_file_allowed_creates_file(tmp_path):
    ex = Executor(AllowAllGate())
    target = tmp_path / "sub" / "dir" / "file.txt"
    result = ex.write_file(str(target), "hello world")
    assert result.written is True
    assert result.blocked is False
    assert target.read_text() == "hello world"


def test_write_file_blocked_does_not_write(tmp_path):
    ex = Executor(DenyGate())
    target = tmp_path / "nope.txt"
    result = ex.write_file(str(target), "should not appear")
    assert result.written is False
    assert result.blocked is True
    assert not target.exists()


def test_run_command_success(tmp_path):
    ex = Executor(AllowAllGate())
    result = ex.run_command("echo hello-from-test", cwd=str(tmp_path))
    assert result.ok is True
    assert result.returncode == 0
    assert "hello-from-test" in result.stdout


def test_run_command_nonzero_exit_is_not_ok(tmp_path):
    ex = Executor(AllowAllGate())
    result = ex.run_command("exit 3", cwd=str(tmp_path))
    assert result.ok is False
    assert result.returncode == 3


def test_run_command_blocked(tmp_path):
    ex = Executor(DenyGate())
    result = ex.run_command("echo hi", cwd=str(tmp_path))
    assert result.blocked is True
    assert result.ok is False
