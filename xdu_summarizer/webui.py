"""Simple Flask WebUI for downloading and summarizing XDU lecture videos."""

from __future__ import annotations

import argparse
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, redirect, render_template_string, request, url_for

from .config import Settings
from .xdu_downloader import run_auto_download, run_live_download

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

_INDEX_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>XDU 网课下载 + 智能总结</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; color: #222; }
    label { display: block; font-weight: 600; margin-top: 1rem; }
    input, select, textarea { width: min(780px, 100%); padding: .55rem; margin-top: .35rem; box-sizing: border-box; }
    textarea { min-height: 7rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    button { margin-top: 1.5rem; padding: .7rem 1.2rem; font-weight: 700; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 1.2rem; max-width: 900px; }
    .hint { color: #666; font-size: .9rem; margin-top: .2rem; }
    .jobs { margin-top: 2rem; }
    .job { padding: .6rem 0; border-bottom: 1px solid #ddd; }
    .status-running { color: #0969da; }
    .status-succeeded { color: #1a7f37; }
    .status-failed { color: #cf222e; }
  </style>
</head>
<body>
  <h1>XDU 网课下载 + 智能总结</h1>
  <form method="post" action="{{ url_for('start_job') }}">
    <h2>课程来源</h2>
    <label>运行模式</label>
    <select name="mode">
      <option value="live">指定 liveId 下载并总结</option>
      <option value="auto">按 UID 自动发现课程并总结</option>
      <option value="course_dir">总结本地已下载目录</option>
    </select>

    <div class="grid">
      <div>
        <label>liveId</label>
        <input name="live_id" placeholder="例如 12345678">
      </div>
      <div>
        <label>本地课程目录</label>
        <input name="course_dir" placeholder="./xdu_downloads/.../课程名">
      </div>
      <div>
        <label>超星 UID</label>
        <input name="uid" placeholder="自动模式必填">
      </div>
      <div>
        <label>学年</label>
        <input name="year" type="number" placeholder="例如 2025">
      </div>
      <div>
        <label>学期</label>
        <select name="term">
          <option value="">自动</option>
          <option value="1">1 - 秋季</option>
          <option value="2">2 - 春季</option>
        </select>
      </div>
      <div>
        <label>下载视频类型</label>
        <select name="download_video_type">
          <option value="ppt">pptVideo</option>
          <option value="teacher">teacherTrack</option>
          <option value="both">两种都下载</option>
        </select>
      </div>
      <div>
        <label>处理视频类型</label>
        <select name="process_video_type">
          <option value="pptVideo">pptVideo</option>
          <option value="teacherTrack">teacherTrack</option>
        </select>
      </div>
      <div>
        <label>跳过周数</label>
        <input name="skip_weeks" placeholder="例如 1-3,7">
      </div>
    </div>

    <label>超星 Cookies</label>
    <textarea name="cookies" placeholder="_d=...; UID=...; vc3=..."></textarea>
    <div class="hint">仅用于本次下载器认证配置；不写入笔记输出。省略时使用下载器已保存认证或交互认证。</div>

    <h2>ASR 与摘要模型</h2>
    <div class="grid">
      <div>
        <label>ASR 引擎</label>
        <select name="asr_engine">
          <option value="funasr" {% if settings.asr_engine == "funasr" %}selected{% endif %}>FunASR（推荐中文课堂）</option>
          <option value="whisper" {% if settings.asr_engine == "whisper" %}selected{% endif %}>本地 Whisper</option>
          <option value="whisper-api" {% if settings.asr_engine == "whisper-api" %}selected{% endif %}>Whisper API</option>
        </select>
      </div>
      <div>
        <label>FunASR 模型</label>
        <input name="funasr_model" value="{{ settings.funasr_model }}">
      </div>
      <div>
        <label>FunASR 设备</label>
        <input name="funasr_device" value="{{ settings.funasr_device }}" placeholder="auto / cpu / cuda:0">
      </div>
      <div>
        <label>FunASR 标点模型</label>
        <input name="funasr_punc_model" value="{{ settings.funasr_punc_model or '' }}" placeholder="ct-punc；留空禁用">
      </div>
      <div>
        <label>Whisper 模型（fallback）</label>
        <input name="whisper_model" value="{{ settings.whisper_model }}">
      </div>
      <div>
        <label>Whisper 设备</label>
        <select name="whisper_device">
          <option value="cpu" {% if settings.whisper_device == "cpu" %}selected{% endif %}>cpu</option>
          <option value="cuda" {% if settings.whisper_device == "cuda" %}selected{% endif %}>cuda</option>
          <option value="cuda:0" {% if settings.whisper_device == "cuda:0" %}selected{% endif %}>cuda:0</option>
        </select>
      </div>
      <div>
        <label>API Key</label>
        <input name="llm_api_key" type="password" autocomplete="off" placeholder="外部 OpenAI-compatible API Key">
      </div>
      <div>
        <label>Endpoint / Base URL</label>
        <input name="llm_base_url" value="{{ settings.llm_base_url or '' }}" placeholder="https://api.example.com/v1">
      </div>
      <div>
        <label>模型</label>
        <input name="llm_model" value="{{ settings.llm_model }}">
      </div>
      <div>
        <label>笔记输出目录</label>
        <input name="notes_output_dir" value="{{ settings.output_dir }}">
      </div>
      <div>
        <label>下载输出目录</label>
        <input name="download_output_dir" value="./xdu_downloads/webui">
      </div>
    </div>

    <label><input name="no_merge" type="checkbox" style="width:auto"> 不合并相邻节次视频</label>
    <label><input name="update_downloader" type="checkbox" style="width:auto"> 启动前更新 XDUClassVideoDownloader</label>
    <label><input name="debug_downloader" type="checkbox" style="width:auto"> 启用下载器 debug 日志</label>

    <button type="submit">开始任务</button>
  </form>

  <div class="jobs">
    <h2>任务</h2>
    {% for job in jobs %}
      <div class="job">
        <a href="{{ url_for('job_detail', job_id=job.id) }}">{{ job.id }}</a>
        <span class="status-{{ job.status }}">{{ job.status }}</span>
        <span>{{ job.title }}</span>
        <span class="hint">{{ job.created_at }}</span>
      </div>
    {% else %}
      <p class="hint">暂无任务。</p>
    {% endfor %}
  </div>
</body>
</html>
"""

_JOB_TEMPLATE = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>任务 {{ job.id }}</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; color: #222; }
    pre { white-space: pre-wrap; background: #f6f8fa; padding: 1rem; border: 1px solid #d0d7de; }
    .status-running { color: #0969da; }
    .status-succeeded { color: #1a7f37; }
    .status-failed { color: #cf222e; }
  </style>
</head>
<body>
  <p><a href="{{ url_for('index') }}">返回</a></p>
  <h1>任务 {{ job.id }}</h1>
  <p>状态：<strong class="status-{{ job.status }}">{{ job.status }}</strong></p>
  <p>{{ job.title }}</p>
  {% if job.notes %}
    <h2>生成的笔记</h2>
    <ul>{% for note in job.notes %}<li>{{ note }}</li>{% endfor %}</ul>
  {% endif %}
  <h2>日志</h2>
  <pre>{{ "\n".join(job.logs) }}</pre>
</body>
</html>
"""


def _tail(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _job_log(job_id: str, message: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["logs"].append(line)


def _set_job(job_id: str, **updates: Any) -> None:
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(updates)


def _has_direct_video(path: Path) -> bool:
    return any(path.glob("*-pptVideo.mp4")) or any(path.glob("pptVideo/**/*.mp4"))


def _has_video(path: Path) -> bool:
    return any(path.glob("**/*-pptVideo.mp4")) or any(path.glob("**/pptVideo/**/*.mp4"))


def _downloaded_course_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if _has_direct_video(root):
        return [root]
    return [child for child in sorted(root.iterdir()) if child.is_dir() and not child.name.startswith(".") and _has_video(child)]


def _optional_int(value: str) -> int | None:
    value = value.strip()
    return int(value) if value else None


def _run_job(job_id: str, form: dict[str, str]) -> None:
    try:
        mode = form.get("mode", "live")
        download_root = Path(form.get("download_output_dir") or "./xdu_downloads/webui")
        cookies = form.get("cookies") or None

        _job_log(job_id, "任务开始")
        if mode == "live":
            live_id = (form.get("live_id") or "").strip()
            if not live_id:
                raise ValueError("liveId 不能为空")
            output_dir = download_root / live_id
            _job_log(job_id, f"下载 liveId={live_id} 到 {output_dir}")
            result = run_live_download(
                live_id,
                output_dir,
                cookies=cookies,
                video_type=form.get("download_video_type") or "ppt",
                skip_weeks=form.get("skip_weeks") or "",
                merge=form.get("no_merge") != "on",
                update=form.get("update_downloader") == "on",
                debug=form.get("debug_downloader") == "on",
            )
            _job_log(job_id, _tail(result.stdout))
            if result.stderr:
                _job_log(job_id, _tail(result.stderr))
            if result.returncode != 0:
                raise RuntimeError(f"下载器退出码 {result.returncode}")
            course_dirs = _downloaded_course_dirs(output_dir)
            if not course_dirs:
                course_dirs = [output_dir]
        elif mode == "auto":
            output_dir = download_root / "auto"
            _job_log(job_id, f"自动下载到 {output_dir}")
            result = run_auto_download(
                output_dir,
                uid=(form.get("uid") or None),
                year=_optional_int(form.get("year") or ""),
                term=_optional_int(form.get("term") or ""),
                cookies=cookies,
                video_type=form.get("download_video_type") or "ppt",
                update=form.get("update_downloader") == "on",
                debug=form.get("debug_downloader") == "on",
            )
            _job_log(job_id, _tail(result.stdout))
            if result.stderr:
                _job_log(job_id, _tail(result.stderr))
            if result.returncode != 0:
                raise RuntimeError(f"下载器退出码 {result.returncode}")
            course_dirs = _downloaded_course_dirs(output_dir)
        elif mode == "course_dir":
            course_dir = Path(form.get("course_dir") or "")
            if not str(course_dir):
                raise ValueError("本地课程目录不能为空")
            course_dirs = [course_dir]
        else:
            raise ValueError(f"未知运行模式: {mode}")

        settings = Settings.from_env()
        settings.asr_engine = form.get("asr_engine") or settings.asr_engine
        settings.funasr_model = form.get("funasr_model") or settings.funasr_model
        settings.funasr_device = form.get("funasr_device") or settings.funasr_device
        settings.funasr_punc_model = form.get("funasr_punc_model") or None
        settings.llm_api_key = form.get("llm_api_key") or settings.llm_api_key
        settings.llm_base_url = form.get("llm_base_url") or settings.llm_base_url
        settings.llm_model = form.get("llm_model") or settings.llm_model
        settings.whisper_model = form.get("whisper_model") or settings.whisper_model
        settings.whisper_device = form.get("whisper_device") or settings.whisper_device
        settings.output_dir = form.get("notes_output_dir") or settings.output_dir
        settings.use_ppt_video = (form.get("process_video_type") or "pptVideo") == "pptVideo"

        from .pipeline import LecturePipeline
        pipeline = LecturePipeline(settings)
        notes: list[str] = []
        for course_dir in course_dirs:
            _job_log(job_id, f"开始总结: {course_dir}")
            notes.extend(pipeline.process_course(str(course_dir), course_dir.name))

        _set_job(job_id, status="succeeded", notes=notes)
        _job_log(job_id, f"任务完成，生成 {len(notes)} 篇笔记")
    except Exception as exc:
        _job_log(job_id, f"失败: {exc}")
        _job_log(job_id, traceback.format_exc())
        _set_job(job_id, status="failed")


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        with _JOBS_LOCK:
            jobs = [dict(id=job_id, **job) for job_id, job in reversed(list(_JOBS.items()))]
        return render_template_string(_INDEX_TEMPLATE, jobs=jobs, settings=Settings.from_env())

    @app.post("/jobs")
    def start_job():
        form = request.form.to_dict()
        title = form.get("live_id") or form.get("course_dir") or form.get("uid") or form.get("mode", "job")
        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "status": "running",
                "title": title,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "logs": [],
                "notes": [],
            }
        thread = threading.Thread(target=_run_job, args=(job_id, form), daemon=True)
        thread.start()
        return redirect(url_for("job_detail", job_id=job_id))

    @app.get("/jobs/<job_id>")
    def job_detail(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job = dict(id=job_id, **job)
        if job is None:
            return "job not found", 404
        return render_template_string(_JOB_TEMPLATE, job=job)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="XDU 网课下载 + 智能总结 WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    app = create_app()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
