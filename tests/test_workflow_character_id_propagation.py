from brains import workflow_test
import sqlite3


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


def test_only_existing_resource_redirects_are_treated_as_idempotent():
    assert workflow_test._is_idempotent_redirect_failure(
        "Redirect contains flash_error=a conversation with this purpose already exists "
        "for this segment (existing conversation_id: conversation_abc)"
    )
    assert not workflow_test._is_idempotent_redirect_failure(
        "Redirect contains flash_error=only an approved character may be activated"
    )
