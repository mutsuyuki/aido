"""
Contract / Failure Taxonomy

Phase の合格条件（contract）の検証、失敗の型分け（failure taxonomy）、
Step 失敗時の repair_instructions 構築を担当する。

全て純粋関数（副作用なし、ただし print による進捗表示を含む）。
"""
from __future__ import annotations

from pathlib import Path

from src.models import ContractViolation, StepResult


# ==========================================
# Failure Taxonomy 解決
# ==========================================
def get_failure_taxonomy(phase: dict, gen: dict) -> dict[str, str] | None:
    """
    phase → generation → None の順でfailure_taxonomyを解決する。
    未指定なら None（従来挙動にフォールバック）。
    """
    phase_ft = phase.get("failure_taxonomy")
    gen_ft = gen.get("failure_taxonomy")

    if phase_ft is None and gen_ft is None:
        return None

    # generation のデフォルトに phase の上書きをマージ
    merged = dict(gen_ft or {})
    if phase_ft:
        merged.update(phase_ft)
    return merged


def classify_failure_type(violations: list[ContractViolation]) -> str:
    """
    violations のリストから failure_type を決定する（畳み込み）。
    優先順位: missing_artifact > timeout > checker_error > reviewer_rejection
    """
    facts = {v.fact for v in violations}

    if "required_file_missing" in facts:
        return "missing_artifact"
    if "timeout" in facts or "session_error" in facts:
        return "timeout"
    if "checker_nonzero" in facts or "forbidden_pattern" in facts:
        return "checker_error"
    if "confidence_below_min" in facts:
        return "reviewer_rejection"
    return ""


def resolve_strategy(failure_type: str, taxonomy: dict[str, str] | None) -> str:
    """
    failure_type に対応する strategy を taxonomy から解決する。
    taxonomy が None なら空文字列（従来挙動）。
    """
    if not failure_type or taxonomy is None:
        return ""
    return taxonomy.get(failure_type, "")


# ==========================================
# Contract 違反検出
# ==========================================
def detect_forbidden_patterns(
    contract: dict, work_dir: Path, phase: dict,
) -> list[ContractViolation]:
    """
    forbidden_patterns を outputs の対象ファイルのみで検索する。
    対象ファイルが指定されていない場合は検索しない。
    """
    patterns = contract.get("forbidden_patterns", [])
    if not patterns:
        return []

    # 検索対象: phase の outputs
    target_globs = list(phase.get("outputs", []))

    if not target_globs:
        return []

    # glob パターンにマッチするファイルを列挙
    target_files: set[Path] = set()
    for g in target_globs:
        target_files.update(work_dir.glob(g))

    violations = []
    for fp in sorted(target_files):
        if not fp.is_file():
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            for pat in patterns:
                if pat in line:
                    rel = fp.relative_to(work_dir)
                    violations.append(ContractViolation(
                        fact="forbidden_pattern",
                        pattern=pat,
                        file=f"{rel}:{line_no}",
                        detail=line.strip()[:120],
                    ))
    return violations


def detect_outputs(phase: dict, work_dir: Path) -> list[ContractViolation]:
    """
    outputs 宣言のファイルが存在するか検証する（phase 完了時）。
    outputs 未指定なら空リスト。
    """
    outputs = phase.get("outputs", [])
    violations = []
    for pattern in outputs:
        matches = list(work_dir.glob(pattern))
        # case-insensitive fallback: fnmatch でワイルドカードにも対応
        if not matches:
            import fnmatch
            parent_pattern = str(Path(pattern).parent)
            name_pattern = Path(pattern).name
            search_dir = work_dir / parent_pattern if parent_pattern != "." else work_dir
            if search_dir.is_dir():
                matches = [
                    p for p in search_dir.iterdir()
                    if fnmatch.fnmatch(p.name.lower(), name_pattern.lower())
                ]
        if not matches:
            violations.append(ContractViolation(
                fact="required_file_missing",
                pattern=pattern,
                detail=f"Declared output '{pattern}' not found",
            ))
    return violations


# ==========================================
# Step 失敗時の repair 構築
# ==========================================
def build_checker_repair(
    result: StepResult,
    contract: dict,
    work_dir: Path,
    phase: dict,
) -> tuple[str, list[ContractViolation]]:
    """
    checker 失敗時に repair_instructions と violations を構築する。
    Returns: (repair文字列, violations リスト)
    """
    violations = [ContractViolation(
        fact="checker_nonzero",
        detail=f"exit code: {', '.join(result.failures)}"[:200],
    )]

    # forbidden_patterns 検出
    if contract.get("forbidden_patterns"):
        forbidden_vs = detect_forbidden_patterns(contract, work_dir, phase)
        violations.extend(forbidden_vs)
        if forbidden_vs:
            print(f"  [contract] forbidden_patterns: {len(forbidden_vs)}件検出")

    # repair 文字列を構築
    parts = ["機械検査の失敗:"]
    parts.extend(f"- {f}" for f in result.failures)
    if result.checker_stdout:
        parts.append(f"\n### 標準出力:\n```\n{result.checker_stdout[:2000]}\n```")
    if result.checker_stderr:
        parts.append(f"\n### エラー出力:\n```\n{result.checker_stderr[:2000]}\n```")

    forbidden_in_violations = [v for v in violations if v.fact == "forbidden_pattern"]
    if forbidden_in_violations:
        parts.append("\n### forbidden_patterns 検出:")
        for v in forbidden_in_violations[:10]:
            parts.append(f"- `{v.pattern}` in {v.file}: {v.detail}")

    return "\n".join(parts), violations


def build_reviewer_repair(
    result: StepResult,
    contract: dict,
) -> tuple[str, list[ContractViolation]]:
    """
    reviewer が pass=false を返した時に repair_instructions と violations を構築する。
    Returns: (repair文字列, violations リスト)
    """
    reviewer_min = contract.get("reviewer_confidence_min")
    detail = (f"reviewer returned pass=false (threshold: {reviewer_min})"
              if reviewer_min is not None
              else "reviewer returned pass=false")
    violations = [ContractViolation(fact="confidence_below_min", detail=detail)]

    # repair 文字列を構築
    issues = result.parsed.get("issues", [])
    if issues and isinstance(issues[0], dict):
        issue_lines = []
        for iss in issues:
            desc = iss.get("description", str(iss))
            fix = iss.get("fix", "")
            file_ref = iss.get("file", "")
            line = f"- [{iss.get('confidence', '?')}] {desc}"
            if file_ref:
                line += f" ({file_ref})"
            if fix:
                line += f"\n  修正案: {fix}"
            issue_lines.append(line)
        repair = "レビュー指摘:\n" + "\n".join(issue_lines)
    else:
        repair = result.parsed.get("repair_instructions", "品質を改善してください")

    return repair, violations


# ==========================================
# Phase 完了時の Contract 検証
# ==========================================
def verify_phase_contract(
    contract: dict,
    phase: dict,
    work_dir: Path,
    steps: list[dict],
) -> list[ContractViolation]:
    """
    phase 完了時に contract と outputs を検証する。
    Returns: 検出された violations のリスト（空なら合格）
    """
    violations = []

    # outputs 宣言のファイル存在チェック
    violations.extend(detect_outputs(phase, work_dir))

    # forbidden_patterns（checker がないフェーズの場合のみ）
    has_checker = any(s.get("role") == "checker" for s in steps)
    if not has_checker and contract.get("forbidden_patterns"):
        violations.extend(detect_forbidden_patterns(contract, work_dir, phase))

    return violations
