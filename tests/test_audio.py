import array

import pytest

import py_modules.audio as audio_mod
from py_modules.audio import _level_from_pcm, AudioReactive


def _pcm(amplitude, count=256):
    return array.array("h", [amplitude] * count).tobytes()


def test_level_zero_on_silence():
    assert _level_from_pcm(_pcm(0)) == 0.0


def test_level_uses_decibel_range_for_quiet_audio():
    assert _level_from_pcm(_pcm(80)) == 0.0
    assert _level_from_pcm(_pcm(800)) == 0.5
    assert _level_from_pcm(_pcm(8000)) == 1.0


def test_level_clamps_loud_signal():
    assert _level_from_pcm(_pcm(20000)) == 1.0


def test_level_empty_is_zero():
    assert _level_from_pcm(b"") == 0.0


def test_silence_keeps_the_base_brightness():
    amb = AudioReactive(lambda c: None, zones=2, runtime_dir=None)
    amb._process(0.0, 0.0)
    assert amb.level == pytest.approx(audio_mod.DEFAULT_BASE)


def test_a_beat_flashes_to_full_and_decays_back():
    amb = AudioReactive(lambda c: None, zones=2, runtime_dir=None)
    amb._base = 0.8
    amb._process(0.05, 0.0)
    assert amb.level == pytest.approx(0.8, abs=0.02)

    amb._process(0.9, 0.2)
    assert amb.level > 0.95

    amb._process(0.9, 0.6)
    assert amb.level < 0.9


def test_quiet_sources_are_normalised_by_the_peak_follower():
    amb = AudioReactive(lambda c: None, zones=2, runtime_dir=None)
    meter = 0.0
    for index in range(30):
        meter = amb._process(0.15, index * 0.064)
    assert meter > 0.9

    loud = AudioReactive(lambda c: None, zones=2, runtime_dir=None)
    for index in range(30):
        loud_meter = loud._process(0.9, index * 0.064)
    assert loud_meter > 0.9


def test_aurora_mode_does_not_paint_itself():
    painted = []
    amb = AudioReactive(painted.append, zones=2, runtime_dir=None)
    amb._mode = "aurora"
    amb._process(0.05, 0.0)
    amb._process(1.0, 0.2)
    amb._emit(1.0)
    assert painted == []
    assert amb.level > 0.9


def test_vu_mode_paints_frames():
    painted = []
    amb = AudioReactive(painted.append, zones=2, runtime_dir=None)
    amb._mode = "vu"
    amb._emit(0.7)
    assert len(painted) == 1
    assert any(max(color) > 0 for color in painted[0])


def test_base_brightness_is_clamped():
    assert audio_mod.clamp_base(0.1) == audio_mod.BASE_RANGE[0]
    assert audio_mod.clamp_base(5) == audio_mod.BASE_RANGE[1]
    assert audio_mod.clamp_base("junk") == audio_mod.DEFAULT_BASE
