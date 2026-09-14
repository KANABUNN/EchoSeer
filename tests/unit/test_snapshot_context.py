"""The copied audio and its time/session metadata always describe the same write."""
import numpy as np
from audio.ring_buffer import RingBuffer
from audio.sources import LiveSource


def test_snapshot_context_survives_wrap_and_oversized_writes(monkeypatch):
    import audio.ring_buffer as module
    monkeypatch.setattr(module.time,"monotonic",lambda:10.)
    ring=RingBuffer(8000,2,.01)
    values=np.arange(200*2,dtype=np.float32).reshape(200,2)
    ring.write(values)
    source=LiveSource(ring,20)
    clip=source.read()
    context=source.event_context
    assert (context.timestamp,context.start_frame,context.end_frame)==(10,180,200)
    np.testing.assert_array_equal(clip.samples,values[-20:])
    ring.write(values[:30])
    source=LiveSource(ring)
    source.read()
    assert (source.event_context.start_frame,source.event_context.end_frame)==(150,230)


def test_frozen_ring_has_stable_time_and_clear_changes_session(monkeypatch):
    import audio.ring_buffer as module
    clock=[10.]
    monkeypatch.setattr(module.time,"monotonic",lambda:clock[0])
    ring=RingBuffer(8000,1)
    ring.write(np.ones((80,1),np.float32))
    source=LiveSource(ring)
    source.read();first=source.event_context
    clock[0]=999
    source.read()
    assert source.event_context==first
    ring.clear();ring.write(np.ones((80,1),np.float32))
    source.read()
    assert source.event_context.stream_id!=first.stream_id
    assert source.event_context.timestamp==999 and source.event_context.start_frame==0
