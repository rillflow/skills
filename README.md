# rillflow/skills

個人で使うAIエージェント向けのスキルを管理しています。スキルごとにフォルダを分け、必要なものを選んで追加・更新できます。

## 利用できるスキル

- [ideation](skills/ideation/README.md): 問題を別の視点で捉え直し、実行可能な案を探します。

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

## 更新時の検証

mainへのpushとPull Requestで、スキル名・説明文・名前の重複・Markdownの相対リンク先を自動検証します。補助スクリプトの単体テストも実行します。リンク先の見出しや外部URL、エージェントの回答品質は検証しません。

ローカルではPython 3.12で実行できます。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/validate.py
.venv/bin/python -m unittest discover -s tests -v
```
