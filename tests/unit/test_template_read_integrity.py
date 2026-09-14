"""The returned clip must still match metadata after a concurrent file change."""
import pytest
from audio.data import AudioDataError
from templates.manager import TemplateManager
from tests.unit.test_classifier import oracle_wave

@pytest.mark.parametrize("original",[False,True])
def test_change_during_final_audio_read_is_rejected(tmp_path,monkeypatch,original):
    import templates.manager as module
    manager=TemplateManager(tmp_path)
    sample=manager.add("L1",oracle_wave(0))
    read=module.read_wav
    calls=[]
    def changed(*args,**kwargs):
        calls.append(args[0])
        if len(calls)==3:
            return oracle_wave(4)
        return read(*args,**kwargs)
    monkeypatch.setattr(module,"read_wav",changed)
    with pytest.raises(AudioDataError,match="変更"):
        manager.load_audio("L1",sample.metadata.sample_id,original=original)
    assert sample.path.exists()
