"""FX rack: chain validation, audible application, endpoints."""

import html as html_lib
import json
import re
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.studio.fxrack import (
    FX_UNITS,
    MAX_UNITS,
    build_chain,
    clean_chain,
    resolve_unit,
)
from synthlab.studio.render import RenderInputs, render_audio
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import preset


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_clean_chain_drops_unknown_and_clamps():
    dirty = [
        {"kind": "reverb", "params": {"mix": 5.0, "bogus": 1}},
        {"kind": "nonsense"},
        "not a dict",
        {"kind": "gate", "pattern": "DROP TABLE"},
    ]
    cleaned = clean_chain(dirty)
    assert [u["kind"] for u in cleaned] == ["reverb", "gate"]
    assert cleaned[0]["params"]["mix"] == 1.0  # clamped to hi
    assert "bogus" not in cleaned[0]["params"]
    assert cleaned[1]["pattern"] == "x.x.x.x."  # invalid pattern -> default


def test_clean_chain_caps_length():
    assert len(clean_chain([{"kind": "width"}] * 20)) == MAX_UNITS


def test_every_unit_builds_and_runs():
    arr = Arrangement(drums=[["kick", 0]])
    x = np.random.default_rng(0).standard_normal((22050, 2)) * 0.1
    for kind in FX_UNITS:
        chain = build_chain([{"kind": kind}], arr)
        assert len(chain) == 1, kind
        y = chain[0](x)
        assert y.shape == x.shape, kind
        assert np.all(np.isfinite(y)), kind


def test_fx_change_the_render():
    arr = Arrangement()
    p = preset("bass_reese")
    dry = render_audio(RenderInputs(patch=p, arr=arr))
    wet = render_audio(
        RenderInputs(
            patch=p, arr=arr, main_fx=[{"kind": "bitcrush", "params": {"bits": 5.0}}]
        )
    )
    assert not np.allclose(dry, wet, atol=1e-6)
    mastered = render_audio(
        RenderInputs(
            patch=p,
            arr=arr,
            master_fx=[{"kind": "gate", "params": {"depth": 1.0}}],
        )
    )
    assert not np.allclose(dry, mastered, atol=1e-6)


def test_fx_endpoints_roundtrip(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/fx/add", data={"scope": "layer", "kind": "phaser"})
    assert r.status_code == 200
    assert "Phaser" in r.text
    assert "preview-ready" in r.headers.get("HX-Trigger", "")
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "0", "name": "rate", "value": "1.5"},
    )
    assert r.status_code == 200 and 'value="1.5"' in r.text
    r = client.post("/fragment/fx/add", data={"scope": "master", "kind": "reverb"})
    assert "Reverb" in r.text
    r = client.post(
        "/fragment/fx/move", data={"scope": "layer", "index": "0", "dir": "1"}
    )
    assert r.status_code == 200
    r = client.post("/fragment/fx/remove", data={"scope": "layer", "index": "0"})
    assert r.status_code == 200
    assert "<strong>Phaser</strong>" not in r.text.split("Master FX")[0]
    assert (
        client.post(
            "/fragment/fx/add", data={"scope": "layer", "kind": "nope"}
        ).status_code
        == 422
    )


def test_fx_persist_across_restart(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/fragment/fx/add", data={"scope": "master", "kind": "vinyl"})
    from synthlab.studio.state import StudioState

    s = StudioState(tmp_path)
    assert [u["kind"] for u in s.fx_master] == ["vinyl"]


def test_tone_units_are_registered_and_build():
    """fold/ringmod/comb map 1:1 onto their factories, so they fall through
    build_chain's generic branch — this pins that they actually do."""
    for kind in ("fold", "ringmod", "comb"):
        assert kind in FX_UNITS, kind
    chain = build_chain(
        [{"kind": "fold"}, {"kind": "ringmod"}, {"kind": "comb"}], Arrangement()
    )
    assert len(chain) == 3
    x = np.zeros((1000, 2))
    for f in chain:  # every unit must survive being called on real audio
        assert f(x).shape == x.shape


def test_comb_feedback_ceiling_matches_the_dsp():
    """The UI's clamp has to agree with effects.COMB_MAX_FEEDBACK, or a
    slider at maximum would ask for a resonator the DSP refuses to build."""
    from synthlab.effects import COMB_MAX_FEEDBACK

    assert FX_UNITS["comb"]["params"]["feedback"][1] == COMB_MAX_FEEDBACK


def test_tone_units_are_audible_in_a_render(tmp_path):
    """A registered unit that does not change the mix is decoration."""
    arr = Arrangement()
    patch = preset("bass_reese")
    dry = render_audio(RenderInputs(patch=patch, arr=arr, curves={}, parts=("synth",)))
    for kind in ("fold", "ringmod", "comb"):
        wet = render_audio(
            RenderInputs(
                patch=patch,
                arr=arr,
                curves={},
                master_fx=[{"kind": kind}],
                parts=("synth",),
            )
        )
        assert wet.shape == dry.shape, kind
        assert np.all(np.isfinite(wet)), kind
        assert not np.allclose(wet, dry, atol=1e-4), f"{kind} did nothing"


def test_delay_sync_resolves_from_bpm():
    """Delay time was raw seconds — the one FX that must lock to the groove
    drifted against the grid. sync 2 = 1/8: at 100 bpm that is
    0.3 s regardless of the time slider."""
    from synthlab.studio.state import Arrangement

    units = clean_chain(
        [
            {
                "kind": "delay",
                "params": {
                    "time": 0.9,
                    "feedback": 0.3,
                    "mix": 0.3,
                    "ping_pong": 1.0,
                    "sync": 2.0,
                },
            }
        ]
    )
    assert units[0]["params"]["sync"] == 2.0  # clean_chain keeps it
    chain = build_chain(units, Arrangement(bpm=100.0))
    assert abs(chain[0].keywords["time"] - 0.3) < 1e-9


def test_delay_sync_off_keeps_the_slider():
    from synthlab.studio.state import Arrangement

    units = [{"kind": "delay", "params": {"time": 0.9, "sync": 0.0}}]
    chain = build_chain(clean_chain(units), Arrangement(bpm=100.0))
    assert abs(chain[0].keywords["time"] - 0.9) < 1e-9


def test_a_sidechain_is_triggered_by_the_kicks_that_sound():
    """The unit's triggers are the drum machine's kick hits — and only
    those of a row that plays: a muted kick, or one soloed out, hands it
    none, and a steady signal comes back untouched instead of pumping
    under a kick nobody hears. (`pump` is the duck with no kick.)"""
    from dataclasses import replace

    arr = Arrangement(bpm=120.0, drums=[["kick", 0], ["kick", 8], ["snare", 4]])
    units = [{"kind": "sidechain", "params": {"depth": 1.0, "release": 0.2}}]
    tone = np.ones((2 * 44100, 2))

    def run(mix):
        (unit,) = build_chain(units, replace(arr, drum_mix=mix))
        return unit.keywords["trigger_times"], np.asarray(unit(tone.copy()))

    times, out = run({})
    assert times[:3] == [0.0, 1.0, 2.0]  # steps 0 and 8 of a 2 s loop, repeated
    assert out[:2000].min() < 0.3 and out[44100:46100].min() < 0.3  # ducked
    for mix in ({"kick": {"solo": True}}, {"kick": {"level": 0.3}}):
        same_times, same = run(mix)
        assert same_times == times
        np.testing.assert_array_equal(same, out)
    for mix in ({"kick": {"mute": True}}, {"snare": {"solo": True}}):
        times, out = run(mix)
        assert times == []
        np.testing.assert_allclose(out, tone, atol=1e-12)


# --- where the keyboard goes after add / move / remove -----------------------------
# These three replace the rack (outerHTML), which destroys the control that
# was pressed. The route names the one that gets the keyboard next and the
# template draws it with `autofocus`; htmx's settle focuses it (the journey
# is tests/browser/test_live_keys.py). Units have no identity of their own —
# an id on each would change what a session saves — so the route says where.


def _autofocus(html: str) -> list[str]:
    """Every control drawn with `autofocus`, as "aria-label" or, for a
    parameter slider, "<unit label> / <param>" by its place in the rack."""
    out = []
    for tag in re.findall(r"<(?:button|select|input)\b[^>]*>", html):
        if not re.search(r"\sautofocus\b", tag):
            continue
        label = re.search(r'aria-label="([^"]+)"', tag)
        vals = re.search(r"hx-vals='([^']+)'", tag)
        out.append(label.group(1) if label else vals.group(1))
    return out


def _focused_vals(html: str) -> dict:
    """The hx-vals of the one control drawn with `autofocus`."""
    (tag,) = [
        t
        for t in re.findall(r"<(?:button|select|input)\b[^>]*>", html)
        if re.search(r"\sautofocus\b", t)
    ]
    return json.loads(html_lib.unescape(re.search(r"hx-vals='([^']+)'", tag)[1]))


def _fx(client, op: str, **data):
    r = client.post(f"/fragment/fx/{op}", data={k: str(v) for k, v in data.items()})
    assert r.status_code == 200, r.text[:200]
    return r.text


def _kinds(client, scope="layer") -> list[str]:
    s = client.app.state.studio
    chain = s.layers[s.active_layer]["fx"] if scope == "layer" else s.fx_master
    return [u["kind"] for u in chain]


def test_a_plain_render_of_the_rack_focuses_nothing(tmp_path):
    client = make_client(tmp_path)
    _fx(client, "add", scope="layer", kind="reverb")
    page = client.get("/").text
    rack = page[page.index('id="fx-rack"') :]
    assert "autofocus" not in rack[: rack.index("</section>")]
    # a parameter change morphs the rack: the slider keeps its own focus
    html = _fx(client, "set", scope="layer", index=0, name="mix", value=0.4)
    assert "autofocus" not in html


def test_add_gives_the_keyboard_to_the_new_units_first_control(tmp_path):
    client = make_client(tmp_path)
    first = next(iter(FX_UNITS["reverb"]["params"]))
    html = _fx(client, "add", scope="layer", kind="reverb")
    assert _autofocus(html) == [f"Reverb {first}"]
    assert _focused_vals(html) == {"scope": "layer", "index": "0", "name": first}
    first = next(iter(FX_UNITS["delay"]["params"]))
    html = _fx(client, "add", scope="layer", kind="delay")  # appended: index 1
    assert _autofocus(html) == [f"Delay {first}"]
    assert _focused_vals(html) == {"scope": "layer", "index": "1", "name": first}
    # another scope's rack: its own new unit, nothing in the layer's
    first = next(iter(FX_UNITS["width"]["params"]))
    html = _fx(client, "add", scope="master", kind="width")
    assert _autofocus(html) == [f"Width {first}"]
    assert _focused_vals(html) == {"scope": "master", "index": "0", "name": first}


def test_a_chain_preset_gives_the_keyboard_to_its_first_unit(tmp_path):
    from synthlab.studio.fxrack import CHAIN_PRESETS

    client = make_client(tmp_path)
    _fx(client, "add", scope="layer", kind="reverb")
    key = min(CHAIN_PRESETS, key=lambda k: len(CHAIN_PRESETS[k]["units"]))
    html = _fx(client, "chain", scope="layer", chain=key)
    assert len(_kinds(client)) == 1 + len(CHAIN_PRESETS[key]["units"])
    kind = _kinds(client)[1]  # the preset's first unit, after the reverb
    first = next(iter(FX_UNITS[kind]["params"]))
    assert _focused_vals(html) == {"scope": "layer", "index": "1", "name": first}


def test_move_keeps_the_keyboard_on_the_moved_units_same_button(tmp_path):
    client = make_client(tmp_path)
    for kind in ("reverb", "delay", "width"):
        _fx(client, "add", scope="layer", kind=kind)
    html = _fx(client, "move", scope="layer", index=0, dir=1)
    assert _kinds(client) == ["delay", "reverb", "width"]
    assert _autofocus(html) == ["move reverb later"]
    # its button is the one at the unit's new index: pressing it again
    # carries the same unit on
    assert _focused_vals(html) == {"scope": "layer", "index": "1", "dir": "1"}
    html = _fx(client, "move", scope="layer", index=1, dir=1)
    assert _kinds(client) == ["delay", "width", "reverb"]
    assert _autofocus(html) == ["move reverb later"]
    # at the end there is nowhere to go: nothing moves, the button keeps focus
    html = _fx(client, "move", scope="layer", index=2, dir=1)
    assert _kinds(client) == ["delay", "width", "reverb"]
    assert _autofocus(html) == ["move reverb later"]
    html = _fx(client, "move", scope="layer", index=2, dir=-1)
    assert _kinds(client) == ["delay", "reverb", "width"]
    assert _autofocus(html) == ["move reverb earlier"]


def test_two_units_of_one_kind_are_told_apart_by_place(tmp_path):
    client = make_client(tmp_path)
    for kind in ("delay", "delay", "reverb"):
        _fx(client, "add", scope="layer", kind=kind)
    html = _fx(client, "move", scope="layer", index=0, dir=1)
    assert _autofocus(html) == ["move delay later"]  # one control, not both delays'
    assert _focused_vals(html) == {"scope": "layer", "index": "1", "dir": "1"}


def test_remove_gives_the_keyboard_to_the_next_unit_or_the_adder(tmp_path):
    client = make_client(tmp_path)
    for kind in ("reverb", "delay", "width"):
        _fx(client, "add", scope="layer", kind=kind)
    html = _fx(client, "remove", scope="layer", index=0)
    assert _autofocus(html) == ["remove delay"]  # the unit that took its place
    html = _fx(client, "remove", scope="layer", index=1)  # the last one
    assert _kinds(client) == ["delay"]
    assert _autofocus(html) == ["add effect to Layer 1 FX"]
    html = _fx(client, "remove", scope="layer", index=0)  # the rack is empty
    assert _kinds(client) == []
    assert _autofocus(html) == ["add effect to Layer 1 FX"]


def test_the_rack_template_has_no_focus_script_and_units_gained_no_id(tmp_path):
    """The mechanism is htmx's own (`autofocus` in swapped-in content), not
    a restore in an island, and a unit is still `{kind, params}`: nothing
    was added to what a session saves to tell units apart."""
    client = make_client(tmp_path)
    _fx(client, "add", scope="layer", kind="reverb")
    s = client.app.state.studio
    assert set(s.layers[s.active_layer]["fx"][0]) <= {"kind", "params", "pattern"}
    for name in ("audio.js", "livefx.js", "layout.js"):
        js = Path("synthlab/studio/static", name).read_text(encoding="utf-8")
        assert "fx-rack" not in js or "focus(" not in js.split("fx-rack")[1][:300]
    tpl = "".join(
        Path("synthlab/studio/templates/fragments", name).read_text(encoding="utf-8")
        for name in ("fx_rack.html", "fx_units.html")
    )
    # adder, earlier, later, remove, the first parameter (a knob or a menu)
    assert tpl.count("autofocus{% endif %}") == 6


# --- distortion and dynamics (Oct 2026): drive modes, the clipper, the
# compressor's attack / release / makeup ----------------------------------------


def test_the_new_params_clean_round_trip_and_clamp():
    dirty = [
        {"kind": "drive", "params": {"amount": 40.0, "mode": 2.6}},
        {"kind": "drive", "params": {"mode": -3}},
        {"kind": "clip", "params": {"drive": 99, "ceiling": 3, "soft": -1, "mix": "x"}},
        {"kind": "compress", "params": {"attack": 0.0, "release": 9, "makeup": 50}},
    ]
    drive, low, clip, comp = clean_chain(dirty)
    assert drive["params"] == {"amount": 12.0, "mix": 1.0, "mode": 3.0}  # snapped
    assert low["params"]["mode"] == 0.0
    assert clip["params"] == {"drive": 24.0, "ceiling": 0.0, "soft": 0.0, "mix": 1.0}
    assert comp["params"] == {
        "threshold": -14.0,
        "ratio": 4.0,
        "attack": 0.0005,
        "release": 1.0,
        "makeup": 24.0,
    }
    assert clean_chain([drive, low, clip, comp]) == [drive, low, clip, comp]


def test_a_stored_unit_without_the_new_params_resolves_to_the_old_call():
    """A drive / compressor saved before these params existed resolves to
    exactly the arguments the old factory defaults gave it."""
    arr = Arrangement()
    (drive,) = clean_chain([{"kind": "drive", "params": {"amount": 2.5, "mix": 0.5}}])
    assert resolve_unit(drive, arr) == (
        "drive",
        {"amount": 2.5, "mix": 0.5, "mode": "soft"},
    )
    (comp,) = clean_chain([{"kind": "compress", "params": {"threshold": -20.0}}])
    name, kw = resolve_unit(comp, arr)
    assert name == "compress" and kw["makeup"] is None
    assert (kw["attack"], kw["release"]) == (0.005, 0.12)
    # (threshold, ratio, attack, release, makeup, sr): what the old call used
    assert fx._compress.__defaults__[2:5] == (0.005, 0.12, None)
    (comp,) = clean_chain([{"kind": "compress", "params": {"makeup": 6.0}}])
    assert resolve_unit(comp, arr)[1]["makeup"] == 10 ** (6.0 / 20)
    (comp,) = clean_chain([{"kind": "compress", "params": {"makeup": 0.0}}])
    assert resolve_unit(comp, arr)[1]["makeup"] == 1.0  # 0 dB is unity, not auto
    for i, mode in enumerate(fx.DRIVE_MODES):
        (u,) = clean_chain([{"kind": "drive", "params": {"mode": i}}])
        assert resolve_unit(u, arr)[1]["mode"] == mode


def test_the_drive_mode_is_a_menu_and_posts_its_index(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/fx/add", data={"scope": "layer", "kind": "drive"})
    assert r.status_code == 200
    menu = re.search(
        r'<label class="fx-param fx-choice">mode(.*?)</label>', r.text, re.S
    )
    assert menu and "<select" in menu.group(1) and 'type="range"' not in menu.group(1)
    assert '<option value="0" selected>soft</option>' in menu.group(1)
    assert "hx-sync" in menu.group(1)
    r = client.post(
        "/fragment/fx/set",
        data={"scope": "layer", "index": "0", "name": "mode", "value": "2"},
    )
    assert '<option value="2" selected>fuzz</option>' in r.text
    r = client.post("/fragment/fx/add", data={"scope": "layer", "kind": "clip"})
    assert "<strong>Clipper</strong>" in r.text
    for name in ("drive", "ceiling", "soft", "mix"):
        assert f'"name": "{name}"' in r.text


def test_the_clipper_in_a_chain_caps_the_render():
    x = np.random.default_rng(3).standard_normal((22050, 2)) * 0.5
    unit = {"kind": "clip", "params": {"drive": 18.0, "ceiling": -6.0, "soft": 0.0}}
    y = build_chain([unit], Arrangement())[0](x)
    assert np.max(np.abs(y)) <= 10 ** (-6.0 / 20) + 1e-12
