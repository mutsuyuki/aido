#!/usr/bin/env python3
"""
aido - AI Development Orchestrator

使い方:
  # プロジェクトの初期化（テンプレートをコピー）
  python main.py init workspace/my-app/settings

  # パイプライン実行
  python main.py run workspace/my-app/settings/project.yaml --auto-approve
  python main.py run workspace/my-app/settings/project.yaml --dry-run
  python main.py run workspace/my-app/settings/project.yaml --only phase_01 phase_02

  # ダッシュボード
  python main.py dashboard workspace/news-feeling/

  # 改善・修正サイクル
  python main.py run workspace/my-app/settings/improve.yaml --auto-approve --input "機能を追加して"
  python main.py run workspace/my-app/settings/fix.yaml --auto-approve --input "バグを直して"
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from src.config import load_project_config
from src.pipeline import run_pipeline
from src.dashboard import start_dashboard, stop_dashboard

AIDO_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = AIDO_DIR / "examples"


def cmd_init(args: argparse.Namespace) -> None:
    """examples/ をコピーしてプロジェクトを初期化する"""
    dest = Path(args.dest).resolve()

    if dest.exists() and any(dest.iterdir()):
        print(f"エラー: {dest} は空ではありません。空のディレクトリまたは新しいパスを指定してください。")
        raise SystemExit(1)

    shutil.copytree(EXAMPLES_DIR, dest, dirs_exist_ok=True)

    # prompts/README.md は説明用なのでそのまま残す
    print(f"プロジェクトを初期化しました: {dest}")
    print()
    print("次のステップ:")
    print(f"  1. {dest}/context/app_spec.md にアプリ仕様を記述")
    print(f"  2. {dest}/project.yaml の project.name と work_dir を編集")
    print(f"  3. checks.commands にプロジェクトに合った検査コマンドを設定")
    print(f"  4. 実行: python {AIDO_DIR}/main.py run {dest}/project.yaml --dry-run")


def cmd_dashboard(args: argparse.Namespace) -> None:
    """ダッシュボードを起動する"""
    project_dir = Path(args.project_dir).resolve()

    # settings/ があるディレクトリを探す（直接指定 or 親をたどる）
    if not (project_dir / "settings").exists():
        # settings/xxx.yaml を直接指定した場合 → 2つ上がproject
        if project_dir.name == "settings" or (project_dir / ".." / "settings").resolve().exists():
            project_dir = project_dir.parent
        elif project_dir.suffix in (".yaml", ".yml"):
            project_dir = project_dir.parent.parent

    start_dashboard(
        project_dir=project_dir,
        port=args.port,
        open_browser=not args.no_browser,
        blocking=True,
    )


def cmd_run(args: argparse.Namespace) -> None:
    """パイプラインを実行する"""
    # --input と --input-file の解決
    user_input = args.user_input or ""
    if args.user_input_file:
        user_input = Path(args.user_input_file).read_text(encoding="utf-8")

    config = load_project_config(Path(args.config))

    if args.only:
        config["phases"] = [p for p in config["phases"] if p["id"] in args.only]

    project = config["project"]
    phases = config["phases"]
    gen = config.get("generation", {})

    print(f"=== aido - AI Development Orchestrator ===")
    print(f"  Project: {project['name']}")
    print(f"  Work dir: {project['work_dir']}")
    print(f"  Phases: {[p['id'] for p in phases]}")
    print(f"  Leader: {'ON' if gen.get('use_leader') else 'OFF'}")

    if args.dry_run:
        print(f"\n[dry-run] 実行計画:")
        for p in phases:
            deps = p.get("dependencies", [])
            steps = [f"{s['role']}/{s.get('action', s['role'])}" for s in p.get("steps", [])]
            print(f"  {p['id']}: {p['title']}")
            print(f"    Steps: {' -> '.join(steps)}")
            if deps:
                print(f"    Deps: {deps}")
        return

    run_pipeline(
        config,
        auto_approve=args.auto_approve,
        user_input=user_input,
        resume_run=args.resume_run,
    )


def main():
    parser = argparse.ArgumentParser(description="aido - AI Development Orchestrator")
    subparsers = parser.add_subparsers(dest="command")

    # init サブコマンド
    init_parser = subparsers.add_parser("init", help="プロジェクトを初期化（テンプレートをコピー）")
    init_parser.add_argument("dest", help="プロジェクトフォルダのパス（例: workspace/my-app/settings）")

    # run サブコマンド
    run_parser = subparsers.add_parser("run", help="パイプラインを実行")
    run_parser.add_argument("config", help="プロジェクト設定YAMLのパス")
    run_parser.add_argument("--only", nargs="*", help="特定のPhaseのみ実行")
    run_parser.add_argument("--dry-run", action="store_true", help="実行せずに計画を表示")
    run_parser.add_argument("--auto-approve", action="store_true", help="human_approvalステップを自動承認")
    run_parser.add_argument("--input", dest="user_input", help="実行時の動的指示（例: 修正内容やテーマ）")
    run_parser.add_argument("--input-file", dest="user_input_file", help="動的指示をファイルから読み込む")
    run_parser.add_argument("--resume-run", dest="resume_run", help="前回の runs/ ディレクトリから成果物を引き継ぐ")

    # dashboard サブコマンド
    dash_parser = subparsers.add_parser("dashboard", help="ダッシュボードを起動")
    dash_parser.add_argument("project_dir", help="プロジェクトディレクトリ（例: workspace/news-feeling/）")
    dash_parser.add_argument("--port", type=int, default=8420, help="ポート番号（デフォルト: 8420）")
    dash_parser.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")

    # 後方互換: サブコマンドなしで yaml を直接指定した場合も run として扱う
    args, remaining = parser.parse_known_args()

    if args.command is None:
        # サブコマンドなし → 第1引数が yaml なら run として扱う
        if remaining or (len(remaining) == 0 and hasattr(args, 'dest')):
            run_args = run_parser.parse_args(remaining)
            cmd_run(run_args)
        else:
            parser.print_help()
        return

    if args.command == "init":
        cmd_init(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)


if __name__ == "__main__":
    main()
