"""Desktop sign-in patch metadata."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, get_settings


def _cfg(monkeypatch) -> Settings:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "desktop_latest_version", "")
    monkeypatch.setattr(cfg, "desktop_patch_url", "")
    monkeypatch.setattr(cfg, "desktop_patch_sha256", "")
    monkeypatch.setattr(cfg, "desktop_patch_notes", "")
    monkeypatch.setattr(cfg, "desktop_patch_file", "")
    monkeypatch.setattr(cfg, "dubber_latest_version", "")
    monkeypatch.setattr(cfg, "dubber_patch_url", "")
    monkeypatch.setattr(cfg, "dubber_patch_sha256", "")
    monkeypatch.setattr(cfg, "dubber_patch_file", "")
    return cfg


def test_no_patch_when_unset(client: TestClient, monkeypatch) -> None:
    _cfg(monkeypatch)
    response = client.get("/api/v1/app/update", params={"current": "0.2.2"})
    assert response.status_code == 200
    body = response.json()
    assert body["update_available"] is False
    assert body["patch_url"] == ""


def test_patch_advertised_when_newer(client: TestClient, monkeypatch) -> None:
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(cfg, "desktop_latest_version", "0.3.0")
    monkeypatch.setattr(
        cfg,
        "desktop_patch_url",
        "https://downloads.example.com/LiveTranslate-0.3.0.zip",
    )
    monkeypatch.setattr(cfg, "desktop_patch_sha256", "ab" * 32)
    monkeypatch.setattr(cfg, "desktop_patch_notes", "Sign-in fix")
    response = client.get("/api/v1/app/update", params={"current": "0.2.2"})
    body = response.json()
    assert body["update_available"] is True
    assert body["latest"] == "0.3.0"
    assert body["patch_sha256"] == "ab" * 32
    assert body["notes"] == "Sign-in fix"


def test_same_version_is_not_an_update(client: TestClient, monkeypatch) -> None:
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(cfg, "desktop_latest_version", "0.2.2")
    monkeypatch.setattr(cfg, "desktop_patch_url", "https://downloads.example.com/patch.zip")
    monkeypatch.setattr(cfg, "desktop_patch_sha256", "cd" * 32)
    response = client.get("/api/v1/app/update", params={"current": "0.2.2"})
    assert response.json()["update_available"] is False


def test_patch_file_route(client: TestClient, monkeypatch, tmp_path: Path) -> None:
    archive = tmp_path / "patch.zip"
    archive.write_bytes(b"PK")
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(cfg, "desktop_patch_file", str(archive))
    response = client.get("/api/v1/app/update/patch")
    assert response.status_code == 200
    assert response.content == b"PK"


def test_dubber_patch_advertised(client: TestClient, monkeypatch) -> None:
    cfg = _cfg(monkeypatch)
    monkeypatch.setattr(cfg, "dubber_latest_version", "0.2.8")
    monkeypatch.setattr(cfg, "dubber_patch_url", "https://downloads.example.com/dubtool.zip")
    monkeypatch.setattr(cfg, "dubber_patch_sha256", "ab" * 32)
    body = client.get("/api/v1/dubber/update").json()
    assert body["windows"]["version"] == "0.2.8"
    assert body["windows"]["url"].startswith("https://")
    assert body["windows"]["sha256"] == "ab" * 32


def test_dubber_patch_hidden_until_complete(client: TestClient, monkeypatch) -> None:
    _cfg(monkeypatch)
    assert client.get("/api/v1/dubber/update").json() == {"windows": {}}


def test_patch_file_missing(client: TestClient, monkeypatch) -> None:
    _cfg(monkeypatch)
    response = client.get("/api/v1/app/update/patch")
    assert response.status_code == 404
