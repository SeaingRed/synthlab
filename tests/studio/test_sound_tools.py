"""The sound-design additions: LFO shapes, envelope curve, tempo sync, macros,
and the three new FX units — plus the page-weight work that came with them.
"""

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.params import live as _active_genes
from synthlab.studio.fxrack import FX_UNITS, build_chain
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import LFO_SHAPES, Patch, Synth, at_bpm, synced_rate

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _rms(a):
    return float(np.sqrt(np.mean(a**2)))


def test_lfo_shape_reaches_the_engine():
    """core.lfo always took a shape; every call site hardcoded "sine", so the
    parameter existed and was unreachable. Each shape must render, and render
    differently."""
    out = {
        shape: Synth(
            Patch(filter_lfo_depth=3000, filter_lfo_rate=6, filter_lfo_shape=shape)
        ).note("C4", 1.0)
        for shape in LFO_SHAPES
    }
    for shape, audio in out.items():
        assert np.all(np.isfinite(audio)), shape
    assert not np.allclose(out["sine"], out["square"])
    assert not np.allclose(out["sine"], out["noise"])


def test_env_curve_reaches_the_engine():
    """core.adsr's `curve` was never passed, pinning every envelope at 2.0.
    Linear holds more energy than a snappy exponential decay."""
    quiet = _rms(Synth(Patch(env_curve=6.0, decay=0.4, sustain=0.15)).note("C4", 1.5))
    loud = _rms(Synth(Patch(env_curve=1.0, decay=0.4, sustain=0.15)).note("C4", 1.5))
    assert loud > quiet * 1.15, (loud, quiet)


def test_tempo_sync_resolves_against_bpm():
    assert synced_rate("off", 124) is None
    # 124bpm is 2.0667 beats/s, so a 1/4-note cycle is that many Hz
    assert synced_rate("1/4", 124) == 124 / 60
    assert synced_rate("1/8", 124) == 2 * (124 / 60)
    p = Patch(lfo_sync="1/8", filter_lfo_sync="1/16")
    q = at_bpm(p, 124)
    assert q.mod_lfo_rate == synced_rate("1/8", 124)
    assert q.filter_lfo_rate == synced_rate("1/16", 124)
    assert at_bpm(Patch(), 124) == Patch()  # unsynced patch untouched


def test_lfo_shape_and_sync_genes_follow_their_lfo():
    """(Registration itself is tests/test_params.py's completeness test.)"""
    # a dead LFO must not spend mutation budget on its shape or sync
    off = _active_genes(Patch(filter_lfo_depth=0.0, mod1_amt=0.0, mod2_amt=0.0))
    assert not {"filter_lfo_shape", "filter_lfo_sync", "lfo_shape"} & off
    # and a synced LFO must not mutate the Hz rate that sync overrides
    synced = _active_genes(Patch(filter_lfo_depth=3000.0, filter_lfo_sync="1/8"))
    assert "filter_lfo_rate" not in synced and "filter_lfo_shape" in synced


def test_new_fx_units_render():
    arr = Arrangement(bpm=124)
    x = np.random.default_rng(0).standard_normal((SR, 2)) * 0.2
    for kind in ("pump", "granular", "shimmer"):
        assert kind in FX_UNITS
        y = fx.chain(x, build_chain([{"kind": kind, "params": {}}], arr))
        assert y.shape == x.shape and np.all(np.isfinite(y)), kind
        assert np.max(np.abs(y)) > 0.0, kind
    # mono in, mono out — the contract every effect keeps
    m = np.random.default_rng(1).standard_normal(SR) * 0.2
    for f in (fx.granular(), fx.shimmer()):
        assert f(m).shape == m.shape
    # seeded: a loop must re-render identically
    assert np.array_equal(fx.granular()(x), fx.granular()(x))


def test_pump_ducks_without_any_drums():
    """Sidechain keys off kick hits, so it does nothing on Synths where the
    preview renders parts=("synth",). Pump ducks on the clock instead."""
    arr = Arrangement(bpm=120, drums=[])
    x = np.ones((SR, 2)) * 0.5
    dry = fx.chain(x, build_chain([{"kind": "sidechain", "params": {}}], arr))
    wet = fx.chain(x, build_chain([{"kind": "pump", "params": {}}], arr))
    assert np.allclose(dry, x), "sidechain has nothing to trigger on"
    assert _rms(wet) < _rms(x) * 0.95, "pump did not duck"


def test_macro_sweeps_its_targets(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        s = client.app.state.studio
        r = client.post(
            "/fragment/macro/0/assign",
            content="name=Sweep&param=cutoff&amt=1&param=resonance&amt=0.8",
            headers=FORM,
        )
        assert r.status_code == 200
        assert s.macros[0]["name"] == "Sweep"
        assert [t["param"] for t in s.macros[0]["targets"]] == ["cutoff", "resonance"]

        seen = []
        for v in ("0", "0.5", "1"):
            r = client.post("/fragment/macro/0", content=f"value={v}", headers=FORM)
            assert r.status_code == 200
            seen.append(s.patch.cutoff)
            # only the moved rows come back, never the whole ~2800-node page
            assert "hx-swap-oob" in r.text and len(r.text) < 20_000
            assert 'id="row-cutoff"' in r.text
        assert seen == sorted(seen) and seen[0] < seen[-1]
        assert s.patch.resonance > 0.0
    # macros are a control layer, deliberately not part of the genome
    assert not hasattr(Patch(), "macros")


def test_chord_library_is_not_shipped_with_every_page(tmp_path):
    """~500 buttons and most of the editor's HTML weight. Fetched on open."""
    with TestClient(create_app(tmp_path)) as client:
        page = client.get("/").text
        assert 'hx-get="/fragment/chordlib?key=' in page
        assert page.count('class="chord-pad lib') == 0
        lib = client.get("/fragment/chordlib").text
        assert lib.count('class="chord-pad lib') > 100


def test_pad_blocks_collapse_and_start_collapsed(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        page = client.get("/").text
    for block in ("pads-notes", "pads-chords", "pads-harmonics", "chord-lib"):
        assert f'id="{block}"' in page, block
    # <details> with no `open` attribute — collapsed until the player says so
    for block in ("pads-notes", "pads-chords", "pads-harmonics"):
        chunk = page.split(f'id="{block}"')[1][:80]
        assert " open" not in chunk, block
    js = Path("synthlab/studio/static/chords.js").read_text(encoding="utf-8")
    assert 'localStorage.setItem("synthlab." + d.id' in js


def test_xy_center_moves_the_params_not_just_the_cross(tmp_path):
    """The cross tracks real values, so it pins to an edge when a param sits
    at its minimum. Centring must move the params, never fake the display."""
    with TestClient(create_app(tmp_path)) as client:
        s = client.app.state.studio
        xparam, yparam = s.xy_axes
        r = client.post("/fragment/xy/center", headers={"HX-Current-URL": "/"})
        assert r.status_code == 200
        for name in (xparam, yparam):
            assert f'id="row-{name}"' in r.text  # the slider comes back OOB
        assert 'id="xypad-panel"' in r.text and "hx-swap-oob" in r.text
