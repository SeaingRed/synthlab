"""Rendering one acoustic hit from a synthetic kit of tiny FLACs."""

import numpy as np
import pytest
import soundfile as sf

from synthlab.core import SR
from synthlab.kits import play
from synthlab.kits.fetch import KitError
from synthlab.kits.sfz import Articulation, KitMap, Layer


def _write(root, name, value, n=400, sr=SR, ch=1):
    shape = (n,) if ch == 1 else (n, ch)
    sf.write(
        str(root / name), np.full(shape, value, dtype="float64"), sr, format="FLAC"
    )
    return name


def _kit(arts, mics=("kick_in", "overheads")):
    return KitMap({(a.row, a.name): a for a in arts}, tuple(mics))


def _art(layers, trim=0.0, mic_trim=None):
    return Articulation("kick", "hit", tuple(layers), trim, "", mic_trim or {})


def _level(audio):
    return float(np.abs(audio).max()) if audio.size else 0.0


def test_velocity_crossfades_between_layers_continuously(tmp_path):
    a = _write(tmp_path, "a.flac", 0.25)
    b = _write(tmp_path, "b.flac", 0.5)
    kit = _kit(
        [_art([Layer(0, 63, ({"kick_in": a},)), Layer(64, 127, ({"kick_in": b},))])]
    )
    levels = [
        _level(play.render_hit(kit, tmp_path, "kick", "hit", v / 100, 0, None))
        for v in range(1, 101)
    ]
    steps = np.abs(np.diff(levels))
    assert steps.max() <= 0.05 * max(levels)
    # louder at the top than the bottom, and the layers are both heard
    assert levels[-1] > levels[50] > levels[5]
    out = play.render_hit(kit, tmp_path, "kick", "hit", 0.5, 0, None)
    assert out.shape[1] == 2 and out.dtype == np.float64


def test_below_and_above_the_centres_one_layer_alone(tmp_path):
    a = _write(tmp_path, "a.flac", 0.25)
    b = _write(tmp_path, "b.flac", 0.5)
    kit = _kit(
        [_art([Layer(0, 63, ({"kick_in": a},)), Layer(64, 127, ({"kick_in": b},))])]
    )
    lo = play.render_hit(kit, tmp_path, "kick", "hit", 10 / 127, 0, None)
    hi = play.render_hit(kit, tmp_path, "kick", "hit", 1.0, 0, None)
    assert _level(lo) == pytest.approx(0.25 * (10 / 127) ** 0.7, rel=0.01)
    assert _level(hi) == pytest.approx(0.5, rel=0.01)


def test_round_robins_rotate(tmp_path):
    a = _write(tmp_path, "t0.flac", 0.2)
    b = _write(tmp_path, "t1.flac", 0.4)
    kit = _kit([_art([Layer(0, 127, ({"kick_in": a}, {"kick_in": b}))])])
    got = [
        round(_level(play.render_hit(kit, tmp_path, "kick", "hit", 1.0, rr, None)), 2)
        for rr in (0, 1, 2)
    ]
    assert got == [0.2, 0.4, 0.2]


def test_mics_sum_at_their_levels(tmp_path):
    a = _write(tmp_path, "in.flac", 0.2)
    b = _write(tmp_path, "oh.flac", 0.4)
    kit = _kit([_art([Layer(0, 127, ({"kick_in": a, "overheads": b},))])])

    def run(mics):
        return play.render_hit(kit, tmp_path, "kick", "hit", 1.0, 0, mics)[100, 0]

    full = run({"close_room": None})
    assert full == pytest.approx(0.6, rel=0.01)
    assert run({"overheads": -6}) - 0.2 == pytest.approx(0.2, rel=0.01)
    assert run({"overheads": None}) == pytest.approx(0.2, rel=0.01)


def test_di_mics_share_their_channel_and_keep_their_own_trim(tmp_path):
    a = _write(tmp_path, "ch.flac", 0.2)
    b = _write(tmp_path, "di.flac", 0.2)
    art = Articulation(
        "hat",
        "closed",
        (Layer(0, 127, ({"hats": a, "hats_di": b},)),),
        0.0,
        "hat",
        {"hats_di": -6.0},
    )
    kit = _kit([art], ("hats", "hats_di"))
    out = play.render_hit(kit, tmp_path, "hat", "closed", 1.0, 0, {"hats": -6})
    assert out[100, 0] == pytest.approx(0.2 * 0.5 + 0.2 * 0.5 * 0.5, rel=0.02)
    off = play.render_hit(kit, tmp_path, "hat", "closed", 1.0, 0, {"hats": None})
    assert _level(off) == 0.0


def test_articulation_trim_and_resample_and_stereo(tmp_path):
    a = _write(tmp_path, "s.flac", 0.5, n=441, sr=22050, ch=2)
    kit = _kit([_art([Layer(0, 127, ({"kick_in": a},))], trim=-6.0)])
    out = play.render_hit(kit, tmp_path, "kick", "hit", 1.0, 0, None)
    assert abs(len(out) - 882) <= 2
    assert out[100, 1] == pytest.approx(0.5 * 10 ** (-6 / 20), rel=0.01)


def test_the_cache_is_bounded(tmp_path):
    names = [_write(tmp_path, f"f{i}.flac", 0.1, n=1000) for i in range(20)]
    cache = play.SampleCache(max_bytes=20_000)
    for n in names:
        cache.get(tmp_path / n)
        assert cache.bytes <= 20_000
    assert 0 < cache.bytes


def test_a_missing_file_raises_kiterror(tmp_path):
    kit = _kit([_art([Layer(0, 127, ({"kick_in": "nope.flac"},))])])
    with pytest.raises(KitError):
        play.render_hit(kit, tmp_path, "kick", "hit", 1.0, 0, None)


def test_levels_overlay_the_studio_preset():
    lv = play.levels({"far_room": -3, "kick_in": None})
    assert lv["far_room"] == -3 and lv["kick_in"] is None
    assert lv["close_room"] == -6 and lv["mid_side"] is None
    assert set(lv) == set(play.MICS)
