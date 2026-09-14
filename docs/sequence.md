# Phase 7–9 — Oracle の順序解析

Replay WAV、または Live に保持した有限音声から、1ラウンドの PASS1 と PASS2 を分離します。
2つの列と各音の信頼度を独立して表示・保存します。通常の Analyze は1音の比較を続けます。

## Replay の操作

1. Calibration で7種類の Oracle サンプルを登録します。
2. Replay で2回の提示を含む1ラウンドの WAV を開きます。最初の音の前と最後の音の後に無音を含めてください。
3. Round を選び、「順序を解析」を押します。
4. PASS1 / PASS2 の各位置、HIGH / MEDIUM / LOW / REJECTED、状態を確認します。
5. 次のラウンドは Next Round で期待個数を選び、次の WAV を開いて解析します。Reset は Round 1 に戻して列を消去します。

| Round | 期待個数 |
|---|---:|
| 1 | 3 |
| 2 | 4 |
| 3 | 5 |
| 4 | 6 |
| 5 | 7 |

表の各位置には採用Oracle・信頼度・複合スコアを表示します。
unknown の位置も残ります。マウスを合わせると、開始/終了時刻・第1候補・理由を確認できます。
下の候補順位・スコア内訳は最後に解析した音の結果です。手動WAV保存は入力全体の前処理済み音声です。
小さい画面ではスクロールして結果と保存操作を確認してください。

入力は120秒以内です。WAV読込・展開の既存サイズ制限も適用します。
再解析は元のWAVを読み直します。前回の途中結果・失敗した解析結果は保存対象にしません。

## Live の保持音声

Live の「直近音声の順序を解析」を押すと、開始時点のリングバッファをコピーして同じ処理を行います。
取得は継続し、解析中にバッファへ追加した音は今回の対象に含めません。
Replay にコピーを残すので、同じ音声を「順序を解析」で再確認できます。

初期の保持長は10秒、audio.buffer_duration の設定範囲は5〜30秒です。
両PASSと前後の無音が収まる長さを設定して起動し直してください。
30秒を超えるラウンドは、別途用意した通し WAV を Replay で解析してください。
この段階では取得を続けながら全ラウンドを追跡する処理とフルセッション自動録音はありません。
Next Round は解析する期待個数の選択です。

## 状態と扱い

| 状態 | 意味 |
|---|---|
| IDLE | 未解析・Reset後 |
| ARMED | 最初のイベント待ち |
| PASS_1 | 1回目の提示を記録中 |
| WAIT_PASS_2 | 期待個数のPASS1を保存し、2回目を待機 |
| PASS_2 | 2回目を独立した列に記録中 |
| VERIFY | 両PASSが揃い、照合中 |
| UNCERTAIN | unknown・不足・間隔不足・タイムアウトなどの確認が必要 |
| CONFIRMED | 両PASSの採用結果が完全一致・期待個数・重複なしで確定 |
| LOCKOUT | 確定後、次ラウンドへ進むまで候補を追加しない |

Phase 8で完全一致をCONFIRMEDにし、食い違いの位置を表示します。
Phase 9で上位候補と重複なしのルールから推定順を組み、INFERRED（要確認）として表示します。
同点・僅差・弱い候補・不足はMISMATCH / CHECKとして扱います。元のPASSは保持します。
詳しくは [2回照合と推定](verification.md) を参照してください。

HIGH / MEDIUM の音だけを採用し、LOW / REJECTED は unknown としてその位置を残します。
抑制済みのduplicateは個数に含めません。1回の提示が期待個数に届く前に長い無音が来たら、
次の提示の音で埋めず UNCERTAIN に止めます。構造が不明な列は確定しません。

SequenceEngine の確定遷移は完全・採用済み・一致・重複なしの2列だけを受け付けます。
補正した順序はconfirmedを付けず、推定順として保持します。
確定後はlockout_durationと連続silence_durationの両方を満たすと次Roundに進みます。
手動Next Round / ResetもFSMに備え、5ラウンド後にRound 6を作りません。

## タイミング設定

config.json を変更し、アプリを起動し直します。初期値は実戦データで未校正です。

| 設定 | 初期値 | 用途 |
|---|---:|---|
| recognition.detection_threshold | 0.025 | native音声の開始RMS下限 |
| sequence.pass_gap | 2秒 | PASS1末尾からPASS2先頭までに必要な間隔 |
| sequence.event_timeout | 5秒 | 同一PASSの次イベント待機の上限 |
| sequence.lockout_duration | 10秒 | FSM確定後の候補追加抑制 |
| sequence.silence_duration | 3秒 | FSMの次Round解除に必要な連続無音 |

間隔は前の音の終了から次の開始まで数え、音自体の長さを待機に含めません。
PASS2待機はpass_gap + event_timeout（初期7秒）でタイムアウトします。
同一PASSが未完了でpass_gap以上の間隔が来た場合は不足と判断します。

Replayの時刻はファイル先頭からの相対秒です。
Liveはコピー時の最終書込みmonotonic時刻とnativeフレームを基に、各音の開始時刻を計算します。
解析にかかった時間やWindows時計の変更は間隔判定に使用しません。
入力ごとに新しいSequenceEngine・ConfidenceEngineを作り、既存の単一音Liveの重複履歴に影響しません。

## イベントの切り出しと制限

native音声で20msごとにチャンネル別DCを除いたRMSを測定します。
peak正規化前に検出し、小さい入力を正規化で大きくして検出することを避けます。
開始前60msと、終了後120ms連続で静かになった区間を含めて各音をコピーします。
終了RMSは開始閾値の40%以下です。微小音とDCのみの区間はイベントにしません。

1音の有効長80ms未満、入力先頭・EOFで音が切れた候補は unknown にします。
1窓は最大3秒 / 16 MiB、候補は128個までです。長い音は複数音へ強制分割せず1候補で制限します。
切り出したnative音声に共通前処理・波形/スペクトル比較・信頼度判定を適用します。
全候補・スコア・音声時刻を残し、後続の照合・再構成が参照できる形にしています。

残響や別の音が重なると複数Oracleを1候補として検出する可能性があります。
不足・unknownになった場合は、録音範囲・音量・提示間隔と初期値を確認してください。

## 保存と再評価

「認識ログを保存」がONなら logs/sessions/*.jsonl に次を保存します。

- 音ごとのround / pass / index、nativeフレーム、開始時刻、信頼度、順位とスコア
- 解析結果のstate / reason、PASS1 / PASS2の独立列、期待個数、状態遷移、checksum

不確かな音声の保存は従来どおり独立して切り替えます。
保存失敗は画面に案内し、解析結果と手動WAV保存を維持します。

提供 A〜G を [対応表](sample-mapping.json) に従って2回提示に組んだレシピは
[phase7-oracles.json](../tests/datasets/phase7-oracles.json) です。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_phase7.py
```

出力は .runtime/phase7-evaluation/ のWAVとreport.jsonです。
全5ラウンドの50イベントで正しいOracleと2つのPASSを確認しました。
欠落・不一致・白色ノイズ・途中切れ・PASS間隔不足も含む計10ケースを評価します。
元の録音・テンプレート・設定は変更しません。

各声は録音済みの実Oracle音声ですが、連結と無音・提示間隔は人工設定です。
独立録音、会話・効果音や重なりを含む実戦の通し録音の採用精度は未確認です。
実装と実環境の確認範囲は [acceptance.md](acceptance.md) に記録しています。
