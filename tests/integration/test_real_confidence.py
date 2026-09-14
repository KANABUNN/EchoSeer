"""Supplied samples show ranking correctness is distinct from confidence acceptance."""
import numpy as np
import pytest
from audio.data import AudioClip
from audio.waveio import read_wav
from detector.classifier import OracleClassifier,TemplateWaveform
from detector.confidence import ConfidenceEngine
from encounter.vog_oracles import OracleId
from tests.integration.test_real_oracles import MAPPING,SAMPLES


def test_supplied_recordings_high_self_matches_and_unknown_disjoint_and_noise():
    if not all((SAMPLES/(n+".wav")).is_file() for n in MAPPING):
        pytest.skip("Optional local samples/A.wav..G.wav are not supplied")
    clips={n:read_wav(SAMPLES/(n+".wav")) for n in MAPPING}
    whole=tuple(TemplateWaveform(OracleId(MAPPING[n]),n,c) for n,c in clips.items())
    first=tuple(TemplateWaveform(OracleId(MAPPING[n]),n,AudioClip(c.samples[:c.sample_rate],c.sample_rate))
                for n,c in clips.items())
    engine=ConfidenceEngine()
    full_classifier=OracleClassifier(templates=whole)
    disjoint_classifier=OracleClassifier(templates=first)
    for name,oracle in MAPPING.items():
        full=engine.evaluate(full_classifier.classify(clips[name]))
        assert full.oracle==OracleId(oracle) and full.status=="HIGH"
        clip=clips[name]
        tail=AudioClip(clip.samples[clip.sample_rate:round(clip.sample_rate*1.75)],clip.sample_rate)
        raw=disjoint_classifier.classify(tail)
        assert raw.best_candidate==OracleId(oracle)
        decision=engine.evaluate(raw)
        assert decision.oracle is None and decision.status in ("LOW","REJECTED")
    clip=clips["B"]
    query=clip.samples[clip.sample_rate:round(clip.sample_rate*1.75)]
    power=float(np.mean(np.square(query,dtype=np.float64)))
    noise=np.random.default_rng(17).normal(0,np.sqrt(power)*10,query.shape).astype(np.float32)
    decision=engine.evaluate(disjoint_classifier.classify(AudioClip(query+noise,clip.sample_rate)))
    assert decision.status=="REJECTED" and decision.oracle is None
