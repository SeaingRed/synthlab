"""The core library (studio docs/sounds-and-breeding.md, "The core
library"): sounds named for what they
are, a shelf a category, on the Library before everything else.

What is pinned: the shelf lists every core recipe once and resolves every
other name it lists (a genre recipe or a factory sound that already IS the
thing, never a copy); its factory cards carry fresh numbers; the page draws
it with each card Used from its own source; and a few sounds measure as
what they are named for. Level, DC, role, hold and the live keys are
`test_style_packs.py`'s table (the core recipes are in its NEW), the
hold-seam bar `test_hold_seams.py`'s. None of it says they sound like
their names — that takes ears.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import recipes
from synthlab.studio.factory import get_factory
from synthlab.studio.recipes import (
    CORE_SHELF,
    FACTORY_REF,
    RECIPES,
    core_cards,
    get_recipe,
    recipe_patch,
    stack_audio,
)
from synthlab.studio.server import create_app
from synthlab.synth import Synth, at_bpm

CORE = [e["name"] for e in RECIPES if "core" in e["tags"]]
CATEGORIES = ("bass", "lead", "keys", "mallets", "orchestral", "pluck", "pad", "fx")
# what the brief asked for by name -> what the shelf holds for it
ASKED = {
    "808 long slide": "808 Long",
    "moog-style lead bass": "Ladder Lead Bass",
    "juno synth-pop bass": "Synth-Pop Bass",
    "fm slap bass": "FM Slap Bass",
    "acid": "factory:Acid Alley",
    "wobble": "Wobble Bass",
    "pluck bass": "Slap Pluck Bass",
    "sub sine": "Sine Sub",
    "sync scream": "Sync Screech",
    "chip square": "Square Lead",
    "brass lead": "Brass Lead",
    "portamento whistle": "Portamento Whistle",
    "screech": "Screech Lead",
    "saw lead": "Bright Saw Lead",
    "soft sine lead": "Soft Sine Lead",
    "rhodes-style tine ep": "Tine EP",
    "wurli-style reed ep": "Reed EP",
    "dx bell ep": "FM Bell EP",
    "clav": "Funk Clav",
    "house organ": "House Organ",
    "drawbar organ": "Drawbar Organ",
    "music box": "Music Box",
    "celesta": "Celesta",
    "marimba": "Marimba",
    "vibraphone": "Vibraphone",
    "glockenspiel": "Glockenspiel",
    "kalimba": "Kalimba",
    "steel drum": "Steel Drum",
    "tubular bells": "Tubular Bells",
    "ob-style synth brass": "Synth Brass",
    "brass stab": "Brass Stab",
    "pizzicato": "Pizzicato",
    "orchestra hit": "Orchestra Hit",
    "synth strings": "Synth Strings",
    "choir": "Synth Choir",
    "harp": "Harp",
    "koto": "Koto",
    "sitar-ish": "Sitar Pluck",
    "bell pluck": "Bell Pluck",
    "nylon": "factory:Nylon Pluck",
    "warm analog pad": "Warm Analog Pad",
    "glass pad": "Glass Pad",
    "evolving scan": "Scanning Pad",
    "vowel pad": "Vowel Drift Pad",
    "dark drone": "Deep Drone",
    "glitch": "Glitch",
    "sci-fi blips": "Sci-fi Blips",
    "alarm": "Alarm",
    "laser": "factory:Laser Zap",
    "ambience bed": "Ambience Bed",
}


def make_client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _shelf() -> dict[str, tuple[str, ...]]:
    return dict(CORE_SHELF)


def test_the_shelf_lists_every_core_recipe_once_and_resolves_the_rest():
    shelf = _shelf()
    assert tuple(shelf) == CATEGORIES
    listed = [ref for refs in shelf.values() for ref in refs]
    assert len(listed) == len(set(listed)) == 83
    # every core recipe, exactly once; nothing else tagged core
    assert sorted(r for r in listed if r in CORE) == sorted(CORE)
    assert len(CORE) == 49
    for ref in listed:
        if ref.startswith(FACTORY_REF):
            assert get_factory(ref[len(FACTORY_REF) :]) is not None, ref
        else:
            e = get_recipe(ref)
            assert e is not None, ref
    # a core recipe is measured at the core's tempo; a category has a few
    for e in RECIPES:
        if e["name"] in CORE:
            assert e["bpm"] == recipes.CORE_BPM and e["how"], e["name"]
    assert all(len(refs) >= 8 for refs in shelf.values())
    # what was asked for is there, under a generic name (no brand as a name)
    for asked, ref in ASKED.items():
        assert ref in listed, asked
    for name in CORE:
        for brand in ("Moog", "Juno", "Rhodes", "Wurli", "DX", "OB-", "Clavinet"):
            assert brand not in name, name


def test_each_category_reads_dark_to_bright_and_says_where_it_came_from():
    cards = dict(core_cards())
    assert tuple(cards) == CATEGORIES
    for cat, shelf in cards.items():
        cents = [c["centroid"] for c in shelf]
        assert cents == sorted(cents) and cents[0] > 0, cat
        for c in shelf:
            assert c["kind"] in ("recipes", "factory") and "core" in c["tags"]
            assert len(c["spectrum"]) == 24
    kinds = {c["name"]: c["kind"] for shelf in cards.values() for c in shelf}
    factory = sorted(n for n, k in kinds.items() if k == "factory")
    assert (
        factory
        == sorted(n for n, _ in recipes.core_factory())
        == [
            "Acid Alley",
            "Laser Zap",
            "Nylon Pluck",
        ]
    )


@pytest.mark.parametrize("name, cat", recipes.core_factory())
def test_a_listed_factory_sounds_card_numbers_are_fresh(name, cat):
    from synthlab.analysis import extract
    from synthlab.studio.soundmeta import band_magnitudes

    stored = recipes._META[FACTORY_REF + name]
    audio = recipes.factory_audio(name, cat)
    spec = band_magnitudes(audio)
    assert max(abs(a - b) for a, b in zip(stored["spectrum"], spec, strict=True)) < 0.15
    cent = extract(audio).centroid
    assert abs(stored["centroid"] - cent) <= 0.1 * cent + 5


def test_the_core_shelf_leads_the_library(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/library").text
    for cat in CATEGORIES:
        assert f'id="shelf-core-{cat}"' in html
    # first: before the genre recipes and the generated families
    assert html.index('id="shelf-core-bass"') < html.index('id="shelf-recipes"')
    # the genre recipes' shelf holds no core recipe
    recipes_shelf = html.split('id="shelf-recipes"')[1].split("</section>")[0]
    assert 'data-name="Marimba"' not in recipes_shelf
    assert 'data-name="Hard Phonk Lead"' in recipes_shelf
    # a factory card is heard and Used as the factory sound it is
    acid = html.split('data-name="Acid Alley"')[1].split("</article>")[0]
    assert 'data-note="/factory/Acid%20Alley/audition.wav"' in acid
    assert 'hx-post="/fragment/factory/load"' in acid
    assert '"source": "factory"' in acid and '<i class="g">core</i>' in acid
    # a recipe card on the Core shelf is the recipe
    marimba = html.split('data-name="Marimba"')[1].split("</article>")[0]
    assert 'hx-post="/fragment/recipe/load"' in marimba
    assert '<i class="g">core</i>' in marimba
    assert 'data-tags="core keys mallets"' in html
    # the filter: mallets and orchestral are families, core a chip of its own
    family = html.split('aria-label="family">')[1].split("</span>")[0]
    assert 'data-tag="mallets"' in family and 'data-tag="orchestral"' in family
    assert 'data-tag="core"' in html
    assert "a core library of 83 named for what they are" in html


def test_use_from_the_core_shelf_lands_on_synths_whatever_the_source(tmp_path):
    client = make_client(tmp_path)
    s = client.app.state.studio
    for route, name in (
        ("/fragment/factory/load", "Laser Zap"),
        ("/fragment/recipe/load", "Tine EP"),
    ):
        r = client.post(
            route,
            data={"name": name},
            headers={"HX-Current-URL": "http://x/view/library"},
        )
        assert r.status_code == 200
        assert r.headers["HX-Push-Url"] == "/view/editor"
        assert s.patch.name == name
    # from Synths' own dock a factory sound still stays put
    r = client.post(
        "/fragment/factory/load",
        data={"name": "Acid Alley"},
        headers={"HX-Current-URL": "http://x/view/editor"},
    )
    assert 'data-view="editor"' in r.text and "HX-Push-Url" not in r.headers


# --- named for what they are --------------------------------------------------


def _note(name: str, pitch: str | None = None, dur: float = 2.0) -> np.ndarray:
    e = get_recipe(name)
    p = at_bpm(recipe_patch(e), e["bpm"])
    x = np.asarray(Synth(p).note(pitch or e["pitch"], dur, gate=dur * 0.9))
    return x.mean(axis=1) if x.ndim == 2 else x


def _peak_hz(x: np.ndarray, lo: float, hi: float) -> float:
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    band = (f >= lo) & (f < hi)
    return float(f[band][np.argmax(spec[band])])


def test_the_engine_is_the_right_tool_where_it_is():
    """fm4 builds the electric pianos, the bars and the bells; the organ
    and pulse tables the organ and the clav; drift the analog pad and the
    synth brass; a slide the whistle and the theremin."""
    shape = {n: recipe_patch(get_recipe(n)) for n in CORE}
    for n in ("Tine EP", "Reed EP", "FM Bell EP", "FM Slap Bass", "House Organ"):
        assert shape[n].osc1 == "fm4", n
    for n in ("Marimba", "Vibraphone", "Glockenspiel", "Kalimba", "Xylophone"):
        assert (shape[n].osc1, shape[n].fm4_algo) == ("fm4", "additive"), n
    assert (shape["Tubular Bells"].osc2, shape["Tubular Bells"].wt_table) == (
        "wavetable",
        "bell",
    )
    assert shape["Drawbar Organ"].wt_table == "organ"
    assert shape["Funk Clav"].wt_table == "pulse"
    assert shape["Synth Choir"].wt_table == "formant"
    assert shape["Warm Analog Pad"].drift > 0 and shape["Synth Brass"].drift > 0
    assert shape["Portamento Whistle"].glide >= 0.1
    assert shape["Theremin Lead"].glide >= 0.2


def test_the_bars_ring_at_their_partials():
    """The glockenspiel's second partial at 2.76 of its note (a free bar's),
    the marimba's at 4 (the partial it is tuned to) — read off the wave."""
    glock = _note("Glockenspiel", "C5", 1.0)[: int(0.3 * SR)]
    c5 = 523.25
    assert _peak_hz(glock, 2.4 * c5, 3.2 * c5) == pytest.approx(2.76 * c5, rel=0.01)
    marimba = _note("Marimba", "C4", 1.0)[: int(0.1 * SR)]
    c4 = 261.63
    assert _peak_hz(marimba, 3.5 * c4, 4.5 * c4) == pytest.approx(4 * c4, rel=0.02)


def test_the_alarm_is_two_tones_a_fourth_apart():
    x = _note("Alarm", "C4", 2.0)
    half = int(0.25 * SR)  # the LFO is 2 Hz: a tone a quarter second
    tones = [_peak_hz(x[k : k + half // 2], 150, 600) for k in range(0, 4 * half, half)]
    lo, hi = min(tones), max(tones)
    assert 12 * np.log2(hi / lo) == pytest.approx(5.0, abs=0.7)
    assert len({round(t) for t in tones}) >= 2


def test_the_vibraphone_turns_at_five_hertz():
    x = _note("Vibraphone", "C4", 2.5)[int(0.3 * SR) : int(2.0 * SR)]
    w = int(0.01 * SR)
    env = np.sqrt(np.convolve(x**2, np.ones(w) / w, mode="valid"))[::w]
    env = np.log(env + 1e-9)
    env = env - np.polyval(np.polyfit(np.arange(len(env)), env, 1), np.arange(len(env)))
    spec = np.abs(np.fft.rfft(env, 8 * len(env)))
    f = np.fft.rfftfreq(8 * len(env), 0.01)
    band = (f > 2) & (f < 10)
    assert float(f[band][np.argmax(spec[band])]) == pytest.approx(5.0, abs=0.3)


def test_the_plucks_die_and_the_pads_hold_in_their_whole_stack():
    """The table's hold is the dry patch's; here the whole recipe, FX and
    all, a second in: the plucked and struck ones well under their attack,
    the pads not."""
    for name, under in (("Synth Pluck", -25), ("Pizzicato", -20), ("Xylophone", -25)):
        a = stack_audio(get_recipe(name)).mean(axis=1)
        w = int(0.05 * SR)
        head = np.sqrt(np.mean(a[:w] ** 2))
        late = np.sqrt(np.mean(a[SR : SR + w] ** 2))
        assert 20 * np.log10(late / head) < under, name
