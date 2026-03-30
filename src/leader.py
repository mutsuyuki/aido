"""
Leader ロジック

パイプライン全体を監督するリーダーの判断処理。
- Phase完了後の checkpoint で次のアクションを決定
- パイプライン開始時の計画確認
- パイプライン終了時の最終評価
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.ai_backend import SessionManager
from src.models import LeaderDecision, PipelineState, PhaseResult


LEADER_ROLE = "leader"


def build_checkpoint_prompt(
    system_prompt: str,
    state: PipelineState,
    latest_result: PhaseResult,
    remaining_phases: list[dict],
) -> str:
    """Phase 完了後に Leader に渡すプロンプトを組み立てる"""
    parts = [system_prompt]

    parts.append(f"\n\n# 進捗レポート")
    parts.append(f"- プロジェクト: {state.project_name}")
    parts.append(f"- 全Phase数: {state.total_phases}")
    parts.append(f"- 完了: {state.completed}")
    parts.append(f"- 失敗: {state.failed}")
    parts.append(f"- 残り: {state.remaining}")

    if state.issues:
        parts.append(f"\n## 累積問題リスト")
        for issue in state.issues:
            parts.append(f"- {issue}")

    parts.append(f"\n## 直近の Phase 結果")
    parts.append(f"- Phase: {latest_result.phase_id} - {latest_result.title}")
    parts.append(f"- ステータス: {latest_result.status}")
    parts.append(f"- 試行回数: {len(latest_result.attempts)}")

    if latest_result.attempts:
        last_attempt = latest_result.attempts[-1]
        for step in last_attempt.steps:
            status = "OK" if step.success else "NG"
            parts.append(f"  - [{step.role}/{step.action}] {status}")
            if step.parsed:
                parts.append(f"    スコア: {step.parsed.get('score', '?')}")
                issues = step.parsed.get("issues", [])
                for issue in issues:
                    parts.append(f"    問題: {issue}")

    if remaining_phases:
        parts.append(f"\n## 残りの Phase 計画")
        for p in remaining_phases:
            parts.append(f"- {p['id']}: {p['title']}")

    parts.append(f"\n## あなたの判断")
    parts.append("以下のJSON形式で判断を返してください:")
    parts.append("""```json
{
  "decision": "continue / retry / add_phase / skip / abort",
  "notes": "判断の理由",
  "issues_to_track": ["追跡すべき問題があれば"],
  "retry_instructions": "retryの場合、具体的な指示",
  "plan_changes": [
    {"action": "add_phase", "after": "phase_02", "phase": {"id": "...", "title": "...", "steps": [...]}}
  ]
}
```""")

    return "\n".join(parts)


def build_plan_review_prompt(
    system_prompt: str,
    project_name: str,
    phases: list[dict],
    context: str = "",
) -> str:
    """パイプライン開始時の計画確認プロンプト"""
    parts = [system_prompt]

    parts.append(f"\n\n# 計画レビュー")
    parts.append(f"プロジェクト「{project_name}」の開発計画をレビューしてください。")

    if context:
        parts.append(f"\n## プロジェクト参考資料\n{context}")

    parts.append(f"\n## Phase 計画")
    for p in phases:
        deps = p.get("dependencies", [])
        steps = [f"{s['role']}/{s.get('action', s['role'])}" for s in p.get("steps", [])]
        parts.append(f"- {p['id']}: {p['title']}")
        parts.append(f"  ステップ: {' → '.join(steps)}")
        if deps:
            parts.append(f"  依存: {deps}")

    parts.append(f"\n## あなたの判断")
    parts.append("計画に問題がなければ continue、変更が必要なら修正案をJSON形式で返してください:")
    parts.append("""```json
{
  "decision": "continue / abort",
  "notes": "計画に対するコメント",
  "issues_to_track": [],
  "plan_changes": []
}
```""")

    return "\n".join(parts)


def build_final_review_prompt(
    system_prompt: str,
    state: PipelineState,
) -> str:
    """パイプライン終了時の最終評価プロンプト"""
    parts = [system_prompt]

    parts.append(f"\n\n# 最終評価")
    parts.append(f"プロジェクト「{state.project_name}」のパイプラインが完了しました。")
    parts.append(f"- 完了: {state.completed}")
    parts.append(f"- 失敗: {state.failed}")

    if state.issues:
        parts.append(f"\n## 累積問題リスト")
        for issue in state.issues:
            parts.append(f"- {issue}")

    if state.phase_summaries:
        parts.append(f"\n## Phase サマリー")
        for pid, summary in state.phase_summaries.items():
            parts.append(f"- {pid}: {summary}")

    parts.append("\n全体の評価と改善提案をJSON形式で返してください:")
    parts.append("""```json
{
  "decision": "continue",
  "notes": "全体の評価コメント",
  "issues_to_track": ["今後対応すべき残課題"]
}
```""")

    return "\n".join(parts)


def parse_leader_response(output: str) -> LeaderDecision:
    """Leader の応答をパースする"""
    match = re.search(r"\{[\s\S]*\}", output)
    if not match:
        return LeaderDecision(
            decision="continue",
            notes="Leader応答のパースに失敗。続行します。",
        )

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return LeaderDecision(
            decision="continue",
            notes="Leader応答のJSONパースに失敗。続行します。",
        )

    return LeaderDecision(
        decision=data.get("decision", "continue"),
        notes=data.get("notes", ""),
        issues_to_track=data.get("issues_to_track", []),
        plan_changes=data.get("plan_changes", []),
        retry_instructions=data.get("retry_instructions", ""),
    )


def call_leader(
    prompt: str,
    session_manager: SessionManager,
    work_dir: Path,
) -> LeaderDecision:
    """Leader を呼び出して判断を取得する"""
    result = session_manager.call(LEADER_ROLE, prompt, work_dir)

    if result.returncode != 0:
        print(f"  [leader] エラー: {result.stderr[:200]}")
        return LeaderDecision(
            decision="continue",
            notes=f"Leader呼び出しエラー。続行します。error={result.stderr[:100]}",
        )

    return parse_leader_response(result.stdout)
