"""Note recording: the quantizer's shared vectors, and
the record / undo routes (overdub vs replace, fork-first, a new cell, Stop
mid-take). The browser only timestamps — the one quantizer is
studio/record.py, so the vectors run here and nowhere else."""

import json
from pathlib import Path

import pytest

from synthlab.studio import record
from tests.studio import test_song_routes as sr
from tests.studio.test_song_routes import post, refused, song, trigger

client = sr.client  # the song-routes fixture: two sections, fixed ids

VECTORS = json.loads(Path("tests/fixtures/record_vectors.json").read_text("utf-8"))


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_quantizer_vectors(case):
    missed: list = []
    got = record.quantize(
        case["events"],
        bpm=case["bpm"],
        bars=case["bars"],
        steps_per_bar=case["steps_per_bar"],
        grid=case["grid"],
        cycle_bars=case.get("cycle_bars"),
        # a trimmed block (its span, bars): times count from its start and
        # a note outside it is dropped (`missed` says how many)
        block=tuple(case["block"]) if "block" in case else None,
        missed=missed,
    )
    assert [list(x) for x in got] == case["cells"]
    assert len(missed) == case.get("missed", 0)


def test_step_of_is_none_outside_a_block_and_unchanged_without_one():
    shape = {"bpm": 120, "bars": 1, "steps_per_bar": 16, "grid": "1/16"}
    assert record.step_of(4.25, cycle_bars=4, **shape) == 2
    assert record.step_of(4.25, cycle_bars=4, block=(2, 2), **shape) == 2
    assert record.step_of(4.25, cycle_bars=4, block=(1, 3), **shape) == 2
    assert record.step_of(0.25, cycle_bars=4, block=(2, 2), **shape) is None
    assert (
        record.step_of(8.0, cycle_bars=4, block=(2, 2), **shape) is None
    )  # wraps to 0
    assert record.step_of(8.0, cycle_bars=4, block=(0, 1), **shape) == 0


def test_window_steps_cover_a_partial_and_a_whole_loop():
    shape = {"bpm": 120, "bars": 1, "steps_per_bar": 16}
    assert record.window_steps(0, 0.5, **shape) == {0, 1, 2, 3}
    assert record.window_steps(1.75, 2.25, **shape) == {14, 15, 0, 1}
    assert record.window_steps(0.3, 9.0, **shape) == set(range(16))


def _take(client, pid, events, take="t1", **kw):
    data = {"events": json.dumps(events), "take": take, "t0": "0", "t1": "2"} | kw
    return post(client, f"/song/pattern/{pid}/record", data)


def _cells(client, pid):
    return sorted(map(tuple, song(client)["patterns"][pid]["cells"]))


def test_overdub_adds_and_replaces_an_identical_note(client):
    events = [{"midi": 45, "t": 0.5, "vel": 0.7}, {"midi": 36, "t": 0.25}]
    r = _take(client, "p2", events)
    assert r.status_code == 200, r.text[:300]
    assert trigger(r)["song-recorded"] == {"pattern_id": "p2", "take": "t1"}
    assert _cells(client, "p2") == [("A2", 4), ("C2", 2)]  # C2@2 was there
    assert song(client)["patterns"]["p2"]["velocities"]["4"] == 0.7


def test_replace_clears_the_window_on_the_first_post_only(client):
    _take(client, "p2", [{"midi": 45, "t": 0.5}], mode="replace", t0="0", t1="4")
    assert _cells(client, "p2") == [("A2", 4)]
    # the same take's next wrap overdubs: nothing it recorded is cleared
    _take(client, "p2", [{"midi": 48, "t": 4.25}], mode="replace", t0="4", t1="8")
    assert _cells(client, "p2") == [("A2", 4), ("C3", 2)]


def test_stop_mid_take_keeps_the_notes_and_replace_clears_only_its_window(client):
    # stopped 0.5 s in: steps 0..3 of the 2-bar Hook are the window
    r = _take(client, "p2", [{"midi": 45, "t": 0.26}], mode="replace", t1="0.5")
    assert r.status_code == 200
    # C2@2 sat in the window and is cleared; the take's own note is kept
    assert _cells(client, "p2") == [("A2", 2)]


def test_undo_restores_the_pre_take_pattern(client):
    before = json.loads(json.dumps(song(client)["patterns"]["p2"]))
    _take(client, "p2", [{"midi": 45, "t": 0.5}], mode="replace")
    _take(client, "p2", [{"midi": 45, "t": 2.5}])  # same take: one undo step
    assert post(client, "/song/record/undo").status_code == 200
    assert song(client)["patterns"]["p2"] == before
    refused(post(client, "/song/record/undo"), "No take")


def test_project_import_clears_the_take_undo(client):
    """A take's undo restores its pattern by id. After an
    import that id names the imported song's pattern, which Ctrl+Z on a
    take recorded before the import used to overwrite."""
    project = client.get("/project.json").json()  # p2 before any take
    _take(client, "p2", [{"midi": 45, "t": 0.5}], mode="replace")
    project["song"]["patterns"]["p2"]["name"] = "Imported hook"
    imported = json.loads(json.dumps(project["song"]["patterns"]["p2"]))
    r = client.post(
        "/project/import",
        files={"file": ("x.synthlab.json", json.dumps(project), "application/json")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.app.state.studio.take_history == []
    refused(post(client, "/song/record/undo"), "No take")
    assert song(client)["patterns"]["p2"] == imported


@pytest.mark.parametrize("mode", ["revert", "defaults"])
def test_resetting_the_song_clears_the_take_undo(client, mode):
    _take(client, "p2", [{"midi": 45, "t": 0.5}])
    assert client.app.state.studio.take_history
    r = post(client, "/fragment/reset", {"scope": "song", "mode": mode})
    assert r.status_code == 200, r.text[:300]
    assert client.app.state.studio.take_history == []
    refused(post(client, "/song/record/undo"), "No take")


def test_resetting_another_scope_keeps_the_take_undo(client):
    s = client.app.state.studio
    _take(client, "p2", [{"midi": 45, "t": 0.5}])
    s.revert("sound")
    assert len(s.take_history) == 1


def test_record_into_a_shared_pattern_with_unique_forks_first(client):
    riff = json.loads(json.dumps(song(client)["patterns"]["p1"]))
    cell = {"unique": "1", "section_id": "s2", "track_id": "t1"}
    r = _take(client, "p1", [{"midi": 45, "t": 0.5}], **cell)
    new = trigger(r)["song-recorded"]["pattern_id"]
    sg = song(client)
    assert new != "p1" and sg["sections"][1]["cells"]["t1"] == new
    assert sg["sections"][0]["cells"]["t1"] == "p1" and sg["patterns"]["p1"] == riff
    assert ["A2", 4] in sg["patterns"][new]["cells"]
    # later posts of the take follow the fork, whatever the path names
    _take(client, "p1", [{"midi": 48, "t": 1.0}], **cell)
    assert ["C3", 8] in song(client)["patterns"][new]["cells"]
    assert song(client)["patterns"]["p1"] == riff
    post(client, "/song/record/undo")
    sg = song(client)
    assert sg["sections"][1]["cells"]["t1"] == "p1" and new not in sg["patterns"]


def test_record_into_an_empty_cell_makes_a_pattern(client):
    cell = {"section_id": "s1", "track_id": "t2"}
    r = _take(client, "new", [{"midi": 45, "t": 0.5}], **cell)
    pid = trigger(r)["song-recorded"]["pattern_id"]
    assert song(client)["sections"][0]["cells"]["t2"] == pid
    assert song(client)["patterns"][pid]["cells"] == [["A2", 4]]
    drums = {"section_id": "s1", "track_id": "td"}
    refused(_take(client, "new", [], take="t9", **drums))


@pytest.mark.parametrize(
    "data",
    [
        {"events": "not json"},
        {"events": json.dumps({"midi": 1})},
        {"events": json.dumps([{"midi": 999, "t": 0}])},
        {"events": json.dumps([{"midi": True, "t": 0}])},
        {"events": json.dumps([{"midi": 45, "t": "nan"}])},
        {"events": json.dumps([{"midi": 45, "t": 0}] * 600)},
        {"take": "../x"},
        {"take": ""},
        {"mode": "erase"},
        {"grid": "1/7"},
        {"t1": "inf"},
    ],
)
def test_hostile_record_posts_are_refused(client, data):
    before = json.loads(json.dumps(song(client)))
    base = {"events": "[]", "take": "t1", "t0": "0", "t1": "2"}
    r = post(client, "/song/pattern/p2/record", base | data)
    assert r.status_code == 422
    assert song(client) == before
