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
