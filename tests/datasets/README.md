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
