"""
パイプライン実行

Phase の順次実行、ステップ列の実行、リトライ制御、
Leader 判断によるフロー変更を担当する。
"""
from __future__ import annotations

import datetime
import json
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from src.ai_backend import FallbackRule, SessionManager
from src.config import (
    resolve_prompt,
    resolve_context_files,
    get_role_config,
    get_check_config,
)
from src.contract import (
    get_failure_taxonomy,
    classify_failure_type,
    resolve_strategy,
    build_checker_repair,
    build_reviewer_repair,
    verify_phase_contract,
)
from src.models import AttemptLog, ContractViolation, PhaseResult, PipelineState, StepResult
from src.steps import execute_step
from src.dashboard import start_dashboard, stop_dashboard
from src.leader import (
    build_checkpoint_prompt,
    build_plan_review_prompt,
    build_final_review_prompt,
    call_leader,
    LEADER_ROLE,
)

AIDO_DIR = Path(__file__).resolve().parent.parent  # src/ の親 = リポジトリルート


# ==========================================
# 成果物・ログ保存
# ==========================================
def _save_attempt_log(run_dir: Path, phase_id: str, attempt: int, log: AttemptLog) -> None:
    d = run_dir / phase_id / f"attempt_{attempt:02d}"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "log.json", "w", encoding="utf-8") as f:
        json.dump(asdict(log), f, ensure_ascii=False, indent=2)


def _save_step_artifact(run_dir: Path, phase_id: str, attempt: int, result: StepResult) -> None:
    """ステップの成果物をファイルに保存する"""
    d = run_dir / phase_id / f"attempt_{attempt:02d}"
    d.mkdir(parents=True, exist_ok=True)
    prefix = f"{result.role}_{result.action}"

    # AI出力
    if result.output and result.role not in ("checker", "human"):
        (d / f"{prefix}.md").write_text(result.output, encoding="utf-8")

    # reviewer/leader のパース済みJSON
    if result.parsed:
        with open(d / f"{prefix}.json", "w", encoding="utf-8") as f:
            json.dump(result.parsed, f, ensure_ascii=False, indent=2)

    # checker の stdout/stderr
    if result.role == "checker":
        if result.checker_stdout:
            (d / f"{prefix}_stdout.txt").write_text(result.checker_stdout, encoding="utf-8")
        if result.checker_stderr:
            (d / f"{prefix}_stderr.txt").write_text(result.checker_stderr, encoding="utf-8")


def _save_pipeline_summary(run_dir: Path, state: PipelineState, results: list[PhaseResult]) -> None:
    """パイプライン全体のサマリーを保存する"""
    summary = {
        "project": state.project_name,
        "total_phases": state.total_phases,
        "completed": state.completed,
        "failed": state.failed,
        "issues": state.issues,
        "phase_summaries": state.phase_summaries,
        "results": [asdict(r) for r in results],
    }
    with open(run_dir / "pipeline_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


# ==========================================
# Canonical state path (path-addressable artifacts)
# ==========================================
# 完了済みフェーズの成果物を、固定パス <work_dir>/.aido/state/<phase_id>/ に
# symlink で公開する。これにより:
#   - AI は timestamp を含まない安定パスから過去成果物を読める
#   - プロンプトに全文埋め込みする必要がなく、truncate もない
#   - AI が必要なファイルを必要なときだけ読みに行ける
#
# .aido/ ディレクトリ構造:
#   <work_dir>/.aido/
#     ├── runs/        実行ログ (タイムスタンプ付き)
#     ├── state/       フェーズ成果物への symlink
#     └── tmp/         AI バックエンド一時ファイル
def _aido_root(work_dir: Path) -> Path:
    return work_dir / ".aido"


def _state_root(work_dir: Path) -> Path:
    return _aido_root(work_dir) / "state"


def _runs_root(work_dir: Path) -> Path:
    return _aido_root(work_dir) / "runs"


def _reset_state_dir(work_dir: Path) -> None:
    """run 開始時に .aido/state/ を空にする"""
    aido_dir = _aido_root(work_dir)
    state_root = aido_dir / "state"
    if state_root.exists() or state_root.is_symlink():
        shutil.rmtree(state_root, ignore_errors=True)
    state_root.mkdir(parents=True, exist_ok=True)
    # .aido/ ごと git 管理外にする
    (aido_dir / ".gitignore").write_text("*\n", encoding="utf-8")


def _promote_phase_to_state(
    work_dir: Path, phase_id: str, source_run_dir: Path, attempt: int,
) -> None:
    """accept された attempt を .aido/state/<phase_id>/ に symlink する"""
    target = (source_run_dir / phase_id / f"attempt_{attempt:02d}").resolve()
    if not target.exists():
        return
    state_root = _state_root(work_dir)
    state_root.mkdir(parents=True, exist_ok=True)
    link = state_root / phase_id
    if link.is_symlink() or link.exists():
        if link.is_symlink() or link.is_file():
            link.unlink()
        else:
            shutil.rmtree(link, ignore_errors=True)
    link.symlink_to(target)


def _promote_from_resume(
    work_dir: Path, resume_run_dir: Path, all_phases: list[dict],
) -> None:
    """resume 元 run の各フェーズの最終 attempt を state/ に symlink する"""
    for phase in all_phases:
        pid = phase["id"]
        phase_dir = resume_run_dir / pid
        if not phase_dir.exists():
            continue
        attempts = sorted(
            d for d in phase_dir.iterdir()
            if d.is_dir() and d.name.startswith("attempt_")
        )
        if not attempts:
            continue
        last = attempts[-1]
        try:
            attempt_num = int(last.name.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        _promote_phase_to_state(work_dir, pid, resume_run_dir, attempt_num)


def _build_state_listing(work_dir: Path) -> str:
    """.aido/state/ 配下のファイルを列挙してプロンプト用文字列を作る"""
    state_root = _state_root(work_dir)
    if not state_root.exists():
        return ""
    lines = []
    for phase_link in sorted(state_root.iterdir()):
        # 中身（symlink 解決後）が読めるか
        try:
            files = sorted(
                f for f in phase_link.iterdir()
                if f.is_file() and f.suffix in (".md", ".json", ".txt") and f.name != "log.json"
            )
        except OSError:
            continue
        for f in files:
            try:
                size = f.stat().st_size
            except OSError:
                continue
            try:
                rel = f.relative_to(work_dir)
            except ValueError:
                rel = f
            lines.append(f"- {rel} ({size} bytes)")
    return "\n".join(lines)


# ==========================================
# Step 単位プロンプトの事前解決
# ==========================================
def _resolve_step_prompts(phases: list[dict], project_dir: Path) -> dict[str, str]:
    """
    Phase の steps に prompt フィールドがある場合、事前に読み込んでおく。
    キーは prompt ファイル名、値はボディ文字列。
    """
    step_prompts: dict[str, str] = {}
    for phase in phases:
        for step in phase.get("steps", []):
            prompt_file = step.get("prompt")
            if prompt_file and prompt_file not in step_prompts:
                try:
                    step_prompts[prompt_file] = resolve_prompt(prompt_file, project_dir)
                except FileNotFoundError:
                    step_prompts[prompt_file] = ""
    return step_prompts


# ==========================================
# Phase 実行
# ==========================================
def execute_phase(
    phase: dict,
    session_managers: dict[str, SessionManager],
    role_configs: dict[str, dict],
    system_prompts: dict[str, str],
    check_config: dict,
    work_dir: Path,
    context: str,
    max_retries: int,
    run_dir: Path,
    auto_approve: bool = False,
    confidence_threshold: int = 80,
    confidence_step: int = 5,
    state_listing: str = "",
    generation_config: dict | None = None,
) -> PhaseResult:
    """Phase 内のステップ列を実行し、失敗時はリトライする"""
    pid = phase["id"]
    title = phase["title"]
    steps = phase.get("steps", [])
    gen = generation_config or {}

    # フェーズ固有の checks があればグローバルより優先
    effective_check_config = phase.get("checks", check_config)

    # フェーズ単位の上書き（省略時はグローバル値を使用）
    effective_max_retries = phase.get("max_retries", max_retries)
    effective_confidence_threshold = phase.get("confidence_threshold", confidence_threshold)
    effective_confidence_step = phase.get("confidence_step", confidence_step)
    pass_on_max_retries = phase.get("pass_on_max_retries", False)

    # Contract / Failure Taxonomy の解決
    contract = phase.get("contract", {})
    failure_taxonomy = get_failure_taxonomy(phase, gen)

    print(f"\n{'='*60}")
    print(f"Phase: {pid} - {title}")
    step_names = [f"{s['role']}/{s.get('action', s['role'])}" for s in steps]
    print(f"Steps: {' -> '.join(step_names)}")
    if contract:
        print(f"Contract: {contract}")
    if pass_on_max_retries:
        print(f"pass_on_max_retries: true")
    print(f"{'='*60}")

    logs: list[AttemptLog] = []
    repair = ""

    for attempt in range(1, effective_max_retries + 1):
        print(f"\n  --- 試行 {attempt}/{effective_max_retries} ---")

        # リトライ回数に応じてconfidence閾値を引き上げ（最大100）
        effective_threshold = min(effective_confidence_threshold + effective_confidence_step * (attempt - 1), 100)
        if attempt > 1 and effective_confidence_step > 0:
            print(f"  (confidence_threshold: {effective_threshold})")

        attempt_log = AttemptLog(attempt=attempt)
        all_ok = True
        # この試行で検出された violations を蓄積
        attempt_violations: list[ContractViolation] = []

        for step in steps:
            result = execute_step(
                step=step,
                phase=phase,
                session_managers=session_managers,
                role_configs=role_configs,
                system_prompts=system_prompts,
                check_config=effective_check_config,
                work_dir=work_dir,
                context=context,
                repair_instructions=repair,
                auto_approve=auto_approve,
                confidence_threshold=effective_threshold,
                state_listing=state_listing,
            )
            attempt_log.steps.append(result)
            _save_step_artifact(run_dir, pid, attempt, result)

            if not result.success:
                all_ok = False
                if result.role == "human":
                    attempt_log.decision = "rejected_by_user"
                    logs.append(attempt_log)
                    _save_attempt_log(run_dir, pid, attempt, attempt_log)
                    return PhaseResult(pid, title, "failed", logs)
                elif result.role == "checker":
                    repair, vs = build_checker_repair(result, contract, work_dir, phase)
                    attempt_violations.extend(vs)
                    attempt_log.decision = "failed_checker"
                elif result.parsed and result.parsed.get("pass") is False:
                    # reviewer が pass=false（steps.py で success=False に統一済み）
                    repair, vs = build_reviewer_repair(result, contract)
                    attempt_violations.extend(vs)
                    attempt_log.decision = "failed_review"
                    print(f"  [review] 修正指示: {repair[:150]}...")
                elif result.timed_out:
                    attempt_violations.append(ContractViolation(
                        fact="timeout",
                        detail=f"{result.role}/{result.action} timed out",
                    ))
                    repair = f"{result.role}/{result.action} がタイムアウトしました"
                    attempt_log.decision = f"failed_{result.role}"
                else:
                    attempt_violations.append(ContractViolation(
                        fact="session_error",
                        detail=f"{result.role}/{result.action}: {result.failures}"[:200],
                    ))
                    repair = f"{result.role}/{result.action} が失敗しました: {result.failures}"
                    attempt_log.decision = f"failed_{result.role}"
                break

            # human が skip を返した場合
            if result.role == "human" and result.output == "skipped":
                print(f"  [human] スキップされました")
                break

        # --- Phase 完了時の Contract 検証 ---
        if all_ok:
            phase_vs = verify_phase_contract(contract, phase, work_dir, steps)
            attempt_violations.extend(phase_vs)
            if phase_vs:
                all_ok = False
                for v in phase_vs:
                    print(f"  [contract] {v.fact}: {v.pattern}")

        # --- Failure Taxonomy: 畳み込み + strategy dispatch + 記録 ---
        if attempt_violations:
            attempt_log.contract_violations = attempt_violations
            failure_type = classify_failure_type(attempt_violations)
            strategy = resolve_strategy(failure_type, failure_taxonomy)
            attempt_log.failure_type = failure_type
            attempt_log.strategy_applied = strategy

            if failure_type:
                print(f"  [taxonomy] failure_type={failure_type}, strategy={strategy or '(legacy)'}")

        if all_ok:
            attempt_log.decision = "accepted"
            logs.append(attempt_log)
            _save_attempt_log(run_dir, pid, attempt, attempt_log)
            print(f"\n  Phase {pid} 合格!")
            return PhaseResult(pid, title, "accepted", logs)

        logs.append(attempt_log)
        _save_attempt_log(run_dir, pid, attempt, attempt_log)

        # --- Strategy に基づくリトライ判定 ---
        strategy = attempt_log.strategy_applied
        if strategy == "abort":
            print(f"\n  [taxonomy] abort 指定。Phase {pid} を即座に失敗とします。")
            return PhaseResult(pid, title, "failed", logs)
        elif strategy == "session_reset_and_retry":
            # 該当ロールのセッションをリセット（SessionManager 側で次回新規セッションになる）
            failed_role = attempt_log.steps[-1].role if attempt_log.steps else ""
            if failed_role and failed_role in session_managers:
                sm = session_managers[failed_role]
                if failed_role in sm._sessions:
                    del sm._sessions[failed_role]
                    print(f"  [taxonomy] {failed_role} のセッションをリセット")

        # リトライ前にウェイト（指数バックオフ: 30s, 60s, 120s, ...）
        if attempt < effective_max_retries:
            wait_sec = 30 * (2 ** (attempt - 1))
            print(f"\n  リトライ待機中... {wait_sec}秒")
            time.sleep(wait_sec)

    # max_retries 到達
    if pass_on_max_retries:
        print(f"\n  Phase {pid} 最大試行数到達。pass_on_max_retries により合格扱い。")
        return PhaseResult(pid, title, "accepted", logs)

    print(f"\n  Phase {pid} 最大試行数到達。失敗。")
    return PhaseResult(pid, title, "failed", logs)


# ==========================================
# パイプライン全体実行
# ==========================================
def run_pipeline(
    config: dict,
    auto_approve: bool = False,
    user_input: str = "",
    resume_run: str | None = None,
) -> list[PhaseResult]:
    """パイプライン全体を実行する"""
    project = config["project"]
    gen = config.get("generation", {})
    phases = list(config["phases"])
    project_dir = Path(config["_project_dir"])
    work_dir = Path(project["work_dir"])
    use_leader = gen.get("use_leader", False)
    max_retries = gen.get("max_retries", 3)
    stop_on_failure = gen.get("stop_on_failure", True)
    confidence_threshold = gen.get("confidence_threshold", 80)
    confidence_step = gen.get("confidence_step", 5)

    # ignore ファイルを aido ルートから work_dir にコピー
    for ignore_file in [".geminiignore", ".claudeignore"]:
        src = AIDO_DIR / ignore_file
        dst = work_dir / ignore_file
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            print(f"  Copied {ignore_file} to {work_dir}")

    # runs/ は <work_dir>/.aido/runs/ に保存
    runs_base = _runs_root(work_dir)
    run_dir = runs_base / datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    # ダッシュボードは手動起動に変更（python main.py dashboard <project>）
    _dashboard_thread = None

    # ロール設定の解決（frontmatter マージ対応）
    role_configs = get_role_config(config, project_dir)
    check_config = get_check_config(config, project_dir)
    context = resolve_context_files(project_dir)
    if user_input:
        context += f"\n\n=== ユーザー指示 ===\n{user_input}"

    # --resume-run: 前回の runs/ から成果物を引き継ぐ
    resume_run_dir: Path | None = None
    if resume_run:
        candidate = Path(resume_run)
        if not candidate.is_absolute():
            candidate = work_dir / candidate
        if candidate.is_dir():
            resume_run_dir = candidate
            print(f"  Resume run: {resume_run_dir}")

    # canonical state path のリセット & resume 元からの引き継ぎ
    work_dir.mkdir(parents=True, exist_ok=True)
    _reset_state_dir(work_dir)
    if resume_run_dir:
        _promote_from_resume(work_dir, resume_run_dir, phases)

    # フォールバックルールの読み込み
    fallback_rules_raw = gen.get("fallbacks", {})
    fallback_rules: dict[str, list[FallbackRule]] = {}
    for role_name, rules in fallback_rules_raw.items():
        fallback_rules[role_name] = [
            FallbackRule(
                error_patterns=r.get("error_patterns", []),
                fallback_backend=r["fallback_backend"],
                fallback_model=r["fallback_model"],
            )
            for r in rules
        ]

    # SessionManager をロールごとに作成
    session_managers: dict[str, SessionManager] = {}
    for role_name, rcfg in role_configs.items():
        session_managers[role_name] = SessionManager(
            backend=rcfg["backend"],
            model=rcfg["model"],
            timeout_sec=rcfg["timeout_sec"],
            permission_mode=rcfg["permission_mode"],
            fallbacks=fallback_rules.get(role_name, []),
        )

    # システムプロンプトの読み込み（ロール単位）
    system_prompts: dict[str, str] = {}
    for role_name, rcfg in role_configs.items():
        try:
            system_prompts[role_name] = resolve_prompt(rcfg["prompt"], project_dir)
        except FileNotFoundError:
            system_prompts[role_name] = ""

    # Step 単位のプロンプトも事前に読み込む
    step_prompts = _resolve_step_prompts(phases, project_dir)
    system_prompts.update(step_prompts)

    # パイプライン状態
    state = PipelineState(
        project_name=project["name"],
        total_phases=len(phases),
        remaining=[p["id"] for p in phases],
    )

    # --- Leader: 計画確認 ---
    if use_leader:
        print("\n[Leader] 計画レビュー中...")
        plan_prompt = build_plan_review_prompt(
            system_prompts.get("leader", ""),
            project["name"], phases, context,
        )
        decision = call_leader(plan_prompt, session_managers["leader"], work_dir)
        print(f"[Leader] 判断: {decision.decision} - {decision.notes}")
        if decision.decision == "abort":
            print("[Leader] パイプラインを中止します。")
            return []
        state.issues.extend(decision.issues_to_track)

    # --- Phase 実行ループ ---
    results: list[PhaseResult] = []
    i = 0

    while i < len(phases):
        phase = phases[i]
        pid = phase["id"]

        state.remaining = [p["id"] for p in phases[i:]]

        # canonical state path 配下のファイル一覧を生成（プロンプト注入用）
        state_listing = _build_state_listing(work_dir)

        result = execute_phase(
            phase=phase,
            session_managers=session_managers,
            role_configs=role_configs,
            system_prompts=system_prompts,
            check_config=check_config,
            work_dir=work_dir,
            context=context,
            max_retries=max_retries,
            run_dir=run_dir,
            auto_approve=auto_approve,
            confidence_threshold=confidence_threshold,
            confidence_step=confidence_step,
            state_listing=state_listing,
            generation_config=gen,
        )
        results.append(result)

        # 状態更新
        if result.status == "accepted":
            state.completed.append(pid)
            # accept された attempt を canonical state path に昇格
            accepted_attempt = len(result.attempts)
            _promote_phase_to_state(work_dir, pid, run_dir, accepted_attempt)
        else:
            state.failed.append(pid)
        state.phase_summaries[pid] = {
            "status": result.status,
            "attempts": len(result.attempts),
        }

        # --- Leader: checkpoint ---
        if use_leader:
            print(f"\n[Leader] checkpoint: Phase {pid} 完了後の判断中...")
            cp_prompt = build_checkpoint_prompt(
                system_prompts.get("leader", ""),
                state, result, phases[i + 1:],
            )
            decision = call_leader(cp_prompt, session_managers["leader"], work_dir)
            print(f"[Leader] 判断: {decision.decision} - {decision.notes}")

            state.issues.extend(decision.issues_to_track)

            if decision.decision == "abort":
                print("[Leader] パイプラインを中止します。")
                break
            elif decision.decision == "retry":
                print(f"[Leader] Phase {pid} をリトライします。")
                if result.status == "failed":
                    state.failed.remove(pid)
                continue
            elif decision.decision == "skip":
                skipped_idx = i + 1
                if skipped_idx < len(phases):
                    skipped = phases[skipped_idx]
                    print(f"[Leader] 次の Phase をスキップします: {skipped['id']}")
                    state.phase_summaries[skipped["id"]] = {
                        "status": "skipped",
                        "attempts": 0,
                    }
                else:
                    print(f"[Leader] skip 指定だが次の Phase がありません。")
                i = skipped_idx + 1
                continue
            elif decision.decision == "add_phase":
                for change in decision.plan_changes:
                    if change.get("action") == "add_phase" and "phase" in change:
                        new_phase = change["phase"]
                        insert_after = change.get("after", pid)
                        idx = next(
                            (j for j, p in enumerate(phases) if p["id"] == insert_after),
                            i,
                        )
                        phases.insert(idx + 1, new_phase)
                        state.total_phases = len(phases)
                        print(f"[Leader] Phase追加: {new_phase.get('id', '?')} (after {insert_after})")
        else:
            if result.status == "failed" and stop_on_failure:
                print(f"\n[中断] Phase {pid} が失敗。パイプラインを停止します。")
                break

        i += 1

    # --- Leader: 最終評価 ---
    if use_leader:
        print(f"\n[Leader] 最終評価中...")
        final_prompt = build_final_review_prompt(
            system_prompts.get("leader", ""), state,
        )
        decision = call_leader(final_prompt, session_managers["leader"], work_dir)
        print(f"[Leader] 最終評価: {decision.notes}")

    # --- サマリー保存 ---
    _save_pipeline_summary(run_dir, state, results)

    print(f"\n{'='*60}")
    print("=== 実行結果サマリー ===")
    print(f"  Run: {run_dir}")
    for r in results:
        icon = "OK" if r.status == "accepted" else "NG"
        print(f"  [{icon}] {r.phase_id}: {r.title} ({r.status}, {len(r.attempts)}試行)")
    if state.issues:
        print("\n=== 追跡中の問題 ===")
        for issue in state.issues:
            print(f"  - {issue}")
    print("Done.")

    # ダッシュボードを停止
    try:
        stop_dashboard()
    except Exception:
        pass

    return results
