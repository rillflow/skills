# 深考で検討する道具の全体像

この表はエージェントの検討用です。利用者への回答は、解決策とその使い方を中心に書きます。表のIDは記録を照合するための識別子であり、TRIZ公式の分類番号ではありません。

## 対象範囲

[TRIZ Body of Knowledge](https://www.aitriz.org/articles/TRIZ_Body_of_Knowledge.pdf)の七領域を、基礎概念、発展法則、ARIZ、物質・場分析、矛盾解消、科学効果、システム分析として扱います。[MATRIZの方法論](https://matriz.org/methodology/)にある問題識別・解決・解法検証へつなぎ、想像力とOTSMは出典を分けて補助に使います。これらの領域を検討対象にすることと、関連書籍や科学効果をすべて収蔵することは別です。

各行の方法を原典で確認し、具体的な入力・得た結果、適用外の理由、未確認の範囲を記録します。各行を個別に判定し、一つの方法の実行で別の方法まで確認済みにしません。同じ作業が複数の行を満たす場合は、その記録を参照できます。

## 問題を選び、構造を調べる

| ID | 確認する方法と得るもの | 原典への入口 |
|---|---|---|
| problem-definition | 発明状況と解く問題、要求と手段、発明水準の区別。必要な結果と観測事実から評価条件を決める | [主要問題](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/key-problem-7057/)、Body of Knowledge第1・7領域 |
| function-analysis | 構成要素、相互作用、機能と担い手、有益・有害・不足・過剰な作用を整理し、変えるべき機能を選ぶ | [機能分析](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/function-cost-analysis-7189/function-analysis-5611/)と下位文書 |
| cost-analysis | 機能を果たす要素ごとの費用と改善余地を比べる。金額や工数が不明なら定量評価は未確認とし、推測の数値を作らない | [費用分析](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/function-cost-analysis-7189/cost-analysis-7099/)、[機能・費用の比較](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/function-cost-analysis-7189/function-cost-analysis-6247/) |
| flow-analysis | 物質・エネルギー・情報の流れ、滞留・損失・迂回。流れを変えると消える問題を探す | [流れの分析](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/flow-analysis-5839/) |
| cause-effect-analysis | 診断、原因と結果の連鎖、必要・十分条件、制御できる原因。観測で区別できる原因仮説と解く位置を得る | [原因結果連鎖分析](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/cause-effect-chain-analysis-5817/)、Body of Knowledge第7領域 |
| system-operator | 部分・全体・上位系と過去・現在・未来。問題を移すと解法が変わる境界を得る | [システム思考](https://altshuller.ru/triz/triz70.asp) |
| ideality-resources | 理想的なシステム・物質・結果、対象・道具・環境の資源。負担を増やさず機能を満たす方向を得る | [ARIZ第2部](https://www.altshuller.ru/triz/ariz85v-2.asp)、[第3部](https://www.altshuller.ru/triz/ariz85v-3.asp) |

## 解法の仕組みを変える

| ID | 確認する方法と得るもの | 原典への入口 |
|---|---|---|
| contradictions | 要求上・技術的・物理的な矛盾を区別する。改善と悪化の因果、同じ性質の反対状態を確かめる | [矛盾の体系](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/contradictions/)、[ARIZ問題モデル表](https://www.altshuller.ru/triz/ariz85v-t1.asp) |
| separation | 時間・場所・条件・全体と部分、巨視的・微視的な変換。両立条件と切替時の成立を調べる | [物理的矛盾の解消](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/contradictions/physical-contradiction-6056/algorithm-for-resolving-physical-contradictions/)、[ARIZ変換表](https://www.altshuller.ru/triz/ariz85v-t2.asp) |
| inventive-principles | 40原理の全項目・下位条件を、候補・適用外・未確認に分ける | [40原理](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/contradictions/inventive-principles-6023/) |
| additional-principles | 追加の41〜50を個別に確認し、候補を広げる。基本40原理の番号には混ぜない | [追加10原理](https://www.altshuller.ru/triz/technique1a.asp) |
| paired-principles | ある解法と反対の勧告をする解法を比較するという仮説を使う。どの変換を逆にし、どの条件で有効かを記す。選んだ組を公式の対応表と呼ばない | [MATRIZ用語集](https://matriz.org/wp-content/uploads/2016/11/TRIZ-Glossary.pdf)のDuality “Principle-Anti-Principle”。[追加資料](https://www.trizminsk.org/e/212002.htm)は取得未確認 |
| contradiction-matrix | 改善・悪化するパラメータの対応と参照セルを確認する。行列を使えない問題では具体的な不一致を記録する | [矛盾行列](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/contradictions/engineering-contradiction-5995/contradiction-matrix-6026/) |
| su-field-standards | 物質・場の構成規則、76標準の全項目と下位条件。構成・破壊、発展、水準移動、測定、適用補助を調べる | [76標準](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/substance-field-modeling/standard-inventive-solutions/) |
| ariz | ARIZ-85Vの9部と戻り分岐。問題モデルから資源、知識の使用、問題の変更、解法と過程の検証へ進む | [ARIZ原文](https://www.altshuller.ru/triz/ariz85v.asp)、[工学的な深掘り](triz-classical.md) |
| trimming | 有益な機能を保ちながら要素を除く。不要化・対象への移転・他要素への移転と、その成立条件を調べる | [トリミングと下位規則](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/trimming-6398/) |
| feature-transfer | 代替システムの長所を組み合わせる。主系・補助系の相互作用と、移植できない条件を明らかにする | [特性の移転](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/feature-transfer-7060/) |
| evolution | 理想性、発展の不均衡、完備性、伝導、調和、上位系、動的変化、相互作用、微視化、整合・不整合。下位の発展方向も読み、次の制約と方向を仮説として比べる | [発展法則と下位文書](https://wiki.matriz.org/docs/triz/trends-of-engineering-systems-evolution-tese-5919/) |
| s-curve | 性能と時間・投入資源の推移を調べ、成長段階と改善余地を検討する。履歴がなければ曲線や成熟度を断定しない | [S曲線分析](https://wiki.matriz.org/docs/triz/problem-solving-tools-5807/s-curve-analysis/) |
| function-oriented-search | 必要な機能を一般化し、その機能を実現している別分野を実際に検索する。発見した仕組みと移植条件を記録する | [機能を基準にした検索](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/function-oriented-search-fos/) |
| analogue-problems | 現在の問題と構造が似た既知問題を検索し、共通する関係と異なる制約を確かめる | [既知問題の解法を一般化して使う方法](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/clone-problems-application/) |
| scientific-effects | 物理・化学・幾何・生物などの効果を、機能・対象・変えたい量から検索する。作用の異なる候補と成立条件を得る | [科学効果](https://wiki.matriz.org/docs/triz/problem-solving-tools-5890/database-of-scientific-effects/)、[検索経路](sources.md#科学効果の検索) |

## 固定観念を崩し、解法の先を確かめる

| ID | 確認する方法と得るもの | 原典への入口 |
|---|---|---|
| rvs | 大きさ・時間・費用を極端に変えて固定した前提を見つけ、現実の条件へ戻して案を作る | [RVS](https://altshuller.ru/triz/triz20.asp) |
| fantogram | 原典の特徴軸と変換を使って構成の別案を考え、現実に使える条件を選ぶ | [ファンタグラム](https://www.altshuller.ru/triz/triz9.asp) |
| little-people | 相互作用を小人の集団として描き、必要な振る舞いと配置を考える。想像上の動作を実現する仕組みは別に確かめる | [ARIZ第4部](https://www.altshuller.ru/triz/ariz85v-4.asp) |
| failure-anticipation | どの作用・順序・資源なら意図せず失敗を起こすかを調べる。故障を作る条件から対策と判別試験を得る | Body of Knowledge第7.10項、[ARIZ第7部](https://www.altshuller.ru/triz/ariz85v-7.asp) |
| secondary-effects | 導入で生まれる害・他工程の負担・追加の有益な作用を調べる。別用途、上位系の変更、問題を解かず改善できる余地も比較する | Body of Knowledge第7.11項、[ARIZ第7部](https://www.altshuller.ru/triz/ariz85v-7.asp)、[第8部](https://www.altshuller.ru/triz/ariz85v-8.asp) |
| connected-problems | 問題と部分解の依存関係を保ち、解決が別問題を生む箇所をまとめて扱う。OTSMの限定した応用であり、理論全体の実装ではない | [Khomenkoの解説](https://otsm-triz.org/sites/default/files/ready/khomenko050120supershortintroductionintoclassicaltrizandotsm.pdf) |
| solution-validation | 原理の成立、制約、実装、反復使用、既知手段との比較、解決過程を検証する。実物試験の結果と仮説を分ける | [MATRIZの方法論](https://matriz.org/methodology/)、[ARIZ第7〜9部](https://www.altshuller.ru/triz/ariz85v-7.asp) |

文書を読むだけでは、そこに書かれた道具を実行したことになりません。原典を確認できない項目は未確認として残し、項目名から手順を補作しません。
