"""Mic takes onto audio tracks: the upload lands in its
section at its bar, is capped and kept, hostile uploads are refused, a
take plays in the song export at its offset, rename, and calibration."""

import io
import json
import wave

import numpy as np
import pytest

from synthlab.studio import limits
from synthlab.studio.clips import save_clip
from tests.studio import test_song_routes as sr
from tests.studio.test_song_routes import post, refused, song, trigger

client = sr.client  # the song-routes fixture: Vox ("ta") is an audio track


def _wav(x, rate=44100) -> bytes:
    pcm = (np.clip(np.asarray(x, dtype=float), -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _take(client, data, body):
    return client.post(
        "/song/take",
        data=data,
        files={"file": ("take.wav", body, "audio/wav")},
        headers=sr.HX,
    )


def _takes(client, sid):
    sec = next(x for x in song(client)["sections"] if x["id"] == sid)
    return sec["audio"]


def test_a_take_lands_on_its_track_in_its_section_at_its_bar(client):
    r = _take(
        client,
        {"section_id": "s2", "track_id": "ta", "at_bar": "1"},
        _wav([0.1] * 4410),
    )
    assert r.status_code == 200, r.text[:300]
    (a,) = _takes(client, "s2")
    assert a["track_id"] == "ta" and a["at_bar"] == 1 and a["clip"].startswith("take-")
    assert "s2" in trigger(r)["song-changed"]["sections"]
    assert "Kept" in trigger(r)["studio-toast"]["message"]
    # a bar past the section is clamped into it; a second take gets its own name
    _take(
        client,
        {"section_id": "s2", "track_id": "ta", "at_bar": "99"},
        _wav([0.1] * 441),
    )
    names = [x["clip"] for x in _takes(client, "s2")]
    assert _takes(client, "s2")[1]["at_bar"] == 1 and len(set(names)) == 2


def test_a_long_take_is_cut_at_the_cap_and_kept(client):
    rate = 8000
    long = _wav(np.full(int(rate * (limits.MAX_CLIP_SECONDS + 5)), 0.1), rate)
    r = _take(client, {"section_id": "s1", "track_id": "ta"}, long)
    assert r.status_code == 200
    assert "first 30 s" in trigger(r)["studio-toast"]["message"]
    (a,) = _takes(client, "s1")
    path = client.app.state.studio.data_dir / "clips" / f"{a['clip']}.wav"
    with wave.open(str(path)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(
            limits.MAX_CLIP_SECONDS
        )


@pytest.mark.parametrize(
    "data,body",
    [
        ({"section_id": "s1", "track_id": "ta"}, b"not a wav"),
        ({"section_id": "s1", "track_id": "ta"}, _wav([0.1] * 100, rate=1)),
        ({"section_id": "s1", "track_id": "ta"}, _wav([], rate=44100)),
        ({"section_id": "s1", "track_id": "gone"}, _wav([0.1] * 100)),  # deleted
        ({"section_id": "s1", "track_id": "t1"}, _wav([0.1] * 100)),  # a synth track
        ({"section_id": "nope", "track_id": "ta"}, _wav([0.1] * 100)),
    ],
)
def test_hostile_takes_are_refused(client, data, body):
    before = json.loads(json.dumps(song(client)))
    refused(_take(client, data, body))
    assert song(client) == before


def test_an_oversize_take_is_refused(client, monkeypatch):
    monkeypatch.setattr(limits, "MAX_UPLOAD_BYTES", 1000)
    r = _take(client, {"section_id": "s1", "track_id": "ta"}, _wav([0.1] * 5000))
    assert r.status_code in (413, 422)
    assert _takes(client, "s1") == []


def test_a_take_plays_in_the_song_export_at_its_offset(client):
    """Only the take sounds: one click at bar 2 of the first section
    (120 bpm: 2 s in) must peak in the export 2 s in."""
    s = client.app.state.studio
    sg = song(client)
    sg["sections"] = [{**sg["sections"][0], "cells": {}, "audio": []}]
    sr.use_song(client, sg)
    click = np.zeros(2205)
    click[0] = 0.9
    r = _take(
        client, {"section_id": "s1", "track_id": "ta", "at_bar": "1"}, _wav(click)
    )
    assert r.status_code == 200
    wav = client.get("/export/song.wav")
    assert wav.status_code == 200
    with wave.open(io.BytesIO(wav.content)) as w:
        rate, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, ch)
    peak = int(np.argmax(np.abs(x[:, 0])))
    assert abs(peak - 2 * rate) <= 2, (peak, 2 * rate)
    assert s.song["sections"][0]["audio"][0]["at_bar"] == 1


def _clip(client, name="loop", seconds=0.5) -> str:
    from synthlab.studio.clips import save_clip

    save_clip(
        client.app.state.studio.data_dir, name, np.full(int(seconds * 44100), 0.2)
    )
    return name


def test_a_clip_from_the_store_is_placed_like_a_take(client):
    """The half `/song/take` never had: no upload, a clip that exists."""
    name = _clip(client)
    r = post(client, "/song/section/s2/clip", {"clip": name, "at_bar": "1"})
    assert r.status_code == 200, r.text[:300]
    (a,) = _takes(client, "s2")
    assert a == {"track_id": "ta", "clip": name, "at_bar": 1, "gain": 1.0}
    assert "s2" in trigger(r)["song-changed"]["sections"]
    assert "Verse, bar 2" in trigger(r)["studio-toast"]["message"]
    # named track, bar clamped into the section, the clip placed twice
    post(
        client,
        "/song/section/s2/clip",
        {"clip": name, "track_id": "ta", "at_bar": "99"},
    )
    bars = next(x for x in song(client)["sections"] if x["id"] == "s2")["bars"]
    assert [x["at_bar"] for x in _takes(client, "s2")] == [1, bars - 1]


def test_a_song_with_no_audio_track_grows_one_for_a_placed_clip(client):
    name = _clip(client)
    sg = json.loads(json.dumps(song(client)))
    sg["tracks"] = [t for t in sg["tracks"] if t["kind"] != "audio"]
    sr.use_song(client, sg)
    assert post(client, "/song/section/s1/clip", {"clip": name}).status_code == 200
    track = song(client)["tracks"][-1]
    assert track["kind"] == "audio" and track["name"] == "Audio"
    assert _takes(client, "s1")[0]["track_id"] == track["id"]


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/song/section/s1/clip", {"clip": "no such clip"}),
        ("/song/section/s1/clip", {"clip": ""}),
        ("/song/section/s1/clip", {"clip": "../state"}),
        ("/song/section/nope/clip", {"clip": "loop"}),
        ("/song/section/s1/clip", {"clip": "loop", "track_id": "t1"}),  # a synth track
        ("/song/section/s1/clip", {"clip": "loop", "track_id": "gone"}),
        ("/song/section/s1/clip", {"clip": "loop", "at_bar": "two"}),
        ("/song/section/s1/clip", {"clip": "loop", "at_bar": "9" * 5000}),
    ],
)
def test_a_clip_placed_wrongly_is_refused_and_changes_nothing(client, path, data):
    _clip(client)
    before = json.loads(json.dumps(song(client)))
    refused(post(client, path, data))
    assert song(client) == before
    assert (client.app.state.studio.data_dir / "clips" / "loop.wav").is_file()


def test_a_full_section_refuses_one_more_placed_clip(client):
    name = _clip(client)
    for _ in range(limits.MAX_LANE_CLIPS):
        assert post(client, "/song/section/s1/clip", {"clip": name}).status_code == 200
    refused(post(client, "/song/section/s1/clip", {"clip": name}), "at most")
    assert len(_takes(client, "s1")) == limits.MAX_LANE_CLIPS


def test_a_short_stopped_take_is_kept_and_renames_inline(client):
    _take(client, {"section_id": "s1", "track_id": "ta"}, _wav([0.1] * 300))
    old = _takes(client, "s1")[0]["clip"]
    r = post(client, f"/song/take/{old}/rename", {"name": "vocal one"})
    assert r.status_code == 200
    assert _takes(client, "s1")[0]["clip"] == "vocal one"
    refused(post(client, "/song/take/nope/rename", {"name": "x"}))
    refused(post(client, "/song/take/vocal one/rename", {"name": "  "}))


def test_latency_calibration_is_stored_per_machine_and_clamped(client):
    assert client.get("/song/calibration.json").json() == {"latency_ms": 0.0}
    assert post(client, "/song/calibration", {"latency_ms": "42"}).json() == {
        "latency_ms": 42.0
    }
    data_dir = client.app.state.studio.data_dir
    assert json.loads((data_dir / "calibration.json").read_text()) == {
        "latency_ms": 42.0
    }
    post(client, "/song/calibration", {"latency_ms": "1e9"})
    assert client.get("/song/calibration.json").json()["latency_ms"] <= 500
    refused(post(client, "/song/calibration", {"latency_ms": "abc"}))
    (data_dir / "calibration.json").write_text("{broken")
    assert client.get("/song/calibration.json").json() == {"latency_ms": 0.0}


# --- a placed take, moved and removed (POST /song/take/edit) ------------------------


def _place(client, clip="loop", sid="s1", at_bar=0):
    save_clip(client.app.state.studio.data_dir, clip, np.full(4410, 0.2))
    r = post(client, f"/song/section/{sid}/clip", {"clip": clip, "at_bar": str(at_bar)})
    assert r.status_code == 200, r.text[:200]


def _edit(client, op, sid="s1", at_bar=0, clip="loop", **more):
    data = {"op": op, "section_id": sid, "track_id": "ta", "clip": clip}
    return post(client, "/song/take/edit", {**data, "at_bar": str(at_bar), **more})


def _undo_title(client) -> str | None:
    return client.app.state.studio.song_undo_state()["undo"]


def test_a_placed_take_moves_within_its_section_in_one_undo_step(client):
    _place(client)
    r = _edit(client, "move", to_bar="1")
    assert r.status_code == 200, r.text[:200]
    (a,) = _takes(client, "s1")
    assert (a["clip"], a["at_bar"], a["track_id"]) == ("loop", 1, "ta")
    assert trigger(r)["studio-toast"]["message"] == "Moved loop to Intro, bar 2."
    assert "s1" in trigger(r)["song-changed"]["sections"]
    assert _undo_title(client) == "move take"
    assert post(client, "/song/undo").status_code == 200
    assert _takes(client, "s1")[0]["at_bar"] == 0


def test_a_take_moves_to_another_section_and_keeps_its_gain(client):
    _place(client)
    song(client)["sections"][0]["audio"][0]["gain"] = 0.5
    r = _edit(client, "move", to_section="s2", to_bar="1")
    assert r.status_code == 200
    assert _takes(client, "s1") == []
    (a,) = _takes(client, "s2")
    assert (a["at_bar"], a["gain"], a["track_id"]) == (1, 0.5, "ta")
    assert set(trigger(r)["song-changed"]["sections"]) == {"s1", "s2"}


def test_the_bar_is_clamped_into_the_section_and_the_menu_counts_from_one(client):
    _place(client)
    _edit(client, "move", to_bar="99")  # Intro is 2 bars
    assert _takes(client, "s1")[0]["at_bar"] == 1
    # the take menu's field is 1-based (`bar_base=1`): "bar 1" is at_bar 0
    _edit(client, "move", at_bar=1, to_bar="1", bar_base="1")
    assert _takes(client, "s1")[0]["at_bar"] == 0
    _edit(client, "move", to_bar="-4")
    assert _takes(client, "s1")[0]["at_bar"] == 0


def test_a_move_onto_itself_is_no_undo_step(client):
    _place(client)
    before = _undo_title(client)
    r = _edit(client, "move", to_bar="0")
    assert r.status_code == 200 and _undo_title(client) == before == "place clip"
    assert trigger(r)["song-changed"]["sections"] == []


def test_a_placed_take_is_removed_and_its_clip_stays_in_the_store(client):
    _place(client)
    r = _edit(client, "remove")
    assert r.status_code == 200
    assert _takes(client, "s1") == []
    assert trigger(r)["studio-toast"]["message"] == "Removed loop from Intro."
    assert _undo_title(client) == "remove take"
    data_dir = client.app.state.studio.data_dir
    assert (data_dir / "clips" / "loop.wav").is_file()
    post(client, "/song/undo")  # Ctrl+Z brings the reference back
    assert [a["clip"] for a in _takes(client, "s1")] == ["loop"]


def test_two_takes_alike_are_the_first_match(client):
    _place(client)
    _place(client)
    assert len(_takes(client, "s1")) == 2
    _edit(client, "remove")
    assert len(_takes(client, "s1")) == 1


@pytest.mark.parametrize(
    "data, said",
    [
        ({"op": "move", "at_bar": "1"}, "no longer there"),  # no take at that bar
        ({"op": "remove", "clip": "nope"}, "no longer there"),
        ({"op": "remove", "section_id": "zz"}, "No such section"),
        ({"op": "move", "to_section": "zz"}, "No such section"),
        ({"op": "move", "to_track": "t1"}, "audio track"),  # a synth track
        ({"op": "move", "to_track": "zz"}, "No such track"),
        ({"op": "move", "at_bar": "x"}, "whole number"),
        ({"op": "move", "to_bar": "x"}, "whole number"),
    ],
)
def test_a_take_edit_that_cannot_be_done_is_refused_and_changes_nothing(
    client, data, said
):
    _place(client)
    before = json.dumps(song(client))
    base = {"section_id": "s1", "track_id": "ta", "clip": "loop", "at_bar": "0"}
    r = post(client, "/song/take/edit", {**base, **data})
    refused(r, said)
    assert json.dumps(song(client)) == before


def test_an_unknown_op_is_a_bare_422(client):
    _place(client)
    assert _edit(client, "explode").status_code == 422
    assert post(client, "/song/take/edit", {}).status_code == 422


def test_a_full_section_refuses_a_seventeenth_take_moved_in(client):
    _place(client)  # the one that will try to move
    for _ in range(limits.MAX_LANE_CLIPS):
        _place(client, sid="s2")
    r = _edit(client, "move", to_section="s2")
    refused(r, "at most")
    assert len(_takes(client, "s1")) == 1
    # inside its own (full) section a take still moves: nothing is added
    r = _edit(client, "move", sid="s2", to_bar="1")
    assert r.status_code == 200
    assert _takes(client, "s2")[0]["at_bar"] == 1


def test_the_take_menu_has_a_bar_field_and_remove(client):
    _place(client)
    html = client.get("/view/song").text
    menu = html[html.index('id="takemenu-s1-ta-0"') :]
    menu = menu[: menu.index("</td>")]
    assert 'hx-post="/song/take/edit" hx-trigger="change"' in menu
    assert 'name="to_bar" min="1" max="2" step="1" value="1"' in menu
    assert '"bar_base": "1"' in menu and '"op": "move"' in menu
    assert '"op": "remove"' in menu and "take-remove" in menu
    assert menu.count('"clip": "loop"') == 2
    # the move, the remove — and "Effects and lanes", the drawer on its row
    for field in ('"section_id": "s1"', '"track_id": "ta"'):
        assert menu.count(field) == 3, field
    assert 'class="btn btn-s menu-btn take-fx" hx-post="/song/drawer"' in menu
    assert '"at_bar": "0"' in menu
    # the chip drags (songdrag.js) and says how
    chip = html[html.index('class="take-chip"') :].split(">")[0]
    assert 'draggable="true"' in chip and 'data-clip="loop"' in chip
