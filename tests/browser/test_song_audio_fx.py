"""An audio track's rack and its lanes on the Song page (Oct 2026; studio
docs/song.md, "An audio track's rack and lanes"): a sampled loop on an
audio track, a lowpass put on the track from the drawer, and a cutoff lane
over the section — the filter-house sweep.

Proves: the track's ⋯ menu opens the drawer on the audio row, the rack's
"+ add…" posts and draws the unit, the lane menu lists the unit's cutoff
and "+ Lane" adds it, and a slider of the unit and of the lane each lands
on the song — in Chromium, without a console error. Does not prove the
sweep is heard (nobody has listened: `tests/studio/test_audio_fx.py`
measures it)."""

from __future__ import annotations

import numpy as np

from synthlab.core import SR
from synthlab.studio import schema
from synthlab.studio import song_model as sm
from synthlab.studio.clips import save_clip
from tests.studio.test_song_routes import song_dict

from .test_song import _wait


def _vox(server) -> dict:
    return next(t for t in server.state.song["tracks"] if t["id"] == "ta")


def test_a_lowpass_on_an_audio_track_and_a_cutoff_lane_from_the_drawer(studio, server):
    rng = np.random.default_rng(3)
    save_clip(server.data_dir, "disco-loop", rng.standard_normal((2 * SR, 2)) * 0.2)
    song = schema.conform(song_dict(), schema.nodes().SONG)
    sm.place_take(song, "s1", "ta", "disco-loop", 0)
    server.state.song = schema.conform(song, schema.nodes().SONG)
    server.state.fx_master = []
    page = studio.open("song")
    studio.settle()

    # the track's ⋯ menu: "Effects and lanes…" opens the drawer on its row
    page.click("#trk-ta .sec-menu-btn")
    page.locator("#trkmenu-ta .track-fx").click()
    page.wait_for_selector("#song-drawer .drawer-fx")
    studio.settle()
    assert page.locator("#song-drawer .drawer-pattern").inner_text() == (
        "Effects and lanes"
    )

    # + add… Lowpass: the unit is on the track and drawn in the rack
    page.select_option("#song-drawer .drawer-fx .fx-adder", "lowpass")
    page.wait_for_selector('#song-drawer .fx-unit[data-unit="lowpass"]')
    studio.settle()
    assert [u["kind"] for u in _vox(server)["fx"]] == ["lowpass"]
    # its resonance up: a knob posts itself
    q = page.locator('#song-drawer .fx-unit[data-unit="lowpass"] input[type=range]')
    q.nth(1).fill("3.5")
    q.nth(1).dispatch_event("change")
    _wait(
        page,
        lambda: _vox(server)["fx"][0]["params"]["q"] == 3.5,
        "the resonance reaching the track",
    )

    # + Lane: the unit's cutoff, over Intro
    page.select_option("#song-drawer .lane-add select", "fx:@0:lowpass:cutoff")
    page.click("#song-drawer .lane-add button[type=submit]")
    page.wait_for_selector('#song-drawer .lane-row[data-target^="fx:"]')
    studio.settle()
    (lane,) = server.state.song["sections"][0]["auto"]["ta"]
    uid = _vox(server)["fx"][0]["id"]
    assert lane["target"] == f"fx:{uid}:lowpass:cutoff"
    # swept open: from the bottom of its range at the start to the top
    row = page.locator('#song-drawer .lane-row[data-target^="fx:"]')
    start = row.locator('input[name="start"]')
    start.fill("0")
    start.dispatch_event("change")
    _wait(
        page,
        lambda: (
            server.state.song["sections"][0]["auto"]["ta"][0]["points"][0][1] == 0.0
        ),
        "the lane's start reaching the song",
    )
    end = row.locator('input[name="end"]')
    end.fill("1")
    end.dispatch_event("change")
    _wait(
        page,
        lambda: (
            server.state.song["sections"][0]["auto"]["ta"][0]["points"]
            == [[0.0, 0.0], [1.0, 1.0]]
        ),
        "the lane's end reaching the song",
    )
    # the arranger marks the automated row
    page.wait_for_selector("#cell-s1-ta .cell-auto")
    studio.settle()
