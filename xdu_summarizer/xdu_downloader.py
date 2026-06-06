"""Stdlib helper for the upstream XDUClassVideoDownloader project."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOWNLOADER_DIR = PROJECT_ROOT / ".xdu_downloader" / "XDUClassVideoDownloader"
_DOWNLOADER_ZIP_URL = "https://github.com/lsy223622/XDUClassVideoDownloader/archive/refs/heads/main.zip"
_REQUIRED_SCRIPTS = ("XDUClassVideoDownloader.py", "Automation.py")

__all__ = [
    "DEFAULT_DOWNLOADER_DIR",
    "ensure_downloader",
    "write_auth_config",
    "run_live_download",
    "run_auto_download",
]


def _as_path(path: Optional[Union[str, Path]], default: Path) -> Path:
    return default if path is None else Path(path).expanduser().resolve()


def _has_required_scripts(downloader_dir: Path) -> bool:
    return all((downloader_dir / name).is_file() for name in _REQUIRED_SCRIPTS)


def _parse_cookies(cookies: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for chunk in cookies.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key in {"_d", "UID", "vc3"} and value:
            values[key] = value

    missing = [name for name in ("_d", "UID", "vc3") if not values.get(name)]
    if missing:
        raise ValueError(
            "cookies must contain the required keys _d, UID, and vc3; missing "
            + ", ".join(missing)
        )
    return values


def _download_and_extract(target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="xdu_downloader_", dir=str(target_dir.parent)) as temp_root:
        temp_root_path = Path(temp_root)
        archive_path = temp_root_path / "XDUClassVideoDownloader-main.zip"
        extract_dir = temp_root_path / "extract"
        extract_dir.mkdir()

        request = urllib.request.Request(
            _DOWNLOADER_ZIP_URL,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response, archive_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
        except Exception as exc:  # pragma: no cover - network failures are environment-specific
            raise RuntimeError(f"failed to download downloader archive from {_DOWNLOADER_ZIP_URL}") from exc

        try:
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise RuntimeError("downloaded downloader archive is not a valid zip file") from exc

        repo_root = None
        for script_path in extract_dir.rglob("XDUClassVideoDownloader.py"):
            candidate_root = script_path.parent
            if (candidate_root / "Automation.py").is_file():
                repo_root = candidate_root
                break

        if repo_root is None:
            raise RuntimeError(
                "downloaded downloader archive does not contain XDUClassVideoDownloader.py and Automation.py"
            )

        backup_dir = target_dir.with_name(f"{target_dir.name}.backup")
        if backup_dir.exists():
            if backup_dir.is_dir():
                shutil.rmtree(backup_dir)
            else:
                backup_dir.unlink()

        if target_dir.exists():
            target_dir.rename(backup_dir)
            try:
                shutil.move(str(repo_root), str(target_dir))
            except Exception:
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                backup_dir.rename(target_dir)
                raise
            else:
                shutil.rmtree(backup_dir)
        else:
            shutil.move(str(repo_root), str(target_dir))

    if not _has_required_scripts(target_dir):
        raise RuntimeError(
            "installed downloader is missing required scripts XDUClassVideoDownloader.py and Automation.py"
        )


def ensure_downloader(downloader_dir: Optional[Union[str, Path]] = None, update: bool = False) -> Path:
    target_dir = _as_path(downloader_dir, DEFAULT_DOWNLOADER_DIR)
    if not update and _has_required_scripts(target_dir):
        return target_dir

    _download_and_extract(target_dir)
    if not _has_required_scripts(target_dir):
        raise RuntimeError(
            "installed downloader is missing required scripts XDUClassVideoDownloader.py and Automation.py"
        )
    return target_dir


def write_auth_config(downloader_dir: Union[str, Path], cookies: Optional[str] = None) -> None:
    if cookies is None:
        return

    cookies = cookies.strip()
    if not cookies:
        return

    values = _parse_cookies(cookies)
    downloader_path = Path(downloader_dir).expanduser().resolve()
    downloader_path.mkdir(parents=True, exist_ok=True)

    auth_ini = downloader_path / "auth.ini"
    auth_ini.write_text(
        "[SETTINGS]\n"
        "auth_method=cookies\n"
        "save_auth_info=true\n"
        "\n"
        "[AUTH]\n"
        f"_d={values['_d']}\n"
        f"UID={values['UID']}\n"
        f"vc3={values['vc3']}\n",
        encoding="utf-8",
    )
    if os.name == "posix":
        auth_ini.chmod(0o600)


def run_live_download(
    live_id: str,
    output_dir: Union[str, Path],
    *,
    cookies: Optional[str] = None,
    video_type: str = "ppt",
    skip_weeks: str = "",
    merge: bool = True,
    downloader_dir: Optional[Union[str, Path]] = None,
    update: bool = False,
    debug: bool = False,
) -> subprocess.CompletedProcess[str]:
    downloader_path = ensure_downloader(downloader_dir, update=update)
    if cookies:
        write_auth_config(downloader_path, cookies)

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    script_path = downloader_path / "XDUClassVideoDownloader.py"
    command = [sys.executable, str(script_path), live_id, "-s", "--video-type", video_type]
    if skip_weeks:
        command.extend(["--skip-weeks", skip_weeks])
    if not merge:
        command.append("--no-merge")
    if debug:
        command.append("--debug")

    return subprocess.run(
        command,
        cwd=str(output_path),
        capture_output=True,
        text=True,
    )


def run_auto_download(
    output_dir: Union[str, Path],
    *,
    uid: Optional[str] = None,
    year: Optional[int] = None,
    term: Optional[int] = None,
    cookies: Optional[str] = None,
    video_type: str = "ppt",
    downloader_dir: Optional[Union[str, Path]] = None,
    update: bool = False,
    debug: bool = False,
) -> subprocess.CompletedProcess[str]:
    downloader_path = ensure_downloader(downloader_dir, update=update)
    if cookies:
        write_auth_config(downloader_path, cookies)

    output_path = Path(output_dir).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    script_path = downloader_path / "Automation.py"
    command = [sys.executable, str(script_path)]
    if uid is not None:
        command.extend(["-u", str(uid)])
    if year is not None:
        command.extend(["-y", str(year)])
    if term is not None:
        command.extend(["-t", str(term)])
    command.extend(["--video-type", video_type])
    if debug:
        command.append("--debug")

    return subprocess.run(
        command,
        cwd=str(output_path),
        capture_output=True,
        text=True,
    )
