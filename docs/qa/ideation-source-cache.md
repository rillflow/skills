# ideation原典キャッシュのQA

原理・標準の見出しIDが重複した取得結果を拒否し、前の本文を上書きしたキャッシュを完成扱いにしないことを確認します。

## 実行手順

リポジトリのルートでPython 3.12の仮想環境を作成し、依存関係を導入して実行してください。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/validate.py
.venv/bin/python -m unittest discover -s tests -v
```

`test_fetch_rejects_duplicate_source_headings_and_marks_cache_incomplete`は、103文書とARIZの応答をローカルの合成データで代替します。原理1と標準1.1.1の重複を別々に混ぜ、取得から失敗記録・読み取り拒否までを実行します。

## 確認結果

2026-10-26に上記の手順で仮想環境を作成し、Python 3.12.11で実行しました。

| 対象 | 結果 |
|---|---|
| 修正前の重複応答 | 例外が発生せず、回帰テストの2ケースが失敗した |
| 修正後の重複応答 | `SourceFormatError`で停止し、重複IDと原因をmanifestに記録した |
| 失敗後のキャッシュ | `complete=false`を保持し、`read_group`が読み取りを拒否した |
| 正常な応答・既存キャッシュの完全性 | 既存テストが通過した |
| 標準5.1.1内の下位見出し5.1.1.1 | 既存テストで5.1.1の本文として保持された |
| 全単体テスト | 24件通過した |

現在の公開原典に重複があると確認したものではありません。外部サイトからの取得と、エージェントの回答品質の再評価は未実施です。ARIZはこの抽出関数を使わず、既存の取得・見出し検査を維持しています。
