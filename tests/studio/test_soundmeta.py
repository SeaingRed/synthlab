import numpy as np

from synthlab.analysis.features import extract
from synthlab.studio.soundmeta import band_magnitudes, derive_tags, preset_meta
from synthlab.synth import Patch, preset


def test_band_magnitudes_shape_and_range():
    sr = 44100
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    audio = np.sin(2 * np.pi * 220 * t).astype(np.float64)
    mags = band_magnitudes(audio)
    assert len(mags) == 24
    assert all(0.0 <= m <= 1.0 for m in mags)
    assert max(mags) == 1.0  # relative scaling: loudest band pinned to 1.0


def test_band_magnitudes_relative_false_keeps_absolute_scale():
    sr = 44100
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    audio = np.sin(2 * np.pi * 220 * t).astype(np.float64)
    mags = band_magnitudes(audio, relative=False)
    assert len(mags) == 24
    assert all(0.0 <= m <= 1.0 for m in mags)
    assert max(mags) > 0.2  # the 220 Hz band actually registers, no rescale


def test_band_magnitudes_relative_shows_shape_for_quiet_render():
    sr = 44100
    t = np.linspace(0, 0.5, sr // 2, endpoint=False)
    quiet = 0.0005 * np.sin(2 * np.pi * 220 * t).astype(np.float64)
    absolute = band_magnitudes(quiet, relative=False)
    relative = band_magnitudes(quiet, relative=True)
    assert max(absolute) < 1.0  # a quiet render pins nowhere near 1.0 absolute
    assert max(relative) == 1.0  # relative scaling restores visible shape


def test_derive_tags_bass_and_never_empty():
    sr = 44100
    t = np.linspace(0, 1.0, sr, endpoint=False)
    sub = np.sin(2 * np.pi * 45 * t)
    assert "bass" in derive_tags(extract(sub))
    bright = np.sin(2 * np.pi * 3000 * t)
    assert derive_tags(extract(bright)) != []


def test_preset_meta_end_to_end():
    meta = preset_meta(preset("lead_supersaw"))
    assert set(meta) == {"tags", "spectrum", "v"}
    assert len(meta["spectrum"]) == 24
    assert meta["tags"]
    assert meta["v"] == 2


def test_long_release_patch_is_not_silent():
    from dataclasses import replace

    from synthlab.studio.soundmeta import note_duration

    p = replace(Patch(), release=1.59)
    assert note_duration(p) > 1.59
    meta = preset_meta(p)
    assert max(meta["spectrum"]) > 0.0
