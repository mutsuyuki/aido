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
import time
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
    # work_dir を settings/ 配下の YAML から解決
    work_dir = _resolve_work_dir(_project_dir, _settings_dir)
    _runs_base = work_dir / ".aido" / "runs"


def _resolve_work_dir(project_dir: Path, settings_dir: Path) -> Path:
    """settings/ 配下の YAML から work_dir を解決する"""
    if settings_dir.is_dir():
        for yaml_file in sorted(settings_dir.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                if config and "project" in config and "work_dir" in config["project"]:
                    wd = Path(config["project"]["work_dir"])
                    if not wd.is_absolute():
                        wd = (settings_dir / wd).resolve()
                    return wd
            except Exception:
                continue
    # フォールバック: 従来のパス
    return project_dir


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

        phase_detail = {
            "id": phase["id"],
            "title": phase.get("title", phase["id"]),
            "description": phase.get("description", ""),
            "dependencies": phase.get("dependencies", []),
            "tasks": phase.get("tasks", []),
            "constraints": phase.get("constraints", []),
            "steps": steps_info,
        }
        # Contract / outputs / phase-level overrides
        if phase.get("contract"):
            phase_detail["contract"] = phase["contract"]
        if phase.get("outputs"):
            phase_detail["outputs"] = phase["outputs"]
        if phase.get("review_checklist"):
            phase_detail["review_checklist"] = phase["review_checklist"]
        phase_detail["pass_on_max_retries"] = phase.get("pass_on_max_retries", False)
        if "max_retries" in phase:
            phase_detail["max_retries"] = phase["max_retries"]
        if "confidence_step" in phase:
            phase_detail["confidence_step"] = phase["confidence_step"]
        phase_details.append(phase_detail)

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
            "failure_taxonomy": gen.get("failure_taxonomy", {}),
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
                info["warnings"] = summary.get("warnings", [])
                info["phase_summaries"] = summary.get("phase_summaries", {})
            except (json.JSONDecodeError, OSError):
                pass
        else:
            # pipeline_summary.json がまだない = 実行中 or 中断
            phase_dirs = [p for p in d.iterdir() if p.is_dir()]
            if phase_dirs:
                info["phase_dirs"] = [p.name for p in sorted(phase_dirs)]
                if _is_run_stale(d):
                    info["aborted"] = True
                else:
                    info["in_progress"] = True
        runs.append(info)
    return runs


def load_run_summary(run_id: str) -> Optional[dict]:
    if not _runs_base:
        return None
    run_dir = _runs_base / run_id
    summary_path = run_dir / "pipeline_summary.json"
    if summary_path.exists():
        try:
            return json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    # pipeline_summary.json が無い = 実行中（または中断）。
    # runディレクトリの中身から概要を合成し、実行中の run も詳細表示できるようにする。
    if not run_dir.is_dir():
        return None
    return _synthesize_running_summary(run_id, run_dir)


def _active_config_phases() -> list[dict]:
    """実行中 config（無ければ project.yaml）の phases を返す。取得不可なら []。"""
    candidates = []
    if _active_config_path and _active_config_path.exists():
        candidates.append(_active_config_path)
    if _project_dir:
        candidates.append(_project_dir / "settings" / "project.yaml")
    for path in candidates:
        try:
            if path.exists():
                cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                phases = cfg.get("phases", []) or []
                if phases:
                    return phases
        except Exception:
            continue
    return []


def _load_running_attempts(phase_dir: Path) -> list[dict]:
    """フェーズの各 attempt を log.json から読み、pipeline_summary の results[].attempts と
    同じ形（attempt/steps/decision）で返す。steps は表示に要る軽量フィールドのみに絞る。
    まだ log.json が無い実行中の attempt はプレースホルダで表す。"""
    out = []
    for a in sorted(phase_dir.iterdir()):
        if not (a.is_dir() and a.name.startswith("attempt_")):
            continue
        n = len(out) + 1
        log = a / "log.json"
        if not log.exists():
            out.append({"attempt": n, "steps": [], "decision": "running"})
            continue
        try:
            d = json.loads(log.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            out.append({"attempt": n, "steps": [], "decision": "unknown"})
            continue
        steps = [
            {"role": s.get("role"), "action": s.get("action"),
             "success": s.get("success"), "elapsed_sec": s.get("elapsed_sec")}
            for s in d.get("steps", [])
        ]
        out.append({
            "attempt": d.get("attempt", n),
            "steps": steps,
            "decision": d.get("decision", "unknown"),
            "failure_type": d.get("failure_type"),
        })
    return out


def _synthesize_running_summary(run_id: str, run_dir: Path) -> Optional[dict]:
    """pipeline_summary.json がまだ無い実行中/中断 run の概要を、フェーズ dir から合成する。
    各 attempt の log.json を読み、完了 run と同じ試行/ステップ詳細を表示できるようにする。
    stop_on_failure 下で先へ進めている＝直前までのフェーズは accepted。最新更新フェーズが実行中。"""
    phase_dirs = [d for d in run_dir.iterdir() if d.is_dir()]
    if not phase_dirs:
        return None
    phase_dirs.sort(key=lambda d: d.stat().st_mtime)  # 実行順 ≒ 更新時刻順
    running = not _is_run_stale(run_dir)
    cfg_phases = _active_config_phases()
    titles = {p.get("id"): p.get("title", p.get("id")) for p in cfg_phases}
    total = len(cfg_phases) if cfg_phases else len(phase_dirs)
    results, completed, phase_summaries = [], [], {}
    for idx, d in enumerate(phase_dirs):
        pid = d.name
        attempts = _load_running_attempts(d)
        if idx == len(phase_dirs) - 1:
            status = "running" if running else "aborted"
        else:
            status = "accepted"
            completed.append(pid)
        phase_summaries[pid] = {"status": status, "attempts": len(attempts)}
        results.append({"phase_id": pid, "title": titles.get(pid, pid),
                        "status": status, "attempts": attempts})
    return {
        "project": _project_dir.name if _project_dir else run_id,
        "total_phases": total,
        "completed": completed,
        "failed": [],
        "issues": [],
        "warnings": [],
        "phase_summaries": phase_summaries,
        "results": results,
        "in_progress": running,
    }


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

_STALE_THRESHOLD_SEC = 21600  # 6時間更新がなければ中断と判断


def _is_run_stale(run_dir: Path) -> bool:
    """runディレクトリ内の最新ファイルが _STALE_THRESHOLD_SEC 以上前なら True"""
    latest_mtime = 0.0
    for f in run_dir.rglob("*"):
        if f.is_file():
            try:
                mt = f.stat().st_mtime
                if mt > latest_mtime:
                    latest_mtime = mt
            except OSError:
                continue
    if latest_mtime == 0.0:
        return True
    return (time.time() - latest_mtime) > _STALE_THRESHOLD_SEC


def detect_active_run() -> Optional[dict]:
    """実行中のrunを検出する。
    pipeline_summary.json が存在すればプロセスは終了済み。
    summary が無く phase ディレクトリがあれば実行中と判断する。
    ただし最終更新が5分以上前なら中断(aborted)と判断する。
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
        if summary_path.exists():
            continue

        phase_paths = [p for p in d.iterdir() if p.is_dir()]
        if phase_paths:
            if _is_run_stale(d):
                continue  # 中断されたrunはスキップ
            # 実行順 ≒ 更新時刻順。最後に更新されたフェーズが現在実行中。
            phase_paths.sort(key=lambda p: p.stat().st_mtime)
            current = phase_paths[-1]
            n_attempts = len([a for a in current.iterdir()
                              if a.is_dir() and a.name.startswith("attempt_")])
            cfg_phases = _active_config_phases()
            total = len(cfg_phases) if cfg_phases else len(phase_paths)
            return {
                "run_id": d.name,
                "status": "running",
                # バナーは簡潔に：Phase <完了数>/<総数>: <現在フェーズ id> (attempt N)
                "current_phase": current.name,          # 短い id（長い正式タイトルは使わない）
                "current_attempt": n_attempts,
                # フロントは completed/failed を「数」として completed+failed する
                "completed": len(phase_paths) - 1,      # 直前までは accepted
                "failed": 0,
                "total_phases": total,
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
