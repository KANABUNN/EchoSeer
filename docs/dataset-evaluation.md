# Dataset評価（Phase 14）

「評価」タブでDatasetのJSONを選び、「一括評価」を押します。
「比較元を選ぶ」で過去のreport.jsonを指定し、同じWAV・正解での差を表示できます。
「比較を解除」で単独評価へ戻ります。「レポート・CSV保存」は新しいサブフォルダーへ保存します。

## 正解情報

JSONはUTF-8です。WAVはJSONと同じフォルダーか、その配下に置きます。

```json
{
  "schema_version": 1,
  "description": "手動確認した録音",
  "tolerance_seconds": 0.15,
  "cases": [
    {"id": "single", "file": "audio/L2.wav", "mode": "clip", "expected": ["L2"]},
    {"id": "background", "file": "audio/noise.wav", "mode": "clip", "expected": []},
    {
      "id": "round-1", "file": "audio/round-1.wav", "mode": "round",
      "round": 1, "expected": ["L2", "R1", "MID"]
    },
    {
      "id": "timed", "file": "audio/events.wav", "mode": "events",
      "expected": ["L2", "R1"],
      "timestamps": [{"start": 0.5, "end": 2.5}, {"start": 3.0, "end": 5.0}],
      "category": "combat_noise", "source_kind": "gameplay", "notes": "手動確認済み"
    }
  ]
}
```

ファイル名・時刻は例です。実際に確認した録音と正解に置き換えてください。
認識結果をそのまま正解にコピーしません。

| mode | 正解 | 解析 |
|---|---|---|
| clip | 単独Oracle 1種類、またはOracleなしの空配列 | 全体を1音として比較 |
| events | 時間順の全Oracle。任意で各音の区間を指定 | 全区間のTimeline |
| round | 両PASSに共通する3〜7種類を1回だけ指定 | Round 1〜5のFSM・2回照合 |

roundのイベント正解はexpectedを2回繰り返した列です。
推定結果を確定順の成功に含めません。期待個数・一意性が必要です。
timestampsはWAV内の**全Oracle**を注釈する、重ならない開始・終了秒です。
予測のOracleを使わず発音時刻から対応付けます。時刻なしは順序の編集距離で対応付けます。

正解IDはL1 / L2 / L3 / MID / R1 / R2 / R3です。
任意のcategoryはsuccess / low_confidence / incorrect / combat_noise、
source_kindはunspecified / gameplay / isolated / syntheticです。notesも指定できます。

## 指標

| 指標 | 定義 |
|---|---|
| 正解率 | 正しく認識したOracle / 正解Oracle数。unknown・欠落も失敗 |
| 採用した音の正解率 | 正しいOracle / 採用した全音。誤認識・余分な採用も分母 |
| 棄却率 | unknownの予測数 / 全予測数。背景のunknownも含む |
| Oracleなしケースの誤検出率 | 1つでもOracleを採用した負例 / expectedが空の全ケース |
| 誤検出数/分 | 余分に採用した音 / 全Oracleの時刻注釈があるWAVの合計分数 |
| Round確定順の正解率 | 正解と一致するCONFIRMED / roundケース数 |

分母がない場合は「—」です。予測がないケースを棄却率100%にしません。
正解音のunknown、欠落、余分な採用、背景・余分なunknownを別に記録します。
時刻なしの余分な音は順序対応付けの結果で、背景音の時刻別誤検出率を算出しません。

report.jsonには各ケースの対応付け、WAV / native音声のchecksum、全7候補のスコア、条件・定義を保存します。
confusion-matrix.csvはUTF-8 BOM付きです。行は正解OracleとNO_ORACLE、列は予測OracleとUNKNOWN / MISSINGです。

## 設定比較とCLI

WAVのSHA-256、正解、mode、時刻、Round、許容時間、ケースIDからDatasetを識別します。
異なる音声・正解との比較と、評価中のWAV・テンプレート変更を拒否します。
条件には元テンプレートのchecksumも含めます。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset C:\data\dataset.json --output .runtime\baseline
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset C:\data\dataset.json --config C:\data\candidate.json --baseline .runtime\baseline\report.json --output .runtime\candidate
```

candidate.jsonはAppConfig全体の設定JSONです。CLIはインストール済み設定を変更しません。
--config省略時は初期認識設定、--templates省略時は通常保存先のテンプレートを使います。
出力先は新しいフォルダーを指定します。JSONとCSVを両方書き終えて公開し、既存レポートを保持します。

## 提供サンプルでの確認

```powershell
.\.venv\Scripts\python.exe scripts\prepare_example_dataset.py --output .runtime\demo
.\.venv\Scripts\python.exe scripts\evaluate_dataset.py --dataset .runtime\demo\dataset.json --output .runtime\demo-report
```

7単独音、無音と白色ノイズ、全5ラウンド、時刻付き6音と背景ノイズの15ケースです。
元A〜Gと対応表を使い、元ファイルを変更しません。--samplesで素材フォルダーを指定できます。

確認結果は正解63/63、余分な採用0、負例2ケースの誤検出0、全5ラウンドの確定順が一致です。
棄却率は背景unknown 3 / 全予測66 = 4.55%です。
波形のみ設定との比較は全指標で差0でした。既定の60% / 40%設定を維持しています。
テンプレートと評価音声は同じ元録音、提示間隔とノイズは人工条件です。独立した実戦精度の測定ではありません。

## 上限

500ケース、JSON 2 MiB、WAV合計2 GiB / 2時間以内です。
clipは10秒、events / roundは120秒以内、1 WAVは共通の128 MiB制限があります。
レポートは64 MiB以内です。大きなDatasetは分割してください。
既存のReplayワーカーで処理し、途中で中止できます。
