# Phase 10–12 — Live / Overlay / Calibration

## Liveで使う

1. Calibrationで7種類のOracleを登録します。既存サンプルをそのまま使用できます。
2. Liveの入力方式とデバイスを選びます。Loopbackはゲームの再生先、VoiceMeeterは対応する録音入力を選びます。
3. Oracleの提示が始まる前にStartを押します。音声取得中は、自動で音の区間を解析します。
4. Round、PASSごとの個数、元の認識、Oracle Map、確定順／推定順をLive画面で確認します。

Oracle Mapには番号と固定ID、表示名を重ねます。色だけでなく文字・番号・枠でも結果を識別します。
PASS表のunknownは補正後も残り、セルにマウスを合わせると元の候補とスコアを確認できます。
Confidenceは記録した音の複合スコアの最低値です。正解の確率ではありません。

| 表示 | 意味 |
|---|---|
| LISTENING | 最初の音を待機 |
| PASS 1 | 1回目の提示を記録 |
| PASS 1 保存 | 期待個数を保存し、2回目を待機 |
| VERIFYING | 2回目を記録・照合 |
| CONFIRMED | 両PASSが採用済み・完全一致・期待個数・重複なし |
| INFERRED / 要確認 | 信頼度と上位候補から推定。確定順にはしない |
| MISMATCH / CHECK | 不一致・弱い候補・途中切れ・不足など。確認が必要 |
| LOCKOUT | 確定済み。破壊音や戦闘音の候補を追加しない |

CONFIRMEDの確定順を残した後、lockout_durationと連続無音silence_durationを満たすと次Roundを待機します。
Round 5の後は遭遇終了で、Resetまで新しい列を作りません。
MISMATCH / INFERRED / CHECKでは自動でRoundを進めません。

Next Roundで任意に次へ進めます。Round選択でも提示を選び直せます。
ResetはRound 1へ戻し、元のPASSとマップ番号、前の確定・推定表示を消します。
Stopは音声取得とLive認識を止め、前の列を消します。
再開・Round選択は操作時点の最新フレームから始め、過去のバッファを再び列へ追加しません。

## 中断と負荷

音声callbackでは解析を行いません。
専用Live workerがリングバッファの未読分だけを取得し、20 msブロックで音の開始・終了を検出します。
Nativeの元音声を保持し、検出区間だけを共通Analyzerと波形／スペクトル分類へ渡します。

1つの音は最大3秒 / 16 MiBです。開始閾値未満の残響が60ms以上続いた後、
開始閾値の1.5倍以上へ再上昇した場合は新しいOracle onsetとして分割します。
この再発音条件がない長い連続音は1回のEVENT_LIMITとして扱い、
終了できる無音まで抑制します。入力全体を蓄積し続ける設計にはしません。
1回目のPASSを保存したところで重複抑制履歴を区切り、2回目の同じOracleを受け付けます。

バッファの読み落とし・overflow・stream変更はCHECKで停止し、前後の音声を組み合わせて確定しません。
入力切断後は同じデバイスへの再接続を試み、復帰時は現在のRoundを空の列からやり直します。
不一致や欠落が出たら、提示前にResetまたはRoundを選び直してください。

Calibrationの録音・Import・Listen・削除・品質確認、Replayの処理中はLive認識を一時停止します。
音声取得は継続し、完了後は現在のRoundを最新フレームから空の列で再開します。
提示の途中にこれらの操作を行うと列が消えるため、提示前に操作を済ませてください。

## Overlay

LiveのOverlayボタンで表示を切り替えます。Overlay設定で次を変更できます。

- 表示／非表示、通常時のクリック透過
- 順序／Oracle Map
- 不透明度0.05〜1、倍率0.25〜4
- 基準幅・高さ、文字サイズ

通常の初期設定はクリック透過ONです。背後のゲームをそのまま操作できます。
「位置をドラッグで調整」をONにした間だけクリック透過を外し、Overlayをドラッグできます。
調整後はOFFに戻してください。設定画面を閉じると位置調整を終了します。
位置調整モードそのものは次回起動へ保存しません。

位置・モード・大きさ・不透明度・倍率はconfig.jsonへ保存します。
モニター構成の変更で画面外になる場合、利用可能なモニター内へ位置と大きさを補正します。
負の座標のモニターやモニター間の空白にも対応した復元処理です。

小さい倍率でも列を読めるよう、順序表示は最低240×112、マップは最低320×320論理ピクセルを使います。
長い順序は折り返し・文字サイズ調整を行い、見出し・補足が収まらない場合は末尾を省略します。
画面より大きい要求は画面内に収めます。主画面も画面に合わせ、収まらない内容はスクロールできます。

OverlayはLiveの同じ状態と照合結果を表示します。
Replayの再解析結果をゲーム用Overlayへ流しません。
推定は黄色のINFERRED／要確認、不一致はMISMATCH／CHECKで、確定のチェック表示と区別します。

実装は独立したQt windowを使い、枠なし・最前面・透過背景・入力透過・非フォーカスの設定を適用します。
入力透過とフォーカスの扱いは[Qt公式仕様](https://doc.qt.io/qt-6/qt.html)、
Windowsの透過ウィンドウの入力処理は[Microsoft公式仕様](https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features)を参照しています。

## Oracle配置と表示名

Overlay設定の「Live画面のOracle配置をドラッグで調整」をONにし、
LiveのOracle Mapで各Oracleを動かします。マウスを離すと位置を保存し、Overlayへ反映します。
設定を閉じると調整を終了します。「Oracle配置を初期値に戻す」も使えます。
配置は0〜1の正規化座標なので、画面サイズが変わっても固定IDとの対応を保ちます。

Calibrationでは表示名を40文字まで編集して保存できます。
L1 / L2 / L3 / MID / R1 / R2 / R3の固定IDとサンプルの対応は変えません。
操作の詳細は[Calibration](calibration.md)を参照してください。

## 保存と確認範囲

Replayの認識ログ・不確かな音声の保存設定はLiveにも適用します。
LiveのJSONLには各音のRound / PASS / index、元の候補・confidence・音声フレーム範囲と、
最終照合の候補履歴・不一致位置・推定根拠を保存します。
Live summaryのsourceはstream_id / sample_rate / start_frame / end_frameを記録します。
checksum_scope=ordered_cue_checksumsは保存した候補区間のchecksumを順番に連結したハッシュで、
バッファ全体のWAVファイルを表すハッシュではありません。

提供A〜Gによる全5ラウンド、異なるchunk境界、実音声1箇所の不一致を検証できます。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_live.py
```

結果は.runtime/live-evaluation/report.jsonです。
7ケースの連続LiveとReplayは元の列が一致し、正常列だけCONFIRMEDになります。
音声と登録テンプレートは同じ元録音で、提示間隔と無音は人工条件です。
実戦の通し録音、独立録音、会話・効果音や残響の重なりによる精度は未検証です。

Windowsのテスト用borderless windowで、クリック透過・位置調整・最前面・非アクティブ表示を確認しました。
100% / 125% / 150%のQt表示倍率、7個の列とマップ、画面内復元、終了時の全4worker解放を確認しています。
Destiny 2実行中のborderless環境、物理的な複数モニター変更、長時間実戦の負荷は未確認です。
検証の記録は[acceptance.md](acceptance.md)を参照してください。
