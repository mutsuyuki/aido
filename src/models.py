"""
データクラス定義
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StepResult:
    """1ステップの実行結果"""
    role: str
    action: str
    success: bool
    elapsed_sec: float = 0.0
    session_id: Optional[str] = None
    output: str = ""
    failures: list[str] = field(default_factory=list)
    # reviewer/leader が返すパース済みJSON
    parsed: Optional[dict] = None
    # checker の stdout/stderr（構造化フィードバック用）
    checker_stdout: str = ""
    checker_stderr: str = ""


@dataclass
class AttemptLog:
    """Phase 1回試行の全ステップ結果"""
    attempt: int
    steps: list[StepResult] = field(default_factory=list)
    decision: str = ""  # "accepted", "failed_step", "failed_review", etc.


@dataclass
class PhaseResult:
    """Phase の最終結果"""
    phase_id: str
    title: str
    status: str  # "accepted", "failed"
    attempts: list[AttemptLog] = field(default_factory=list)


@dataclass
class LeaderDecision:
    """Leader の判断"""
    decision: str  # "continue", "retry", "add_phase", "skip", "abort"
    notes: str = ""
    issues_to_track: list[str] = field(default_factory=list)
    plan_changes: list[dict] = field(default_factory=list)
    retry_instructions: str = ""


@dataclass
class PipelineState:
    """パイプライン全体の状態（Leader に渡す情報）"""
    project_name: str
    total_phases: int
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    phase_summaries: dict[str, dict] = field(default_factory=dict)
