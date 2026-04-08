"""
設定の読み込みとパス解決

プロジェクト固有設定とフレームワークデフォルトのマージを担当する。

プロンプト解決の優先順位:
  1. プロジェクトフォルダの prompts/ にあればそれを使う
  2. なければ aido/prompts/ のデフォルトを使う

プロンプトファイルは YAML frontmatter を持てる:
  ---
  name: code-reviewer
  model: sonnet
  session: stateless
  permission_mode: plan
  ---
  （ここにシステムプロンプト本文）

frontmatter の設定は project.yaml の roles 設定にマージされる。
project.yaml 側の設定が優先（明示的に上書きできる）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

AIDO_DIR = Path(__file__).resolve().parent.parent  # src/ の親 = リポジトリルート
DEFAULT_PROMPTS_DIR = AIDO_DIR / "prompts"

# frontmatter で指定可能なキー
_FRONTMATTER_KEYS = {"name", "model", "session", "permission_mode", "backend", "timeout_sec"}


# ==========================================
# frontmatter パース
# ==========================================
def parse_prompt_file(content: str) -> tuple[dict, str]:
    """
    プロンプトファイルを frontmatter とボディに分離する。
    frontmatter がなければ空 dict と全文を返す（後方互換）。
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}, content

    try:
        frontmatter = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, content

    body = content[match.end():]
    # frontmatter から認識するキーのみ抽出
    filtered = {k: v for k, v in frontmatter.items() if k in _FRONTMATTER_KEYS}
    return filtered, body


# ==========================================
# プロジェクト設定
# ==========================================
def load_project_config(project_yaml_path: Path) -> dict:
    """プロジェクトYAMLを読み込み、パスを解決した設定を返す"""
    project_yaml_path = project_yaml_path.resolve()
    project_dir = project_yaml_path.parent

    with open(project_yaml_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    project = config["project"]

    # work_dir をプロジェクトフォルダからの相対パスとして解決
    work_dir = Path(project["work_dir"])
    if not work_dir.is_absolute():
        work_dir = (project_dir / work_dir).resolve()
    project["work_dir"] = str(work_dir)

    # プロジェクトフォルダのパスを保持
    config["_project_dir"] = str(project_dir)
    config["_project_yaml"] = str(project_yaml_path)

    return config


# ==========================================
# プロンプト解決
# ==========================================
def _find_prompt_path(prompt_filename: str, project_dir: Path) -> Path:
    """プロンプトファイルのパスを解決する"""
    project_prompt = project_dir / "prompts" / prompt_filename
    if project_prompt.exists():
        return project_prompt

    default_prompt = DEFAULT_PROMPTS_DIR / prompt_filename
    if default_prompt.exists():
        return default_prompt

    raise FileNotFoundError(
        f"Prompt not found: {prompt_filename}\n"
        f"  Searched: {project_prompt}\n"
        f"  Searched: {default_prompt}"
    )


def resolve_prompt(prompt_filename: str, project_dir: Path) -> str:
    """プロンプトファイルのボディ（frontmatter除外）を返す"""
    path = _find_prompt_path(prompt_filename, project_dir)
    content = path.read_text(encoding="utf-8")
    _, body = parse_prompt_file(content)
    return body


def resolve_prompt_with_meta(prompt_filename: str, project_dir: Path) -> tuple[dict, str]:
    """プロンプトファイルの frontmatter とボディを両方返す"""
    path = _find_prompt_path(prompt_filename, project_dir)
    content = path.read_text(encoding="utf-8")
    return parse_prompt_file(content)


# ==========================================
# コンテキスト
# ==========================================
def resolve_context_files(project_dir: Path) -> str:
    """プロジェクトの context/ フォルダ内のファイルを全て読み込んで結合する"""
    context_dir = project_dir / "context"
    if not context_dir.exists():
        return ""

    parts = []
    for f in sorted(context_dir.iterdir()):
        if f.is_file() and f.suffix in (".md", ".txt", ".yaml", ".yml"):
            parts.append(f"=== {f.name} ===\n{f.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


# ==========================================
# チェック設定
# ==========================================
def get_check_config(config: dict, project_dir: Path) -> dict:
    """チェック設定を解決する。moduleパスはプロジェクトフォルダ基準。"""
    checks = config.get("checks", {})
    if "module" in checks:
        checks["_project_dir"] = str(project_dir)
    return checks


# ==========================================
# ロール設定（frontmatter マージ対応）
# ==========================================
def get_role_config(config: dict, project_dir: Path = None) -> dict:
    """
    ロール設定を取得する。マージ優先順位（後勝ち）:
      1. ハードコードデフォルト
      2. generation セクションのデフォルト
      3. プロンプトファイルの frontmatter
      4. project.yaml の roles セクション（最優先）
    """
    defaults = {
        "coder":      {"session": "continue",  "prompt": "coder_system.md"},
        "tester":     {"session": "continue",  "prompt": "tester_system.md"},
        "reviewer":   {"session": "stateless", "prompt": "reviewer_system.md"},
        "designer":   {"session": "continue",  "prompt": "designer_system.md"},
        "documenter": {"session": "continue",  "prompt": "documenter_system.md"},
        "explorer":   {"session": "continue",  "prompt": "explorer_system.md",
                       "permission_mode": "plan"},
        "leader":     {"session": "continue",  "prompt": "leader_system.md"},
    }

    gen = config.get("generation", {})
    roles_config = config.get("roles", {})

    result = {}
    for role_name, role_defaults in defaults.items():
        # Layer 1+2: ハードコードデフォルト + generation デフォルト
        merged = {
            "backend": gen.get("default_backend", "claude"),
            "model": gen.get("default_model", ""),
            "timeout_sec": gen.get("default_timeout_sec", 300),
            "permission_mode": gen.get("default_permission_mode", "bypassPermissions"),
            "session": role_defaults["session"],
            "prompt": role_defaults["prompt"],
        }

        # Layer 3: frontmatter（プロンプトファイルが見つかれば）
        if project_dir:
            try:
                meta, _ = resolve_prompt_with_meta(merged["prompt"], project_dir)
                for k, v in meta.items():
                    if k in merged:
                        merged[k] = v
            except FileNotFoundError:
                pass

        # Layer 4: project.yaml の roles セクション（最優先）
        user_cfg = roles_config.get(role_name, {})
        for k, v in user_cfg.items():
            if k in merged:
                merged[k] = v

        result[role_name] = merged

    return result
