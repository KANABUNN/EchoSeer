"""Optional supplied game recordings; disjoint portions are not new recordings."""
from pathlib import Path
import pytest
from audio.data import AudioClip
from audio.waveio import read_wav
from detector.classifier import OracleClassifier,TemplateWaveform
from encounter.vog_oracles import OracleId

SAMPLES=Path(__file__).resolve().parents[2]/"samples"
MAPPING={"A":"L3","B":"R3","C":"MID","D":"L1","E":"R1","F":"L2","G":"R2"}

@pytest.mark.parametrize("mode",["whole","disjoint"])
@pytest.mark.parametrize("name,oracle",list(MAPPING.items()))
def test_supplied_game_recording_seven_way_ranking(name,oracle,mode):
    if not all((SAMPLES/(n+".wav")).is_file() for n in MAPPING):
        pytest.skip("Optional local samples/A.wav..G.wav are not supplied")
    clips={n:read_wav(SAMPLES/(n+".wav")) for n in MAPPING}
    templates=tuple(TemplateWaveform(OracleId(MAPPING[n]),n,
        c if mode=="whole" else AudioClip(c.samples[:c.sample_rate],c.sample_rate))
        for n,c in clips.items())
    clip=clips[name]
    query=clip if mode=="whole" else AudioClip(clip.samples[clip.sample_rate:round(clip.sample_rate*1.75)],clip.sample_rate)
    result=OracleClassifier(templates=templates).classify(query)
    assert result.oracle==OracleId(oracle) and len(result.ranking)==7
    assert result.best_score>result.second_score
