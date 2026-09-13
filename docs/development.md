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
