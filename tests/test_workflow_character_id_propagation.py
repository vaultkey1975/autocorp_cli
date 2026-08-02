from brains import workflow_test
import argparse
import autocorp
import sqlite3
import subprocess
import wave


PREFX_ID = "character_765bd61c159240f38ea11e6f4b36a91a"
BARE_ID = "765bd61c159240f38ea11e6f4b36a91a"


def test_character_creation_recovers_prefixed_character_id():
    html = f"""
    <section>
      <h3>AutoCorp Disposable Host</h3>
      <form action="/studios/studio_1/characters/{PREFX_ID}/activate"></form>
    </section>
    """

    assert workflow_test._extract_char_id_from_html(html, "AutoCorp Disposable Host") == PREFX_ID


def test_exact_recovered_id_is_passed_to_activation_route():
    path = "/studios/{studio_id}/characters/{character_id}/activate"

    resolved = workflow_test._substitute_path_params(
        path,
        {"studio_id": "studio_123", "character_id": PREFX_ID},
    )

    assert resolved == f"/studios/studio_123/characters/{PREFX_ID}/activate"
    assert f"/characters/{BARE_ID}/activate" not in resolved


def test_bare_character_id_is_preserved_when_that_is_the_canonical_returned_id():
    html = f"""
    <section>
      <h3>AutoCorp Disposable Host</h3>
      <form action="/studios/studio_1/characters/{BARE_ID}/activate"></form>
    </section>
    """

    assert workflow_test._extract_char_id_from_html(html, "AutoCorp Disposable Host") == BARE_ID


def test_prefixed_id_prevents_identifier_propagation_defect_url():
    resolved = workflow_test._substitute_path_params(
        "/studios/{studio_id}/characters/{character_id}/activate",
        {"studio_id": "studio_123", "character_id": PREFX_ID},
    )

    assert "character_765bd61c159240f38ea11e6f4b36a91a" in resolved
    assert "AUTOCORP_IDENTIFIER_PROPAGATION_DEFECT" not in resolved


def test_only_missing_records_are_classified_as_identifier_propagation_defects():
    assert (
        workflow_test._classify_redirect_failure(
            "Redirect contains flash_error=radio_characters record not found: character_123"
        )
        == "AUTOCORP_IDENTIFIER_PROPAGATION_DEFECT"
    )
    assert (
        workflow_test._classify_redirect_failure(
            "Redirect contains flash_error=only an approved character may be activated"
        )
        == "CLONECAST_WORKFLOW_PRECONDITION"
    )


def test_approved_voice_profile_lookup_prefers_larry_without_creating_voice(tmp_path):
    db_path = tmp_path / "workflow.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE voice_profiles (voice_profile_id TEXT, display_name TEXT, lifecycle_status TEXT)"
    )
    conn.execute(
        "INSERT INTO voice_profiles VALUES ('voice_other', 'Daniel', 'approved')"
    )
    conn.execute(
        "INSERT INTO voice_profiles VALUES ('voice_larry', 'Larry', 'approved')"
    )
    conn.execute(
        "INSERT INTO voice_profiles VALUES ('voice_draft', 'Larry', 'draft')"
    )
    conn.commit()
    conn.close()

    assert workflow_test._find_approved_voice_profile(str(db_path)) == "voice_larry"


def test_extract_json_accepts_pretty_printed_cli_payload():
    payload = workflow_test._extract_json('{\n  "script_id": "script_123",\n  "status": "approved"\n}\n')

    assert payload == {"script_id": "script_123", "status": "approved"}


def test_active_radio_audio_preset_lookup_prefers_clean_studio(tmp_path):
    db_path = tmp_path / "workflow.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE radio_audio_presets (preset_id TEXT, lifecycle_status TEXT)")
    conn.execute("INSERT INTO radio_audio_presets VALUES ('rapreset_telephone_caller_v1', 'active')")
    conn.execute("INSERT INTO radio_audio_presets VALUES ('rapreset_clean_studio_v1', 'active')")
    conn.execute("INSERT INTO radio_audio_presets VALUES ('rapreset_draft', 'draft')")
    conn.commit()
    conn.close()

    assert workflow_test._find_active_radio_audio_preset(str(db_path)) == "rapreset_clean_studio_v1"


def test_only_existing_resource_redirects_are_treated_as_idempotent():
    assert workflow_test._is_idempotent_redirect_failure(
        "Redirect contains flash_error=a conversation with this purpose already exists "
        "for this segment (existing conversation_id: conversation_abc)"
    )
    assert not workflow_test._is_idempotent_redirect_failure(
        "Redirect contains flash_error=only an approved character may be activated"
    )


def test_route_path_matches_current_openapi_template_to_concrete_url():
    assert workflow_test._route_path_matches(
        "/conversations/sessions/{session_id}/validate",
        "/conversations/sessions/session_abc/validate",
    )
    assert not workflow_test._route_path_matches(
        "/conversations/{conversation_id}/assemble",
        "/conversations/sessions/session_abc/assemble",
    )


def test_clonecast_env_keeps_generated_outputs_inside_disposable_root(tmp_path):
    repo = "/home/larry/clonecast"
    db = str(tmp_path / "db" / "cloneshow.db")
    env = workflow_test._clonecast_env(repo, str(tmp_path), db)

    assert env["CLONECAST_ROOT"] == str(tmp_path)
    assert env["CLONECAST_DB_PATH"] == db
    for key in (
        "CLONECAST_SPEECH_OUTPUT_DIR",
        "CLONECAST_CONVERSATION_ASSEMBLY_DIR",
        "CLONECAST_RADIO_EPISODE_INTEGRATION_DIR",
        "CLONECAST_PUBLICATION_DROP_DIR",
    ):
        assert workflow_test._inside(env[key], str(tmp_path))


def test_build_body_preserves_supplied_optional_schema_fields():
    route = {
        "content_type": "application/x-www-form-urlencoded",
        "required_fields": ["studio_id", "title"],
        "request_schema": {
            "properties": {
                "studio_id": {"type": "string"},
                "title": {"type": "string"},
                "expected_duration_seconds": {"type": "number"},
                "episode_id": {"type": "string"},
            }
        },
    }

    _content_type, body = workflow_test._build_body(
        route,
        {
            "studio_id": "studio_1",
            "title": "Disposable",
            "expected_duration_seconds": 20.0,
            "episode_id": "episode_1",
        },
    )

    assert body["expected_duration_seconds"] == 20.0
    assert body["episode_id"] == "episode_1"


def test_db_record_exists_uses_readonly_lookup(tmp_path):
    db_path = tmp_path / "workflow.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE items (item_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO items VALUES ('item_1')")
    conn.commit()
    conn.close()

    assert workflow_test._db_record_exists(str(db_path), "items", "item_id", "item_1")
    assert not workflow_test._db_record_exists(str(db_path), "items", "item_id", "missing")


def test_ffprobe_records_real_audio_metadata(tmp_path):
    wav_path = tmp_path / "artifact.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x01\x00" * 2400)

    meta, error = workflow_test._ffprobe(str(wav_path))

    assert error == ""
    assert meta.path == str(wav_path)
    assert meta.size_bytes > 0
    assert meta.duration_seconds > 0
    assert meta.codec == "pcm_s16le"
    assert meta.sample_rate == 24000
    assert meta.channels == 1


def _init_clonecast_like_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    db_dir = path / "db"
    db_dir.mkdir()
    conn = sqlite3.connect(db_dir / "cloneshow.db")
    conn.execute(
        "CREATE TABLE voice_reference_assets (reference_asset_id TEXT, managed_path TEXT)"
    )
    conn.commit()
    conn.close()
    (path / ".venv" / "bin").mkdir(parents=True)
    (path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def test_dirty_tree_failure_returns_structured_report_without_disposable_unbound(tmp_path):
    _init_clonecast_like_repo(tmp_path)
    (tmp_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.exit_code == 1
    assert report.overall_status == "SAFETY_BLOCKED"
    assert report.workflow_stage == "ISOLATION_PROOF"
    assert report.failure_reason == "Dirty working tree."
    assert report.cleanup_attempted is False
    assert report.verification_summary
    assert report.recommended_next_action


def test_workspace_creation_failure_returns_structured_report(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)

    def fail_mkdtemp(prefix):
        raise OSError("no temp space")

    monkeypatch.setattr(workflow_test.tempfile, "mkdtemp", fail_mkdtemp)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "FAILED TO CREATE DISPOSABLE WORKSPACE"
    assert "FAILED TO CREATE DISPOSABLE WORKSPACE" in report.failure_reason
    assert report.cleanup_attempted is False
    assert report.repository_unchanged is True


def test_git_status_exception_returns_structured_report(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)

    def fail_git_info(repo_path):
        raise RuntimeError("git inspection exploded")

    monkeypatch.setattr(workflow_test.scanner, "_git_info", fail_git_info)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "SAFETY_BLOCKED"
    assert report.workflow_stage == "ISOLATION_PROOF"
    assert "git inspection exploded" in report.failure_reason
    assert report.cleanup_attempted is False
    assert report.exit_code == 1


def test_database_copy_failure_returns_structured_report(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)

    def fail_copy(src, dst):
        raise OSError("copy denied")

    monkeypatch.setattr(workflow_test.shutil, "copy2", fail_copy)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "DATABASE COPY FAILED"
    assert "DATABASE COPY FAILED" in report.failure_reason
    assert report.cleanup_attempted is True
    assert report.cleanup_removed is True
    assert report.repository_unchanged is True


def test_startup_failure_returns_structured_report_and_cleans_up(tmp_path):
    _init_clonecast_like_repo(tmp_path)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "APPLICATION FAILED TO START"
    assert "APPLICATION FAILED TO START" in report.failure_reason
    assert report.cleanup_attempted is True
    assert report.cleanup_removed is True
    assert report.repository_unchanged is True


def test_approved_script_mode_dispatches_without_starting_web_server(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)
    captured = {}

    def fake_approved(**kwargs):
        captured.update(kwargs)
        report = kwargs["report"]
        report.overall_status = "DISPOSABLE_WORKFLOW_COMPLETE"
        workflow_test._finalize(report, kwargs["prod_db"], kwargs["t0"], kwargs["disp"], kwargs["disp_db"])
        return report

    monkeypatch.setattr(workflow_test, "_run_approved_script_workflow", fake_approved)

    report = workflow_test.run_workflow_test(str(tmp_path), workflow_mode="approved-script-production")

    assert report.workflow_mode == "approved-script-production"
    assert captured["env"]["CLONECAST_DB_PATH"].startswith(captured["disp"])
    assert report.cleanup_attempted is True
    assert report.cleanup_removed is True


def test_workflow_test_cli_approved_script_flag_selects_mode(monkeypatch, tmp_path):
    selected = {}

    class DummyReport:
        repo_path = str(tmp_path)
        disposable_root = "/tmp/acwf-test"
        production_db_path = str(tmp_path / "db" / "cloneshow.db")
        workflow_mode = "approved-script-production"
        overall_status = "DISPOSABLE_WORKFLOW_COMPLETE"
        success = True
        failure_reason = ""
        workflow_stage = "OWNER_LISTENING_GATE_ENFORCED"
        duration = 0.1
        cleanup_attempted = True
        cleanup_removed = True
        cleanup_error = ""
        repository_unchanged = True
        verification_summary = "database=PASS; cleanup=REMOVED; repository_unchanged=yes"
        recommended_next_action = "Review the generated report and keep production credentials disabled."
        stages = []
        artifacts = []
        database_verification = workflow_test.DatabaseVerification(checked=True, integrity_check="ok", integrity_ok=True, foreign_keys_ok=True)
        include_publishing = False
        publishing_readiness = "NOT_RUN"
        production_db_before = production_db_after = "sha"
        production_db_size_before = production_db_size_after = 1
        production_db_mtime_before = production_db_mtime_after = 1.0
        production_db_sidecars_before = production_db_sidecars_after = {}
        clonecast_git_status_before = clonecast_git_status_after = ""
        first_failure = ""
        ollama_disabled = True
        ollama_generation_calls = 0
        script_preservation = {}
        gpu_reservation_evidence = {}

    def fake_run(repo_root, port=8000, include_publishing=False, workflow_mode="conversation-production"):
        selected["workflow_mode"] = workflow_mode
        return DummyReport()

    monkeypatch.setattr(autocorp, "_resolve_repo", lambda _args: str(tmp_path))
    monkeypatch.setattr(autocorp.workflow_test, "run_workflow_test", fake_run)

    rc = autocorp.cmd_workflow_test(argparse.Namespace(repo=str(tmp_path), disposable=True, approved_script=True, workflow="conversation-production"))

    assert rc == 0
    assert selected["workflow_mode"] == "approved-script-production"


def test_publish_validation_records_structured_block_when_workflow_cannot_run(tmp_path):
    _init_clonecast_like_repo(tmp_path)

    report = workflow_test.run_workflow_test(str(tmp_path), include_publishing=True)

    assert report.publishing_readiness == "FAIL"
    assert report.publishing_findings
    assert report.publishing_findings[0].category == "publishing_validation"
    assert "could not run" in report.publishing_findings[0].evidence


def test_cleanup_failure_is_reported_without_attribute_error(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)

    def fail_rmtree(path, ignore_errors=False):
        raise OSError("cleanup denied")

    monkeypatch.setattr(workflow_test.shutil, "rmtree", fail_rmtree)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "CLEANUP_FAILED"
    assert report.cleanup_attempted is True
    assert report.cleanup_removed is False
    assert "cleanup denied" in report.cleanup_error
    assert report.exit_code == 1


def test_cleanup_attribute_error_is_reported(monkeypatch, tmp_path):
    _init_clonecast_like_repo(tmp_path)

    def fail_rmtree(path, ignore_errors=False):
        raise AttributeError("partial cleanup state")

    monkeypatch.setattr(workflow_test.shutil, "rmtree", fail_rmtree)

    report = workflow_test.run_workflow_test(str(tmp_path))

    assert report.overall_status == "CLEANUP_FAILED"
    assert report.cleanup_attempted is True
    assert report.cleanup_removed is False
    assert "partial cleanup state" in report.cleanup_error
    assert report.exit_code == 1


def test_partial_finalize_handles_uncreated_resources(tmp_path):
    _init_clonecast_like_repo(tmp_path)
    report = workflow_test.WorkflowTestReport()
    report.repo_path = str(tmp_path)
    report.production_db_path = str(tmp_path / "db" / "cloneshow.db")
    report.production_db_before = workflow_test._sha256_file(report.production_db_path)
    report.clonecast_git_status_before = ""
    report.overall_status = "FAILED TO CREATE DISPOSABLE WORKSPACE"
    report.first_failure = "FAILED TO CREATE DISPOSABLE WORKSPACE: no temp space"

    rc = workflow_test._finalize(report, report.production_db_path, 0, None, None)

    assert rc == 1
    assert report.cleanup_attempted is False
    assert report.workflow_stage == "NOT_STARTED"
    assert report.failure_reason.startswith("FAILED TO CREATE DISPOSABLE WORKSPACE")
    assert "cleanup=NOT_CREATED" in report.verification_summary


def test_missing_publishing_credentials_are_reported_without_network_calls(monkeypatch):
    for env_vars in workflow_test._PLATFORM_CREDENTIAL_ENV_VARS.values():
        for name in env_vars:
            monkeypatch.delenv(name, raising=False)

    statuses = workflow_test._check_external_publishing_dependencies()

    assert statuses
    assert all(status.credentials_configured is False for status in statuses)
    assert all(status.real_upload_code_exists is False for status in statuses)
