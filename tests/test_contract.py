"""
aido 回帰テスト — Contract / Failure Taxonomy / Pipeline
"""
import tempfile
import inspect
from dataclasses import asdict
from pathlib import Path

from src.contract import (
    get_failure_taxonomy,
    detect_forbidden_patterns,
    detect_outputs,
    classify_failure_type,
    resolve_strategy,
    build_checker_repair,
    build_reviewer_repair,
    verify_phase_contract,
)
from src.models import ContractViolation, StepResult, AttemptLog
from src.steps import filter_review_by_confidence, execute_step
from src.pipeline import (
    execute_phase,
    _state_root,
    _reset_state_dir,
    _promote_phase_to_state,
    _build_state_listing,
    _promote_from_resume,
)
from src.config import load_project_config


# ==========================================
# Failure Taxonomy 解決
# ==========================================

def test_failure_taxonomy_none_when_unspecified():
    assert get_failure_taxonomy({}, {}) is None


def test_failure_taxonomy_phase_overrides_generation():
    gen = {"failure_taxonomy": {"checker_error": "retry_coder", "timeout": "abort"}}
    phase = {"failure_taxonomy": {"timeout": "session_reset_and_retry"}}
    result = get_failure_taxonomy(phase, gen)
    assert result == {"checker_error": "retry_coder", "timeout": "session_reset_and_retry"}


# ==========================================
# Contract 違反検出
# ==========================================

def test_detect_empty_when_no_contract():
    assert detect_forbidden_patterns({}, Path("/tmp"), {}) == []
    assert detect_outputs({}, Path("/tmp")) == []


def test_detect_forbidden_patterns():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "hello.py").write_text("x = 1  # TODO: fix this\ny = 2\n")
        contract = {"forbidden_patterns": ["TODO:"]}
        phase = {"outputs": ["hello.py"]}
        vs = detect_forbidden_patterns(contract, td, phase)
        assert len(vs) == 1
        assert vs[0].fact == "forbidden_pattern"
        assert vs[0].pattern == "TODO:"
        assert "hello.py:1" in vs[0].file


def test_detect_outputs():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "DESIGN.md").write_text("# Design")
        phase = {"outputs": ["DESIGN.md", "MISSING.md"]}
        vs = detect_outputs(phase, td)
        assert len(vs) == 1
        assert vs[0].pattern == "MISSING.md"


# ==========================================
# failure_type 分類
# ==========================================

def test_classify_checker_error():
    assert classify_failure_type([ContractViolation(fact="checker_nonzero")]) == "checker_error"


def test_classify_reviewer_rejection():
    assert classify_failure_type([ContractViolation(fact="confidence_below_min")]) == "reviewer_rejection"


def test_classify_missing_artifact():
    assert classify_failure_type([ContractViolation(fact="required_file_missing")]) == "missing_artifact"


def test_classify_timeout():
    assert classify_failure_type([ContractViolation(fact="timeout")]) == "timeout"


def test_classify_priority_missing_over_checker():
    vs = [ContractViolation(fact="checker_nonzero"), ContractViolation(fact="required_file_missing")]
    assert classify_failure_type(vs) == "missing_artifact"


def test_classify_priority_timeout_over_checker():
    vs = [ContractViolation(fact="checker_nonzero"), ContractViolation(fact="timeout")]
    assert classify_failure_type(vs) == "timeout"


# ==========================================
# Strategy 解決
# ==========================================

def test_resolve_strategy_none_taxonomy():
    assert resolve_strategy("checker_error", None) == ""


def test_resolve_strategy_empty_failure_type():
    assert resolve_strategy("", {"checker_error": "retry_coder"}) == ""


def test_resolve_strategy_match():
    assert resolve_strategy("checker_error", {"checker_error": "retry_coder"}) == "retry_coder"
    assert resolve_strategy("missing_artifact", {"missing_artifact": "abort"}) == "abort"


# ==========================================
# Repair 構築
# ==========================================

def test_build_checker_repair():
    sr = StepResult(
        role="checker", action="run_checks", success=False,
        failures=["[flutter analyze] exit=1: error"],
        checker_stdout="Error: unused import", checker_stderr="",
    )
    repair, vs = build_checker_repair(sr, {}, Path("/tmp"), {})
    assert "flutter analyze" in repair
    assert any(v.fact == "checker_nonzero" for v in vs)


def test_build_reviewer_repair():
    sr = StepResult(
        role="reviewer", action="review", success=False,
        parsed={
            "pass": False,
            "issues": [{"description": "SQL injection", "confidence": 95, "file": "api.py:42", "fix": "use params"}],
        },
    )
    repair, vs = build_reviewer_repair(sr, {"reviewer_confidence_min": 80})
    assert "SQL injection" in repair
    assert any(v.fact == "confidence_below_min" for v in vs)
    assert "threshold: 80" in vs[0].detail


# ==========================================
# Phase Contract 検証
# ==========================================

def test_verify_phase_contract_detects_missing_output():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "DESIGN.md").write_text("# Design")
        phase = {"outputs": ["DESIGN.md", "MISSING.md"]}
        vs = verify_phase_contract({}, phase, td, [])
        assert len(vs) == 1
        assert vs[0].pattern == "MISSING.md"


def test_verify_phase_contract_all_outputs_present():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "DESIGN.md").write_text("# Design")
        phase = {"outputs": ["DESIGN.md"]}
        vs = verify_phase_contract({}, phase, td, [])
        assert len(vs) == 0


# ==========================================
# AttemptLog シリアライズ
# ==========================================

def test_attempt_log_serialization():
    log = AttemptLog(
        attempt=1,
        contract_violations=[ContractViolation(fact="checker_nonzero", detail="exit 1")],
        failure_type="checker_error",
        strategy_applied="retry_coder",
    )
    d = asdict(log)
    assert d["failure_type"] == "checker_error"
    assert d["strategy_applied"] == "retry_coder"
    assert len(d["contract_violations"]) == 1
    assert d["contract_violations"][0]["fact"] == "checker_nonzero"


# ==========================================
# Confidence フィルタリング
# ==========================================

def test_confidence_filter_high_keeps_fail():
    parsed = {"pass": False, "issues": [{"description": "test", "confidence": 90}]}
    filtered = filter_review_by_confidence(parsed, 80)
    assert filtered["pass"] is False


def test_confidence_filter_low_auto_passes():
    parsed = {"pass": False, "issues": [{"description": "test", "confidence": 50}]}
    filtered = filter_review_by_confidence(parsed, 80)
    assert filtered["pass"] is True


# ==========================================
# 既存プロジェクト後方互換
# ==========================================

def test_existing_projects_load_without_error():
    """workspace 内の全プロジェクトの project.yaml が正常にロードできること"""
    workspace = Path("workspace")
    if not workspace.exists():
        return
    for proj_dir in sorted(workspace.iterdir()):
        yaml_path = proj_dir / "settings" / "project.yaml"
        if yaml_path.exists():
            cfg = load_project_config(yaml_path)
            assert "project" in cfg, f"{proj_dir.name}: project section missing"
            assert "phases" in cfg, f"{proj_dir.name}: phases section missing"


def test_examples_project_has_new_features():
    cfg = load_project_config(Path("examples/project.yaml"))
    gen = cfg.get("generation", {})
    ft = get_failure_taxonomy({}, gen)
    has_contract = any(p.get("contract") for p in cfg.get("phases", []))
    has_outputs = any(p.get("outputs") for p in cfg.get("phases", []))
    assert ft is not None
    assert has_contract
    assert has_outputs


# ==========================================
# Pipeline / Steps コード構造
# ==========================================

def test_step_level_override_exists():
    src = inspect.getsource(execute_step)
    assert "step_backend" in src
    assert "step_model" in src


def test_pass_on_max_retries_exists():
    src = inspect.getsource(execute_phase)
    assert "pass_on_max_retries" in src


def test_phase_level_overrides_exist():
    src = inspect.getsource(execute_phase)
    assert "effective_max_retries" in src
    assert "effective_confidence_step" in src


# ==========================================
# Canonical state path (.aido/state/)
# ==========================================

def _make_run_dir_with_attempt(run_dir: Path, phase_id: str, attempt: int, files: dict[str, str]) -> None:
    """テスト用に runs/<phase>/attempt_NN/ にファイルを作る"""
    d = run_dir / phase_id / f"attempt_{attempt:02d}"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (d / name).write_text(content, encoding="utf-8")


def test_reset_state_dir_creates_clean_root():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td) / "work"
        work_dir.mkdir()
        # 古い state を仕込む
        old = _state_root(work_dir) / "phase_old"
        old.mkdir(parents=True)
        (old / "stale.md").write_text("old", encoding="utf-8")

        _reset_state_dir(work_dir)

        assert _state_root(work_dir).exists()
        assert not (_state_root(work_dir) / "phase_old").exists()
        # gitignore が作られる
        assert (work_dir / ".aido" / ".gitignore").read_text() == "*\n"


def test_promote_phase_to_state_creates_symlink():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        work_dir = td / "work"
        run_dir = td / "runs" / "20260410_120000"
        work_dir.mkdir(parents=True)
        _reset_state_dir(work_dir)

        _make_run_dir_with_attempt(
            run_dir, "phase_01", 2,
            {"designer_design.md": "DESIGN BODY", "log.json": "{}"},
        )

        _promote_phase_to_state(work_dir, "phase_01", run_dir, 2)

        link = _state_root(work_dir) / "phase_01"
        assert link.is_symlink()
        # symlink 越しに内容が読める
        assert (link / "designer_design.md").read_text() == "DESIGN BODY"


def test_promote_phase_overwrites_previous_symlink():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        work_dir = td / "work"
        run_dir = td / "runs" / "r1"
        work_dir.mkdir(parents=True)
        _reset_state_dir(work_dir)

        _make_run_dir_with_attempt(run_dir, "p", 1, {"a.md": "v1"})
        _promote_phase_to_state(work_dir, "p", run_dir, 1)

        _make_run_dir_with_attempt(run_dir, "p", 2, {"a.md": "v2"})
        _promote_phase_to_state(work_dir, "p", run_dir, 2)

        assert (_state_root(work_dir) / "p" / "a.md").read_text() == "v2"


def test_build_state_listing_excludes_log_json():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        work_dir = td / "work"
        run_dir = td / "runs" / "r"
        work_dir.mkdir(parents=True)
        _reset_state_dir(work_dir)

        _make_run_dir_with_attempt(
            run_dir, "phase_01", 1,
            {
                "designer_design.md": "x" * 100,
                "log.json": "should_not_appear",
                "reviewer_review.json": '{"pass": true}',
            },
        )
        _promote_phase_to_state(work_dir, "phase_01", run_dir, 1)

        listing = _build_state_listing(work_dir)
        assert ".aido/state/phase_01/designer_design.md" in listing
        assert ".aido/state/phase_01/reviewer_review.json" in listing
        assert "log.json" not in listing


def test_build_state_listing_empty_when_no_promotions():
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td) / "work"
        work_dir.mkdir()
        _reset_state_dir(work_dir)
        assert _build_state_listing(work_dir) == ""


def test_promote_from_resume_picks_last_attempt():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        work_dir = td / "work"
        resume_dir = td / "runs" / "old"
        work_dir.mkdir(parents=True)
        _reset_state_dir(work_dir)

        _make_run_dir_with_attempt(resume_dir, "p1", 1, {"x.md": "old1"})
        _make_run_dir_with_attempt(resume_dir, "p1", 2, {"x.md": "old2"})
        _make_run_dir_with_attempt(resume_dir, "p2", 1, {"y.md": "yval"})

        _promote_from_resume(work_dir, resume_dir, [{"id": "p1"}, {"id": "p2"}])

        assert (_state_root(work_dir) / "p1" / "x.md").read_text() == "old2"
        assert (_state_root(work_dir) / "p2" / "y.md").read_text() == "yval"
