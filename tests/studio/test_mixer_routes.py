"""The Mix view and its routes (studio docs/song.md,
"The mixer"): `GET /view/mixer`, `POST /song/mix/strip/{track_id}`,
`POST /song/mix/return/{return_id}`, `POST /song/mix`, the plan's
`levels`, and the mix in the song's undo ring, its saved state, its
project file and its exports.

The render itself (the pan law, a send's level, the returns) is
tests/studio/test_mixer_render.py."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits, mixer, schema
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import (
    HX,
    changed,
    plan_urls,
    post,
    refused,
    song,
    song_dict,
    trigger,
    use_song,
)

MIX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/mixer"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        yield c


def track(client, tid) -> dict:
    return next(t for t in song(client)["tracks"] if t["id"] == tid)


def undo_depth(client) -> int:
    return client.app.state.studio.song_undo_state()["depth"]


def strip(client, tid, data, headers=MIX):
    return post(client, f"/song/mix/strip/{tid}", data, headers)


# --- the view ---------------------------------------------------------------------


def test_the_mix_view_draws_a_strip_for_every_track(client):
    html = client.get("/view/mixer").text
    assert 'data-view="mixer"' in html and 'id="mixer-strips"' in html
    for t in song(client)["tracks"]:
        assert f'data-strip="{t["id"]}"' in html
        assert f'data-meter="{t["id"]}"' in html  # a meter a strip, lit by the page
    # a strip's first four value controls are level, pan, send A, send B (the
    # deck's focus mode reads them in this order), then duck and the EQ
    one = html.split('data-strip="t1"')[1].split("</fieldset>")[0]
    names = re.findall(r'<input type="range"[^>]*name="([^"]+)"', one)
    assert names == [
        "gain",
        "pan",
        "send.0",
        "send.1",
        "duck",
        "eq.low",
        "eq.mid",
        "eq.high",
    ]
    assert 'class="knob fader"' in one
    # the audio track has no pan, sends, duck or role, but an EQ (its chain
    # runs over its takes); the drums do not duck under themselves
    audio = html.split('data-strip="ta"')[1].split("</fieldset>")[0]
    for name in ("pan", "send.0", "send.1", "duck", "role"):
        assert re.search(rf'name="{name}"[^>]*disabled', audio), name
    assert not re.search(r'name="eq.low"[^>]*disabled', audio)
    drums = html.split('data-strip="td"')[1].split("</fieldset>")[0]
    assert re.search(r'name="duck"[^>]*disabled', drums)
    assert not re.search(r'name="pan"[^>]*disabled', drums)


def test_the_mix_view_is_in_the_rail_the_palette_and_on_a_key(client):
    html = client.get("/").text
    link = re.search(r'<a class="view[^>]*href="/view/mixer"[^>]*>', html).group(0)
    # its rail jump key and Alt+M (the rail jumps by number)
    assert 'data-cmd="Go to Mix"' in link and ' · Alt+M"' in link
    # right after Song in the rail (the make group: Synths, Drums, Perform,
    # Song, Mix)
    assert html.index('href="/view/song"') < html.index('href="/view/mixer"')
    assert html.index('href="/view/perform"') < html.index('href="/view/song"')
    assert "/static/mixer.js" in html


def test_every_action_on_the_mix_view_is_a_command(client):
    post(client, "/song/mix", {"op": "return-add"}, MIX)
    html = client.get("/view/mixer").text
    view = html.split('id="view"')[1].split("</main>")[0]
    for tag in re.findall(r"<button\b[^>]*>", view):
        if "hx-post" in tag or "data-song-action" in tag or "data-mix-focus" in tag:
            assert "data-cmd=" in tag or "data-menu=" in tag, tag
    for name in ("Balance the mix", "Mute the focused strip", "Play the song"):
        assert f'data-cmd="{name}"' in view


def test_an_empty_song_gets_an_empty_state_that_starts_one(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        html = c.get("/view/mixer").text
        assert 'class="empty-state mix-empty"' in html
        assert 'hx-post="/song/start"' in html.split("mix-empty")[1]
        assert "data-strip=" not in html
        # ... and it does: one click makes a song with strips
        r = post(c, "/song/start", {}, MIX)
        assert r.status_code == 200
        assert 'data-strip="' in c.get("/view/mixer").text


def test_a_view_swap_to_mix_renders_no_loop_preview(client):
    r = client.get("/view/mixer", headers=HX)
    assert r.status_code == 200
    assert "preview-ready" not in (r.headers.get("HX-Trigger") or "")


# --- a strip ----------------------------------------------------------------------


def test_pan_is_set_clamped_and_changes_the_sections_it_plays_in(client):
    before = plan_urls(client)
    r = strip(client, "t1", {"pan": "-0.4"})
    assert r.status_code == 200
    assert track(client, "t1")["pan"] == -0.4
    assert changed(r) == ["s1", "s2"]
    assert plan_urls(client) != before
    assert 'id="mixer-strips"' in r.text and "outerMorph" in r.text
    strip(client, "t1", {"pan": "7"})
    assert track(client, "t1")["pan"] == 1.0
    strip(client, "t1", {"pan": "0"})
    assert "pan" not in track(client, "t1")  # absent when unset
    assert plan_urls(client) == before


def test_the_first_send_makes_the_returns_and_a_send_of_zero_is_absent(client):
    assert "returns" not in song(client)
    r = strip(client, "t1", {"send.0": "0.5"})
    assert r.status_code == 200
    (bus,) = song(client)["returns"]
    assert bus["name"] == "A Reverb" and bus["fx"][0]["kind"] == "reverb"
    assert track(client, "t1")["sends"] == {bus["id"]: 0.5}
    assert f'data-return="{bus["id"]}"' in r.text
    strip(client, "t2", {"send.1": "0.3"})
    assert [b["name"] for b in song(client)["returns"]] == ["A Reverb", "B Delay"]
    strip(client, "t1", {"send.0": "0"})
    assert "sends" not in track(client, "t1")
    assert len(song(client)["returns"]) == 2  # the return stays


def test_duck_and_eq_are_the_tracks_units(client):
    r = strip(client, "t1", {"duck": "0.7"})
    assert r.status_code == 200
    (unit,) = track(client, "t1")["fx"]
    assert unit["kind"] == "sidechain" and unit["params"]["depth"] == 0.7
    strip(client, "t1", {"eq.mid": "-4.5"})
    eq = track(client, "t1")["fx"][1]
    assert eq["kind"] == "eq" and eq["params"]["mid_gain"] == -4.5
    # the Song page's own picture of the chain follows from there
    html = client.get("/view/mixer").text
    one = html.split('data-strip="t1"')[1].split("</fieldset>")[0]
    assert "Sidechain" in one and "EQ" in one


def test_mute_and_solo_are_buttons_and_the_plan_follows(client):
    before = plan_urls(client)
    r = strip(client, "t1", {"mute": "on"})
    assert track(client, "t1")["mute"] is True
    assert plan_urls(client) != before
    assert re.search(r'data-mix="mute" aria-pressed="true"', r.text)
    strip(client, "t1", {"mute": "off"})
    assert plan_urls(client) == before
    strip(client, "t2", {"solo": "on"})
    assert track(client, "t2")["solo"] is True


def test_the_drums_fader_is_heard(client):
    """A drums track's level had no reader before the mixer: the Song
    page's knob for it moved no sample."""
    before = plan_urls(client)
    strip(client, "td", {"gain": "0.5"})
    assert plan_urls(client) != before
    svc = client.app.state.svc
    s = song(client)
    quiet = svc.song_stem_levels(s)
    strip(client, "td", {"gain": "1"})
    client.get("/song/plan.json")
    loud = svc.song_stem_levels(song(client))
    sid = s["sections"][0]["id"]
    # the song's gain moves with the mix: compare against a track left alone
    ratio = (quiet[sid]["td"][1] / quiet[sid]["t1"][1]) / (
        loud[sid]["td"][1] / loud[sid]["t1"][1]
    )
    assert ratio == pytest.approx(0.5, rel=1e-3)


def test_a_fader_gesture_is_one_undo_step_and_each_control_its_own(client):
    was = undo_depth(client)
    for v in ("0.9", "0.8", "0.7", "0.6"):
        strip(client, "t1", {"gain": v})
    assert undo_depth(client) == was + 1
    strip(client, "t1", {"pan": "0.2"})
    strip(client, "t1", {"pan": "0.3"})
    assert undo_depth(client) == was + 2
    strip(client, "t2", {"pan": "0.3"})  # another strip: another step
    assert undo_depth(client) == was + 3
    r = post(client, "/song/undo", headers=MIX)
    assert r.status_code == 200 and "pan" not in track(client, "t2")
    post(client, "/song/undo", headers=MIX)
    assert "pan" not in track(client, "t1") and track(client, "t1")["gain"] == 0.6
    post(client, "/song/undo", headers=MIX)
    assert track(client, "t1")["gain"] == 1.0
    post(client, "/song/redo", headers=MIX)
    assert track(client, "t1")["gain"] == 0.6


def test_the_first_send_and_its_returns_are_one_undo_step(client):
    strip(client, "t1", {"send.1": "0.5"})
    assert len(song(client)["returns"]) == 2
    post(client, "/song/undo", headers=MIX)
    assert "returns" not in song(client) and "sends" not in track(client, "t1")


def test_hostile_strip_posts_change_nothing(client):
    blob = json.dumps(song(client), sort_keys=True)
    refused(strip(client, "nope", {"pan": "0.5"}), "No such track")
    refused(strip(client, "t1", {"pan": "left"}), "number")
    refused(strip(client, "t1", {"pan": "nan"}), "number")
    refused(strip(client, "t1", {"mute": "maybe"}), "on or off")
    refused(strip(client, "t1", {"send.7": "0.5"}), "No such send")
    refused(strip(client, "t1", {"eq.sub": "3"}), "No such EQ band")
    refused(strip(client, "ta", {"pan": "0.5"}), "audio track")
    refused(strip(client, "td", {"duck": "0.5"}), "synth")
    refused(strip(client, "t1", {}), "Nothing to change")
    refused(strip(client, "t1", {"patch": "{}", "name": "x"}), "Nothing to change")
    assert json.dumps(song(client), sort_keys=True) == blob
    # an unknown field beside a known one is ignored, never applied
    assert strip(client, "t1", {"pan": "0.1", "kind": "audio"}).status_code == 200
    assert track(client, "t1")["kind"] == "synth"


# --- returns ----------------------------------------------------------------------


def test_returns_are_added_capped_edited_and_removed(client):
    r = post(client, "/song/mix", {"op": "return-add"}, MIX)
    assert r.status_code == 200
    post(client, "/song/mix", {"op": "return-add"}, MIX)
    a, b = song(client)["returns"]
    assert (a["fx"][0]["kind"], b["fx"][0]["kind"]) == ("reverb", "delay")
    assert len(a["id"]) == 8 and a["id"] != b["id"]
    refused(post(client, "/song/mix", {"op": "return-add"}, MIX), "at most")
    assert limits.MAX_RETURNS == 2
    # its level and its unit's parameters, clamped to the unit's range
    r = post(client, f"/song/mix/return/{a['id']}", {"gain": "0.5"}, MIX)
    assert r.status_code == 200 and song(client)["returns"][0]["gain"] == 0.5
    post(client, f"/song/mix/return/{a['id']}", {"p.decay": "99"}, MIX)
    assert song(client)["returns"][0]["fx"][0]["params"]["decay"] == 6.0
    refused(
        post(client, f"/song/mix/return/{a['id']}", {"p.mix": "0.2"}, MIX),
        "No such return parameter",
    )
    refused(post(client, "/song/mix/return/nope", {"gain": "1"}, MIX), "No such return")
    refused(post(client, f"/song/mix/return/{a['id']}", {}, MIX), "Nothing")
    # removing one drops every send to it
    strip(client, "t1", {"send.0": "0.5"})
    strip(client, "t1", {"send.1": "0.25"})
    r = post(client, "/song/mix", {"op": "return-remove", "id": a["id"]}, MIX)
    assert r.status_code == 200
    assert [x["id"] for x in song(client)["returns"]] == [b["id"]]
    assert track(client, "t1")["sends"] == {b["id"]: 0.25}
    refused(
        post(client, "/song/mix", {"op": "return-remove", "id": a["id"]}, MIX),
        "No such return",
    )
    refused(post(client, "/song/mix", {"op": "explode"}, MIX), "No such mix action")


def test_a_send_is_heard_in_the_plan_and_only_where_the_track_plays(client):
    before = plan_urls(client)
    r = strip(client, "t2", {"send.0": "0.6"})  # t2 plays in the Verse only
    assert changed(r) == ["s2"]  # the Intro's own audio is untouched
    assert plan_urls(client)["s2"] != before["s2"]
    # a send on a track that plays nowhere changes no section
    r = strip(client, "t2", {"send.0": "0"})
    assert changed(r) == ["s2"] and plan_urls(client) == before


# --- levels and Balance -----------------------------------------------------------


def test_the_plan_carries_each_sections_stem_levels(client):
    strip(client, "t2", {"send.0": "0.6"})
    rid = song(client)["returns"][0]["id"]
    plan = client.get("/song/plan.json").json()
    intro, verse = plan["sections"]
    assert set(intro["levels"]) == {"td", "t1"}
    assert set(verse["levels"]) == {"td", "t1", "t2", rid}
    for peak, rms in verse["levels"].values():
        assert 0 < rms <= peak <= 1.5
    # a muted track has no level; the others keep theirs
    strip(client, "t1", {"mute": "on"})
    plan = client.get("/song/plan.json").json()
    assert set(plan["sections"][0]["levels"]) == {"td"}
    # asked again, nothing renders and the levels are still there
    assert client.get("/song/plan.json").json() == plan


def test_balance_sets_roles_under_the_drums_in_one_undo_step(client):
    post(client, "/song/track/t1/mix", {"role": "bass"}, MIX)
    post(client, "/song/track/t2/mix", {"role": "pad"}, MIX)
    strip(client, "td", {"gain": "0.3"})  # quiet drums: no target is past a fader
    was = undo_depth(client)
    # what the route should do, worked from the levels it reads (a cold
    # song: it renders them itself)
    svc = client.app.state.svc
    want = json.loads(json.dumps(song(client)))
    svc.song_gain(want)
    moved = mixer.balance(want, svc.song_stem_levels(want))
    assert [m[0] for m in moved] == ["Bass", "Lead"]
    svc.reset_caches()
    r = post(client, "/song/mix", {"op": "balance"}, MIX)
    assert r.status_code == 200, r.text[:300]
    said = trigger(r)["studio-toast"]["message"]
    assert said.startswith("Balanced in Verse: Bass 100") and "Lead 100" in said
    assert undo_depth(client) == was + 1
    assert changed(r)  # the player follows
    for tid in ("t1", "t2"):
        assert track(client, tid)["gain"] == next(
            t["gain"] for t in want["tracks"] if t["id"] == tid
        )
    # where it landed: each role's level under the drums, in the Verse
    client.get("/song/plan.json")
    now = svc.song_stem_levels(song(client))["s2"]
    assert mixer.db(now["t1"][1] / now["td"][1]) == pytest.approx(-2.0, abs=0.5)
    assert mixer.db(now["t2"][1] / now["td"][1]) == pytest.approx(-9.0, abs=0.5)
    # one Ctrl+Z takes the whole balance back
    post(client, "/song/undo", headers=MIX)
    assert track(client, "t1")["gain"] == 1.0 and track(client, "t2")["gain"] == 1.0


def test_balance_with_nothing_to_move_says_so_and_is_no_undo_step(client):
    post(client, "/song/track/t1/mix", {"role": "bass"}, MIX)
    svc = client.app.state.svc
    s = json.loads(json.dumps(song(client)))
    svc.song_gain(s)
    mixer.balance(s, svc.song_stem_levels(s))
    gain = next(t["gain"] for t in s["tracks"] if t["id"] == "t1")
    if gain in mixer.GAIN_RANGE:  # held at an end of the fader: it stays there
        strip(client, "t1", {"gain": str(gain)})
        was = undo_depth(client)
        r = post(client, "/song/mix", {"op": "balance"}, MIX)
        assert trigger(r)["studio-toast"]["message"].startswith("Already balanced")
        assert undo_depth(client) == was


def test_balance_refuses_a_song_with_no_roles_or_no_sections(client, tmp_path):
    refused(post(client, "/song/mix", {"op": "balance"}, MIX), "role")
    with TestClient(create_app(data_dir=tmp_path / "empty")) as c:
        refused(post(c, "/song/mix", {"op": "balance"}, MIX), "no sections")


# --- the master strip -------------------------------------------------------------


def test_the_master_rack_on_the_mix_view_is_the_songs_master_chain(client):
    before = plan_urls(client)
    r = post(client, "/fragment/fx/add", {"scope": "master", "kind": "eq"}, MIX)
    assert r.status_code == 200 and 'id="fx-rack"' in r.text
    assert "Master FX" in r.text and "Layer 1 FX" not in r.text
    assert "song-changed" in trigger(r) and "preview-ready" not in trigger(r)
    assert client.app.state.studio.fx_master[0]["kind"] == "eq"
    post(
        client,
        "/fragment/fx/set",
        {"scope": "master", "index": "0", "name": "low", "value": "6"},
        MIX,
    )
    assert plan_urls(client) != before


# --- saved, bundled, exported -----------------------------------------------------


def test_the_mix_is_saved_and_an_old_state_loads_unchanged(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        use_song(c)
        strip(c, "t1", {"pan": "-0.4", "send.0": "0.5"})
        strip(c, "t2", {"duck": "0.6"})
        want = json.dumps(song(c), sort_keys=True)
        c.app.state.studio.save()
    with TestClient(create_app(data_dir=tmp_path)) as c:
        assert json.dumps(song(c), sort_keys=True) == want
        # ... and it travels in the project file, as any song key does
        project = c.get("/project.json")
        assert project.status_code == 200
        assert '"returns"' in project.text and '"sends"' in project.text
        assert song(c)["returns"][0]["id"] in project.text
    # a song from before the mixer conforms to itself: no key appears
    old = schema.conform(song_dict(), schema.nodes().SONG)
    assert "returns" not in old
    assert not any({"pan", "sends"} & set(t) for t in old["tracks"])
    assert schema.conform(old, schema.nodes().SONG) == old


def test_the_stems_sum_to_the_mix_with_a_pan_and_a_return(client):
    """Each stem carries its own share of the returns, so the stems of a
    song whose returns are linear still add up to the mixdown."""
    strip(client, "t1", {"pan": "-0.5", "send.0": "0.5"})
    strip(client, "t2", {"send.0": "0.3", "send.1": "0.4"})
    strip(client, "td", {"gain": "0.8", "pan": "0.2"})
    svc = client.app.state.svc
    s = song(client)
    mix = svc.song_mixdown(s)
    stems = svc.song_stems(s)
    assert [name for name, _a in stems] == ["drums.wav", "synth-1.wav", "synth-2.wav"]
    total = sum(a for _n, a in stems)
    assert total.shape == mix.shape
    assert np.max(np.abs(total - mix)) < 1e-9
    # and the export is the plan, as ever
    assert np.max(np.abs(mix)) > 0.1
