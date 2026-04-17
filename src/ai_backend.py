"""
AIバックエンド抽象化モジュール

Claude / Gemini / Codex CLI を統一インターフェースで呼び出す。
各CLIのセッション管理の差異を吸収し、役割ごとにコンテキストを分離・継続できる。

セッション管理の仕組み:
  - Claude:  --session-id <uuid> で作成、--resume <uuid> で継続
  - Gemini:  初回は自動生成、--list-sessions でUUID取得、--resume <uuid> で継続
  - Codex:   初回は --ephemeral なしで実行、exec resume <uuid> で継続
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ==========================================
# データクラス
# ==========================================
@dataclass
class AIResult:
    stdout: str
    stderr: str
    returncode: int
    elapsed_sec: float
    backend: str
    model: str
    session_id: Optional[str] = None
    prompt_hash: str = ""
    timed_out: bool = False


@dataclass
class SessionInfo:
    """セッションの状態を保持する"""
    session_id: str
    backend: str
    role: str  # e.g. "coder_frontend", "coder_backend", "reviewer"
    call_count: int = 0


@dataclass
class FallbackRule:
    """フォールバックルール: 特定のエラーパターンで別のバックエンド/モデルに切替える"""
    error_patterns: list[str]  # stderr に含まれるパターン（いずれか一致でトリガー）
    fallback_backend: str
    fallback_model: str


# ==========================================
# 内部ユーティリティ
# ==========================================
def _prompt_hash(prompt: str) -> str:
    return hashlib.md5(prompt.encode()).hexdigest()[:8]


def _progress_thread(start_time: float, stop_event: threading.Event, label: str) -> threading.Thread:
    def indicator():
        while not stop_event.is_set():
            elapsed = int(time.time() - start_time)
            if elapsed > 0 and elapsed % 15 == 0:
                print(f"    ... {label} ({elapsed}s)")
            time.sleep(1)
    t = threading.Thread(target=indicator, daemon=True)
    return t


def _write_prompt_file(prompt: str, tmp_dir: Path) -> Path:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    h = _prompt_hash(prompt)
    path = tmp_dir / f"prompt_{h}.md"
    path.write_text(prompt, encoding="utf-8")
    return path


# ==========================================
# Claude CLI バックエンド
# ==========================================
def _call_claude(
    prompt: str,
    model: str,
    work_dir: Path,
    session_id: Optional[str],
    is_resume: bool,
    timeout_sec: int,
    permission_mode: str,
) -> AIResult:
    # プロンプトをstdin経由で渡す（長文でシェル引数制限を回避）
    cmd = ["claude", "-p", "--no-chrome"]

    if model:
        cmd.extend(["--model", model])

    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])

    if session_id:
        if is_resume:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])

    phash = _prompt_hash(prompt)
    print(f"  [claude] model={model}, session={session_id or 'new'}, hash={phash}")

    start = time.time()
    stop_ev = threading.Event()
    t = _progress_thread(start, stop_ev, "claude thinking")
    t.start()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec,
            cwd=str(work_dir), input=prompt,
        )
        return AIResult(
            r.stdout.strip(), r.stderr.strip(), r.returncode,
            round(time.time() - start, 2), "claude", model,
            session_id=session_id, prompt_hash=phash,
        )
    except subprocess.TimeoutExpired:
        return AIResult("", "Timeout", -1, timeout_sec, "claude", model,
                        session_id=session_id, prompt_hash=phash, timed_out=True)
    except Exception as e:
        return AIResult("", str(e), -1, 0.0, "claude", model,
                        session_id=session_id, prompt_hash=phash)
    finally:
        stop_ev.set()
        t.join()


# ==========================================
# Gemini CLI バックエンド
# ==========================================
def _get_gemini_latest_session_id(work_dir: Path) -> Optional[str]:
    """直近のGeminiセッションIDを取得する"""
    try:
        r = subprocess.run(
            ["gemini", "--list-sessions"],
            capture_output=True, text=True, timeout=10, cwd=str(work_dir),
        )
        # "  1. description (time ago) [uuid]" の形式からUUIDを取得
        match = re.search(r"\[([0-9a-f-]{36})\]", r.stdout)
        return match.group(1) if match else None
    except Exception:
        return None


def _call_gemini(
    prompt: str,
    model: str,
    work_dir: Path,
    session_id: Optional[str],
    is_resume: bool,
    timeout_sec: int,
) -> AIResult:
    # プロンプトをファイルに書き出し、@file で参照させる
    # これにより長文プロンプトのシェル引数制限を回避し、安定性が上がる
    prompt_file = _write_prompt_file(prompt, work_dir / ".aido" / "tmp")
    cmd = [
        "gemini", "-p",
        f"@{prompt_file} 上記の指示に従って作業してください。作業対象はカレントディレクトリです。必要なファイルは自分で読み込んでください。",
        "-o", "text", "--yolo",
    ]

    if model:
        cmd.extend(["--model", model])

    if session_id and is_resume:
        cmd.extend(["--resume", session_id])

    phash = _prompt_hash(prompt)
    print(f"  [gemini] model={model or 'default'}, session={session_id or 'new'}, hash={phash}")

    start = time.time()
    stop_ev = threading.Event()
    t = _progress_thread(start, stop_ev, "gemini thinking")
    t.start()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec, cwd=str(work_dir),
        )
        # 初回呼び出し時、セッションIDを取得
        result_session_id = session_id
        if not is_resume and r.returncode == 0:
            result_session_id = _get_gemini_latest_session_id(work_dir)

        return AIResult(
            r.stdout.strip(), r.stderr.strip(), r.returncode,
            round(time.time() - start, 2), "gemini", model or "default",
            session_id=result_session_id, prompt_hash=phash,
        )
    except subprocess.TimeoutExpired:
        return AIResult("", "Timeout", -1, timeout_sec, "gemini", model or "default",
                        session_id=session_id, prompt_hash=phash, timed_out=True)
    except Exception as e:
        return AIResult("", str(e), -1, 0.0, "gemini", model or "default",
                        session_id=session_id, prompt_hash=phash)
    finally:
        stop_ev.set()
        t.join()


# ==========================================
# Codex CLI バックエンド
# ==========================================
def _get_codex_latest_session_id() -> Optional[str]:
    """直近のCodexセッションIDを取得する"""
    try:
        # codex sessions are stored as rollout-*.jsonl files
        sessions_dir = Path.home() / ".codex" / "sessions"
        if not sessions_dir.exists():
            return None
        # 最新のjsonlファイルを探す
        files = sorted(sessions_dir.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        # ファイル名: rollout-2025-11-05T10-15-53-<uuid>.jsonl
        match = re.search(r"rollout-[\dT-]+-([0-9a-f-]{36})\.jsonl", files[0].name)
        return match.group(1) if match else None
    except Exception:
        return None


def _call_codex(
    prompt: str,
    model: str,
    work_dir: Path,
    session_id: Optional[str],
    is_resume: bool,
    timeout_sec: int,
) -> AIResult:
    tmp_dir = work_dir / ".aido" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    phash = _prompt_hash(prompt)
    out_file = tmp_dir / f"codex_out_{phash}.md"

    if session_id and is_resume:
        cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
               "--skip-git-repo-check", "resume", session_id, prompt]
    else:
        cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox",
               "--skip-git-repo-check", "-o", str(out_file), prompt]

    if model:
        cmd.extend(["-m", model])

    print(f"  [codex] model={model or 'default'}, session={session_id or 'new'}, hash={phash}")

    start = time.time()
    stop_ev = threading.Event()
    t = _progress_thread(start, stop_ev, "codex thinking")
    t.start()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec, cwd=str(work_dir),
        )
        elapsed = round(time.time() - start, 2)

        if out_file.exists():
            output = out_file.read_text(encoding="utf-8").strip()
            out_file.unlink()
        else:
            output = r.stdout.strip()

        result_session_id = session_id
        if not is_resume and r.returncode == 0:
            result_session_id = _get_codex_latest_session_id()

        return AIResult(
            output, r.stderr.strip(), r.returncode, elapsed,
            "codex", model or "default",
            session_id=result_session_id, prompt_hash=phash,
        )
    except subprocess.TimeoutExpired:
        return AIResult("", "Timeout", -1, timeout_sec, "codex", model or "default",
                        session_id=session_id, prompt_hash=phash, timed_out=True)
    except Exception as e:
        return AIResult("", str(e), -1, 0.0, "codex", model or "default",
                        session_id=session_id, prompt_hash=phash)
    finally:
        stop_ev.set()
        t.join()
        if out_file.exists():
            out_file.unlink()


# ==========================================
# セッションマネージャー
# ==========================================
class SessionManager:
    """
    役割ごとのセッションを管理する。

    使い方:
        sm = SessionManager(backend="claude", model="sonnet")
        # 同じ役割なら同一セッションを引き継ぐ
        result = sm.call("coder_frontend", prompt, work_dir)
        result = sm.call("coder_frontend", prompt, work_dir)  # 同じセッション継続
        # 別の役割は別セッション
        result = sm.call("reviewer", prompt, work_dir)
    """

    def __init__(
        self,
        backend: str = "claude",
        model: str = "",
        timeout_sec: int = 300,
        permission_mode: str = "bypassPermissions",
        fallbacks: list[FallbackRule] | None = None,
    ):
        self.backend = backend
        self.model = model
        self.timeout_sec = timeout_sec
        self.permission_mode = permission_mode
        self.fallbacks = fallbacks or []
        self._sessions: dict[str, SessionInfo] = {}
        self._using_fallback: bool = False
        self._original_backend: str = backend
        self._original_model: str = model

    def call(self, role: str, prompt: str, work_dir: Path) -> AIResult:
        """
        指定した役割でAIを呼び出す。
        同じ役割なら前回のセッションを引き継ぐ。
        """
        session = self._sessions.get(role)
        is_resume = session is not None and session.session_id is not None

        if session is None and self.backend == "claude":
            # Claudeは初回からUUIDを指定できる
            sid = str(uuid.uuid4())
            session = SessionInfo(session_id=sid, backend=self.backend, role=role)
            self._sessions[role] = session
            is_resume = False
        elif session is None:
            # Gemini/Codexは初回実行後にIDを取得
            session = SessionInfo(session_id="", backend=self.backend, role=role)
            self._sessions[role] = session
            is_resume = False

        result = self._dispatch(
            prompt=prompt,
            work_dir=work_dir,
            session_id=session.session_id if session.session_id else None,
            is_resume=is_resume,
        )

        if result.returncode != 0:
            # フォールバック判定
            fallback = self._check_fallback(result)
            if fallback:
                print(f"  [fallback] {self.backend}/{self.model} → {fallback.fallback_backend}/{fallback.fallback_model}")
                self.backend = fallback.fallback_backend
                self.model = fallback.fallback_model
                self._using_fallback = True
                # セッションをリセットして新しいバックエンドで再実行
                del self._sessions[role]
                return self.call(role, prompt, work_dir)

            # 失敗したセッションは汚染されている可能性があるためリセット
            # 次回呼び出し時は新規セッションで開始される
            del self._sessions[role]
            return result

        # セッションIDを更新（Gemini/Codexの初回呼び出し後）
        if result.session_id:
            session.session_id = result.session_id
        session.call_count += 1

        return result

    def call_stateless(self, prompt: str, work_dir: Path) -> AIResult:
        """セッションを引き継がない単発呼び出し（レビュー等に使用）"""
        result = self._dispatch(
            prompt=prompt,
            work_dir=work_dir,
            session_id=None,
            is_resume=False,
        )

        if result.returncode != 0:
            fallback = self._check_fallback(result)
            if fallback:
                print(f"  [fallback] {self.backend}/{self.model} → {fallback.fallback_backend}/{fallback.fallback_model}")
                self.backend = fallback.fallback_backend
                self.model = fallback.fallback_model
                self._using_fallback = True
                return self.call_stateless(prompt, work_dir)

        return result

    def get_session(self, role: str) -> Optional[SessionInfo]:
        return self._sessions.get(role)

    def _check_fallback(self, result: AIResult) -> Optional[FallbackRule]:
        """結果を検査し、マッチするフォールバックルールがあれば返す"""
        if self._using_fallback:
            # 既にフォールバック中なら二重フォールバックしない
            return None
        combined = f"{result.stdout}\n{result.stderr}".lower()
        for rule in self.fallbacks:
            for pattern in rule.error_patterns:
                if pattern.lower() in combined:
                    return rule
        return None

    def _dispatch(
        self,
        prompt: str,
        work_dir: Path,
        session_id: Optional[str],
        is_resume: bool,
    ) -> AIResult:
        if self.backend == "claude":
            return _call_claude(
                prompt, self.model, work_dir, session_id, is_resume,
                self.timeout_sec, self.permission_mode,
            )
        elif self.backend == "gemini":
            return _call_gemini(
                prompt, self.model, work_dir, session_id, is_resume,
                self.timeout_sec,
            )
        elif self.backend == "codex":
            return _call_codex(
                prompt, self.model, work_dir, session_id, is_resume,
                self.timeout_sec,
            )
        else:
            raise ValueError(f"Unknown backend: {self.backend}")


# ==========================================
# 簡易エントリポイント（後方互換・単発呼び出し用）
# ==========================================
def call_ai(
    prompt: str,
    backend: str,
    model: str,
    work_dir: Path,
    timeout_sec: int = 300,
) -> AIResult:
    sm = SessionManager(backend=backend, model=model, timeout_sec=timeout_sec)
    return sm.call_stateless(prompt, work_dir)
