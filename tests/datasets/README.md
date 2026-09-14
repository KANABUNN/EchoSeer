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
