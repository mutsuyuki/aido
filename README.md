# aido - AI Development Orchestrator

AIエージェント（Claude / Gemini / Codex）をオーケストレーションして、ソフトウェア開発パイプラインを自動実行するフレームワーク。

開発プロセスをスクリプトで強制することで、途中離脱や品質のばらつきを抑え、再現性のある開発フローを実現する。

## 特徴

- **設定駆動**: YAML でフェーズ・ステップ・ロールを定義。コードを書かずにパイプラインを組み替え可能
- **セッション管理**: ロールごとにAIのコンテキストを分離・継続。Coder は文脈を引き継ぎ、Reviewer は毎回フレッシュな視点でレビュー
- **マルチバックエンド**: Claude / Gemini / Codex の CLI を統一インターフェースで呼び出し。ロールごとに異なるバックエンド・モデルを割り当て可能
- **Leader ロール**: パイプライン全体を監督し、進捗に応じてフェーズの追加・スキップ・リトライを判断
- **プロジェクト分離**: フレームワーク本体とプロジェクト固有設定を完全に分離。使い回しが効く
- **ignore ファイル自動配布**: aido ルートの `.geminiignore` / `.claudeignore` をパイプライン実行時に work_dir へ自動コピー。node_modules 等の巨大ディレクトリによるトークン上限超過を防止
- **成果物記録**: 各ステップの出力を `runs/` に自動保存。開発ログとして後から解析可能。前フェーズの成果物は次フェーズに自動注入
- **リトライ制御**: 指数バックオフ（30s, 60s, 120s, ...）と progressive confidence threshold でレビューワーの無限ループを防止

## クイックスタート

```bash
# 1. プロジェクトを初期化（テンプレートをコピー）
python main.py init workspace/my-app/settings

# 2. 仕様を記述
#    workspace/my-app/settings/context/app_spec.md を編集
#    workspace/my-app/settings/project.yaml の name, work_dir, checks を編集

# 3. dry-run で計画を確認
python main.py run workspace/my-app/settings/project.yaml --dry-run

# 4. 実行
python main.py run workspace/my-app/settings/project.yaml --auto-approve

# 5. 改善サイクル（既存アプリに機能追加・バグ修正）
python main.py run workspace/my-app/settings/improve.yaml --auto-approve \
  --input "気の利いた機能を1つ追加して"
python main.py run workspace/my-app/settings/fix.yaml --auto-approve \
  --input "ログインで500エラーが出る"
```

## ディレクトリ構成

```
(repo root)/
├── Dockerfile                   # 共通環境（これだけで使える）
├── DockerRun.sh                 # コンテナ起動スクリプト
├── main.py                      # CLI エントリポイント（init / run）
├── src/                         # フレームワーク内部モジュール
├── prompts/                     # デフォルトプロンプト（7ロール分）
│   ├── coder_system.md
│   ├── reviewer_system.md
│   └── ...
├── examples/                    # プロジェクトテンプレート（init でコピーされる）
│   ├── project.yaml             # 新規開発用
│   ├── improve.yaml             # 改善サイクル用
│   ├── fix.yaml                 # バグ修正用
│   ├── context/app_spec.md      # 仕様書テンプレート
│   └── prompts/README.md        # プロンプト上書きの説明
└── workspace/                   # gitignored（ランタイム用）
    └── my-app/                  # プロジェクト単位のディレクトリ
        ├── settings/            # aido パイプライン設定
        │   ├── project.yaml
        │   ├── improve.yaml
        │   ├── fix.yaml
        │   ├── context/app_spec.md
        │   └── prompts/
        ├── work/                # 生成されたアプリ（独立git repo可）
        ├── runs/                # 実行ログ・成果物（自動生成）
        │   └── 20260325_143020/
        │       ├── phase_01/attempt_01/
        │       │   ├── log.json
        │       │   └── reviewer_review.json
        │       └── pipeline_summary.json
        └── docker/              # プロジェクト固有 Dockerfile（任意）
            └── Dockerfile
```

## プロジェクト設定 (project.yaml)

```yaml
project:
  name: "My App"
  work_dir: "../work"                # このファイルからの相対パス

generation:
  default_backend: "claude"      # claude / gemini / codex
  default_model: "sonnet"
  default_timeout_sec: 600       # AI呼び出しのタイムアウト（秒）
  default_permission_mode: "dangerously-skip-permissions"  # CLI権限モード
  max_retries: 3                 # 各フェーズの最大リトライ回数
  stop_on_failure: true          # フェーズ失敗時にパイプラインを停止
  use_leader: true
  confidence_threshold: 80       # reviewer の confidence フィルタ閾値
  confidence_step: 5             # リトライごとの閾値増分（80→85→90→...→100）

roles:
  leader:
    model: "opus"                # リーダーだけ高性能モデル
  reviewer:
    session: "stateless"         # 毎回新規セッション（バイアス排除）

checks:
  commands:
    - "flutter analyze"
    - "dart format --output=none --set-exit-if-changed ."

phases:
  - id: "phase_01"
    title: "データモデル実装"
    tasks:
      - "models/ にデータモデルを作成"
    steps:
      - role: coder
        action: implement
      - role: checker
        action: run_checks
      - role: reviewer
        action: review
```

## ロール一覧

| ロール | セッション | 用途 |
|---|---|---|
| **coder** | 継続 | コード実装。修正指示を受けて改善を積み重ねる |
| **reviewer** | 毎回新規 | コードレビュー。confidence スコアで重要な問題のみ報告 |
| **tester** | 継続 | テストコード作成 |
| **explorer** | 継続 | 既存コードの調査（read-only） |
| **designer** | 継続 | 設計書・スケルトン作成 |
| **documenter** | 継続 | ドキュメント生成 |
| **leader** | 継続 | パイプライン監督。進捗管理とフロー制御 |
| **checker** | - | 機械検査（非AI。lint, compile, test 実行） |
| **human** | - | ユーザー確認ポイント（非AI） |

## ステップの書き方

### 基本

```yaml
steps:
  - role: coder
    action: implement
  - role: checker
    action: run_checks
  - role: reviewer
    action: review
```

### ユーザー確認を挟む

```yaml
steps:
  - role: designer
    action: design
  - role: human             # ユーザーが設計を確認
    action: approve
  - role: coder
    action: implement
```

`--auto-approve` フラグで自動承認可能。

### 観点別レビュー（同じロールに異なるプロンプト）

```yaml
steps:
  - role: reviewer
    action: review
    prompt: reviewer_simplicity.md    # prompts/ 内のファイル名
  - role: reviewer
    action: review
    prompt: reviewer_correctness.md
```

### 人間がAIの代わりに作業（human_override）

```yaml
steps:
  - role: coder
    action: implement
    human_override: true      # AIの代わりに人間が実装
  - role: checker
    action: run_checks
```

`human_override: true` を付けると、そのステップでは AI を呼ばず、人間に作業を委ねる。作業完了後に Enter で続行。`--auto-approve` 時はスキップされる。

### 事前調査（read-only）

```yaml
steps:
  - role: explorer
    action: explore
  - role: coder
    action: implement
```

## プロンプトの frontmatter

プロンプトファイル (.md) に YAML frontmatter を記述すると、ロール設定として自動マージされる。

```markdown
---
name: my-reviewer
model: opus
session: stateless
permission_mode: plan
---

あなたはコードレビュアーです。
（以下プロンプト本文）
```

設定の優先順位（後勝ち）:
1. フレームワークのハードコードデフォルト
2. `generation` セクションのデフォルト値
3. プロンプトファイルの frontmatter
4. `roles` セクション（project.yaml 内、最優先）

## セッション管理

同じロール名のステップは同一セッション（AIの会話コンテキスト）を引き継ぐ。

```
Phase 1: coder/implement  ─┐
Phase 2: coder/implement  ─┤ 同じセッション（文脈を共有）
Phase 3: coder/implement  ─┘

Phase 1: reviewer/review  ── 毎回新規セッション（stateless）
Phase 2: reviewer/review  ── 毎回新規セッション
```

**失敗時の自動リセット**: AI 呼び出しが失敗（exit≠0）した場合、そのセッションは自動的に破棄される。次回呼び出し時は新規セッションで開始される。これにより、トークン上限超過等で汚染されたセッションが後続の呼び出しに影響することを防ぐ。

セッション管理は `ai_backend.py` の `SessionManager` が各CLI の差異を吸収する:

| CLI | セッション作成 | セッション継続 | ファイル参照 |
|---|---|---|---|
| Claude | `--session-id <uuid>` | `--resume <uuid>` | cwd 内を自動探索 |
| Gemini | 自動生成 → `--list-sessions` でUUID取得 | `--resume <uuid>` | cwd 内を自力探索（ディレクトリの一括プリロードはしない） |
| Codex | 自動生成 → セッションファイルからUUID取得 | `exec resume <uuid>` | cwd 内を自動探索 |

全バックエンドとも `cwd=work_dir` で実行される。Gemini は以前 `@work_dir` でディレクトリ全体をコンテキストにプリロードしていたが、node_modules 等を含む場合にトークン上限を超過するため、必要なファイルを自力で読み込む方式に変更した。

## Leader の判断フロー

```
パイプライン開始
│
├── Leader: 計画レビュー → abort なら中止
│
├── Phase 1 実行
│   └── Leader: checkpoint
│       ├── "continue"   → 次の Phase へ
│       ├── "retry"      → 同じ Phase を再実行
│       ├── "skip"       → 次の Phase をスキップ
│       ├── "add_phase"  → 新しい Phase を動的に挿入
│       └── "abort"      → パイプライン中止
│
├── Phase 2 実行
│   └── Leader: checkpoint → ...
│
└── Leader: 最終評価
```

## リトライ制御

### 指数バックオフ

リトライ間に待機時間を挟む（API レート制限対策）:
- 1回目失敗後: 30秒
- 2回目失敗後: 60秒
- 3回目失敗後: 120秒
- ...（`30 * 2^(attempt-1)` 秒）

### Progressive Confidence Threshold

`confidence_step` を設定すると、リトライ回数に応じて reviewer の confidence 閾値が上がる。これにより、初回は厳しくレビューしつつ、リトライが重なると軽微な指摘を自動除外し、レビューワーの無限ループを防止する。

```yaml
generation:
  confidence_threshold: 80    # 初回の閾値
  confidence_step: 5          # リトライごとの増分
  max_retries: 5
```

上記の場合: 80 → 85 → 90 → 95 → 100

`confidence_step: 0` にすれば閾値は固定（従来動作）。

## Confidence スコア（Reviewer）

Reviewer は各 issue に confidence (0-100) を付ける。閾値未満の issue は自動除外される。

```json
{
  "pass": false,
  "issues": [
    {"description": "SQL injection", "confidence": 95, "file": "api.py:42", "fix": "パラメータ化クエリを使用"},
    {"description": "変数名が短い", "confidence": 30, "file": "utils.py:10", "fix": "..."}
  ]
}
```

上記の場合、confidence 30 の issue は除外され、95 の issue のみが修正指示として coder に渡される。

## 成果物記録（runs/）

パイプライン実行時、各ステップの出力が `workspace/<project>/runs/<timestamp>/` に自動保存される。

```
workspace/my-app/runs/20260325_143020/
├── phase_01/
│   └── attempt_01/
│       ├── log.json                    # ステップごとの成否・時間
│       ├── explorer_explore.md         # Explorer の調査結果
│       └── reviewer_review.json        # レビュー結果（confidence付き）
├── phase_02/
│   └── attempt_01/
│       ├── designer_design.md          # 設計書
│       └── checker_run_checks_stdout.txt  # lint/test の出力
└── pipeline_summary.json              # 全体の結果サマリー
```

### 用途

- **開発ログ**: どのフェーズで何が指摘され、何回リトライしたかを追跡
- **前フェーズの成果物注入**: 完了済みフェーズの出力は、後続フェーズのプロンプトに `## 前フェーズの成果物` として自動注入される
- **事後分析**: `pipeline_summary.json` から全体の成否・問題点を人や AI が解析可能

### checker の構造化フィードバック

checker が失敗した場合、stdout/stderr がそのまま保存され、リトライ時の修正指示にも含まれる。Coder はエラーメッセージを直接参照して修正できる。

## CLI オプション

```bash
# サブコマンド
python main.py init <dest>           # プロジェクト初期化（examples/ をコピー）
python main.py run <config.yaml>     # パイプライン実行

# run のオプション
--dry-run                            # 実行せずに計画を表示
--auto-approve                       # human ステップを自動承認
--only phase_01 phase_02             # 特定フェーズのみ実行
--input "修正内容"                    # 実行時の動的指示を注入
--input-file issues.md               # ファイルから動的指示を読み込み
--resume-run runs/20260325_185807    # 前回の成果物を引き継ぐ
```

## フェーズ固有の検査

グローバルの `checks:` に加え、フェーズ単位で検査コマンドを上書きできる。

```yaml
phases:
  - id: "test_phase"
    checks:                          # このフェーズだけ pytest を実行
      commands:
        - "cd /path/to/app && python -m pytest tests/ -x"
    steps:
      - role: tester
        action: write_tests
      - role: checker
        action: run_checks           # ↑ のフェーズ固有 checks が使われる
```

## Docker 環境

```bash
# 共通環境でコンテナ起動
./DockerRun.sh

# プロジェクト固有環境で起動（workspace/<project>/docker/Dockerfile を使用）
./DockerRun.sh gemma-chat
./DockerRun.sh news-feeling
```

共通の `Dockerfile` には Python, Node.js, LLM CLI ツール等が含まれる。Flutter SDK 等のプロジェクト固有の依存は `workspace/<project>/docker/Dockerfile` で `FROM` 継承して追加する。

## 依存関係

- Python 3.12+
- PyYAML (`pip install pyyaml`)
- AI CLI ツール（使用するバックエンドに応じて）:
  - `claude` (Claude Code CLI)
  - `gemini` (Gemini CLI)
  - `codex` (Codex CLI)
