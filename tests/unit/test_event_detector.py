"""Native event boundaries, hysteresis, bounded windows and truncated/weak audio."""
from threading import Event
import numpy as np
import pytest
from audio.data import AudioClip,AudioDataError
from audio.operations import OperationCancelled
from detector.events import RmsEventDetector


def audio(parts,rate=8000,channels=1):
    arrays=[]
    for seconds,amplitude in parts:
        frames=round(seconds*rate)
        values=(amplitude*np.sin(2*np.pi*440*np.arange(frames)/rate)).astype(np.float32)
        arrays.append(np.tile(values[:,None],(1,channels)))
    return AudioClip(np.concatenate(arrays),rate)


@pytest.mark.parametrize("rate",[44100,48000,192000])
@pytest.mark.parametrize("channels",[1,2])
def test_event_windows_preserve_samples_with_pre_and_post_roll(rate,channels):
    original=audio([(.3,0),(.2,.2),(.5,0),(.2,.2),(.3,0)],rate,channels)
    events=list(RmsEventDetector().detect(original))
    assert len(events)==2 and not any(e.reason for e in events)
    for event in events:
        assert event.start_frame<event.onset_frame<event.signal_end_frame<event.end_frame
        assert event.clip.sample_rate==rate and event.clip.channels==channels
        np.testing.assert_array_equal(event.clip.samples,original.samples[event.start_frame:event.end_frame])
    assert abs(events[0].onset_frame/rate-.3)<=.02
    assert abs(events[1].onset_frame/rate-1)<=.02


@pytest.mark.parametrize("amplitude",[0,.001])
def test_silent_and_weak_audio_do_not_produce_events(amplitude):
    assert not list(RmsEventDetector().detect(audio([(1,amplitude)])))


def test_constant_dc_is_not_an_onset():
    assert not list(RmsEventDetector().detect(AudioClip(np.full((8000,2),.3,np.float32),8000)))


def test_short_quiet_inside_chime_is_merged():
    original=audio([(.2,0),(.12,.2),(.08,0),(.12,.2),(.2,0)])
    assert len(list(RmsEventDetector().detect(original)))==1


def test_short_click_is_retained_as_rejected_candidate():
    event=list(RmsEventDetector().detect(audio([(.2,0),(.02,.2),(.2,0)])))[0]
    assert event.reason=="SHORT_EVENT"


@pytest.mark.parametrize("parts",[
    [(.2,.2),(.2,0)],
    [(.2,0),(.2,.2)],
])
def test_start_and_eof_cuts_are_marked(parts):
    event=list(RmsEventDetector().detect(audio(parts)))[0]
    assert event.reason=="CLIPPED_EVENT"


def test_long_sound_is_one_bounded_rejected_event():
    original=audio([(.2,0),(5,.2),(.2,0)])
    events=list(RmsEventDetector().detect(original))
    assert len(events)==1 and events[0].reason=="EVENT_LIMIT"
    assert events[0].clip.duration_seconds<=3
    assert events[0].signal_end_frame == events[0].end_frame


def test_native_byte_limit(monkeypatch):
    import detector.events as module
    monkeypatch.setattr(module,"MAX_EVENT_BYTES",1024)
    event=list(RmsEventDetector().detect(audio([(.2,0),(.2,.2),(.2,0)],channels=2)))[0]
    assert event.reason=="EVENT_LIMIT" and event.clip.samples.nbytes<=1024


def test_zero_threshold_still_ignores_silence():
    assert not list(RmsEventDetector(0).detect(audio([(1,0)])))


def test_excess_events_are_refused(monkeypatch):
    import detector.events as module
    monkeypatch.setattr(module,"MAX_EVENTS",2)
    original=audio([(.2,0),(.2,.2),(.2,0),(.2,.2),(.2,0),(.2,.2),(.2,0)])
    with pytest.raises(AudioDataError):list(RmsEventDetector().detect(original))


def test_cancel_before_audio_processing():
    cancel=Event();cancel.set()
    with pytest.raises(OperationCancelled):list(RmsEventDetector().detect(audio([(1,0)]),cancel))


def test_strong_retrigger_splits_overlapping_decay_without_release_silence():
    original = audio([(.2, 0), (.16, .2), (.1, .025), (.16, .2), (.2, 0)])
    events = list(RmsEventDetector().detect(original))
    assert len(events) == 2
    assert not any(event.reason for event in events)
    assert events[0].end_frame == events[1].onset_frame
    assert events[1].start_frame < events[1].onset_frame
    assert events[0].signal_end_frame == events[1].onset_frame


def test_small_rise_in_decay_does_not_split_an_event():
    original = audio([(.2, 0), (.16, .2), (.1, .025), (.08, .04), (.2, 0)])
    events = list(RmsEventDetector().detect(original))
    assert len(events) == 1 and not events[0].reason


@pytest.mark.parametrize("retrigger_seconds,retrigger_ratio", [
    (.01, 1.5), (.12, 1.5), (.06, 1.0), (.06, 4.1),
])
def test_invalid_retrigger_settings(retrigger_seconds, retrigger_ratio):
    with pytest.raises(ValueError):
        RmsEventDetector(
            retrigger_seconds=retrigger_seconds, retrigger_ratio=retrigger_ratio
        )


def test_adaptive_noise_floor_detects_a_cue_below_the_fixed_threshold():
    original = audio([(.4, .001), (.2, .02), (.3, .001)])
    events = list(RmsEventDetector().detect(original))
    assert len(events) == 1 and not events[0].reason
    assert events[0].onset_frame / original.sample_rate == pytest.approx(.4, abs=.02)


def test_local_retrigger_splits_quiet_cues_with_audible_residual_tails():
    original = audio([
        (.4, .001),
        (.18, .02), (.9, .006),
        (.18, .02), (.9, .006),
        (.18, .02), (.2, .001),
    ])
    events = list(RmsEventDetector().detect(original))
    assert len(events) == 3
    assert not any(event.reason for event in events)
    assert [event.onset_frame / original.sample_rate for event in events] == pytest.approx(
        [.4, 1.48, 2.56], abs=.02
    )


def test_local_retrigger_does_not_split_a_strong_internal_rise_before_one_second():
    original = audio([
        (.4, .001), (.18, .02), (.66, .006), (.18, .02), (.2, .001),
    ])
    events = list(RmsEventDetector().detect(original))
    assert len(events) == 1 and not events[0].reason
