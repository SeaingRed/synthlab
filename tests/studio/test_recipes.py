"""Genre recipe sounds (`studio/recipes.py`): format-2 Sounds on the
Library's Recipes shelf, measured card numbers kept honest, and each one
doing the technique it is named for."""

import functools

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.analysis import extract
from synthlab.core import SR
from synthlab.effects import chain as fx_chain
from synthlab.effects import to_stereo
from synthlab.studio import recipes, schema, sounds
from synthlab.studio.fxrack import FX_UNITS, build_chain, clean_chain
from synthlab.studio.recipes import (
    RECIPES,
    get_recipe,
    measure_all,
    preset_json,
    recipe_chain,
    stack_audio,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement, preset_from_json
from synthlab.synth import Patch, Synth, at_bpm

NAMES = [e["name"] for e in RECIPES]


def make_client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def test_the_twelve_recipes_and_the_packs_seventeen():
    """The first twelve keep their places (melodic techno, then garage);
    the four style packs' sounds follow them (test_style_packs.py), then
    two packs (Just Ice, Nine Inch Wails), six each, and two more (Psych
    Haze, Sixties Studio), six each, and thirteen (the five artists' gaps,
    the drop FX), and twenty-two (hip-hop / trap, drum & bass, dubstep,
    synthwave, future bass) and twenty-three (lo-fi hip-hop, ambient /
    cinematic, chiptune, hardstyle, phonk), and the core library's
    forty-nine."""
    assert len(NAMES) == 160
    assert NAMES[:12] == [
        "Cinematic Gate Lead",
        "Rolling Sidechained Sub",
        "Two-Saw Top Bass",
        "Metal FM Shot",
        "Gated Choir Pad",
        "Long Cinema Pad",
        "Rising Air",
        "Sine Sub",
        "Phone-Mic Keys",
        "Warm Garage Keys",
        "Offbeat Stab",
        "Washy Texture Pad",
    ]
    assert NAMES[12:] == [
        "Chopped Stab Loop",
        "Slide Funk Bass",
        "Talkbox Carrier",
        "Vowel Lead",
        "Phaser String Pad",
        "Felt Keys",
        "Vox Pluck",
        "Ducked Slide Sub",
        "Chop Bed Pad",
        "Evolving Pluck",
        "Wide Unison Stab",
        "Rolling Mid Bass",
        "Rolling Sub",
        "Anthem Supersaw",
        "Stack Pad",
        "Gated Pad",
        "Dotted Pluck",
        "Crushed Saw Bass",
        "Gated Disco Stab",
        "Cathedral Choir Pad",
        "Blown Lead",
        "Slap Pluck Bass",
        "Siren Riser",
        "Grinding Bass",
        "Struck Metal",
        "Static Drone",
        "Ghost Keys",
        "Fuzz Lead",
        "Machine Pulse",
        "Chorus Fuzz Bass",
        "Phased Fuzz Lead",
        "Haze Pad",
        "Wobble Keys",
        "Phase Arp",
        "Tape Swell",
        "Rotary Organ",
        "Tape Strings",
        "Tape Flute",
        "Round Bass",
        "Double Track Lead",
        "Backwards Cymbal",
        "Reese Bass",
        "Dark Arp Pluck",
        "Cinematic Choir Lead",
        "Trance Pluck Lead",
        "Big Room Pad",
        "String Machine",
        "Envelope Funk Bass",
        "Felt Piano Keys",
        "Squashed Vocal Pad",
        "Impact",
        "Sub Drop",
        "Noise Downlifter",
        "Reverse Crash",
        "808 Long",
        "808 Distorted",
        "Dark Bell Keys",
        "Dark Choir Pad",
        "Airy Flute Lead",
        "Neuro Reese",
        "Rolling DnB Sub",
        "Atmos Pad",
        "Liquid Stab",
        "Wobble Bass",
        "Growl Bass",
        "Half-Time Sub",
        "Screech Lead",
        "Poly Chorus Pad",
        "Poly Brass Stab",
        "Eighties Arp Bass",
        "Bright Saw Lead",
        "Lush Strings",
        "Supersaw Chord Stab",
        "Vocal Chop Lead",
        "Bent Pluck",
        "Future Pad",
        "Dusty Keys",
        "Jazz Chord Pad",
        "Soft Sub",
        "Muted Pluck",
        "Scanning Pad",
        "Vowel Drift Pad",
        "Shimmer Bed",
        "Glass Bell Texture",
        "Deep Drone",
        "Cinematic Braam",
        "Square Lead",
        "Pulse Lead",
        "Thin Pulse Arp",
        "Triangle Bass",
        "Crush Lead",
        "Sync Screech",
        "Euphoric Supersaw",
        "Reverse Bass",
        "Euphoric Pad",
        "Phonk Cowbell",
        "Phonk 808",
        "Dark Vowel Pad",
        "Hard Phonk Lead",
        # the core library (test_core_library.py)
        "Ladder Lead Bass",
        "Synth-Pop Bass",
        "FM Slap Bass",
        "Rubber Bass",
        "Upright Bass",
        "Brass Lead",
        "Portamento Whistle",
        "Soft Sine Lead",
        "Theremin Lead",
        "PWM Lead",
        "Tine EP",
        "Reed EP",
        "FM Bell EP",
        "Funk Clav",
        "House Organ",
        "Drawbar Organ",
        "Harpsichord",
        "Music Box",
        "Celesta",
        "Marimba",
        "Vibraphone",
        "Glockenspiel",
        "Kalimba",
        "Steel Drum",
        "Tubular Bells",
        "Xylophone",
        "Synth Brass",
        "Brass Stab",
        "Pizzicato",
        "Orchestra Hit",
        "Synth Strings",
        "Synth Choir",
        "Soft Horns",
        "Harp",
        "Koto",
        "Sitar Pluck",
        "Bell Pluck",
        "Synth Pluck",
        "Steel String Pluck",
        "Warm Analog Pad",
        "Glass Pad",
        "PWM Pad",
        "Sweep Pad",
        "Soft Pad",
        "Glitch",
        "Sci-fi Blips",
        "Alarm",
        "Ambience Bed",
        "Wind",
    ]
    assert recipes.GENRES == (
        "techno",
        "garage",
        "house",
        "trance",
        "electro",
        "industrial",
        "psych",
        "sixties",
        "drops",
        "hiphop",
        "dnb",
        "dubstep",
        "synthwave",
        "future",
        "lofi",
        "ambient",
        "chiptune",
        "hardstyle",
        "phonk",
        "core",  # not a genre: the core library's tag, one a recipe all the same
    )
    for e in RECIPES:
        assert len(set(recipes.GENRES) & set(e["tags"])) == 1, e["name"]
        assert set(e["tags"]) & {"lead", "bass", "pad", "keys", "fx"}, e["name"]
        assert e["how"]


@pytest.mark.parametrize("name", NAMES)
def test_a_recipe_is_a_valid_format_2_preset(name):
    e = get_recipe(name)
    patch, chain = preset_from_json(preset_json(e))
    assert patch == Patch.from_dict(e["patch"]) and patch.name == name
    # conformed exactly as a saved v2 file is: nothing clamped or dropped
    assert chain["main_fx"] == e["main_fx"] == clean_chain(e["main_fx"])
    assert chain["curves"] == e["curves"]
    assert chain["layers"] == (e["layers"] or [])
    assert all(u["kind"] in FX_UNITS for u in e["main_fx"])
    for lay in e["layers"] or []:
        assert lay["role"] in ("sub", "pad", "pluck", "air")
        assert lay["follow"]["octave"] in (-2, -1, 0, 1)
        assert lay["cells"] == []
    # the Sound Use loads: layers None when the recipe brings none
    rc = recipe_chain(e)
    assert (rc["layers"] is None) == (not e["layers"])
    assert schema.conform(preset_json(e), schema.nodes().PRESET_V2)["patch"]


@functools.cache
def _fresh() -> dict:
    """`measure_all` once for the module: it renders every recipe (~9 s at
    160), and asking it per recipe made the drift check quadratic."""
    return measure_all()


@pytest.mark.parametrize("name", NAMES)
def test_a_recipe_renders_and_its_card_numbers_are_fresh(name):
    e = get_recipe(name)
    audio = stack_audio(e)
    assert np.all(np.isfinite(audio))
    assert float(np.abs(audio).max()) > 0.1, name
    stored = recipes._META[name]
    fresh = _fresh()[name]
    drift = max(
        abs(a - b) for a, b in zip(stored["spectrum"], fresh["spectrum"], strict=True)
    )
    assert drift < 0.15, f"{name}: re-run tools/refresh_sound_meta.py ({drift:.3f})"
    assert abs(stored["centroid"] - fresh["centroid"]) <= 0.1 * fresh["centroid"] + 5


def test_the_shelf_reads_dark_to_bright():
    cards = recipes.cards()
    assert [c["name"] for c in cards] != []
    assert {c["name"] for c in cards} == set(NAMES)
    assert [c["brightness"] for c in cards] == sorted(c["brightness"] for c in cards)
    centroids = [c["centroid"] for c in cards]
    assert centroids == sorted(centroids)
    assert cards[0]["brightness"] == 0.0 and cards[-1]["brightness"] == 1.0


@pytest.mark.parametrize("name", [n for n in NAMES if get_recipe(n)["layers"]])
def test_a_recipes_layers_sit_under_its_main(name):
    """A following layer supports the main (-12..-20 dB here), never
    outshouts it."""
    e = get_recipe(name)
    arr = Arrangement(bpm=e["bpm"])
    main = Patch.from_dict(e["patch"])
    dur = 3.0
    main_rms = _rms(
        recipes._lane_audio(main, e["pitch"], dur, e["main_fx"], arr, e["curves"])
    )
    for lay in e["layers"]:
        from synthlab.sequencer import midi, name_of

        pitch = name_of(midi(e["pitch"]) + 12 * lay["follow"]["octave"])
        own = lay["gain"] * _rms(
            recipes._lane_audio(
                Patch.from_dict(lay["patch"]), pitch, dur, lay["fx"], arr, None
            )
        )
        db = 20 * np.log10(own / main_rms)
        assert -22.0 < db < -10.0, f"{name} {lay['role']}: {db:+.1f} dB"


# ------------------------------------------------------ the techniques


def _env_db(x: np.ndarray, frame: float = 0.004) -> np.ndarray:
    m = x.mean(axis=1) if x.ndim == 2 else x
    w = int(frame * SR)
    k = len(m) // w
    return 20 * np.log10(np.sqrt(np.mean(m[: k * w].reshape(k, w) ** 2, axis=1)) + 1e-9)


def _mod_rate(db: np.ndarray, frame: float = 0.004) -> float:
    x = (db - db.mean()) * np.hanning(len(db))
    spec = np.abs(np.fft.rfft(x, n=8 * len(x)))
    f = np.fft.rfftfreq(8 * len(x), frame)
    band = (f > 0.5) & (f < 40)
    return float(f[band][np.argmax(spec[band])])


def test_gate_lead_is_gated_at_sixteenths_by_its_lfo():
    """A square LFO synced to 1/16 on osc1_level: at 125 BPM the level
    swings at 8.33 Hz, by more than 10 dB (measured ~12-15 dB)."""
    e = get_recipe("Cinematic Gate Lead")
    p = at_bpm(Patch.from_dict(e["patch"]), 125.0)
    assert p.mod_lfo_rate == pytest.approx(125.0 / 60.0 / 0.25)
    audio = Synth(p).note("C4", 3.0, gate=2.8)
    db = _env_db(audio[int(0.5 * SR) : int(2.5 * SR)])
    assert _mod_rate(db) == pytest.approx(8.33, abs=0.3)
    assert np.percentile(db, 90) - np.percentile(db, 10) > 10.0


def _duck_db(units: list, bpm: float) -> float:
    """How far a chain pulls a steady tone down right after a beat, against
    the level just before the next (dB)."""
    tone = to_stereo(np.sin(2 * np.pi * 110 * np.arange(int(3 * SR)) / SR) * 0.5)
    out = np.asarray(fx_chain(tone, build_chain(units, Arrangement(bpm=bpm))))
    beat = int(60.0 / bpm * SR)
    dips, highs = [], []
    for k in (2, 3, 4):
        seg = np.abs(out[k * beat : (k + 1) * beat, 0])
        w = int(0.01 * SR)
        env = np.array([seg[i : i + w].max() for i in range(0, len(seg) - w, w)])
        dips.append(env[: len(env) // 4].min())
        highs.append(env[-3:].mean())
    return float(20 * np.log10(np.mean(dips) / np.mean(highs)))


@pytest.mark.parametrize(
    "name, deeper_than",
    [
        ("Rolling Sidechained Sub", -8.0),  # measured -9.4 dB
        ("Two-Saw Top Bass", -4.0),  # -5.5 dB
        ("Warm Garage Keys", -6.0),  # -7.3 dB: "heavy" ducking
    ],
)
def test_pumped_recipes_duck_on_the_beat(name, deeper_than):
    e = get_recipe(name)
    pumps = [u for u in e["main_fx"] if u["kind"] == "pump"]
    assert pumps and pumps[0]["params"]["beats"] == 1.0
    assert _duck_db(pumps, e["bpm"]) < deeper_than


def test_rolling_sub_skips_the_kicks_sixteenth():
    """The gate plays three 16ths of every beat and leaves the kick's."""
    e = get_recipe("Rolling Sidechained Sub")
    gate = [u for u in e["main_fx"] if u["kind"] == "gate"][0]
    assert gate["pattern"] == ".xxx" * 4
    audio = stack_audio(e)
    six = int(60.0 / e["bpm"] / 4 * SR)
    first, second = audio[: six // 2], audio[six + six // 4 : 2 * six]
    assert _rms(first) < 0.05 * _rms(second)


def test_top_bass_is_two_saws_an_octave_and_two_below():
    p = Patch.from_dict(get_recipe("Two-Saw Top Bass")["patch"])
    assert (p.osc1, p.osc1_octave, p.osc2, p.osc2_octave) == ("saw", -1, "saw", -2)


def test_garage_sub_is_a_mono_sine():
    e = get_recipe("Sine Sub")
    assert Patch.from_dict(e["patch"]).osc1 == "sine"
    audio = stack_audio(e)
    np.testing.assert_allclose(audio[:, 0], audio[:, 1])
    f = extract(audio)
    assert f.sub_20_60 + f.sub_60_120 > 0.9


def test_phone_mic_keys_are_band_limited_and_mono():
    audio = stack_audio(get_recipe("Phone-Mic Keys"))
    np.testing.assert_allclose(audio[:, 0], audio[:, 1])
    p = np.abs(np.fft.rfft(audio[:, 0])) ** 2
    f = np.fft.rfftfreq(len(audio), 1 / SR)
    assert p[f > 5000].sum() / p.sum() < 0.01


def test_rising_air_rises():
    e = get_recipe("Rising Air")
    assert e["curves"]["cutoff"][0][1] < e["curves"]["cutoff"][-1][1]
    audio = stack_audio(e)
    n = len(audio)
    early = extract(audio[int(0.1 * n) : int(0.3 * n)]).centroid
    late = extract(audio[int(0.5 * n) : int(0.7 * n)]).centroid
    assert late > 1.5 * early


# ------------------------------------------------------------ the shelf


def test_recipes_shelf_on_the_library(tmp_path):
    html = make_client(tmp_path).get("/view/library").text
    assert 'id="shelf-recipes"' in html
    for name in NAMES:
        assert f'data-name="{name}"' in html
    # the genre recipes on their shelf, the core ones (and the genre recipes
    # it lists besides) on the Core shelf (test_core_library.py)
    genre = [e for e in RECIPES if "core" not in e["tags"]]
    on_core = [
        c for _, cs in recipes.core_cards() for c in cs if c["kind"] == "recipes"
    ]
    assert html.count('hx-post="/fragment/recipe/load"') == len(genre) + len(on_core)
    assert '"source": "recipes"' in html
    for tag in recipes.GENRES:
        assert f'data-tag="{tag}"' in html


def test_recipe_routes(tmp_path):
    client = make_client(tmp_path)
    w = client.get("/recipe/Sine Sub/audition.wav")
    assert w.status_code == 200 and w.content[:4] == b"RIFF"
    assert client.get("/recipe/nope/audition.wav").status_code == 404
    bad = client.post("/fragment/recipe/load", data={"name": "nope"})
    assert bad.status_code == 422 and "Recipe not found." in bad.text


def test_a_recipe_without_layers_keeps_the_sessions_lanes(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/layer/add", data={"source": "factory", "name": "Sub Bomb"})
    lanes_before = [dict(lay) for lay in s.layers if lay.get("patch")]
    assert len(lanes_before) == 2
    client.post("/fragment/recipe/load", data={"name": "Phone-Mic Keys"})
    assert s.patch.name == "Phone-Mic Keys"
    assert [u["kind"] for u in s.layers[s.active_layer]["fx"]] == [
        u["kind"] for u in get_recipe("Phone-Mic Keys")["main_fx"]
    ]
    assert sum(1 for lay in s.layers if lay.get("patch")) == 2


def test_a_recipe_with_layers_brings_them(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/recipe/load", data={"name": "Long Cinema Pad"})
    others = [
        lay for i, lay in enumerate(s.layers) if i != s.active_layer and lay["patch"]
    ]
    assert [lay["role"] for lay in others] == ["air"]
    assert others[0]["follow"] == {"octave": 1}


def _stack(s) -> list:
    return [
        (lay["patch"].name if lay.get("patch") else None, lay.get("gain"))
        for lay in s.layers
    ]


def test_the_next_sound_does_not_inherit_a_recipes_lanes_or_curves(tmp_path):
    """Clicking through sounds, the previous sound's settings must not blend
    over: a recipe's pad layer must not play under every single-patch sound
    picked after it. What a whole Sound brought besides
    its patch and FX goes with it when the next sound loads, unless it was
    changed; "before last load" brings it back."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/recipe/load", data={"name": "Cinematic Gate Lead"})
    # the lead is the edited slot (`s.patch`); its pad is another lane
    assert sum(1 for lay in s.layers if lay.get("patch")) == 1
    s.curves = {"cutoff": [[0.0, 0.2], [1.0, 0.8]]}  # as if the recipe drew one
    s.sound_brought["curves"] = dict(s.curves)
    client.post("/fragment/factory/load", data={"name": "Interstellar Whoop"})
    fresh = make_client(tmp_path / "fresh")
    fresh.post("/fragment/factory/load", data={"name": "Interstellar Whoop"})
    assert _stack(s) == _stack(fresh.app.state.studio)
    assert s.curves == {}
    # "before last load": the recipe's whole stack again
    client.post("/fragment/restore_prev")
    assert s.patch.name == "Cinematic Gate Lead"
    assert sum(1 for lay in s.layers if lay.get("patch")) == 1


def test_a_recipes_lane_you_changed_stays_with_the_next_sound(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/recipe/load", data={"name": "Cinematic Gate Lead"})
    pad = next(
        i for i, lay in enumerate(s.layers) if i != s.active_layer and lay.get("patch")
    )
    s.layers[pad]["gain"] = 0.3  # yours now
    client.post("/fragment/factory/load", data={"name": "Interstellar Whoop"})
    assert s.layers[pad].get("patch") and s.layers[pad]["gain"] == 0.3


def test_a_recipe_can_be_added_as_a_layer(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    r = client.post(
        "/fragment/layer/add", data={"source": "recipes", "name": "Metal FM Shot"}
    )
    assert r.status_code == 200
    assert s.patch.name == "Metal FM Shot"  # the new layer is the edited one
    assert [u["kind"] for u in s.layers[s.active_layer]["fx"]] == ["delay", "reverb"]
    assert sounds.find(client.app.state.svc, "recipes", "Metal FM Shot") is not None
