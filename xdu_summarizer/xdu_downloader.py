"""Stdlib helper for the upstream XDUClassVideoDownloader project."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOWNLOADER_DIR = PROJECT_ROOT / ".xdu_downloader" / "XDUClassVideoDownloader"
DEFAULT_IDS_HELPER = PROJECT_ROOT / ".xdu_downloader" / "bin" / (
    "xdu-ids-auth.exe" if os.name == "nt" else "xdu-ids-auth"
)
IDS_HELPER_SOURCE_DIR = Path(__file__).resolve().parent / "ids_helper"
_DOWNLOADER_ZIP_URL = "https://github.com/lsy223622/XDUClassVideoDownloader/archive/refs/heads/main.zip"
_REQUIRED_SCRIPTS = ("XDUClassVideoDownloader.py", "Automation.py")
_PRESERVED_CONFIG_FILES = ("auth.ini", "automation_config.ini")

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DOWNLOADER_DIR",
    "DEFAULT_IDS_HELPER",
    "ensure_downloader",
    "authenticate_via_ids",
    "write_auth_config",
    "run_live_download",
    "run_auto_download",
    "load_downloaded_subtitle",
]


def _as_path(path: Optional[Union[str, Path]], default: Path) -> Path:
    return default if path is None else Path(path).expanduser().resolve()


def _has_required_scripts(downloader_dir: Path) -> bool:
    return all((downloader_dir / name).is_file() for name in _REQUIRED_SCRIPTS)


def _has_subtitle_support(downloader_dir: Path) -> bool:
    downloader = downloader_dir / "downloader.py"
    if not downloader.is_file():
        return False
    try:
        source = downloader.read_text(encoding="utf-8")
    except OSError:
        return False
    return "download_subtitle_for_row" in source and "merge_subtitles_for_videos" in source


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
        if not _has_subtitle_support(repo_root):
            raise RuntimeError("downloaded downloader archive does not contain subtitle support")

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
                for name in _PRESERVED_CONFIG_FILES:
                    previous = backup_dir / name
                    current = target_dir / name
                    if previous.is_file() and not current.exists():
                        shutil.copy2(previous, current)
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
    if not update and _has_required_scripts(target_dir) and _has_subtitle_support(target_dir):
        return target_dir

    if _has_required_scripts(target_dir) and not _has_subtitle_support(target_dir):
        logger.info("检测到旧版 XDUClassVideoDownloader，更新以启用字幕下载")

    _download_and_extract(target_dir)
    if not _has_required_scripts(target_dir) or not _has_subtitle_support(target_dir):
        raise RuntimeError(
            "installed downloader is missing required scripts or subtitle support"
        )
    return target_dir


_TIMESTAMP_RE = re.compile(
    r"^\s*(?P<start>(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3})\s+-->\s+"
    r"(?P<end>(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$"
)


def _timestamp_seconds(value: str) -> float:
    parts = value.replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"invalid subtitle timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_subtitle(content: str) -> list[dict]:
    normalized = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    segments = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        match = None
        time_line_index = 0
        for index, line in enumerate(lines):
            match = _TIMESTAMP_RE.match(line)
            if match is not None:
                time_line_index = index
                break
        if match is None:
            continue
        text = " ".join(lines[time_line_index + 1 :]).strip()
        if not text:
            continue
        try:
            start = _timestamp_seconds(match.group("start"))
            end = _timestamp_seconds(match.group("end"))
        except ValueError:
            continue
        if end > start:
            segments.append({"start": start, "end": end, "text": text})
    return segments


def _subtitle_candidates(video_path: Path):
    base_stem = video_path.stem
    for track_type in ("pptVideo", "teacherTrack"):
        track_suffix = f"-{track_type}"
        if base_stem.endswith(track_suffix):
            base_stem = base_stem[: -len(track_suffix)]
            break
    for extension in (".srt", ".vtt"):
        yield video_path.with_name(f"{base_stem}{extension}")


def load_downloaded_subtitle(video_path: Union[str, Path]) -> Optional[dict]:
    """Load the subtitle emitted beside a downloader video as transcript data."""
    video = Path(video_path)
    for subtitle_path in _subtitle_candidates(video):
        if not subtitle_path.is_file():
            continue
        try:
            segments = _parse_subtitle(subtitle_path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            logger.warning("读取字幕失败，将回退到 ASR: %s — %s", subtitle_path, exc)
            continue
        if not segments:
            logger.warning("字幕为空或无法解析，将回退到 ASR: %s", subtitle_path)
            continue
        logger.info("使用下载器字幕，跳过音频提取和 ASR: %s", subtitle_path)
        return {
            "text": "\n".join(segment["text"] for segment in segments),
            "segments": segments,
            "engine": "subtitle",
            "source": str(subtitle_path),
        }
    return None


def _ensure_ids_helper(helper_path: Optional[Union[str, Path]] = None) -> Path:
    configured_path = helper_path or os.getenv("XDU_IDS_AUTH_HELPER")
    if configured_path:
        helper = Path(configured_path).expanduser().resolve()
        if not helper.is_file():
            raise RuntimeError(f"configured IDS auth helper does not exist: {helper}")
        return helper

    helper = DEFAULT_IDS_HELPER
    sources = [
        *IDS_HELPER_SOURCE_DIR.rglob("*.go"),
        IDS_HELPER_SOURCE_DIR / "go.mod",
        IDS_HELPER_SOURCE_DIR / "go.sum",
    ]
    if helper.is_file() and all(source.stat().st_mtime <= helper.stat().st_mtime for source in sources):
        return helper

    go_binary = shutil.which("go")
    if go_binary is None:
        raise RuntimeError(
            "IDS login requires Go 1.25.9+ to build the bundled helper, or set "
            "XDU_IDS_AUTH_HELPER to a prebuilt xdu-ids-auth executable"
        )

    helper.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="xdu_ids_helper_", dir=str(helper.parent)) as temp_dir:
        built_helper = Path(temp_dir) / helper.name
        result = subprocess.run(
            [go_binary, "build", "-trimpath", "-o", str(built_helper), "./cmd/xdu-ids-auth"],
            cwd=str(IDS_HELPER_SOURCE_DIR),
            capture_output=True,
            text=True,
            env=_subprocess_env_without_ids_secrets(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"failed to build IDS auth helper: {detail[-2000:]}")
        shutil.move(str(built_helper), helper)
    if os.name == "posix":
        helper.chmod(0o700)
    return helper


def _subprocess_env_without_ids_secrets() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("XDU_IDS_PASSWORD", None)
    environment.pop("XDU_IDS_PROXY", None)
    return environment


def authenticate_via_ids(
    username: str,
    password: str,
    *,
    reauth_channel: str = "sms",
    reauth_code_provider: Optional[Callable[[str], str]] = None,
    helper_path: Optional[Union[str, Path]] = None,
) -> dict[str, str]:
    """Authenticate through the bundled Quasar-based helper without argv secrets."""
    if not username.strip() or not password:
        raise ValueError("IDS username and password are required")

    helper = _ensure_ids_helper(helper_path)
    process = subprocess.Popen(
        [str(helper)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_subprocess_env_without_ids_secrets(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    try:
        request = {
            "username": username.strip(),
            "password": password,
            "reauth_channel": reauth_channel,
            "proxy": os.getenv("XDU_IDS_PROXY", ""),
        }
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()

        while True:
            line = process.stdout.readline()
            if not line:
                detail = process.stderr.read().strip()
                process.wait(timeout=5)
                raise RuntimeError(
                    "IDS auth helper exited without a result"
                    + (f": {detail[-2000:]}" if detail else "")
                )
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("IDS auth helper returned invalid JSON") from exc

            status = response.get("status")
            if status in {"reauth_required", "reauth_rejected"}:
                if reauth_code_provider is None:
                    raise RuntimeError(
                        "IDS requires second-factor authentication, but no verification-code provider was supplied"
                    )
                prompt = response.get("sent_to") or response.get("error", "")
                code = reauth_code_provider(prompt).strip()
                if not code:
                    raise RuntimeError("IDS verification code cannot be empty")
                process.stdin.write(json.dumps({"code": code}) + "\n")
                process.stdin.flush()
                continue
            if status == "error":
                raise RuntimeError(f"IDS authentication failed: {response.get('error', 'unknown error')}")
            if status != "success":
                raise RuntimeError(f"IDS auth helper returned unknown status: {status}")

            cookies = response.get("cookies") or {}
            missing = [name for name in ("_d", "UID", "vc3") if not cookies.get(name)]
            if missing:
                raise RuntimeError("IDS authentication did not return required cookies: " + ", ".join(missing))
            process.stdin.close()
            process.wait(timeout=5)
            if process.returncode != 0:
                detail = process.stderr.read().strip()
                raise RuntimeError(
                    "IDS auth helper failed after returning cookies"
                    + (f": {detail[-2000:]}" if detail else "")
                )
            return {name: str(cookies[name]) for name in ("_d", "UID", "vc3")}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _write_auth_values(downloader_path: Path, values: dict[str, str]) -> None:
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


def write_auth_config(downloader_dir: Union[str, Path], cookies: Optional[str] = None) -> None:
    if cookies is None:
        return

    cookies = cookies.strip()
    if not cookies:
        return

    values = _parse_cookies(cookies)
    downloader_path = Path(downloader_dir).expanduser().resolve()
    _write_auth_values(downloader_path, values)


def _configure_auth(
    downloader_path: Path,
    *,
    cookies: Optional[str],
    ids_username: Optional[str],
    ids_password: Optional[str],
    ids_reauth_channel: str,
    reauth_code_provider: Optional[Callable[[str], str]],
) -> None:
    has_cookies = bool(cookies and cookies.strip())
    has_ids = bool(ids_username or ids_password)
    if has_cookies and has_ids:
        raise ValueError("choose either cookies or IDS credentials, not both")
    if has_ids:
        if not ids_username or not ids_password:
            raise ValueError("both IDS username and password are required")
        values = authenticate_via_ids(
            ids_username,
            ids_password,
            reauth_channel=ids_reauth_channel,
            reauth_code_provider=reauth_code_provider,
        )
        _write_auth_values(downloader_path, values)
    elif has_cookies:
        write_auth_config(downloader_path, cookies)


def run_live_download(
    live_id: str,
    output_dir: Union[str, Path],
    *,
    cookies: Optional[str] = None,
    ids_username: Optional[str] = None,
    ids_password: Optional[str] = None,
    ids_reauth_channel: str = "sms",
    reauth_code_provider: Optional[Callable[[str], str]] = None,
    video_type: str = "ppt",
    skip_weeks: str = "",
    merge: bool = True,
    downloader_dir: Optional[Union[str, Path]] = None,
    update: bool = False,
    debug: bool = False,
) -> subprocess.CompletedProcess[str]:
    downloader_path = ensure_downloader(downloader_dir, update=update)
    _configure_auth(
        downloader_path,
        cookies=cookies,
        ids_username=ids_username,
        ids_password=ids_password,
        ids_reauth_channel=ids_reauth_channel,
        reauth_code_provider=reauth_code_provider,
    )

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
        env=_subprocess_env_without_ids_secrets(),
    )


def run_auto_download(
    output_dir: Union[str, Path],
    *,
    uid: Optional[str] = None,
    year: Optional[int] = None,
    term: Optional[int] = None,
    cookies: Optional[str] = None,
    ids_username: Optional[str] = None,
    ids_password: Optional[str] = None,
    ids_reauth_channel: str = "sms",
    reauth_code_provider: Optional[Callable[[str], str]] = None,
    video_type: str = "ppt",
    downloader_dir: Optional[Union[str, Path]] = None,
    update: bool = False,
    debug: bool = False,
) -> subprocess.CompletedProcess[str]:
    downloader_path = ensure_downloader(downloader_dir, update=update)
    _configure_auth(
        downloader_path,
        cookies=cookies,
        ids_username=ids_username,
        ids_password=ids_password,
        ids_reauth_channel=ids_reauth_channel,
        reauth_code_provider=reauth_code_provider,
    )

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
        env=_subprocess_env_without_ids_secrets(),
    )
