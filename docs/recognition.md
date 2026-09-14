# Phase 4–5 — Oracle の波形・スペクトル認識

## 比較する

1. Calibration で L1 / L2 / L3 / MID / R1 / R2 / R3 のサンプルを登録します。
2. Replay で、1つの Oracle を含む WAV を開いて Analyze を押します。
3. 第1候補と第2候補、それぞれの複合スコア、差、全7種類の順位を確認します。
4. Live の「直近音声を Replay へ」でも同じ比較を行えます。Stop 後のバッファも使えます。

比較区間は 20 ms〜10秒です。Live の初期バッファは10秒なので、1つの Oracle を
含む短い区間で停止して比較してください。複数 Oracle を含む1ラウンドの音声は
Phase 7 の「順序を解析」で切り出し、PASS1 / PASS2 を分離します。[sequence.md](sequence.md) を参照してください。
小さい画面では Replay 全体をスクロールして順位表と保存操作を確認できます。

順位表の値は波形・スペクトル・複合の類似度（0〜1）です。正解の確率ではありません。
Phase 6 の「認識結果」と信頼度の判定基準は [confidence.md](confidence.md) を確認してください。
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
| waveform_weight | 0.60 | 波形成分の重み（0〜1） |
| spectrum_weight | 0.40 | スペクトル成分の重み（0〜1）。2つの重みの合計は1 |
| bandpass_enabled | false | 比較時の帯域処理を有効化 |
| bandpass_low_hz | 350.0 | 帯域の下端 |
| bandpass_high_hz | 3000.0 | 帯域の上端 |

帯域は 0 < 下端 < 上端 < 内部レートの半分を満たす必要があります。
イベントとサンプルの両方に同じ帯域処理を適用します。
帯域処理は元 WAV、保存済み sample.wav、Replay の音声変換・保存結果を変更しません。
waveform_weight / spectrum_weight は Phase 5 から動作します。confidence 閾値も Phase 6 から動作します。

## 実装と確認範囲

チャンネル平均 → DC 除去 → 内部レート変換 → peak 0.95 正規化を共通化し、
保存した native 元音声も現在の設定で前処理します。必要に応じ帯域処理と再正規化を追加します。
FFT 相関で時刻ずれを探索し、各重複区間の平均とエネルギーで正規化します。
極性反転にも対応する絶対相関です。短い端部だけの偶然一致を避けるため、
短い方の80%以上の長さと中心化後エネルギーの50%以上を含む重複だけを比較します。
比較用 mono 音声とスペクトル特徴の合計は128 MiB以下に制限します。

Qt に依存しない OracleClassifier は oracle / best_score / second_candidate /
second_score と全候補・各サンプルの結果を返します。Replay の専用ワーカーで比較し、
取得 callback に分類処理を追加していません。

提供された48 kHz stereo WAV は全区間同士で **7/7**、最初の1秒をテンプレート、
1.00〜1.75秒を比較音声にした重ならない区間でも **7/7** の第1候補が正解でした。
Phase 4（波形のみ）の後者の第1スコアは0.237〜0.456で、波形の完全一致ではありません。
両確認とも同じ7録音を使っており、独立した録音・雑音下・実戦の精度評価ではありません。
実 Windows 画面でも両条件の順位、通常サイズ・760×600の表示、終了を確認しました。
詳細は [受け入れ確認](acceptance.md) を参照してください。

## Phase 5 — スペクトルと複合スコア

順位は次の複合スコアで決めます。第1・第2候補の表示も複合スコアです。

```text
combined = waveform × waveform_weight + spectrum × spectrum_weight
```

「スコア内訳を表示」を選ぶと、同じ表に波形・スペクトルの列を追加します。
各サンプルにも3種類のスコアを保持します。複数サンプルの最高値・上位N件平均は、
複合スコアで選んだ同じサンプル集合の波形・スペクトルを使用します。
波形1.0 / スペクトル0.0なら、Phase 4 の順位と同じ比較ができます。

スペクトルは Hann 窓128 ms・hop10 msの STFT から生成します。
100〜8000 Hz（内部レートの Nyquist 以下）の基音・倍音を含む帯域を保持し、
全区間と8つの時間区間の平均パワーを使います。
周波数方向の局所背景を除き、peak 正規化した非負の log magnitude に変換します。
cosine similarity を使い、全区間の比較25%と、減衰の変化に対応する
各時間区間の最も近いテンプレート区間との比較75%を合成します。
Oracle の音名や位置に依存した特別な判定はありません。

スペクトルは保存済み native 音声から比較時に再生成し、メモリに保持します。
既存サンプルのファイルや metadata を書き換えません。
STFT は32フレームずつ計算してキャンセルを確認し、
比較音声とスペクトルの合計を128 MiB以下に制限します。
取得 callback では STFT・分類を行いません。

### Replay Dataset で比較する

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_phase5.py
```

[評価条件](../tests/datasets/phase5-oracles.json) と提供 A.wav〜G.wav を使い、
生成した WAV を読み直して画面と同じ Analyzer / classifier に通します。
結果・各候補の3スコア・元音声の checksum は
.runtime/phase5-evaluation/report.json、生成 WAV はその queries/ に保存します。

| 条件 | ケース数 | Phase 4 波形のみ | Phase 5 複合 |
|---|---:|---:|---:|
| 静かな全区間 / 重ならない前後区間 | 14 | 14正解 | 14正解 |
| 白色ノイズ SNR 10 dB | 21 | 21正解 | 21正解 |
| 白色ノイズ SNR 0 dB | 21 | 21正解 | 21正解 |
| 白色ノイズ SNR -10 dB | 21 | 21正解 | 21正解 |
| 白色ノイズ SNR -20 dB | 21 | 20正解 | 21正解 |

ノイズは seed17 / 37 / 73（ファイル番号を加算）で固定します。
登録0〜1秒、比較1.00〜1.75秒の区間を使用し、比較区間の RMS に対して
指定 SNR の白色ノイズを加えます。
98ケースで悪化0件、改善1件でした。-20 dB / seed17 の B.wav は
波形のみの L3 から正解の R3 へ変わりました。
全体平均の改善は、この固定した同じ7録音と人工ノイズでの結果です。
別録音・会話・ゲーム効果音・実戦の認識率や誤検出率は未評価です。
上記の正解数は候補順位の評価です。Phase 6 の confidence 判定後に採用する数とは区別します。
初期閾値では重ならない前後区間の7件をすべて unknown にします。
有限音声のイベント切り出し・PASS分離は Phase 7 に追加しました。
取得を続けながらのラウンド追跡と2回照合・再構成は後続 Phase です。
