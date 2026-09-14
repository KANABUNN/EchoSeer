# Destiny 2 VoG Oracle Assistant / EchoSeer

Vault of Glass のゲーム音声からオラクルを識別し、順序と信頼度を表示する
Windows アプリケーションを、指示書の Phase 順に開発しています。

**Phase 15の記録・再評価機能まで実装済みです。実戦データに基づく調整は未実施です。**
WASAPI Loopback / 通常録音入力の選択、Start / Stop、リアルタイム音量表示、
リングバッファ、WAV 読み書き、共通前処理、簡易 Replay、設定保存・復旧、診断ログが動作します。
Calibration で Oracle ごとの複数サンプル登録・録音・Import・Listen・削除・復元ができます。
Replay と Live の直近区間で、波形とスペクトルによる Oracle 候補・複合スコア・全7種類の順位を表示します。
HIGH / MEDIUM / LOW / REJECTED と unknown、Live の重複抑制、認識ログ・不確かな音声の保存切替を実装しました。
Replay WAV と Live の保持音声からイベントを切り出し、Round 1〜5 の3〜7個を PASS1 / PASS2 に独立保存します。
完全一致のCONFIRMED、不一致位置、INFERRED / CHECK、上位候補による重複なしの順序再構成に対応します。
詳しくは [信頼度](docs/confidence.md) / [順序解析](docs/sequence.md) / [2回照合と推定](docs/verification.md) を確認してください。
Liveは取得中に両PASSを照合し、Round・個数・確定順／推定順と番号付きOracle Mapを表示します。
枠なし・最前面・クリック透過のOverlayは順序／マップを切り替え、位置・不透明度・倍率を保存します。
CalibrationのQuality Checkで保存した元音声を再確認し、7種類の登録状況と表示名をGUIから管理できます。
手順は [Live / Overlay](docs/live-overlay.md) / [Calibration](docs/calibration.md) を確認してください。
ReplayのTimelineで各音の時刻・理由とスコア内訳を確認し、Datasetの一括評価・比較・混同行列CSV保存ができます。
Live / 保存ログから固定音声を開き、手動の正解・分類を付けて収集できます。採用音声の収集は初期OFFです。
手順は [Replay / Debug](docs/replay-debug.md) / [Dataset評価](docs/dataset-evaluation.md) / [実戦記録](docs/gameplay-review.md)。
後続PhaseはHotkey・UX、配布EXEです。

ゲームへの操作送信、メモリ読み取り、DLL 注入、ゲームファイルへのアクセス、
自動入力、Bungie API、クラウド認識、テレメトリーは実装しません。

## 必要環境

- Windows 10 / 11 x64
- Python 3.14 x64（検証環境: 3.14.6）
- PyAudioWPatch 0.2.12.8 / NumPy 2.5.3 / SciPy 1.18.1 / PySide6 6.11.2
- pytest 9.1.1 / PyInstaller 6.22.3（開発・ビルド用）

対応ホイールのインストール・import を実環境で確認しています。
通常使用時に Python が不要な EXE は Phase 17 で作成します。

## インストール・起動

このフォルダーで PowerShell を開きます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe main.py
```

環境を有効化している場合は `python main.py` でも起動できます。
ウィンドウの「ファイル → 終了」または × で終了します。

診断用オプション:

```powershell
.\.venv\Scripts\python.exe main.py --debug
.\.venv\Scripts\python.exe main.py --data-dir .\.runtime\manual
```

`--debug` は詳細な診断ログをコンソールにも出力します。
`--data-dir` は設定・ログの保存先を指定します。

## Windows 再生音を取り込む

1. Live の入力方式で「再生デバイス（WASAPI Loopback）」を選びます。
2. ゲームや確認音の出力先と同じデバイスを選びます。初回は Windows の既定再生先を選択します。
3. デバイス欄にマウスを合わせてレートとチャンネル数を確認し、Start を押します。
4. LIVE になり、音声に応じて RMS / Peak と音量メーターが変化することを確認します。
5. Stop で停止します。停止後にデバイス変更・再検索ができます。

起動だけでは音声取得を開始しません。
小さいウィンドウでは Live / Replay / Calibration の内容をスクロールできます。
長いデバイス名は項目にマウスを合わせて確認できます。

## VoiceMeeter の音を取り込む

入力方式を「録音デバイス（VoiceMeeter など）」に変更し、
ゲーム音を送っている VoiceMeeter の録音出力を選択します。
名称はバージョンにより `VoiceMeeter Output`、`VoiceMeeter AUX Output`、
`Voicemeeter Out B1`、`Voicemeeter Out B2` などになります。

VoiceMeeter 側でゲーム音を対象バスへ送る必要があります。
LIVE でも音量がゼロなら、選択したバスへの経路・ミュートを確認してください。
ゲーム音が Windows の既定 `Voicemeeter Input` へ出ている場合は、
その再生デバイスの WASAPI Loopback でも確認できます。

同名のデバイスが複数ある場合、末尾の `[Windows WASAPI]` / `[MME]` などで区別します。
取得には選択したデバイスの native sample rate / チャンネル数を使用します。

## 切断・入力開始エラー

保存したデバイスが見つからない場合は再選択を案内します。
別の入力を自動選択して取得を開始することはありません。

取得中のストリーム停止・エラーは `Audio device lost` と表示し、
約 2 秒間隔で同じ名称・host API・loopback のデバイスへ再接続を試みます。
復帰時には現在の sample rate / チャンネル数でバッファを作り直します。
無音の loopback は正常な LIVE 状態として扱います。

開始に失敗した場合は画面に案内を表示します。
Windows / VoiceMeeter の出力先・音声設定を確認して再検索し、再試行してください。
別の利用可能な入力を選択することもできます。詳細は診断ログへ記録します。

## バッファと保存

取得した float32 音声をメモリ内のリングバッファへ保持します。
長さは初期 10 秒、設定で 5～30 秒です。満杯になると古いフレームを置き換えます。
バッファは入力の native rate / チャンネル数を保持します。

- 「直近音声を Replay へ」は処理開始時のバッファのコピーを共通 Analyzer へ渡します。
- 「直近音声を WAV 保存」は元の native 形式を float32 WAV として保存します。
- Replay で Analyze を押し直すと、取得が進んでも同じコピーを再解析します。
- Replay の「WAV 保存」は変換済みモノラル音声を保存します。

WAV は保存・サンプル録音登録・Import で生成します。
「不確かな音声を保存」がONなら、不確かなイベントの短いWAVも保存します。自動のフルセッション録音はありません。
Stop 後の最後のバッファはメモリ内に残り、次の取得開始時に置き換えます。
アプリ終了時に破棄されます。
停止後も保持バッファの長さと保存操作を利用でき、現在の音量メーターはゼロになります。
取得中はバッファ長、欠落・オーバーフロー回数、クリッピングを表示します。

## Replay / 音声変換（Phase 2）

1. Replay タブの「WAV を開く」でファイルを選びます。
2. Analyze を押すと、入力音声と解析後のレート・チャンネル・サンプル数・長さ・RMS / Peak を表示します。
3. 同じ WAV に対して再び Analyze を押すと、サンプル列が前回と一致したことを表示します。
4. 「WAV 保存」で解析後の音声を任意の場所へ保存できます。float32 または PCM 16 bit を選びます。

共通 Analyzer は、チャンネルの単純平均 → DC 成分除去 →
内部レートへの SciPy polyphase 変換 → peak 0.95 への正規化を行います。
内部レートは初期 48000 Hz で、audio.internal_sample_rate の設定を使用します。
peak が 1e-6 以下の音声はゼロとして扱い、微小ノイズを増幅しません。
左右が逆位相なら平均により打ち消されます。元の音声は別に保持します。

Live / Replay のどちらも一つの完全な入力区間に前処理を適用します。
callback の境界ごとに変換・正規化することはありません。
「順序を解析」では native 音声から発音区間を切り出し、各区間に同じ前処理を適用します。
Start中の連続認識は専用Live workerで行い、未読音声だけを処理してRoundを追跡します。

読込対応は little-endian RIFF / WAVE の PCM 8 / 16 / 24 / 32 bit、
IEEE float 32 / 64 bit、および対応 PCM / float の WAVE_FORMAT_EXTENSIBLE です。
圧縮 WAV・RIFX・RF64 はこの段階では対象外です。
入力ファイルは 128 MiB 以下、展開後の native 音声と解析後音声はそれぞれ 256 MiB 以下に制限します。
長い録音は短い区間に分けてください。空・途中で切れたデータや不正な数値には画面で案内します。

ファイルの読み込み・解析・保存は、取得とは別のワーカーで行います。
再解析に失敗した場合は古い結果の保存を無効にします。
保存は一時ファイルを書き終えてから置換し、途中の失敗・キャンセルでは既存ファイルを保護します。

## Calibration / テンプレート管理（Phase 3・12）

1. Calibration で Oracle を選び、Import WAV で複数ファイルを登録します。
2. 録音する場合は Live を Start し、Calibration で時間を選んで Record Sample を押します。
3. Quality Checkで保存した元音声の品質を再確認し、Listen・再生停止・Delete・削除を戻すを使えます。

開始後の音声を指定時間だけ取得し、登録は共通前処理済みの mono 音声と元の native 音声を保存します。
無音は登録を拒否し、短さ・長さ・小さな RMS・クリッピングを警告します。
途中の録音中断や音声欠落では部分サンプルを登録しません。
Oracle ごとの複数サンプルはアプリの再起動後も残ります。
詳しい操作と保存形式は [サンプル登録手順](docs/calibration.md) を参照してください。

## Oracle 認識（Phase 4–5）

1. Calibration で7種類の Oracle サンプルを登録します。
2. Replay で1つの Oracle を含む20 ms〜10秒の WAV を開き、Analyze を押します。
3. 第1・第2候補と複合スコア、差、7種類の順位を確認します。スコア内訳で波形・スペクトルの値も表示できます。
4. Live の「直近音声を Replay へ」でも同じ比較を行います。

提供された A.wav〜G.wav は、対応画像に従って通常の保存先へ登録済みです。
3スコアは類似度であり、正解の確率ではありません。初期重みは波形60%・スペクトル40%です。
無音・未登録・同点などでは確定しません。1ラウンドの順序解析は下記の別操作で行います。
詳しい操作・対応表・最高値または上位N件平均・帯域設定は
[Oracle 認識の操作説明](docs/recognition.md) を参照してください。

## 順序解析（Phase 7）

1. Replay で PASS1 と PASS2 を含む1ラウンドの WAV を開きます。
2. Round を選び、「順序を解析」を押します。期待個数は Round 1〜5 の3 / 4 / 5 / 6 / 7個です。
3. 2つの PASS、各音の信頼度、現在の状態を確認します。
4. Next Round で次の期待個数を選び、次のラウンドの WAV を開きます。Reset は Round 1 に戻します。

Live の「直近音声の順序を解析」も、処理開始時にコピーした保持音声を使います。
初期バッファは10秒なので、両PASSを含む長さが必要です。設定可能な上限は30秒です。
より長いラウンドは Replay WAV を使ってください。順序入力は120秒以内です。

両PASSを照合し、採用Oracleの完全一致・期待個数・重複なしを満たすと CONFIRMED と確定順を表示します。
補正した順序は INFERRED（要確認）、決められない位置は MISMATCH / CHECK です。不一致・重複と元の候補を残します。
unknown は位置を残し、欠落や間隔不足は UNCERTAIN にします。
提供された個別の実 Oracle 音声を2回提示に組み、全5ラウンドの分離を確認しました。
実戦での提示間隔・音の重なりを含む通し録音は未検証です。

## 設定・ログ保存場所

```text
%LOCALAPPDATA%\OracleAssistant\
  config.json
  templates\
  logs\
    application.log
```

`LOCALAPPDATA` がなければ `APPDATA`、両方なければユーザーの
`AppData\Local` を使います。アプリ本体の保存先には書き込みません。

- schema version 1。型・範囲・項目間の関係を検証します。
- デバイスの入力方式・host API・名称・loopback と形式の参考情報を保存します。device index は保存しません。
- 一時ファイルから置換して保存し、失敗時に既存設定を保護します。
- 破損・不正な設定は `config.corrupt-日時-識別子.json` へ退避して初期値で復旧します。
- 退避できなければ原本を保持し、読み込み・保存のエラーを GUI に表示します。
- 診断ログは 5 MiB × 最大 4 ファイルです。
- 比較には waveform_weight / spectrum_weight / template_aggregation / top_n / bandpass を使用します。
- confidence 閾値は採用判定、detection_threshold / sequence.pass_gap / event_timeout は順序解析に使用します。
- lockout_duration / silence_duration は FSM の確定後遷移で使用します。
- candidate_top_n / max_corrections / inference_margin / reconstruction_margin は候補保存・推定に使用します。
- 認識ログがONなら logs/sessions/ にイベントと独立したPASSのJSONLを保存します。

設定例は [config.example.json](docs/config.example.json) を参照してください。
認識閾値やタイミングは調整前の仮値です。

## プロジェクト構造

```text
main.py
app/             起動処理、保存先
config/          初期値、schema、設定保存・復旧
logging_ext/     診断ログ、JSONL イベントログ基盤
audio/           取得、リングバッファ、WAV 入出力、音声ソース、レート変換
ui/              Live / Replay / Calibration、音量表示、Qt Signal/Slot、処理ワーカー
dsp/             mono・DC 除去・正規化・帯域処理・正規化相関・STFT特徴
templates/       複数テンプレート管理・native品質再確認
detector/        Oracle 波形/スペクトル順位・複合スコア・信頼度判定、有限／連続音声のイベント切り出し
encounter/       OracleId、期待個数・Sequence FSM・独立PASS・2回照合・候補再構成
replay/          Live / WAV 共通 Analyzer、1ラウンドの順序解析
tests/           Unit / 音声ワーカー / GUI テスト、音声・Dataset 用フォルダー
scripts/         セットアップ・検証・対応表によるサンプル登録
build/           配布ビルド用フォルダー
docs/            原指示書、設定例、開発・受け入れ記録
```

音声 callback は NumPy のビューを作って上限付き Queue へ渡します。
音量計算とリングバッファ書き込みは取得ワーカーが行い、
GUI は Qt の Signal/Slot 経由でメインスレッドから更新します。
停止・終了時にはストリーム・PortAudio・スレッドを解放します。
後続 Phase の予約パッケージには説明のみを置いています。

## テスト

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

直接実行する場合:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .runtime/pytest-tmp
.\.venv\Scripts\python.exe -m pip check
```

Python 3.14.6 で **674 passed**、依存関係の確認も成功しています。
自動テストは実デバイス・ゲーム・VoiceMeeter の起動を必要としません。
うち20件は任意のローカル samples/A.wav〜G.wav を使い、音声がない環境では skip します。
GUI は offscreen、音声は実スレッドで動くデバイス代替を用いて確認します。

実 Windows ウィンドウでも、既定再生先の loopback 音量表示、
VoiceMeeter B1 の 48000 / 44100 Hz ストリーム取得、
計 15 回の反復 Start / Stop と入力エラー後の再試行を確認しました。
表示修正後に既定 loopback の Start / Stop を追加で 1 回確認しています。
Phase 2 では実 loopback を WAV 保存し、Live と Replay の解析列が完全に一致しました。
録音 WAV を 5 回、PCM16 stereo 44100 Hz WAV を 3 回再解析して一致を確認しています。
解析後の float32 / PCM16 保存・再読込、破損 WAV の案内・再試行も確認しています。
Phase 3 では 7 種 × 2 件の WAV を登録し、native 48000 Hz / 2 ch の 0.5 秒を録音しました。
Listen・停止・削除・復元、再起動後の 15 件保持、再生中の終了と全ワーカー解放も確認しています。
実機確認の詳細と制限は [受け入れ確認](docs/acceptance.md) に記載しています。
Phase 4 では提供 WAV の全区間と、登録・比較を重ならない前後の区間に分けた条件で各7/7を確認しました。
実 Windows 画面でも順位表・第1/第2候補、再起動後の登録7件、終了時の全ワーカー解放を確認しています。
Phase 5 の98ケースでは静かな14/14を維持し、固定白色ノイズ付き音声を83/84から84/84へ改善しました。
再評価は scripts/evaluate_phase5.py、画面のスコア内訳も実 Windows で確認しています。
Phase 6 は曖昧な候補を unknown とし、Live の重複除外と Replay の反復判定を確認しました。
提供音声の自己一致は7/7 HIGH、別区間は初期閾値で7/7 unknown です。
Phase 7 は個別の提供録音を2回提示に組んだ全5ラウンドと、欠落・不一致・ノイズ・途中切れ・間隔不足の計10ケースを確認しました。
実 Windows 画面で7個の表示、有限Live音声とReplayコピーの一致、終了時の全ワーカー解放も確認しました。
Phase 8–9 は完全一致の確定、不一致位置の特定、1箇所のLOW・重複の推定を確認しました。
Phase 10–12は連続LiveとReplayの7ケース、Windowsで提供7音声のGUI登録・品質確認・Live確定／不一致を確認しました。
Windowsのテスト用borderless画面でクリック透過・非アクティブ表示・位置調整、100% / 125% / 150%表示、全4worker解放を確認しました。
Phase 13〜15は提供音声の15ケースで一括評価・設定比較、WindowsのTimeline・手動正解付け・ログReplay・Live固定コピーを確認しました。
Destiny 2実行中のOverlay、物理的な複数モニター変更、長時間実戦の性能は未確認です。
提供音声に分類誤りを注入した8ケースは、推定・同点・弱い根拠を確定表示にしません。
別録音・会話や効果音を含む実戦の認識率、VoiceMeeter B1 へのゲーム音経路、物理的な切断・再接続は未検証です。

## Troubleshooting

- **Python が見つからない**: Python 3.14 x64 を用意してください。
- **Import error**: setup.ps1 を実行し、このプロジェクトの .venv で起動してください。
- **設定の警告**: 保存先のアクセス権・空き容量・退避ファイルを確認してください。
- **LIVE でも無音**: ゲームの出力先、VoiceMeeter のバス、ミュートを確認してください。
- **デバイスを開始できない**: 再検索し、Windows / VoiceMeeter の音声設定を確認してください。
- **Oracle 候補が出ない**: Calibration の登録数、Replay の区間長・無音・警告を確認してください。複数の音は「順序を解析」で確認してください。
- **WAV を解析できない**: 対応形式・サイズを確認し、元の音声から再出力してください。
- **保存できない**: 保存先のアクセス権・空き容量を確認してください。
- **起動しない / 入力エラー**: --debug の出力と logs/application.log を確認してください。

## アンインストール

開発フォルダーを削除してアプリ本体を除去します。
保存データも不要なら `%LOCALAPPDATA%\OracleAssistant` を削除してください。
サンプル・ログが必要なら先に保管してください。

## 開発状況・参照元

[原指示書](docs/implementation-guide.ja.md) の Phase 順で実装しています。
[開発記録](docs/development.md) / [受け入れ確認](docs/acceptance.md)。

[PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) /
[PyAudio API](https://people.csail.mit.edu/hubert/pyaudio/docs/) の仕様を確認しました。
[SciPy WAV](https://docs.scipy.org/doc/scipy/reference/generated/scipy.io.wavfile.read.html) /
[polyphase resampling](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.resample_poly.html)。
[correlate](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.correlate.html) /
[Butterworth](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html) /
[SOS filtering](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html) /
[ShortTimeFFT](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.ShortTimeFFT.html)。
[Prophet](https://github.com/PyrexPi/prophet) は参照先として確認済みで、
ソース・テンプレート・アセットは転載していません。
既存 [LICENSE.txt](LICENSE.txt) を適用します。
