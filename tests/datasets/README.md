# 評価用 Dataset

実音声を用意できた段階で WAV と expected metadata を追加します。
現時点で音声データは同梱していません。

```json
{
  "file": "round5_test01.wav",
  "expected": ["L2", "R1", "MID", "L3", "R2", "L1", "R3"]
}
```

順序のみの評価と、timestamp 付き false positive rate の評価を区別します。

## Phase 5 の比較レシピ

phase5-oracles.json は静かな14ケースと固定白色ノイズ84ケースの生成条件です。
元の7音声はローカル samples/A.wav〜G.wav、位置対応は docs/sample-mapping.json。
scripts/evaluate_phase5.py で WAV を生成・再読込して波形のみと複合順位を比較します。
音声・画像・生成 Dataset・report.json はGit対象外です。
詳細は docs/recognition.md / docs/acceptance.md を参照してください。

これは同じ録音と人工ノイズの比較で、独立録音・実戦・VoGの順序評価ではありません。

## Phase 7 のPASS分離レシピ

[phase7-oracles.json](phase7-oracles.json) は A〜G の実録音に無音を挟み、
Round 1〜5の3 / 4 / 5 / 6 / 7個を2回提示に組む条件です。
scripts/evaluate_phase7.py は全5ラウンドと欠落・不一致・ノイズ・途中切れ・短いPASS間隔の
計10 WAVを生成・再読込し、独立したPASS・状態・フレーム時刻・元ファイルの保全を検証します。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_phase7.py
```

元の提供音声がある環境では test_phase7_dataset.py も実行します。
出力は .runtime/phase7-evaluation/、音声は同梱しません。
声は実録音、無音・連結・提示間隔は人工設定です。実戦の通し録音の評価とは区別します。
詳細は [sequence.md](../../docs/sequence.md)。

## Phase 8–9 の照合・補正ケース

scripts/evaluate_phase89.pyは同じ提供録音を2回提示に組み、
特定位置に分類スコアの誤りを明示的に注入する8ケースです。
元の順位と注入した順位を両方記録し、補正結果がCONFIRMEDにならないことを確認します。
正常、1位置LOW、HIGH重複、両PASS LOW重複、弱い根拠、同点、2位置LOW、HIGH不一致を扱います。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_phase89.py
```

出力は .runtime/phase89-evaluation/、詳細は [verification.md](../../docs/verification.md)。
Phase 7のレシピも現在は完全一致CONFIRMED・異常UNCERTAINの照合まで検証します。
実戦の自然な誤認識・通し録音・独立録音の精度とは区別します。

## Phase 10の連続Live比較

scripts/evaluate_live.pyはPhase 7の音声レシピを連続Liveへ渡します。
全5ラウンド、異なるchunk境界・長いduplicate cooldown、別Oracleの実録音を使う不一致の7ケースです。
有限Replayとの元のPASS列の一致、正常だけのCONFIRMED、元WAV保全を検証します。

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_live.py
```

出力は.runtime/live-evaluation/。提供音声がある場合はtest_live_dataset.pyでも実行します。
音声とテンプレートは同じ元録音、無音・連結・提示間隔は人工条件です。
独立録音や実戦の通し録音での精度とは区別します。詳細は[live-overlay.md](../../docs/live-overlay.md)。

## Phase 14–15のラベル付き評価

scripts/prepare_example_dataset.pyは元A〜Gを保ち、単独音7、負例2、両PASSの全5ラウンド、時刻付き6音とノイズの15ケースを生成します。
正解はdocs/sample-mapping.jsonと元音声の挿入時刻から作り、認識結果から生成しません。
scripts/evaluate_dataset.pyで評価・比較・report.json / confusion-matrix.csv保存を行います。
テンプレートと音声は同じ元録音、提示間隔・無音・ノイズは人工条件です。
[Dataset評価](../../docs/dataset-evaluation.md) / [手動記録](../../docs/gameplay-review.md)を参照してください。
