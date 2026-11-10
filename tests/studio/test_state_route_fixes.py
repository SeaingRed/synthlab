"""Regressions for the studio state / route review (Sep 2026): each test
names the bug it pins. Layer slots own their undo ring, baselines and
sequencing; a track edit owns its curves and FX; slider posts only apply
what moved; routes answer for the view that asked; the song bridges keep
mutes and grids; and a handful of small 500s and orphan writes."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, replace
from html.parser import HTMLParser

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.sequencer import midi
from synthlab.studio import schema
from synthlab.studio.breedboard import Tile
from synthlab.studio.factory import get_factory
from synthlab.studio.paramui import _SPEC_BY_NAME
from synthlab.studio.server import create_app
from synthlab.studio.state import (
    RESET_SCOPES,
    Arrangement,
    StudioState,
    preset_from_json,
)
from synthlab.synth import Patch
from tests.studio import test_song_routes as sr


def _url(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


EDITOR = {"HX-Request": "true", "HX-Current-URL": "http://testserver/"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _preview_key(r) -> str:
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    return url.rsplit("/", 1)[1].removesuffix(".wav")


# --- 1. undo / revert belong to the layer on screen ----------------------------


def test_undo_after_switching_layers_never_reaches_the_other_layer(tmp_path):
    s = StudioState(tmp_path)
    s.patch = s.patch.tweak(cutoff=500.0)
    s.push_history()
    s.patch = s.patch.tweak(cutoff=900.0)
    s.select_layer(1)
    s.patch = s.patch.tweak(cutoff=2000.0)
    assert not s.pop_history()  # layer 2's ring is its own, and empty
    assert s.patch.cutoff == 2000.0
    s.select_layer(0)
    assert s.patch.cutoff == 900.0
    assert s.pop_history() and s.patch.cutoff == 500.0


def test_switching_layers_shows_no_change_and_revert_stays_on_the_layer(tmp_path):
    s = StudioState(tmp_path)
    s.patch = s.patch.tweak(cutoff=500.0)
    s.mark_baseline("sound", "Bass")
    s.patch = s.patch.tweak(cutoff=900.0)
    assert s.is_dirty("sound")
    s.select_layer(1)
    assert not any(s.is_dirty(k) for k in ("sound", "fx", "sequence"))
    s.patch = s.patch.tweak(cutoff=3000.0)
    assert s.revert("sound")
    # layer 2's own baseline (the sound it inherited), never layer 1's
    assert s.patch.cutoff == 900.0
    s.select_layer(0)
    assert s.baseline_label("sound") == "Bass" and s.is_dirty("sound")
    assert s.revert("sound") and s.patch.cutoff == 500.0


def test_opening_a_section_or_a_project_drops_other_slots_history(tmp_path):
    s = StudioState(tmp_path)
    s.select_layer(1)
    s.push_history()
    s.select_layer(0)
    s.song = schema.conform(sr.song_dict(), schema.nodes().SONG)
    assert s.section_to_loop("s1")
    s.select_layer(1)
    assert not s.pop_history()  # slot 2 holds the section's lane now
    s.push_history()
    s.load_project(s.project_payload())
    assert not s._history and not s._layer_undo


# --- 2. a slot's sequencing and curves travel with it ---------------------------


def test_switching_layers_and_back_is_a_no_op_on_the_loop(client):
    svc, s = client.app.state.svc, client.app.state.studio
    curve = {"cutoff": [[0.0, 0.2], [1.0, 0.8]]}
    s.arrangement = Arrangement(
        cells=[["A1", 0], ["C2", 2]],
        velocities={"0": 0.3},
        slides=[2],
        arp_mode="up",
        arp_octaves=2,
    )
    s.curves = dict(curve)
    before = svc.render_inputs("perform").key()
    s.select_layer(1)
    # the new slot's own lanes: nothing of layer 1's sequencing leaks in
    assert s.arrangement.velocities == {} and s.arrangement.slides == []
    s.arrangement = Arrangement(
        cells=[["E2", 1]], velocities={"1": 1.0}, arp_mode="down", arp_octaves=3
    )
    s.curves = {"resonance": [[0.0, 1.0], [1.0, 0.0]]}
    s.select_layer(0)
    arr = s.arrangement
    assert (arr.velocities, arr.slides, arr.arp_mode, arr.arp_octaves) == (
        {"0": 0.3},
        [2],
        "up",
        2,
    )
    assert s.curves == curve
    s.set_layer_muted(1, True)  # layer 2 now has notes: mute it to compare
    assert svc.render_inputs("perform").key() == before
    s.select_layer(1)
    assert s.arrangement.arp_mode == "down" and s.curves["resonance"]


def test_per_layer_sequencing_persists(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement = Arrangement(cells=[["A1", 0]], velocities={"0": 0.4})
    s.select_layer(1)
    s.save()
    s2 = StudioState(tmp_path)
    assert s2.layers[0]["seq"]["velocities"] == {"0": 0.4}
    s2.select_layer(0)
    assert s2.arrangement.velocities == {"0": 0.4}


# --- 3. a track edit edits the track's curves and FX, not the loop's -----------


def test_track_edit_owns_curves_fx_and_baselines(client):
    s = client.app.state.studio
    sr.use_song(client)
    loop_curves = {"cutoff": [[0.0, 0.1], [1.0, 0.9]]}
    s.curves = dict(loop_curves)
    s.layers[s.active_layer]["fx"] = [{"kind": "reverb", "params": {}}]
    loop_fx = list(s.layers[s.active_layer]["fx"])
    s.mark_baseline("sound", "loop sound")
    loop_patch = s.patch
    assert s.edit_track("t1")
    assert s.curves == {} and s.layers[s.active_layer]["fx"] == []
    assert s.baseline_label("sound") == "Bass" and not s.is_dirty("sound")
    s.curves["resonance"] = [[0.0, 0.5], [1.0, 0.5]]
    s.layers[s.active_layer]["fx"].append({"kind": "drive", "params": {}})
    assert s.mirror_patch_to_track()
    track = next(t for t in s.song["tracks"] if t["id"] == "t1")
    assert track["curves"] == {"resonance": [[0.0, 0.5], [1.0, 0.5]]}
    assert [u["kind"] for u in track["fx"]] == ["drive"]
    # mid-edit, the session file keeps the loop's own sound (as a load
    # would hold it: this test's hand-built reverb with its parameters)
    payload = s._payload()
    assert payload["curves"] == loop_curves
    assert payload["layers"][s.active_layer]["fx"] == schema.conform(
        loop_fx, schema.nodes().FX
    )
    assert s._live_payload()["layers"][s.active_layer]["fx"] == loop_fx
    assert payload["baselines"]["sound"]["label"] == "loop sound"
    # revert goes back to the track as opened, never to the loop's sound
    assert s.revert("sound")
    assert s.patch.to_json() == Patch.from_dict(track["patch"]).to_json()
    assert s.end_track_edit()
    assert s.patch is loop_patch and s.curves == loop_curves
    assert s.layers[s.active_layer]["fx"] == loop_fx
    assert s.baseline_label("sound") == "loop sound"


# --- 4. a slider post applies only what moved ------------------------------------


class _Controls(HTMLParser):
    """The #patch form's controls as a browser would post them: linear
    ranges clamped to their min/max and snapped to their step."""

    def __init__(self):
        super().__init__()
        self.values: dict[str, str] = {}
        self._in_form = self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "patch":
            self._in_form = True
        if not self._in_form:
            return
        name = a.get("name")
        if tag == "input" and name in _SPEC_BY_NAME:
            if a.get("type") == "range":
                lo, hi, st = float(a["min"]), float(a["max"]), float(a["step"])
                v = min(hi, max(lo, float(a["value"])))
                k = round((v - lo) / st)
                if lo + k * st > hi + 1e-12:
                    k -= 1
                self.values[name] = repr(lo + k * st)
            elif a.get("type") == "radio":
                if "checked" in a:
                    self.values[name] = a["value"]
            else:
                self.values[name] = a.get("value", "")
        if tag == "select" and name in _SPEC_BY_NAME:
            self._select = name
        if tag == "option" and self._select and "selected" in a:
            self.values[self._select] = a["value"]

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        if tag == "form":
            self._in_form = False


@pytest.mark.parametrize(
    "name", ["Laser Zap", "Event Horizon Drone", "Interstellar Whoop", "Rising Whoosh"]
)
def test_one_slider_nudge_never_rewrites_a_factory_sound(client, name):
    s = client.app.state.studio
    assert client.post("/fragment/factory/load", data={"name": name}).status_code == 200
    factory = get_factory(name)
    parser = _Controls()
    parser.feed(client.get("/").text)
    form = dict(parser.values)
    assert len(form) > 50
    form["resonance"] = repr(round(float(s.patch.resonance) + 0.05, 2))
    assert client.post("/fragment/patch", data=form, headers=EDITOR).status_code == 200
    changed = {
        k
        for k, v in asdict(s.patch).items()
        if k != "resonance" and v != getattr(factory, k)
    }
    assert not changed, {k: getattr(s.patch, k) for k in changed}


def test_a_moved_slider_clamps_to_the_range_every_load_accepts(client):
    s = client.app.state.studio
    client.post("/fragment/patch", data={"filter_env": "-2600"}, headers=EDITOR)
    assert s.patch.filter_env == -2600.0  # it used to become 0
    client.post("/fragment/patch", data={"filter_env": "-99999"}, headers=EDITOR)
    assert s.patch.filter_env == -6000.0


def test_a_slider_is_drawn_wide_enough_for_its_value(client):
    client.post("/fragment/factory/load", data={"name": "Laser Zap"})
    html = client.get("/").text
    tag = re.search(r'<input type="range" id="p-filter_env"[^>]*>', html).group(0)
    assert re.search(r'min="-2600(\.0)?"', tag)
    assert re.search(r'value="-2600(\.0)?"', tag)


# --- 5. the XY pad sends its sliders back ---------------------------------------


def test_xy_move_returns_the_moved_rows_out_of_band(client):
    r = client.post("/fragment/xy", data={"x": "0.3", "y": "0.7"}, headers=EDITOR)
    assert r.status_code == 200
    for name in client.app.state.studio.xy_axes:
        assert re.search(rf'id="row-{name}"[^>]*hx-swap-oob', r.text), name


# --- 6. the drum transport answers for the view it sits on ----------------------


def test_drum_transport_on_perform_previews_the_perform_loop(client):
    svc = client.app.state.svc
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "131"}, headers=_url("perform")
    )
    assert _preview_key(r) == svc.render_inputs("perform").key()
    r = client.post(
        "/fragment/drums/transport", data={"bars": "2"}, headers=_url("perform")
    )
    assert _preview_key(r) == svc.render_inputs("perform").key()
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "120"}, headers=_url("drums")
    )
    assert _preview_key(r) == svc.render_inputs("drums").key()


# --- 7. Bass Lab's quick key is its own -----------------------------------------


def test_bass_quick_key_keeps_the_grid_and_renders_the_bench_low(client):
    svc, s = client.app.state.svc, client.app.state.studio
    s.arrangement = Arrangement(cells=[["A1", 0], ["C2", 3]], bars=2)
    before = asdict(s.arrangement)
    r = client.post("/fragment/bass/key", data={"root": "E1"}, headers=_url("bass"))
    assert r.status_code == 200
    assert asdict(s.arrangement) == before and s.bass_root == "E1"
    inp = svc.render_inputs("bass")  # Bass Lab's own view carries the root
    assert _preview_key(r) == inp.key()
    bench = svc.render_inputs("editor")  # the bench phrase, unmoved
    assert inp.parts == bench.parts and inp.master_fx == bench.master_fx
    assert inp.arr.root == "E1"
    assert [midi(p) - midi("E1") for p, _ in inp.arr.cells] == [
        midi(p) - midi(bench.arr.root) for p, _ in bench.arr.cells
    ]


def test_bass_panels_keep_refreshing_after_their_first_swap(client):
    html = client.get("/fragment/bass").text
    root = re.search(r'<section id="bass-panels"[^>]*>', html).group(0)
    assert 'hx-get="/fragment/bass"' in root and "preview-ready" in root


# --- 8. the Perform grid hides, never deletes ------------------------------------


def _grid_form(s: StudioState, **over) -> dict:
    arr = s.arrangement
    form = {
        "bpm": str(arr.bpm),
        "bars": str(arr.bars),
        "steps": str(arr.steps_per_bar),
        "root": arr.root,
        "scale_name": arr.scale_name,
        "swing": "0",
        "arp_mode": "off",
        "arp_octaves": "1",
    }
    total = arr.bars * arr.steps_per_bar
    for p, st in arr.cells:
        if st < total:
            form[f"cell.{p}.{st}"] = "on"
    for st in range(total):
        form[f"vel.{st}"] = str(arr.velocities.get(str(st), 0.9))
    return form | over


def test_changing_the_key_or_shrinking_the_grid_keeps_every_note(client):
    s = client.app.state.studio
    s.arrangement = Arrangement(
        cells=[["A1", 0], ["C2", 20]], bars=2, steps_per_bar=16, velocities={"20": 0.4}
    )
    client.post(
        "/fragment/arrangement", data=_grid_form(s, bars="1"), headers=_url("perform")
    )
    assert sorted(s.arrangement.cells) == [["A1", 0], ["C2", 20]]
    assert s.arrangement.velocities["20"] == 0.4
    client.post(
        "/fragment/arrangement", data=_grid_form(s, root="E1"), headers=_url("perform")
    )
    assert s.arrangement.root == "E1"
    assert sorted(s.arrangement.cells) == [["A1", 0], ["C2", 20]]
    # back in A1 and 2 bars, the notes are where they were
    client.post(
        "/fragment/arrangement",
        data=_grid_form(s, root="A1", bars="2"),
        headers=_url("perform"),
    )
    assert sorted(s.arrangement.cells) == [["A1", 0], ["C2", 20]]
    # unticking a note the grid shows still deletes it
    form = _grid_form(s)
    del form["cell.A1.0"]
    client.post("/fragment/arrangement", data=form, headers=_url("perform"))
    assert s.arrangement.cells == [["C2", 20]]


# --- 9. loads are undoable and baselined ------------------------------------------


def test_panel_reset_is_one_undo_step(client):
    s = client.app.state.studio
    s.patch = s.patch.tweak(cutoff=333.0)
    s._history_at = -1e9
    client.post("/fragment/panel/reset", data={"group": "Filter"}, headers=EDITOR)
    assert s.patch.cutoff != 333.0
    client.post("/fragment/undo", headers=EDITOR)
    assert s.patch.cutoff == 333.0


def test_ab_swap_and_panel_preset_load_are_undoable_loads(client):
    s = client.app.state.studio
    s.patch = s.patch.tweak(cutoff=400.0)
    client.post("/fragment/ab/slot_a", headers=EDITOR)  # store 400 as A
    s.patch = s.patch.tweak(cutoff=700.0)
    client.post("/fragment/ab/slot_b", headers=EDITOR)  # store 700 as B
    client.post("/fragment/ab/slot_a", headers=EDITOR)  # switch to A: a load
    assert s.patch.cutoff == 400.0 and s.slot_prev.cutoff == 700.0
    assert s.pop_history() and s.patch.cutoff == 700.0
    s.save_panel_preset("Filter", "dark")
    s.patch = s.patch.tweak(cutoff=5000.0)
    s._history_at = -1e9
    client.post(
        "/fragment/panel/preset/load",
        data={"group": "Filter", "name": "dark"},
        headers=EDITOR,
    )
    assert s.patch.cutoff == 700.0 and s.slot_prev.cutoff == 5000.0
    assert s.pop_history() and s.patch.cutoff == 5000.0


def test_opening_a_section_stashes_the_outgoing_sound(client):
    s = client.app.state.studio
    sr.use_song(client)
    s.patch = s.patch.tweak(cutoff=1234.0)
    s._history_at = -1e9
    assert s.section_to_loop("s1")
    assert s.slot_prev.cutoff == 1234.0
    assert s.pop_history() and s.patch.cutoff == 1234.0


def test_map_load_marks_the_sound_baseline(client):
    s = client.app.state.studio
    client.post("/fragment/breed/generation", data={})
    tid = s.breed_tiles[0]["id"]
    assert client.post("/fragment/load", data={"id": tid}).status_code == 200
    assert s.baseline_label("sound") == s.patch.name
    assert not s.is_dirty("sound")


# --- 10. breed stars ------------------------------------------------------------


def _fake_generation(monkeypatch):
    count = iter(range(1000))

    def gen(state, direction, amount=None, **_kw):
        g = next(count)
        return [
            Tile(id=f"g{g}t{i}", name=f"t{i}", sub_pct=0.0, centroid=0)
            for i in range(8)
        ]

    monkeypatch.setattr("synthlab.studio.routes.breed.new_generation", gen)


def test_stars_are_consumed_by_a_generation_and_always_unstarrable(client, monkeypatch):
    _fake_generation(monkeypatch)
    s = client.app.state.studio
    r = client.post("/fragment/breed/generation", data={}, headers=_url("breed"))
    assert re.search(r'id="gen-strip"[^>]*hx-swap-oob', r.text)
    r = client.post("/fragment/breed/star", data={"id": "g0t3"}, headers=_url("breed"))
    assert s.breed_starred == ["g0t3"]
    assert "1 starred" in r.text  # the parent line rides along
    client.post("/fragment/breed/generation", data={}, headers=_url("breed"))
    assert s.breed_starred == []  # the parent bred this litter
    # a star left over from an old board (e.g. an older save) comes off
    s.breed_starred = ["zzz"]
    client.post("/fragment/breed/star", data={"id": "zzz"}, headers=_url("breed"))
    assert s.breed_starred == []
    # a card of a restorable generation can be starred
    client.post("/fragment/breed/star", data={"id": "g0t1"}, headers=_url("breed"))
    assert s.breed_starred == ["g0t1"]
    r = client.post(
        "/fragment/breed/restore", data={"index": "0"}, headers=_url("breed")
    )
    assert re.search(r'id="gen-strip"[^>]*hx-swap-oob', r.text)


def test_breed_page_always_has_a_gen_strip_to_swap(client, monkeypatch):
    _fake_generation(monkeypatch)
    s = client.app.state.studio
    s.breed_tiles = [asdict(Tile(id="x", name="x", sub_pct=0.0, centroid=0))]
    s.breed_history = []
    html = client.get("/view/breed").text
    assert re.search(r'<nav class="gen-strip" id="gen-strip"[^>]*hidden', html)


# --- 11. FX move at the edge keeps the view's racks --------------------------


def test_fx_move_at_the_edge_redraws_the_drum_bus_on_drums(client):
    client.post(
        "/fragment/fx/add",
        data={"scope": "drums", "kind": "reverb"},
        headers=_url("drums"),
    )
    r = client.post(
        "/fragment/fx/move",
        data={"scope": "drums", "index": "0", "dir": "-1"},
        headers=_url("drums"),
    )
    assert r.status_code == 200
    assert "Drum bus FX" in r.text and "Master FX" not in r.text


# --- 12. the vocoder's render key ------------------------------------------------


def _take_wav(freq: float) -> bytes:
    import io
    import wave

    t = np.arange(4410) / 44100
    pcm = (0.3 * np.sin(2 * np.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def test_voice_render_key_follows_the_take_and_the_chain(client):
    def upload(freq):
        r = client.post(
            "/voice/take", files={"file": ("t.wav", _take_wav(freq), "audio/wav")}
        )
        assert r.status_code == 200

    def render(**form):
        r = client.post("/voice/render", data={"bands": "16", **form})
        assert r.status_code == 200, r.text[:200]
        return _preview_key(r)

    upload(220)
    a = render()
    upload(330)  # same length, other words
    b = render()
    assert a != b
    client.app.state.studio.fx_master = [{"kind": "reverb", "params": {}}]
    assert render() != b
    assert render(bands="17") == render(bands="16")  # snapped to a choice


@pytest.mark.parametrize("bands", ["inf", "1e400", "-5", "nan"])
def test_voice_render_never_uses_a_nonsense_band_count(client, bands):
    """It used to answer 422. Since the Voice lab keeps its settings
    (`voice_lab.json`) a band count that is
    no number at all falls back to the saved one, and one that is a number
    off the menu (-5) is snapped to the nearest choice, as 17 is — never a
    500, never the nonsense."""
    from synthlab.studio.voice import BAND_CHOICES

    client.post("/voice/take", files={"file": ("t.wav", _take_wav(220), "audio/wav")})
    # the fallback first: each render saves the settings it used
    want = {"bands": str(min(BAND_CHOICES))} if bands == "-5" else {}
    expected = client.post("/voice/render", data=want)
    r = client.post("/voice/render", data={"bands": bands})
    assert r.status_code == expected.status_code == 200
    assert _preview_key(r) == _preview_key(expected)


# --- 14. the song's mix mutes never become a track's own ------------------------


def test_a_solo_silenced_track_round_trips_to_itself(client):
    s = client.app.state.studio
    song = sr.song_dict()
    for t in song["tracks"]:
        if t["id"] == "t2":
            t["solo"] = True  # silences t1 (the main lane) in the mix
    sr.use_song(client, song)
    tracks_before = [t["id"] for t in s.song["tracks"]]
    assert s.section_to_loop("s2")
    active = s.layers[s.active_layer]
    assert active["muted"] and active["silent"]
    sid = s.loop_to_section("again")
    assert [t["id"] for t in s.song["tracks"]] == tracks_before  # no muted copy
    sec = s.song["sections"][-1]
    assert sec["id"] == sid and set(sec["cells"]) >= {"t1", "t2"}


def test_a_muted_layer_with_notes_stays_in_the_section(client):
    s = client.app.state.studio
    song = sr.song_dict()
    for t in song["tracks"]:
        if t["id"] == "t2":
            t["mute"] = True
    sr.use_song(client, song)
    assert s.section_to_loop("s2")
    lay = next(
        lay for i, lay in enumerate(s.layers) if i != s.active_layer and lay["cells"]
    )
    assert lay["muted"] and not lay["silent"]
    s.loop_to_section("again")
    sec = s.song["sections"][-1]
    assert sec["cells"].get("t2") is not None  # it used to be dropped


def test_a_layer_on_its_own_grid_lands_on_the_loop_grid(client):
    s = client.app.state.studio
    song = sr.song_dict()
    song["patterns"]["p2"] |= {"steps_per_bar": 8, "cells": [["C2", 1], ["C2", 9]]}
    sr.use_song(client, song)
    assert s.section_to_loop("s2")
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (2, 16)
    hook = next(
        lay for i, lay in enumerate(s.layers) if i != s.active_layer and lay["cells"]
    )
    # 8ths onto 16ths: each note fills the two steps it covers, both bars
    assert sorted(map(tuple, hook["cells"])) == [
        ("C2", 2),
        ("C2", 3),
        ("C2", 18),
        ("C2", 19),
    ]


# --- 15. the small ones -----------------------------------------------------------


def test_a_v2_preset_load_and_save_mark_the_fx_baseline(tmp_path):
    s = StudioState(tmp_path)
    s.layers[s.active_layer]["fx"] = [{"kind": "reverb", "params": {}}]
    s.save_preset("wide", include_layers=True)
    assert s.baseline_label("fx") == "wide"
    s.layers[s.active_layer]["fx"] = []
    s.load_preset("wide")
    assert s.baseline_label("fx") == "wide" and not s.is_dirty("fx")


def test_a_v2_preset_without_a_patch_is_refused():
    assert preset_from_json({"format": 2, "patch": "junk", "layers": []}) is None
    assert preset_from_json({"format": 2}) is None
    assert preset_from_json({"format": 2, "patch": {"cutoff": 500}}) is not None


def test_the_kit_scope_owns_the_drum_samples(tmp_path):
    assert "arr.drum_samples" in RESET_SCOPES["kit"][1]
    s = StudioState(tmp_path)
    s.mark_baseline("kit", "kit")
    s.arrangement = Arrangement(drum_samples={"snare": "vox"})
    assert s.is_dirty("kit") and s.revert("kit")
    assert s.arrangement.drum_samples == {}


@pytest.mark.parametrize(
    "meta",
    [
        {"v": 2, "tags": [], "spectrum": 5},
        {"v": 2, "tags": "x", "spectrum": [0.1]},
        {"v": 2, "tags": [1], "spectrum": [0.1]},
        {"v": 2, "tags": [], "spectrum": ["a"]},
        ["not", "a", "dict"],
    ],
)
def test_malformed_preset_meta_is_recomputed_not_a_500(client, meta):
    s = client.app.state.studio
    s.save_preset("pad")
    s._meta_path("pad").write_text(json.dumps(meta), encoding="utf-8")
    metas = s.list_presets_meta()
    assert metas[0]["name"] == "pad" and isinstance(metas[0]["tags"], list)
    assert client.get("/view/library").status_code == 200


def test_a_held_note_below_the_audible_range_loops_inside_its_buffer(client):
    r = client.get("/note/C-9.wav?hold=1")
    assert r.status_code == 200
    start, end, _gate = map(float, r.headers["X-Loop"].split(","))
    assert 0 <= start < end


def test_a_refused_mic_take_leaves_no_clip_behind(client, monkeypatch):
    from synthlab.studio import limits
    from synthlab.studio.clips import clip_dir

    sr.use_song(client)
    s = client.app.state.studio
    monkeypatch.setattr(limits, "MAX_LANE_CLIPS", 0)
    r = client.post(
        "/song/take",
        data={"section_id": "s1", "track_id": "ta", "at_bar": "0"},
        files={"file": ("take.wav", _take_wav(220), "audio/wav")},
        headers=sr.HX,
    )
    assert r.status_code == 422
    assert not list(clip_dir(s.data_dir).glob("take-*"))


def test_a_section_cannot_end_before_its_take(client):
    sr.use_song(client)
    s = client.app.state.studio
    s.song["sections"][0]["audio"] = [
        {"track_id": "ta", "clip": "vox", "at_bar": 1, "gain": 1.0}
    ]
    r = sr.post(client, "/song/section/s1/bars", {"bars": "1"})
    sr.refused(r, "take")
    assert s.song["sections"][0]["bars"] == 2


def test_switching_the_active_layer_leaves_the_loop_unchanged(tmp_path):
    """Each layer keeps its own velocity lane and the renderer plays an
    inactive layer with *its own* (state.render_layer carries it), so which
    layer is being edited is inaudible. (Curves are the edited lane's only,
    as in a song, so this loop has none.)"""
    import json

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        perform = {"HX-Current-URL": "http://x/view/perform"}
        # layer 0: soft velocities
        s.arrangement.cells = [["A2", 0], ["A2", 4], ["E2", 8]]
        s.arrangement.velocities = {"0": 0.2, "4": 0.3, "8": 0.25}
        c.post("/fragment/layer/select", data={"index": "1"}, headers=perform)
        # layer 1: its own loud notes
        s.arrangement.cells = [["E2", 2], ["A2", 10]]
        s.arrangement.velocities = {"2": 1.0, "10": 1.0}
        s.save()

        def loop() -> bytes:
            r = c.post("/fragment/preview", data={"view": "perform"})
            url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
            return c.get(url).content

        with_1_active = loop()
        c.post("/fragment/layer/select", data={"index": "0"}, headers=perform)
        assert loop() == with_1_active


# Each layer's own sequencing as `select_layer` stashes it: a lane with no
# velocity lane of its own plays 0.9, whatever the other lane's says.
_PLAIN = {"velocities": {}, "slides": [], "arp_mode": "off", "arp_octaves": 1}
_LANES = {
    "arp_on_0": (
        {"velocities": {"0": 0.3, "5": 1.0}, "arp_mode": "up", "arp_octaves": 2},
        {},
    ),
    "slides_on_1": ({}, {"velocities": {"1": 0.4}, "slides": [3, 9]}),
    "both_own_everything": (
        {"velocities": {"4": 0.2}, "slides": [4], "arp_mode": "off"},
        {"velocities": {"3": 1.0, "7": 0.5}, "arp_mode": "updown", "arp_octaves": 3},
    ),
    "velocities_on_0_only": ({"velocities": {"0": 0.2, "3": 0.25, "4": 0.3}}, {}),
    "velocities_on_1_only": ({}, {"velocities": {"1": 1.0, "3": 0.1, "9": 0.2}}),
}


def _loop_audio(s: StudioState) -> np.ndarray:
    from synthlab.studio.render import RenderInputs, render_audio

    return render_audio(
        RenderInputs(
            patch=s.patch,
            arr=s.arrangement,
            layers=s.other_layers(),
            main_fx=s.layers[s.active_layer].get("fx") or [],
            parts=("synth",),
        )
    )


@pytest.mark.parametrize(("seq0", "seq1"), _LANES.values(), ids=_LANES.keys())
def test_switching_the_active_layer_never_changes_the_loop(tmp_path, seq0, seq1):
    """Every lane plays its own velocities, slides and arp whether it is
    being edited or not (`render_layer` -> `song_bridge.lane_render` ->
    `arrange._lane_timeline`), so which slot is active is inaudible — with
    an arp, slides or a velocity lane on either layer."""
    s = StudioState(tmp_path)
    s.patch = Patch(name="bass", osc1="saw", release=0.05, glide=0.0)
    s.arrangement = Arrangement(
        cells=[["A2", 0], ["C3", 3], ["C3", 4], ["E3", 5], ["A2", 10]],
        **(_PLAIN | seq0),
    )
    s.select_layer(1)
    s.patch = Patch(name="lead", osc1="square", release=0.08, cutoff=3000.0)
    s.arrangement = replace(
        s.arrangement,
        cells=[["E3", 1], ["G3", 3], ["A3", 9], ["C4", 12]],
        **(_PLAIN | seq1),
    )
    with_1_active = _loop_audio(s)
    s.select_layer(0)
    with_0_active = _loop_audio(s)
    np.testing.assert_allclose(with_0_active, with_1_active, atol=1e-9)
    # the test means something: inactive, layer 1 plays its own lanes, not
    # the plain per-row render of its cells
    if seq1:
        own = s.layers[1]["seq"]
        s.layers[1]["seq"] = dict(_PLAIN)
        assert not np.allclose(_loop_audio(s), with_0_active, atol=1e-4)
        s.layers[1]["seq"] = own


def test_a_layers_plain_render_keeps_its_key(tmp_path):
    """A layer whose own lanes sound like the live lane's carries nothing
    new (velocities equal at its notes, slides and velocities only where it
    has no notes): its render dict, and so every `RenderInputs.key()`, is
    what it was."""
    s = StudioState(tmp_path)
    s.arrangement = Arrangement(cells=[["A2", 0]], velocities={"0": 0.5})
    s.select_layer(1)
    s.arrangement = replace(
        s.arrangement,
        cells=[["E3", 0], ["E3", 1]],
        velocities={"0": 0.5, "7": 0.1},  # 7: no note there
        slides=[5],  # no note there either
    )
    s.select_layer(0)
    (lay,) = s.other_layers()
    assert set(lay) == {"patch", "cells", "muted", "gain", "fx"}


def test_a_layers_gain_plays_the_same_active_or_not(tmp_path):
    """The active slot plays at its own gain, as it does while inactive."""
    import json

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with TestClient(create_app(data_dir=tmp_path)) as c:
        s = c.app.state.studio
        perform = {"HX-Current-URL": "http://x/view/perform"}
        s.arrangement.cells = [["A2", 0], ["E2", 8]]
        c.post("/fragment/layer/select", data={"index": "1"}, headers=perform)
        s.arrangement.cells = [["E2", 4]]
        s.layers[1]["gain"] = 0.4
        s.save()

        def loop() -> bytes:
            r = c.post("/fragment/preview", data={"view": "perform"})
            url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
            return c.get(url).content

        with_1_active = loop()
        c.post("/fragment/layer/select", data={"index": "0"}, headers=perform)
        assert loop() == with_1_active


# --- "before last load" belongs to the slot it was stashed in --------------------


def test_swap_back_never_lands_on_another_slot(tmp_path):
    """`slot_prev` was one per session: load over slot 1's sound, add a
    layer (slot 2 is edited), press "Before last load" — slot 2 took slot
    1's old sound and slot 1 kept the loaded one."""
    s = StudioState(tmp_path)
    s.patch = replace(s.patch, name="orig0")
    s.load_sound(Patch(name="loaded"))
    assert s.slot_prev.name == "orig0"
    assert s.add_layer(Patch(name="layer")) == 1 and s.active_layer == 1
    assert s.slot_prev is None and not s.swap_prev()  # nothing loaded here
    assert s.patch.name == "layer"
    assert Patch.from_dict(s.layers[0]["patch"]).name == "loaded"
    s.select_layer(0)
    assert s.slot_prev.name == "orig0" and s.swap_prev()
    assert s.patch.name == "orig0" and s.slot_prev.name == "loaded"


def test_a_stashed_stack_is_swapped_back_only_on_its_own_slot(tmp_path):
    """A Sound loaded with its layers stashes the lanes, main FX and curves
    it replaced. Swapped back from another slot, the old lanes overwrote
    every slot but that one and the old main FX landed on it."""
    s = StudioState(tmp_path)
    fx = [{"kind": "drive", "params": {"amount": 2.0, "mix": 1.0}}]
    chain = {"layers": [], "main_fx": fx, "curves": {}}
    s.patch = replace(s.patch, name="orig0")
    s.load_sound(Patch(name="stacked"), chain)
    assert s.stack_prev is not None
    s.select_layer(1)
    assert s.stack_prev is None and not s.swap_prev()
    assert s.layers[1].get("fx") in ([], None)
    assert Patch.from_dict(s.layers[0]["patch"]).name == "stacked"
    s.select_layer(0)
    assert s.swap_prev() and s.patch.name == "orig0"
    assert s.layers[0]["fx"] == [] and s.stack_prev["main_fx"] == fx


def _stacked_session_with_a_track(tmp_path):
    """A loop whose "before last load" holds a whole stack (patch + FX),
    and a song with one synth track to edit."""
    s = StudioState(tmp_path)
    s.arrangement = replace(s.arrangement, cells=[["A1", 0]])
    s.loop_to_section("Verse")
    track = next(t["id"] for t in s.song["tracks"] if t["kind"] == "synth")
    fx = [{"kind": "drive", "params": {"amount": 2.0, "mix": 1.0}}]
    s.patch = replace(s.patch, name="orig0")
    s.layers[0]["fx"] = [{"kind": "reverb", "params": {"mix": 0.3}}]
    s.load_sound(Patch(name="stacked"), {"layers": [], "main_fx": fx, "curves": {}})
    assert s.slot_prev.name == "orig0" and s.stack_prev is not None
    return s, track, fx


def test_a_track_edit_gives_the_whole_before_last_load_stash_back(tmp_path):
    """`edit_track` set `slot_prev` aside and left `stack_prev` live: a
    patch-only load on the track dropped it (`stash_prev`), so after the
    edit "Before last load" restored the patch without the FX and lanes it
    had replaced."""
    s, track, fx = _stacked_session_with_a_track(tmp_path)
    stack = s.stack_prev
    assert s.edit_track(track)
    assert s.slot_prev is None and s.stack_prev is None and not s.swap_prev()
    # mid-edit the file still holds the loop's stash, both halves (as a
    # load would hold it: the hand-built reverb with its parameters)
    node = schema.nodes().PAYLOAD.fields["stack_prev"]
    assert s._payload()["stack_prev"] == schema.conform(stack, node)
    assert s._live_payload()["stack_prev"] is stack
    assert s._payload()["slot_prev"]["name"] == "orig0"
    s.load_sound(Patch(name="on the track"))  # patch-only: stash_prev
    assert s.slot_prev is not None and s.stack_prev is None
    assert s.end_track_edit()
    assert s.slot_prev.name == "orig0" and s.stack_prev is stack
    assert s.swap_prev() and s.patch.name == "orig0"
    assert [u["kind"] for u in s.layers[0]["fx"]] == ["reverb"]
    assert s.stack_prev["main_fx"] == fx


def test_a_chain_load_on_a_track_never_pairs_its_stack_with_the_loops_patch(
    tmp_path,
):
    """A whole Sound loaded during the edit stashed the *track edit's*
    stack; the edit's end gave back the loop's `slot_prev` beside it."""
    s, track, fx = _stacked_session_with_a_track(tmp_path)
    stack = s.stack_prev
    assert s.edit_track(track)
    other = [{"kind": "delay", "params": {"mix": 0.2}}]
    s.load_sound(Patch(name="x"), {"layers": None, "main_fx": other, "curves": {}})
    assert s.stack_prev is not None and s.stack_prev is not stack
    assert s.end_track_edit()
    assert s.slot_prev.name == "orig0" and s.stack_prev is stack


def test_the_swap_back_row_shows_only_on_the_slot_that_loaded(client):
    s = client.app.state.studio
    s.load_sound(Patch(name="loaded"))
    assert "/fragment/restore_prev" in client.get("/").text
    s.add_layer(Patch(name="layer"))
    assert "/fragment/restore_prev" not in client.get("/").text
    r = client.post("/fragment/restore_prev", headers=EDITOR)
    assert r.status_code == 422 and s.patch.name == "layer"
