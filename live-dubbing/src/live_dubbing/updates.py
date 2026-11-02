"""Sign-in patch check, download, and apply for the installed desktop app."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import structlog
from PyQt6.QtCore import QEventLoop, QThread, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from live_dubbing import __version__

logger = structlog.get_logger(__name__)

_MAX_PATCH_BYTES = 1024 * 1024 * 1024


@dataclass(frozen=True)
class UpdateOffer:
    """A published patch newer than this build."""

    latest: str
    notes: str
    patch_url: str
    patch_sha256: str


def version_key(value: str) -> tuple[int, ...]:
    """Turn a dotted version into a tuple of integers."""
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
    """Return True when ``latest`` is higher than ``current``."""
    left = version_key(latest)
    right = version_key(current)
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    return left > right


def install_dir() -> Path | None:
    """Folder that contains LiveTranslate.exe, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}


def resolve_patch_url(base_url: str, patch_url: str) -> str:
    """Join a relative patch path onto the backend, and reject other schemes."""
    absolute = urljoin(base_url.rstrip("/") + "/", patch_url)
    if not _allowed_url(absolute):
        raise ValueError("Patch URL must be https, or http on localhost")
    return absolute


def fetch_update(base_url: str, current: str = __version__) -> UpdateOffer | None:
    """Ask the backend if a patch is published. Network errors yield None."""
    endpoint = base_url.rstrip("/") + "/api/v1/app/update"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(endpoint, params={"current": current})
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("Update check failed", error=str(exc))
        return None
    if not isinstance(body, dict) or not body.get("update_available"):
        return None
    latest = str(body.get("latest") or "")
    patch_url = str(body.get("patch_url") or "")
    digest = str(body.get("patch_sha256") or "").strip().lower()
    if not latest or not patch_url or len(digest) != 64:
        return None
    if not version_is_newer(latest, current):
        return None
    try:
        absolute = resolve_patch_url(base_url, patch_url)
    except ValueError as exc:
        logger.info("Update patch URL rejected", error=str(exc))
        return None
    return UpdateOffer(
        latest=latest,
        notes=str(body.get("notes") or ""),
        patch_url=absolute,
        patch_sha256=digest,
    )


def validate_patch_zip(path: Path) -> None:
    """Reject archives that would write outside the install folder."""
    with zipfile.ZipFile(path) as archive:
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
    if not names:
        raise ValueError("Patch archive is empty")
    for name in names:
        if name.startswith(("/", "\\")) or Path(name).is_absolute():
            raise ValueError("Patch contains an absolute path")
        parts = Path(name).parts
        if ".." in parts:
            raise ValueError("Patch contains a parent path")


def download_patch(
    url: str,
    digest: str,
    on_progress: Callable[[int], None] | None = None,
) -> Path:
    """Download the patch zip and check its SHA-256."""
    if not _allowed_url(url):
        raise ValueError("Patch URL must be https, or http on localhost")
    handle = tempfile.NamedTemporaryFile(prefix="livetranslate-patch-", suffix=".zip", delete=False)
    dest = Path(handle.name)
    handle.close()
    hasher = hashlib.sha256()
    written = 0
    try:
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                final = str(response.url)
                if not _allowed_url(final):
                    raise ValueError("Patch download redirected to a disallowed URL")
                total = int(response.headers.get("content-length") or 0)
                with dest.open("wb") as out:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > _MAX_PATCH_BYTES:
                            raise ValueError("Patch is larger than 1 GB")
                        out.write(chunk)
                        hasher.update(chunk)
                        if on_progress is not None and total > 0:
                            on_progress(min(100, int(written * 100 / total)))
        if hasher.hexdigest() != digest.lower():
            raise ValueError("Patch checksum does not match")
        validate_patch_zip(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if on_progress is not None:
        on_progress(100)
    return dest


_APPLY_SCRIPT = r"""
param(
  [int]$ProcessId,
  [string]$ZipPath,
  [string]$InstallDir,
  [string]$ExePath
)
$log = Join-Path $env:TEMP "LiveTranslate-update.log"
function Write-Log($msg) { Add-Content -Path $log -Value "$(Get-Date -Format o) $msg" }
try {
  $deadline = (Get-Date).AddSeconds(90)
  while (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    if ((Get-Date) -gt $deadline) { break }
    Start-Sleep -Milliseconds 400
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
  try {
    foreach ($entry in $zip.Entries) {
      if ([string]::IsNullOrEmpty($entry.Name)) { continue }
      $rel = $entry.FullName.Replace('\', '/')
      if ($rel.StartsWith('/') -or $rel.Contains('..')) { throw "Unsafe path $rel" }
      $dest = Join-Path $InstallDir ($entry.FullName.Replace('/', '\'))
      $parent = Split-Path $dest -Parent
      if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
      }
      [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
    }
  } finally {
    $zip.Dispose()
  }
  Write-Log "applied $ZipPath"
  Start-Process -FilePath $ExePath
} catch {
  Write-Log $_.Exception.Message
}
"""


def launch_patch_helper(zip_path: Path) -> None:
    """Start a detached helper that applies the zip after this process exits."""
    folder = install_dir()
    if folder is None:
        raise RuntimeError("Patches apply only to the installed app")
    script = Path(tempfile.gettempdir()) / "LiveTranslate-apply-patch.ps1"
    script.write_text(_APPLY_SCRIPT, encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-ProcessId",
            str(os.getpid()),
            "-ZipPath",
            str(zip_path),
            "-InstallDir",
            str(folder),
            "-ExePath",
            str(Path(sys.executable).resolve()),
        ],
        creationflags=flags,
        close_fds=True,
    )


class UpdateCheckThread(QThread):
    """Background check so the sign-in dialog stays responsive."""

    found = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

    def run(self) -> None:
        try:
            offer = fetch_update(self._base_url)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))
            return
        if offer is not None:
            self.found.emit(offer)


class PatchDownloadThread(QThread):
    """Download and verify a patch without blocking the UI."""

    progress = pyqtSignal(int)
    failed = pyqtSignal(str)
    ready = pyqtSignal(str)

    def __init__(self, offer: UpdateOffer) -> None:
        super().__init__()
        self._offer = offer

    def run(self) -> None:
        try:
            def report(percent: int) -> None:
                if self.isInterruptionRequested():
                    raise ValueError("Update cancelled")
                self.progress.emit(percent)

            path = download_patch(
                self._offer.patch_url,
                self._offer.patch_sha256,
                on_progress=report,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.ready.emit(str(path))


def offer_installed_patch(parent: QWidget | None, base_url: str) -> bool:
    """Ask a signed-in user to install a patch. Return True if the app should exit."""
    offer = fetch_update(base_url)
    if offer is None:
        return False
    notes = f"\n\n{offer.notes}" if offer.notes else ""
    answer = QMessageBox.question(
        parent,
        "Update available",
        f"Version {offer.latest} is ready.{notes}\n\nInstall the update now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return False
    if install_dir() is None:
        QMessageBox.information(
            parent,
            "Update available",
            "This copy is running from source. Open the installed Live Translate "
            "app and sign in to apply the patch.",
        )
        return False
    return _download_and_launch(parent, offer)


def _download_and_launch(parent: QWidget | None, offer: UpdateOffer) -> bool:
    progress = QProgressDialog("Downloading update…", "Cancel", 0, 100, parent)
    progress.setWindowTitle("Live Translate")
    progress.setAutoClose(False)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    thread = PatchDownloadThread(offer)
    loop = QEventLoop()
    result: dict[str, str] = {}

    def _finish_ok(path: str) -> None:
        result["path"] = path
        loop.quit()

    def _finish_err(message: str) -> None:
        result["error"] = message
        loop.quit()

    thread.progress.connect(progress.setValue)
    thread.ready.connect(_finish_ok)
    thread.failed.connect(_finish_err)
    progress.canceled.connect(thread.requestInterruption)
    thread.start()
    loop.exec()
    thread.wait(2000)
    progress.close()
    if "error" in result:
        QMessageBox.warning(parent, "Update failed", result["error"])
        return False
    path = result.get("path")
    if not path:
        return False
    try:
        launch_patch_helper(Path(path))
    except Exception as exc:
        QMessageBox.warning(parent, "Update failed", str(exc))
        return False
    return True
