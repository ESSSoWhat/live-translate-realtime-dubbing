"""Install a newer desktop release when the app starts."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

_RELEASES_URL = (
    "https://api.github.com/repos/ESSSoWhat/live-translate-realtime-dubbing/releases/latest"
)


def parse_version(value: str) -> tuple[int, ...]:
    """Turn ``v0.2.9`` into a tuple that sorts 0.2.9 before 0.2.10."""
    parts: list[int] = []
    for part in value.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """True when *latest* should replace *current*."""
    return parse_version(latest) > parse_version(current)


def choose_setup_asset(release: dict) -> tuple[str, str, int] | None:
    """Return ``(version, download_url, size)`` for the Windows setup file."""
    tag = str(release.get("tag_name") or "")
    version = tag.strip().lstrip("vV")
    if not version:
        return None
    wanted = f"livetranslate-{version}-setup.exe"
    for asset in release.get("assets") or []:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name.lower() == wanted and url:
            try:
                size = int(asset.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            return version, url, size
    return None


def install_command(installer: Path, relaunch: Path, pid: int) -> str:
    """PowerShell that waits for this process, installs quietly, then reopens the app."""
    setup = _ps_quote(installer)
    app = _ps_quote(relaunch)
    return (
        f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
        f"$p = Start-Process -FilePath {setup} "
        "-ArgumentList '/VERYSILENT','/NORESTART','/CLOSEAPPLICATIONS' -Wait -PassThru; "
        f"if ($p.ExitCode -eq 0) {{ Start-Process -FilePath {app} }}"
    )


def stage_update(
    release: dict,
    current: str,
    *,
    download: Callable[[str, str, int], Path],
    install: Callable[[Path], None],
) -> str | None:
    """Download and start the installer when *release* is newer than *current*."""
    chosen = choose_setup_asset(release)
    if chosen is None:
        return None
    version, url, size = chosen
    if not is_newer(version, current):
        return None
    installer = download(url, version, size)
    install(installer)
    return version


def fetch_latest_release() -> dict:
    """Read the latest GitHub release. Network errors propagate to the caller."""
    import httpx

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "LiveTranslate"}
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        response = client.get(_RELEASES_URL, headers=headers)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Update feed did not return a release")
    return payload


def download_installer(
    url: str,
    version: str,
    size: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Save the setup file under the temp directory and return its path."""
    import httpx

    folder = Path(tempfile.gettempdir()) / "LiveTranslate"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"LiveTranslate-{version}-Setup.exe"
    partial = dest.with_suffix(".exe.part")
    headers = {"User-Agent": "LiveTranslate"}
    timeout = httpx.Timeout(30.0, read=120.0)
    written = 0
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    written += len(chunk)
                    if on_progress is not None:
                        total = size or int(response.headers.get("content-length") or 0)
                        on_progress(written, total)
    if size and written != size:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"Update download stopped at {written} of {size} bytes")
    partial.replace(dest)
    return dest


def schedule_install(installer: Path, relaunch: Path | None = None) -> None:
    """Leave a hidden helper that installs after this process exits."""
    if sys.platform != "win32":
        raise RuntimeError("The desktop installer runs on Windows")
    target = relaunch or Path(sys.executable)
    command = install_command(installer, target, os.getpid())
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
        creationflags=flags,
        close_fds=True,
    )


def apply_startup_update() -> bool:
    """Install a newer release before the window opens. True means this process should exit."""
    skip = os.environ.get("LIVE_TRANSLATE_SKIP_UPDATE", "").strip().lower()
    if skip in {"1", "true", "yes"}:
        return False
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False

    from live_dubbing import __version__

    try:
        release = fetch_latest_release()
        chosen = choose_setup_asset(release)
        if chosen is None or not is_newer(chosen[0], __version__):
            return False
        version, url, size = chosen
        path = _download_with_progress(version, url, size)
        schedule_install(path)
        logger.info("Startup update started", version=version)
        return True
    except Exception as exc:
        logger.warning("Startup update skipped", error=str(exc))
        return False


def _download_with_progress(version: str, url: str, size: int) -> Path:
    from PyQt6.QtWidgets import QApplication, QProgressDialog

    dialog = QProgressDialog(f"Updating Live Translate to {version}…", "", 0, max(size, 0))
    dialog.setWindowTitle("Live Translate")
    dialog.setCancelButton(None)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setValue(0)
    app = QApplication.instance()

    def on_progress(written: int, total: int) -> None:
        if total > 0:
            dialog.setMaximum(total)
            dialog.setValue(min(written, total))
        if app is not None:
            app.processEvents()

    try:
        return download_installer(url, version, size, on_progress)
    finally:
        dialog.close()


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"
