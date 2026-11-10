"""The drum machine: its own clock, 8 voices, mixer, choke, humanise,
per-row tools, a drum FX bus — and the Perform view's launchers."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import beatgen
from synthlab.studio.arrange import build_song, loop_bars, loop_seconds
from synthlab.studio.render import RenderInputs, render_audio
from synthlab.studio.server import create_app
from synthlab.studio.state import DRUM_ROWS, Arrangement, StudioState
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://x/view/{view}"}


def _drums_only(arr: Arrangement) -> np.ndarray:
    return render_audio(RenderInputs(patch=Patch(), arr=arr, parts=("drums",)))


def test_eight_voices_and_own_clock():
    assert DRUM_ROWS == ["kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash"]
    arr = Arrangement(bars=1, steps_per_bar=4, drum_bars=2, drum_steps=16)
    assert loop_bars(arr, ("synth",)) == 1
    assert loop_bars(arr, ("drums",)) == 2
    assert loop_bars(arr, ("synth", "drums")) == 2
    arr = replace(arr, bars=3)
    assert loop_bars(arr) == 6  # lcm: whole repeats of both
    assert loop_seconds(arr, ("drums",)) == 2 * 4 * 60 / arr.bpm


def test_every_voice_renders_on_its_own_step_grid():
    arr = Arrangement(
        drum_bars=1, drum_steps=8, drums=[[v, i] for i, v in enumerate(DRUM_ROWS)]
    )
    audio = _drums_only(arr)
    assert audio.shape[0] == round(loop_seconds(arr, ("drums",)) * SR)
    step = audio.shape[0] // 8
    for i in range(8):  # each step carries its voice's hit
        assert np.abs(audio[i * step : i * step + step // 2]).max() > 0.01, DRUM_ROWS[i]


def test_mute_solo_level_and_probability():
    base = Arrangement(drum_bars=1, drum_steps=4, drums=[["kick", 0], ["snare", 2]])
    song = build_song(Patch(), replace(base, cells=[]), None)
    assert {"drum_kick", "drum_snare"} <= set(song.tracks)
    muted = replace(base, drum_mix={"snare": {"mute": True}})
    assert (
        "drum_snare" not in build_song(Patch(), replace(muted, cells=[]), None).tracks
    )
    solo = replace(base, drum_mix={"kick": {"solo": True}})
    assert set(build_song(Patch(), replace(solo, cells=[]), None).tracks) == {
        "drum_kick"
    }
    never = replace(base, drum_mix={"snare": {"prob": 0.0}})
    t = build_song(Patch(), replace(never, cells=[]), None).tracks["drum_snare"]
    assert not t.events
    half = replace(base, drum_mix={"kick": {"level": 0.5}})
    loud = _drums_only(replace(base, drums=[["kick", 0]]))
    quiet = _drums_only(replace(half, drums=[["kick", 0]]))
    # the final normalisation lifts a lone quiet hit back up — compare the
    # scheduled gains instead
    g_full = (
        build_song(Patch(), replace(base, cells=[]), None)
        .tracks["drum_kick"]
        .events[0][2]
    )
    g_half = (
        build_song(Patch(), replace(half, cells=[]), None)
        .tracks["drum_kick"]
        .events[0][2]
    )
    assert g_half == 0.5 * g_full
    assert loud.shape == quiet.shape


def test_velocity_lane_and_humanise_are_deterministic():
    arr = Arrangement(drum_bars=1, drum_steps=4, drums=[["hat", 0], ["hat", 1]])
    v = replace(arr, drum_vel={"1": 0.25})
    ev = build_song(Patch(), replace(v, cells=[]), None).tracks["drum_hat"].events
    gains = sorted(e[2] for e in ev)
    assert gains[0] == 0.25 * gains[1]
    h = replace(arr, drum_humanize=0.8)
    a = _drums_only(h)
    b = _drums_only(h)
    assert np.array_equal(a, b)  # seeded: the loop does not wobble per render
    assert not np.array_equal(a, _drums_only(arr))  # but it is not the straight take


def test_open_hat_is_choked_by_the_next_closed_hat():
    long_open = {"ohat": {"decay": 0.8}}
    free = Arrangement(
        drum_bars=1, drum_steps=4, drums=[["ohat", 0]], drum_params=long_open
    )
    choked = replace(
        free,
        drums=[["ohat", 0], ["hat", 1]],
        drum_params={**long_open, "hat": {"decay": 0.02}},
    )
    e_free = (
        build_song(Patch(), replace(free, cells=[]), None).tracks["drum_ohat"].events[0]
    )
    e_choked = (
        build_song(Patch(), replace(choked, cells=[]), None)
        .tracks["drum_ohat"]
        .events[0]
    )
    assert len(e_choked[1]) < len(e_free[1])
    # cut exactly at the closed hat's step (one step = a beat here)
    assert abs(len(e_choked[1]) - round(60 / free.bpm * SR)) <= 2


def test_per_row_generation_touches_only_that_row():
    cells = beatgen.generate_row("tom", "techno", 3, 1, 16)
    assert cells and all(v == "tom" for v, _ in cells)
    assert beatgen.generate_row("crash", "techno", 3, 2, 16) == [
        ["crash", 0],
        ["crash", 16],
    ]
    assert beatgen.generate_row("kick", "four-floor", 1, 1, 16)[:4] == [
        ["kick", 0],
        ["kick", 4],
        ["kick", 8],
        ["kick", 12],
    ]
    grid = [["kick", 0], ["snare", 4]]
    out = beatgen.mutate(grid, 5, 1, 16, rate=0.5, voice="hat")
    assert [c for c in out if c[0] != "hat"] == grid


def test_drum_fx_bus_is_part_of_the_render_and_its_key():
    arr = Arrangement(drum_bars=1, drum_steps=4, drums=[["kick", 0]])
    dry = render_audio(RenderInputs(patch=Patch(), arr=arr, parts=("drums",)))
    wet = render_audio(
        RenderInputs(
            patch=Patch(),
            arr=arr,
            parts=("drums",),
            drums_fx=[{"kind": "drive", "params": {}}],
        )
    )
    assert dry.shape == wet.shape and not np.allclose(dry, wet)
    assert (
        RenderInputs(patch=Patch(), arr=arr, parts=("drums",)).key()
        != RenderInputs(
            patch=Patch(),
            arr=arr,
            parts=("drums",),
            drums_fx=[{"kind": "drive", "params": {}}],
        ).key()
    )
    # the synth-only key never depends on the drum bus
    assert (
        RenderInputs(patch=Patch(), arr=arr, parts=("synth",)).key()
        == RenderInputs(
            patch=Patch(),
            arr=arr,
            parts=("synth",),
            drums_fx=[{"kind": "drive", "params": {}}],
        ).key()
    )


def test_drums_page_has_the_whole_machine(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/drums").text
    assert html.count('class="gridrow drumrow') == 8
    assert 'name="humanize"' in html and 'name="steps"' in html
    assert html.count('class="btn btn-s btn-icon rowbtn mute"') == 8
    assert 'data-step="0"' in html and "vel-lane" in html
    assert "Drum bus FX" in html and "Master FX" not in html
    assert html.count('name="kick.level"') == 1 and 'name="crash.prob"' in html
    # row tools
    r = client.post("/fragment/drums/row/tom/randomise", headers=_hx("drums"))
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert any(v == "tom" for v, _ in s.arrangement.drums)
    r = client.post("/fragment/drums/row/tom/clear", headers=_hx("drums"))
    assert not any(v == "tom" for v, _ in s.arrangement.drums)
    client.post("/fragment/drums/row/kick/solo", headers=_hx("drums"))
    assert s.arrangement.drum_mix["kick"]["solo"] is True
    assert (
        client.post("/fragment/drums/row/nope/mute", headers=_hx("drums")).status_code
        == 404
    )
    # the drum clock is its own: bars/steps/swing/humanise
    r = client.post(
        "/fragment/drums/transport",
        data={"bars": "2", "steps": "8", "humanize": "0.3"},
        headers=_hx("drums"),
    )
    assert r.status_code == 200 and 'id="drum-bank"' in r.text
    assert (s.arrangement.drum_bars, s.arrangement.drum_steps) == (2, 8)
    assert s.arrangement.drum_humanize == 0.3
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) != (2, 8)
    # mixer + velocity lane through their forms
    client.post(
        "/fragment/drums",
        data={"kick.level": "0.7", "kick.prob": "0.5"},
        headers=_hx("drums"),
    )
    assert s.arrangement.drum_mix["kick"]["level"] == 0.7
    client.post(
        "/fragment/drums/grid",
        data={"drum.kick.0": "on", "dvel.0": "0.4"},
        headers=_hx("drums"),
    )
    assert s.arrangement.drum_vel["0"] == 0.4
    # the synth transport never disturbs any of it
    client.post(
        "/fragment/arrangement",
        data={"bpm": "120", "bars": "1", "steps": "4"},
        headers=_hx("editor"),
    )
    assert (
        s.arrangement.drum_bars,
        s.arrangement.drum_steps,
        s.arrangement.drum_humanize,
    ) == (2, 8, 0.3)
    assert s.arrangement.drum_mix["kick"]["level"] == 0.7
    # drum FX scope
    r = client.post(
        "/fragment/fx/add",
        data={"scope": "drums", "kind": "drive"},
        headers=_hx("drums"),
    )
    assert r.status_code == 200 and s.fx_drums and s.fx_drums[0]["kind"] == "drive"


def test_perform_parts_sounds_and_scenes(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.save_preset("My Lead")
    sid = s.loop_to_section("intro")
    html = client.get("/view/perform").text
    assert 'class="part-btn"' in html and 'class="launch sound-launch"' not in html
    assert '<option value="preset:My Lead">My Lead</option>' in html  # the menu
    # a section opens from the Loops panel as a scene (routes/slots.py)
    assert 'hx-post="/fragment/perform/scene/section"' in html
    r = client.post(
        "/fragment/perform/parts", data={"part": "synth"}, headers=_hx("perform")
    )
    assert r.status_code == 200 and s.perform_parts == ["drums"]
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True
    client.post(
        "/fragment/perform/parts", data={"part": "drums"}, headers=_hx("perform")
    )
    assert s.perform_parts == ["drums"]  # never empty
    lane = {"index": "0", "sound": "preset:My Lead"}
    r = client.post("/fragment/perform/lane/sound", data=lane, headers=_hx("perform"))
    assert r.status_code == 200 and s.patch.name == "My Lead"
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True
    gone = {"index": "0", "sound": "preset:nope"}
    r = client.post("/fragment/perform/lane/sound", data=gone, headers=_hx("perform"))
    assert r.status_code == 422 and "gone" in r.text
    r = client.post(
        "/fragment/perform/scene/section", data={"section": sid}, headers=_hx("perform")
    )
    assert r.status_code == 200
    gone = {"section": "nope"}
    r = client.post(
        "/fragment/perform/scene/section", data=gone, headers=_hx("perform")
    )
    assert r.status_code == 422 and "No such section" in r.text


def test_bank_loads_queue_at_the_loop_wrap(tmp_path):
    """Switching beats must land on the bar: the drum bank's slot and
    library loads carry queue=True like Perform's launcher does."""
    client = make_client(tmp_path)
    r = client.post("/fragment/drums/bank/load/1", headers=_hx("drums"))
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True


def test_velocity_lane_marks_steps_with_no_hit(tmp_path):
    """A full-height velocity bar under a silent step is noise. Lanes whose
    step has no drum hit carry `idle` so they can be dimmed."""
    client = TestClient(create_app(data_dir=tmp_path))
    html = client.get("/view/drums").text
    assert 'class="vel-lane idle" data-step="0"' in html  # empty kit: all idle

    # put a kick on step 0 only, via the mini-notation the Drums view uses
    r = client.post("/fragment/drums/notation", data={"notation": "bd ~ ~ ~"})
    assert r.status_code == 200, r.text[:200]
    html = client.get("/view/drums").text
    assert 'class="vel-lane" data-step="0"' in html  # step 0 now fires
    assert 'class="vel-lane idle" data-step="1"' in html  # step 1 still silent


@pytest.fixture
def short_cap(monkeypatch):
    """The loop cap as it was (10 s). At the real one every tempo and bar
    count the controls offer plays, so the refusal needs a smaller cap."""
    from synthlab.studio import render

    monkeypatch.setattr(render, "LOOP_CAP_SECONDS", 10.0)


def test_every_tempo_and_bar_count_the_controls_offer_plays(tmp_path):
    """The cap used to be 10 s: 4 bars would not play under 96 bpm, so a
    beat read from a slow song could not be heard at its own tempo."""
    from synthlab.studio import limits, render

    longest = Arrangement(bpm=limits.BPM_RANGE[0], bars=3, drum_bars=4)
    assert loop_bars(longest) == 12
    assert loop_seconds(longest) <= render.LOOP_CAP_SECONDS
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=200.0, drum_bars=4)
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "74"}, headers=_hx("drums")
    )
    assert r.status_code == 200 and state.arrangement.bpm == 74.0
    assert "preview-ready" in r.headers["HX-Trigger"]


def test_a_tempo_the_loop_cannot_play_is_refused_and_changes_nothing(
    tmp_path, short_cap
):
    """4 drum bars at 80 bpm are 12 s, past a 10 s loop cap. The transport
    used to store the tempo first and then fail to render it: the page
    showed 80, the old loop kept playing at the old tempo, and every later
    edit answered 422 until the tempo was raised again."""
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=200.0, drum_bars=4)
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "80"}, headers=_hx("drums")
    )
    assert r.status_code == 422
    assert "96 bpm" in r.text  # the slowest tempo 4 bars can play
    assert state.arrangement.bpm == 200.0
    # the session is not stuck: the next edit renders
    r = client.post(
        "/fragment/drums/transport", data={"swing": "0.2"}, headers=_hx("drums")
    )
    assert r.status_code == 200 and state.arrangement.drum_swing == 0.2


def test_more_bars_than_the_tempo_can_play_are_refused(tmp_path, short_cap):
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=80.0, drum_bars=2)
    r = client.post(
        "/fragment/drums/transport", data={"bars": "4"}, headers=_hx("drums")
    )
    assert r.status_code == 422 and state.arrangement.drum_bars == 2


def test_a_session_already_past_the_cap_can_still_be_brought_back(tmp_path, short_cap):
    """Refusal is for a change that makes the loop longer: one that
    shortens an over-long loop, or leaves its length alone, goes through."""
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=60.0, drum_bars=4)
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "70"}, headers=_hx("drums")
    )
    assert state.arrangement.bpm == 70.0  # still too long to render, but closer
    r = client.post(
        "/fragment/drums/transport", data={"bars": "2"}, headers=_hx("drums")
    )
    assert r.status_code == 200 and state.arrangement.drum_bars == 2


def test_a_tempo_that_is_not_a_number_is_ignored(tmp_path):
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    before = state.arrangement.bpm
    r = client.post(
        "/fragment/drums/transport", data={"bpm": "nan"}, headers=_hx("drums")
    )
    assert r.status_code == 200 and state.arrangement.bpm == before


@pytest.mark.parametrize("path", ["/fragment/arrangement", "/fragment/patch"])
def test_the_synths_tempo_box_refuses_before_it_stores_too(tmp_path, short_cap, path):
    """The same store-then-fail the drum transport had: a tempo typed on
    Synths or Perform was kept, failed to render, and every later edit on
    every page was a 422."""
    client = make_client(tmp_path)
    state = client.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=200.0, drum_bars=4)
    patch = state.patch
    form = {"bpm": "80", "bars": "1", "steps": "4", "root": "A1"}
    form["scale_name"] = "pentatonic_minor"
    r = client.post(path, data={**form, "cutoff": "500"}, headers=_hx("perform"))
    assert r.status_code == 422 and "still 200 bpm" in r.text
    assert state.arrangement.bpm == 200.0 and state.patch == patch
    r = client.post(path, data={**form, "bpm": "150"}, headers=_hx("perform"))
    assert r.status_code == 200 and state.arrangement.bpm == 150.0
