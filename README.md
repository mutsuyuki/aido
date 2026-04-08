# aido - AI Development Orchestrator

AIエージェント（Claude / Gemini / Codex）をオーケストレーションして、ソフトウェア開発パイプラインを自動実行するフレームワーク。

開発プロセスをスクリプトで強制することで、途中離脱や品質のばらつきを抑え、再現性のある開発フローを実現する。

## 特徴

- **設定駆動**: YAML でフェーズ・ステップ・ロールを定義。コードを書かずにパイプラインを組み替え可能
- **セッション管理**: ロールごとにAIのコンテキストを分離・継続。Coder は文脈を引き継ぎ、Reviewer は毎回フレッシュな視点でレビュー
- **マルチバックエンド**: Claude / Gemini / Codex の CLI を統一インターフェースで呼び出し。ロールごとに異なるバックエンド・モデルを割り当て可能
- **Leader ロール**: パイプライン全体を監督し、進捗に応じてフェーズの追加・スキップ・リトライを判断
- **Contract / Failure Taxonomy**: フェーズの合格条件と失敗の型分け・回復戦略を YAML で宣言的に定義
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
├── main.py                      # CLI エントリポイント（init / run / dashboard）
├── src/                         # フレームワーク内部モジュール
│   ├── pipeline.py              #   パイプライン実行・リトライ制御
│   ├── contract.py              #   Contract 検証・Failure Taxonomy
│   ├── steps.py                 #   ステップ実行・プロンプト組立
│   ├── ai_backend.py            #   AI CLI 呼び出し・セッション管理
│   ├── config.py                #   YAML 読み込み・プロンプト解決
│   ├── leader.py                #   Leader ロジック
│   ├── models.py                #   データクラス定義
│   └── dashboard.py             #   Web UI（モニタリング用）
├── prompts/                     # デフォルトプロンプト（7ロール分）
├── examples/                    # プロジェクトテンプレート（init でコピーされる）
│   ├── project.yaml             # 新規開発用（設定リファレンスを兼ねる）
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
        └── docker/              # プロジェクト固有 Dockerfile（任意）
```

## プロジェクト設定 (project.yaml)

設定の全項目・書き方の例・リファレンスは [`examples/project.yaml`](examples/project.yaml) を参照。

以下は主要セクションの概要のみ記載する。

| セクション | 役割 |
|---|---|
| `schema_version` | 後方互換管理用バージョン（未指定なら旧フォーマット扱い） |
| `project` | プロジェクト名と `work_dir`（YAML からの相対パス） |
| `generation` | デフォルトバックエンド・モデル・タイムアウト・リトライ数・failure_taxonomy 等 |
| `roles` | ロール別のバックエンド・モデル・セッションポリシー上書き |
| `checks` | 全フェーズ共通の機械検査コマンド（フェーズ単位で上書き可） |
| `phases` | フェーズ定義（id, title, tasks, steps, contract, outputs 等） |

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

## フェーズ設計の原則

各フェーズ内で「作業 → レビュー → 修正」のループが完結するように設計する。reviewer が fail を返すとフェーズ内でリトライされ、作業ロール（designer/coder 等）が修正指示を受けて成果物を書き直す。

```yaml
# 良い設計: 作業 + レビューが同じフェーズ内で完結
- id: "architecture"
  steps:
    - role: designer          # リトライ時は reviewer の指摘を元に修正
      action: design
    - role: reviewer          # fail なら designer に差し戻し
      action: review
```

レビューだけの独立フェーズを作ると、reviewer が fail を返しても成果物が修正されないまま同じレビューが繰り返される。

## Step 単位のバックエンド上書き

`roles` セクションでロール単位にバックエンド・モデルを設定するのが基本だが、step に `backend` / `model` を指定すると、その step だけ別のバックエンドで実行できる。セッションは引き継がない（stateless）。

```yaml
phases:
  - id: "architecture"
    steps:
      - role: designer
        action: design
      - role: reviewer
        action: review
        prompt: reviewer_feasibility.md
        # → roles.reviewer の設定（gemini）を使う
      - role: reviewer
        action: review
        prompt: reviewer_feasibility.md
        backend: codex
        model: gpt-5.4
        # → この step だけ codex で実行
```

これにより、同じフェーズ内で異なる AI の視点を組み合わせたレビューができる。

## Contract と Failure Taxonomy

### Contract（フェーズの合格条件）

フェーズごとに合格条件を宣言できる。省略時は従来挙動（checker が通れば合格）。

```yaml
phases:
  - id: "phase_02"
    contract:
      checker_must_pass: true          # checker 非ゼロ終了で失敗
      reviewer_confidence_min: 80      # reviewer の最低 confidence
      required_files:                  # phase 完了時に存在を検証（glob 可）
        - "lib/models/*.dart"
      forbidden_patterns:              # outputs/required_files 内を検索
        - "TODO:"
    outputs:                           # この phase が生成する成果物
      - "lib/models/*.dart"
```

### Failure Taxonomy（失敗分類と回復戦略）

失敗の種類ごとに回復戦略を定義できる。`generation` で全体デフォルト、`phase` で上書き可能。省略時は従来の `max_retries` + `stop_on_failure` 挙動。

```yaml
generation:
  failure_taxonomy:
    checker_error:        retry_coder                # checker stdout を coder に渡してリトライ
    reviewer_rejection:   retry_with_confidence_step  # confidence 閾値を上げてリトライ
    timeout:              session_reset_and_retry     # セッション破棄 → 新規リトライ
    missing_artifact:     abort                       # パイプライン停止
```

失敗時の処理フロー:

```
Step 失敗
│
├── 事実の検出（violation）
│   checker_nonzero, forbidden_pattern, confidence_below_min,
│   required_file_missing, timeout, session_error
│
├── failure_type への畳み込み
│   checker_error / reviewer_rejection / timeout / missing_artifact
│
├── strategy の適用
│   retry_coder / retry_with_confidence_step / session_reset_and_retry / abort
│
└── runs/ に記録
    contract_violations, failure_type, strategy_applied
```

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

全バックエンドとも `cwd=work_dir` で実行される。

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

## バックエンドのフォールバック

ロールごとに、特定のエラーパターンで別のバックエンド/モデルに自動切替できる。

```yaml
generation:
  fallbacks:
    coder:
      - error_patterns: ["rate limit", "quota", "429", "capacity"]
        fallback_backend: "claude"
        fallback_model: "claude-opus-4-6"
```

## 成果物記録（runs/）

パイプライン実行時、各ステップの出力が `workspace/<project>/runs/<timestamp>/` に自動保存される。

```
workspace/my-app/runs/20260325_143020/
├── phase_01/
│   └── attempt_01/
│       ├── log.json                    # ステップごとの成否・時間・contract_violations・failure_type
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
- **失敗分析**: `log.json` の `contract_violations`, `failure_type`, `strategy_applied` で失敗の原因と回復戦略を確認可能

## CLI オプション

```bash
# サブコマンド
python main.py init <dest>           # プロジェクト初期化（examples/ をコピー）
python main.py run <config.yaml>     # パイプライン実行
python main.py dashboard <project>   # モニタリング Web UI を起動

# run のオプション
--dry-run                            # 実行せずに計画を表示
--auto-approve                       # human ステップを自動承認
--only phase_01 phase_02             # 特定フェーズのみ実行
--input "修正内容"                    # 実行時の動的指示を注入
--input-file issues.md               # ファイルから動的指示を読み込み
--resume-run runs/20260325_185807    # 前回の成果物を引き継ぐ
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
