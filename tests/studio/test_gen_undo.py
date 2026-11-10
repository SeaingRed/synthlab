"""Every generator and every auto-arrange is
ONE undo step however often it is rolled again — "Again" replaces the
previous roll's step, it does not stack — and its result asks the page to
play (`song-autoplay`).

The acceptance journey of section 6.5, on the song's undo ring: a starter,
three rolls of the bass, one Ctrl+Z gives the first bass back, one more the
song before the starter."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from synthlab.studio.server import create_app

from .test_song_routes import post, song, use_song


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _undo(client) -> dict:
    return client.app.state.studio.song_undo_state()


def _trigger(r) -> dict:
    return json.loads(r.headers.get("hx-trigger", "{}"))


def test_three_rolls_of_the_bass_are_one_step_and_the_starter_is_another(client):
    use_song(client)
    before = copy.deepcopy(song(client))
    post(client, "/song/start", {"style": "trance", "seed": "1"})
    started = copy.deepcopy(song(client))
    mid = next(t["id"] for t in started["tracks"] if t["name"] == "Mid Bass")
    seen = []
    for seed in ("11", "12", "13"):
        r = post(client, "/song/gen", {"track_id": mid, "kind": "again", "seed": seed})
        assert r.status_code == 200
        assert "song-autoplay" in _trigger(r)  # what was made plays by itself
        seen.append(copy.deepcopy(song(client)))
    assert seen[0] != seen[1] != seen[2] != started
    # the bass changed and nothing else: every other track's cells are equal
    bass = {t["id"] for t in started["tracks"] if t.get("role") == "bass"}
    for sec_a, sec_b in zip(started["sections"], seen[2]["sections"], strict=True):
        assert sec_a == sec_b
    moved = {p for p, v in seen[2]["patterns"].items() if v != started["patterns"][p]}
    owners = {
        t for x in started["sections"] for t, p in x["cells"].items() if p in moved
    }
    assert moved and owners <= bass
    assert _undo(client)["depth"] == 2
    assert _undo(client)["undo"] == "generate: again"
    post(client, "/song/undo")  # one Ctrl+Z after three rolls: the first bass
    assert song(client) == started
    post(client, "/song/undo")  # one more: the song before the starter
    assert song(client) == before
    assert _undo(client)["depth"] == 0


def test_a_roll_after_another_edit_is_a_new_step(client):
    post(client, "/song/start", {"style": "garage", "seed": "2"})
    s = song(client)
    sub = next(t["id"] for t in s["tracks"] if t["name"] == "Sub")
    keys = next(t["id"] for t in s["tracks"] if t["name"] == "Keys")
    post(client, "/song/gen", {"track_id": sub, "seed": "1"})
    post(client, "/song/gen", {"track_id": sub, "seed": "2"})
    assert _undo(client)["depth"] == 2
    post(client, "/song/gen", {"track_id": keys, "seed": "1"})  # another part
    post(client, "/song/gen", {"track_id": keys, "seed": "2"})
    assert _undo(client)["depth"] == 3
    post(client, "/song/track/" + keys + "/mix", {"mute": "on"})  # an edit between
    post(client, "/song/gen", {"track_id": keys, "seed": "3"})
    assert _undo(client)["depth"] == 5
    # an undo closes the step a roll was joining
    post(client, "/song/undo")
    post(client, "/song/gen", {"track_id": keys, "seed": "4"})
    post(client, "/song/gen", {"track_id": keys, "seed": "5"})
    assert _undo(client)["depth"] == 5


def test_busier_sparser_and_a_new_generator_join_the_parts_step(client):
    use_song(client)
    post(client, "/song/gen", {"track_id": "t1", "kind": "lead", "seed": "1"})
    post(client, "/song/gen", {"track_id": "t1", "kind": "busier"})
    post(client, "/song/gen", {"track_id": "t1", "kind": "sparser"})
    post(client, "/song/gen", {"track_id": "t1", "kind": "arp", "seed": "2"})
    post(client, "/song/gen", {"track_id": "t1", "seed": "3"})
    assert _undo(client)["depth"] == 1
    was = copy.deepcopy(song(client))
    use_song(client)
    fresh = copy.deepcopy(song(client))
    use_song(client, was)
    assert was != fresh


def test_starting_and_arranging_again_replace_their_own_step(client):
    use_song(client)
    first = copy.deepcopy(song(client))
    for style, seed in (("trance", "1"), ("garage", "2"), ("filter_house", "3")):
        r = post(client, "/song/start", {"style": style, "seed": seed})
        assert _trigger(r)["song-autoplay"] == {"head": True}
    # one step, named for the starter it holds now
    assert _undo(client)["depth"] == 1 and _undo(client)["undo"] == "start: daft rock"
    post(client, "/song/undo")
    assert song(client) == first
    for seed in ("1", "2", "3"):
        post(client, "/song/arrange", {"source": "loop", "seed": seed})
    assert _undo(client)["depth"] == 1
    post(client, "/song/undo")
    assert song(client) == first
    # the chord strip's roll, however often
    for seed in ("1", "2", "3", "4"):
        post(client, "/song/chords", {"op": "roll", "seed": seed})
    assert _undo(client)["depth"] == 1


def test_a_refused_generator_is_no_step(client):
    use_song(client)
    assert (
        post(client, "/song/gen", {"track_id": "t1", "kind": "kazoo"}).status_code
        == 422
    )
    assert post(client, "/song/start", {"style": "polka"}).status_code == 422
    assert _undo(client)["depth"] == 0
