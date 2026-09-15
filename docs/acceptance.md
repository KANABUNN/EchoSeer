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


## Phase 5 — スペクトル解析と複合スコア（2026-09-14）

- [x] Hann STFT、基音・倍音を含む log magnitude と時間区間のスペクトルテンプレート
- [x] cosine similarity と、減衰に対応した時間区間の比較
- [x] waveform / spectrum / combined を各サンプルと各 Oracle に保持
- [x] 初期重み0.60 / 0.40、設定反映、0 / 1の切替、不正な重みの拒否
- [x] 複合スコアで同じサンプル集合を選び、最高値 / 上位N件平均を適用
- [x] GUI に複合順位と候補、スコア内訳の波形・スペクトル列、現在の重み
- [x] 無音・不正な値・短い音声、形式違い、キャンセル、有限で読み取り専用の特徴
- [x] STFT を32フレームずつ計算し、mono音声と特徴の bank を128 MiB以下に制限
- [x] 既存 metadata と元音声を変更せず、現在の設定から特徴を再生成
- [x] 98ケースの Replay Dataset を WAV 保存・再読込と共通 Analyzer で比較
- [x] 静かな14ケースの正解数が Phase 4 と同じ14/14
- [x] 白色ノイズ付き84ケースは Phase 4 の83/84から84/84へ改善、悪化ケース0
- [x] 実 Windows で7種類の全区間・前後区間、ノイズで改善したBの候補と3スコア
- [x] 通常サイズ・760×600のスコア内訳とスクロール、終了時の全 Oracle worker 0
- [x] 全 pytest: **343 passed** / pip check 成功（既存299件 + Phase 5 の44件）

Phase 5 の44件は STFT20、複合分類22、GUI1、提供 Replay Dataset1。
実音声を使う15件（既存14 + Dataset1）はローカル samples/ がない環境では skip します。

| Replay 条件 | 件数 | Phase 4 正解 | Phase 5 正解 |
|---|---:|---:|---:|
| 全区間 | 7 | 7 | 7 |
| 登録0〜1秒 / 比較1.00〜1.75秒 | 7 | 7 | 7 |
| 白色ノイズ 10 dB | 21 | 21 | 21 |
| 白色ノイズ 0 dB | 21 | 21 | 21 |
| 白色ノイズ -10 dB | 21 | 21 | 21 |
| 白色ノイズ -20 dB | 21 | 20 | 21 |

ノイズ条件は各7音声×固定seed3種。改善したB（-20 dB / seed17）は波形のみL3、
複合R3で、正解はR3。実画面でも波形0.030760 / スペクトル0.252182 /
複合0.119329、第2候補L3の複合0.093967を確認した。
類似度スコアであり、HIGH confidence を認定した結果ではない。

評価レシピは tests/datasets/phase5-oracles.json、再評価は scripts/evaluate_phase5.py。
生成 WAV / report.json は .runtime/phase5-evaluation/、
実画面の画像 / report.json は .runtime/phase5-native/ にあり、Git 対象外。
元 WAV のファイル checksum が評価前後で一致することも確認した。

同じ7録音の区間と人工白色ノイズによる比較で、独立した録音・実戦の評価ではない。
色付きノイズ・会話・効果音・未知音の誤検出率は未確認。
confidence、重複除去、連続イベント検出、VoG Sequence は後続 Phase。

## 後続 Phase — 未実装 / 未検証

- Phase 8–9の実戦通し録音による採用精度・誤検出率・補正妥当性
- 実戦通し録音での提示間隔・音の重なりを含むPass分離・採用精度
- Phase 13: Replay / DebugのTimelineとスコア詳細の完成
- Phase 14–16: Dataset 数値評価、実戦ログ、再接続の実戦調整、Hotkey、UX
- Phase 17: Python がない Windows での onedir EXE 起動
- Phase 18: Borderless Window 上の Overlay、クリック透過、100 / 125 / 150% DPI、複数モニター、物理切断・再接続、再起動

合成音やロジックテストだけで、実 Oracle 音声の認識率・Pass 分離・
実戦使用の完了条件を達成したとは判断しません。


## Phase 6 — 実装・自動テスト確認済み（2026-09-14）

- [x] 複合 best と第2候補の margin を両方使う HIGH / MEDIUM / LOW / REJECTED
- [x] L2 0.83 / R1 0.82 を LOW / unknown とし、僅差を確定しない
- [x] LOW / REJECTED・同点・比較サンプル不足・不正スコアでは採用 Oracle が null
- [x] 閾値・margin・cooldown の設定反映、範囲・順序・有限値の検証
- [x] Live の音声・monotonic 時刻・stream ID・フレーム範囲の同時コピー
- [x] Oracle ごとの重複抑制、境界での解除、別 Oracle、cooldown 0、ストリーム変更
- [x] LOW / REJECTED・除外した重複で抑制起点を変更しない
- [x] Stop 後の同じ音声コピーを Live で再送すると重複、Replay 再解析は同じ採用結果
- [x] Replay が Live の抑制履歴を変更しない、過去時刻の Live 候補を拒否
- [x] JSONL に採用結果・候補・3スコア・margin・理由・音声ソース・checksum
- [x] 日時・ログ処理 monotonic 時刻・音声イベント時刻の分離
- [x] 候補あり LOW / REJECTED・duplicate の native WAV 保存、最大3秒 / 16 MiB
- [x] 切り出しフレーム・チャンネル・レートを記録、float32 サンプル値を保持
- [x] 認識ログ / 不確かな音声の保存切替を画面から操作・設定保存、両OFFでファイルを作らない
- [x] 保存失敗でも解析・unknown 表示・手動 WAV 保存を維持し、警告を表示
- [x] 再解析・失敗時に以前の信頼度と認識結果を消去
- [x] Phase 6 新規69件、全 **412 passed**（91.32秒）/ pip check 成功

GUI は offscreen とデバイス代替を用います。提供音声を使う16件は任意ローカル音声が
ない環境で skip します。Phase 5 の候補順位評価98ケースも引き続き通っています。

## Phase 6 — 実 Windows / 提供音声確認

- [x] Qt platform windows、通常1024×820・760×600の画面と文字を画像で確認
- [x] 既存保存先の7テンプレートと提供WAV全体を比較し、7/7 HIGH で正しい Oracle を採用
- [x] 登録0〜1秒・比較1〜1.75秒では順位7/7を維持、6 REJECTED / 1 LOW ですべて unknown
- [x] Phase 5 の -20 dB / seed17 B の候補 R3 / 0.119329 を REJECTED / unknown
- [x] 提供 F.wav を有限 LiveSource バッファへコピーし、L2 採用→再送重複→Replay 再採用
- [x] JSONL と不確かな WAV の作成、スコア内訳・unknown・重複の表示
- [x] 元の samples/A.wav〜G.wav の SHA-256 が変更前後で一致
- [x] 終了時に Capture / Replay / Template の全ワーカー解放、exit code 0

画像と機械可読レポートは .runtime/phase6-native/ に保存し、Git 対象外です。
この段階の Live 確認は提供WAVを入れた有限バッファで行い、実デバイスの新たな録音確認ではありません。
自己一致は独立録音の精度評価ではありません。初期閾値は実戦データで未校正です。

- [ ] 独立した Oracle 録音・会話・効果音での採用率と誤検出率
- [ ] Destiny 2 実行中の連続イベント切り出しと重複判定
- [x] Phase 7 Sequence FSM と有限録音の PASS 分離（下記）

## Phase 7 — 実装・自動テスト確認済み（2026-09-14）

- [x] Qt非依存 SequenceEngine と全9状態、Encounter定義で3 / 4 / 5 / 6 / 7個
- [x] PASS1 / PASS2 の独立した不変スナップショット、候補順位・信頼度・開始/終了時刻の保持
- [x] HIGH / MEDIUM の採用、unknown の位置保持、抑制したduplicateは個数に含めない
- [x] PASS間隔、音の欠落・タイムアウト、音声終了、source変更・時刻逆行で UNCERTAIN
- [x] confirmの遷移フックは不一致・unknown・重複・不完全な列を拒否、確定後LOCKOUTと連続無音
- [x] native音声で20ms RMS、DC除去・ヒステリシス、60ms pre-roll / 120ms quiet release
- [x] 微小音・DC・短音、途中切れ、長い音、最大3秒 / 16 MiB、128候補、入力120秒の制限
- [x] Replay と有限 LiveSource で共通処理、取得ワーカーを止めず Qt メインスレッドに途中経過を表示
- [x] 解析失敗・キャンセル時に途中結果を消去、Next Round / Reset、解析中の終了で全ワーカー解放
- [x] JSONL にイベントのround / pass / indexと独立PASSのまとめを保存、両OFF・保存失敗を確認
- [x] Phase 7 新規65件、全 **477 passed**（136.69秒）/ pip check 成功

17件はローカルの提供録音を使う任意テストで、音声がない環境ではskipします。
録音済みの A〜G から3〜7個を2回提示に組み、float32 WAV保存・再読込後、
全5ラウンドの計50イベントを正しいOracle・順序で分離しました。結果はすべてVERIFYです。
欠落、PASS不一致、-20dB白色ノイズ、EOF途中切れ、短いPASS間隔の5追加ケースも通りました。
不一致はPhase 7では独立列として保存し、照合待ちにします。CONFIRMEDは出力しません。

## Phase 7 — 実 Windows / 提供音声確認

- [x] Qt platform windows、1024×820と760×700の画面でPASSの文字・全7個の表示を画像検査
- [x] 通常保存先の既存7テンプレートでRound 1 / Round 5を解析、正しい2つのPASSを表示
- [x] 欠落・ノイズ・EOF・間隔不足のUNCERTAIN、不一致の独立保存・VERIFYを表示
- [x] 全Round 1音声を30秒の有限LiveSourceへ入れ、Replayコピーで同じ採用列になることを確認
- [x] Next Round / Reset、進捗のメインスレッド更新、全ワーカー解放、exit code 0
- [x] 提供WAV・既存テンプレート・config.jsonのSHA-256が変更前後で一致

レシピは tests/datasets/phase7-oracles.json、再評価は scripts/evaluate_phase7.py。
生成WAV / report.jsonは .runtime/phase7-evaluation/、画面画像 / report.jsonは
.runtime/phase7-native/ に保存し、Git対象外です。

各声は実 Oracle 録音ですが、連結と無音・提示間隔は人工的に設定しています。
通し録音・独立録音・会話や効果音・残響の重なりを含む実戦精度を確認した結果ではありません。
音の重なりを1イベントとして検出すると unknown・不足になり得ます。初期閾値は未校正です。
取得し続けるLiveの全ラウンド追跡とOverlayはPhase 10–12に追加しました。後の記録を参照してください。

## Phase 8–9 — 実装・自動テスト確認済み（2026-09-14）

- [x] PassComparator、全3〜7個の完全一致と重複なしだけCONFIRMED
- [x] 不一致位置を1始まりで特定、4番目L3 0.96 / L2 0.61はINFERRED
- [x] 元のPASS・全順位を保持し、上位N種類の候補履歴・信頼度・時刻を独立保存
- [x] 同一PASSの重複、期待個数、不完全・比較不能・不正スコアを検証
- [x] 重複なし・採用結果との一致最大・両PASS候補の支持最大・スコア総和最大の探索
- [x] 第2候補で1箇所LOW・両PASSのLOW重複をINFERREDに補正
- [x] 弱い候補・同点・僅差・補正上限・高信頼度の食い違い・途中切れは確定しない
- [x] 最大7!、約13,700部分状態の探索と処理中キャンセル
- [x] 設定4項目の範囲・型・境界、Phase 7設定の互換性と入力の非変更
- [x] CONFIRMED / MISMATCH / INFERRED / CHECK、確定順・推定順、不一致・重複の色と候補tooltip
- [x] JSONLにverification・mismatch_indices・候補履歴・再構成根拠・確定順/推定順を保存
- [x] 再解析失敗・Next Round / Resetで前の照合表示・順序を消去、全ワーカー解放
- [x] 新規64件、全 **541 passed** (158.71s) / pip check成功

18件は提供ローカル録音を使う任意テストで、音声がない環境ではskipします。
提供音声から組んだ全5ラウンドの50イベントで正しい確定順、欠落・不一致・ノイズ・EOF・
間隔不足の5異常ケースを再確認しました。Phase 7の10ケースも現在の照合結果を検証します。
分類スコアへの明示的な誤り注入8ケースも成功しました。
正常だけCONFIRMED、1箇所LOW・HIGH重複・両PASS LOW重複はINFERRED、
弱い候補・同点はCHECK、2箇所LOW・HIGHの不一致はMISMATCHです。
補正した全ケースでconfirmed=false、final_sequence=nullを確認しました。

## Phase 8–9 — 実 Windows / 提供音声確認

- [x] 通常保存先の既存7テンプレートで正しいCONFIRMEDと確定順
- [x] 提供音声の誤認識注入でINFERRED、元のunknown/候補・不一致位置・重複位置を保持
- [x] 弱い候補・同点のCHECK、高信頼度不一致のMISMATCHを表示
- [x] 760×700の全7個・確定順と、1024×820の状態・推定順・元のPASSを画像で確認
- [x] 有限LiveSourceとReplayコピーで同じ確定順、メインスレッドで進捗表示
- [x] Next Round / Reset、全ワーカー0、exit code 0
- [x] 元WAV・既存テンプレート・config.jsonのSHA-256が前後で一致

再評価は scripts/evaluate_phase7.py / scripts/evaluate_phase89.py。
生成WAV・report.jsonは .runtime/phase89-segmentation/ / .runtime/phase89-evaluation/、
実画面画像・report.jsonは .runtime/phase89-native/、Git対象外です。
分類誤りは試験用に注入したものと元の順位を記録しています。実戦中の自然な誤認識を補正できた評価ではありません。
実声の連結・無音・提示間隔は人工条件です。独立録音・残響や会話・効果音の重なりを含む
実戦の採用率・誤検出率・補正妥当性は未検証です。Live全ラウンド追跡はPhase 10の後の記録を参照してください。

## Phase 10–12 — 実装・自動テスト確認済み（2026-09-14）

- [x] 連続Liveでnative未読フレームからRMS区間検出・分類・FSM・2回照合
- [x] 任意のchunk境界、有限Replayと検出区間・元のPASS列が一致
- [x] Round 1〜5、個数・元の2つのPASS・状態・最低スコア・番号付きOracle Map・確定／推定順
- [x] PASS境界の重複履歴リセット、CONFIRMED後のquiet lockoutと次Round
- [x] MISMATCH / INFERRED / CHECKは確定しない。unknown・候補・不一致位置を保持
- [x] stream変更・読み落とし・overflow・切断は確定消去とCHECK、Reset / Round選択で再開
- [x] Reset / Stop / 中断で古いOverlayを即時消去、古いgenerationの更新を排除
- [x] 3秒 / 16 MiBのイベント上限、長音を繰り返しOracleへ分割しない、callbackで解析しない
- [x] Live summaryのsource・frame範囲・checksum_scope、両ログOFF・保存失敗
- [x] Overlayの枠なし・最前面・透過背景・クリック透過・一時ドラッグ・順序／マップ
- [x] 位置・不透明度・倍率・大きさ・表示名・正規化Map配置の保存と再起動
- [x] 負のモニター座標・モニター間の空白・画面外位置・過大サイズの復元
- [x] Calibrationの7種類の件数・未登録案内・Record / Import / Listen / Delete / Quality Check
- [x] Quality Checkのnative Peak / RMS / 長さ / 形式・品質警告・保存音声のchecksum検証
- [x] 品質結果の選択切替消去、破損ファイル案内、原音・保存metadataの保全
- [x] Calibration / Replay処理中のLive一時停止と新しい区間からの再開
- [x] 解析・録音・品質確認中の終了、全4worker解放、exit code 0
- [x] 新規65件、全 **606 passed**（207.55秒）/ pip check成功

19件は提供ローカル録音を使う任意テストで、音声がない環境ではskipします。
scripts/evaluate_live.pyは全5ラウンド、異なるchunkと長いduplicate cooldown、
別Oracleの実録音を2回目の1位置へ入れた不一致の計7ケースです。
連続Liveと有限Replayの元列が一致し、正常だけCONFIRMED、不一致はMISMATCHです。
音声と登録テンプレートは同じ元録音で、無音・連結・提示間隔は人工条件です。
実戦精度の評価ではありません。生成音声・report.jsonは.runtime/live-evaluation/、Git対象外です。

## Phase 10–12 — 実 Windows / 提供音声確認

- [x] Qt platform windowsで提供A〜GをGUIから7種類へ登録し、全7件の品質を再読込確認
- [x] 録音操作から登録・Quality Check、削除後の件数と元サンプル維持
- [x] 登録した実音声で連続LiveのRound 1を確定、異なる実音声で3番目をMISMATCH
- [x] 7個のINFERRED表示fixtureで確定チェックなし、最小240×112の順序Overlayで全7個表示
- [x] 100% / 125% / 150%のQt表示倍率、主画面・マップ・2つのPASS・小さいOverlayを画像確認
- [x] テスト用borderless windowでWin32の実クリックが背後へ届き、Overlayがフォーカスを奪わない
- [x] ドラッグ中だけOverlayへ入力し、移動と位置保存、終了後にクリック透過へ復帰
- [x] 画面外・過大サイズを画面内へ復元、設定画面を閉じて調整終了
- [x] 元の提供WAVのSHA-256が前後で一致、終了後の全4workerが0

画像・機械可読レポートは.runtime/phase1012-native/、Git対象外です。
acceptance-1/report.jsonは提供音声のGUI登録・品質確認・連続認識、
probe-1.25/とprobe-1.5/のprobe-report.jsonは入力透過・ドラッグ・表示倍率の記録です。
GUI録音操作はデバイス代替の入力、7個の推定表示は明示的なview fixtureで確認しています。

- [ ] Destiny 2実行中のBorderless WindowでOverlay表示とゲーム操作
- [ ] 物理的な複数モニターの接続・切断と倍率変更
- [ ] 独立した録音・実戦の通し録音で精度、重なり・会話・効果音と長時間負荷
- [ ] 実ゲーム中のCalibration手動録音

操作は[live-overlay.md](live-overlay.md) / [calibration.md](calibration.md)を参照してください。

## Phase 13–15 — 実装・検証（2026-09-14）

- [x] 全区間Timeline、時刻・元候補・信頼度・成分スコア・理由、選択行の全7順位
- [x] native切り出しListen / Stop、反復解析の同一結果・条件、失敗時の古い結果消去
- [x] clip / events / roundの手動正解、時刻対応付け、順序対応付け、CONFIRMEDのみ確定順成功
- [x] 正解率・採用precision・棄却率・負例誤検出・時刻付き誤検出/分・Oracle別指標、分母なしはnull
- [x] report.jsonとUTF-8 BOMのconfusion-matrix.csv、新規保存先・キャンセル・Windows一時ロック
- [x] 同じDatasetの比較、WAV・正解違いの拒否、解析中のWAV・テンプレート変更の拒否
- [x] 採用音声収集の初期OFFと独立切替、不一致の採用側証拠、1音の追加保存1回・最大14音 / 32 MiB
- [x] 元判定と手動正解を分離、4分類・由来・メモ、元形式のCase保存・Dataset固定スナップショット
- [x] JSONLの最新2000行、破損・範囲外音声の案内、開く時のchecksum検証
- [x] Liveの認識世代に限定した直近音の固定コピー、Stop / Resetで古い音を消去
- [x] 新規68件、全674 passed（321.85秒）/ pip check成功

提供素材の15ケースは7単独、負例2、両PASSの全5ラウンド、時刻付き6音と背景ノイズ。
正解63/63、採用precision 100%、余分な採用0、負例誤検出0/2、時刻付き誤検出0/分、全5ラウンド確定順が一致。
背景unknownは3、全予測66、棄却率4.55%。既定重みと波形のみのCLI比較は全指標差0、全Oracle別差0。
テンプレートと音声は同じ元録音、無音・提示間隔・ノイズは人工条件で、実戦精度の測定ではない。

Windows 100% / 150%の独立データで、GUI登録7音、Round 5の14音、反復一致、全7内訳、native再生区間と停止を確認。
手動ラベルを意図的にR3としたL2音を独立した正解として保存し、評価で誤認識1と表示するUI fixtureを確認した。
28行の採用音声ログからchecksum一致のReplay、LiveでのMID音の固定コピーと空の正解欄を確認した。
両倍率で主画面が利用可能画面内、終了時worker 0、元A〜GのSHA-256前後一致。
表見出しのコントラスト修正と手動入力欄を150%画像で再確認した。
選択Listenの再生対象・停止は再生デバイス代替で検証している。実際の音声出力先での聞き取りは別途確認が必要。

証跡は.runtime/phase1315-verification.log / phase1315-example/ / phase1315-cli/{baseline,waveform-only}/ / phase1315-native/dpi-{1,1.5}/、Git対象外。
再現はscripts/prepare_example_dataset.py / scripts/evaluate_dataset.pyと関連テスト。

- [ ] 成功・低confidence・自然な誤認識・戦闘音が重なる実戦録音の収集と手動正解
- [ ] 独立した実戦Datasetでの変更前後比較と、閾値・重み・帯域・テンプレートの改善確認

非戦闘の3音WAVに加え、ラウンド開始から両PASSまでの実録音1件を受領した。
後者は両PASSを中央 / 右1 / 右2として確定できたが、手動正解付きの複数実戦Datasetと
戦闘音が重なる条件の比較は未完了。認識閾値・重み・帯域・既存ユーザーテンプレートは維持している。

最終の表表示・手動正解入力修正後はGUI / 起動19件も成功（15.17秒）。
空欄・区切り記号だけの入力を保存しないことと、Windows 100%の表見出し・全7Oracleの評価表示を再確認した。


## Phase 16 — 設定と操作

Settings、初期OFFのGlobal Hotkey、Roundのやり直しと自動移行設定、
手動再接続、Overlayサイズ選択、画面のエラー案内を実装した。
Windows上の専用確認用ウィンドウでHotkeyの受信・重複抑制・登録競合・編集時の解除、
提供音声のReplay14音、終了時の4worker・全登録の解放を確認した。

ユーザーの実戦使用を待ち、設定調整・改善測定後にPhase 17・18を行う。
Destiny 2実行中、物理的な切断・再接続、長時間使用と配布環境の最終受け入れは未完了。
使用手順は[設定と操作](settings-ux.md)。


Phase 16の全体検証712件と、最終差分の18件（新規1件を含む）は成功。
100%の実キー確認は編集欄を空にして新規入力を検証した。
150%の設定表示・登録競合・Replay14音・終了処理は確認したが、確認用ウィンドウへの切り替えが
Windowsに受け付けられず、実キー入力は送らずに中止した。150%の入力操作は未確認として扱う。

## 非戦闘の連続Oracle分離（2026-09-15）

- [x] 7.16秒の実録音で、変更前の1件 `EVENT_LIMIT` と最初の未採用を再現
- [x] 同じ録音を1.10 / 2.42 / 3.72秒の3候補へ分離し、登録音との照合をすべてHIGHで採用
- [x] ReplayとLiveの960 / 4093 / 22050 frame分割でR2 / L1 / L2の順序が一致
- [x] 短い無音・弱い再上昇・定常長音を従来どおり統合または棄却し、有限/Liveの境界を一致
- [x] 元WAV・config.json・登録済み7音のSHA-256を維持
- [ ] 手動正解、戦闘音・会話・効果音、両PASS、異なる音量での誤検出率と採用率

既定のdetection threshold 0.025、confidence、複合重み、帯域、登録音は変更していない。
新規11件を含む全724件（513.56秒）とpip checkは成功した。
Phase 17・18は、残る実戦調整・改善測定後まで保留する。

## 低音量の両PASS通し録音（2026-09-15）

- [x] 27.093秒 / 32000 Hz stereoの実録音で、固定しきい値0.025ではイベント0件になることを再現
- [x] 直近環境音へ追従する開始値で、固定しきい値未満のOracleを検出
- [x] 余韻の局所的な減衰と再上昇から、7.80 / 9.10 / 10.40秒と14.70 / 15.98 / 17.30秒へ分離
- [x] 最低一致度未満の開始効果音4候補をPASS位置から除外し、ログとTimelineには保持
- [x] ReplayとLiveの960 / 4093 / 22050 frame分割で中央 / 右1 / 右2の両PASSをCONFIRMED
- [x] 元WAV、保存設定、登録済み7音を変更せず検証
- [ ] 手動正解付きの複数ラウンド、戦闘音・会話が重なる条件で採用率と誤検出率を比較

対象WAVのSHA-256は
d6498c4d52531b0818c50231915867e6dc11ce57b6e16963534647f2753ccfca。
固定しきい値、confidence、複合重み、帯域は変更していない。
全734件（493.66秒）とpip checkは成功した。
