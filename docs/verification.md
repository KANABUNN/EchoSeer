# Phase 8–9 — 2回照合とVoGルールによる推定

LiveのStart中の連続認識と、1ラウンドの音声を「順序を解析」で処理する有限解析は、
同じPassComparatorとVoG再構成でPASS1 / PASS2を照合します。
連続Live・Overlayの操作は[live-overlay.md](live-overlay.md)を参照してください。
通常の Analyze は1音の候補比較です。音声の開き方・Live保持長は [sequence.md](sequence.md) を参照してください。

## 結果を見る

| 結果 | 表示と扱い |
|---|---|
| CONFIRMED | 両PASSの採用Oracleが完全一致し、期待個数と重複なしを満たす。確定順を表示 |
| MISMATCH | 認識の食い違いが解消しない。不一致位置を表示 |
| INFERRED | 信頼度差・上位候補・VoGルールで組んだ推定順。要確認として表示 |
| CHECK | 音の不足、弱い候補、同点・僅差、途中切れなど。確認が必要 |

不一致位置は表の1〜7の番号です。例えば4番目が異なる場合は「不一致位置：4」です。
不一致位置の背景を赤、同一PASSの重複候補を茶色にします。
セルにマウスを合わせると、元の上位候補とスコア、重複位置、音声時刻を確認できます。
PASS表は補正前の認識結果を保持します。unknownの位置も残します。

CONFIRMED のときだけ「確定順」を表示します。
補正した順序は「推定順（要確認）」、決められない位置は ? で表示します。
INFERRED / MISMATCH / CHECK は FSM の UNCERTAIN に対応します。
確定後の FSM は LOCKOUT へ進み、一定時間と連続無音を満たすと次Roundへ進めます。
有限Replay・保持Liveの画面は1ラウンドの結果を残し、Next Round / Resetで選び直します。

## Phase 8 — PassComparator

期待個数はRound 1〜5の3 / 4 / 5 / 6 / 7個です。
各位置の採用Oracle・第1候補・信頼度を比較します。
完全一致でも、unknown、同一PASS重複、不完全な個数、比較不能なイベントがあれば確定しません。

食い違った位置で、採用済みの候補と相手の信頼度差が inference_margin 以上なら推定できます。
例えば PASS1 の L3 0.96 と PASS2 の L2 0.61 は L3 を推定します。
元の L2 の認識と4番目の不一致を保存し、CONFIRMEDにはしません。

Liveの順序解析はPASSの境界で重複抑制履歴を区切ります。
長いcooldown設定でも、2回目の提示で同じOracleが再び採用されます。
通常の単一音Live比較の履歴は変更しません。

## Phase 9 — 候補履歴と再構成

各位置の上位N種類を候補履歴に保存します。
波形スコア・スペクトルスコア・複合スコア、採用Oracle、第1候補、信頼度と時刻を保持します。
生の全順位は従来どおり SequenceEntry に残り、元の認識結果を削除・上書きしません。

候補の中から、Oracleが重複しない順序を探索します。
評価の優先順位は次のとおりです。

1. 採用済みOracleと一致する数を最大化
2. 両PASSで候補スコアの下限を満たす位置を最大化
3. 両PASSの候補スコアの平均を位置ごとに足した総和を最大化

重複していない採用済みの一致位置を保持し、不確かな位置を補正対象にします。
初期設定は1箇所までです。使用できる候補に解がない場合、複数位置の補正が必要な場合、
同じ一致度の次点案と僅差の場合は、自動で推定順を採用しません。

推定する位置には、少なくとも片方の採用Oracle、または両PASSの十分なスコアが必要です。
途中切れ・短すぎる音・長い音の上限超過・比較サンプル不足・REJECTED は推定に使いません。
両方HIGHの異なる認識が僅差で、VoG重複ルールによる解消もできなければ MISMATCH のままです。
候補を便宜的な順番で選んだ同点案を確定・推定表示にしません。

7種類のOracleだけを使う探索は最大7!の完了順序、約13,700の部分状態で制限されます。
キャンセルを探索中も確認します。巨大な探索結果や音声コピーを候補履歴へ蓄積しません。

## 設定

config.json の sequence を変更し、アプリを起動し直します。
既存のPhase 7設定はそのまま読め、追加項目がなければ初期値を補います。

| 設定 | 初期値 | 範囲・意味 |
|---|---:|---|
| candidate_top_n | 3 | 1〜7。保存・探索するOracleの上位種類数 |
| max_corrections | 1 | 0〜7。推定で変更・補完できる位置数。0で推定を無効 |
| inference_margin | 0.18 | 0〜1。個別信頼度差から推定する下限 |
| reconstruction_margin | 0.10 | 0〜1。同じ一致度を持つ次点案との総スコア差の下限 |

候補の下限は recognition.low_score_threshold（初期0.55）を使用します。
両PASSからの未採用候補には recognition.confidence_threshold（初期0.82）以上の根拠も必要です。
recognition.top_n はテンプレート集約に使用し、上記 candidate_top_n とは別の設定です。
同点・ほぼ同点はmargin設定を0にしても自動推定しません。
これらの初期値は実戦録音で校正した値ではありません。スコアは正解の確率ではありません。

## ログ

「認識ログを保存」がONなら、解析全体のJSONL summaryに次を保存します。

- verification.status / reason、1始まりのmismatch_indices、correction_indices
- PASS1 / PASS2 の元の認識、同一PASS重複、unknownと推定に使えない位置
- history の各位置の上位N候補・スコア・時刻・信頼度
- reconstruction の最良案・次点案・根拠・総スコア・差・探索数
- confirmed、確定時だけのfinal_sequence、推定時だけのsuggested_sequence

次点に同じ一致度の案がない場合、marginはnullです。
弱い案や同点案も診断用には残しますが、suggested_sequenceには採用しません。
認識ログと不確かな音声の保存は独立して切り替えられます。保存失敗でも解析結果を保持します。
再解析の開始・失敗・Next Round / Resetでは前の確定順・推定順を消去します。

## 再評価と確認範囲

提供A〜Gを2回提示に組んだ全5ラウンドは、現在の照合処理で正しい確定順を表示します。
欠落・途中切れ・ノイズ・間隔不足・実音声同士の不一致も検証しています。
加えて、提供音声を使い、特定位置の分類スコアに意図的な誤認識を注入する8ケースを用意しました。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_phase7.py --output-dir .runtime\phase89-segmentation
.\.venv\Scripts\python.exe scripts\evaluate_phase89.py
```

出力は指定フォルダーのWAV・report.jsonです。
正常、1箇所LOW、HIGHの重複、両PASSのLOW重複、弱い候補、同点案、2箇所LOW、HIGHの不一致を検証します。
注入した変更と元の分類順位を明記し、補正ケースがCONFIRMEDにならないことを確認します。

各声は実録音ですが、連結と無音・提示間隔、誤認識注入は人工的な条件です。
独立録音、会話・効果音、残響の重なりを含む実戦の採用率・誤検出率は未確認です。
詳細は [acceptance.md](acceptance.md) に記録しています。
