# プロジェクト固有プロンプト

このフォルダにプロンプトファイルを置くと、aido/prompts/ のデフォルトを上書きできます。

## 仕組み

aido は以下の優先順位でプロンプトファイルを探します:

1. **このフォルダ** (`prompts/`) にあれば → それを使う
2. **なければ** → `aido/prompts/` のデフォルトを使う

## 例

`aido/prompts/reviewer_system.md` のレビュー基準をこのプロジェクト専用にしたい場合:

```
prompts/
  reviewer_system.md   ← これを置くとデフォルトの代わりに使われる
```

## 使えるファイル名

- `coder_system.md` — コーダーへの指示
- `tester_system.md` — テスター向け指示
- `reviewer_system.md` — レビュー基準
- `designer_system.md` — 設計者向け指示
- `explorer_system.md` — 調査者向け指示

各ファイルには YAML frontmatter でモデルやセッション設定も書けます:

```yaml
---
model: gpt-5.4
session: stateless
---
あなたはコードレビュアーです。...
```
