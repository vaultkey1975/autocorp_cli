import pytest

import config
from app import file_service


@pytest.fixture
def isolated_uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_research_upload_succeeds_and_checksum_matches(isolated_uploads):
    data = b"%PDF-1.4 fake research body"
    saved = file_service.save_upload("sess_1", "research", "Bigfoot Research.pdf", data)
    assert saved.original_filename == "Bigfoot Research.pdf"
    assert saved.size_bytes == len(data)
    import hashlib

    assert saved.sha256 == hashlib.sha256(data).hexdigest()
    from pathlib import Path

    assert Path(saved.managed_path).read_bytes() == data


def test_script_upload_only_accepts_txt(isolated_uploads):
    with pytest.raises(file_service.FileServiceError, match="unsupported file type"):
        file_service.save_upload("sess_1", "script", "script.pdf", b"not a txt")


def test_unsupported_extension_rejected_with_friendly_message(isolated_uploads):
    with pytest.raises(file_service.FileServiceError, match="unsupported file type"):
        file_service.save_upload("sess_1", "research", "malware.exe", b"MZ\x90\x00")


def test_empty_upload_rejected(isolated_uploads):
    with pytest.raises(file_service.FileServiceError, match="empty"):
        file_service.save_upload("sess_1", "research", "empty.txt", b"")


def test_oversized_upload_rejected(isolated_uploads, monkeypatch):
    monkeypatch.setattr(config, "APP_UPLOAD_MAX_BYTES", 10)
    with pytest.raises(file_service.FileServiceError, match="limit"):
        file_service.save_upload("sess_1", "research", "big.txt", b"x" * 11)


def test_path_traversal_filename_is_neutralized(isolated_uploads):
    saved = file_service.save_upload("sess_1", "research", "../../etc/passwd.txt", b"content")
    from pathlib import Path

    managed = Path(saved.managed_path).resolve()
    base = Path(config.app_uploads_dir()).resolve()
    assert base in managed.parents
    assert saved.original_filename == "passwd.txt"


def test_path_traversal_session_id_is_rejected(isolated_uploads):
    with pytest.raises(file_service.FileServiceError):
        file_service.save_upload("../../etc", "research", "a.txt", b"x")


def test_two_uploads_never_collide_or_overwrite(isolated_uploads):
    a = file_service.save_upload("sess_1", "research", "notes.txt", b"first")
    b = file_service.save_upload("sess_1", "research", "notes.txt", b"second")
    assert a.managed_path != b.managed_path
    from pathlib import Path

    assert Path(a.managed_path).read_bytes() == b"first"
    assert Path(b.managed_path).read_bytes() == b"second"


def test_is_path_within_managed_area(isolated_uploads):
    saved = file_service.save_upload("sess_1", "research", "notes.txt", b"data")
    assert file_service.is_path_within_managed_area(saved.managed_path)
    assert not file_service.is_path_within_managed_area("/etc/passwd")
    assert not file_service.is_path_within_managed_area("/tmp/some-other-file.txt")
