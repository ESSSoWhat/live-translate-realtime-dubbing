"""Desktop app patch metadata, checked when the user signs in."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.config import get_settings

router = APIRouter(prefix="/app", tags=["app"])


def _version_key(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in value.strip().lstrip("vV").split("."):
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def version_is_newer(latest: str, current: str) -> bool:
    """Return True when ``latest`` is a higher dotted version than ``current``."""
    left = _version_key(latest)
    right = _version_key(current)
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def _sha_ok(value: str) -> bool:
    digest = value.strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


@router.get("/update")
async def app_update(current: str = Query(default="0.0.0")) -> dict:
    """Tell a signing-in desktop app whether a patch zip is published."""
    cfg = get_settings()
    latest = (cfg.desktop_latest_version or "").strip()
    notes = (cfg.desktop_patch_notes or "").strip()
    digest = (cfg.desktop_patch_sha256 or "").strip().lower()
    url = (cfg.desktop_patch_url or "").strip()
    if not url and (cfg.desktop_patch_file or "").strip():
        url = "/api/v1/app/update/patch"
    ready = bool(latest) and version_is_newer(latest, current) and bool(url) and _sha_ok(digest)
    return {
        "current": current,
        "latest": latest or current,
        "update_available": ready,
        "notes": notes if ready else "",
        "patch_url": url if ready else "",
        "patch_sha256": digest if ready else "",
    }


@router.get("/update/patch")
async def app_update_patch() -> FileResponse:
    """Serve the patch zip configured with DESKTOP_PATCH_FILE."""
    raw = (get_settings().desktop_patch_file or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="No patch published")
    path = Path(raw)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No patch published")
    return FileResponse(path, media_type="application/zip", filename=path.name)
