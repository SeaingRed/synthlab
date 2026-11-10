"""The song arranger's routes: sections, tracks, the
pattern pool, cells, the drawer's pattern grid, and the loop bridges.

Every mutation answers with the regions it touches, out of band, plus an
`HX-Trigger: song-changed` naming the sections whose audio changed — read
off the playback plan's URLs before and after, so it is exact (a rename
changes nothing; an edit to a shared pattern lists every section using it).
A refusal is a 422 toast carrying the model's message and changes nothing.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import fxlive, limits, schema, song_bridge
from synthlab.studio import song_model as sm
from synthlab.studio.render import RenderInputs, render_stems
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

SHORT = Patch(name="short", release=0.05).to_json()
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/song"}


def song_dict() -> dict:
    """Two sections over a drums track and two synth tracks, with fixed ids:
    Intro plays Beat + Riff; Verse plays Beat + Riff + Hook (a 2-bar
    pattern), twice. Spare is in the pool, unused; Vox is an empty audio
    track."""
    return {
        "bpm": 120.0,
        "root": "A1",
        "scale_name": "minor",
        "tracks": [
            {"id": "td", "kind": "drums", "name": "Drums"},
            {"id": "t1", "kind": "synth", "name": "Bass", "patch": SHORT},
            {"id": "t2", "kind": "synth", "name": "Lead", "patch": SHORT},
            {"id": "ta", "kind": "audio", "name": "Vox"},
        ],
        "patterns": {
            "pd": {
                "id": "pd",
                "kind": "drums",
                "name": "Beat",
                "bars": 1,
                "steps": 16,
                "drums": [["kick", 0], ["snare", 8]],
            },
            "p1": {
                "id": "p1",
                "kind": "notes",
                "name": "Riff",
                "bars": 1,
                "steps_per_bar": 16,
                "cells": [["A1", 0], ["E2", 4]],
            },
            "p2": {
                "id": "p2",
                "kind": "notes",
                "name": "Hook",
                "bars": 2,
                "steps_per_bar": 16,
                "cells": [["C2", 2]],
            },
            "p3": {"id": "p3", "kind": "notes", "name": "Spare", "cells": []},
        },
        "sections": [
            {"id": "s1", "name": "Intro", "bars": 2, "cells": {"td": "pd", "t1": "p1"}},
            {
                "id": "s2",
                "name": "Verse",
                "bars": 2,
                "repeats": 2,
                "cells": {"td": "pd", "t1": "p1", "t2": "p2"},
            },
        ],
    }


def use_song(client: TestClient, song: dict | None = None) -> None:
    s = client.app.state.studio
    s.song = schema.conform(song or song_dict(), schema.nodes().SONG)
    s.fx_master = []


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def song(client) -> dict:
    return client.app.state.studio.song


def post(client, path, data=None, headers=HX):
    return client.post(path, data=data or {}, headers=headers)


def changed(r) -> list[str]:
    return json.loads(r.headers["HX-Trigger"])["song-changed"]["sections"]


def trigger(r) -> dict:
    return json.loads(r.headers.get("HX-Trigger") or "{}")


def oob_ids(html: str) -> set[str]:
    out = set()
    for tag in re.findall(r"<[a-z]+\b[^>]*>", html):
        m = re.search(r'\bid="([^"]+)"', tag)
        if m and "hx-swap-oob" in tag:
            out.add(m.group(1))
    return out


def refused(r, *words) -> None:
    assert r.status_code == 422, r.text[:300]
    for w in words:
        assert w in r.text, (w, r.text[:300])


def names(client) -> list[str]:
    return [sc["name"] for sc in song(client)["sections"]]


def plan_urls(client) -> dict:
    plan = client.get("/song/plan.json").json()
    return {e["section_id"]: (e["url"], e["loop_url"]) for e in plan["sections"]}


# --- sections -----------------------------------------------------------------


def test_add_a_section(client):
    r = post(client, "/song/section/add", {"name": "Chorus", "bars": "4"})
    assert r.status_code == 200, r.text[:300]
    sec = song(client)["sections"][-1]
    assert (sec["name"], sec["bars"], sec["cells"]) == ("Chorus", 4, {})
    assert {"song-arranger", "song-topbar"} <= oob_ids(r.text)
    assert "Chorus" in r.text  # the arranger draws it
    assert changed(r) == [sec["id"]]  # new audio: its own (silent) WAVs


def test_add_a_section_names_itself_and_can_be_inserted(client):
    r = post(client, "/song/section/add", {"at": "0"})
    assert r.status_code == 200
    assert names(client)[0] == "Section 3"
    assert song(client)["sections"][0]["bars"] == 4  # the default length


def test_adding_a_section_past_the_cap_is_refused(client):
    s = client.app.state.studio
    full = song_dict()
    full["sections"] = [
        {"id": f"x{i}", "name": f"S{i}", "bars": 1} for i in range(limits.MAX_SECTIONS)
    ]
    use_song(client, full)
    before = copy.deepcopy(s.song)
    r = post(client, "/song/section/add", {"name": "One more"})
    refused(r, str(limits.MAX_SECTIONS))
    assert s.song == before


def test_rename_a_section(client):
    r = post(client, "/song/section/s1/rename", {"name": "  Big <b>Intro</b>\n "})
    assert r.status_code == 200
    assert names(client)[0] == "Big <b>Intro</b>"
    assert "Big &lt;b&gt;Intro&lt;/b&gt;" in r.text  # autoescaped, never |safe
    assert "<b>Intro</b>" not in r.text
    assert changed(r) == []  # a name is not audio


def test_a_section_name_is_capped_and_never_blank(client):
    r = post(client, "/song/section/s1/rename", {"name": "x" * 500})
    assert r.status_code == 200
    assert names(client)[0] == "x" * limits.MAX_TITLE_CHARS
    refused(post(client, "/song/section/s1/rename", {"name": "   "}), "name")
    assert names(client)[0] == "x" * limits.MAX_TITLE_CHARS


def test_an_unknown_section_is_refused(client):
    for path in (
        "/song/section/nope/rename",
        "/song/section/nope/bars",
        "/song/section/nope/repeats",
        "/song/section/nope/duplicate",
        "/song/section/nope/move",
        "/song/section/nope/delete",
    ):
        refused(
            post(client, path, {"name": "x", "bars": "2", "repeats": "2"}), "No such"
        )


def test_section_bars(client):
    r = post(client, "/song/section/s1/bars", {"bars": "4"})
    assert r.status_code == 200
    assert song(client)["sections"][0]["bars"] == 4
    assert {"song-arranger", "song-topbar"} <= oob_ids(r.text)
    # a 1-bar cycle's WAVs are the same: only the plan's pass count changed
    # (the player re-reads the plan on every song-changed)
    assert changed(r) == []
    assert client.get("/song/plan.json").json()["sections"][0]["passes"] == 4


def test_section_bars_that_change_the_cycle_change_its_audio(client):
    r = post(client, "/song/section/s1/bars", {"bars": "3"})  # 3 % 1: still 1
    assert changed(r) == []
    post(
        client, "/song/cell", {"section_id": "s1", "track_id": "t2", "pattern_id": "p2"}
    )
    r = post(client, "/song/section/s1/bars", {"bars": "4"})  # the cycle: 4 -> 2
    assert "s1" in changed(r)


def test_section_bars_cannot_cut_its_longest_pattern(client):
    r = post(client, "/song/section/s2/bars", {"bars": "1"})
    refused(r, "Hook", "2 bars")
    assert song(client)["sections"][1]["bars"] == 2


@pytest.mark.parametrize("bad", ["", "abc", "nan", "1e400", "2.5"])
def test_hostile_section_bars_are_refused(client, bad):
    refused(post(client, "/song/section/s1/bars", {"bars": bad}))
    assert song(client)["sections"][0]["bars"] == 2


def test_section_bars_clamp_to_the_cap(client):
    r = post(client, "/song/section/s1/bars", {"bars": "9999"})
    assert r.status_code == 200
    assert song(client)["sections"][0]["bars"] == limits.MAX_SECTION_BARS


def test_a_section_that_would_make_the_song_too_long_is_refused(client):
    assert post(client, "/song/section/s1/bars", {"bars": "64"}).status_code == 200
    r = post(client, "/song/section/s1/repeats", {"repeats": "2"})  # 256 s
    assert r.status_code == 200  # under the cap since it became 480 s
    r = post(client, "/song/section/s1/repeats", {"repeats": "4"})  # 512 s > 480
    refused(r, "cap")
    assert song(client)["sections"][0]["repeats"] == 2


def test_section_repeats_absolute_and_delta(client):
    r = post(client, "/song/section/s1/repeats", {"repeats": "3"})
    assert r.status_code == 200
    assert song(client)["sections"][0]["repeats"] == 3
    post(client, "/song/section/s1/repeats", {"delta": "-1"})
    assert song(client)["sections"][0]["repeats"] == 2
    post(client, "/song/section/s1/repeats", {"delta": "-9"})
    assert song(client)["sections"][0]["repeats"] == 1  # clamped, not refused
    refused(post(client, "/song/section/s1/repeats", {"repeats": "x"}))


def test_duplicate_a_section(client):
    r = post(client, "/song/section/s1/duplicate")
    assert r.status_code == 200
    secs = song(client)["sections"]
    assert [s["name"] for s in secs] == ["Intro", "Intro ′", "Verse"]
    assert secs[1]["cells"] == secs[0]["cells"]  # the same patterns, by reference
    assert {"song-arranger", "song-pool", "song-topbar"} <= oob_ids(r.text)
    assert secs[1]["id"] in changed(r)


def test_move_a_section(client):
    r = post(client, "/song/section/s2/move", {"delta": "-1"})
    assert r.status_code == 200
    assert names(client) == ["Verse", "Intro"]
    assert set(changed(r)) == {"s1", "s2"}  # both heads fold something new
    post(client, "/song/section/s2/move", {"to": "5"})
    assert names(client) == ["Intro", "Verse"]
    refused(post(client, "/song/section/s2/move", {"to": "x"}))


def test_delete_a_section(client):
    r = post(client, "/song/section/s1/delete")
    assert r.status_code == 200
    assert names(client) == ["Verse"]
    assert "p1" in song(client)["patterns"]  # patterns stay in the pool
    assert {"song-arranger", "song-pool", "song-topbar"} <= oob_ids(r.text)
    assert changed(r) == ["s2"]  # its head no longer folds the intro in


# --- tracks -------------------------------------------------------------------


def test_add_tracks(client):
    r = post(client, "/song/track/add", {"kind": "synth"})
    assert r.status_code == 200
    t = song(client)["tracks"][-1]
    assert (t["kind"], t["name"]) == ("synth", "Synth 3")
    assert "Synth 3" in r.text
    post(client, "/song/track/add", {"kind": "audio", "name": "Choir"})
    assert song(client)["tracks"][-1]["name"] == "Choir"


def test_track_caps_are_refused(client):
    refused(post(client, "/song/track/add", {"kind": "drums"}), "1 drums")
    for _ in range(limits.MAX_SYNTH_TRACKS - 2):
        assert post(client, "/song/track/add", {"kind": "synth"}).status_code == 200
    refused(
        post(client, "/song/track/add", {"kind": "synth"}),
        f"{limits.MAX_SYNTH_TRACKS} synth",
    )
    assert (limits.MAX_SYNTH_TRACKS, limits.MAX_AUDIO_TRACKS) == (8, 3)
    assert limits.MAX_TRACKS == 12
    refused(post(client, "/song/track/add", {"kind": "kazoo"}), "kind")


def test_rename_a_track(client):
    r = post(client, "/song/track/t1/rename", {"name": "Sub"})
    assert r.status_code == 200
    assert song(client)["tracks"][1]["name"] == "Sub"
    assert "Sub" in r.text
    assert changed(r) == []
    refused(post(client, "/song/track/zz/rename", {"name": "x"}), "No such track")


def test_track_mix_mute_solo_gain(client):
    r = post(client, "/song/track/t2/mix", {"gain": "0.5"})
    assert r.status_code == 200
    assert song(client)["tracks"][2]["gain"] == 0.5
    assert "song-arranger" in oob_ids(r.text)
    assert "s2" in changed(r)  # Lead only plays in the verse
    post(client, "/song/track/t2/mix", {"mute": "on"})
    assert song(client)["tracks"][2]["mute"] is True
    post(client, "/song/track/t2/mix", {"mute": "0", "solo": "true"})
    t = song(client)["tracks"][2]
    assert (t["mute"], t["solo"], t["gain"]) == (False, True, 0.5)
    post(client, "/song/track/t2/mix", {"gain": "9"})
    assert song(client)["tracks"][2]["gain"] == 1.5  # clamped


@pytest.mark.parametrize("bad", ["nan", "inf", "abc", ""])
def test_hostile_track_gain_is_refused(client, bad):
    refused(post(client, "/song/track/t2/mix", {"gain": bad}))
    assert song(client)["tracks"][2]["gain"] == 1.0


def test_a_track_in_use_cannot_be_deleted(client):
    r = post(client, "/song/track/t1/delete")
    refused(r, "Intro", "Verse")
    assert sm.track_index(song(client), "t1") is not None


def test_delete_an_unused_track(client):
    r = post(client, "/song/track/ta/delete")
    assert r.status_code == 200
    assert sm.track_index(song(client), "ta") is None
    # (the starter sheet names a "Vox Chops" track: look for the row itself)
    assert ">Vox<" not in r.text and "Vox track options" not in r.text


def test_deleting_the_track_being_edited_ends_the_edit(client):
    """The route ends the edit itself, not
    only the routing hook (a request that names no view never ends it)."""
    s = client.app.state.studio
    loop_patch = s.patch
    sm.add_track(s.song, "synth", "Extra", patch=SHORT)
    tid = s.song["tracks"][-1]["id"]
    assert s.edit_track(tid)
    r = client.post(f"/song/track/{tid}/delete", data={})  # no view header
    assert r.status_code == 200
    assert s.song_edit_track is None
    assert s.patch == loop_patch


# --- editing a track's sound ----------------------------------------------------


def test_edit_sound_opens_synths_with_the_banner(client):
    s = client.app.state.studio
    post(client, "/song/track/t1/rename", {"name": "Bass <i>x</i>"})
    r = post(client, "/song/track/t1/edit-sound")
    assert r.status_code == 200
    loc = json.loads(r.headers["HX-Location"])
    assert loc["path"] == "/view/editor"
    assert s.song_edit_track == "t1"
    page = client.get("/view/editor", headers={"HX-Request": "true"}).text
    banner = page[page.index('id="track-edit-banner"') :][:600]
    assert "Editing" in banner and "back to Song" in banner
    assert "Bass &lt;i&gt;x&lt;/i&gt;" in banner  # autoescaped
    assert "<i>x</i>" not in banner


def test_a_lanes_sound_menu_swaps_the_sound_and_nothing_else(client):
    from synthlab.studio.factory import FACTORY
    from synthlab.studio.recipes import cards, get_recipe

    s = client.app.state.studio
    page = client.get("/view/song").text  # (the menu is the page frame's)
    name = FACTORY[3]["name"]
    assert f'<option value="factory:{name}">' in page
    assert 'hx-post="/song/track/t1/sound"' in page
    before = copy.deepcopy(s.song["tracks"][1])
    r = post(client, "/song/track/t1/sound", {"sound": f"factory:{name}"})
    assert r.status_code == 200 and changed(r) and "song-arranger" in oob_ids(r.text)
    t = s.song["tracks"][1]
    assert t["patch"]["name"] == name and t["patch"] != before["patch"]
    # a plain patch: the lane's FX, level, cells and mix are its own
    assert {k: v for k, v in t.items() if k != "patch"} == {
        k: v for k, v in before.items() if k != "patch"
    }
    # a recipe is a whole Sound: its FX come with it
    recipe = next(c["name"] for c in cards() if get_recipe(c["name"])["main_fx"])
    post(client, "/song/track/t1/sound", {"sound": f"recipes:{recipe}"})
    assert s.song["tracks"][1]["fx"] and s.song["tracks"][1]["patch"]["name"] == recipe
    # one undo step each
    post(client, "/song/undo")
    assert s.song["tracks"][1]["patch"]["name"] == name
    refused(post(client, "/song/track/td/sound", {"sound": f"factory:{name}"}), "synth")
    assert post(client, "/song/track/t1/sound", {"sound": "nope:x"}).status_code == 422
    assert (
        post(client, "/song/track/t1/sound", {"sound": "factory:"}).status_code == 422
    )


def test_a_sound_picked_for_the_docked_track_reaches_the_dock(client):
    from synthlab.studio.factory import FACTORY

    s = client.app.state.studio
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    name = FACTORY[3]["name"]
    post(client, "/song/track/t1/sound", {"sound": f"factory:{name}"})
    assert s.song_edit_track == "t1" and s.patch.name == name


def test_the_drawer_picks_the_sound_its_pattern_plays_with(client):
    """The editor drawer's head: the sound the pattern plays with is its
    TRACK's, a picker over the one sound menu (soundmenu.js) and ✎ beside it
    — on a cell, its track; on a pool pattern, the lane ▶ Play hears it
    through; none on a beat or an audio row."""
    post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    page = client.get("/view/song", headers=HX).text
    head = page[page.index('id="song-drawer"') :]
    head = head[: head.index("</header>")]
    assert 'data-track-sound="t2"' in head and 'data-track-name="Lead"' in head
    assert f'data-sound-name="{SHORT["name"]}"' in head
    assert 'data-keys="Alt+S"' in head and "in every section" in head
    assert 'hx-post="/song/track/t2/edit-sound"' in head
    post(client, "/song/drawer", {"pattern_id": "p3", "through": "t1"})
    page = client.get("/view/song", headers=HX).text
    assert 'data-track-sound="t1"' in page
    for cell in ({"section_id": "s1", "track_id": "td"}, {"pattern_id": "pd"}):
        post(client, "/song/drawer", cell)
        assert "data-track-sound=" not in client.get("/view/song", headers=HX).text


def test_a_browse_of_sounds_is_one_undo_step_heard_at_once(client):
    """The drawer's picker browses: every sound auditioned under one
    `browse` token (one open of the menu, one run of ↑ ↓ / the wheel) is ONE
    undo step, back to the sound before it; another token is another step.
    Every swap answers `song-changed` with `now` (the playing pass takes it
    where it stands) and redraws the open drawer."""
    from synthlab.studio.factory import FACTORY

    s = client.app.state.studio
    post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    was = s.song["tracks"][2]["patch"]["name"]
    names = [FACTORY[i]["name"] for i in (1, 2, 3)]
    for name in names:
        r = post(
            client,
            "/song/track/t2/sound",
            {"sound": f"factory:{name}", "browse": "abc123"},
        )
        assert r.status_code == 200 and trigger(r)["song-changed"]["now"] is True
        assert "song-drawer" in oob_ids(r.text)
        assert f'data-sound-name="{name}"' in r.text
        assert s.song["tracks"][2]["patch"]["name"] == name
    assert s.song_undo_state()["undo"] == "sound of Lead"
    depth = s.song_undo_state()["depth"]
    # a second browse: its own step
    post(
        client, "/song/track/t2/sound", {"sound": f"factory:{names[1]}", "browse": "x9"}
    )
    post(
        client, "/song/track/t2/sound", {"sound": f"factory:{names[0]}", "browse": "x9"}
    )
    assert s.song_undo_state()["depth"] == depth + 1
    post(client, "/song/undo")
    assert s.song["tracks"][2]["patch"]["name"] == names[-1]
    post(client, "/song/undo")
    assert s.song["tracks"][2]["patch"]["name"] == was  # the whole first browse
    # a plain pick (the track head's menu) is a step of its own and heard at once
    r = post(client, "/song/track/t2/sound", {"sound": f"factory:{names[1]}"})
    assert trigger(r)["song-changed"]["now"] is True
    assert s.song_undo_state()["undo"] == f"sound: {names[1]}"[:40]


def test_a_rows_edit_docks_synths_on_the_song_page(client):
    """✎ on a row: no navigation — the answer is the dock, Synths in a
    frame; the Song page's own requests keep the edit open, its ✕ and any
    other view end it."""
    s = client.app.state.studio
    loop_patch = s.patch
    r = post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    assert r.status_code == 200 and "HX-Location" not in r.headers
    assert s.song_edit_track == "t1" and "song-dock" in oob_ids(r.text)
    assert '<iframe class="dock-frame" src="/view/editor?embed=1"' in r.text
    post(client, "/song/track/t2/mix", {"gain": "0.5"})  # a Song page edit
    assert s.song_edit_track == "t1"
    page = client.get("/view/song", headers={"HX-Request": "true"}).text
    assert 'class="dock-frame"' in page
    # the framed page knows it is one, and drops its shell
    framed = client.get("/view/editor", headers={"Sec-Fetch-Dest": "iframe"}).text
    assert " data-embed" in framed[:400]
    assert " data-embed" not in client.get("/view/editor").text[:400]
    r = post(client, "/song/dock/close")
    assert s.song_edit_track is None and s.patch == loop_patch
    assert "song-dock" in oob_ids(r.text) and "dock-frame" not in r.text
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    perform = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
    client.post("/fragment/layer/focus", data={"on": "0"}, headers=perform)
    assert s.song_edit_track is None


def test_add_loop_to_song_under_a_docked_edit_adds_the_loops_own_sound(client):
    """Review finding: the edit stays open on the Song page, and "Add loop
    to song" read the working patch — the TRACK's — as the loop's."""
    s = client.app.state.studio
    s.patch = Patch(name="the loop's lead", osc1="triangle")
    s.arrangement = replace(s.arrangement, cells=[["A2", 0], ["C3", 4]])
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    assert s.patch.name != "the loop's lead"
    r = post(client, "/song/from-loop", {"name": "From loop"})
    assert r.status_code == 200 and s.song_edit_track is None
    assert s.patch.name == "the loop's lead"
    names = [t["patch"].get("name") for t in s.song["tracks"] if t["kind"] == "synth"]
    assert "the loop's lead" in names


def _wav(client, url) -> np.ndarray:
    import io

    import soundfile as sf

    return sf.read(io.BytesIO(client.get(url).content))[0]


def test_play_on_synths_hears_the_track_at_its_level_in_the_song(client):
    """A track edit's ▶ (`StudioService.track_heard`): the track's first
    section, one loop of it as the song plays it — "in the mix" is the
    section's own WAV, "alone" the cell's (`Solo this cell`), both at the
    song's level. It was the track alone at unity, ~18 dB over the song,
    and sounded like another sound. The sound bar's Hear radios choose; the
    loop's key and tempo are neither shown as the song's nor moved."""
    s = client.app.state.studio
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    loop = (s.arrangement.root, s.arrangement.scale_name, s.arrangement.bpm)
    post(client, "/song/track/t2/edit-sound", {"dock": "1"})
    page = client.get("/view/editor?embed=1").text
    # the pattern alone by default: the block clicked, no other lane
    assert 'name="edit_hear" value="alone" checked' in page
    assert "120 bpm · A1 minor" in page and 'name="phrase"' not in page
    assert "the song's Lead track" in page and 'class="ls-track"' in page
    heard = {}
    for mode in ("mix", "alone"):
        form = {"edit_hear": mode, "root": loop[0], "scale_name": loop[1]}
        r = client.post("/fragment/arrangement", data=form, headers=ed)
        assert r.status_code == 200 and s.song_edit_hear == mode
        url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
        heard[mode] = _wav(client, url)
        assert "dB rms" in r.text  # the readouts are measured
    # Lead plays first in the Verse
    assert np.allclose(
        heard["mix"], _wav(client, "/song/section/s2.wav?loop=1"), atol=1e-4
    )
    assert np.allclose(heard["alone"], _wav(client, "/song/cell/s2/t2.wav"), atol=1e-4)
    assert (s.arrangement.root, s.arrangement.scale_name, s.arrangement.bpm) == loop
    # an edit to the sound is heard on the next ▶ (mirrored before it renders)
    before = heard["alone"]
    r = client.post("/fragment/patch", data={"cutoff": "300"}, headers=ed)
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    assert not np.allclose(_wav(client, url), before, atol=1e-3)


def test_play_on_synths_follows_the_block_opened_on_the_song_page(client):
    """The dock's ▶ plays the block last opened in the drawer
    (`song_bridge.heard_section`): Bass plays in the Intro and the Verse,
    and a click on its Verse block makes ▶ the Verse's — it was always the
    first section. The drawer's answer tells the frame to render again
    (`dock-reheard`, songdock.js)."""
    s = client.app.state.studio
    song = s.song
    assert song_bridge.heard_section(song, "t1") == 0
    assert (
        song_bridge.heard_section(song, "t1", {"section_id": "s2", "track_id": "t1"})
        == 1
    )
    assert (
        song_bridge.heard_section(song, "t2", {"section_id": "s1", "track_id": "t1"})
        == 1
    )
    assert song_bridge.heard_section(song, "t2", {"pattern_id": "p2"}) == 1
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    r = post(client, "/song/drawer", {"section_id": "s2", "track_id": "t1"})
    assert json.loads(r.headers["HX-Trigger"]) == {"dock-reheard": True}
    page = client.get("/view/editor?embed=1").text
    assert "Riff · Verse" in page
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    r = client.post("/fragment/preview", data={"view": "editor"}, headers=ed)
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    assert np.allclose(
        _wav(client, url), _wav(client, "/song/cell/s2/t1.wav"), atol=1e-4
    )
    # with no dock, the drawer says nothing of it
    post(client, "/song/dock/close")
    r = post(client, "/song/drawer", {"section_id": "s1", "track_id": "t1"})
    assert "dock-reheard" not in r.headers.get("HX-Trigger", "")


def test_a_pool_pattern_plays_through_a_lane_and_the_dock_hears_it(client):
    """The pattern editor's ▶ Play on a pool pattern no section plays
    (`/song/pattern/{id}/play.wav`, `StudioService.pattern_heard`): one
    loop of it through a lane's sound, alone, at the song's level — the
    lane chosen in the drawer ("through"), else the one the dock edits;
    and the dock's own ▶ plays it too while it is open."""
    s = client.app.state.studio
    assert song_bridge.pattern_lane(s.song, "p3") == "t1"  # the first synth lane
    assert song_bridge.pattern_lane(s.song, "p3", "t2") == "t2"
    assert song_bridge.pattern_lane(s.song, "p3", "td") == "t1"  # not a synth lane
    assert song_bridge.pattern_lane(s.song, "pd", "t2") == "td"  # a beat: the drums
    s.song["patterns"]["p3"]["cells"] = [["A2", 0], ["C3", 8]]
    a = _wav(client, "/song/pattern/p3/play.wav?track=t1")
    b = _wav(client, "/song/pattern/p3/play.wav?track=t2")
    assert a.shape == b.shape and np.abs(a).max() > 0.01
    post(client, "/song/track/t2/sound", {"sound": "factory:" + _factory(3)})
    c = _wav(client, "/song/pattern/p3/play.wav?track=t2")
    assert not np.allclose(b, c, atol=1e-3)  # the lane's sound is what plays it
    assert client.get("/song/pattern/nope/play.wav").status_code == 404
    # the drawer on it: ▶ Play's lane is the dock's, then the one chosen
    post(client, "/song/track/t2/edit-sound", {"dock": "1"})
    post(client, "/song/drawer", {"pattern_id": "p3"})
    page = client.get("/view/song", headers={"HX-Request": "true"}).text
    assert "/song/pattern/p3/play.wav?track=t2" in page and "▶ Play" in page
    r = post(client, "/song/drawer", {"pattern_id": "p3", "through": "t1"})
    assert s.song_drawer == {"pattern_id": "p3", "through": "t1"}
    assert s.song_edit_track == "t1"  # the dock follows the lane chosen
    assert json.loads(r.headers["HX-Trigger"]) == {"dock-reheard": True}
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    r = client.post("/fragment/preview", data={"view": "editor"}, headers=ed)
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    heard = _wav(client, url)
    assert np.allclose(
        heard, _wav(client, "/song/pattern/p3/play.wav?track=t1"), atol=1e-4
    )


def test_a_pool_pattern_plays_in_a_song_with_no_lane_for_it(client):
    """A song with no tracks yet (a beat made in the pool first): ▶ Play
    hears the working loop's kit for a beat and Synths' sound for notes
    (`song_bridge.stand_in_lane`), levelled as a song of its own — it used
    to have no ▶ at all."""
    s = client.app.state.studio
    use_song(client, {"bpm": 124.0, "root": "A1", "scale_name": "minor"})
    beat = sm.add_pattern(s.song, "drums", "Beat 1", {"bars": 1, "steps": 16})
    s.song["patterns"][beat]["drums"] = [["kick", 0], ["kick", 8], ["snare", 4]]
    riff = sm.add_pattern(s.song, "notes", "Riff 1", {"bars": 1, "steps_per_bar": 16})
    s.song["patterns"][riff]["cells"] = [["A2", 0], ["C3", 8]]
    for pid in (beat, riff):
        audio = _wav(client, f"/song/pattern/{pid}/play.wav")
        assert np.abs(audio).max() > 0.05, pid
        post(client, "/song/drawer", {"pattern_id": pid})
        page = client.get("/view/song", headers={"HX-Request": "true"}).text
        assert f'data-cell-solo="/song/pattern/{pid}/play.wav"' in page
    assert s.song["tracks"] == []  # the stand-in is the audition's alone


def _factory(i: int) -> str:
    from synthlab.studio.factory import FACTORY

    return FACTORY[i]["name"]


def test_the_docks_frame_is_told_it_is_one_without_the_browsers_header(client):
    """Review finding: Sec-Fetch-Dest is only sent to a trustworthy origin,
    so over a LAN share the frame drew the whole shell."""
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    page = client.get("/view/song", headers={"HX-Request": "true"}).text
    assert 'src="/view/editor?embed=1"' in page
    assert " data-embed" in client.get("/view/editor?embed=1").text[:400]


def test_the_dock_redraws_for_another_track_of_the_same_name(client):
    s = client.app.state.studio
    post(client, "/song/track/t2/rename", {"name": s.song["tracks"][1]["name"]})
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    r = post(client, "/song/track/t2/edit-sound", {"dock": "1"})
    assert s.song_edit_track == "t2" and "song-dock" in oob_ids(r.text)


def test_a_song_undo_under_a_docked_edit_takes_the_working_sound_with_it(client):
    """The Song page's undo puts the track's old sound back while Synths
    still holds the edited one: the working sound follows the track, or
    the next change in the dock would mirror the undone edit back."""
    s = client.app.state.studio
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    client.post("/fragment/patch", data={"cutoff": "777"}, headers=ed)
    assert s.song["tracks"][1]["patch"]["cutoff"] == 777.0
    assert post(client, "/song/undo").status_code == 200
    assert s.song["tracks"][1]["patch"]["cutoff"] == SHORT["cutoff"]
    assert s.song_edit_track == "t1" and s.patch.cutoff == SHORT["cutoff"]
    client.post("/fragment/patch", data={"resonance": "0.4"}, headers=ed)
    assert s.song["tracks"][1]["patch"]["cutoff"] == SHORT["cutoff"]


def test_edit_sound_mirrors_patch_edits_into_the_track(client):
    s = client.app.state.studio
    post(client, "/song/track/t1/edit-sound")
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    r = client.post("/fragment/patch", data={"cutoff": "777"}, headers=ed)
    assert r.status_code == 200
    assert s.song["tracks"][1]["patch"]["cutoff"] == 777.0
    assert s.song["tracks"][2]["patch"]["cutoff"] == SHORT["cutoff"]


def test_a_track_edit_is_heard_on_the_songs_clock_not_the_loops(client):
    """Synths' ▶ and keys during a track edit: the track's own part, alone,
    on the song's tempo and key. It used to be the bench phrase on the
    working loop's — another tempo, another key, another octave."""
    svc = client.app.state.svc
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, bpm=75.0, root="A0")
    s.song["bpm"], s.song["root"] = 122.0, "D2"
    loop = svc.render_inputs("editor")
    assert loop.arr.bpm == 75.0
    post(client, "/song/track/t1/edit-sound")
    inp = svc.render_inputs("editor")
    part, name = song_bridge.track_alone(s.song, "t1")
    assert (inp.arr.bpm, inp.arr.root) == (122.0, "D2")
    assert inp.arr.cells == part.cells and inp.arr.cells
    assert not inp.arr.drums and not inp.layers
    assert fxlive.keys_bpm(s) == 122.0
    page = client.get("/view/editor", headers={"HX-Request": "true"}).text
    assert f"its part in {name}" in page
    # leaving ends it: the loop's own clock again
    s.end_track_edit()
    assert svc.render_inputs("editor").key() == loop.key()
    assert fxlive.keys_bpm(s) == 75.0


def test_the_banner_is_empty_when_no_track_is_edited(client):
    page = client.get("/view/editor").text
    banner = page[page.index('id="track-edit-banner"') :][:300]
    assert "Editing" not in banner


def test_only_a_synth_or_drums_track_has_a_sound_to_edit(client):
    refused(post(client, "/song/track/ta/edit-sound"), "synth or drums")
    refused(post(client, "/song/track/zz/edit-sound"), "synth or drums")
    assert client.app.state.studio.song_edit_track is None


DRUMS = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/drums"}
NEW_MIX = {"kick": {"level": 0.5}, "snare": {"level": 0.7}}


def test_the_drums_tracks_kit_is_edited_in_the_drum_lab_and_mirrored(client):
    """The same edit for the song's drums: the Drum Lab works on the loop's
    kit, so the track's stands in its place while the edit runs, every
    change from the Drums view is the track's, and the loop's own kit and
    drum-bus FX come back — in memory and in the saved session."""
    s = client.app.state.studio
    track = s.song["tracks"][0]
    assert track["kind"] == "drums"
    was = copy.deepcopy(track["drum_mix"])  # (the mirror writes into `track`)
    s.arrangement = replace(s.arrangement, drum_mix={"kick": {"level": 0.33}})
    s.fx_drums = [{"kind": "drive", "params": {"amount": 0.4}}]
    loop_kit = {k: getattr(s.arrangement, k) for k in song_bridge.KIT}
    loop_fx = s.fx_drums
    r = post(client, "/song/track/td/edit-sound", {"dock": "1"})
    assert r.status_code == 200 and s.song_edit_track == "td"
    assert s.track_edit_view() == "drums"
    assert 'src="/view/drums?embed=1"' in r.text and "its kit" in r.text
    assert s.arrangement.drum_mix == track["drum_mix"] != {"kick": {"level": 0.33}}
    assert s.fx_drums == track["fx"]
    # the saved session keeps the loop's kit, not the track's
    saved = s._live_payload()
    assert saved["arrangement"].get("drum_mix") == {"kick": {"level": 0.33}}
    assert saved["fx_drums"] == loop_fx
    # a change made in the Drum Lab is the track's
    s.arrangement = replace(
        s.arrangement, drum_mix={"kick": {"level": 0.5}, "snare": {"level": 0.7}}
    )
    assert client.get("/view/drums", headers={"HX-Request": "true"}).status_code == 200
    r = client.post("/fragment/layer/focus", data={"on": "0"}, headers=DRUMS)
    assert s.song["tracks"][0]["drum_mix"] == NEW_MIX
    page = client.get("/view/drums", headers={"HX-Request": "true"}).text
    assert "Editing the kit of the song" in page
    # the Song page keeps it; Synths (any other view) ends it
    post(client, "/song/track/t2/mix", {"gain": "0.5"})
    assert s.song_edit_track == "td"
    ed = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    client.post("/fragment/layer/focus", data={"on": "0"}, headers=ed)
    assert s.song_edit_track is None and s.fx_drums == loop_fx
    assert {k: getattr(s.arrangement, k) for k in song_bridge.KIT} == loop_kit
    assert s.song["tracks"][0]["drum_mix"] == NEW_MIX
    # one undo step on the Song page takes the kit edit back
    post(client, "/song/undo")
    post(client, "/song/undo")
    assert s.song["tracks"][0]["drum_mix"] == was != NEW_MIX


def test_another_tab_looking_on_does_not_close_the_dock(client):
    """The user problem: a second tab of the studio, on another view,
    refreshes itself at every change notice (its view again, a preview) —
    and those requests ended the edit the dock had just opened: the dock
    closed by itself. A page that is not the one that opened the edit ends
    it only by changing something; the opening page ends it by leaving."""
    s = client.app.state.studio

    def hdr(view: str, page: str) -> dict:
        url = f"http://testserver/view/{view}"
        return {"HX-Request": "true", "HX-Current-URL": url, "X-Studio-Page": page}

    client.post(
        "/song/track/t1/edit-sound", data={"dock": "1"}, headers=hdr("song", "A")
    )
    assert s.song_edit_track == "t1"
    # the other tab (B), on Perform: its own refresh
    assert client.get("/view/perform", headers=hdr("perform", "B")).status_code == 200
    client.post(
        "/fragment/preview", data={"view": "perform"}, headers=hdr("perform", "B")
    )
    assert s.song_edit_track == "t1"
    # ...but an edit made there is the loop's: the track's sound steps aside
    client.post("/fragment/layer/focus", data={"on": "0"}, headers=hdr("perform", "B"))
    assert s.song_edit_track is None
    # and the page that opened it leaving for another view ends it too
    client.post(
        "/song/track/t1/edit-sound", data={"dock": "1"}, headers=hdr("song", "A")
    )
    assert client.get("/view/perform", headers=hdr("perform", "A")).status_code == 200
    assert s.song_edit_track is None


def test_the_open_dock_follows_what_is_opened_in_the_drawer(client):
    """Click a block or a pool pattern and the bar shows that lane's
    instrument — a beat brings the Drum Lab on the song's kit; with the
    dock closed, nothing opens it."""
    s = client.app.state.studio
    post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    assert s.song_edit_track is None  # closed: opening a cell opens no dock
    post(client, "/song/track/t1/edit-sound", {"dock": "1"})
    r = post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    assert s.song_edit_track == "t2" and "song-dock" in oob_ids(r.text)
    assert s.patch.name == s.song["tracks"][2]["patch"].get("name")
    r = post(client, "/song/drawer", {"section_id": "s1", "track_id": "td"})
    assert s.song_edit_track == "td" and 'src="/view/drums?embed=1"' in r.text
    # a pool pattern: the first lane that plays it
    pid = s.song["sections"][0]["cells"]["t1"]
    post(client, "/song/drawer", {"pattern_id": pid})
    assert s.song_edit_track == "t1" and s.track_edit_view() == "editor"
    # an audio lane has no sound to show: the dock stays where it was
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "ta"})
    assert s.song_edit_track == "t1"


# --- the pattern pool -----------------------------------------------------------


def test_add_patterns(client):
    r = post(client, "/song/pattern/add", {"kind": "drums"})
    assert r.status_code == 200
    p = list(song(client)["patterns"].values())[-1]
    assert (p["kind"], p["name"], p["drums"]) == ("drums", "Beat 2", [])
    assert "song-pool" in oob_ids(r.text) and "Beat 2" in r.text
    post(client, "/song/pattern/add", {"kind": "notes", "name": "Arp"})
    assert list(song(client)["patterns"].values())[-1]["name"] == "Arp"
    refused(post(client, "/song/pattern/add", {"kind": "chords"}), "kind")


def test_the_pool_cap_is_refused(client):
    full = song_dict()
    for i in range(limits.MAX_PATTERNS - 4):
        full["patterns"][f"q{i}"] = {"id": f"q{i}", "kind": "notes", "name": f"q{i}"}
    use_song(client, full)
    refused(post(client, "/song/pattern/add", {"kind": "notes"}), "128")


def test_rename_a_pattern(client):
    r = post(client, "/song/pattern/p1/rename", {"name": "Bassline"})
    assert r.status_code == 200
    assert song(client)["patterns"]["p1"]["name"] == "Bassline"
    assert {"song-pool", "song-arranger"} <= oob_ids(r.text)
    assert changed(r) == []
    refused(post(client, "/song/pattern/zz/rename", {"name": "x"}), "No such pattern")


def test_a_pattern_in_use_cannot_be_deleted_and_the_toast_names_the_sections(client):
    r = post(client, "/song/pattern/p1/delete")
    refused(r, "Riff", "Intro", "Verse")
    assert "p1" in song(client)["patterns"]


def test_delete_an_unused_pattern(client):
    r = post(client, "/song/pattern/p3/delete")
    assert r.status_code == 200
    assert "p3" not in song(client)["patterns"]
    assert "Spare" not in r.text


# --- cells ----------------------------------------------------------------------


def test_assign_and_clear_a_cell(client):
    r = post(
        client, "/song/cell", {"section_id": "s1", "track_id": "t2", "pattern_id": "p3"}
    )
    assert r.status_code == 200
    assert song(client)["sections"][0]["cells"]["t2"] == "p3"
    assert {"song-arranger", "song-pool"} <= oob_ids(r.text)
    r = post(
        client, "/song/cell", {"section_id": "s1", "track_id": "t1", "pattern_id": ""}
    )
    assert r.status_code == 200
    assert song(client)["sections"][0]["cells"]["t1"] is None
    assert "s1" in changed(r)


def test_a_cell_refuses_the_wrong_kind_and_audio_tracks(client):
    refused(
        post(
            client,
            "/song/cell",
            {"section_id": "s1", "track_id": "td", "pattern_id": "p1"},
        ),
        "notes pattern",
    )
    refused(
        post(
            client,
            "/song/cell",
            {"section_id": "s1", "track_id": "ta", "pattern_id": "p1"},
        ),
        "Audio tracks",
    )
    refused(
        post(
            client,
            "/song/cell",
            {"section_id": "s1", "track_id": "t1", "pattern_id": "zz"},
        ),
        "No such pattern",
    )


def test_a_longer_pattern_grows_its_section_and_says_so(client):
    sec = song(client)["sections"][0]
    sec["bars"] = 1
    sec["cells"] = {"td": "pd"}
    r = post(
        client, "/song/cell", {"section_id": "s1", "track_id": "t2", "pattern_id": "p2"}
    )
    assert r.status_code == 200
    assert song(client)["sections"][0]["bars"] == 2
    assert "Intro" in trigger(r)["studio-toast"]["message"]
    assert "song-topbar" in oob_ids(r.text)


def test_make_a_cell_unique(client):
    r = post(client, "/song/cell/unique", {"section_id": "s2", "track_id": "t1"})
    assert r.status_code == 200
    new = song(client)["sections"][1]["cells"]["t1"]
    assert new != "p1" and song(client)["patterns"][new]["name"] == "Riff ′"
    assert song(client)["sections"][0]["cells"]["t1"] == "p1"
    assert {"song-arranger", "song-pool", "song-drawer"} <= oob_ids(r.text)
    drawer = r.text[r.text.index('id="song-drawer"') :]
    assert "Riff ′" in drawer  # the drawer now edits the copy
    assert changed(r) == []  # the same notes: nothing sounds different
    refused(
        post(client, "/song/cell/unique", {"section_id": "s1", "track_id": "t2"}),
        "empty",
    )


def test_a_cell_variation(client):
    r = post(
        client,
        "/song/cell/variation",
        {"section_id": "s2", "track_id": "t1", "seed": "7"},
    )
    assert r.status_code == 200
    new = song(client)["sections"][1]["cells"]["t1"]
    assert (
        song(client)["patterns"][new]["cells"]
        != song(client)["patterns"]["p1"]["cells"]
    )
    assert "s2" in changed(r)
    r = post(client, "/song/cell/variation", {"section_id": "s1", "track_id": "td"})
    assert r.status_code == 200  # a seed is optional
    assert song(client)["sections"][0]["cells"]["td"] != "pd"


def test_a_new_pattern_goes_straight_into_a_cell(client):
    """One step from an empty cell to a pattern you can draw in: it is made,
    assigned and opened in the drawer, on a 16th grid."""
    r = post(client, "/song/cell/new", {"section_id": "s1", "track_id": "t2"})
    assert r.status_code == 200, r.text[:300]
    pid = song(client)["sections"][0]["cells"]["t2"]
    p = song(client)["patterns"][pid]
    assert (p["kind"], p["name"], p["cells"]) == ("notes", "Riff 4", [])
    assert (p["bars"], p["steps_per_bar"]) == (1, 16)
    assert {"song-arranger", "song-pool", "song-drawer", "song-thumbs"} <= oob_ids(
        r.text
    )
    drawer = r.text[r.text.index('id="song-drawer"') :]
    assert "Riff 4" in drawer and f'hx-post="/song/pattern/{pid}/grid"' in drawer
    assert client.app.state.studio.song_drawer == {"section_id": "s1", "track_id": "t2"}
    assert changed(r) == []  # an empty pattern: nothing sounds different
    # on a drums track it is a beat
    r = post(client, "/song/cell/new", {"section_id": "s2", "track_id": "td"})
    assert r.status_code == 200
    new = song(client)["patterns"][song(client)["sections"][1]["cells"]["td"]]
    assert (new["kind"], new["name"], new["steps"]) == ("drums", "Beat 2", 16)
    assert "pd" in song(client)["patterns"]  # what the cell held stays in the pool
    assert "s2" in changed(r)  # Verse lost its beat


def test_a_new_cell_pattern_is_refused_whole(client):
    before = copy.deepcopy(song(client))
    cell = {"section_id": "s1", "track_id": "ta"}
    refused(post(client, "/song/cell/new", cell), "Audio tracks")
    refused(
        post(client, "/song/cell/new", {"section_id": "zz", "track_id": "t1"}),
        "No such section",
    )
    refused(post(client, "/song/cell/new", {"section_id": "s1"}), "No such track")
    assert song(client) == before
    full = song_dict()
    for i in range(limits.MAX_PATTERNS - 4):
        full["patterns"][f"q{i}"] = {"id": f"q{i}", "kind": "notes", "name": f"q{i}"}
    use_song(client, full)
    refused(
        post(client, "/song/cell/new", {"section_id": "s1", "track_id": "t2"}), "128"
    )
    assert song(client)["sections"][0]["cells"].get("t2") is None


def test_a_new_cell_pattern_ignores_the_fields_htmx_ajax_sends_along(client):
    """The arranger's N key posts through `htmx.ajax`, which carries every
    field on the page: the drawer's rename box, each section menu's name and
    bars. Browser-found: the new pattern was named after the open one."""
    junk = {"name": "Riff", "bars": "4", "kind": "riser", "pattern_id": "p1"}
    cell = {"section_id": "s1", "track_id": "t2"}
    r = post(client, "/song/cell/new", junk | cell)
    assert r.status_code == 200
    p = song(client)["patterns"][song(client)["sections"][0]["cells"]["t2"]]
    assert (p["name"], p["kind"], p["bars"], p["cells"]) == ("Riff 4", "notes", 1, [])


def test_duplicate_a_pool_pattern(client):
    r = post(client, "/song/pattern/p2/duplicate")
    assert r.status_code == 200, r.text[:300]
    pats = song(client)["patterns"]
    new = next(pid for pid in pats if pid not in ("pd", "p1", "p2", "p3"))
    src, dup = pats["p2"], pats[new]
    assert dup["name"] == "Hook ′"
    for key in ("kind", "bars", "steps_per_bar", "cells", "color"):
        assert dup[key] == src[key], key
    assert dup["cells"] is not src["cells"]
    assert sm.pattern_usage(song(client), new) == []  # in the pool, in no cell
    assert client.app.state.studio.song_drawer == {"pattern_id": new}
    assert {"song-pool", "song-drawer", "song-thumbs"} <= oob_ids(r.text)
    assert "song-arranger" not in oob_ids(r.text)  # no cell changed
    assert changed(r) == []
    refused(post(client, "/song/pattern/zz/duplicate"), "No such pattern")


def test_a_new_note_pattern_opens_on_a_sixteenth_grid(client):
    """ "+ note pattern" used to make a 1-bar pattern of 4 steps: every new
    riff began with a click on "16". The schema's own default is unchanged
    (an older file with no steps_per_bar still loads as 4)."""
    post(client, "/song/pattern/add", {"kind": "notes"})
    assert list(song(client)["patterns"].values())[-1]["steps_per_bar"] == 16
    assert song(client)["patterns"]["p3"]["steps_per_bar"] == 4  # Spare: no key


def test_the_drawer_opens_a_cell_or_a_pool_pattern(client):
    r = post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    assert r.status_code == 200
    drawer = r.text[r.text.index('id="song-drawer"') :]
    assert "Hook" in drawer and "Verse" in drawer and "Lead" in drawer
    assert "HX-Trigger" not in r.headers  # opening changes no audio
    r = post(client, "/song/drawer", {"pattern_id": "p3"})
    assert "Spare" in r.text[r.text.index('id="song-drawer"') :]
    r = post(client, "/song/drawer", {})  # closes
    assert "Spare" not in r.text
    assert client.app.state.studio.song_drawer is None


# --- the drawer's pattern grid ---------------------------------------------------


def test_the_drum_grid_edits_the_pattern_and_never_the_loop(client):
    s = client.app.state.studio
    loop = copy.deepcopy(s.arrangement)
    r = post(
        client,
        "/song/pattern/pd/grid",
        {
            "bars": "1",
            "steps": "16",
            "drum.kick.0": "on",
            "drum.hat.2": "on",
            "dvel.2": "0.5",
        },
    )
    assert r.status_code == 200, r.text[:300]
    p = s.song["patterns"]["pd"]
    assert p["drums"] == [["hat", 2], ["kick", 0]]
    assert p["drum_vel"]["2"] == 0.5
    assert s.arrangement == loop
    assert set(changed(r)) == {"s1", "s2"}  # both sections play Beat


def test_the_note_grid_edits_the_pattern(client):
    s = client.app.state.studio
    loop = copy.deepcopy(s.arrangement)
    r = post(
        client,
        "/song/pattern/p2/grid",
        {
            "bars": "2",
            "steps": "16",
            "cell.C2.2": "on",
            "cell.A1.17": "on",
            "vel.17": "0.4",
            "slide.17": "on",
            "arp_mode": "up",
            "arp_octaves": "2",
        },
    )
    assert r.status_code == 200, r.text[:300]
    p = s.song["patterns"]["p2"]
    assert sorted(p["cells"]) == [["A1", 17], ["C2", 2]]
    assert (p["velocities"], p["slides"]) == ({"17": 0.4}, [17])
    assert (p["arp_mode"], p["arp_octaves"]) == ("up", 2)
    assert s.arrangement == loop
    assert "s2" in changed(r)


def test_a_grid_post_keeps_notes_written_under_another_key(client):
    """The drawer's grid shows the song key's rows; a note on a pitch
    outside them (written before a key change) is hidden, not deleted."""
    song(client)["patterns"]["p1"]["cells"].append(["G4", 3])
    r = post(
        client, "/song/pattern/p1/grid", {"bars": "1", "steps": "16", "cell.A1.0": "on"}
    )
    assert r.status_code == 200
    assert sorted(song(client)["patterns"]["p1"]["cells"]) == [["A1", 0], ["G4", 3]]


def test_growing_a_pattern_grows_its_sections_and_redraws_the_drawer(client):
    post(client, "/song/drawer", {"pattern_id": "p1"})  # the drawer shows it
    r = post(
        client, "/song/pattern/p1/grid", {"bars": "4", "steps": "16", "cell.A1.0": "on"}
    )
    assert r.status_code == 200
    assert song(client)["patterns"]["p1"]["bars"] == 4
    assert [sc["bars"] for sc in song(client)["sections"]] == [4, 4]
    assert "grew" in trigger(r)["studio-toast"]["message"]
    assert {"song-drawer", "song-arranger", "song-topbar"} <= oob_ids(r.text)


def test_growing_a_drum_pattern_repeats_its_bars(client):
    r = post(
        client,
        "/song/pattern/pd/grid",
        {"bars": "2", "steps": "16", "drum.kick.0": "on"},
    )
    assert r.status_code == 200
    assert song(client)["patterns"]["pd"]["drums"] == [["kick", 0], ["kick", 16]]


def test_a_grid_post_to_an_unknown_pattern_is_refused(client):
    refused(
        post(client, "/song/pattern/zz/grid", {"drum.kick.0": "on"}), "No such pattern"
    )


def test_a_hostile_grid_never_500s(client):
    for form in (
        {"bars": "x", "steps": "999", "drum.nope.0": "on", "drum.kick.x": "on"},
        {"bars": "-1", "dvel.²": "1", "dvel.3": "nan", "drum.kick.99999": "on"},
    ):
        assert post(client, "/song/pattern/pd/grid", form).status_code in (200, 422)
    for form in (
        {"cell.Z9.0": "on", "vel.0": "x", "slide.q": "on", "arp_mode": "sideways"},
        {"steps": "7", "arp_octaves": "99", "cell.A2.-1": "on"},
    ):
        assert post(client, "/song/pattern/p1/grid", form).status_code in (200, 422)
    schema.conform(song(client), schema.nodes().SONG)  # still a fixed point


# --- the loop bridges -------------------------------------------------------------


def test_add_the_loop_to_the_song(client):
    r = post(client, "/song/from-loop", {"name": "Jam"})
    assert r.status_code == 200
    assert names(client)[-1] == "Jam"
    assert {"song-arranger", "song-pool", "song-topbar"} <= oob_ids(r.text)
    assert song(client)["sections"][-1]["id"] in changed(r)


def test_open_a_section_in_the_loop(client):
    s = client.app.state.studio
    r = post(client, "/song/section/s2/to-loop")
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Location"])["path"] == "/view/perform"
    # Hook (the layer) is 2 bars, Riff 1: the loop's one grid covers the
    # whole cycle, Riff tiled onto it (song_bridge.to_loop)
    assert s.arrangement.bars == 2
    assert sorted(s.arrangement.cells) == [
        ["A1", 0],
        ["A1", 16],
        ["E2", 4],
        ["E2", 20],
    ]
    assert s.arrangement.drums == [["kick", 0], ["snare", 8]]
    refused(post(client, "/song/section/zz/to-loop"), "No such section")


# --- song-changed is exact ----------------------------------------------------------


def three_sections() -> dict:
    """The fixture song plus Outro (drums only) after the verse."""
    sg = song_dict()
    sg["sections"].append(
        {"id": "s3", "name": "Outro", "bars": 1, "cells": {"td": "pd"}}
    )
    return sg


def test_a_pattern_edit_lists_its_sections_and_the_next_head(client):
    """Hook plays only in the verse: the verse changes, and so does the
    outro's first pass, which folds the verse's tail in. The intro doesn't."""
    use_song(client, three_sections())
    r = post(
        client, "/song/pattern/p2/grid", {"bars": "2", "steps": "16", "cell.E2.5": "on"}
    )
    assert changed(r) == ["s2", "s3"]


def test_an_edit_to_a_shared_pattern_lists_every_section_using_it(client):
    r = post(
        client, "/song/pattern/p1/grid", {"bars": "1", "steps": "16", "cell.A1.8": "on"}
    )
    assert set(changed(r)) == {"s1", "s2"}


@pytest.fixture
def renders(monkeypatch):
    """Every audio render the studio can start, counted."""
    from synthlab.studio import render, service

    calls: list[str] = []

    def spy(mod, name):
        real = getattr(mod, name)

        def wrapped(*a, **k):
            calls.append(name)
            return real(*a, **k)

        monkeypatch.setattr(mod, name, wrapped)

    spy(render, "build_song")
    spy(render, "build_clips_song")
    spy(service, "section_master_audio")
    spy(service, "render_loop")
    return calls


def test_an_edit_renders_no_audio(client, renders):
    """song-changed comes from render-free idents: a drawer grid post, a
    track mix and a cell change render nothing inside the request."""
    use_song(client, three_sections())
    for path, form in (
        ("/song/pattern/p2/grid", {"bars": "2", "steps": "16", "cell.D2.3": "on"}),
        ("/song/pattern/pd/grid", {"bars": "1", "steps": "16", "drum.hat.4": "on"}),
        ("/song/track/t1/mix", {"gain": "0.7"}),
        ("/song/cell", {"section_id": "s3", "track_id": "t1", "pattern_id": "p1"}),
    ):
        r = post(client, path, form)
        assert r.status_code == 200, r.text[:200]
        assert changed(r)
    assert renders == []


def test_a_failing_trigger_never_fails_a_committed_edit(client, monkeypatch):
    svc = client.app.state.svc

    def boom(*a, **k):
        raise RuntimeError("idents exploded")

    monkeypatch.setattr(svc, "_song_idents", boom)
    r = post(client, "/song/section/s1/rename", {"name": "Start"})
    assert r.status_code == 200
    assert changed(r) == []
    assert names(client)[0] == "Start"
    saved = json.loads((svc.data_dir / "state.json").read_text(encoding="utf-8"))
    assert saved["song"]["sections"][0]["name"] == "Start"


def test_the_drawer_counts_notes_outside_the_key(client):
    song(client)["patterns"]["p1"]["cells"] += [["G4", 3], ["C5", 5]]
    r = post(client, "/song/drawer", {"pattern_id": "p1"})
    assert "2 notes outside the key" in r.text
    ctx = client.app.state.svc.song_context()
    assert ctx["song_drawer"]["hidden_count"] == 2
    post(client, "/song/drawer", {"pattern_id": "pd"})
    assert client.app.state.svc.song_context()["song_drawer"]["hidden_count"] == 0


# --- Ruling 6: preview keys change only where the audio did ------------------------


def _loop_inputs(gain: float) -> RenderInputs:
    return RenderInputs(
        patch=Patch.from_dict(SHORT),
        arr=Arrangement(bars=1, steps_per_bar=4, cells=[["C3", 1]]),
        curves={},
        layers=[_layer(gain)],
    )


def test_a_layer_gain_loop_gets_a_new_preview_key():
    """/preview/{key}.wav is immutable: a loop whose layer gain is now
    heard must not reuse its pre-fix key. Before the layer-gain fix the
    gain-1.0 loop's key was "e02c2cc063a1" (the fix kept it), and every
    key then moved once with `render.RENDER_REV` 1 (the release rule)."""
    assert _loop_inputs(1.0).key() == "0743efba58a5"  # RENDER_REV 3
    assert _loop_inputs(1.0).key() != "e02c2cc063a1"  # the old release's audio
    assert _loop_inputs(0.5).key() != "fe477644e6b6"  # the pre-fix audio's key


def test_a_refusal_changes_nothing_and_triggers_nothing(client):
    before = copy.deepcopy(song(client))
    r = post(client, "/song/pattern/p1/delete")
    assert r.status_code == 422
    assert "song-changed" not in trigger(r)
    assert song(client) == before


# --- Ruling 6: a layer's gain is heard ----------------------------------------------


def _layer(gain: float) -> dict:
    return {
        "patch": SHORT,
        "cells": [["A2", 0], ["E3", 2]],
        "muted": False,
        "gain": gain,
        "fx": [],
    }


def test_a_layer_at_half_gain_halves_its_loop_stem():
    def layer_stem(gain):
        arr = Arrangement(bars=1, steps_per_bar=4, cells=[["C3", 1]])
        inp = RenderInputs(
            patch=Patch.from_dict(SHORT), arr=arr, curves={}, layers=[_layer(gain)]
        )
        return dict(render_stems(inp))["layer2"]

    full = layer_stem(1.0)
    assert np.max(np.abs(full)) > 0.01
    np.testing.assert_array_equal(layer_stem(0.5), 0.5 * full)


def test_a_layer_track_at_half_gain_halves_its_section_stem():
    def layer_stem(gain):
        sg = schema.conform(song_dict(), schema.nodes().SONG)
        sg["tracks"][2]["gain"] = gain  # Lead: a layer under Bass in the verse
        parts = sm.section_arrangement(sg, sg["sections"][1])
        assert parts.layers[0]["gain"] == gain
        return dict(render_stems(parts.inputs()))["layer2"]

    full = layer_stem(1.0)
    assert np.max(np.abs(full)) > 0.01
    np.testing.assert_array_equal(layer_stem(0.5), 0.5 * full)


# --- solo -----------------------------------------------------------------


def test_a_solo_silences_every_other_track_in_a_section():
    sg = schema.conform(song_dict(), schema.nodes().SONG)
    sg["tracks"][2]["solo"] = True  # Lead
    parts = sm.section_arrangement(sg, sg["sections"][1])
    assert parts.main_muted  # Bass
    assert parts.arr.drums == []  # the drums track
    assert [lay["muted"] for lay in parts.layers] == [False]  # Lead plays
    sg["tracks"][2]["solo"] = False
    parts = sm.section_arrangement(sg, sg["sections"][1])
    assert not parts.main_muted and parts.arr.drums
