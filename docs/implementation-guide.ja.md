# Destiny 2 VoG Oracle Assistant 実装指示書

## 1. プロジェクト概要

Destiny 2「Vault of Glass」のオラクル遭遇において、ゲーム音声からオラクルの出現音をリアルタイムに検出し、

- どのオラクルが鳴ったか
- どの順番で鳴ったか
- 認識結果がどの程度信頼できるか
- 1回目と2回目の提示内容が一致しているか

を判定し、視認性の高いGUIおよびゲーム用オーバーレイへ表示するWindowsアプリケーションを作成する。

既存ソフト「Prophet」と同じ目的を持つが、単純なテンプレートマッチングだけではなく、

1. 音響認識
2. 候補分類
3. 信頼度判定
4. VoG固有ルールによる検証
5. 1回目・2回目の提示結果の相互照合

を分離し、極稀な誤認識をできるだけ排除する。

最終的には、通常使用時にPython環境を必要としないWindows実行ファイルとして配布可能にする。

---

# 2. 最重要要件

本ソフトウェアはDestiny 2へ一切の操作を送信しない。

行ってよい処理は以下に限定する。

- Windows上の音声を取得する
- 音声を解析する
- 解析結果をGUIへ表示する
- 必要に応じて解析用音声をローカルへ保存する

以下は実装しない。

- Destiny 2プロセスのメモリ読み取り
- DLL Injection
- ゲームファイルへのアクセス
- 自動射撃
- キー入力自動化
- マウス入力自動化
- ゲームへのパケット送信
- Bungie APIを使用した戦闘中の情報取得

本ソフトは純粋な「音声解析補助ツール」とする。

---

# 3. 対象環境

初期対象環境：

- Windows 10 / 11 x64
- Python 3.14
- Destiny 2 PC版
- WASAPI
- 通常のWindows再生デバイス
- VoiceMeeter等の仮想オーディオデバイスにも対応

主要ライブラリ：

```text
PyAudioWPatch
NumPy
SciPy
PySide6
PyInstaller
```

可能な限り追加依存関係を増やさないこと。

初期実装ではlibrosaや機械学習ライブラリは使用しない。

標準ライブラリで可能な処理は標準ライブラリを使用する。

---

# 4. 基本アーキテクチャ

処理を以下の層へ明確に分離すること。

```text
Windows Audio
     │
     ▼
Audio Capture
     │
     ▼
Ring Buffer
     │
     ▼
Pre Processor
     │
     ▼
Oracle Event Detector
     │
     ▼
Oracle Classifier
     │
     ▼
Confidence Evaluator
     │
     ▼
Sequence Engine
     │
     ▼
VoG Rule Validator
     │
     ▼
Pass Comparator
     │
     ├───────────┐
     ▼           ▼
Main GUI       Overlay
```

GUIコードと音声解析コードを密結合させないこと。

音声取得、DSP、シーケンス判定、GUIはそれぞれ独立したモジュールとして実装する。

---

# 5. 推奨ディレクトリ構成

以下を基本構成とする。

```text
oracle-assistant/
│
├─ main.py
├─ pyproject.toml
├─ requirements.txt
├─ README.md
│
├─ app/
│   ├─ __init__.py
│   ├─ application.py
│   └─ paths.py
│
├─ audio/
│   ├─ __init__.py
│   ├─ backend.py
│   ├─ wasapi.py
│   ├─ input_device.py
│   ├─ capture.py
│   ├─ ring_buffer.py
│   ├─ resampler.py
│   └─ device_manager.py
│
├─ dsp/
│   ├─ __init__.py
│   ├─ preprocess.py
│   ├─ filters.py
│   ├─ normalization.py
│   ├─ onset.py
│   ├─ correlation.py
│   ├─ spectrum.py
│   └─ features.py
│
├─ detector/
│   ├─ __init__.py
│   ├─ detector.py
│   ├─ classifier.py
│   ├─ confidence.py
│   ├─ duplicate_filter.py
│   └─ result.py
│
├─ encounter/
│   ├─ __init__.py
│   ├─ state.py
│   ├─ sequence.py
│   ├─ validator.py
│   ├─ comparator.py
│   └─ vog_oracles.py
│
├─ templates/
│   ├─ manager.py
│   ├─ metadata.py
│   └─ samples/
│       ├─ L1/
│       ├─ L2/
│       ├─ L3/
│       ├─ MID/
│       ├─ R1/
│       ├─ R2/
│       └─ R3/
│
├─ ui/
│   ├─ __init__.py
│   ├─ main_window.py
│   ├─ live_page.py
│   ├─ calibration_page.py
│   ├─ replay_page.py
│   ├─ settings_page.py
│   ├─ oracle_map.py
│   ├─ sequence_view.py
│   ├─ confidence_view.py
│   ├─ overlay.py
│   └─ theme.py
│
├─ config/
│   ├─ defaults.py
│   ├─ manager.py
│   └─ schema.py
│
├─ logging_ext/
│   ├─ event_logger.py
│   ├─ audio_logger.py
│   └─ session_logger.py
│
├─ replay/
│   ├─ analyzer.py
│   ├─ dataset.py
│   └─ evaluator.py
│
├─ tests/
│   ├─ unit/
│   ├─ integration/
│   ├─ audio/
│   └─ datasets/
│
└─ build/
    ├─ build.ps1
    └─ oracle-assistant.spec
```

必要に応じて変更してよいが、責務分離は維持すること。

---

# 6. 内部Oracle識別子

内部では表示名と識別子を分離する。

例：

```python
class OracleId(Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    MID = "MID"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
```

表示名は設定から変更可能とする。

例：

```json
{
    "oracle_labels": {
        "L1": "左1",
        "L2": "左2",
        "L3": "左奥",
        "MID": "中央",
        "R1": "右1",
        "R2": "右2",
        "R3": "右奥"
    }
}
```

UI内部の位置も設定可能な構造にしておくこと。

---

# 7. 音声処理の基本方針

## 7.1 音声入力

優先する方式：

```text
WASAPI Loopback
```

PyAudioWPatchを利用する。

通常録音デバイスも選択可能にする。

つまり、

```text
AudioBackend
 ├─ WasapiLoopbackBackend
 └─ InputDeviceBackend
```

という抽象化を行う。

Destiny 2をVoiceMeeterへ出力している場合は、

```text
VoiceMeeter Output
VoiceMeeter AUX Output
```

等を通常入力として選択可能にする。

---

## 7.2 デバイス保存

PortAudioのdevice indexのみを永続保存してはならない。

以下を保存する。

```json
{
    "host_api": "Windows WASAPI",
    "device_name": "Speakers (...)",
    "loopback": true
}
```

起動時にはデバイス一覧から再検索する。

同名デバイスが複数ある場合のみ追加情報を利用する。

---

## 7.3 サンプリングレート

44100 Hz固定としない。

入力デバイスのnative sample rateを取得する。

内部処理用サンプリングレートを、

```text
48000 Hz
```

に統一する設計を基本とする。

必要ならSciPyを使ってresampleする。

テンプレート側も同じ内部サンプリングレートへ統一する。

---

## 7.4 チャンネル

ステレオ入力はモノラル化する。

単純平均を初期実装とする。

```python
mono = (left + right) / 2
```

ただし将来的に左右別解析できる構造は残しておく。

---

# 8. 音声スレッド設計

音声callback内では重い解析をしない。

callbackの仕事は、

```text
音声取得
↓
NumPy変換
↓
リングバッファまたはQueueへ格納
```

までとする。

解析はWorker Threadで行う。

```text
Audio Thread
     │
     ▼
Queue / Ring Buffer
     │
     ▼
DSP Worker
     │
     ▼
Detection Events
     │
     ▼
Sequence Worker
     │
     ▼
Qt Main Thread
```

GUI操作は必ずQt Main Threadで行う。

---

# 9. リングバッファ

常時直近数秒の音声を保持する。

標準：

```text
10秒
```

設定可能：

```text
5～30秒
```

用途：

- Oracle認識
- 誤認識時音声保存
- デバッグ
- Replayデータ生成

メモリコピーを必要以上に発生させない。

---

# 10. 前処理

最低限、以下を実装する。

```text
Stereo → Mono
DC Offset Removal
Amplitude Normalization
Optional Band-pass Filter
```

Band-passの周波数範囲はコードへ固定しすぎない。

設定可能にする。

Oracleサンプル解析によって適切な帯域を決められるようにする。

---

# 11. Oracleイベント検出

音声全体を毎回7種類のテンプレートへフル比較するのではなく、

```text
音声ストリーム
↓
Oracleらしいイベント候補を検出
↓
候補区間のみ分類
```

とする。

初期実装では以下を利用してよい。

- RMS
- spectral energy
- onset
- テンプレートとの粗い相関

候補イベントの前後を含め、

```text
pre-roll
post-roll
```

付きで解析区間を切り出す。

---

# 12. Oracle分類方式

単一方式ではなく、最低2系統使用する。

## A. 正規化相互相関

Oracleテンプレート波形とのNormalized Cross Correlationを計算する。

音量差の影響を可能な限り取り除く。

各Oracleについて複数テンプレートがある場合、

```text
最高スコア
```

だけではなく、

```text
上位N件平均
```

も選べるようにする。

---

## B. 周波数スペクトル比較

STFTを使用する。

比較対象：

- fundamental付近
- 倍音構造
- log magnitude spectrum
- スペクトログラム形状

各テンプレートとのcosine similarity等を求める。

---

# 13. 複合スコア

最終スコアは複数方式を合成する。

初期値例：

```text
waveform_weight = 0.60
spectrum_weight = 0.40
```

ただし設定可能とする。

```text
combined =
    waveform_score * waveform_weight
  + spectrum_score * spectrum_weight
```

将来的に第三のclassifierを追加できる構造にする。

---

# 14. Confidence判定

単純に最高スコアのOracleを採用してはならない。

以下を評価する。

```text
best_score
second_score
margin = best_score - second_score
```

例：

```text
L2 = 0.95
R1 = 0.51
```

なら高信頼。

一方、

```text
L2 = 0.87
R1 = 0.85
```

なら不確実。

Confidence状態：

```text
HIGH
MEDIUM
LOW
REJECTED
```

を用意する。

初期閾値は設定ファイルへ置き、後から調整可能にする。

LOWまたはREJECTEDの場合、

```text
無理に確定しない
```

こと。

「わからない」という判断を許容する。

これは本アプリの重要な設計思想とする。

---

# 15. 重複検出防止

1回のOracle音を複数回検出しないよう、

```text
cooldown
```

を設ける。

同一Oracleが極短時間内に複数検出された場合、

```text
duplicate candidate
```

として破棄する。

cooldown時間は設定可能にする。

---

# 16. VoGオラクル遭遇専用Sequence Engine

遭遇中は状態機械で管理する。

```text
IDLE
 ↓
ARMED
 ↓
PASS_1
 ↓
WAIT_PASS_2
 ↓
PASS_2
 ↓
VERIFY
 ↓
CONFIRMED
 ↓
LOCKOUT
 ↓
ARMED
```

異常時：

```text
VERIFY
 ↓
UNCERTAIN
```

も用意する。

---

# 17. ラウンド

テンプラー前Oracle遭遇の期待個数をデータとして持つ。

```text
Round 1 = 3
Round 2 = 4
Round 3 = 5
Round 4 = 6
Round 5 = 7
```

Encounter定義として記述し、classifier内部へ直接ハードコードしない。

例：

```python
VOG_ORACLES = EncounterDefinition(
    sequence_lengths=[3, 4, 5, 6, 7]
)
```

---

# 18. 1回目と2回目の提示

1ラウンドにつき、同じOracleシーケンスが2回提示されることを利用する。

例：

```text
PASS 1
L2 R1 MID L3 R2

PASS 2
L2 R1 MID L3 R2
```

完全一致：

```text
CONFIRMED
```

とする。

---

# 19. 不一致時処理

例：

```text
PASS 1
L2 R1 MID L3 R2

PASS 2
L2 R1 MID L2 R2
```

この場合、

```text
MISMATCH AT INDEX 4
```

として記録する。

各Oracleのconfidenceを比較する。

例：

```text
PASS1 L3 = 0.96
PASS2 L2 = 0.61
```

であれば、

```text
推定値 = L3
```

としてよい。

ただしUIでは、

```text
CONFIRMED
```

ではなく、

```text
INFERRED
```

または

```text
CHECK
```

と表示する。

---

# 20. VoGルールによる検証

1つの提示シーケンス内ではOracle重複を異常として扱う。

例：

```text
L1 R2 MID L1
```

なら、

```text
duplicate Oracle
```

として疑う。

ただしclassifierの結果自体を即削除せず、

```text
candidate history
```

へ保存する。

2位候補等を使用して整合する組合せを探す余地を残す。

---

# 21. Sequence Reconstruction

高度機能として、LOW confidenceや重複が含まれる場合、

各位置について上位候補を保持する。

例：

```text
Index 1
L2 0.96
R1 0.42

Index 2
R1 0.91
L2 0.63

Index 3
MID 0.95
```

VoGルールとPASS1/PASS2を利用し、

```text
最も総合confidenceが高い有効シーケンス
```

を探索する。

ただし初期段階では必須ではない。

後述Phase 7で実装する。

---

# 22. CONFIRMED後の誤検出防止

2回目の提示が終了した後は、

Oracle破壊中の音や戦闘音を新しい提示として認識しない。

CONFIRMED後は、

```text
LOCKOUT
```

へ移る。

一定時間Oracle candidateをSequenceへ追加しない。

その後、十分な無音区間または次ラウンド開始条件を検出してARMEDへ戻す。

手動の、

```text
Next Round
Reset
```

も用意する。

自動判定が失敗した場合でもユーザーが即座に復帰できるようにする。

---

# 23. Main GUI

PySide6で作成する。

メイン画面は最低以下のタブを持つ。

```text
Live
Calibration
Replay
Settings
Logs
```

---

# 24. Live画面

Live画面には以下を表示する。

上部：

```text
Audio Device
LIVE / STOPPED
Current Round
Current State
Audio Level
```

中央：

```text
Oracle Map
```

下部：

```text
Pass 1
Pass 2
Final Sequence
Confidence
```

ボタン：

```text
Start
Stop
Reset
Next Round
Previous Round
Clear
Overlay
```

---

# 25. Oracle Map

7個のOracle位置を視覚的に表示する。

概念例：

```text
              L3


        L2            R3


 L1           MID            R2


              R1
```

正確な画面位置は後から変更可能にする。

Sequenceが、

```text
L2 → R1 → MID → L3
```

ならマップ上に、

```text
1
2
3
4
```

を重ねる。

色だけに依存せず、

```text
番号
ラベル
枠
```

でも識別可能にする。

---

# 26. Sequence表示

最終表示例：

```text
1  L2
2  R1
3  MID
4  L3
5  R2
```

横表示も用意する。

```text
L2 → R1 → MID → L3 → R2
```

---

# 27. Confidence表示

Oracle単位とシーケンス全体の両方を表示する。

例：

```text
L2   98%
R1   96%
MID  99%
L3   91%
R2   97%

Sequence Confidence: 96%
```

状態：

```text
CONFIRMED
INFERRED
UNCERTAIN
MISMATCH
```

を明確に表示する。

---

# 28. Overlay

ゲームプレイ時の最重要UI。

独立したQt Windowとする。

要件：

```text
Always On Top
Frameless
Transparent
Click Through
Position Adjustable
Scale Adjustable
Opacity Adjustable
```

表示例：

```text
┌──────────────────────────────┐
│ ✓  L2 → R1 → M → L3 → R2   │
│             96%              │
└──────────────────────────────┘
```

マップ型Overlayにも切替可能にする。

Overlay位置は保存する。

モニター変更時に画面外へ消えないよう復元時に座標補正する。

---

# 29. Overlay状態表示

認識中：

```text
LISTENING...
```

PASS1終了：

```text
PASS 1
L2 → R1 → M → L3
```

PASS2検出中：

```text
VERIFYING...
```

確定：

```text
✓ CONFIRMED
L2 → R1 → M → L3
```

不一致：

```text
⚠ CHECK
L2 → R1 → ? → L3
```

とする。

---

# 30. Calibration機能

各Oracleのテンプレートをユーザー自身の環境で追加できるようにする。

Calibration画面：

```text
Oracle: [L1 ▼]

Samples: 5

[Record Sample]
[Listen]
[Delete]
[Import WAV]
```

録音後、

- peak
- RMS
- length
- sample rate
- clipping

を確認する。

明らかに不正なサンプルには警告する。

---

# 31. テンプレート複数登録

Oracleごとに複数サンプルを保持する。

```text
templates/samples/L1/
  0001.wav
  0002.wav
  0003.wav

templates/samples/L2/
  ...
```

metadataも保持する。

例：

```json
{
    "oracle": "L1",
    "sample_rate": 48000,
    "duration": 0.84,
    "created_at": "...",
    "source": "recorded"
}
```

---

# 32. Prophetサンプル等のImport

既存のWAVデータを利用できるよう、

```text
Import WAV
```

を実装する。

形式が異なる場合、

- mono化
- sample rate変換
- normalize

を自動実行する。

原本は必要に応じて別途保持してよい。

---

# 33. Replayモード

リアルタイム入力だけでなく、WAVファイルを解析できるようにする。

```text
Replay
```

画面から、

```text
Open WAV
Analyze
```

を実行する。

結果として、

```text
timestamp
detected oracle
confidence
scores
```

を表示する。

---

# 34. Replayの重要性

リアルタイムでしか再現できない設計にしてはならない。

音声認識処理は、

```text
Live Audio
Recorded WAV
```

の両方から同じAnalyzerへ入力できるようにする。

例：

```text
AudioSource
 ├─ LiveSource
 └─ WaveFileSource
```

---

# 35. 誤認識音声保存

以下の場合は、該当イベント前後の音声を自動保存可能にする。

- LOW confidence
- PASS不一致
- duplicate
- userが「Incorrect」を押した
- classifierがREJECTしたがOracle候補だった

例：

```text
logs/audio/
2026-09-13/
  211421_001.wav
  211433_002.wav
```

---

# 36. Recognition Log

各イベントをJSONL等で保存する。

例：

```json
{
    "timestamp": 1789301661.32,
    "round": 3,
    "pass": 1,
    "oracle": "L2",
    "confidence": 0.94,
    "waveform_score": 0.92,
    "spectrum_score": 0.97,
    "second_candidate": "R1",
    "second_score": 0.53
}
```

ユーザーがログ保存をOFFにできるようにする。

---

# 37. セッション録音

フルセッション録音はOFFを初期値とする。

設定からONにできる。

誤認識イベント周辺の短時間保存は別設定とする。

---

# 38. 評価用Dataset

将来的な精度評価用に、

```text
tests/datasets/
```

へテスト音声を登録できるようにする。

metadata：

```json
{
    "file": "round5_test01.wav",
    "expected": [
        "L2",
        "R1",
        "MID",
        "L3",
        "R2",
        "L1",
        "R3"
    ]
}
```

---

# 39. 自動評価

Datasetに対して一括解析できるコマンドまたはGUI機能を作る。

出力：

```text
Total events
Correct
Incorrect
Rejected
Accuracy
False positive rate
```

Oracle単位：

```text
L1 99.4%
L2 98.9%
...
```

---

# 40. Confusion Matrix

最終段階で、

```text
actual vs predicted
```

のConfusion Matrixを生成できるようにする。

GUI表示までは必須ではない。

CSV出力でもよい。

---

# 41. 設定

最低以下を設定可能にする。

```text
Audio device
Input backend
Internal sample rate
Buffer duration

Waveform weight
Spectrum weight

Detection threshold
Confidence threshold
Margin threshold
Duplicate cooldown

Lockout duration

Oracle labels
Oracle map positions

Overlay
 - enabled
 - position
 - opacity
 - size
 - font size

Logging
 - event logs
 - uncertain audio
 - full recording
```

---

# 42. 設定保存場所

ユーザー設定はWindowsのユーザーデータ領域を使用する。

例：

```text
%APPDATA%\OracleAssistant\
```

または

```text
%LOCALAPPDATA%\OracleAssistant\
```

設定：

```text
config.json
```

テンプレート：

```text
templates/
```

ログ：

```text
logs/
```

とする。

アプリ本体のProgram Files等へ設定を書き込む設計にしない。

---

# 43. 設定破損対策

config.jsonが壊れていても起動不能にしない。

```text
読み込み失敗
↓
バックアップ
↓
defaultsを使用
```

とする。

schema versionを付ける。

```json
{
    "schema_version": 1
}
```

---

# 44. エラー処理

以下でクラッシュしないこと。

- 音声デバイスが消えた
- VoiceMeeterが停止した
- ヘッドホンを抜いた
- sample rateが変わった
- templateが不足
- configが破損
- WAVが壊れている
- GPU/モニター構成が変更された

音声デバイス切断時は、

```text
Audio device lost
```

とGUIへ表示し、自動再接続を試みる。

---

# 45. パフォーマンス

Destiny 2と同時使用するため、CPU負荷を抑える。

目標：

```text
通常認識時 CPU負荷 数%程度
```

とする。

毎フレームFFTを大量に計算するような設計は避ける。

Oracle candidateが検出された区間を重点的に解析する。

---

# 46. スレッドセーフ

Qt UIをWorker Threadから直接変更しない。

Signal/Slotを使う。

共有データには必要に応じてQueueまたはLockを使用する。

停止時にThreadが残らないようclean shutdownを実装する。

---

# 47. ホットキー

後半段階で以下を追加する。

例：

```text
Start / Stop
Reset
Next Round
Toggle Overlay
```

ゲーム中に衝突する可能性があるため、

- デフォルト無効
- ユーザー設定可能

とする。

Windows Global Hotkeyを使用してよい。

---

# 48. UIデザイン

基本方針：

- ダークテーマ
- 高コントラスト
- 情報量を詰め込みすぎない
- プレイ中に一瞬見て理解できる
- 赤・緑だけに依存しない
- 数字と文字でも状態を表す

Oracle認識結果を最優先する。

設定画面よりLive画面を中心とする。

---

# 49. 開発段階

以下のPhase順で実装すること。

各Phaseが正常動作するまで次Phaseへ進まないこと。

---

# Phase 0：プロジェクト基盤

## 実装

- Git repository初期化
- Python 3.14 venv
- requirements.txt
- pyproject.toml
- ディレクトリ作成
- logging基盤
- config manager
- pytest環境
- main.py
- 基本PySide6 Window

## 起動

```bash
python main.py
```

で空のメインウィンドウが開くところまで。

## 完了条件

- Python 3.14で起動する
- Import errorなし
- pytest実行可能
- configを保存・再読込できる

---

# Phase 1：音声取得

## 実装

- PyAudioWPatch
- WASAPI device列挙
- loopback device列挙
- 通常input device列挙
- device selector
- audio capture thread
- ring buffer
- audio level meter

まだOracle認識は実装しない。

GUIへ、

```text
Device
Sample Rate
Channels
LIVE
Level Meter
```

を表示する。

## テスト

- Windows通常出力
- WASAPI loopback
- VoiceMeeter Output
- Start/Stop反復

## 完了条件

Destiny 2等の音を再生した際、

```text
Level Meter
```

がリアルタイムに反応する。

Start/Stopを繰り返してもクラッシュしない。

---

# Phase 2：Replayおよび音声基盤

## 実装

- WAV読込
- WAV保存
- resampling
- mono化
- normalization
- ring buffer dump
- AudioSource abstraction

```text
LiveSource
WaveFileSource
```

の双方から同一Analyzerへデータを渡せるようにする。

## 完了条件

同じWAVを、

```text
Replay
```

から何度解析しても同一のサンプル列を取得できる。

この段階ではOracle判定不要。

---

# Phase 3：テンプレート管理

## 実装

- OracleId
- TemplateManager
- WAV import
- Recording
- sample metadata
- template一覧
- delete
- playback

Oracle：

```text
L1
L2
L3
MID
R1
R2
R3
```

## 完了条件

各Oracleへ複数WAVを登録可能。

アプリ再起動後も残る。

---

# Phase 4：基本Oracle認識

## 実装

最初は最小構成でよい。

- preprocessing
- normalized correlation
- Oracle classifier
- score ranking

入力：

```text
audio event
```

出力：

```text
oracle
best score
second candidate
second score
```

GUIへ表示する。

## 完了条件

静かな環境で、

```text
7種類のOracle
```

を正常に区別できる。

この段階ではVoG Sequence処理不要。

---

# Phase 5：スペクトル解析追加

## 実装

- STFT
- spectral template
- cosine similarity
- waveform + spectrum score
- configurable weights

結果：

```text
Waveform score
Spectrum score
Combined score
```

を内部で保持する。

Debug UIでは確認可能にする。

## 完了条件

Replay DatasetでPhase 4より認識率が悪化しない。

ノイズ付き音源で改善することを確認する。

---

# Phase 6：Confidence Engine

## 実装

- best threshold
- second candidate margin
- HIGH/MEDIUM/LOW/REJECTED
- duplicate cooldown
- uncertain event logging

## 完了条件

曖昧な音を無理にOracleへ分類しない。

例えば、

```text
L2 0.83
R1 0.82
```

をHIGH扱いしない。

---

# Phase 7：VoG Sequence Engine

## 実装

Finite State Machine：

```text
IDLE
ARMED
PASS_1
WAIT_PASS_2
PASS_2
VERIFY
CONFIRMED
UNCERTAIN
LOCKOUT
```

Round：

```text
3
4
5
6
7
```

個。

PASS1/PASS2を独立保存。

## 完了条件

録音済みの実際のOracle音声を再生し、

```text
PASS1
PASS2
```

を正しく分離できる。

---

# Phase 8：2回照合

## 実装

PassComparator。

完全一致：

```text
CONFIRMED
```

不一致：

```text
MISMATCH
```

個別confidenceから推定：

```text
INFERRED
```

を実装。

## 完了条件

意図的に1箇所誤認識させたテストデータで、

```text
どの位置が不一致か
```

を正しく特定できる。

---

# Phase 9：VoGルール補正

## 実装

- 同一Pass内重複検出
- expected sequence length
- candidate history
- top-N candidate保存
- sequence reconstruction

再構成時には、

```text
Oracle重複なし
PASS1/PASS2最大一致
confidence総和最大
```

などを評価する。

## 完了条件

1箇所程度の低confidence誤認識を、自動補正または「確認必要」として扱える。

誤った順番をCONFIRMED表示してはならない。

---

# Phase 10：Live GUI完成

## 実装

- Oracle Map
- Round
- Pass1
- Pass2
- Final Sequence
- Confidence
- State indicator
- Audio device
- Reset
- Next Round

## 完了条件

通常プレイでLive画面だけ見れば、

```text
現在何ラウンドか
何個認識したか
確定順
認識が怪しいか
```

が即座に把握できる。

---

# Phase 11：Overlay

## 実装

- Frameless
- Always On Top
- Transparent
- Click Through
- Drag position mode
- Opacity
- Scale
- Sequence mode
- Map mode

## 完了条件

Destiny 2 Borderless Window環境でゲーム上へ表示できる。

通常時はクリック操作を妨害しない。

---

# Phase 12：Calibration UI

## 実装

各Oracleについて、

```text
Record
Import
Listen
Delete
Quality Check
```

をGUI化。

サンプル数を表示。

## 完了条件

コードやフォルダを直接操作せず、新しい環境用Oracleサンプルを作成できる。

---

# Phase 13：Replay / Debug

## 実装

WAV解析画面。

Timeline：

```text
00:01.23 L2 96%
00:02.41 R1 94%
00:03.72 MID 98%
```

Score detailも確認できるようにする。

## 完了条件

誤認識したWAVを何度でも同条件で再解析できる。

---

# Phase 14：Dataset評価

## 実装

- expected metadata
- batch analysis
- accuracy
- rejection rate
- false positive
- per-Oracle accuracy
- confusion matrix CSV

## 完了条件

認識アルゴリズム変更前後で精度を数値比較できる。

---

# Phase 15：実戦ログ改善

実際のVoGで使用し、

以下を収集する。

```text
成功例
低confidence例
誤認識例
大量の戦闘音が重なった例
```

誤認識音声について、

```text
なぜ誤認識したか
```

をReplayで分析する。

必要に応じて、

- threshold
- spectral weight
- waveform weight
- filter
- template

を調整する。

推測でアルゴリズムを変更せず、Dataset評価結果を基準に変更する。

---

# Phase 16：UX改善

実戦使用を前提に、

- 視認性
- Overlayサイズ
- Reset操作
- 自動Round移行
- Device reconnect
- Settings
- Hotkey

を改善する。

エラーをコンソールだけに表示しない。

ユーザー向けエラーと開発用ログを分離する。

---

# Phase 17：配布版作成

PyInstallerを使用する。

最初は、

```text
--onedir
```

形式で作成する。

安定確認後、

```text
--onefile
```

も必要なら検討する。

配布物例：

```text
OracleAssistant/
 ├─ OracleAssistant.exe
 ├─ README.txt
 └─ licenses/
```

初回起動でユーザーデータフォルダを自動生成する。

---

# Phase 18：最終テスト

最低以下を確認する。

### 音声

- WASAPI Loopback
- VoiceMeeter
- 44100 Hz
- 48000 Hz
- stereo
- device reconnect

### Oracle

- 7種類単独
- Round1
- Round2
- Round3
- Round4
- Round5
- PASS1/PASS2一致
- PASS不一致
- LOW confidence
- duplicate

### GUI

- Main Window
- Overlay
- DPI scaling
- 100%
- 125%
- 150%
- multi monitor

### Lifecycle

- 起動
- Start
- Stop
- Start
- device変更
- 終了
- 再起動

---

# 50. テスト方針

可能なロジックはpytestでUnit Testを書く。

特に、

```text
ConfidenceEvaluator
SequenceEngine
PassComparator
Validator
ConfigManager
```

は音声デバイスなしでもテスト可能にする。

例：

```python
def test_duplicate_oracle_is_rejected():
    ...

def test_matching_passes_are_confirmed():
    ...

def test_low_confidence_mismatch_is_inferred():
    ...

def test_round_5_requires_7_oracles():
    ...
```

---

# 51. 音声アルゴリズムとUIを分離する

以下のようなコードは禁止。

```python
def detect_oracle():
    ...
    label.setText("L2")
```

代わりに、

```python
DetectionResult
```

を返す。

例：

```python
@dataclass
class DetectionResult:
    timestamp: float
    oracle: OracleId | None

    confidence: float

    waveform_score: float
    spectrum_score: float

    second_candidate: OracleId | None
    second_score: float

    status: ConfidenceLevel
```

UI側がこれを受け取って表示する。

---

# 52. 時刻

イベント順の管理には可能な限り、

```python
time.monotonic()
```

を使用する。

ログ表示用の日時と、

```text
シーケンス内部時間
```

を分離する。

Windows時計変更で内部判定が壊れないようにする。

---

# 53. Debug Mode

CLIまたは設定でDebug Modeを用意する。

Debug Modeでは、

```text
audio RMS
event detection
classifier scores
state transitions
sequence
```

をログへ出す。

Release版では大量ログを初期状態で出さない。

---

# 54. 開発上の原則

実装中に不確かな点が出た場合、推測で大規模に書き進めない。

以下の優先順で対応する。

```text
1. 現行コードを確認
2. 再現テスト作成
3. 問題を再現
4. 原因を特定
5. 最小修正
6. テスト
```

既存機能を壊して別機能を追加しない。

Phase完了時には既存テストをすべて実行する。

---

# 55. コーディング規約

- Python type hintを可能な限り付ける
- dataclassを適切に使う
- 巨大なmain.pyを作らない
- 1クラスへ全責務を集中させない
- magic numberを避ける
- threshold類はconfigへ置く
- pathはpathlib.Pathを使用
- bare except禁止
- printによる恒常的デバッグは禁止
- loggingを使用
- Windows pathを文字列連結しない

---

# 56. README

READMEには最低以下を書く。

```text
概要
必要環境
インストール
起動
音声デバイス設定
WASAPI Loopback設定
VoiceMeeter使用時
Calibration
Live Mode
Overlay
Replay
Troubleshooting
ログ保存場所
アンインストール
```

---

# 57. 初期リリースの必須機能

v1.0として必須：

```text
WASAPI audio capture
VoiceMeeter input
7 Oracle classification
Multiple templates
Waveform matching
Spectrum matching
Confidence
PASS1/PASS2
Sequence verification
Oracle Map
Text sequence
Overlay
Calibration
Replay
Uncertain audio logging
Settings
PyInstaller build
```

---

# 58. v1.0では不要な機能

最初から以下を実装しない。

```text
Deep Learning
Cloud recognition
Online account
Automatic update server
Telemetry
Bungie API
Remote server
Mobile app
```

必要になった場合のみ後から追加する。

---

# 59. 最終的な理想動作

ユーザーがOracle遭遇へ入る。

アプリ：

```text
LISTENING
Round 3
```

Oracleの1回目：

```text
PASS 1

L2 → R1 → MID → L3 → R2
```

Oracleの2回目：

```text
PASS 2

L2 → R1 → MID → L3 → R2
```

自動比較：

```text
✓ CONFIRMED

1 L2
2 R1
3 MID
4 L3
5 R2

Confidence 97%
```

Overlay：

```text
✓  L2 → R1 → M → L3 → R2
```

---

# 60. 誤認識時の理想動作

PASS1：

```text
L2 → R1 → MID → L3 → R2
```

PASS2：

```text
L2 → R1 → MID → L2 → R2
```

内部結果：

```text
PASS1 index4
L3 96%

PASS2 index4
L2 61%
L3 57%
```

出力：

```text
⚠ CHECK

L2 → R1 → MID → L3? → R2

Estimated confidence: 89%
```

この場合に「CONFIRMED」と表示してはならない。

---

# 61. 本プロジェクトで最も重要な考え方

このアプリの目的は、

```text
最も可能性が高いOracleを必ず表示する
```

ことではない。

目的は、

```text
十分な根拠があるOracleだけを確定し、
怪しい場合には怪しいこと自体をユーザーへ知らせる
```

ことである。

したがって、

```text
誤った確定
```

より、

```text
UNCERTAIN
```

の方が望ましい。

Destiny 2ソロVoGでは誤った順序を提示することによる損失が大きいため、precisionを重視する。

---

# 62. Codexへの作業指示

上記Phase 0から順番に実装すること。

一度に全Phaseを雑に作成してはならない。

各Phaseについて必ず、

```text
1. 実装内容を確認
2. 必要なファイルを作成・変更
3. Unit Testを追加
4. 実際にテストを実行
5. エラーを修正
6. 完了条件を確認
7. 次Phaseへ進む
```

という流れで進めること。

既存コードを確認せずに全面書き換えしない。

各Phase終了時に、

```text
実装した内容
変更したファイル
実行したテスト
テスト結果
既知の問題
次Phaseの作業
```

を簡潔に報告すること。

必要な情報がリポジトリ内から取得できる場合は、ユーザーへ質問する前にコード、設定、ログ、テストデータを確認すること。

明らかな実装方針が決まっている部分について逐一確認を求めず、上記仕様を基準として実装を進めること。

---

# 63. 最初に行う作業

まずPhase 0のみ実行すること。

Phase 0完成後、

- プロジェクト構造
- requirements
- config設計
- 起動方法
- テスト結果

を提示する。

その後Phase 1へ進む。

ただし、ユーザーが「全Phaseを継続して実装してよい」と指示している場合は、各Phaseのテストと完了条件を確認しながらPhase 18まで継続してよい。

最終的には、

```text
OracleAssistant.exe
```

としてWindows上で単独起動できる状態を完成とする。