# 環境ごとの役割: Codex

- 呼び出し元: このセッションのモデル。自分の側の手順1の独立判断は呼び出し元が書く。結論を書く前にideationを使い、Claudeの結論を見る前に書き終える。
- 実行役: 環境の`AGENTS.md`が定める実行役のモデルを使う。定めがなければ呼び出し元が実行する。実行役は判断するモデルに数えない。
- 相手（Claude）の呼び出し: 実行役が`scripts/ask.sh claude --prompt-file <F> --out-dir <D> [--cwd <作業フォルダ>]`を実行する。Claude側の省略時はFableを要求し、本文で定めた使用上限エラーだけでOpusを一度要求する。モデルを固定したいときは`--model`を付ける。実際のモデルIDは`.meta.json`の`model_observed`（出典はClaudeのstream-json最終resultの`modelUsage`）を台帳に写す。
- プロンプトには、手順1でideationを使う指示と、同じ問い・原資料の版・制約・受け入れ基準を入れる。呼び出し元の候補や推奨は入れない。
- Claude側は`--permission-mode default`と`--disallowedTools Edit Write NotebookEdit`で実行し、ファイルを書き込むツールを止める。Bashの実行は利用者の設定の許可ルールに従う。書き込みが起きてはならない資料は、ファイルを読ませずにプロンプトに貼って渡す。
- Codexのサンドボックスが`claude`の実行やネットワークを止めた場合は、`.err`と終了コードを台帳の冒頭に書いて終える。
