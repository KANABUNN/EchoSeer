# Phase 6 — 信頼度と unknown

Replay の「認識結果」が、信頼度判定後に採用した Oracle です。
第1・第2候補と順位表は比較結果を残すため、unknown の場合にも表示します。
スコアは類似度であり、正解の確率ではありません。

## 判定基準

上位の複合スコアを best、第2候補との差を margin として両方を評価します。
7 種すべての比較サンプルが揃っていることも採用条件です。

| 信頼度 | 初期値での条件 | 認識結果 |
|---|---|---|
| HIGH | best ≥ 0.92、margin ≥ 0.18 | Oracle を採用 |
| MEDIUM | best ≥ 0.82、margin ≥ 0.10。HIGH の条件には未到達 | Oracle を採用 |
| LOW | best ≥ 0.55 だが、採用条件に未到達・僅差・比較サンプル不足 | unknown |
| REJECTED | best < 0.55、無音、比較不能、不正スコア、取得時刻が過去 | unknown |

同点・ほぼ同点は margin の設定が 0 でも採用しません。
例えば L2 0.83 / R1 0.82 は LOW、認識結果は unknown になります。
重複を除外したイベントも認識結果は unknown です。この場合、スコアの信頼度を残して
「HIGH / 重複を除外」のように理由を併記します。

## 閾値の変更

保存先の config.json の recognition で変更し、アプリを起動し直します。
スコアと margin は 0〜1、cooldown は 0〜10 秒です。
スコア閾値は low ≤ confidence ≤ high、margin 閾値は通常 ≤ high の順序が必要です。

| 設定名 | 初期値 | 意味 |
|---|---:|---|
| low_score_threshold | 0.55 | LOW と REJECTED の境界 |
| confidence_threshold | 0.82 | MEDIUM 以上を採用する best の下限 |
| high_confidence_threshold | 0.92 | HIGH の best の下限 |
| margin_threshold | 0.10 | MEDIUM 以上を採用する候補間の差 |
| high_margin_threshold | 0.18 | HIGH の候補間の差 |
| duplicate_cooldown | 0.45 | 同じ Oracle の重複抑制時間。0 で無効 |

これらは初期値であり、実戦の誤検出率から校正した値ではありません。
detection_threshold は Phase 7 の有限音声のイベント切り出しで使用します。
手順と時間設定は [sequence.md](sequence.md) を参照してください。

## Live と Replay の時刻

Live はバッファからコピーした音声と、最終書込み時の monotonic 時刻・ストリーム ID・
native フレーム範囲を同じロックで取得します。解析の処理時間や Windows の時計変更は
cooldown に影響しません。Stop 後の同じバッファは取得時刻も同じため、再送すると重複になります。

重複は Oracle ごとに、最後に採用したイベントから測ります。除外した重複で抑制時間を延長せず、
LOW / REJECTED は抑制の起点にしません。Start・再接続・音声欠落後のバッファリセットで
新しいストリーム ID になり、抑制履歴を初期化します。

Replay は有限 WAV の末尾の相対時刻を使い、重複抑制を行いません。
同じ音声・サンプル・設定の再解析は同じ判定を返し、Live の履歴を変更しません。
ここでの時刻は単一音を比較する解析窓の取得時刻です。
Phase 7 の「順序を解析」は各音の開始時刻を使用し、入力ごとに新しい履歴でPASSを作ります。
単一音の Live 重複履歴は変更しません。

## ログと音声の保存

Replay の順位表の下にある「認識ログを保存」「不確かな音声を保存」で個別に切り替えます。
切替は config.json に保存します。解析中は変更できません。
通常の保存先は %LOCALAPPDATA%\\OracleAssistant です。

- logs/sessions/*.jsonl：判定、理由、採用した Oracle または null、第1・第2候補、
  margin、各 Oracle の3スコア、元音声の checksum、音声ソースと保存 WAV の情報。
- logs/audio/YYYY-MM-DD/*.wav：候補がある LOW / REJECTED と重複の元音声。
  最大 3 秒・16 MiB。長い入力は最も音量の大きい 100 ms 区間の前後を保存します。

保存 WAV は native のレート・全チャンネル・float32 サンプル値を保持します。
保存した開始・終了フレームを JSONL に記録します。無音・比較候補なしのイベントは音声を保存しません。
日時用 timestamp、書込み処理の monotonic_time、音声の event_time を分けて記録します。
Replay の event_time は WAV 内の相対時刻、Live は monotonic 時刻です。

2 つとも OFF なら認識用 JSONL・WAV を作成しません。診断ログ application.log は別です。
フルセッション録音は初期 OFF のままです。既存ログを自動削除せず、必要に応じて保存先で整理します。
保存失敗は画面で案内し、解析結果と信頼度判定を保持します。

## 提供音声での確認

提供された A.wav〜G.wav を登録音声全体と比較すると、7 種とも HIGH で採用されました。
登録を 0〜1 秒、比較を 1〜1.75 秒に分けると、順位は 7 種とも正しいままですが、
複合スコアは 0.456〜0.608 で、初期閾値では 6 REJECTED・1 LOW、すべて unknown です。
Phase 5 の -20 dB 白色ノイズ付き B.wav も、候補 R3 / 0.119329 を REJECTED としました。

自己一致を別録音での認識率とは扱いません。独立した録音・会話・効果音・実戦での閾値校正は未確認です。
[受け入れ確認](acceptance.md) に自動テストと実 Windows 画面の確認範囲を記載しています。
