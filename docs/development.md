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

## Phase 4 — 波形による Oracle 分類（2026-09-14）

### 実装範囲

dsp.correlation の normalized_correlation は FFT で時刻ずれを探索し、
重複区間ごとの平均・エネルギーで正規化した絶対相関を返す。
短い方の80%以上のフレームと中心化後エネルギーの50%以上を必要とし、
静かな端部のごく短い一致でスコア1になる問題を避ける。
有限の1D実数入力を検証し、float64 の累積和で各区間を計算する。
lag の処理を分割してキャンセルを確認し、スコアは0〜1に制限する。

detector.classifier の OracleClassifier は Qt 非依存で、共通前処理・任意帯域処理・
テンプレートごとの相関・Oracle ごとの最高値または上位N件平均・全順位を返す。
毎回 catalog を更新し、保存した native 元音声を現在の内部レートで前処理する。
イベントは20 ms〜10秒、比較用 mono bank は128 MiB以下。
無音・未登録・無一致・ほぼ同点・制限超過は判定状態として返す。
スペクトル・confidence と Sequence のロジックは追加していない。

既存 Replay の OperationController へ classifier を注入し、
Analyzer の processed 音声を共有する。元音声・変換結果・checksum・WAV 保存を維持。
RecognitionWidget が第1・第2候補、両スコア、差、7種類の順位と件数・警告を表示する。
再解析開始時に古い候補を消し、失敗した再読込で前回の結果を表示しない。
波形スコアは確率ではないことを画面に明示する。
MainWindow が設定の集約方法・N・帯域を適用する。

検証中に Windows の一時的なファイルロックで pending ディレクトリの公開に
WinError 5 が発生したため、移動を専用の共通処理にまとめた。
WinError 5 / 32 / 33 だけ最大5回、20 / 40 / 80 / 160 msで再試行し、
各試行の保存範囲・リンク・衝突を確認する。恒久的な失敗・キャンセルでは既存を保持する。
load_audio は元音声の選択を追加し、返す直前の再読込でも形式と checksum を検証する。

scripts/import_samples.py と docs/sample-mapping.json で、
ユーザーが追加した Templar 対応画像に基づく7音声を登録する。
同一 Oracle / native 音声 checksum / 形式の既存サンプルは再登録しない。
対応表・全入力を先に読み、元ファイルと既存サンプルを保持する。
提供された WAV / PNG はローカル samples/ として Git から除外した。

### 検証

Phase 4 の81件は相関18、分類32、Windows移動5、Import6、再読込整合性2、
GUI統合4、任意の提供実音声14。全体は **299 passed** / pip check 成功。
直接計算との相関一致、時刻・音量・DC・極性・レート変換、端部偽一致、
複数テンプレートの集約、破損・登録・削除、キャンセルとメモリ制限を確認。
Live のコピーと WAV の順位一致、分類中も取得が進むこと、Qt スレッド更新と終了も確認。

提供実音声は全区間同士で7/7、最初の1秒を登録し1.00〜1.75秒を比較しても7/7。
後者の第1スコアは0.237〜0.456。独立した録音での精度評価ではない。
実 Windows 画面でも両条件の全順位・候補・スコアを確認し、
通常サイズと760×600の画像を検査して表の見出しと高さを調整した。
調整後も7/7、元 WAV のハッシュ一致、終了後の全 Oracle ワーカー0を確認。
通常のユーザーデータへ7件を登録し、再登録では追加0件 / 同一7件を確認。
画像・report.json・比較用 WAV は .runtime/phase4-native/、Git 対象外。

次は Phase 5 — STFT / スペクトルテンプレート / cosine similarity /
波形とスペクトルの複合スコア・設定可能な重み。

## Phase 5 — STFT と複合分類（2026-09-14）

dsp.spectrum の spectral_template は Hann窓128 ms・hop10 msの ShortTimeFFT を使う。
窓長の2倍以上の2べき長でFFTし、100〜8000 Hz（Nyquist以下）を保持。
全区間と8時間区間の平均powerを作り、周波数方向31binの局所中央値の1.5倍を除去。
残るmagnitudeをpeak正規化してlog1pへ変換する。
近い低周波成分を分離し、広帯域ノイズの弱い成分を過大評価しないための処理。

spectral_similarity は全区間cosineの25%と、各有効なイベント時間区間から
最も近いテンプレート区間へのcosine平均75%を使用する。
Oracle音の減衰で倍音のバランスが変わるため、時刻・長さが異なる区間も比較できる。
絶対時刻やVoGの順序を判定する処理ではない。
特徴は有限の実数と周波数軸を検証し、所有するreadonly float32配列として保持。
STFTは32列ずつ計算してキャンセルを確認し、192 kHzでも複素作業領域を分割する。

OracleClassifier は既存の波形相関に spectrum_score と combined_score を追加した。
SampleScore / OracleScore に3スコア、ClassificationResultに実際の重みを保持する。
score / best_score / second_score と順位は複合スコアになる。
集約時は複合順で選んだ同じサンプルの成分を平均する。
コンストラクターで重みの型・範囲・合計を検証し、許容誤差内の合計を1へ正規化。
waveform_weight=1 / spectrum_weight=0 はPhase 4の順位を維持する。
Debugの内訳用に重み0の成分も計算する。
waveformと特徴を合わせてbank128 MiBを適用する。既存native音声から毎回再生成し、
保存済み sample.wav / original.wav / metadata を変更しない。

MainWindowが既存設定の重みをworkerへ注入し、RecognitionWidgetは複合順位を表示。
スコア内訳のcheckboxで波形・スペクトル列を追加し、現在の重みも表示する。
再解析・失敗時には古い6列の値を消す。OperationControllerのdebugログにも3成分を出す。
Phase 4の波形値の範囲を確認するテストは明示的な1 / 0の重みで維持し、
Phase 5の7種類×2レートの合成音比較を別途追加した。

評価レシピとscripts/evaluate_phase5.pyで98ケースをWAV生成・再読込・共通Analyzerに通す。
提供7音声の全区間と重ならない区間はPhase 4 / 5とも14/14。
白色ノイズ（SNR10 / 0 / -10 / -20 dB、seed17 / 37 / 73）は83/84から84/84、
悪化0件・改善1件。Bの-20 dB / seed17はL3から正解R3へ変わった。
ノイズでの改善は同じ録音と固定した人工ノイズ条件に限定した結果。
レシピ・元ファイルhash・候補・各Oracleの3成分をreport.jsonに保存する。

新規44テストはSTFT20 / 複合分類22 / GUI1 / 任意実音声Dataset1。
全体 **343 passed**（81.46秒）/ pip check 成功。既存の取得・録音・削除・再生・保存も確認。
実Windowsで全区間7/7・前後区間7/7・改善したノイズ音声の内訳を確認し、
通常と760×600の画像を検査。全workerが終了し、exit code 0。
評価と画像は .runtime/phase5-evaluation/ / .runtime/phase5-native/、Git対象外。

次はPhase 6 — Confidence Engine、best threshold、margin、
HIGH / MEDIUM / LOW / REJECTED、duplicate cooldown、uncertain event logging。


## Phase 6 — Confidence Engine（2026-09-14）

Qt に依存しない ConfidenceEngine / DetectionResult / ConfidenceLevel を追加した。
OracleClassifier は従来の候補順位を維持し、その後に best と margin で採用を判断する。
採用結果は OperationResult.detection.oracle、順位上の暫定候補は classification.best_candidate
で区別する。LOW / REJECTED・比較サンプル不足は null、重複も null。
HIGH と MEDIUM はスコアと margin の両方を満たす場合だけ採用する。
小数の減算による境界の丸め誤差は1e-12の絶対許容誤差で扱う。

audio.event.EventContext は音声窓の時刻とストリームを表す。RingBuffer.snapshot_event が
音声・最終書込み monotonic 時刻・stream ID・フレーム範囲を一度にコピーする。
クリア時に ID を変えて欠落前のイベント履歴を引き継がない。処理時間を重複判定に使わない。
Live の採用時刻は Oracle ごとに保持し、重複で延長しない。過去時刻は拒否する。
Replay は窓末尾の相対時刻を使い、Live 履歴に触れず反復判定が一致する。

RecognitionRecorder は解析ワーカーでJSONLと短いnative音声を保存する。
既存 EventLogger と atomic WAV writer を利用する。最も大きい100ms区間の前後を
最大3秒 / 16 MiB保存し、形式・checksum・切り出し範囲を記録する。
保存失敗は PersistenceResult.notices で返し、解析結果を失わせない。
不正な順位はスコアをログへ持ち込まず、理由付きのREJECTEDイベントとして記録する。
認識ログと不確かな音声は独立して切り替え、設定を保存する。フル録音は初期OFFを維持する。
通常の診断ログで候補を確定Oracleと誤解しないよう候補詳細をdebugにした。

RecognitionWidget は採用Oracle / unknown と信頼度を先に表示し、順位・内訳を維持する。
再解析・失敗時には採用結果を消去する。保存切替は解析中に無効にする。
認識閾値の検証はDSPのレート設定に依存させない。
192kHz用の有効な帯域設定をConfidenceEngineが48kHz用として拒否しない回帰確認も追加した。

新規69件は信頼度46 / ログ16 / 音声時刻2 / GUI4 / 任意提供音声1。
全 **412 passed**（91.32秒）/ pip check成功。
提供7音声の自己一致は7/7 HIGH、前後別区間は7/7正しい順位を維持して全unknown。
初期閾値による6 REJECTED / 1 LOWであり、別録音の採用精度は未校正。
実WindowsのHIGH・unknown・duplicate・小画面・内訳を画像検査し、全worker解放を確認した。
詳細は docs/confidence.md と docs/acceptance.md。

次は Phase 7 — Sequence FSM。連続イベントの切り出し・PASS・順序処理は未実装。

## Phase 7 — VoG Sequence FSM（2026-09-14）

encounter.definition の EncounterDefinition に各Roundの期待個数を置き、
Qt非依存の SequenceEngine / SequenceEntry / SequenceSnapshot / StateTransition を追加した。
IDLE → ARMED → PASS_1 → WAIT_PASS_2 → PASS_2 → VERIFY を実入力で進める。
CONFIRMED / UNCERTAIN / LOCKOUT、Next Round / Reset、確定後の連続無音解除もFSMに実装した。
confirmはPhase 8用フックで、採用済み・完全・一致・重複なしの場合だけ許可する。
Phase 7のReplayはconfirmを呼ばず、照合前の列を確定しない。

PASS1/PASS2は独立したtupleに保存する。生の全候補と信頼度・音声時刻を残して、
後続のPassComparatorと再構成が参照できる。unknownはスロットを占め、duplicateは追加しない。
途中の長い無音で期待個数に届かなければUNCERTAINに止め、次のPASSを埋め合わせに使わない。
待機のタイムアウトは前の音の終了時刻から数え、音そのものの長さを待機時間に含めない。

detector.events はnative音声を20ms単位でDC除去したRMSで検出する。
60ms pre-rollと120ms連続quietのreleaseを含む所有コピーを切り出し、
共通Analyzer → 既存OracleClassifier → ConfidenceEngine → SequenceEngineへ渡す。
80ms未満の音、入力先頭・EOFで切れた音、上限を超える音はunknownにして候補を保存する。
1窓は3秒 / 16 MiB、候補128、入力120秒で制限し、キャンセルをブロックごとに確認する。
ReplaySequenceAnalyzerは有限Replayと保持Live音声を共有し、nativeフレームと開始時刻を保持する。

OperationControllerの既存ワーカーに順序解析を追加し、途中の不変snapshotをQueued Signalで表示する。
SequenceWidgetは2つのPASSと各音の信頼度・スコアを表示し、Next Round / Resetで期待個数を選び直す。
Live入力は処理開始時にコピーし、Replayから同じ区間を再解析できる。
自動で全セッションを追跡する機能は追加せず、既存の単一音Liveの重複履歴も維持する。
JSONLには音ごとのround / pass / indexとPASS全体のsummaryを別レコードで保存する。
保存失敗の警告は解析結果と手動保存を維持して表示する。

新規65件、全 **477 passed**（136.69秒）/ pip check成功。
提供個別録音を組んだ全5ラウンドの50イベントで正しいOracle・PASS分離を確認した。
欠落・不一致・ノイズ・EOF・間隔不足の5ケース、実Windowsの7個表示・有限Liveコピーの
再解析・Next Round / Reset・全worker解放も確認した。原音・設定・テンプレートは変更していない。
連結と提示間隔は人工的で、残響が重なる実戦の通し録音は未検証。
手順・制限は docs/sequence.md、記録は docs/acceptance.md。

次は Phase 8 — PassComparator と2回照合。

## Phase 8–9 — PassComparator / VoG Reconstruction（2026-09-14）

Qt非依存のPassComparator、候補履歴・PassValidationとSequenceReconstructorを追加した。
完全・採用済み・一致・重複なしの両PASSだけCONFIRMEDにし、
元の1始まりの不一致位置を保持したまま信頼度差からINFERREDにできる。
UNKNOWNと比較不能なイベント、同一PASS重複、期待個数を別々に記録する。

各位置の上位N種類の複合・波形・スペクトルスコアを不変の候補履歴に保存する。
元のSequenceEntry/classifier順位を変更しない。LOW第1候補の重複も疑いとして記録する。
一意のOracle順序を探索し、採用済みの一致数、両PASSからの支持数、平均スコア総和を順に最大化する。
一致した非重複位置を保持し、初期1位置だけ補正する。最大7!、約13,700部分状態に収まる。
次点差・根拠不足・途中切れ・補正上限は自動採用しない。HIGH同士の僅差の不一致も、
VoG重複ルールで解消できる場合を除いてMISMATCHに残す。補正結果はCONFIRMEDにしない。

SequenceEngine.verifyをfinite入力終了後に呼び、CONFIRMEDかUNCERTAINへ遷移する。
Snapshotにverificationとsuggested_sequenceを追加し、Next Round / Resetで消去する。
CONFIRMED後のLOCKOUTとRound履歴はPhase 7の機能を維持する。
JSONL summaryには両PASS・候補履歴・比較・探索根拠・確定順・推定順を保存する。
SequenceWidgetは照合状態、不一致/重複の位置と色、元の上位候補、確定順/推定順を表示する。

新しいsequence設定はcandidate_top_n=3、max_corrections=1、inference_margin=0.18、
reconstruction_margin=0.10。旧設定へ初期値を補い、型・範囲を検証する。
recognition.top_nのテンプレート集約とは分離する。実戦から校正した値ではない。

新規64件、全 **541 passed** (158.71s) / pip check成功。
提供音声の全5ラウンド+異常入力10ケース、分類誤り注入8ケースを検証した。
実Windowsの4結果・全7個表示・有限Liveコピー・終了を確認し、元音声/設定/テンプレートを保持した。
実声だがタイミングと誤り注入は人工条件で、実戦精度の確認とは区別する。
手順はdocs/verification.md、記録はdocs/acceptance.md。

次はPhase 10 — Live GUI。

## Phase 10–12 — Live GUI / Overlay / Calibration（2026-09-14）

LiveControllerの専用workerとQt非依存LiveSequenceSessionを追加した。
未読のnativeフレームだけを読み、RMS検出・共通前処理・分類・信頼度・FSM・2回照合を連続実行する。
各音の保持は3秒 / 16 MiB、読み出しは最大0.5秒で制限する。長い連続音は1回のEVENT_LIMITとし、
quiet releaseまで再分割しない。stream変更・バッファ欠落・入力切断は確定を消去してCHECKへ止める。
Reset・Round選択・再開は操作時点から始め、古い取得区間を再投入しない。

LiveにRound・両PASSの個数・元の候補・最低スコア・確定順／推定順・番号付きOracle Mapを集約した。
CONFIRMED後のquiet lockoutは次Roundへ進む。不一致・推定・欠落はUNCERTAINに保持する。
generationでキャンセルと古い更新を排除し、Qtの変更はQueued Signalでメインスレッドへ渡す。
Replay・Calibrationの処理中は連続認識を一時停止し、取得自体は継続する。

独立したOverlayは枠なし・最前面・透過背景・非アクティブ表示・クリック透過に対応する。
Liveと同じ結果を使い、INFERREDを確定表示しない。位置調整は一時的に入力透過を解除する。
順序／マップ・位置・不透明度・倍率・大きさ・字体を設定保存し、画面外や負の座標を補正する。
Oracle Mapの正規化配置と表示名をGUIで変更して保存できる。

Calibrationに7種類の件数・未登録位置・表示名・Quality Checkを追加した。
保存WAVをchecksum検証して再読込し、元のnative音量・長さ・形式・警告を表示する。
品質確認で原音・metadataを変更せず、Oracleの正誤を品質結果から確定しない。
録音・Import・Listen・削除・復元は既存の保存保護と共通前処理を維持する。

新規65件、全 **606 passed**（207.55秒）/ pip check成功。
うち19件はローカル提供音声を使う任意テストで、音声がない環境ではskipする。
提供A〜Gの連続Live / Replay比較7ケースと元WAVの保全を検証した。
Windowsの実Qt画面で7音声のGUI登録・品質確認・Liveの確定と実音声不一致を確認した。
テスト用borderless画面でクリック透過・位置調整・非アクティブ表示、100% / 125% / 150%表示と
終了時の全4worker解放を確認した。高倍率・小さい主画面ではスクロールを使用する。
Destiny 2実行中のOverlay、物理的な複数モニター変更、通し録音・長時間実戦の確認は残る。
手順はdocs/live-overlay.md / docs/calibration.md、証跡と制限はdocs/acceptance.md。

次はPhase 13 — Replay / Debug。
