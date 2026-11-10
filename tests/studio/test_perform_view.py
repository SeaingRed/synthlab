import copy
import json
import re
from dataclasses import asdict, replace
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.drumfactory import DRUM_FACTORY
from synthlab.studio.drumlab import specs_of
from synthlab.studio.server import create_app
from synthlab.studio.state import StudioState
from synthlab.studio.state.model import grid_rows

PERFORM = Path("synthlab/studio/static/perform.js")
AUDIO = Path("synthlab/studio/static/audio.js")


HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _code_lines(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith(("//", "*", "/*"))
    ]


def test_perform_view_renders_pads_xy_and_launcher(tmp_path):
    r = make_client(tmp_path).get("/view/perform")
    assert r.status_code == 200
    assert r.text.count('class="hit-pad pad"') == 8
    assert 'id="drumxy"' in r.text
    assert 'id="play"' in r.text
    # the song strip and clip placer are the Song page's
    assert 'id="song-strip"' not in r.text and 'id="clip-lane"' not in r.text
    assert 'href="/view/song"' in r.text and "Arrange ▸" in r.text


def test_nav_contains_perform(tmp_path):
    html = make_client(tmp_path).get("/").text
    # (the page is named "Loops"; its route stays /view/perform)
    assert ">Loops<" in html and 'href="/view/perform"' in html


def test_pad_voices_all_serve_a_hit(tmp_path):
    client = make_client(tmp_path)
    for voice in ("kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash"):
        r = client.get(f"/drum/{voice}/hit.wav")
        assert r.status_code == 200, voice
        assert r.headers["content-type"] == "audio/wav"
    assert client.get("/drum/cowbell/hit.wav").status_code == 404


def test_perform_xy_maps_to_hat_decay_and_kick_punch(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/perform/xy", data={"x": "1", "y": "0"})
    assert r.status_code == 200
    assert "preview-ready" in r.headers["HX-Trigger"]
    s: StudioState = client.app.state.studio
    hat_decay = next(sp for sp in specs_of("hat") if sp["name"] == "decay")
    kick_punch = next(sp for sp in specs_of("kick") if sp["name"] == "punch")
    assert s.arrangement.drum_params["hat"]["decay"] == hat_decay["max"]
    assert s.arrangement.drum_params["kick"]["punch"] == kick_punch["min"]
    # out-of-range clamps, bad input falls back to 0
    client.post("/fragment/perform/xy", data={"x": "9", "y": "abc"})
    assert s.arrangement.drum_params["hat"]["decay"] == hat_decay["min"]


def _pool_beat(s: StudioState, slot: int) -> str:
    from synthlab.studio import song_model as sm

    p = DRUM_FACTORY[slot]
    return sm.add_pattern(
        s.song,
        "drums",
        p["name"],
        {"bars": p["bars"], "steps": p["steps_per_bar"], "drums": p["steps"]},
    )


def _queued(r) -> None:
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["preview-ready"]["queue"] is True
    assert trig["preview-ready"]["url"].startswith("/preview/")


def test_beats_launcher_lists_the_pool_and_launch_queues(tmp_path):
    """The Beats
    launcher is the song pool's drum patterns; a launch puts one on the
    working loop's drum lane and queues at the wrap as before."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    ids = [_pool_beat(s, i) for i in range(3)]
    html = client.get("/view/perform").text
    for pid in ids:
        assert f'hx-post="/fragment/perform/launch/{pid}"' in html
    r = client.post(f"/fragment/perform/launch/{ids[2]}", headers=HX)
    _queued(r)
    assert "reset-menu" in r.text
    assert sorted(map(list, s.arrangement.drums)) == sorted(
        map(list, DRUM_FACTORY[2]["steps"])
    )
    assert client.post("/fragment/perform/launch/nope").status_code == 404
    html = client.get("/view/perform").text
    playing = re.search(r'data-pattern-id="([^"]+)"\s+data-state="playing"', html)
    assert playing and playing.group(1) == ids[2]


def _launcher(html: str) -> str:
    return html.split('id="launcher"', 1)[1].split("</section>", 1)[0]


def test_empty_pool_launcher_offers_the_bank_and_loads_a_slot_at_the_wrap(tmp_path):
    """A fresh studio's pool is empty, so the Beats launcher would be an
    empty box beside a bank of factory beats. With no drums
    pattern in the pool it shows the bank's filled slots ("from the
    bank"), and a launch loads the slot as the Drums bank does, queued."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    assert not s.song["patterns"]
    s.drum_bank[1] = None  # an empty slot is not offered
    start = sorted(map(list, s.arrangement.drums))
    launcher = _launcher(client.get("/view/perform").text)
    assert "from the bank" in launcher and "pool</small>" not in launcher
    offered = re.findall(r'hx-post="/fragment/perform/bank/(\d+)"', launcher)
    assert offered == [str(i) for i, p in enumerate(s.drum_bank) if p]
    assert "1" not in offered
    assert "/fragment/perform/launch/" not in launcher

    r = client.post("/fragment/perform/bank/2", headers=HX)
    _queued(r)
    assert s.active_slot == 2
    assert sorted(map(list, s.arrangement.drums)) == sorted(
        map(list, s.drum_bank[2]["steps"])
    )
    assert 'id="launcher"' not in r.text  # perform.js is showing "queued"
    playing = re.search(
        r'data-bank-slot="(\d+)"\s+data-state="playing"',
        client.get("/view/perform").text,
    )
    assert playing and playing.group(1) == "2"
    assert client.post("/fragment/perform/bank/1").status_code == 404
    assert client.post("/fragment/perform/bank/99").status_code == 404
    # Ctrl+Z on Perform takes the launch back, like a pool beat's
    assert client.post("/fragment/pattern/undo", headers=HX).status_code == 200
    assert sorted(map(list, s.arrangement.drums)) == start


def test_a_pool_beat_replaces_the_bank_in_the_launcher(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    pid = _pool_beat(s, 0)
    launcher = _launcher(client.get("/view/perform").text)
    assert "pool</small>" in launcher and "from the bank" not in launcher
    assert re.findall(r'hx-post="(/fragment/perform/[^"]+)"', launcher) == [
        f"/fragment/perform/launch/{pid}"
    ]


def test_launcher_watch_follows_the_bank_only_while_it_is_shown(tmp_path):
    """The region's watch reads exactly what it draws: a bank rename moves
    it while the pool is empty, and not once the pool holds a beat."""
    from synthlab.studio.service import _w_launcher

    client = make_client(tmp_path)
    svc = client.app.state.svc
    s: StudioState = client.app.state.studio
    before = _w_launcher(svc, "perform")
    s.drum_bank[0] = {**s.drum_bank[0], "name": "Renamed"}
    assert _w_launcher(svc, "perform") != before
    _pool_beat(s, 3)
    pooled = _w_launcher(svc, "perform")
    s.drum_bank[0] = {**s.drum_bank[0], "name": "Again"}
    s.drum_bank[4] = None
    assert _w_launcher(svc, "perform") == pooled


def test_the_sections_open_from_the_loops_panel_as_a_scene_not_a_launcher(tmp_path):
    """Perform has no Sections launcher (it replaced the working loop with
    a section) — the Loops panel's "sections" kind
    lists the song's sections, and Open lays one out as a scene of the
    launcher (tests/studio/test_slots.py). The old route is gone."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(s.arrangement, cells=[["A1", 0]], drums=[["kick", 0]])
    sid = s.loop_to_section("intro")
    html = client.get("/view/perform").text
    assert (
        'id="scene-launcher"' not in html and "/fragment/perform/section/" not in html
    )
    assert 'data-lp-kind="section"' in html and 'data-what="section"' in html
    assert f'"section": "{sid}"' in html.replace("&#34;", '"')
    assert 'hx-post="/fragment/perform/scene/section"' in html
    assert client.post(f"/fragment/perform/section/{sid}").status_code in (404, 405)


def _loop_now(s: StudioState) -> dict:
    """What a section launch replaces, as plain values to compare."""
    return {
        "arr": asdict(s.arrangement),
        "patch": asdict(s.patch),
        "curves": copy.deepcopy(s.curves),
        "layers": copy.deepcopy(s.layers),
        "fx_drums": copy.deepcopy(s.fx_drums),
        "vars": (copy.deepcopy(s.beat_vars), s.beat_var),
        "editing": sorted(e["name"] for e in s.loop_edit.values()),
        "sound_base": copy.deepcopy(s.baselines.get("sound")),
    }


def test_a_section_launch_is_one_undo_step_that_puts_the_whole_loop_back(tmp_path):
    """A section launched on Perform replaces the loop whole — notes,
    beat, lanes and their sounds, tempo, key, meter — so Ctrl+Z must not
    say "Nothing to undo in the pattern" (`section_to_loop` must not empty
    the ring). The launch is ONE pattern undo step holding
    the whole loop, and the steps under it are still there."""
    from synthlab.studio import loops

    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(s.arrangement, cells=[["A1", 0]], drums=[["kick", 0]])
    sid = s.loop_to_section("intro")
    # the loop moves on: a saved loop open on the grid, in 3/4 at 97 bpm,
    # another sound, a second lane, a variation of the beat
    lane = replace(s.arrangement, bpm=97.0, cells=[["C3", 2], ["E3", 5]])
    loops.save(s.data_dir, "hook", lane, s.sound_payload())
    r = client.post("/loop/edit", data={"name": "hook"}, headers=HX)
    assert r.status_code == 200
    s.set_meter("3/4")
    s.patch = s.patch.tweak(cutoff=999.0)
    s.layers[1] = {**s.layers[1], "cells": [["G3", 1]], "gain": 0.5}
    s.beat_vars = {"B": s._var_lane()}  # a variation of the beat as it is
    s.arrangement = replace(s.arrangement, drums=[["hat", 1]], swing=0.2)
    was, ring = _loop_now(s), len(s._history)

    # (Song's "Open in Perform": Perform has no Sections launcher)
    assert client.post(f"/song/section/{sid}/to-loop", headers=HX).status_code == 200
    launched = _loop_now(s)
    assert launched["arr"]["bpm"] != 97.0 and launched["arr"]["meter"] == "4/4"
    assert launched["patch"] != was["patch"] and launched["editing"] == []
    assert launched["layers"] != was["layers"] and launched["vars"][0] == {}

    r = client.post("/fragment/pattern/undo", headers=HX)
    assert r.status_code == 200, r.text
    assert _loop_now(s) == was
    assert len(s._history) == ring  # the outgoing sound's step went with it
    # the ring under it is intact: the step that opened the saved loop is next
    assert client.post("/fragment/pattern/undo", headers=HX).status_code == 200
    assert not s.loop_edit


def test_a_section_launch_moves_the_rolls_window_to_its_notes(tmp_path):
    """Any load onto the grid shows its notes (Edit always did): a section
    whose notes are two octaves up opens with the window on them, not on
    "48 hidden notes"; and Ctrl+Z brings the window back to the loop's."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(
        s.arrangement, root="A1", cells=[["A3", 0], ["C4", 4], ["E4", 8]]
    )
    sid = s.loop_to_section("up high")
    s.arrangement = replace(s.arrangement, cells=[["A1", 0]])
    s.grid_span, s.grid_oct = 1, 0
    assert client.post(f"/song/section/{sid}/to-loop", headers=HX).status_code == 200
    rows = set(grid_rows(s.arrangement, s.grid_span, s.grid_oct))
    assert {"A3", "C4", "E4"} <= rows, (s.grid_span, s.grid_oct)
    client.post("/fragment/pattern/undo", headers=HX)
    assert "A1" in grid_rows(s.arrangement, s.grid_span, s.grid_oct)


def test_retired_perform_routes_are_gone(tmp_path):
    client = make_client(tmp_path)
    for path in (
        "/fragment/song/capture",
        "/fragment/song/load",
        "/fragment/song/repeats",
        "/fragment/song/remove",
        "/fragment/perform/clip/add",
        "/fragment/perform/clip/remove",
        "/fragment/perform/scene/0",
    ):
        assert client.post(path).status_code in (404, 405), path


def test_perform_island_within_budget(tmp_path):
    r = make_client(tmp_path).get("/static/perform.js")
    assert r.status_code == 200
    assert len(_code_lines(PERFORM)) <= 150
    assert "loop-switched" in PERFORM.read_text(encoding="utf-8")


def test_audio_island_has_queued_switching():
    js = AUDIO.read_text(encoding="utf-8")
    assert "queueTo" in js and "loop-switched" in js and "detail.queue" in js


def test_piano_qwerty_yields_to_perform_pads():
    # Q W E R / A S D F are the pads on the Perform view while the keyboard
    # dock's typing → pads says so (by default typing plays the
    # dock's keys there, as on every page); piano.js must not also fire
    # synth notes for them then, and perform.js plays them only then.
    js = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert 'typeTo === "pads" && !!document.getElementById("perform-pads")' in js
    assert "!padsTyping()" in js.split("const typing = ")[1].split(";")[0]
    perform = Path("synthlab/studio/static/perform.js").read_text(encoding="utf-8")
    assert perform.count("!window.synthlab.padsTyping()") == 2  # the pads, the FX keys


PERFORM_HTML = Path("synthlab/studio/templates/views/perform.html")
AUDIO_JS = Path("synthlab/studio/static/audio.js")
PERFORM_JS = Path("synthlab/studio/static/perform.js")
PIANO_JS = Path("synthlab/studio/static/piano.js")


def test_performance_fx_strip_renders(tmp_path):
    html = TestClient(create_app(data_dir=tmp_path)).get("/view/perform").text
    assert 'class="perf-fx module"' in html
    for fx in ("tape", "half", "double", "reverse", "stutter"):
        assert f'data-fx="{fx}"' in html, fx
    # three stutter rates, each with its own beat length
    assert html.count('data-fx="stutter"') == 3
    for beats in ("1", "0.5", "0.25"):
        assert f'data-beats="{beats}"' in html, beats


def test_performance_fx_keys_collide_with_nothing_else_on_perform():
    """The near-miss worth pinning: piano.js binds z/x for octave *before*
    its perform-pads guard, so a strip key of z would have double-fired.
    Every FX key must avoid piano.js's KEYMAP, its octave pair, and
    perform.js's own pad keys."""
    import re

    html = PERFORM_HTML.read_text(encoding="utf-8")
    fx_keys = set(re.findall(r'data-key="([a-z])"', html))
    assert len(fx_keys) == 7, fx_keys

    piano = PIANO_JS.read_text(encoding="utf-8")
    perform = PERFORM_JS.read_text(encoding="utf-8")
    pairs = r"(\w): \d+"
    keymap = set(re.findall(pairs, piano[piano.index("const KEYMAP") :].split("}")[0]))
    octave = set(re.findall(r'key === "(\w)"', piano))
    pads = set(re.findall(pairs, perform[perform.index("const KEYS") :].split("}")[0]))

    for claimed, who in ((octave, "piano octave"), (pads, "perform pads")):
        clash = fx_keys & claimed
        assert not clash, f"FX keys {clash} already claimed by {who}"
    # the FX keys are typing → pads' (perform.js), and there the dock's
    # typing stands down whole — piano.js's `typing` gate, which its
    # keydown asks before z / x and any KEYMAP letter. So since the classic
    # split runs on past J (K O L P ; '), O and P are both an FX
    # key and a KEYMAP letter, and only one of the two ever fires — the
    # gate is what is pinned
    guard = "if (e.repeat || !typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;"
    assert guard in piano
    assert piano.index(guard) < piano.index('if (key === "z")')
    assert fx_keys & keymap <= {"o", "p"}, fx_keys & keymap


def test_loop_fx_lives_in_the_island_that_owns_the_buffer():
    """audio.js owns the AudioBufferSourceNode, so the audio surface belongs
    there; perform.js owns the gestures and holds no audio state."""
    audio = AUDIO_JS.read_text(encoding="utf-8")
    assert "loopFx" in audio
    for op in ("playbackRate", "loopStart", "loopEnd"):
        assert op in audio, op
    perform = PERFORM_JS.read_text(encoding="utf-8")
    assert "window.synthlab.loopFx" in perform
    # perform.js must not reach for the audio graph itself
    for forbidden in ("createBufferSource", "playbackRate", "AudioContext"):
        assert forbidden not in perform, forbidden


def test_held_fx_are_released_on_swap_and_blur():
    """A held pad must not survive the view being swapped out from under it,
    or the loop stays bent with nothing showing as pressed. But after:swap
    fires for every swap:"none" request too (dragging the drum XY, launching
    a beat), so it releases only when a held pad was actually replaced."""
    perform = PERFORM_JS.read_text(encoding="utf-8")
    assert "allFxOff" in perform
    swap = perform.split('addEventListener("htmx:after:swap", () =>')[-1].split("\n")[0]
    assert "isConnected" in swap and "allFxOff" in swap
    assert 'addEventListener("htmx:after:swap", allFxOff)' not in perform
    assert 'addEventListener("blur", allFxOff)' in perform
    assert "pointercancel" in perform


def test_perform_carries_the_typing_switch(tmp_path):
    """Typing switched off on Synths also silences Perform's pads and FX keys
    (window.synthlab.keysOn), so Perform needs the switch to turn it back on:
    the keyboard dock's (every page's), with typing → keys |
    pads beside it — one switch, not a second copy in Perform's head."""
    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    html = TestClient(create_app(data_dir=tmp_path)).get("/view/perform").text
    assert html.count('id="keys-toggle"') == 1 and 'id="kbd-typeto"' in html
