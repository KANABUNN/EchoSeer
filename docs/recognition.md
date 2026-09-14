# Phase 4 — Oracle の波形認識

## 比較する

1. Calibration で L1 / L2 / L3 / MID / R1 / R2 / R3 のサンプルを登録します。
2. Replay で、1つの Oracle を含む WAV を開いて Analyze を押します。
3. 第1候補と第2候補、それぞれの波形スコア、差、全7種類の順位を確認します。
4. Live の「直近音声を Replay へ」でも同じ比較を行えます。Stop 後のバッファも使えます。

比較区間は 20 ms〜10秒です。Live の初期バッファは10秒なので、1つの Oracle を
含む短い区間で停止して比較してください。複数 Oracle の連続検出・切り出しと
VoG の順序処理は後続 Phase で実装します。
小さい画面では Replay 全体をスクロールして順位表と保存操作を確認できます。

表示する値は波形の類似度（0〜1）です。正解の確率や HIGH confidence ではありません。
無音・未登録・同点・不一致では Oracle を確定しません。
比較できない Oracle は「未比較」と表示し、破損サンプルは警告して除外します。
登録・削除した内容は次の Analyze に反映します。

## 提供された音声と対応表

samples/VaultOfGlass_Templar.png の手前から奥への配置に従います。
左1・右1は手前、左2・右2は中段、左奥・右奥は奥です。
Atheon の6個配置とは別に、Phase 4 では Templar の7種類を固定 ID に対応させます。

| ファイル | 固定 ID | 表示名 | 図の音名 |
|---|---|---|---|
| A.wav | L3 | 左奥 | A |
| B.wav | R3 | 右奥 | B♭ |
| C.wav | MID | 中央 | C |
| D.wav | L1 | 左1 | D |
| E.wav | R1 | 右1 | E |
| F.wav | L2 | 左2 | F♯ |
| G.wav | R2 | 右2 | G |

対応表は [sample-mapping.json](sample-mapping.json) に保存しています。
次の操作で通常のアプリ保存先へ登録できます。既存と同じ音声・Oracle は追加しません。

```powershell
.\.venv\Scripts\python.exe scripts/import_samples.py
```

保存先を分ける場合:

```powershell
.\.venv\Scripts\python.exe scripts/import_samples.py --data-dir .\.runtime\oracle-check
.\.venv\Scripts\python.exe main.py --data-dir .\.runtime\oracle-check
```

全7ファイルと対応表を読み込んでから追加します。登録中のディスクエラーや
キャンセルでは、それ以前に登録済みのサンプルは保持されます。
元 WAV と対応画像は変更しません。ローカルの samples/ は Git 対象外です。
今回の7音声は通常の保存先へ登録済みです。Calibration で確認できます。

## 比較設定

config.json の recognition 内の次の項目が動作します。
設定を変更したらアプリを起動し直してください。

| 項目 | 初期値 | 意味 |
|---|---|---|
| template_aggregation | "best" | Oracle ごとの最高スコア。"top_n_mean" で上位N件の平均 |
| top_n | 3 | 平均する上位件数。登録数が少ない場合は存在する件数 |
| bandpass_enabled | false | 比較時の帯域処理を有効化 |
| bandpass_low_hz | 350.0 | 帯域の下端 |
| bandpass_high_hz | 3000.0 | 帯域の上端 |

帯域は 0 < 下端 < 上端 < 内部レートの半分を満たす必要があります。
イベントとサンプルの両方に同じ帯域処理を適用します。
帯域処理は元 WAV、保存済み sample.wav、Replay の音声変換・保存結果を変更しません。
waveform_weight / spectrum_weight と confidence 閾値は Phase 5 / 6 で使用します。

## 実装と確認範囲

チャンネル平均 → DC 除去 → 内部レート変換 → peak 0.95 正規化を共通化し、
保存した native 元音声も現在の設定で前処理します。必要に応じ帯域処理と再正規化を追加します。
FFT 相関で時刻ずれを探索し、各重複区間の平均とエネルギーで正規化します。
極性反転にも対応する絶対相関です。短い端部だけの偶然一致を避けるため、
短い方の80%以上の長さと中心化後エネルギーの50%以上を含む重複だけを比較します。
比較用 mono テンプレートの合計は128 MiB以下に制限します。

Qt に依存しない OracleClassifier は oracle / best_score / second_candidate /
second_score と全候補・各サンプルの結果を返します。Replay の専用ワーカーで比較し、
取得 callback に分類処理を追加していません。

提供された48 kHz stereo WAV は全区間同士で **7/7**、最初の1秒をテンプレート、
1.00〜1.75秒を比較音声にした重ならない区間でも **7/7** の第1候補が正解でした。
後者の第1スコアは0.237〜0.456で、波形の完全一致ではありません。
両確認とも同じ7録音を使っており、独立した録音・雑音下・実戦の精度評価ではありません。
実 Windows 画面でも両条件の順位、通常サイズ・760×600の表示、終了を確認しました。
詳細は [受け入れ確認](acceptance.md) を参照してください。
