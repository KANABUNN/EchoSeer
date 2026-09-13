# Destiny 2 VoG Oracle Assistant / EchoSeer

Vault of Glass のゲーム音声からオラクルを識別し、順序と信頼度を表示する
Windows アプリケーションを、添付指示書の Phase 順に開発します。

**現在は Phase 0（プロジェクト基盤）まで実装済みです。**
基本ウィンドウ、設定保存・復旧、診断ログ、テスト環境が動作します。
音声取得、オラクル認識、Calibration、Replay、Overlay、配布 EXE は未実装です。

ゲームへの操作送信、メモリ読み取り、DLL 注入、ゲームファイルへのアクセス、
自動入力、Bungie API、クラウド認識、テレメトリーは実装しません。

## 必要環境

- Windows 10 / 11 x64
- Python 3.14 x64（検証環境: 3.14.6）
- Runtime: PyAudioWPatch 0.2.12.8 / NumPy 2.5.3 / SciPy 1.18.1 / PySide6 6.11.2
- Test: pytest 9.1.1 / Build: PyInstaller 6.22.3

上記の対応ホイールを実環境にインストールし、import を確認しています。
[PyAudioWPatch](https://pypi.org/project/PyAudioWPatch/)、
[Qt for Python / PySide6](https://pypi.org/project/PySide6/)。

通常使用時に Python が不要な EXE は Phase 17 で作成します。

## インストール・起動

このフォルダーで PowerShell を開きます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe main.py
```

仮想環境を有効化している場合は `python main.py` でも起動できます。

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

環境の有効化が実行ポリシーで制限されても、最初の直接起動方法を利用できます。
ウィンドウの「ファイル → 終了」または × で終了します。

診断用オプション:

```powershell
.\.venv\Scripts\python.exe main.py --debug
.\.venv\Scripts\python.exe main.py --data-dir .\.runtime\manual
```

`--debug` は詳細な診断ログをコンソールにも出力します。
`--data-dir` は保存先を明示します。既定ではユーザーデータ領域を使用します。

## プロジェクト構造

```text
main.py
app/             起動処理、保存先
config/          初期値、schema、検証、設定保存・復旧
logging_ext/     診断ログ、JSONL イベントログ基盤
ui/              基本 PySide6 Window、ダークテーマ
audio/           音声取得（Phase 1 以降の予約パッケージ）
dsp/             前処理・特徴量（後続 Phase の予約パッケージ）
templates/       複数テンプレート管理（Phase 3）
detector/        検出・分類・信頼度（Phase 4 以降）
encounter/       遭遇・Pass 判定（Phase 7 以降）
replay/          共通 Analyzer による WAV 解析・評価
tests/           Unit / GUI 起動テスト、音声・Dataset 用フォルダー
scripts/         セットアップ・検証
build/           配布ビルド用の予約フォルダー
docs/            原指示書、設定例、開発・受け入れ記録
```

予約パッケージには説明のみを置いています。未実装の処理を成功扱いするコードはありません。
音声解析・遭遇判定は Qt に依存させず、結果を GUI に渡す構成で追加します。

## 設定設計・ログ保存場所

```text
%LOCALAPPDATA%\OracleAssistant\
  config.json
  templates\
  logs\
    application.log
```

`LOCALAPPDATA` がなければ `APPDATA`、両方なければユーザーの
`AppData\Local` を使用します。アプリ本体の保存先には書き込みません。

- `schema_version: 1`。型・範囲・項目間の関係を検証します。
- 未指定項目には初期値を補います。設定例は [config.example.json](docs/config.example.json)。
- 一時ファイルを保存してから置換し、保存失敗時に既存設定を保護します。
- JSON 破損・不正な型・未知の項目やバージョンは
  `config.corrupt-日時-識別子.json` へ退避し、初期設定で起動します。
- 退避できなければ原本を保持します。読み込み・保存のエラーは GUI に表示します。
- デバイス情報は host API、名称、loopback。device index を永続保存しません。
- 内部レート 48000 Hz、バッファ 10 秒（5～30 秒）。
- Oracle の表示名と、マップの正規化座標を別の設定で保持します。
- waveform / spectrum の重みは 0.60 / 0.40。
- フルセッション録音は初期 OFF。不確実音声保存とイベントログは別設定です。
- Phase 0 は音声を取得・保存せず、認識イベントも生成しません。
- 診断ログは 5 MiB × 最大 4 ファイルに制限します。

認識閾値やタイミングの初期値は調整前の仮値です。
実音声による精度検証は後続 Phase で行います。

## 音声デバイス / WASAPI Loopback（Phase 1 で実装予定）

ゲームの出力先と同じ Windows 再生デバイスを選択し、
WASAPI Loopback で取得する機能を追加します。
入力デバイスの native sample rate を確認し、内部レートへ変換します。
Phase 0 にはデバイス選択・Start / Stop・音量メーターはありません。

## VoiceMeeter（Phase 1 で実装予定）

通常入力として `VoiceMeeter Output` / `VoiceMeeter AUX Output` 等を
選べるようにします。実際のデバイスでの確認は未実施です。

## Live Mode（未実装）

Round、Pass 1 / Pass 2、最終順序、信頼度、Oracle Map を表示します。
LOW / REJECTED を無理に確定せず、怪しい順序を「確認必要」と表示します。

## Calibration（未実装）

Oracle ごとに複数 WAV を録音・Import・再生・削除し、品質を確認します。
WAV Import 時にはモノラル化・レート変換・正規化を行います。

## Overlay（未実装）

最前面、透過、クリック透過、位置・サイズ・不透明度の調整、
Sequence / Map モードを追加します。

## Replay（未実装）

WAV を Live と共通の解析処理へ渡し、時刻・候補・信頼度・スコアを表示します。
実録音を Dataset に登録して、誤認識と変更前後の精度を評価します。

## テスト

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
```

直接実行する場合:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .runtime/pytest-tmp
.\.venv\Scripts\python.exe -m pip check
```

58 テストを Python 3.14.6 で確認しています。
GUI 起動テストは Qt offscreen の別プロセスで実行します。
実 Windows ウィンドウの起動・終了も別途確認しています。
音声デバイス・ゲーム・VoiceMeeter はテストに不要です。
実音声の認識やゲーム上の Overlay は、これらの確認とは別の受け入れ項目です。

## Troubleshooting

- **Python が見つからない**: Python 3.14 x64 を用意し、`py -3.14 --version` を確認してください。
- **Import error**: `scripts\setup.ps1` を実行し、このプロジェクトの `.venv\Scripts\python.exe` で起動してください。
- **設定の警告**: 保存先のアクセス権・空き容量・退避ファイルを確認してください。
- **認識しない / 音声を選べない**: 現在の Phase 0 では音声取得・認識は未実装です。
- **起動しない**: `main.py --debug` の出力と `logs\application.log` を確認してください。

## アンインストール

開発フォルダーを削除してアプリ本体を除去します。
保存データも不要なら `%LOCALAPPDATA%\OracleAssistant` を削除してください。
後続 Phase で追加したサンプル・ログが必要なら、先に保管してください。

## 開発状況・参照元

[原指示書](docs/implementation-guide.ja.md) の第63節に従い、初回は Phase 0 のみを実装しました。
[開発記録](docs/development.md) / [受け入れ確認](docs/acceptance.md)。

既存 [LICENSE.txt](LICENSE.txt) を適用します。
[Prophet](https://github.com/PyrexPi/prophet) は参照先として確認しました。
Phase 0 では Prophet のソースや音声サンプルを取り込んでいません。
