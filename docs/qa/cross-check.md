# cross-checkの検証

更新日: 2026-10-26

応答の復元と引数の検証を対象に、合成入力による回帰試験とCLIの実行結果を記録します。モデルの判断品質や、不一致を交換する手順全体の検証は含みません。

## 環境とローカルでの検証

実行環境はmacOS、Python 3.12.11です。以下のコマンドはリポジトリのルートで実行します。準備手順は[README](../../README.md)と共通です。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/validate.py
.venv/bin/python -m unittest discover -s tests -v
```

検証スクリプトは、3個のスキルと32個のMarkdownファイルで成功しました。

実際のモデルを呼び出さずに、引数の検証と合成CLIからの応答収集だけを確認する場合:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_cross_check*.py' -v
bash -n skills/cross-check/scripts/ask.sh
git diff --check
```

## 合成入力による回帰試験

実在の業務資料は使っていません。rolloutとClaudeのJSONを一時フォルダに生成し、試験用CLIで起動の有無と応答収集を確認します。

| 対象 | 確認した動作 |
|---|---|
| 最終応答の復元 | 長いanalysis・commentary・種別不明のメッセージを除外し、最終応答だけを復元する |
| 最終応答の識別 | `channel=final`と、channelが無い場合の`phase=final_answer`を認識する。両方の値が矛盾する場合は除外する |
| 短い応答 | 短い最終応答も復元する。通常の答えに点検の定型句が含まれていても保持する |
| 復元できない応答 | 元の`.out`を保持し、`output_unverified`・終了コード6を返す |
| 失敗の優先順位 | CLIの失敗・時間切れ・モデル不一致・モデル未確認を、応答復元不可で上書きしない |
| オプションの値 | 6種類のオプションで、値の欠落・空文字・次のオプションを渡す18ケースが終了コード2になる。正常な必須引数を先に指定し、CLIも出力フォルダも作成しないことを確認する |
| 正常な引数 | Codex・Claudeの試験用CLIを起動し、応答とモデルをmetaへ収集する |

最初の修正前の回帰実行では、assertionが9件失敗し、値を欠いた6種類のオプションは時間切れになりました。修正後は全42件の単体テストが成功しました。上表の18ケースは、単体テスト内で個別に確認しています。

## 実際のCLIによる合成試験

ツールの呼び出し、ファイルの読み書き、外部資料へのアクセス、公開を禁止した合成プロンプトを使いました。進捗として`SYNTHETIC_PROGRESS`、最終応答として`SYNTHETIC_FINAL_OK`を返すよう依頼しました。

呼び出しには[`ask.sh`](../../skills/cross-check/scripts/ask.sh)を使い、共通で`--timeout 120 --effort high`を指定しました。`--prompt-file`・`--out-dir`・`--cwd`には合成試験用の一時フォルダ内のパスを指定しました。Codexには`--model gpt-6.1-sol`も指定し、Claudeではモデル指定を省略しました。

| 選択されたCLI | 結果 |
|---|---|
| Codex CLI 0.164.0-alpha.11.1 | 10秒、終了コード0、`ok`。stdoutは`SYNTHETIC_FINAL_OK`だけで、進捗はstderrに出力された。rolloutには`phase=commentary`と`phase=final_answer`が記録された。モデル`gpt-6.1-sol`とeffortの`high`をrolloutで確認した |
| PATHが最初に選んだClaude Code 1.0.44 | 2秒、終了コード1、`error`。stderrは`error: unknown option '--effort'`。モデルの応答は得られず、モデル・effort・セッションは未確認だった |
| 既にインストールされていたClaude Code 2.1.207 | 起動プロセスのPATHだけを変更して実行した。8秒、終了コード0、`ok`。JSONの`result`と`.out`は、進捗と最終応答を空行でつないだ内容だった |

Claude Code 2.1.207の`modelUsage`は、`claude-haiku-4-5-20251001`と`claude-opus-4-8[1m]`でした。応答を生成したassistantの記録は`claude-opus-4-8`でした。effortは`high`を指定しましたが、セッション記録に値が無く、metaの実測値は`null`です。

Claudeの最初の失敗は、PATHから古いCLIが選ばれたことが原因でした。`command -v claude`・`claude --version`・`claude --help`で、実際に選択される版と必要なオプションを確認してください。対応するCLIが既にある場合は、その実行ファイルのあるディレクトリを、試験プロセスのPATHの先頭に指定できます。この試験ではCLIの更新・インストールと、永続的なPATHや設定の変更は行っていません。

## 検証していない範囲

- 2つのモデルによる独立判断から、不一致を交換して報告するまでの手順全体。
- 実際の書き込み後の点検フックへの再入と、その場での応答復元。復元の回帰試験は合成rolloutで行いました。
- Claudeの実際のeffort値。値の無い記録から、指定どおりに実行されたとは判断していません。
