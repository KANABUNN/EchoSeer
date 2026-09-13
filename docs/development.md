# 開発記録

## Phase 0 — 完了（2026-09-13）

原指示書第63節「まずPhase 0のみ実行すること」に従い、初回は基盤を実装しました。

### 実装

- 既存 Git repository と LICENSE.txt を保持。
- Python 3.14 venv、requirements.txt / requirements-dev.txt / requirements-build.txt、pyproject.toml。
- 音声取得、DSP、分類、遭遇判定、テンプレート、Replay、GUI を分けた構成。
- schema version 1、型・範囲検証、設定の atomic save と破損退避。
- ユーザーデータ保存先、ローテーションする診断ログ、JSONL イベントログ基盤。
- main.py、PySide6 基本ウィンドウ、ダークテーマ。
- 設定・パス・ログの Unit Test、別プロセスでの起動・終了・再起動テスト。

### 変更ファイル

app/、config/、logging_ext/、ui/、tests/、scripts/、
main.py、pyproject.toml、requirements*.txt、README.md、docs/、
および後続 Phase 用の予約パッケージと build/。

### 実行した確認

- Python 3.14.6 x64、指定ライブラリのインストール・import。
- pytest: **58 passed**。
- main.py --data-dir .runtime/native-smoke --smoke-test:
  実 Windows ウィンドウを表示して終了、exit code 0。
- pip check: No broken requirements found。
- 設定の保存・再読込、カスタム表示名・デバイス情報の保持。
- JSON 破損・不正値の退避、初期値での起動。
- 退避失敗と atomic replace 失敗で原本を保持。
- 診断ログの再設定・終了時の handler 解放、並列 JSONL 書き込み。

初回検証で見つかった検証用フォルダーの不足と、極端な整数値による
数値変換エラーを修正してから、全テストを再実行しました。

### 既知の制限

音声取得、7 Oracle 分類、Pass 分離、Sequence、Overlay は未実装です。
認識閾値や timing は実録音で未調整です。
実ゲーム・VoiceMeeter・複数モニター・配布 EXE の確認は後続 Phase の作業です。

### 次の段階

Phase 1 — PyAudioWPatch による WASAPI / loopback / 通常入力の列挙、
デバイス選択、capture thread、ring buffer、音量メーター、
Start / Stop の反復と実音声入力の確認。

### 最終確認

- README に記載した scripts/setup.ps1: exit code 0。
- scripts/verify.ps1: pip check 成功、58 passed（4.18 秒）、exit code 0。
- pyproject.toml の editable package 作成・登録に成功。
- 登録した GUI launcher をソース直下以外から起動し、終了ログを確認。
- Qt platform は windows。実ウィンドウの画像で表示を確認。
- 設定例を schema と照合し、原指示書のコピーが byte 単位で一致することを確認。
- git diff --check と git diff --cached --check: 成功。
- 新規 39 ファイルも個別の diff --no-index --check で空白エラーなし。
- 作業中に既存 LICENSE.txt が MIT の内容に更新されていたため、
  pyproject の license 表記を MIT に合わせた。LICENSE.txt 自体は編集していない。
- Git commit / push は未実施。既存の LICENSE.txt の staged 変更を保持。

## Phase 1 — 音声取得の実装完了（2026-09-13）

ユーザーの「続いて，Phase1の実装をお願いする．」に従って、音声取得までを追加しました。
Oracle 認識は原指示書の Phase 1 に従い、後続段階へ残しています。

### 実装・変更ファイル

- audio/backend.py、wasapi.py、input_device.py: native 形式の入力を列挙・取得。
- audio/device_manager.py: 名称・host API・loopback で保存情報を解決。index の変更を許容。
- audio/capture.py: PortAudio callback、float32 ビュー、上限付き Queue、欠落・overflow 計数。
- audio/ring_buffer.py、level.py: native multichannel の直近音声保持、RMS / Peak / dBFS / clipping。
- audio/service.py: 一つの取得ワーカーが列挙・開始・停止・再接続を直列処理し、資源を解放。
- ui/audio_controller.py、live_page.py、main_window.py、theme.py: デバイス欄、Start / Stop、LIVE・音量・バッファ表示、Qt Signal/Slot、スクロール。
- app/application.py: 読み込んだ設定を画面へ渡し、終了時に取得ワーカーを閉じる。
- tests/fakes_audio.py、unit/test_audio.py、integration/test_audio_service.py、integration/test_live_ui.py、tests/__init__.py。
- audio/__init__.py、README.md、docs/acceptance.md、docs/development.md。

### 設計上の境界

callback 内で RMS・DSP・GUI 更新を行わず、NumPy のビューを Queue へ渡します。
Queue は 50 block（約 1 秒）を上限とし、満杯なら古い block を捨てて計数します。
取得ワーカーがリングバッファへ書き込み、メーターは最大約 20 Hz で通知します。
欠落・overflow 後は不連続な音声を連結しないようバッファをリセットします。

リングバッファは設定された 5～30 秒、native rate / channels の float32 です。
入力レートを無理に 48000 Hz に固定しません。
mono化・resampling・normalization・WAV 読み書きは Phase 2 で共通化します。
Phase 1 はメモリ内音声だけを保持し、録音ファイル・認識イベントを生成しません。

起動時は列挙だけを行い、Start で取得します。
Stop と終了で stream / PortAudio を解放し、終了時に callback / worker が残らない構成です。
開始処理が待機していても終了操作は GUI を止めず、開始待ちが解消してから閉じます。
取得中の入力停止では Audio device lost を通知し、同じデバイスへ約 2 秒間隔で再試行します。
デバイス不在時に別入力へ自動切替せず、無音の loopback は正常として扱います。

### テスト結果

- Python 3.14.6 / PyAudioWPatch 0.2.12.8 / PySide6 6.11.2。
- scripts/verify.ps1: pip check 成功、**90 passed（9.34 秒）**、exit code 0。
- 既存 Phase 0 の全 58 件を含めて成功。Phase 1 は 32 件追加。
- native 44100 / 48000 / 192000 Hz × 1 / 2 / 8 ch の指定、Queue 上限、バッファ折返し・コピーを確認。
- 実 callback スレッドを使う代替デバイスで反復開始・停止、切断後の index / rate 変更、自動再接続を確認。
- Qt メインスレッド更新、設定保存、保存入力不在の案内、開始失敗・再試行を確認。
- 開始待ち中の終了は 0.1 秒未満で GUI に戻り、終了要求後に stream を開始せず閉じる。
- 致命的な列挙エラーでも画面案内を残して資源を解放。
- 小さい画面ではスクロールし、ボタン・デバイス欄の文字を保つ。

実機では既定 Windows 再生先の WASAPI loopback、VoiceMeeter B1 の
WASAPI 48000 Hz / MME 44100 Hz で、計 15 回の Start / Stop が成功しました。
既定 loopback は実再生音に RMS / Peak / メーターが反応し、
利用できない入力のエラーから再選択して取得を再開できました。
全反復で欠落・overflow は 0、終了時のワーカー解放も確認しています。
表示修正後に既定 loopback を追加で 1 回取得し、実ウィンドウと最小サイズの画像を確認しました。

実機検証の測定値・画面画像は .runtime/ 以下に置いており、Git の対象外です。
音声サンプルは保存していません。

### 再現して修正した問題

- Queue サイズ 0 が上限なしになるため、正の上限を必須にした。
- 開始待ち中に終了した後、stream を開始しないよう開始直前に終了要求を確認する。
- 致命的なワーカー異常の案内が終了通知で消える問題を修正。
- 実機の Errno -9996 を、画面上の音声設定確認・再検索・再試行の案内へ変換。詳細はログに残す。
- メーター下部の欠落表示の不要な折返しと、小さいウィンドウで操作欄が潰れる表示を修正。

### 既知の制限・未確認事項

VoiceMeeter B1 はフレームを取得できましたが、現在のルーティングで確認音が届かず、
B1 へのゲーム音を用いた実音量の確認は未完了です。既存 VoiceMeeter 設定は変更していません。

G431 の 48000 Hz / 8 ch と Realtek の 192000 Hz / 2 ch は、
形式照会が成功しても実開始で Errno -9996 になりました。
G431 の 2 ch 指定でも同様で、原因をチャンネル数だけに断定していません。
これらの実取得は未確認です。エラー表示・資源解放・別入力の再試行は確認済みです。

物理的な抜き差し、Destiny 2 実行中の長時間取得、負荷測定は未実施です。
切断・レート変更からの復帰は自動テストによる確認です。
Oracle 認識、WAV、Calibration、Replay、Overlay、EXE は未実装です。

### 次の段階

Phase 2 — WAV 読み書き、resampling、mono化、normalization、
ring buffer dump、LiveSource / WaveFileSource の共通音声基盤。

## Phase 2 — Replay および音声基盤の実装完了（2026-09-14）

ユーザーの「Phase1の実装を確認した．次に移ってほしい．」に従い Phase 2 を実装しました。

### 実装・変更ファイル

- audio/data.py、operations.py: owned read-only float32 音声区間と協調キャンセル。
- audio/waveio.py: WAV ヘッダー検証、PCM / IEEE float 読込、atomic float32 / PCM16 保存。
- audio/resampler.py、dsp/preprocess.py、normalization.py: 共通前処理。
- audio/sources.py: AudioSource、LiveSource、WaveFileSource、固定コピーを読む ClipSource。
- audio/ring_buffer.py: snapshot をロック内でコピーし、WAV dump はロック外で書く。
- replay/analyzer.py: native 音声、変換済み音声、level、notice、SHA-256 を返す共通 Analyzer。
- ui/operation_controller.py、replay_page.py: 別ワーカーのファイル処理・解析、簡易 Replay GUI。
- ui/live_page.py、main_window.py、app/application.py: Live の保存・Replay 送信、結果表示、両ワーカーの終了。
- audio/service.py: Stop / device lost 後も保持バッファの長さを正しく表示する。
- audio/、dsp/、replay/ の __init__.py 説明、tests/unit/test_waveio.py、test_preprocess.py、
  tests/integration/test_replay_ui.py、既存 GUI テストの cleanup、README と受け入れ記録。

### 前処理と保存の境界

mono は全チャンネルの単純平均。完全な入力区間ごとに DC 平均を除去し、
SciPy resample_poly の Kaiser 5.0 / zero padding を使って設定された内部レートへ変換します。
最後に peak 0.95 に正規化します。peak 1e-6 以下は無音とし、微小音を増幅しません。
入力の多チャンネル音声は解析結果内へ別に保持します。

LiveSource は現在の直近バッファを一回コピーし、WaveFileSource は WAV を毎回再読込します。
どちらも同じ Analyzer へ native 音声を渡します。callback 境界で前処理を分割しません。
Replay へ送った Live の区間は ClipSource として固定され、取得が進んでも再解析に同じ区間を使います。
連続 Oracle 解析や候補区間抽出は後続 Phase です。

raw ring dump は float32 WAV で rate / channels / samples を保持します。
解析後の保存は float32（サンプル精度保持）または PCM16（量子化・範囲制限）です。
保存ボタン操作だけでファイルを生成し、自動セッション録音を始めません。

読込は little-endian RIFF / WAVE の PCM 8 / 16 / 24 / 32 bit、
float 32 / 64 bit、対応 WAVE_FORMAT_EXTENSIBLE に対応します。
24 bit の left-justified int32 を正しく振幅変換します。
圧縮・RIFX・RF64・複数 data チャンクはこの段階では対象外です。
入力 128 MiB、展開後の native / 解析後それぞれ 256 MiB に制限し、
空・不整合なヘッダー・途中で切れたフレーム・非有限値を拒否します。

### テストと実機確認

- Python 3.14.6 / SciPy 1.18.1 / PySide6 6.11.2。
- scripts/verify.ps1: pip check 成功、**153 passed**、exit code 0。
- Phase 0・1 の 90 件を保持し、Phase 2 の 63 件を追加。
- WAV 6形式 × 44100 / 48000 Hz × mono / stereo、拡張 PCM、odd 最終チャンクを確認。
- float32 保存・読込の完全一致、PCM16 の丸め・範囲制限。
- DC / silence / opposite phase / clipping、44.1 / 48 / 192 kHz の周波数・長さ、
  downsampling alias 抑制と端数フレームを確認。
- Live snapshot と raw WAV の共通 Analyzer で sample / hash 完全一致。
- 保存途中・置換失敗・キャンセルでも既存ファイルを保持し、一時ファイルを除去。
- GUI の再解析・保存・失敗後の再試行、メインスレッド更新、解析待ち中の取得継続、
  終了操作が 0.1 秒未満で戻ること、ワーカー解放を確認。

実 Windows / 既定 loopback 48000 Hz / 2 ch を取得し、
native 29760 フレームの保存・再読込と Live / WAV の解析列を完全一致で確認しました。
録音 WAV の 5 回、PCM16 stereo 44100 Hz の 3 回の GUI 再解析も一致しています。
解析後 float32 / PCM16 保存、破損 WAV の案内・正常入力で再試行、
通常ウィンドウと最小サイズの画像を確認しました。
最終の Stop 表示修正は別の実機操作で、保持バッファ 0.6 秒とゼロ音量、WAV 保存を再確認しました。
両ワーカーは終了後に残っていません。

実機検証の音声・画面・レポートは .runtime/phase2-native/ と
.runtime/phase2-buffer-display/ にあり、Git 対象外です。

### 確認中に整えた点

- 失敗した再解析で古い音声を保存できないよう、解析開始時に結果を無効化した。
- レート変換による大きな出力も、変換開始前にサイズを確認する。
- Stop 後の保持バッファ長が 0 と表示される問題を修正。
- ワーカー継続テストの固定 100 ms 待機は環境の実行順序に左右されたため、
  フレーム到着を確認する条件待機へ変更した。

### 既知の制限・次の段階

Oracle 分類、テンプレート管理、候補時刻・スコア・信頼度、音声再生、Overlay は未実装です。
Phase 1 の VoiceMeeter B1 ゲーム音経路・物理再接続・一部 endpoint の取得制限は継続します。
実戦 WAV による認識精度・長時間負荷の確認は後続 Phase です。

次は Phase 3 — OracleId、複数テンプレート管理、録音 / WAV import、
metadata、再起動後の保持、削除・再生です。

## Phase 3 — テンプレート管理（2026-09-14）

### 実装範囲

encounter.vog_oracles の OracleId を固定 ID とし、表示には設定の oracle_labels を使用。
Calibration の最小 UI に Oracle 選択・一覧・録音・複数 WAV Import・品質情報・Listen・
再生停止・Delete・最後の削除の復元・再読込を追加した。詳細調整は Phase 12 に残す。

TemplateManager は Qt に依存せず、Oracle / UUID ディレクトリに
sample.wav、original.wav、schema version 1 の metadata.json を保存する。
登録は Phase 2 の Analyzer を共用。前処理済み mono と native 元音声を float32 WAV に保存。
元ファイルを変更しない。Peak / RMS / clipping、長さ、形式、UTC 日時、source、SHA-256 を保持。
無音・DC・逆位相は拒否し、短さ・長さ・小さな RMS・最大振幅付近は警告する。
サンプルは 10 秒以内、録音用配列は 64 MiB 以下。

一時ディレクトリへ全ファイルを書き、flush / fsync・キャンセル確認後にディレクトリを公開する。
失敗した新規サンプルを一覧へ出さず、既存サンプルに影響しない。
厳格な metadata 検証と音声形式・checksum の確認で、不正なサンプルを一覧から隔離して警告する。
Oracle フォルダー自体が不正でも他の Oracle のサンプルは読み込める。
削除は範囲とリンクを検査したディレクトリを .trash へ移し、同じ ID と音声で復元可能。
共有 manifest を持たず、Oracle ごとの複数サンプルは起動後に再走査する。

録音は既存 CaptureService の worker 内で、開始後のフレームだけを正確な指定数まで蓄積する。
callback に DSP / ファイル処理を追加しない。
録音中止・入力停止・音声欠落・時間切れでは部分サンプルを保存しない。
録音セットアップの失敗も RecordingEvent にして UI の待機を終え、健康な Live を維持する。

TemplateController の専用 worker が Import・保存・一覧・削除・復元・再生を実行する。
再生は現在の WASAPI 既定出力へレート・チャンネル変換して音量を適用する。
callback は用意した音声を渡し、最後のブロックをゼロで埋める。
停止・開始失敗・途中停止・アプリ終了でも stream / PortAudio を解放する。
MainWindow の終了は Capture / Replay / Template の 3 worker を非同期に待つ。

### 検証

- scripts/verify.ps1: pip check 成功、**218 passed**、exit code 0、pytest 21.58 秒。
- 既存 Phase 0–2 の 153 件に Phase 3 の 65 件を追加。
- 7 Oracle × 2 サンプルの保存と新しい manager での再読込。
- WAV 形式変換、native サンプル完全保持、品質警告、無音拒否。
- metadata / 音声破損・欠落・公開失敗・キャンセル・復元衝突・不正 ID / リンク / junction。
- 録音の stale 区間除外、開始・終了境界、指定フレーム数、進捗、Stop / 中止 / 入力停止 / overflow / timeout。
- 再生の native 変換・gain・末尾 padding、停止・反復再生・出力エラー・資源解放。
- GUI Import の部分成功、固定 Oracle 対応、品質表示・Listen・削除と復元・アプリ再起動。
- Import / 録音 / 再生中の終了、Qt メインスレッド更新とワーカー解放。

実 Windows では合成音声 14 件を GUI 登録し、
既定 loopback 48000 Hz / 2 ch の 0.5 秒 / 24000 フレームを追加録音した。
低音量 Listen・停止・削除 / 復元、アプリ再起動後の 15 件保持を確認。
最終の一覧サイズ調整後もネイティブ Listen と再生中の終了を確認した。
画像・report.json・検証 WAV は .runtime/phase3-native/ へ保存し、Git 対象外。

QtTest の待機が Python ファイル worker の進行を妨げるため、テストの条件待機で GIL を譲る。
Qt が str Enum を QVariant の文字列として返すため、選択値を OracleId に変換し直す。
実 Oracle 分類・ゲーム中の録音・認識精度は未検証。
次は Phase 4 — 基本 Oracle 認識、normalized correlation、分類とスコア順位の GUI 表示。
