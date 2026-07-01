> **メモの位置づけ（2026-07-01 追記）**
> これは 2026-04 時点の設計検討メモ（design rationale）。**実装は未着手**。
> 本文中の「現在の aido はこう動いている」という現状認識は執筆当時のもので、
> その後 `.aido/` 再編・canonical state path 導入などのコミットが入っている。
> 着手する際は最新コード（`pipeline.py` / `steps.py` / `contract.py` / `config.py`）で必ず裏取りすること。
> 参考論文 (NLAH): https://arxiv.org/abs/2603.25723 （PDFはリポジトリに含めない。上記URLから取得）

以下は aido の改善検討に関する引き継ぎ情報である。
目的は、Natural-Language Agent Harnesses (NLAH) 論文の問題意識を参考にしつつ、aido に低リスクで価値の高い改善を入れること。
ただし、議論を通して、単に論文の見た目を真似るのではなく、aido の既存の強み（deterministic さ、責務分離、調整可能性）を保ちながら、state semantics と retry discipline をどう強化するかが主題になっている。

この文書は、ここまでの議論の「前提」「変遷」「現在の考え方」「今後の論点」を、後続の LLM が思考を再開しやすいようにまとめたものである。

# 0. 出発点
対象は aido。
aido はすでにかなり Harness Engineering の方向にある、という認識から出発している。

現状で既にあると見なしているもの:
- phases / steps による stage structure
- coder / reviewer / designer / leader などの roles
- outputs / checker / reviewer_confidence / forbidden_patterns による contracts & gates
- failure_taxonomy
- leader による限定的 orchestration
- runs/ と pipeline_summary.json による履歴保存
- reviewer/checker の結果を次の attempt に戻す retry の仕組み

したがって今回の主題は、
「aido を NLAH 的に作り直すこと」
ではなく、
「既にある harness engineering を、NLAH 的な関心（state semantics, durable state, evidence, retry discipline）の観点でどこをどう補強すると良いか」
である。

# 1. 論文と aido の整合に関する初期判断
NLAH 論文や関連解説を踏まえると、aido は方向性としてかなり近い。

特に、ハーネスのコア要素として挙げられる
- contracts
- roles
- stage structure
- adapters / scripts
- state semantics
- failure taxonomy
のうち、前半の多くはすでに aido にある。

ただし完全一致ではない。
NLAH / IHR は「自然言語ハーネスを in-loop LLM が逐次解釈する shared runtime」という色が比較的強い。
それに対して aido は、YAML 駆動で比較的 deterministic な実行器であり、この違いはむしろ aido の強みでもある。

このため、議論の比較的早い段階から、
- 論文の問題意識は参考にする
- しかし aido の deterministic さ、再現性、調整可能性は壊さない
という方針が共有されている。

# 2. 最初に採用候補として絞られたもの
論文の主張と別案レビューの比較を踏まえ、初期段階では次の 3 つが有力候補になった。

1. File-backed state
2. Evidence-backed answering
3. Self-evolution

逆に今回は見送る方向になったもの:
- Multi-candidate search を中心機能として導入すること
- runtime 全体を in-loop LLM 解釈型に大きく変えること
- verifier 層を厚く増やすこと
- dynamic orchestration を今回の主改善テーマにすること

見送り理由:
- aido には既に orchestration 基盤がある
- 論文でも multi-candidate search や heavy verifier はコスパが高いとは限らない
- 今回の狙いは「論文の再現」ではなく「aido に効く低リスク改善」である

# 3. 一度有力になった導入案
その後、一度は以下のような案がかなり有力になった。

AI に以下の artifact を書かせる:
- STATE.md
- EVIDENCE.md
- REFLECTION.md

Python 側で以下を自動生成する:
- MANIFEST.json
- TASK_HISTORY.jsonl

この案で想定されていた役割:
- STATE.md = run 全体の authoritative snapshot
- EVIDENCE.md = 修正根拠や検証証跡
- REFLECTION.md = retry 時の再設計メモ
- MANIFEST.json = authoritative artifact の一覧表
- TASK_HISTORY.jsonl = phase / attempt / status / failure_type / strategy などの簡易履歴

その後の修正で、さらに次の改善が入った:
- STATE.md は特定 phase の成果物ではなく、run 全体で固定パスの authoritative snapshot にする
- REFLECTION.md は retry 時のみ required artifact にする
- MANIFEST.json と TASK_HISTORY.jsonl は早めに Python 自動生成する

ここまでは、「file-backed state を入れ、evidence と reflection を artifact 化する」という NLAH 的な方向にかなり沿った案だった。

# 4. そこに対して出てきた違和感・再考
議論を進める中で、重要な違和感が出てきた。
これが現在の考え方の核になっている。

## 違和感A: STATE / EVIDENCE / REFLECTION は本当に普通の md 成果物なのか？
設計書、調査報告書、企画書、比較表などは、phase の本来成果物なので AI が自由に md を書けばよい。
しかし
- STATE
- EVIDENCE
- REFLECTION
の 3 つは、それとは性質が違うのではないか、という違和感が出てきた。

これらは
- 状態伝達
- 修正根拠
- retry の再設計
のような、パイプライン機能そのものに近い情報である。
そのため、
「phase の自由成果物」と同じように、AI にフリーフォーマット md を直接書かせるのは違和感がある
という認識が強くなった。

## 違和感B: AI に自由に artifact を書かせると不安定にならないか？
非常に強い懸念として、
「ファイルが増えることで逆に決定論的に決まる部分が減り、不安定になるのではないか」
という点が出てきた。

特に
- 役割が近いファイルが複数存在する
- prompt 内にも似た情報が入り、artifact 側にも似た情報がある
- AI が自由文で STATE / EVIDENCE / REFLECTION を更新する
という設計だと、
- 情報の重複
- authoritative source の曖昧化
- 説明のぶれ
- 定型的で空疎な artifact
が起きやすい、という懸念が強くなった。

ユーザーの好みとしても、
- AI に任せることによるブレは極力減らしたい
- 情報の重複はろくなことがない
という方向性がかなり明確である。

## 違和感C: REFLECTION 相当のものは今も別の形で存在していたのではないか？
aido の既存 retry を見直した結果、次の再認識があった。

今の aido でも reviewer/checker の失敗は次回 attempt に戻されており、その主経路は repair_instructions である。
つまり現在も
- reviewer / checker の失敗
- pipeline がそれを repair_instructions という文字列にまとめる
- 次回 attempt の prompt に修正指示として注入する
という retry の流れが既にある。

そのため、
「REFLECTION に相当する概念が今まで全くなかった」
のではなく、
「retry のための制御データは既にあったが、それは artifact として独立化されていなかった」
と見る方が正確だ、という理解になった。

# 5. repair_instructions に対する現在の見方
ここはかなり重要なので丁寧に書く。

途中の議論では、
- REFLECTION.md を導入するなら repair_instructions は不要ではないか
- 情報の重複を避けるために消したい
という話も出た。

しかしその後、repair_instructions は「意外と筋が良い」と再評価された。
ただしこの「筋が良い」の意味は、
「repair_instructions をそのまま残したい」
という意味ではない。

現在の理解は次の通り:
- repair_instructions は今の aido の設計にはかなり沿っている
- reviewer/checker の失敗を次の attempt に渡す retry 制御面として、実際に機能している
- そのため、REFLECTION 的なものを導入するなら、repair_instructions の発想を捨てるより、そこから切り出した方が筋が良い

ただし同時に、
- repair_instructions と REFLECTION.md の両方が並立して残るのは嫌
- 情報の重複は避けたい
- authoritative retry surface は 1 つにしたい
という考えも明確にある。

つまり現在のスタンスは、
- repair_instructions は「設計として筋が良い」寄りで評価している
- しかし最終的には repair_instructions を残したいのではなく、
- repair_instructions に相当するものを、他の 2 つ（STATE / EVIDENCE）と並ぶ形で REFLECTION.md として切り出したい
というものである。

要するに:
- repair_instructions の発想は支持
- だが repair_instructions と REFLECTION.md の二重管理は嫌
- 方向としては REFLECTION.md に一本化したい
- ただし、その実装コストや、AI 自由記述に寄せるか、Python 構造化に寄せるかはまだ検討中

ここは誤解しないこと。
現在の主張は
「repair_instructions を残す」ではなく、
「repair_instructions は筋が良いので、その発想を REFLECTION.md に発展的に引き継ぎたい」
である。

# 6. 現在の中心的な考え方
ここまでの議論を経て、現在の考え方はかなり次のように整理されている。

## 6-1. AI の自由成果物と、パイプライン機能の状態物は分けたい
ユーザーの現在の感覚では、成果物には 2 種類ある。

### A. phase の本来成果物
例:
- 設計書
- 調査報告書
- 分析結果
- 比較文書
- 提案書

これらは人間が読む自由文書であり、AI にフリーフォーマットの md を書かせてよい。
多少表現がぶれても問題は小さい。

### B. パイプライン機能の一部としての成果物
例:
- 現在の状態
- 修正根拠
- retry 時の再設計情報
- 次回 attempt に戻す指示
- authoritative artifact の一覧
- 進行履歴

これらは人間向け自由文書というより、
- 状態伝達
- 制御
- 再試行
- handoff
に関わる、パイプラインの一部である。

そのため、
- AI に自由文書として直接書かせるより
- パイプラインが責務を持つ構造化データとして扱い
- 伝えたい情報の中身だけ AI に埋めさせる
方がよいのではないか、という考えが強くなっている。

この分離は現時点のかなり重要な結論である。

## 6-2. Python コードが増えても良い
当初は
- できれば YAML と prompts の変更中心で
- Python 改修は最小限
という方針もあった。

しかし現在は、次の理由から Python 側の責務増加を許容する方向に傾いている。

- 調整しやすくしたい
- 決定論性を保ちたい
- authoritative source を明確にしたい
- AI の自由記述によるぶれを減らしたい
- 同じ意味の情報が複数箇所に散るのを避けたい

つまり、
「自然言語 artifact を見かけ上増やす」より、
「aido の運用性・調整可能性・安定性を優先し、そのために Python コードが増えるのは受け入れる」
という方向に寄っている。

## 6-3. REFLECTION.md は作りたい寄り
ここも重要。

現在のスタンスは、
- REFLECTION 相当の retry surface は必要
- しかも他の 2 ファイル（STATE / EVIDENCE）と並ぶ概念として切り出したい
- そのため REFLECTION.md を作ること自体には賛成寄り
である。

ただし、
- 自由記述 artifact として AI に書かせるのか
- repair_instructions を発展させた構造化 retry surface として Python 側が責務を持つのか
- そのための修正コストをどこまで許容するのか
はまだ未確定であり、今後の重要論点である。

つまり、
「REFLECTION.md はいらない」
ではなく、
「REFLECTION.md は作りたいが、その持ち方をちゃんと設計したい」
が現在の考え方。

## 6-4. 論文の思想との関係
この方向性は、一見すると NLAH 論文の「自然言語で外在化」という主張に逆らっているように見えるかもしれない。
しかし現在の理解では、そう単純ではない。

論文自体も、
- deterministic hooks は code / adapters / scripts に持たせる
- natural language は orchestration logic を担う
- state は durable artifact として externalize する
としており、「すべてを AI の自由文にしろ」と言っているわけではない。

したがって現在の方向性は、
「NLAH の思想をそのまま再現する」
のではなく、
「NLAH が気にしている問題（state semantics, retry discipline, evidence, durability）を、aido の流儀で再解釈する」
と見る方がよい。

# 7. MANIFEST.json と TASK_HISTORY.jsonl について
途中で出てきた
- MANIFEST.json
- TASK_HISTORY.jsonl
は、今回の議論の中で導入候補になった新規 artifact であり、もともと aido にあったわけではない。

## TASK_HISTORY.jsonl
これは run の簡易な時系列履歴。
例としては
- phase
- attempt
- status
- failure_type
- strategy
- key artifacts
などを append-only に記録するものとして提案された。
AI に書かせるより Python 自動生成に向く。

役割:
- 過去の試行の簡易履歴
- 全ログを再読しなくても今まで何が起きたか把握しやすい
- 後から分析しやすい

現時点では比較的筋が良いと感じられている。

## MANIFEST.json
これは authoritative artifact の一覧表。
例えば
- どの artifact が current / final / promoted とみなされるか
- どのファイルが state surface として参照対象か
- 重要成果物は何か
を構造化して持つものとして提案された。

役割:
- authoritative source の明確化
- reopen / recovery / handoff の補助
- 「どのファイルを見ればよいか」の曖昧さ削減

現時点では、
- TASK_HISTORY.jsonl は比較的有力
- MANIFEST.json は価値はありそうだが、設計次第で、まだ再検討余地あり
という感触がある。

# 8. 現在の未解決論点
今後の検討では、少なくとも次の問いを詰める必要がある。

## 論点1
STATE / EVIDENCE / REFLECTION を本当に独立 md artifact にするべきか？

あるいは
- パイプライン内部データ
- JSON / 構造化オブジェクト
- Python が整形して出力する controlled artifact
として持つべきか？

## 論点2
REFLECTION.md をどう設計するか？

現時点での方向性:
- repair_instructions と同じ情報を二重に持つのは避けたい
- REFLECTION.md は作りたい寄り
- そのため、repair_instructions の発想を吸収した authoritative retry surface として設計するのが有力
- ただし実装コストとの兼ね合いもあり、どの程度 Python 主導にするかは検討中

## 論点3
AI に何を自由記述させ、何を構造化出力させるか？

現在の傾向:
- phase の本来成果物は自由 md
- pipeline state / retry / evidence のような制御寄り情報は構造化寄り

## 論点4
MANIFEST.json / TASK_HISTORY.jsonl は入れるか？

現時点では
- TASK_HISTORY.jsonl はかなり筋が良い
- MANIFEST.json は state surface の設計次第
という印象。

# 9. 現在のスタンスを一言で言うと
現在の議論の着地点は、だいたい次のように要約できる。

- NLAH 論文の問題意識（state semantics / evidence / retry discipline / durable state）には強く共感している
- しかし、それをそのまま「AI に自由な md を書かせる artifact 群」として導入するのは aido には合わない可能性が高い
- aido では、phase の自由成果物と、pipeline state を明確に分離したい
- retry / state / evidence は、より Python 主導・構造化寄りに設計したい
- repair_instructions は今の aido の設計に沿った筋の良い retry surface だが、最終的には REFLECTION.md に発展的に引き継いで一本化したい
- Python コードが増えても、調整しやすく deterministic な方がよい

# 10. 次回以降の検討方針
次の検討では、抽象論を続けるより、実コードを見ながら次を詰めるのが良い。

対象としては特に:
- pipeline.py
- steps.py
- contract.py
- config.py

検討すべきこと:
1. 今の repair_instructions がどこでどう生成され、どこで消費されるか
2. それを REFLECTION.md にどう発展させて置き換えるか
3. STATE / EVIDENCE / REFLECTION を本当に file として持つべきか、それとも pipeline 内部データ + controlled output にすべきか
4. TASK_HISTORY.jsonl や MANIFEST.json を入れるなら、どの責務で、どの粒度で更新するか

重要なのは、もう単に
「NLAH 論文に沿うかどうか」
ではなく、
「aido の既存の強み（deterministic さ、調整可能性、責務分離）を保ったまま、state semantics と retry discipline をどう強化するか」
が現在の主題である、ということ。
