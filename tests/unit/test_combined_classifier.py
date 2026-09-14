"""Component retention, weights, aggregation and seven-way combined ranking."""
import numpy as np
import pytest
from detector.classifier import OracleClassifier, TemplateWaveform
from dsp.correlation import CorrelationMatch
from encounter.vog_oracles import OracleId
from tests.unit.test_classifier import oracle_wave, seven_templates


@pytest.mark.parametrize("index,oracle", list(enumerate(OracleId)))
@pytest.mark.parametrize("rate", [44100, 48000])
def test_combined_seven_way_ranking_keeps_all_three_scores(index, oracle, rate):
    result = OracleClassifier(templates=seven_templates()).classify(
        oracle_wave(index, rate=rate, phase=.4, amplitude=.03, dc=.1))
    assert result.oracle == oracle and result.best_score > result.second_score
    assert result.waveform_weight == .6 and result.spectrum_weight == .4
    for row in result.ranking:
        assert row.combined_score == pytest.approx(.6*row.waveform_score + .4*row.spectrum_score)
        for sample in row.samples:
            assert sample.combined_score == pytest.approx(.6*sample.waveform_score + .4*sample.spectrum_score)
            assert all(np.isfinite(s) and 0 <= s <= 1 for s in
                       (sample.waveform_score, sample.spectrum_score, sample.combined_score))
    first = result.ranking[0]
    assert first.waveform_score > .98 and first.spectrum_score > .98


def test_weights_select_waveform_spectrum_or_combined_and_validate_candidate_change(monkeypatch):
    import detector.classifier as module
    templates = (TemplateWaveform(OracleId.L1, "wrong-pitch", oracle_wave(0)),
                 TemplateWaveform(OracleId.R1, "right-pitch", oracle_wave(3)))
    query = oracle_wave(3)
    results = []
    for wave, spectrum in ((1,0), (0,1), (.6,.4)):
        scores = iter((.4, .3))
        monkeypatch.setattr(module, "normalized_correlation", lambda *a, **k: CorrelationMatch(next(scores)))
        result = OracleClassifier(templates=templates, waveform_weight=wave, spectrum_weight=spectrum).classify(query)
        results.append(result)
        assert result.ranking[0].score == pytest.approx(
            wave*result.ranking[0].waveform_score + spectrum*result.ranking[0].spectrum_score)
    assert [r.oracle for r in results] == [OracleId.L1, OracleId.R1, OracleId.R1]


def test_top_n_selects_same_samples_for_components_and_combined():
    templates = (TemplateWaveform(OracleId.L1, "exact", oracle_wave(0)),
                 TemplateWaveform(OracleId.L1, "near", oracle_wave(0,phase=.8)),
                 TemplateWaveform(OracleId.L1, "other-pitch", oracle_wave(3)))
    best = OracleClassifier(templates=templates).classify(oracle_wave(0))
    mean = OracleClassifier(templates=templates, aggregation="top_n_mean", top_n=2).classify(oracle_wave(0))
    row = mean.ranking[0]
    chosen = sorted(row.samples, key=lambda s:-s.combined_score)[:2]
    assert row.waveform_score == pytest.approx(np.mean([s.waveform_score for s in chosen]))
    assert row.spectrum_score == pytest.approx(np.mean([s.spectrum_score for s in chosen]))
    assert row.score == pytest.approx(np.mean([s.combined_score for s in chosen]))
    assert mean.best_score <= best.best_score and len(row.samples) == 3


@pytest.mark.parametrize("wave,spectrum", [(-.1,1.1), (0,0), (1,1), (float("nan"),.4), (.6,float("inf")), (True,0)])
def test_invalid_weights_are_rejected(wave, spectrum):
    with pytest.raises(ValueError):
        OracleClassifier(waveform_weight=wave, spectrum_weight=spectrum)
