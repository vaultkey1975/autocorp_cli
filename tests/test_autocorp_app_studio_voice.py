import pytest

from app import clonecast_client as cc


STUDIOS = [
    cc.Studio(studio_id="studio_1", display_name="Shadow Frequency", lifecycle_status="approved"),
    cc.Studio(studio_id="studio_2", display_name="Shadow Frequency II", lifecycle_status="active"),
]

VOICES = [
    cc.VoiceProfile(
        voice_profile_id="voice_larry_draft",
        display_name="Larry",
        lifecycle_status="draft",
        stable_name="larry.v1",
        version_label="larry-production-v1",
        version_number=1,
    ),
    cc.VoiceProfile(
        voice_profile_id="voice_larry_approved",
        display_name="Larry",
        lifecycle_status="approved",
        stable_name="larry.v2",
        version_label="larry-production-v1",
        version_number=2,
    ),
    cc.VoiceProfile(
        voice_profile_id="voice_daniel",
        display_name="Daniel",
        lifecycle_status="approved",
        stable_name="daniel.v1",
        version_label="daniel-v1",
        version_number=1,
    ),
]


def test_friendly_display_name_maps_to_real_id():
    studio = cc.resolve_studio("Shadow Frequency", STUDIOS)
    assert studio.studio_id == "studio_1"


def test_raw_id_maps_correctly():
    studio = cc.resolve_studio("studio_2", STUDIOS)
    assert studio.display_name == "Shadow Frequency II"


def test_id_followed_by_display_name_is_normalized():
    # The historical malformed terminal input: "studio_id: Display Name".
    studio = cc.resolve_studio("studio_1: Shadow Frequency", STUDIOS)
    assert studio.studio_id == "studio_1"


def test_ambiguous_display_name_is_rejected():
    studios = STUDIOS + [cc.Studio(studio_id="studio_3", display_name="Shadow Frequency", lifecycle_status="draft")]
    with pytest.raises(cc.AmbiguousSelectionError):
        cc.resolve_studio("Shadow Frequency", studios)


def test_unknown_studio_is_rejected():
    with pytest.raises(ValueError):
        cc.resolve_studio("studio_nonexistent", STUDIOS)


def test_malformed_studio_input_cannot_corrupt_downstream_id():
    # Garbage containing a colon must never silently resolve to something.
    with pytest.raises(ValueError):
        cc.resolve_studio("not a real studio: also not real", STUDIOS)


def test_disambiguate_voice_labels_adds_version_info_only_when_needed():
    labels = cc.disambiguate_voice_labels(VOICES)
    assert labels["voice_daniel"] == "Daniel — Approved"
    assert "larry-production-v1" in labels["voice_larry_approved"]
    assert "larry-production-v1" in labels["voice_larry_draft"]
    assert labels["voice_larry_approved"] != labels["voice_larry_draft"]


def test_resolve_voice_by_id_and_by_unique_display_name():
    assert cc.resolve_voice("voice_daniel", VOICES).display_name == "Daniel"
    assert cc.resolve_voice("Daniel", VOICES).voice_profile_id == "voice_daniel"


def test_resolve_voice_ambiguous_display_name_is_rejected():
    with pytest.raises(cc.AmbiguousSelectionError):
        cc.resolve_voice("Larry", VOICES)


def test_read_only_allowlist_blocks_unapproved_commands():
    class DummyCLI:
        def checked(self, args, *, input_text=None):
            raise AssertionError("should not be reached")

    client = cc.CloneCastClient("/tmp/does-not-matter", cli=DummyCLI())
    from brains.guided_clonecast_episode import EpisodeBuildError

    with pytest.raises(EpisodeBuildError, match="allowlist"):
        client._run(["episode-create", "--danger"])
