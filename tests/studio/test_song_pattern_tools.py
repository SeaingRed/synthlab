"""The Song drawer's pattern tools (`/song/pattern/{id}/tool/{op}`): the
same edits as Perform's grids (`patternops.notes_tool` / `drums_tool`),
on a pool pattern every section using it follows."""

import json

from tests.studio.test_song_routes import changed, client, post, song  # noqa: F401


def test_shift_moves_the_pool_patterns_notes(client):  # noqa: F811
    r = post(client, "/song/pattern/p1/tool/shift_right")
    assert r.status_code == 200
    assert sorted(map(tuple, song(client)["patterns"]["p1"]["cells"])) == [
        ("A1", 1),
        ("E2", 5),
    ]
    # both sections play Riff: both re-render
    assert set(changed(r)) == {"s1", "s2"}


def test_transpose_walks_the_songs_key(client):  # noqa: F811
    post(client, "/song/pattern/p1/tool/up")
    cells = sorted(map(tuple, song(client)["patterns"]["p1"]["cells"]))
    # A minor from A1: A1 -> B1, E2 -> F2
    assert cells == [("B1", 0), ("F2", 4)]


def test_doubling_grows_the_sections_that_use_it(client):  # noqa: F811
    r = post(client, "/song/pattern/p2/tool/duplicate")
    s = song(client)
    assert s["patterns"]["p2"]["bars"] == 4
    assert sorted(map(tuple, s["patterns"]["p2"]["cells"])) == [("C2", 2), ("C2", 34)]
    verse = next(x for x in s["sections"] if x["id"] == "s2")
    assert verse["bars"] == 4
    assert "grew" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def test_a_pattern_at_its_longest_refuses_to_double(client):  # noqa: F811
    """A pool pattern doubles to 8 bars while bars x steps fits the grid
    (`limits.PATTERN_BAR_CHOICES`: 8 x 16 = 128 steps), and no further."""
    post(client, "/song/pattern/p2/tool/duplicate")
    r = post(client, "/song/pattern/p2/tool/duplicate")
    assert r.status_code == 200
    assert song(client)["patterns"]["p2"]["bars"] == 8
    r = post(client, "/song/pattern/p2/tool/duplicate")
    assert r.status_code == 422 and "double" in r.text and "8" in r.text


def test_drum_patterns_take_the_drum_tools(client):  # noqa: F811
    post(client, "/song/pattern/pd/tool/reverse")
    assert sorted(map(tuple, song(client)["patterns"]["pd"]["drums"])) == [
        ("kick", 15),
        ("snare", 7),
    ]
    r = post(client, "/song/pattern/pd/tool/up")  # a notes-only tool
    assert r.status_code == 422


def test_the_drawer_draws_the_tools(client):  # noqa: F811
    post(client, "/song/drawer", {"pattern_id": "p1"})
    html = client.get("/view/song").text
    assert 'hx-post="/song/pattern/p1/tool/shift_left"' in html
    assert 'hx-post="/song/pattern/p1/tool/oct_up"' in html
