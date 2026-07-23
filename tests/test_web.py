from pathlib import Path

import pytest

import app.web as web


def test_resolve_artifact_path_accepts_file_inside_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    document = root / "cv.docx"
    document.write_bytes(b"document")
    monkeypatch.setattr(web, "ARTIFACT_ROOT", root.resolve())
    assert web.resolve_artifact_path(str(document)) == document.resolve()


def test_resolve_artifact_path_rejects_file_outside_artifact_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(web, "ARTIFACT_ROOT", root.resolve())
    with pytest.raises(ValueError):
        web.resolve_artifact_path(str(outside))
