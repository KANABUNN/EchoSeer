# 受け入れ確認

実装済みと実環境で確認済みを分けて管理します。

## Phase 0 — 確認済み

- [x] Python 3.14.6 x64 で main.py を起動
- [x] 実 Windows ウィンドウの表示・終了（exit code 0）
- [x] PySide6 / NumPy / SciPy / PyAudioWPatch / PyInstaller の import
- [x] 設定の保存・再読込、カスタム設定の保持
- [x] 破損設定の退避・初期値での復旧
- [x] 退避・保存失敗時の既存ファイル保護
- [x] Phase 0 pytest: 58 passed / pip check
- [x] README の setup.ps1 / verify.ps1
- [x] pyproject.toml の package 登録と GUI launcher 起動・終了
- [x] 設定例と原指示書コピーの整合
- [x] Phase 0 の Git 差分・新規ファイルの空白チェック

## Phase 1 — 実装・自動テスト確認済み（2026-09-13）

- [x] WASAPI loopback / 通常 input の列挙・選択
- [x] native sample rate / channels / LIVE / RMS / Peak / Level Meter の表示
- [x] 入力方式・デバイス名称・host API・loopback の設定保存（index は保存しない）
- [x] 保存デバイス不在・同名衝突の案内、別入力へ自動切替しない
- [x] callback は上限付き Queue へ渡し、音量処理は取得ワーカーで行う
- [x] 5～30 秒のリングバッファ、折返し・大きい書込み・コピー取得
- [x] 44100 / 48000 / 192000 Hz × 1 / 2 / 8 ch の native 形式を開く指定
- [x] 欠落・オーバーフローの計数、欠落時のバッファリセット
- [x] 無音の loopback をデバイス切断と誤判定しない
- [x] 反復 Start / Stop とストリーム・PortAudio・スレッドの解放
- [x] GUI を Qt メインスレッドで更新
- [x] Start 失敗の画面表示・再試行、終了時にも致命的エラーの案内を保持
- [x] 開始待ち中の終了が GUI を止めず、終了要求後にストリームを開始しない
- [x] ストリーム停止後の同一デバイスへの再接続（index / rate の変更を模擬）
- [x] 小さいウィンドウのスクロール表示、操作欄の文字が潰れない
- [x] 全 pytest: **90 passed**（9.34 秒）/ pip check 成功

形式・再接続・異常系の自動テストはデバイス代替を用いています。
物理デバイスの全形式が利用可能であることを保証する確認ではありません。

## Phase 1 — 実 Windows / 実デバイス確認

Qt platform は windows。実ウィンドウを表示して操作・画像を確認しました。

| 対象 | 取得形式 | Start / Stop | 結果 |
|---|---|---:|---|
| 既定 Windows 再生先 Voicemeeter Input の WASAPI loopback | 48000 Hz / 2 ch | 5 回 | 実再生音にメーターが反応 |
| Voicemeeter Out B1 / Windows WASAPI 通常入力 | 48000 Hz / 2 ch | 5 回 | ストリームとフレーム取得成功 |
| Voicemeeter Out B1 / MME 通常入力 | 44100 Hz / 2 ch | 3 回 | ストリームとフレーム取得成功 |
| エラー後に既定 loopback へ再選択 | 48000 Hz / 2 ch | 2 回 | 再試行・実音量表示に成功 |

- [x] 上記計 15 回でクラッシュなし、欠落・オーバーフロー 0
- [x] 終了後に取得ワーカーが残らない
- [x] 表示修正後、既定 loopback を追加で 1 回開始・停止し、実音量と 760 × 600 のスクロール表示を再確認
- [x] Logicool G431 の 48000 Hz / 8 ch と Realtek の 192000 Hz / 2 ch は、開始エラーを GUI に表示し、別入力で再試行できる
- [ ] VoiceMeeter B1 にゲーム音を流した実音量表示
- [ ] Logicool G431 / Realtek の上記 native 形式での実取得
- [ ] 物理的な抜き差し・Windows 側のデバイス切断後の自動復帰
- [ ] Destiny 2 実行中の音声取得・長時間負荷測定

VoiceMeeter B1 は現在の経路で確認音が届かず、WASAPI は無音、
MME は微小な値でした。B1 の確認はフレーム取得までです。
VoiceMeeter のルーティング設定は変更していません。

G431 / Realtek は PortAudio の形式照会では対応と判定されましたが、
実際の開始は Errno -9996（Invalid device）で拒否されました。
G431 は 2 ch 指定でも拒否されました。原因を形式だけに断定できないため、
取得成功扱いせず、画面案内・資源解放・別入力への再試行を確認しました。

## Phase 2 — 実装・自動テスト確認済み（2026-09-14）

- [x] PCM 8 / 16 / 24 / 32 bit、float 32 / 64 bit、対応 WAVE_FORMAT_EXTENSIBLE の WAV 読込
- [x] 44100 / 48000 Hz × mono / stereo の実 WAV を用いた読込・振幅変換
- [x] float32 WAV 保存で native レート・チャンネル数・サンプル値を完全保持
- [x] PCM16 保存の量子化・範囲制限
- [x] 空・破損・圧縮形式・不正数値・入力サイズ・解析後サイズの拒否
- [x] 一時ファイルからの置換、書込み・置換失敗・キャンセル時の既存ファイル保護
- [x] stereo / multichannel 平均、DC 除去、polyphase resampling、peak 正規化
- [x] 44.1 / 48 / 192 kHz の変換後レート・長さ・周波数保持
- [x] downsampling 時の alias 抑制、端数フレームの長さ
- [x] 無音・逆位相・微小音声を正規化で無理に増幅しない
- [x] リングバッファの dump、折返し後の chronological 順序と精度
- [x] AudioSource / LiveSource / WaveFileSource / ClipSource と共通 Analyzer
- [x] Live のコピーと保存した WAV から同一の解析サンプル列
- [x] 同じ WAV の反復解析が bit 単位で一致し、SHA-256 も一致
- [x] ファイル変更は再読込で反映
- [x] Replay の開く・Analyze・float32 / PCM16 保存操作
- [x] Live の直近バッファの Replay 送信・native WAV 保存
- [x] Qt メインスレッドで結果表示、解析待ち中も取得継続、終了時の両ワーカー解放
- [x] 再解析失敗時の古い結果の保存を無効化
- [x] Stop 後の保持バッファ長表示、無音メーターと保存操作
- [x] 全 pytest: **153 passed** / pip check 成功（既存 Phase 0・1 の 90 件を含む）

## Phase 2 — 実 Windows / 音声・WAV 確認

- [x] Qt platform windows、実ウィンドウで Live / Replay と 760 × 600 のスクロール表示を確認
- [x] Windows 既定再生先の loopback 48000 Hz / 2 ch を取得、Start / Stop
- [x] native 29760 フレームの float32 WAV 保存・再読込が元のバッファと完全一致
- [x] LiveSource の解析結果と同じ録音 WAV の Replay 解析結果が完全一致
- [x] 録音 WAV を 5 回解析し、同一サンプル列・SHA-256 に一致
- [x] PCM16 stereo 44100 Hz / 4410 フレームを 48000 Hz mono / 4800 フレームへ変換
- [x] 上記 PCM16 WAV を 3 回解析して同一サンプル列
- [x] 解析後 float32 / PCM16 WAV 保存と再読込
- [x] 破損 WAV の案内、保存無効化、正常 WAV での再試行
- [x] 最終修正後、実機で保持バッファ 0.6 秒の表示と停止時のゼロ音量、native WAV 保存を再確認
- [x] Capture / Replay ワーカーが終了後に残らない

この段階の確認音と保存 WAV はプロジェクトの .runtime/ に置いており、Git 対象外です。
実 Oracle 音声の認識・長時間実戦負荷・VoiceMeeter B1 のゲーム音経路の確認ではありません。
LiveSource は直近区間のコピーを読む有限音声ソースです。
候補イベントの連続解析と Replay の時刻・信頼度表示は後続 Phase で追加します。

## Phase 3 — 実装・自動テスト確認済み（2026-09-14）

- [x] 固定 OracleId の 7 種と編集可能な表示名の分離
- [x] 各 Oracle への複数 WAV Import / 録音登録、UUID ごとの保存
- [x] 共通 Analyzer で mono / 内部レート / DC 除去 / 正規化
- [x] native 元音声の float32 WAV 保持、日時・形式・Peak / RMS・品質・checksum
- [x] 無音・DC・逆位相の拒否、短さ・長さ・微小音量・クリッピングの警告
- [x] Oracle ごとの一覧・件数・選択音声の品質表示・再読込
- [x] 新しい TemplateManager / アプリ起動後の保存内容保持
- [x] 保存途中・公開失敗・キャンセル時の未完成サンプル隔離と既存データ保護
- [x] 情報・音声の破損、欠落、Oracle フォルダー不正を警告し、正常サンプルは表示
- [x] サンプルとフォルダーのリンク・junction / 範囲外 ID の拒否
- [x] Delete による .trash 退避、最後の削除の復元、衝突時の既存ファイル保護
- [x] 録音開始前の音声除外、native の指定フレーム数、進捗表示
- [x] 録音中止・Stop・入力停止・overflow・タイムアウトで部分サンプルを破棄
- [x] 録音開始失敗を通知し、健康な Live ストリームを維持
- [x] 既定出力へのレート・チャンネル変換、再生音量、Listen / 停止
- [x] 出力開始失敗・途中停止・キャンセル時の再生資源解放
- [x] Qt メインスレッドで結果表示、録音・再生・Import 中の終了とワーカー解放
- [x] 全 pytest: **218 passed** / pip check 成功（既存 153 件 + Phase 3 の 65 件）

リンク・junction の拒否は OS の判定を差し替えたテストです。
Windows の symlink 作成権限を前提にしていません。

## Phase 3 — 実 Windows / 音声確認

- [x] Qt platform windows、通常サイズと 760 × 600 の Calibration スクロール表示
- [x] 合成 WAV の 7 種 × 2 件を GUI Import し、Oracle ごとの一覧・形式を確認
- [x] Windows 既定 loopback 48000 Hz / 2 ch で 0.5 秒 / 24000 native フレームを録音登録
- [x] 元音声の非無音、Peak / RMS、48 kHz mono の登録形式を確認
- [x] 実 Windows 既定出力で Listen（検証音量 5%）・停止
- [x] 録音サンプルの Delete / 復元、同じ ID を保持
- [x] アプリを開き直して L1 3 件・残り各 2 件、合計 15 件を保持
- [x] 最終の画面調整後に Listen を再確認、再生中の終了でも全 Oracle ワーカー解放

検証音・ユーザーデータ・画像・report.json は .runtime/phase3-native/ にあり、Git 対象外です。
既定 loopback には確認音以外の Windows 再生音が含まれる場合があります。
実 Oracle サンプルを使った品質判定・分類・認識精度、ゲーム中の手動録音は未検証です。
Phase 3 の完了範囲はサンプル管理で、分類器は後続 Phase に実装します。

## Phase 4 — 基本 Oracle 認識（2026-09-14）

- [x] Qt に依存しない OracleClassifier と oracle / best_score / second_candidate / second_score
- [x] Live / WAV 共通の前処理、保存した native 元音声の現在設定による前処理
- [x] FFT による正規化相関、音量・DC・時刻ずれ・極性反転への対応
- [x] 短い端部だけの偽一致を避ける重複長・エネルギー制限
- [x] Oracle ごとの複数テンプレート、最高値 / 上位N件平均、対称な任意帯域処理
- [x] 7種類の全順位、第1・第2候補、両スコア、差、比較サンプル数と警告
- [x] 無音・短すぎる区間・長すぎる区間・未登録・同点で確定しない
- [x] 破損したサンプルを除外して警告し、次の比較に登録・削除を反映
- [x] 比較用 mono テンプレート合計128 MiBの制限とキャンセル
- [x] 比較中も取得ワーカーが進行、Qt メインスレッドの更新、終了時の全ワーカー解放
- [x] サンプル読み込み中の変更を最終 checksum でも拒否
- [x] Windows の一時的なディレクトリ移動失敗への最大5回の限定再試行
- [x] 対応表による7音声登録、同一の再登録は除外、元 WAV を変更しない
- [x] 全 pytest: **299 passed** / pip check 成功（既存218件 + Phase 4 の81件）

うち14件は任意のローカル実音声で、samples/A.wav〜G.wav がない環境では skip します。
残る285件は音声デバイス・ゲーム・ローカル実音声を必要としません。

### 提供された実音声 / 実 Windows の確認

- [x] Templar 対応図から A=L3 / B=R3 / C=MID / D=L1 / E=R1 / F=L2 / G=R2
- [x] 7音声を通常の %LOCALAPPDATA%/OracleAssistant/templates/ に登録
- [x] 再登録で追加0件 / 同一7件、アプリの新しい起動でも7件を保持
- [x] 全区間を登録・比較し、第1候補が7/7正解
- [x] 登録区間0〜1秒、比較区間1.00〜1.75秒の重ならない条件でも7/7正解
- [x] Qt platform windows の実ウィンドウで上記両条件の候補・スコア・全順位
- [x] 通常サイズで7行の表・見出し、760×600で全体のスクロール表示
- [x] 表示調整後の再確認、終了時に残る Oracle ワーカー0
- [x] 確認前後で提供した元 WAV のファイル SHA-256 が一致

| 音声 | 正解 | 重ならない区間の第1スコア | 第2候補 | 第2スコア |
|---|---|---:|---|---:|
| A.wav | L3 | 0.237173 | R3 | 0.120797 |
| B.wav | R3 | 0.277657 | L3 | 0.171843 |
| C.wav | MID | 0.308502 | L3 | 0.126861 |
| D.wav | L1 | 0.455704 | MID | 0.111211 |
| E.wav | R1 | 0.280242 | R3 | 0.105023 |
| F.wav | L2 | 0.374056 | R2 | 0.167146 |
| G.wav | R2 | 0.314906 | L2 | 0.204810 |

両条件とも提供された同じ7録音を使います。全区間同士の一致は登録経路の確認であり、
前後の区間による確認も独立した録音ではありません。
静かな提供音声の区別は確認できましたが、雑音下・別録音・実戦の認識率を示す結果ではありません。
スコアは生の波形類似度です。Phase 5 のスペクトル比較、Phase 6 の confidence、
連続イベント検出・VoG Sequence 処理はこの結果に含みません。

画像と実画面の report.json は .runtime/phase4-native/、数値評価は
.runtime/phase4-real/report.json に保存し、Git 対象外です。
提供音声と対応画像もローカル samples/ に保持し、Git 対象外です。

## 後続 Phase — 未実装 / 未検証

- Phase 5–6: スペクトル解析、信頼度判定
- Phase 7–9: 実 Oracle 録音の Pass 分離、Round 1～5、一致、不一致、重複、再構成
- Phase 10–13: Live の認識表示、Oracle Map、Overlay、Calibration、Replay
- Phase 14–16: Dataset 数値評価、実戦ログ、再接続の実戦調整、Hotkey、UX
- Phase 17: Python がない Windows での onedir EXE 起動
- Phase 18: Borderless Window 上の Overlay、クリック透過、100 / 125 / 150% DPI、複数モニター、物理切断・再接続、再起動

合成音やロジックテストだけで、実 Oracle 音声の認識率・Pass 分離・
実戦使用の完了条件を達成したとは判断しません。
