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
