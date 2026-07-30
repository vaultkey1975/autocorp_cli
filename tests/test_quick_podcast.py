#!/usr/bin/env python3
"""Tests for brains/quick_podcast.py after the observability refactor: the
subprocess.run(capture_output=True) + embedded `python -c` string was
replaced with a streamed subprocess.Popen call to a real module
(`python -m brains.quick_podcast_runner`) plus a shared persistent log file.
These tests cover the parts that don't require a real CloneCast checkout.
"""

import os

from brains import quick_podcast


def test_parse_duration_minutes():
    assert quick_podcast.parse_duration("10m") == 600


def test_parse_duration_seconds():
    assert quick_podcast.parse_duration("90s") == 90


def test_parse_duration_defaults_to_ten_minutes():
    assert quick_podcast.parse_duration(None) == 600


def test_parse_duration_rejects_non_positive():
    import pytest
    with pytest.raises(quick_podcast.QuickPodcastError):
        quick_podcast.parse_duration("0m")


def test_format_mmss_under_a_minute():
    assert quick_podcast._format_mmss(42) == "42s"


def test_format_mmss_minutes_and_seconds():
    assert quick_podcast._format_mmss(702) == "11m 42s"


def test_clonecast_env_includes_repo_src_and_autocorp_root_on_pythonpath(tmp_path):
    repo = tmp_path / "clonecast"
    disp = tmp_path / "disp"
    disp_db = disp / "db" / "cloneshow.db"
    (repo / "src").mkdir(parents=True)

    env = quick_podcast._clonecast_env(repo, disp, disp_db)

    pythonpath_entries = env["PYTHONPATH"].split(os.pathsep)
    assert str(repo / "src") in pythonpath_entries
    assert str(quick_podcast._AUTOCORP_ROOT) in pythonpath_entries


def test_default_output_dir_is_outside_target_repository(tmp_path):
    repo = tmp_path / "clonecast"
    repo.mkdir()

    output = quick_podcast._default_output_dir(repo)

    assert not output.is_relative_to(repo)
    assert str(output).startswith(str(quick_podcast.DEFAULT_OUTPUT_ROOT))


def test_tee_log_writes_to_both_stdout_and_file(tmp_path, capsys):
    log_path = tmp_path / "quick_podcast.log"
    log = quick_podcast._TeeLog(str(log_path), "w")

    log.emit("first")
    log.emit("second")
    log.close()

    captured = capsys.readouterr()
    assert "first" in captured.out
    assert "second" in captured.out
    assert log_path.read_text(encoding="utf-8") == "first\nsecond\n"


def test_tee_log_append_mode_preserves_prior_content(tmp_path):
    log_path = tmp_path / "quick_podcast.log"
    first = quick_podcast._TeeLog(str(log_path), "w")
    first.emit("from the worker")
    first.close()

    second = quick_podcast._TeeLog(str(log_path), "a")
    second.emit("from the final summary")
    second.close()

    assert log_path.read_text(encoding="utf-8") == "from the worker\nfrom the final summary\n"


def test_tee_log_flushes_immediately_without_closing(tmp_path):
    """Usable with `tail -f` while a run is still in progress."""
    log_path = tmp_path / "quick_podcast.log"
    log = quick_podcast._TeeLog(str(log_path), "w")

    log.emit("line one")
    on_disk = log_path.read_text(encoding="utf-8")

    assert on_disk == "line one\n"
    log.close()


def test_require_repo_rejects_missing_directory(tmp_path):
    import pytest
    with pytest.raises(quick_podcast.QuickPodcastError):
        quick_podcast._require_repo(tmp_path / "does-not-exist")


def test_require_repo_rejects_non_git_directory(tmp_path):
    import pytest
    repo = tmp_path / "not_a_repo"
    repo.mkdir()
    with pytest.raises(quick_podcast.QuickPodcastError):
        quick_podcast._require_repo(repo)


def test_require_repo_rejects_missing_production_db(tmp_path):
    import pytest
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "clonecast").mkdir(parents=True)
    (repo / "migrations").mkdir()
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").touch()
    with pytest.raises(quick_podcast.QuickPodcastError):
        quick_podcast._require_repo(repo)


def test_require_repo_accepts_well_formed_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "clonecast").mkdir(parents=True)
    (repo / "migrations").mkdir()
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").touch()
    (repo / "db").mkdir()
    (repo / "db" / "cloneshow.db").touch()

    quick_podcast._require_repo(repo)  # must not raise


def test_run_clonecast_refuses_without_test_flag(tmp_path):
    import pytest
    log = quick_podcast._TeeLog(str(tmp_path / "log.txt"), "w")
    try:
        with pytest.raises(quick_podcast.QuickPodcastError) as exc_info:
            quick_podcast._run_clonecast(
                tmp_path, tmp_path / "disp", tmp_path / "disp" / "db.sqlite",
                tmp_path / "research.md", tmp_path / "output", "Show", "Topic",
                600, "", test=False, log=log,
            )
        assert exc_info.value.phase == "Publishing"
    finally:
        log.close()
