# Destiny 2 VoG Oracle Assistant / EchoSeer

Vault of Glass のゲーム音声からオラクルを識別し、順序と信頼度を表示する
Windows アプリケーションを、指示書の Phase 順に開発しています。

**Phase 1（音声取得）まで実装済みです。**
WASAPI Loopback / 通常録音入力の選択、Start / Stop、リアルタイム音量表示、
リングバッファ、設定保存・復旧、診断ログが動作します。
オラクル認識、Calibration、WAV Replay、Overlay、配布 EXE は後続 Phase で実装します。

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
3. 表示された Sample Rate / Channels を確認し、Start を押します。
4. LIVE になり、音声に応じて RMS / Peak と音量メーターが変化することを確認します。
5. Stop で停止します。停止後にデバイス変更・再検索ができます。

起動だけでは音声取得を開始しません。
小さいウィンドウでは Live の内容をスクロールできます。
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
内部 48000 Hz への変換・モノラル化・正規化・WAV 保存は Phase 2 の作業です。

Phase 1 は音声ファイルや認識イベントを生成しません。
Stop 後の最後のバッファはメモリ内に残り、次の開始時に置き換えます。
アプリ終了時に破棄されます。
メーターにはバッファ長、取得欠落・オーバーフロー回数、クリッピングを表示します。

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
- 後続 Phase 用の認識・録音設定は現在の音声取得に影響しません。

設定例は [config.example.json](docs/config.example.json) を参照してください。
認識閾値やタイミングは調整前の仮値です。

## プロジェクト構造

```text
main.py
app/             起動処理、保存先
config/          初期値、schema、設定保存・復旧
logging_ext/     診断ログ、JSONL イベントログ基盤
audio/           デバイス列挙・解決、取得、Queue、リングバッファ、音量
ui/              Live 入力欄、音量表示、Qt Signal/Slot、ダークテーマ
dsp/             前処理・特徴量（後続 Phase）
templates/       複数テンプレート管理（Phase 3）
detector/        検出・分類・信頼度（Phase 4 以降）
encounter/       遭遇・Pass 判定（Phase 7 以降）
replay/          WAV 解析・評価（後続 Phase）
tests/           Unit / 音声ワーカー / GUI テスト、音声・Dataset 用フォルダー
scripts/         セットアップ・検証
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

Python 3.14.6 で **90 passed**、依存関係の確認も成功しています。
自動テストは実デバイス・ゲーム・VoiceMeeter の起動を必要としません。
GUI は offscreen、音声は実スレッドで動くデバイス代替を用いて確認します。

実 Windows ウィンドウでも、既定再生先の loopback 音量表示、
VoiceMeeter B1 の 48000 / 44100 Hz ストリーム取得、
計 15 回の反復 Start / Stop と入力エラー後の再試行を確認しました。
表示修正後に既定 loopback の Start / Stop を追加で 1 回確認しています。
実機確認の詳細と制限は [受け入れ確認](docs/acceptance.md) に記載しています。
VoiceMeeter B1 へのゲーム音経路、物理的な切断・再接続、実 Oracle 認識は未検証です。

## Troubleshooting

- **Python が見つからない**: Python 3.14 x64 を用意してください。
- **Import error**: setup.ps1 を実行し、このプロジェクトの .venv で起動してください。
- **設定の警告**: 保存先のアクセス権・空き容量・退避ファイルを確認してください。
- **LIVE でも無音**: ゲームの出力先、VoiceMeeter のバス、ミュートを確認してください。
- **デバイスを開始できない**: 再検索し、Windows / VoiceMeeter の音声設定を確認してください。
- **オラクルを認識しない**: Phase 1 は音声取得までの実装です。
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
[Prophet](https://github.com/PyrexPi/prophet) は参照先として確認済みで、
ソース・テンプレート・アセットは転載していません。
既存 [LICENSE.txt](LICENSE.txt) を適用します。
