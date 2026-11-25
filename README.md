# rillflow/skills

仕事の判断をAIエージェントに任せるときに、根拠と経過を残すためのスキル集です。Claude Code・Codexなど、スキルを読めるエージェントで使えます。スキルごとにフォルダを分けているので、必要なものだけを追加できます。

## 利用できるスキル

- [ideation](skills/ideation/README.md): 問題を別の視点で捉え直し、実行できる案を出します。依頼に応じて迅速・分析・深考の3つの深さで検討し、深さを指定することもできます。
- [throughline](skills/throughline/README.md): 顧客と向き合う仕事で、会議の準備や議事録の整理、今日の優先順位の相談に答えます。案件を続けて扱うときは、目的・約束・分岐した作業を台帳に残します。顧客から受け取った情報を読める環境は、利用者の設定で決めます。
- [cross-check](skills/cross-check/README.md): 答えが決まっていない問いを、ClaudeとCodexに独立に判断させます。食い違った主張だけを根拠と一緒に交換し、結果を主張台帳に残します。

## インストール

使うスキルを選んで追加します。

```bash
npx skills add rillflow/skills
```

スキル名を指定する場合:

```bash
npx skills add rillflow/skills --skill ideation
```

## 構成と管理

各スキルは`skills/<skill-name>/`に置きます。

- `SKILL.md`: エージェントが読む実行手順と適用条件
- `README.md`: 利用者向けの説明と使用例
- `references/`: 必要に応じて読む参照資料
- `scripts/`: 必要に応じて使う実行補助

新しいスキルはフォルダを追加し、上の一覧に載せます。個別の使い方は各スキルのREADMEで説明します。

スキルの外には、検証に使うファイルを置きます。

- `docs/qa/`: スキルごとの検証記録と、再現に使う資料
- `tests/`: 補助スクリプトと検証用資料の単体テスト、QA用の収集器と固定データ
- `scripts/validate.py`: スキルのfrontmatterとMarkdownの相対リンクの検証
- `.github/workflows/validate.yml`: `scripts/validate.py`と単体テストを自動で実行する設定

## 更新時の検証

mainへのpushとPull Requestで、`scripts/validate.py`と単体テストを自動で実行します。GitHub Actionsの画面から手動でも実行できます。`scripts/validate.py`は次を検証します。

- `skills/`の各フォルダに`SKILL.md`があること
- `SKILL.md`が`---`で始まり`---`で閉じるYAML frontmatterを持つこと
- frontmatterの中身が空でないマッピングで、キーが文字列かつ重複しないこと
- `name`が英小文字・数字をハイフンでつないだ1〜64文字で（先頭・末尾と連続のハイフンは不可）、フォルダ名と一致し、ほかのスキルと重複しないこと
- `description`が空でない1024文字以内の文字列であること
- `.`で始まるフォルダとファイルを除く全Markdownファイルで、相対リンクと画像の参照先が存在すること

リンク先の見出しや外部URL、エージェントの回答品質は検証しません。

ローカルではPython 3.12で実行できます。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/validate.py
.venv/bin/python -m unittest discover -s tests -v
```
