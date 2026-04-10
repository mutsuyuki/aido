"""
ステップ実行

各ロールのステップ実行ロジックを定義する。

ステップ定義(YAMLから):
  - role: coder
    action: implement
  - role: checker
    action: run_checks
  - role: reviewer
    action: review
    prompt: reviewer_correctness.md   # step単位でプロンプト上書き可能
  - role: human
    action: approve                   # ユーザー確認ステップ
"""
from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from src.ai_backend import SessionManager
from src.models import StepResult


def _find_json_in_workdir(work_dir: Path, step_start_time: float) -> dict | None:
    """ステップ実行中にワークディレクトリに作られた .json ファイルからレビュー結果を探す。

    誤検出を避けるため:
      - ファイル名に review/reviewer を含むものを優先
      - パース後 dict であり、"pass" が bool であることを要求
    """
    candidates = []
    for f in work_dir.rglob("*.json"):
        try:
            if f.stat().st_mtime >= step_start_time:
                candidates.append(f)
        except OSError:
            continue

    # review/reviewer を名前に含むファイルを優先、その後 mtime 降順
    def _sort_key(p: Path):
        name = p.name.lower()
        named = 0 if ("review" in name) else 1
        return (named, -p.stat().st_mtime)

    for f in sorted(candidates, key=_sort_key):
        try:
            content = f.read_text(encoding="utf-8")
            # コードブロックで囲まれている場合は中身を取り出す
            content = re.sub(r"^```\w*\n", "", content).rstrip("`\n ")
            match = re.search(r"\{[\s\S]*\}", content)
            if not match:
                continue
            parsed = json.loads(match.group(0))
            if not isinstance(parsed, dict):
                continue
            if not isinstance(parsed.get("pass"), bool):
                continue
            print(f"  [fallback] JSONをファイルから取得: {f.name}")
            return parsed
        except (json.JSONDecodeError, OSError):
            continue
    return None


# ==========================================
# AI ステップ（共通）
# ==========================================
def _call_ai_step(
    role: str,
    action: str,
    prompt: str,
    session_manager: SessionManager,
    work_dir: Path,
    use_session: str,  # "continue", "stateless", "new"
) -> StepResult:
    """AIロールの共通呼び出し"""
    start = time.time()

    if use_session == "stateless":
        result = session_manager.call_stateless(prompt, work_dir)
    else:
        result = session_manager.call(role, prompt, work_dir)

    elapsed = round(time.time() - start, 2)

    if result.returncode != 0:
        return StepResult(
            role=role, action=action, success=False, elapsed_sec=elapsed,
            session_id=result.session_id, output=result.stderr[:500],
            failures=[f"AI call failed (exit={result.returncode}): {result.stderr[:200]}"],
            timed_out=result.timed_out,
        )

    # reviewer/leader のJSONパース
    parsed = None
    if role in ("reviewer", "leader") or action in ("review", "checkpoint", "approve"):
        match = re.search(r"\{[\s\S]*\}", result.stdout)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        # stdout にJSONがなければ、ワークディレクトリ内の新規 .json ファイルを探す
        if parsed is None and work_dir:
            parsed = _find_json_in_workdir(work_dir, start)

    return StepResult(
        role=role, action=action, success=True, elapsed_sec=elapsed,
        session_id=result.session_id, output=result.stdout, parsed=parsed,
    )


# ==========================================
# Checker ステップ（非AI）
# ==========================================
def _run_checker(
    check_config: dict,
    work_dir: Path,
) -> StepResult:
    """機械検査を実行する"""
    start = time.time()

    if "module" in check_config:
        project_dir = check_config.get("_project_dir", ".")
        sys.path.insert(0, project_dir)
        try:
            mod = importlib.import_module(check_config["module"])
            func = getattr(mod, check_config.get("function", "run_all"))
            ok, failures = func(work_dir)
        finally:
            sys.path.pop(0)
        elapsed = round(time.time() - start, 2)
        return StepResult(
            role="checker", action="run_checks", success=ok,
            elapsed_sec=elapsed, failures=failures,
        )

    if "commands" in check_config:
        failures = []
        all_stdout = []
        all_stderr = []
        for cmd_str in check_config["commands"]:
            try:
                r = subprocess.run(
                    cmd_str, shell=True, capture_output=True, text=True,
                    timeout=120, cwd=str(work_dir),
                )
                if r.stdout:
                    all_stdout.append(f"[{cmd_str}]\n{r.stdout}")
                if r.stderr:
                    all_stderr.append(f"[{cmd_str}]\n{r.stderr}")
                if r.returncode != 0:
                    failures.append(f"[{cmd_str}] exit={r.returncode}: {r.stderr[:200]}")
            except subprocess.TimeoutExpired:
                failures.append(f"[{cmd_str}] timeout")
        elapsed = round(time.time() - start, 2)
        return StepResult(
            role="checker", action="run_checks", success=len(failures) == 0,
            elapsed_sec=elapsed, failures=failures,
            checker_stdout="\n".join(all_stdout),
            checker_stderr="\n".join(all_stderr),
        )

    # チェック未設定
    return StepResult(role="checker", action="run_checks", success=True, elapsed_sec=0.0)


# ==========================================
# Human approval ステップ
# ==========================================
def _human_approval(phase: dict, auto_approve: bool) -> StepResult:
    """ユーザー確認を求める。auto_approve=True なら自動承認。"""
    if auto_approve:
        print(f"  [human/approve] 自動承認 (--auto-approve)")
        return StepResult(role="human", action="approve", success=True)

    print(f"\n  === ユーザー確認が必要です ===")
    print(f"  Phase: {phase['title']}")
    if phase.get("tasks"):
        for task in phase["tasks"]:
            print(f"    - {task}")
    print()

    while True:
        answer = input("  続行しますか？ [y]es / [n]o / [s]kip: ").strip().lower()
        if answer in ("y", "yes", ""):
            return StepResult(role="human", action="approve", success=True)
        elif answer in ("n", "no"):
            return StepResult(
                role="human", action="approve", success=False,
                failures=["ユーザーが拒否しました"],
            )
        elif answer in ("s", "skip"):
            return StepResult(
                role="human", action="approve", success=True,
                output="skipped",
            )
        print("  y / n / s で答えてください。")


# ==========================================
# Human override（AIロールを人間が代行）
# ==========================================
def _human_override(role: str, action: str, phase: dict, auto_approve: bool) -> StepResult:
    """AIロールの代わりに人間が作業する。"""
    if auto_approve:
        print(f"  [{role}/{action}] human_override: auto-approve モードではスキップ")
        return StepResult(role=role, action=action, success=True, output="(human_override skipped in auto-approve)")

    print(f"\n  === 人間による作業が必要です ({role}/{action}) ===")
    print(f"  Phase: {phase['title']}")
    if phase.get("tasks"):
        for task in phase["tasks"]:
            print(f"    - {task}")
    print(f"\n  ワークディレクトリで作業を行い、完了したら Enter を押してください。")
    print(f"  中断する場合は 'abort' と入力してください。")

    answer = input("  > ").strip().lower()
    if answer == "abort":
        return StepResult(
            role=role, action=action, success=False,
            failures=["ユーザーが作業を中断しました"],
        )

    return StepResult(role=role, action=action, success=True, output="(human_override completed)")


# ==========================================
# Confidence フィルタリング（reviewer用）
# ==========================================
def filter_review_by_confidence(parsed: dict, threshold: int = 80) -> dict:
    """
    reviewer の応答から confidence が閾値未満の issue を除外する。

    期待するJSON形式:
    {
      "pass": true/false,
      "issues": [
        {"description": "...", "confidence": 90, "file": "...", "fix": "..."},
        {"description": "...", "confidence": 50, ...}
      ],
      "repair_instructions": "..."
    }

    旧形式（issues が文字列配列）もそのまま通す（後方互換）。
    """
    issues = parsed.get("issues", [])
    if not issues or not isinstance(issues[0], dict):
        return parsed

    high_confidence = [i for i in issues if i.get("confidence", 100) >= threshold]
    filtered = dict(parsed)
    filtered["issues"] = high_confidence
    filtered["_filtered_count"] = len(issues) - len(high_confidence)

    # 高confidence issue がなければ pass にする
    if not high_confidence and not parsed.get("pass", False):
        filtered["pass"] = True
        filtered["_auto_passed"] = True

    return filtered


# ==========================================
# プロンプト組み立て
# ==========================================
def build_step_prompt(
    system_prompt: str,
    phase: dict,
    step: dict,
    context: str = "",
    repair_instructions: str = "",
    prior_outputs: str = "",
) -> str:
    """ステップ用のプロンプトを組み立てる"""
    parts = [system_prompt]

    parts.append(f"\n\n# Phase: {phase['title']}")

    if phase.get("description"):
        parts.append(f"\n{phase['description']}")

    if phase.get("tasks"):
        parts.append("\n\n## タスク")
        for task in phase["tasks"]:
            parts.append(f"- {task}")

    if phase.get("constraints"):
        parts.append("\n\n## 制約")
        for c in phase["constraints"]:
            parts.append(f"- {c}")

    if phase.get("dependencies"):
        parts.append("\n\n## 依存 (完了済み)")
        for dep in phase["dependencies"]:
            parts.append(f"- {dep}")

    if context:
        parts.append(f"\n\n## プロジェクト参考資料\n{context}")

    if prior_outputs:
        parts.append(f"\n\n## 前フェーズの成果物\n{prior_outputs}")

    if repair_instructions:
        parts.append(f"\n\n## 修正指示 (前回のフィードバック)\n{repair_instructions}")

    return "\n".join(parts)


def build_review_prompt(
    system_prompt: str,
    phase: dict,
) -> str:
    """レビュー用プロンプトを組み立てる"""
    parts = [system_prompt]
    parts.append(f"\n\n# レビュー対象: {phase['title']}")

    if phase.get("review_checklist"):
        parts.append("\n\n## チェックリスト")
        for item in phase["review_checklist"]:
            parts.append(f"- {item}")

    parts.append("\n\nワークディレクトリの該当ファイルを確認してレビューしてください。")

    return "\n".join(parts)


# ==========================================
# ステップ実行ディスパッチ
# ==========================================
def execute_step(
    step: dict,
    phase: dict,
    session_managers: dict[str, SessionManager],
    role_configs: dict[str, dict],
    system_prompts: dict[str, str],
    check_config: dict,
    work_dir: Path,
    context: str = "",
    repair_instructions: str = "",
    auto_approve: bool = False,
    confidence_threshold: int = 80,
    prior_outputs: str = "",
) -> StepResult:
    """
    1ステップを実行する。

    step の形式:
      {"role": "coder", "action": "implement"}
      {"role": "reviewer", "action": "review", "prompt": "reviewer_correctness.md"}
      {"role": "human", "action": "approve"}
    """
    role = step["role"]
    action = step.get("action", role)

    print(f"  [{role}/{action}] 実行中...")

    # Human approval
    if role == "human" or action == "human_approval":
        return _human_approval(phase, auto_approve)

    # Checker（非AI）
    if role == "checker":
        result = _run_checker(check_config, work_dir)
        status = "OK" if result.success else f"NG: {', '.join(result.failures)}"
        print(f"  [{role}/{action}] {status}")
        return result

    # Human override（AI ロールを人間が代行）
    if step.get("human_override"):
        return _human_override(role, action, phase, auto_approve)

    # AI ロール
    role_cfg = role_configs.get(role, {})

    # step 単位の backend/model 上書き
    # 指定されていればその step だけ別の SessionManager を使う
    step_backend = step.get("backend")
    step_model = step.get("model")
    if step_backend or step_model:
        effective_backend = step_backend or role_cfg.get("backend", "claude")
        effective_model = step_model or role_cfg.get("model", "")
        sm = SessionManager(
            backend=effective_backend,
            model=effective_model,
            timeout_sec=role_cfg.get("timeout_sec", 300),
            permission_mode=role_cfg.get("permission_mode", "bypassPermissions"),
        )
        print(f"  [{role}/{action}] step-level override: {effective_backend}/{effective_model}")
    else:
        sm = session_managers.get(role)

    if sm is None:
        return StepResult(
            role=role, action=action, success=False,
            failures=[f"SessionManager not found for role: {role}"],
        )

    # step 単位のプロンプト上書き → system_prompts dict から取得
    step_prompt_key = step.get("prompt")
    if step_prompt_key:
        system_prompt = system_prompts.get(step_prompt_key, "")
    else:
        system_prompt = system_prompts.get(role, "")

    if role == "reviewer" or action == "review":
        prompt = build_review_prompt(system_prompt, phase)
    else:
        prompt = build_step_prompt(
            system_prompt, phase, step,
            context=context, repair_instructions=repair_instructions,
            prior_outputs=prior_outputs,
        )

    # step-level override の場合は stateless（セッション引き継ぎなし）
    use_session = "stateless" if (step_backend or step_model) else role_cfg.get("session", "continue")

    result = _call_ai_step(
        role=role,
        action=action,
        prompt=prompt,
        session_manager=sm,
        work_dir=work_dir,
        use_session=use_session,
    )

    # Confidence フィルタリング（reviewer の場合）
    if result.success and result.parsed and (role == "reviewer" or action == "review"):
        result.parsed = filter_review_by_confidence(result.parsed, confidence_threshold)
        filtered = result.parsed.get("_filtered_count", 0)
        if filtered > 0:
            print(f"  [{role}/{action}] {filtered}件の低confidence issueを除外")

        # reviewer の pass=false を success=false に統一
        # pipeline.py での二重チェックを不要にする
        if result.parsed.get("pass") is False:
            result.success = False

    if result.success:
        print(f"  [{role}/{action}] 完了 ({result.elapsed_sec}s)")
        if result.parsed:
            score = result.parsed.get("score", "?")
            passed = result.parsed.get("pass", "?")
            print(f"  [{role}/{action}] スコア: {score}, パス: {passed}")
            if result.parsed.get("issues"):
                for issue in result.parsed["issues"][:5]:
                    if isinstance(issue, dict):
                        conf = issue.get("confidence", "?")
                        desc = issue.get("description", str(issue))
                        print(f"    [{conf}] {desc[:80]}")
                    else:
                        print(f"    - {str(issue)[:80]}")
    else:
        print(f"  [{role}/{action}] 失敗: {result.failures}")

    return result
