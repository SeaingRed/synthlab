"""Genre drum kits (`drumfactory.DRUM_KITS`, `POST /fragment/drums/kit`) and
the two genre beat styles (`beatgen` "melodic-techno" / "garage-rim")."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import drums
from synthlab.core import SR
from synthlab.studio import beatgen
from synthlab.studio.drumfactory import DRUM_FACTORY, DRUM_KITS
from synthlab.studio.drumlab import specs_of
from synthlab.studio.server import create_app
from synthlab.studio.state import DRUM_ROWS


def make_client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _hits(steps, voice):
    return sorted(s for v, s in steps if v == voice)


# ------------------------------------------------------------------ styles

GENRE_STYLES = ("melodic-techno", "garage-rim")


@pytest.mark.parametrize("style", GENRE_STYLES)
def test_genre_styles_are_deterministic_per_seed(style):
    a = beatgen.generate(style, seed=11, bars=2)
    assert a == beatgen.generate(style, seed=11, bars=2)
    assert a != beatgen.generate(style, seed=12, bars=2)
    # bar 1 of a longer pattern is exactly the 1-bar pattern
    one = beatgen.generate(style, seed=11)
    assert [c for c in a if c[1] < 16] == one


def test_genre_style_outputs_are_pinned():
    """The seeded grooves themselves, so a table edit is a deliberate act."""
    assert beatgen.generate("melodic-techno", seed=125) == [
        ["clap", 4],
        ["clap", 12],
        ["clap", 14],
        ["hat", 1],
        ["hat", 3],
        ["hat", 5],
        ["hat", 7],
        ["hat", 8],
        ["hat", 9],
        ["hat", 10],
        ["hat", 11],
        ["hat", 13],
        ["hat", 15],
        ["kick", 0],
        ["kick", 4],
        ["kick", 8],
        ["kick", 12],
        ["ohat", 2],
        ["ohat", 6],
        ["ohat", 10],
        ["ohat", 14],
    ]
    garage = beatgen.generate("garage-rim", seed=132)
    assert _hits(garage, "rim") == [2, 6, 10, 14]
    assert _hits(garage, "clap") == [4, 12]
    assert {0, 10} <= set(_hits(garage, "kick"))
    assert 8 not in _hits(garage, "kick")  # 2-step: beat 3 is skipped


def test_melodic_techno_rolls_and_opens_on_the_offbeats():
    for seed in range(8):
        steps = beatgen.generate("melodic-techno", seed=seed)
        assert _hits(steps, "kick") == [0, 4, 8, 12]
        assert _hits(steps, "ohat") == [2, 6, 10, 14]
        assert set(range(1, 16, 2)) <= set(_hits(steps, "hat"))


@pytest.mark.parametrize("style", GENRE_STYLES)
def test_genre_rows_reroll_on_their_own(style):
    row = beatgen.generate_row("rim", style, seed=3, bars=2)
    assert row and all(v == "rim" for v, _ in row)


def test_randomise_sets_a_genre_style_feel_and_only_then(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/drums/randomise", data={"style": "garage-rim", "seed": "4"})
    assert s.arrangement.drum_swing == pytest.approx(0.16)
    # the style's cells, and its FEEL on top (`beatgen.dress`, the beat
    # workflow): the ghost kick the kit used to hold down with a whole
    # step's velocity is a hit of its own now, and the kick and the clap
    # never swing whatever is drawn on their rows later
    cells = beatgen.generate("garage-rim", 4)
    assert s.arrangement.drums == sorted([*cells, ["kick", 7]])
    assert s.arrangement.drum_hits["kick.7"] == {"v": 0.35}
    assert s.arrangement.drum_row_swing == {"kick": 0.0, "clap": 0.0}
    # an older style keeps whatever swing the clock had — and has no feel:
    # its beat carries no per-hit data and no row swing
    client.post("/fragment/drums/randomise", data={"style": "techno", "seed": "4"})
    assert s.arrangement.drum_swing == pytest.approx(0.16)
    assert s.arrangement.drums == beatgen.generate("techno", 4)
    assert s.arrangement.drum_hits == {} and s.arrangement.drum_row_swing == {}
    client.post(
        "/fragment/drums/randomise", data={"style": "melodic-techno", "seed": "4"}
    )
    assert s.arrangement.drum_swing == 0.0


# -------------------------------------------------------------------- kits


def test_kits_are_valid_and_in_range():
    assert list(DRUM_KITS) == [
        "Melodic Techno",
        "Garage Rim",
        "Filter House",  # the style packs' three (test_style_packs.py)
        "Two-Step",
        "Trance",
        "Just Ice",  # French electro, industrial
        "Nine Inch Wails",
        "Psych Haze",  # neo-psych, 60s studio
        "Sixties Studio",
        "Hip-Hop 808",  # the genre packs
        "Drum and Bass",
        "Dubstep",
        "Synthwave",
        "Future Bass",
        "Lo-Fi",  # more genre packs
        "Cinematic",
        "Chiptune",
        "Hardstyle",
        "Phonk",
    ]
    # a kit is found by its style in the bank's select: one kit a style
    assert len({kit["style"] for kit in DRUM_KITS.values()}) == len(DRUM_KITS)
    for name, kit in DRUM_KITS.items():
        assert kit["style"] in beatgen.STYLES
        p = kit["pattern"]
        assert p["name"] == name
        assert all(v in DRUM_ROWS and 0 <= s < 16 for v, s in p["steps"])
        assert 0.0 <= p["swing"] <= 0.6
        for voice, params in kit["params"].items():
            specs = {sp["name"]: sp for sp in specs_of(voice)}
            assert set(params) <= set(specs), (name, voice)
            for k, v in params.items():
                assert specs[k]["min"] <= float(v) <= specs[k]["max"], (name, k)
        for step, vel in kit["vel"].items():
            assert 0 <= int(step) < 16 and 0.0 <= vel <= 1.0
    # the bank's factory slots are untouched by the kits
    assert len(DRUM_FACTORY) == 8


def test_garage_kit_is_two_step_swung_with_a_ghost_kick():
    kit = DRUM_KITS["Garage Rim"]
    steps = kit["pattern"]["steps"]
    assert _hits(steps, "rim") == [2, 6, 10, 14]  # every offbeat eighth
    assert _hits(steps, "kick") == [0, 7, 10]
    assert 0.55 <= 0.5 + kit["pattern"]["swing"] / 2 <= 0.6  # ~58 % shuffle
    # the ghost: the kick on 7 is quiet, and nothing else shares its step
    assert kit["vel"] == {"7": 0.35}
    assert [v for v, s in steps if s == 7] == ["kick"]


def _peak_and_centroid(x: np.ndarray) -> tuple[float, float]:
    mono = x.mean(axis=1) if x.ndim == 2 else x
    p = np.abs(np.fft.rfft(mono, n=1 << 15)) ** 2
    f = np.fft.rfftfreq(1 << 15, 1 / SR)
    smooth = np.convolve(p, np.ones(64) / 64, mode="same")
    return float(f[np.argmax(smooth)]), float((f * p).sum() / p.sum())


def test_garage_hats_are_band_passed_near_4800_hz():
    """Measured: the kit's hat peaks at ~4.0 kHz (centroid ~6.5 kHz) where
    the stock hat peaks at ~7.6 kHz (centroid ~10.1 kHz)."""
    peak, centroid = _peak_and_centroid(
        drums.hit("hat", **DRUM_KITS["Garage Rim"]["params"]["hat"])
    )
    assert 3500 < peak < 5500
    stock_peak, stock_centroid = _peak_and_centroid(drums.hit("hat"))
    assert peak < 0.7 * stock_peak and centroid < 0.75 * stock_centroid


@pytest.mark.parametrize("name", sorted(DRUM_KITS))
def test_loading_a_kit_sets_pattern_feel_voices_and_style(tmp_path, name):
    client = make_client(tmp_path)
    s = client.app.state.studio
    kit = DRUM_KITS[name]
    r = client.post(
        "/fragment/drums/kit",
        data={"kit": name},
        headers={"HX-Current-URL": "http://x/view/drums"},
    )
    assert r.status_code == 200
    assert 'data-view="drums"' in r.text
    assert "preview-ready" in r.headers["HX-Trigger"]
    arr = s.arrangement
    assert sorted(map(list, arr.drums)) == kit["pattern"]["steps"]
    assert (arr.drum_bars, arr.drum_steps) == (1, 16)
    assert arr.drum_swing == pytest.approx(kit["pattern"]["swing"])
    assert arr.drum_vel == kit["vel"]
    for voice, params in kit["params"].items():
        assert arr.drum_params[voice] == params
    assert (s.beat_style, s.beat_seed) == (kit["style"], kit["seed"])
    # loaded as a whole: nothing reads "changed" afterwards
    assert not s.is_dirty("pattern") and not s.is_dirty("kit")
    # and it renders
    wav = client.get(r.headers["HX-Trigger"].split('"url": "')[1].split('"')[0]).content
    frames = np.frombuffer(wav[44:], dtype="<i2")
    assert np.abs(frames).max() > 1000


def test_unknown_kit_is_a_toast(tmp_path):
    r = make_client(tmp_path).post("/fragment/drums/kit", data={"kit": "Polka"})
    assert r.status_code == 422 and "No such kit." in r.text


def test_the_bank_offers_the_kits(tmp_path):
    html = make_client(tmp_path).get("/view/drums").text
    assert 'hx-post="/fragment/drums/kit"' in html
    for name in DRUM_KITS:
        assert f'<option value="{name}"' in html
    for style in GENRE_STYLES:  # its value the id, its label the display name
        assert f'<option value="{style}" ' in html
