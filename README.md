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

新しいスキルはフォルダを追加し、上の一覧に載せます。個別の使い方は各スキルのREADMEで説明します。
