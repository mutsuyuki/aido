"""
aido Dashboard - プロジェクト単位のパイプラインモニタリング Web UI

使い方:
  # プロジェクトを指定して起動（settings/ と runs/ を自動検出）
  python main.py dashboard workspace/news-feeling/

  # パイプライン実行中は自動起動される
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Optional

import yaml

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
except ImportError:
    print("Dashboard requires: pip install fastapi uvicorn websockets")
    sys.exit(1)

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False

from src.config import load_project_config, get_role_config

STATIC_DIR = Path(__file__).parent / "dashboard_static"

app = FastAPI(title="aido Dashboard")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Global state
_project_dir: Optional[Path] = None  # e.g. workspace/news-feeling/
_settings_dir: Optional[Path] = None
_runs_base: Optional[Path] = None
_active_config_path: Optional[Path] = None  # pipeline実行中のYAML
_ws_clients: set[WebSocket] = set()


# ==========================================
# Project discovery
# ==========================================

def _discover_project(project_dir: Path) -> None:
    """プロジェクトディレクトリからsettings/とruns/を検出"""
    global _project_dir, _settings_dir, _runs_base
    _project_dir = project_dir.resolve()
    _settings_dir = _project_dir / "settings"
    _runs_base = _project_dir / "runs"


# ==========================================
# Config parsing
# ==========================================

def parse_config_for_preview(config: dict) -> dict:
    """YAML設定からダッシュボード表示用の構造を生成"""
    project = config.get("project", {})
    gen = config.get("generation", {})
    phases = config.get("phases", [])
    project_dir = Path(config.get("_project_dir", "."))

    role_configs = get_role_config(config, project_dir)
    fallbacks = gen.get("fallbacks", {})

    phase_details = []
    for phase in phases:
        steps_info = []
        for step in phase.get("steps", []):
            role = step["role"]
            action = step.get("action", role)
            rcfg = role_configs.get(role, {})
            fb = fallbacks.get(role, [])

            # step-level override があればそちらを優先表示
            effective_backend = step.get("backend") or rcfg.get("backend", "?")
            effective_model = step.get("model") or rcfg.get("model", "?")
            step_info = {
                "role": role,
                "action": action,
                "backend": effective_backend,
                "model": effective_model,
                "session": rcfg.get("session", "?"),
                "timeout_sec": rcfg.get("timeout_sec", 300),
                "fallbacks": [
                    {
                        "error_patterns": f.get("error_patterns", []),
                        "fallback_backend": f.get("fallback_backend", ""),
                        "fallback_model": f.get("fallback_model", ""),
                    }
                    for f in fb
                ],
            }
            if "prompt" in step:
                step_info["prompt_override"] = step["prompt"]
            steps_info.append(step_info)

        phase_details.append({
            "id": phase["id"],
            "title": phase.get("title", phase["id"]),
            "description": phase.get("description", ""),
            "dependencies": phase.get("dependencies", []),
            "tasks": phase.get("tasks", []),
            "constraints": phase.get("constraints", []),
            "steps": steps_info,
        })

    return {
        "project_name": project.get("name", ""),
        "work_dir": project.get("work_dir", ""),
        "config_file": config.get("_project_yaml", ""),
        "generation": {
            "default_backend": gen.get("default_backend", ""),
            "default_model": gen.get("default_model", ""),
            "max_retries": gen.get("max_retries", 3),
            "stop_on_failure": gen.get("stop_on_failure", True),
            "use_leader": gen.get("use_leader", False),
            "confidence_threshold": gen.get("confidence_threshold", 80),
            "confidence_step": gen.get("confidence_step", 5),
        },
        "checks": config.get("checks", {}),
        "phases": phase_details,
    }


# ==========================================
# Settings listing
# ==========================================

def list_settings() -> list[dict]:
    """settings/内のYAMLファイル一覧"""
    if not _settings_dir or not _settings_dir.exists():
        return []

    configs = []
    for f in sorted(_settings_dir.iterdir()):
        if not f.is_file() or f.suffix not in (".yaml", ".yml"):
            continue
        info = {"name": f.stem, "filename": f.name}
        try:
            with open(f, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            if raw and isinstance(raw, dict):
                info["project_name"] = raw.get("project", {}).get("name", "")
                phases = raw.get("phases", [])
                info["phase_count"] = len(phases)
                info["phase_ids"] = [p.get("id", "") for p in phases]
        except Exception:
            pass
        configs.append(info)
    return configs


def load_setting_preview(name: str) -> Optional[dict]:
    """特定のsettings YAMLをプレビュー用に解析"""
    if not _settings_dir:
        return None
    path = _settings_dir / f"{name}.yaml"
    if not path.exists():
        path = _settings_dir / f"{name}.yml"
    if not path.exists():
        return None
    try:
        config = load_project_config(path)
        return parse_config_for_preview(config)
    except Exception as e:
        return {"error": str(e)}


# ==========================================
# Run data loading
# ==========================================

def list_runs() -> list[dict]:
    """利用可能なrun一覧"""
    if not _runs_base or not _runs_base.exists():
        return []

    runs = []
    for d in sorted(_runs_base.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        summary_path = d / "pipeline_summary.json"
        info = {"id": d.name, "path": str(d)}
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                info["project"] = summary.get("project", "")
                info["total_phases"] = summary.get("total_phases", 0)
                info["completed"] = len(summary.get("completed", []))
                info["failed"] = len(summary.get("failed", []))
                info["phase_summaries"] = summary.get("phase_summaries", {})
            except (json.JSONDecodeError, OSError):
                pass
        else:
            # pipeline_summary.json がまだない = 実行中の可能性
            # phase ディレクトリの存在で進捗を推定
            phase_dirs = [p for p in d.iterdir() if p.is_dir()]
            if phase_dirs:
                info["in_progress"] = True
                info["phase_dirs"] = [p.name for p in sorted(phase_dirs)]
        runs.append(info)
    return runs


def load_run_summary(run_id: str) -> Optional[dict]:
    if not _runs_base:
        return None
    summary_path = _runs_base / run_id / "pipeline_summary.json"
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_phase_detail(run_id: str, phase_id: str) -> dict:
    if not _runs_base:
        return {"attempts": []}

    phase_dir = _runs_base / run_id / phase_id
    if not phase_dir.exists():
        return {"attempts": []}

    attempts = []
    for attempt_dir in sorted(phase_dir.iterdir()):
        if not attempt_dir.is_dir() or not attempt_dir.name.startswith("attempt_"):
            continue

        attempt_data = {"name": attempt_dir.name, "files": {}}

        log_path = attempt_dir / "log.json"
        if log_path.exists():
            try:
                attempt_data["log"] = json.loads(log_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        for f in sorted(attempt_dir.iterdir()):
            if f.name == "log.json":
                continue
            if f.is_file():
                try:
                    content = f.read_text(encoding="utf-8")
                    if len(content) > 50000:
                        content = content[:50000] + "\n... (truncated)"
                    attempt_data["files"][f.name] = content
                except (UnicodeDecodeError, OSError):
                    attempt_data["files"][f.name] = "(binary file)"

        attempts.append(attempt_data)

    return {"phase_id": phase_id, "attempts": attempts}


# ==========================================
# Active run detection
# ==========================================

def detect_active_run() -> Optional[dict]:
    """実行中のrunを検出する。
    pipeline_summary.json がまだ存在しないか、completed+failed < total_phases なら実行中。
    """
    if not _runs_base or not _runs_base.exists():
        return None

    # 最新のrunディレクトリを確認
    dirs = sorted(
        (d for d in _runs_base.iterdir() if d.is_dir()),
        key=lambda d: d.name,
        reverse=True,
    )
    for d in dirs[:3]:  # 最新3つまでチェック
        summary_path = d / "pipeline_summary.json"
        if not summary_path.exists():
            # summary なし = 開始直後 or 実行中
            phase_dirs = [p.name for p in d.iterdir() if p.is_dir()]
            if phase_dirs:
                return {
                    "run_id": d.name,
                    "status": "running",
                    "phases_started": sorted(phase_dirs),
                    "config_name": _active_config_path.stem if _active_config_path else None,
                }
            continue

        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        completed = len(summary.get("completed", []))
        failed = len(summary.get("failed", []))
        total = summary.get("total_phases", 0)

        if completed + failed < total:
            # 進行中のフェーズを特定
            done_ids = set(summary.get("completed", []) + summary.get("failed", []))
            all_results = summary.get("results", [])
            current_phase = None
            for r in all_results:
                if r["phase_id"] not in done_ids:
                    current_phase = r
                    break

            return {
                "run_id": d.name,
                "status": "running",
                "project": summary.get("project", ""),
                "total_phases": total,
                "completed": completed,
                "failed": failed,
                "current_phase": current_phase.get("phase_id") if current_phase else None,
                "current_title": current_phase.get("title") if current_phase else None,
                "current_attempt": len(current_phase.get("attempts", [])) if current_phase else 0,
                "config_name": _active_config_path.stem if _active_config_path else None,
            }

    return None


# ==========================================
# API routes
# ==========================================

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/project")
async def get_project():
    """プロジェクト概要"""
    return JSONResponse({
        "project_dir": str(_project_dir) if _project_dir else None,
        "project_name": _project_dir.name if _project_dir else None,
    })


@app.get("/api/settings")
async def get_settings():
    """設定ファイル一覧"""
    return JSONResponse(list_settings())


@app.get("/api/settings/{name}")
async def get_setting(name: str):
    """特定の設定ファイルのプレビュー"""
    preview = load_setting_preview(name)
    if preview is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(preview)


@app.get("/api/runs")
async def get_runs():
    return JSONResponse(list_runs())


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str):
    summary = load_run_summary(run_id)
    if summary is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(summary)


@app.get("/api/runs/{run_id}/phases/{phase_id}")
async def get_phase(run_id: str, phase_id: str):
    return JSONResponse(load_phase_detail(run_id, phase_id))


@app.get("/api/status")
async def get_status():
    """実行中のrunを検出"""
    active = detect_active_run()
    return JSONResponse(active)


# ==========================================
# WebSocket
# ==========================================

@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(ws)


async def broadcast(data: dict):
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


# ==========================================
# File watcher
# ==========================================

class RunsWatcher(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def on_modified(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name in ("pipeline_summary.json", "log.json"):
            self._notify(path)

    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix in (".json", ".md", ".txt"):
            self._notify(path)

    def _notify(self, path: Path):
        try:
            rel = path.relative_to(_runs_base)
            run_id = rel.parts[0] if rel.parts else ""
            asyncio.run_coroutine_threadsafe(
                broadcast({"event": "update", "run_id": run_id, "file": str(rel)}),
                self._loop,
            )
        except (ValueError, IndexError):
            pass


# ==========================================
# Server lifecycle
# ==========================================

_server: Optional[uvicorn.Server] = None
_observer: Optional[Observer] = None


def start_dashboard(
    project_dir: Optional[Path] = None,
    active_config_path: Optional[Path] = None,
    host: str = "0.0.0.0",
    port: int = 8420,
    open_browser: bool = True,
    blocking: bool = True,
) -> Optional[threading.Thread]:
    """ダッシュボードサーバーを起動する"""
    global _active_config_path, _server, _observer

    _active_config_path = active_config_path

    if project_dir:
        _discover_project(project_dir)

    print(f"\n  aido Dashboard: http://localhost:{port}")
    if _project_dir:
        print(f"  Project: {_project_dir}")

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    _server = uvicorn.Server(config)

    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    if blocking:
        loop = asyncio.new_event_loop()
        if HAS_WATCHDOG and _runs_base and _runs_base.exists():
            _observer = Observer()
            _observer.schedule(RunsWatcher(loop), str(_runs_base), recursive=True)
            _observer.start()

        asyncio.set_event_loop(loop)
        loop.run_until_complete(_server.serve())
        return None
    else:
        loop = asyncio.new_event_loop()
        if HAS_WATCHDOG and _runs_base and _runs_base.exists():
            _observer = Observer()
            _observer.schedule(RunsWatcher(loop), str(_runs_base), recursive=True)
            _observer.start()

        def _run():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_server.serve())

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread


def stop_dashboard():
    global _server, _observer
    if _observer:
        _observer.stop()
        _observer.join(timeout=3)
        _observer = None
    if _server:
        _server.should_exit = True
        _server = None
