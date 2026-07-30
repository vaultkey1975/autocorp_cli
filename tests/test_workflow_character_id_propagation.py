from brains import workflow_test
import sqlite3
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
