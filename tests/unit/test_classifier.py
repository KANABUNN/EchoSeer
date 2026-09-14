"""Seven-class ranking, aggregation, symmetric processing and fresh storage."""
from threading import Event
import numpy as np
import pytest

from audio.data import AudioClip,AudioDataError
from audio.operations import OperationCancelled
from dsp.bandpass import BandpassSettings,bandpass_audio
from detector.classifier import OracleClassifier,TemplateWaveform
from encounter.vog_oracles import OracleId
from templates.manager import TemplateManager


def oracle_wave(index, rate=48000, phase=0, amplitude=.2, dc=0, seconds=.12):
    t=np.arange(round(rate*seconds))/rate
    frequency=420+index*137
    envelope=np.sin(np.pi*np.arange(len(t))/len(t))**2
    wave=envelope*(np.sin(2*np.pi*frequency*t+phase)+.3*np.sin(2*np.pi*frequency*2*t+phase*.4))
    return AudioClip((amplitude*wave+dc).astype(np.float32),rate)


def seven_templates():
    return tuple(TemplateWaveform(oracle,str(i),oracle_wave(i))
                 for i,oracle in enumerate(OracleId))


@pytest.mark.parametrize("index,oracle",list(enumerate(OracleId)))
@pytest.mark.parametrize("rate",[44100,48000])
def test_seven_distinct_tones_with_gain_dc_phase_rate_and_padding(index,oracle,rate):
    query=oracle_wave(index,rate,phase=.15,amplitude=.07,dc=.08)
    samples=np.stack((query.samples[:,0],query.samples[:,0]*.6),axis=1)
    samples=np.pad(samples,((round(rate*.013),round(rate*.02)),(0,0)))
    result=OracleClassifier(templates=seven_templates(),waveform_weight=1,spectrum_weight=0).classify(AudioClip(samples,rate))
    assert result.oracle==oracle and result.status=="RANKED"
    assert result.best_score>.95 and result.second_score<.2
    assert not result.missing_oracles and result.sample_count==7
    assert result.margin==pytest.approx(result.best_score-result.second_score)
    assert [r.score for r in result.ranking]==sorted([r.score for r in result.ranking],reverse=True)


def test_multiple_samples_group_by_oracle_and_top_n_mean_changes_aggregation():
    query=oracle_wave(0)
    templates=(
        TemplateWaveform(OracleId.L1,"exact",query),
        TemplateWaveform(OracleId.L1,"weak",oracle_wave(3)),
        TemplateWaveform(OracleId.R1,"other",oracle_wave(1)),
    )
    best=OracleClassifier(templates=templates,waveform_weight=1,spectrum_weight=0).classify(query)
    mean=OracleClassifier(templates=templates,aggregation="top_n_mean",top_n=2,waveform_weight=1,spectrum_weight=0).classify(query)
    assert best.best_candidate==mean.best_candidate==OracleId.L1
    assert best.best_score==pytest.approx(1,abs=1e-10)
    scores=mean.ranking[0].samples
    assert mean.best_score==pytest.approx(np.mean([s.match.score for s in scores]))
    assert mean.best_score<best.best_score and len(scores)==2
    assert len(mean.ranking)==2 and len(mean.missing_oracles)==5


def test_identical_templates_tie_without_forcing_an_oracle():
    query=oracle_wave(0)
    templates=tuple(TemplateWaveform(oracle,str(i),query) for i,oracle in enumerate(OracleId))
    result=OracleClassifier(templates=templates).classify(query)
    assert result.status=="TIED" and result.oracle is None
    assert result.best_candidate==OracleId.L1 and result.second_candidate==OracleId.L2
    assert result.margin==pytest.approx(0)


@pytest.mark.parametrize("kind",["empty","short","long","silence","dc","reverse_phase"])
def test_unusable_event_never_gets_a_forced_oracle(kind):
    values=oracle_wave(0).samples
    if kind=="empty": values=np.empty((0,1))
    elif kind=="short": values=values[:100]
    elif kind=="long": values=np.ones((480001,1))
    elif kind=="silence": values=np.zeros_like(values)
    elif kind=="dc": values=np.full_like(values,.2)
    elif kind=="reverse_phase": values=np.concatenate((values,-values),axis=1)
    result=OracleClassifier(templates=seven_templates()).classify(AudioClip(values,48000))
    assert result.oracle is None and not result.ranking
    assert result.notices


def test_no_or_only_one_template_has_honest_missing_candidates():
    clip=oracle_wave(0)
    empty=OracleClassifier().classify(clip)
    assert empty.status=="NO_TEMPLATES" and empty.best_score is None
    single=OracleClassifier(templates=(TemplateWaveform(OracleId.MID,"one",clip),)).classify(clip)
    assert single.oracle==OracleId.MID
    assert single.second_candidate is None and single.second_score is None and single.margin is None
    assert len(single.missing_oracles)==6


def test_store_changes_are_used_on_the_next_classification(tmp_path):
    manager=TemplateManager(tmp_path)
    a=manager.add("L1",oracle_wave(0))
    classifier=OracleClassifier(manager)
    assert classifier.classify(oracle_wave(0)).oracle==OracleId.L1
    b=manager.add("R3",oracle_wave(0))
    assert classifier.classify(oracle_wave(0)).status=="TIED"
    manager.delete("L1",a.metadata.sample_id)
    result=classifier.classify(oracle_wave(0))
    assert result.oracle==OracleId.R3 and result.sample_count==1
    b.path.write_bytes(b"broken")
    result=classifier.classify(oracle_wave(0))
    assert result.status=="NO_TEMPLATES" and result.notices


def test_original_audio_load_and_old_template_rate_are_supported(tmp_path):
    manager=TemplateManager(tmp_path,44100)
    original=oracle_wave(2,rate=44100)
    sample=manager.add("L3",original)
    loaded=manager.load_audio("L3",sample.metadata.sample_id,original=True)
    np.testing.assert_array_equal(loaded.samples,original.samples)
    result=OracleClassifier(manager,48000).classify(oracle_wave(2,48000))
    assert result.oracle==OracleId.L3 and result.best_score>.999


def test_bandpass_is_symmetric_for_query_and_template():
    query=oracle_wave(1,seconds=.3)
    classifier=OracleClassifier(templates=(TemplateWaveform(OracleId.R2,"one",query),),
                                bandpass=BandpassSettings(350,1800))
    result=classifier.classify(query)
    assert result.oracle==OracleId.R2 and result.best_score==pytest.approx(1,abs=1e-10)
    t=np.arange(48000)/48000
    wave=np.sin(2*np.pi*800*t)+np.sin(2*np.pi*7000*t)
    filtered=bandpass_audio(wave,48000,BandpassSettings(500,2000))
    spectrum=np.abs(np.fft.rfft(filtered[2000:-2000]))
    freq=np.fft.rfftfreq(len(filtered[2000:-2000]),1/48000)
    low=spectrum[np.argmin(abs(freq-800))]
    high=spectrum[np.argmin(abs(freq-7000))]
    assert high<low*.01


@pytest.mark.parametrize("kwargs",[
    {"sample_rate":0},{"aggregation":"unknown"},{"top_n":0},{"top_n":True},
    {"bandpass":BandpassSettings(1000,25000)},
])
def test_classifier_settings_are_validated(kwargs):
    with pytest.raises((ValueError,AudioDataError)):
        OracleClassifier(**kwargs)


def test_bank_limit_fails_whole_comparison_and_cancel_never_returns_old_result(monkeypatch):
    import detector.classifier as module
    monkeypatch.setattr(module,"MAX_BANK_BYTES",1)
    result=OracleClassifier(templates=seven_templates()).classify(oracle_wave(0))
    assert result.status=="LIMIT" and result.oracle is None and not result.ranking
    cancel=Event()
    cancel.set()
    with pytest.raises(OperationCancelled):
        OracleClassifier(templates=seven_templates()).classify(oracle_wave(0),cancel)
