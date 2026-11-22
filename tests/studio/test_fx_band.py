"""A unit's band on the rack (`"band"` on a unit: `fxrack.clean_chain`,
`edit_chain`, `build_chain`, `fxauto`, the mixer's returns, the live keys'
chain): stored only when set, so no chain, state or key from before moves;
refused in words where it cannot be done; loop-safe on the time remaps."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio import fxauto
from synthlab.studio.curves import fx_target
from synthlab.studio.fxlive import live_chain
from synthlab.studio.fxrack import (
    BAND_LABELS,
    BANDLESS,
    FX_UNITS,
    RACK_PRESSES,
    RackError,
    build_chain,
    clean_chain,
    edit_chain,
    rack_rows,
)
from synthlab.studio.render import RenderInputs, _wet, _wrap_tile
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import preset

ARR = Arrangement(bpm=120.0)
ED = {"HX-Current-URL": "http://testserver/view/editor", "Origin": "http://testserver"}


def _stem(own: int) -> np.ndarray:
    """A stem's raw render: notes through one loop of `own` samples, the
    last one ringing past it and dying out well before the buffer's end
    (2 x own), as a render's release tails do."""
    x = _ringing(2 * own)
    t = np.arange(2 * own) / SR
    tail = np.where(t < own / SR, 1.0, np.exp(-(t - own / SR) / 0.05))
    return x * tail[:, None]


def _ringing(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 55 * t) * 0.3 + np.sin(2 * np.pi * 3300 * t) * 0.2
    env = np.exp(-(t % 0.5) / 0.15)
    return np.column_stack([x * env, 0.8 * x * env])


# --- storing it -----------------------------------------------------------------


def test_a_chain_without_a_band_is_stored_and_keyed_as_it_always_was():
    unit = {"kind": "drive", "params": {"amount": 3.0}}
    (plain,) = clean_chain([unit])
    assert "band" not in plain
    # whatever a band field holds that is not a band, it is the same unit
    for junk in ("", None, "full", "LOW", "lows", 3, ["low"], {"b": 1}, "low "):
        assert clean_chain([{**unit, "band": junk}]) == [plain], junk
    # a key hashes the stored (clean) chain: the same chain, the same key
    p = preset("bass_reese")

    def key(u):
        return RenderInputs(patch=p, arr=ARR, main_fx=clean_chain([u])).key()

    assert key(unit) == RenderInputs(patch=p, arr=ARR, main_fx=[plain]).key()
    for junk in ("", None, "sideways"):
        assert key({**unit, "band": junk}) == key(unit)
    assert key({**unit, "band": "low"}) != key(unit)


def test_each_of_the_five_bands_is_kept_and_the_limiter_takes_none():
    for band in fx.BANDS:
        (u,) = clean_chain([{"kind": "chorus", "params": {}, "band": band}])
        assert u["band"] == band and clean_chain([u]) == [u]
    (lim,) = clean_chain([{"kind": "limiter", "params": {}, "band": "low"}])
    assert "band" not in lim
    assert set(BANDLESS) == {"limiter"}
    assert tuple(BAND_LABELS)[1:] == fx.BANDS


def test_the_rack_sets_a_band_in_one_press_and_refuses_in_words():
    chain = clean_chain([{"kind": "eq", "params": {"high": 6.0}}])
    assert "band" in RACK_PRESSES  # a press: its own undo step
    out, said, at = edit_chain(
        chain, "set", {"index": "0", "name": "band", "value": "high"}
    )
    assert out[0]["band"] == "high" and at == 0 and said == "EQ on highs"
    back, said, _ = edit_chain(
        out, "set", {"index": "0", "name": "band", "value": "full"}
    )
    assert back == chain and said == "EQ on full"
    with pytest.raises(RackError, match="No such band"):
        edit_chain(chain, "set", {"index": "0", "name": "band", "value": "treble"})
    lim = clean_chain([{"kind": "limiter", "params": {}}])
    with pytest.raises(RackError, match="lookahead"):
        edit_chain(lim, "set", {"index": "0", "name": "band", "value": "low"})
    rows = rack_rows([*out, *lim])
    assert rows[0]["band"] == "high" and [k for k, _ in rows[0]["bands"]][1:] == list(
        fx.BANDS
    )
    assert rows[1]["bands"] == []  # the limiter draws no band select


def test_build_chain_runs_a_banded_unit_on_its_band():
    x = _ringing(SR)
    for band in fx.BANDS:
        units = [{"kind": "drive", "params": {"amount": 7.0}, "band": band}]
        got = fx.chain(x, build_chain(units, ARR))
        want = fx.band_apply(x, band, fx.drive(amount=7.0, mix=1.0, mode="soft"))
        np.testing.assert_array_equal(got, want)
        wet = fx.chain(x, build_chain(units, ARR, wet=True))
        assert np.array_equal(
            wet, fx.band_apply(x, band, fx.drive(amount=7.0), wet=True)
        )


# --- lanes on a banded unit ------------------------------------------------------


@pytest.mark.parametrize("band", fx.BANDS)
@pytest.mark.parametrize(
    ("kind", "param"), [("reverb", "mix"), ("eq", "high"), ("pump", "depth")]
)
def test_a_lane_on_a_banded_unit_is_the_banded_unit_with_its_slider_moving(
    band, kind, param
):
    n = SR
    x = np.vstack([_ringing(n), np.zeros((n, 2))])
    unit = clean_chain([{"kind": kind, "params": {}, "band": band, "id": "a1b2c3d4"}])
    lo, hi, _d = FX_UNITS[kind]["params"][param]
    v = 0.6
    still = [[fx_target("a1b2c3d4", kind, param), [[0.0, v], [1.0, v]]]]
    got, _out = fxauto.render(x, unit, ARR, still, n, 2 * n / SR)
    static = clean_chain([{**unit[0], "params": {param: lo + v * (hi - lo)}}])
    want = fx.chain(x, build_chain(static, ARR, span=2 * n / SR))
    tol = 1e-12 if kind == "eq" else 0.0
    assert np.max(np.abs(got - want)) <= tol
    # a moving mix lane on the band: dry * (1 - m) + wet * m inside it
    if param == "mix":
        rise = [[fx_target("a1b2c3d4", kind, "mix"), [[0.0, 0.0], [1.0, 1.0]]]]
        moved, _out = fxauto.render(x, unit, ARR, rise, n, 2 * n / SR)
        m = fxauto.values([[0.0, 0.0], [1.0, 1.0]], 0.0, 1.0, n, len(x))[:, None]
        full = fx.reverb(mix=1.0)

        def blend(y):
            return y * (1 - m) + full(y) * m

        np.testing.assert_allclose(moved, fx.band_apply(x, band, blend), atol=1e-12)


# --- the mixer's returns ----------------------------------------------------------


@pytest.mark.parametrize("band", ["high", "ms_side"])
def test_a_banded_return_unit_sends_its_band_alone(band):
    x = _ringing(SR)
    units = clean_chain([{"kind": "reverb", "params": {"mix": 1.0}, "band": band}])
    got = _wet(x, units, ARR, 1.0)
    want = fx.band_apply(x, band, fx.reverb(mix=1.0), wet=True)
    np.testing.assert_allclose(got, want, atol=1e-12)
    # a banded delay's undelayed term is its band's, taken out: echoes only
    d = clean_chain(
        [
            {
                "kind": "delay",
                "params": {"time": 0.2, "mix": 1.0, "feedback": 0.5, "ping_pong": 0.0},
                "band": band,
            }
        ]
    )
    y = _wet(x, d, ARR, 1.0)
    assert np.max(np.abs(y[: int(0.2 * SR) - 1])) < 1e-12  # nothing before the echo
    assert np.max(np.abs(y)) > 0.01


# --- the time remaps stay loop-safe -----------------------------------------------


@pytest.mark.parametrize("band", fx.BANDS)
@pytest.mark.parametrize(
    "unit",
    [
        {
            "kind": "beat_repeat",
            "params": {"repeats": 3.0},
            "pattern": "....x.......x...",
        },
        {"kind": "stop", "params": {"beats": 1.0}},
    ],
    ids=["beat_repeat", "stop"],
)
def test_a_banded_time_remap_folds_like_the_loop_it_plays(band, unit):
    """The banded unit commutes with `_wrap_tile`'s fold: the folded stem is
    the effect on the steady-state loop (the middle of three tiled
    repeats). The remap is linear and periodic in the loop, the crossovers
    linear and time-invariant — so it folds as an EQ before it would: to
    rounding, given a render whose tail has died before the buffer's end
    (a crossover's ring past 2 x own is cut by the fold, as any filter's).
    Measured 4.5e-14 on this stem; 0.016 on a tone cut off at full level."""
    arr = Arrangement(bpm=120.0, bars=1, drum_bars=1)
    own = 2 * SR
    raw = _stem(own)
    chain = build_chain([{**unit, "band": band}], arr)
    folded = _wrap_tile(fx.chain(raw, chain), own, own)
    looping = fx.chain(_wrap_tile(raw, own, 3 * own), chain)[own : 2 * own]
    assert not np.allclose(folded, _wrap_tile(raw, own, own))  # it did something
    assert np.max(np.abs(folded - looping)) < 1e-9


# --- the live keys -----------------------------------------------------------------


def test_the_live_chain_carries_the_band_and_its_crossover():
    units = [
        {"kind": "drive", "params": {}, "band": "low"},
        {"kind": "reverb", "params": {}, "band": "ms_side"},
        {"kind": "drive", "params": {}},
    ]
    live = live_chain(units, ARR, False)
    assert live[0]["band"] == "low" and live[0]["band_split"] == fx.OTT_SPLIT
    assert live[0]["band_q"] == fx.OTT_Q and live[1]["band"] == "ms_side"
    assert "band" not in live[2] and "band_split" not in live[2]


# --- the route --------------------------------------------------------------------


def test_the_band_select_posts_one_undo_step(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    client.post(
        "/fragment/fx/add", data={"scope": "master", "kind": "chorus"}, headers=ED
    )
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "master", "index": "0", "name": "band", "value": "ms_side"},
        headers=ED,
    )
    assert r.status_code == 200 and s.fx_master[0]["band"] == "ms_side"
    sel = r.text.split('class="field field-s fx-band"')[1].split("</select>")[0]
    assert 'hx-sync="this:queue last"' in sel and '"name": "band"' in sel
    assert '<option value="ms_side" selected>side</option>' in sel
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "master", "index": "0", "name": "band", "value": "bogus"},
        headers=ED,
    )
    assert r.status_code == 422 and s.fx_master[0]["band"] == "ms_side"
    client.post("/fragment/undo", headers=ED)
    assert "band" not in s.fx_master[0]
    client.post("/fragment/undo", headers=ED)
    assert s.fx_master == []


SPLIT_JOIN = """
globalThis.sampleRate = 44100;
globalThis.AudioWorkletProcessor = class { constructor() { this.port = {}; } };
const reg = {};
globalThis.registerProcessor = (name, cls) => { reg[name] = cls; };
const fs = require("fs");
// eval, deliberately: the first-party worklet modules, under the shims above
const src = (f) => fs.readFileSync(process.argv[2].replace("fx-worklet", f), "utf8");
for (const f of ["fx-worklet", "fx2-worklet", "band-worklet"]) eval(src(f));
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const raw = new Float32Array(fs.readFileSync(job.input).buffer.slice(0)), n = job.total;
const out = [];
for (const unit of job.units) {
  const sp = new reg["synthlab-band"]({ processorOptions: { unit, join: false } });
  const jn = new reg["synthlab-band"]({ processorOptions: { unit, join: true } });
  const L = new Float32Array(n), R = new Float32Array(n);
  for (let i = 0; i < n; i += 128) {
    const k = Math.min(128, n - i), f = () => new Float32Array(k);
    const band = [f(), f()], rest = [f(), f()], o = [f(), f()];
    const inp = [raw.subarray(i, i + k), raw.subarray(n + i, n + i + k)];
    sp.process([inp], [band, rest]);
    // a stand-in for the native node between them: its two channels apart
    band[0] = band[0].map((v) => v * 0.5); band[1] = band[1].map((v) => v * 0.25);
    jn.process([band, rest], [o]);
    L.set(o[0], i); R.set(o[1], i);
  }
  out.push(Buffer.from(L.buffer), Buffer.from(R.buffer));
}
fs.writeFileSync(job.out, Buffer.concat(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs the worklets")
def test_the_split_and_join_round_a_native_unit_are_the_engines_band(tmp_path):
    """A banded ConvolverNode or biquad sits between two `synthlab-band`
    nodes (livefx.js). With a stand-in between them (each channel its own
    gain), the pair is `effects.band_apply` round the same stand-in: float32
    between the nodes, measured <= 7.7e-8 of peak."""
    n = SR // 2
    x = _ringing(n)
    (tmp_path / "x.f32").write_bytes(
        np.concatenate([x[:, 0], x[:, 1]]).astype("<f4").tobytes()
    )
    units = [
        {"band": b, "band_split": fx.OTT_SPLIT, "band_q": fx.OTT_Q} for b in fx.BANDS
    ]
    job = {
        "input": str(tmp_path / "x.f32"),
        "total": n,
        "units": units,
        "out": str(tmp_path / "o.f32"),
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (tmp_path / "h.cjs").write_text(SPLIT_JOIN, encoding="utf-8")
    fxw = Path("synthlab/studio/static/fx-worklet.js").resolve()
    subprocess.run(
        ["node", str(tmp_path / "h.cjs"), str(fxw), str(tmp_path / "job.json")],
        check=True,
        timeout=120,
    )
    raw = np.fromfile(tmp_path / "o.f32", dtype="<f4").astype(np.float64).reshape(-1, n)
    x32 = x.astype(np.float32).astype(np.float64)

    def stand_in(y):
        return np.column_stack([y[:, 0] * 0.5, y[:, 1] * 0.25])

    for i, band in enumerate(fx.BANDS):
        live = np.column_stack([raw[2 * i], raw[2 * i + 1]])
        eng = fx.band_apply(x32, band, stand_in)
        err = np.max(np.abs(live - eng)) / np.max(np.abs(eng))
        assert err < 1e-6, (band, err)
