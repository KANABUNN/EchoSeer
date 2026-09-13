# 受け入れ確認

実装済みと実環境で確認済みを分けて管理します。

## Phase 0 — 確認済み

- [x] Python 3.14.6 x64 で main.py を起動
- [x] 実 Windows ウィンドウの表示・終了（exit code 0）
- [x] PySide6 / NumPy / SciPy / PyAudioWPatch / PyInstaller の import
- [x] 設定の保存・再読込、カスタム設定の保持
- [x] 破損設定の退避・初期値での復旧
- [x] 退避・保存失敗時の既存ファイル保護
- [x] pytest: 58 passed
- [x] pip check

- [x] README の setup.ps1 / verify.ps1
- [x] pyproject.toml の package 登録と GUI launcher 起動・終了
- [x] 設定例と原指示書コピーの整合
- [x] git diff --check / staged 差分 / 新規ファイルの空白チェック

## 未実装 / 未検証

- Phase 1: WASAPI Loopback、通常入力、VoiceMeeter、音量メーター、反復 Start / Stop
- Phase 2–6: WAV、44100 / 48000 Hz、stereo、複数テンプレート、7分類、複合スコア、曖昧音の棄却
- Phase 7–9: 実 Oracle 録音の Pass 分離、Round 1～5、一致、不一致、重複、再構成
- Phase 10–13: Live、Oracle Map、Overlay、Calibration、Replay
- Phase 14–16: Dataset 数値評価、実戦ログ、再接続、Hotkey、UX
- Phase 17: Python がない Windows での onedir EXE 起動
- Phase 18: ゲーム Borderless Window 上の Overlay、クリック透過、100 / 125 / 150% DPI、複数モニター、デバイス切断・再接続、再起動

合成音やロジックテストだけで、実 Oracle 音声の認識率・Pass 分離・
実戦使用の完了条件を達成したとは判断しません。
