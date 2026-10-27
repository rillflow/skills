# 環境ごとの役割: Claude Code

- 呼び出し元: このセッションのモデル。自分の側の手順1の独立判断は呼び出し元が書く。結論を書く前にideationを使い、Codexの結論を見る前に書き終える。
- 実行役: Agentツールで起動するサブエージェント。実行役は判断するモデルに数えない。
- 相手（Codex）の呼び出し: 実行役が`scripts/ask.sh codex --prompt-file <F> --out-dir <D> [--cwd <作業フォルダ>]`を実行する。モデルを固定したいときは`--model`を付ける。実際のモデルIDは`.meta.json`の`model_observed`（出典は`model_observed_source`。通常は`~/.codex/sessions/`のrolloutにある`turn_context.payload.model`）を台帳に写す。
- プロンプトには、手順1でideationを使う指示と、同じ問い・原資料の版・制約・受け入れ基準を入れる。呼び出し元の候補や推奨は入れない。
- Codexは`-s read-only`のサンドボックスで実行され、ファイルを変更しない。
- 結果ファイルは作業フォルダの中の記録用フォルダ（例: `cross-check/<日付>-<主題>/`）に置き、台帳の応答ファイルの欄にパスを書く。
