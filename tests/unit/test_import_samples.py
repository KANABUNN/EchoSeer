"""Mapped sample import is explicit, idempotent and preserves user data."""
from pathlib import Path
import pytest

from audio.data import AudioDataError
from audio.waveio import write_wav
from scripts.import_samples import import_samples
from templates.manager import TemplateManager
from tests.unit.test_classifier import oracle_wave

MAPPING={"A.wav":"L3","B.wav":"R3","C.wav":"MID","D.wav":"L1","E.wav":"R1","F.wav":"L2","G.wav":"R2"}


def sources(tmp_path):
    root=tmp_path/"sources"
    root.mkdir()
    for i,name in enumerate(MAPPING):
        write_wav(root/name,oracle_wave(i))
    return root


def test_mapped_import_is_idempotent_and_preserves_original_source(tmp_path):
    source=sources(tmp_path)
    before={p.name:p.read_bytes() for p in source.iterdir()}
    manager=TemplateManager(tmp_path/"store")
    result=import_samples(source,manager,MAPPING)
    assert len(result.added)==7 and not result.skipped
    result=import_samples(source,manager,MAPPING)
    assert not result.added and len(result.skipped)==7
    assert len(manager.catalog().samples)==7
    assert {p.name:p.read_bytes() for p in source.iterdir()}==before
    assert {s.metadata.source_name:s.metadata.oracle for s in manager.catalog().samples}==MAPPING


@pytest.mark.parametrize("mapping",[
    {},{"../A.wav":"L3",**{k:v for k,v in MAPPING.items() if k!="A.wav"}},
    {**MAPPING,"G.wav":"L1"}, {**MAPPING,"G.wav":"invalid"},
])
def test_invalid_mapping_never_publishes(tmp_path,mapping):
    with pytest.raises((AudioDataError,ValueError)):
        import_samples(tmp_path,TemplateManager(tmp_path/"store"),mapping)
    assert not (tmp_path/"store").exists()


def test_missing_source_never_partially_publishes(tmp_path):
    source=sources(tmp_path)
    (source/"G.wav").unlink()
    manager=TemplateManager(tmp_path/"store")
    with pytest.raises(OSError):
        import_samples(source,manager,MAPPING)
    assert not manager.root.exists()
