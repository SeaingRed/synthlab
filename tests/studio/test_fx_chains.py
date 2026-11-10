"""FX chain presets (`fxrack.CHAIN_PRESETS`, "+ add chain…" in every rack,
`POST /fragment/fx/chain`): each is a clean chain of existing units, lands
on a scope exactly as written, appends all-or-nothing, and does to a signal
what its name says."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.analysis import extract
from synthlab.core import SR
from synthlab.effects import chain as fx_chain
from synthlab.studio.fxrack import (
    CHAIN_PRESETS,
    FX_UNITS,
    MAX_UNITS,
    build_chain,
    chain_preset,
    clean_chain,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, Synth


def make_client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _run(units, x, bpm=124.0):
    return np.asarray(fx_chain(x, build_chain(units, Arrangement(bpm=bpm))))


def _band_share(x, lo, hi):
    mono = x.mean(axis=1) if x.ndim == 2 else x
    p = np.abs(np.fft.rfft(mono)) ** 2
    f = np.fft.rfftfreq(len(mono), 1 / SR)
    return float(p[(f >= lo) & (f < hi)].sum() / p.sum())


def test_the_chains_exist_and_are_clean():
    labels = [p["label"] for p in CHAIN_PRESETS.values()]
    assert labels == [
        "Phone Mic",
        "Mono Maker",
        "Washy",
        "Sidechain Pump 1/4",
        "Tape Lo-fi",
        # the style packs' eight (test_style_packs.py)
        "Filter Disco",
        "Hard Duck",
        "Crush Bus",
        "Dotted Ping-Pong",
        "Trance Gate",
        "Tape Wear",
        "Wide Hall",
        "Tape Stop",
        # Just Ice and Nine Inch Wails
        "Blown Out",
        "Machine Rust",
        # Psych Haze and Sixties Studio
        "Phase Wash",
        "Tape Haze",
        "Double Track",
        "Rotary Room",
        "Slap Plate",
        # the filter, tape and voice units in the packs (test_style_packs.py)
        "Filter Sweep",
        "Psych Tape",
        "Sixties Spring",
        "Barber Pole",
        "Robot Voice",
        # the dynamics units (test_style_packs.py)
        "OTT Squash",
        "Big Room Hall",
        "Ducked Verb",
        "Drum Punch",
        "Master Glue",
        # the genre packs (test_style_packs.py)
        "808 Saturate",
        "Boom Bap Crunch",
        "Neuro Grind",
        "Growl Squash",
        "Gated Verb",
        "Eighties Space",
        "Future Pump",
        # more genre packs (test_style_packs.py)
        "Dusty Tape",
        "Huge Hall",
        "Shimmer Wash",
        "8-Bit Crush",
        "Hard Kick Drive",
        "Phonk Crush",
    ]
    for name, preset in CHAIN_PRESETS.items():
        units = preset["units"]
        assert 1 <= len(units) <= MAX_UNITS, name
        assert all(u["kind"] in FX_UNITS for u in units), name
        # stored already clean: what you see in the table is what lands
        assert clean_chain(units) == units, name
        assert chain_preset(name) == units
        assert chain_preset(name) is not units  # a copy, never the table
    assert chain_preset("nope") is None


def test_every_chain_says_what_it_is_for_where_it_came_from_and_what_it_does():
    """The chain browser lists each chain under a purpose (`group`) or its
    genre pack (`pack`), with a one-line `blurb`. A chain that names none
    the browser knows still shows, under "Other" — this is what makes the
    one who adds it fill them in."""
    from synthlab.studio.fxrack import CHAIN_GROUPS, CHAIN_PACKS, chain_browser

    for name, preset in CHAIN_PRESETS.items():
        assert preset.get("group") in CHAIN_GROUPS, name
        assert preset.get("pack") in CHAIN_PACKS, name
        blurb = preset.get("blurb", "")
        assert 20 <= len(blurb) <= 90 and blurb.endswith("."), (name, blurb)
        assert "\n" not in blurb, name
    # every purpose has a chain under it; the browser lists each chain once,
    # in table order within its group, and Other only for what is unfiled
    browser = chain_browser()
    assert browser["count"] == len(CHAIN_PRESETS)
    rows = [r for group in browser["by_group"].values() for r in group]
    assert sorted(r["key"] for r in rows) == sorted(CHAIN_PRESETS)
    assert all(browser["by_group"][g] for g in CHAIN_GROUPS)
    assert not browser["by_group"]["other"]
    for group in browser["by_group"].values():
        assert [r["i"] for r in group] == sorted(r["i"] for r in group)
    path = next(r["path"] for r in rows if r["key"] == "phone_mic")
    assert path == "Reverb → Highpass → Lowpass → Compress → Drive → Width"


def test_a_chain_with_no_group_falls_under_other(monkeypatch):
    from synthlab.studio import fxrack

    monkeypatch.setitem(
        fxrack.CHAIN_PRESETS, "later", {"label": "Later", "units": [], "pack": "nope"}
    )
    browser = fxrack.chain_browser()
    (row,) = browser["by_group"]["other"]
    assert (row["key"], row["pack"], row["blurb"]) == ("later", "other", "")
    assert list(browser["groups"])[-1] == "other" == list(browser["packs"])[-1]


def test_the_browser_lists_every_chain_by_purpose_and_holds_both_headings(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/editor").text
    pop = html[html.index('id="fx-chains-layer"') :].split('id="fx-chains-master"')[0]
    assert pop.count('class="fxc-row"') == len(CHAIN_PRESETS)
    assert 'data-by="pack" data-key="hiphop"' in pop  # (filled by the island)
    for preset in CHAIN_PRESETS.values():
        assert preset["blurb"] in pop.replace("&#39;", "'")
    row = pop[pop.index('data-group="vocal"') :].split("</button>")[0]
    assert 'hx-post="/fragment/fx/chain"' in row
    assert '"scope": "layer", "chain": "phone_mic"' in row
    # the opener is the rack's one command for it (the rows are a list)
    assert 'popovertarget="fx-chains-layer"' in html
    assert 'data-cmd="Add effect chain to Layer 1 FX"' in html


@pytest.mark.parametrize("scope", ["layer", "master", "drums"])
@pytest.mark.parametrize("name", sorted(CHAIN_PRESETS))
def test_a_chain_lands_exactly_on_an_empty_scope(tmp_path, scope, name):
    client = make_client(tmp_path)
    r = client.post("/fragment/fx/chain", data={"scope": scope, "chain": name})
    assert r.status_code == 200
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    s = client.app.state.studio
    stored = {
        "layer": s.layers[s.active_layer]["fx"],
        "master": s.fx_master,
        "drums": s.fx_drums,
    }[scope]
    assert stored == CHAIN_PRESETS[name]["units"]


def test_a_chain_appends_after_what_is_there(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/fx/add", data={"scope": "master", "kind": "chorus"})
    client.post("/fragment/fx/chain", data={"scope": "master", "chain": "mono_maker"})
    kinds = [u["kind"] for u in client.app.state.studio.fx_master]
    assert kinds == ["chorus", "width"]


def test_a_chain_that_does_not_fit_changes_nothing(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "chorus"})
    s = client.app.state.studio
    before = [dict(u) for u in s.layers[s.active_layer]["fx"]]
    r = client.post("/fragment/fx/chain", data={"scope": "layer", "chain": "phone_mic"})
    assert r.status_code == 422 and "Phone Mic needs 6 free units" in r.text
    assert s.layers[s.active_layer]["fx"] == before


def test_bad_chain_or_scope_is_refused(tmp_path):
    client = make_client(tmp_path)
    for data in (
        {"scope": "layer", "chain": "nope"},
        {"scope": "nowhere", "chain": "washy"},
    ):
        assert client.post("/fragment/fx/chain", data=data).status_code == 422


def test_every_rack_offers_the_chains(tmp_path):
    client = make_client(tmp_path)
    for view in ("editor", "drums"):
        html = client.get(f"/view/{view}").text
        assert 'hx-post="/fragment/fx/chain"' in html
        assert "+ add chain…" in html
        for preset in CHAIN_PRESETS.values():
            assert preset["label"] in html


# ------------------------------------------------------- what each one does


def _chord() -> np.ndarray:
    """A bright, wide test signal: a supersaw with unison spread."""
    p = Patch(osc1="supersaw", unison=4, unison_spread=1.0, cutoff=12000.0)
    return np.asarray(Synth(p).note("C3", 1.5))


def test_phone_mic_band_limits_and_squashes():
    dry = _chord()
    wet = _run(chain_preset("phone_mic"), dry)
    # the handset's band (measured: under 200 Hz 59 % -> 7 %, over 5 kHz
    # 1.4 % -> 0.3 %, 200 Hz-3.5 kHz 39 % -> 92 %; 2-pole skirts, so the
    # C3 second harmonic at 262 Hz mostly survives the 300 Hz high-pass)
    assert _band_share(wet, 0, 200) < 0.15 * _band_share(dry, 0, 200)
    assert _band_share(wet, 5000, 20000) < 0.3 * _band_share(dry, 5000, 20000)
    assert _band_share(wet, 200, 3500) > 0.85
    # and one channel
    assert np.allclose(wet[:, 0], wet[:, 1])


def test_mono_maker_folds_stereo_to_mono():
    dry = _chord()
    assert extract(dry).stereo_width > 0.3
    wet = _run(chain_preset("mono_maker"), dry)
    np.testing.assert_allclose(wet[:, 0], wet[:, 1])
    np.testing.assert_allclose(wet[:, 0], dry.mean(axis=1))


def test_washy_is_long_and_dark():
    dry = np.zeros((int(4 * SR), 2))
    dry[: int(0.3 * SR)] = _chord()[: int(0.3 * SR)]
    wet = _run(chain_preset("washy"), dry)
    tail = wet[int(1.5 * SR) :]
    assert float(np.sqrt(np.mean(tail**2))) > 1e-3  # still ringing at 1.5 s
    assert _band_share(wet, 6000, 20000) < _band_share(dry, 6000, 20000)


def test_pump_quarter_ducks_on_every_beat():
    bpm = 120.0
    x = np.ones((int(2 * SR), 2)) * 0.5
    wet = _run(chain_preset("pump_quarter"), x, bpm)
    beat = int(60.0 / bpm * SR)
    for k in range(1, 3):
        start = k * beat
        dip = float(np.min(np.abs(wet[start : start + beat // 4, 0])))
        late = float(np.abs(wet[start + int(0.9 * beat), 0]))
        assert dip < 0.35 * late  # a deep duck right after the beat...
        assert late > 0.4  # ...recovered before the next


def test_tape_lofi_adds_noise_and_grit():
    dry = np.asarray(Synth(Patch(osc1="sine", cutoff=12000.0)).note("A3", 1.5))
    wet = _run(chain_preset("tape_lofi"), dry)
    # a pure sine's spectrum away from its harmonics fills in: hiss,
    # crackle, crushed bits and drive
    assert _band_share(wet, 3000, 20000) > 20 * _band_share(dry, 3000, 20000)
