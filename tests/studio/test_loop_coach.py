"""Perform's ✦ Coach: `loopcoach.py` (the rules, the options,
the proposal, Accept / Reverse), `loopchat.py` (the talk) and
`routes/coach.py`.

The loop it is checked on is a typical hand-made one (`seed`): a 4-bar lead riff
in A major with notes off the key (F, C, D#, G), every velocity the same,
a note the roll does not show, a bass lane and a beat. No test reaches
Gemini: the talk is checked against a stand-in (`gemini`), as the clip
hub's morph is (`takecoach.client_for` / `load_config`, read at call
time; `tests/conftest.py` makes the real client unbuildable).
"""

from __future__ import annotations

import io
import json
import re
import types
import wave
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import loopchat, loops, takecoach
from synthlab.studio import loopcoach as lc
from synthlab.studio.agent.config import AgentConfig
from synthlab.studio.server import create_app
from synthlab.studio.state.model import grid_rows
from synthlab.synth import Patch

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
RIFF = ["A3", "C#4", "E4", "F4", "E4", "C4", "B3", "A3"]  # F and C: off A major
BASS = [["A1", s] for s in (0, 8, 16, 24, 32, 40, 48, 56)]
BEAT = (
    [["kick", s] for s in (0, 4, 8, 12)]
    + [["snare", 4], ["snare", 12]]
    + [["hat", s] for s in (2, 6, 10, 14)]
)


def seed(state) -> None:
    """A typical loop: A major, 4 bars of 16; the riff (F, C off the key,
    a D# and a G too), every velocity the same; a bass on lane 2; a beat;
    the roll on the riff's octave with one note above it."""
    cells = [[p, b * 16 + i * 2] for b in range(4) for i, p in enumerate(RIFF)]
    cells += [["D#4", 53], ["G4", 59]]
    state.arrangement = replace(
        state.arrangement,
        root="A2",
        scale_name="major",
        bars=4,
        steps_per_bar=16,
        cells=cells,
        cuts=[],
        velocities={},
        slides=[],
        drums=[list(c) for c in BEAT],
        drum_bars=1,
        drum_steps=16,
        drum_vel={},
        drum_hits={},
    )
    state.add_layer(Patch(name="Sub bass"), [], select=False, slot=1)
    state.set_follow(1, None)
    state.layers[1]["cells"] = [list(c) for c in BASS]
    state.show_notes()
    rows = grid_rows(state.arrangement, state.grid_span, state.grid_oct)
    top = max(rows, key=lambda r: lc.midi(r))
    hidden = lc.name_of(lc.midi(top) + 12 + (2 if lc.midi(top) % 12 == 9 else 0))
    state.arrangement = replace(state.arrangement, cells=cells + [[hidden, 30]])


@pytest.fixture
def client(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path))
    seed(c.app.state.studio)
    return c


def _st(client):
    return client.app.state.studio


def _ctx(client) -> dict:
    return lc.context(_st(client))


def _ids(client) -> set[str]:
    return {f["id"] for f in _ctx(client)["findings"]}


# --- the doc --------------------------------------------------------------------------


def test_the_doc_reads_every_own_lane_and_the_beat_and_writes_them_back(client):
    s = _st(client)
    doc = lc.read(s)
    assert set(doc["lanes"]) == {"0", "1"}  # the edited lane, the bass
    assert doc["lanes"]["1"]["cells"] == sorted(BASS, key=lambda c: (c[1], c[0]))
    assert len(doc["drums"]["drums"]) == len(BEAT)
    doc["lanes"]["1"]["cells"] = [["A1", 0]]
    doc["drums"]["drums"] = [["kick", 0]]
    lc.write(s, doc)
    assert s.layers[1]["cells"] == [["A1", 0]]
    assert s.arrangement.drums == [["kick", 0]]
    assert lc.fingerprint(lc.read(s)) == lc.fingerprint(doc)


def test_a_following_lane_is_no_lane_of_its_own(client):
    s = _st(client)
    s.set_follow(1, -1)
    assert set(lc.read(s)["lanes"]) == {"0"}


def test_notes_and_back_keep_runs_cuts_and_velocities():
    lane = lc._lane_doc(
        [["A3", 0], ["A3", 1], ["A3", 2], ["A3", 3], ["C4", 4]],
        [["A3", 2]],
        {"0": 0.5, "2": 0.7},
        [],
    )
    ns = lc.notes(lane)
    assert [(n.pitch, n.start, n.n) for n in ns] == [
        ("A3", 0, 2),
        ("A3", 2, 2),
        ("C4", 4, 1),
    ]
    back = lc.with_notes(lane, ns)
    assert back["cells"] == lane["cells"] and back["cuts"] == [["A3", 2]]
    assert back["velocities"] == {"0": 0.5, "2": 0.7}


# --- the rules ------------------------------------------------------------------------


def test_the_owners_loop_is_assessed_with_a_why_and_a_fix(client):
    ctx = _ctx(client)
    ids = {f["id"] for f in ctx["findings"]}
    assert {"off_key:0", "hidden:0", "velocity:0", "velocity:drums"} <= ids
    off = next(f for f in ctx["findings"] if f["id"] == "off_key:0")
    assert (
        "A major" in off["title"] and "F" in off["title"] and off["fix"] and off["why"]
    )
    hidden = next(f for f in ctx["findings"] if f["id"] == "hidden:0")
    assert "1 note outside the roll's rows" in hidden["title"]
    assert hidden["show"] and not hidden["fix"]  # it plays: the window's to fix
    assert ctx["fixable"] >= 3


def _doc(cells, *, cuts=(), vel=None, drums=(), lanes=None):
    doc = {
        "lanes": {"0": lc._lane_doc(cells, cuts, vel or {}, [])},
        "drums": {"drums": [list(c) for c in drums], "drum_vel": {}, "drum_hits": {}},
    }
    for k, v in (lanes or {}).items():
        doc["lanes"][k] = lc._lane_doc(v, [], {}, [])
    return doc


def _facts(**kw) -> lc.Facts:
    base = dict(
        total=64,
        spb=16,
        bars=4,
        beat=4,
        drum_total=16,
        drum_spb=16,
        keys=frozenset(lc.takeclean._in_key("A2", "major")),
        key="A major",
        root="A2",
        scale_name="major",
        chords=None,
        rows=[lc.name_of(m) for m in range(45, 70)],
        active=0,
    )
    return lc.Facts(**(base | kw))


def _fixed(rule, doc, f, key="0"):
    got = rule(doc, f, key)
    assert got is not None and got.after is not None
    assert rule(got.after, f, key) is None, "the fix leaves the thing it fixed"
    return got


def test_off_key_notes_move_to_the_nearest_note_of_the_key():
    got = _fixed(lc.r_off_key, _doc([["F4", 0], ["A3", 2], ["C4", 4]]), _facts())
    assert sorted(c[0] for c in got.after["lanes"]["0"]["cells"]) == ["A3", "B3", "E4"]


def test_off_chord_held_notes_move_to_a_chord_tone():
    song = {
        "root": "A2",
        "scale_name": "major",
        "chords": lc.chordtrack.from_degrees((1,), 4),
    }
    chords = lc.chordtrack.resolve(song, None, 4, 16)  # A major under every step
    doc = _doc(
        [["B3", 0], ["B3", 1], ["B3", 2], ["B3", 3], ["D4", 5]]
    )  # D4 off-beat: passing
    got = _fixed(lc.r_off_chord, doc, _facts(chords=chords))
    pitches = {c[0] for c in got.after["lanes"]["0"]["cells"]}
    assert "B3" not in pitches and "D4" in pitches


def test_notes_past_the_end_are_tidied_and_notes_off_the_rows_are_shown():
    f = _facts(rows=["A3", "B3", "C#4"])
    got = lc.r_hidden(_doc([["A3", 0], ["A3", 70], ["E5", 4]]), f, "0")
    assert got.show and got.after["lanes"]["0"]["cells"] == [["A3", 0], ["E5", 4]]
    only_rows = lc.r_hidden(_doc([["A3", 0], ["E5", 4]]), f, "0")
    assert only_rows.show and only_rows.after is None


def test_a_semitone_rub_inside_a_lane_loses_its_odd_note():
    got = _fixed(lc.r_rub, _doc([["E4", 0], ["F4", 0], ["A3", 4]]), _facts())
    assert ["F4", 0] not in got.after["lanes"]["0"]["cells"]


def test_a_line_whose_held_note_runs_under_the_next_is_cut_where_it_starts():
    cells = [["A3", s] for s in range(0, 6)] + [["C#4", 4], ["E4", 8], ["A3", 12]]
    got = _fixed(lc.r_overlap, _doc(cells), _facts())
    assert ["A3", 4] not in got.after["lanes"]["0"]["cells"]


def test_a_stray_sixteenth_snaps_to_the_grid_the_rest_sit_on():
    cells = [["A3", s] for s in (0, 2, 4, 6, 8, 10, 12)] + [["E4", 15]]
    got = _fixed(lc.r_off_grid, _doc(cells), _facts())
    assert ["E4", 15] not in got.after["lanes"]["0"]["cells"]
    assert ["E4", 16] in got.after["lanes"]["0"]["cells"]  # the nearest eighth


def test_flat_velocities_get_an_accent_and_erratic_ones_are_drawn_in():
    cells = [["A3", s] for s in range(0, 16, 2)]
    flat = _fixed(lc.r_velocity, _doc(cells), _facts())
    v = flat.after["lanes"]["0"]["velocities"]
    assert v["0"] > v["4"] > v["2"]  # the one, a beat, an off-beat eighth
    wild = _doc(cells, vel={str(s): (1.0 if s % 4 else 0.2) for s in range(0, 16, 2)})
    tamed = lc.r_velocity(wild, _facts(), "0")
    assert tamed and "all over the place" in tamed.title


def test_one_bar_that_breaks_the_pattern_is_put_back_and_a_turnaround_is_left():
    bar = [["A3", 0], ["C#4", 4], ["E4", 8]]
    cells = [[p, s + b * 16] for b in range(4) for p, s in bar]
    slip = [c for c in cells if c != ["C#4", 20]] + [["D4", 20]]
    got = _fixed(lc.r_repeat, _doc(slip), _facts())
    assert "Bar 2" in got.title
    turn = [c for c in cells if c[1] < 48] + [
        ["B3", 50],
        ["D4", 54],
        ["F#4", 58],
        ["G#4", 62],
    ]
    assert lc.r_repeat(_doc(turn), _facts(), "0") is None


def test_an_empty_bar_is_filled_and_a_crowded_one_thinned():
    cells = [["A3", s] for b in (0, 1, 3) for s in range(b * 16, b * 16 + 16, 4)]
    got = _fixed(lc.r_bars, _doc(cells), _facts())
    assert "Bar 3 is empty" in got.title
    busy = [["A3" if s % 2 else "C#4", s] for s in range(0, 16)] + [
        ["E4", s] for s in range(16, 64, 4)
    ]
    crowded = lc.r_bars(_doc(busy), _facts(), "0")
    assert crowded and "crowded" in crowded.title


def test_the_seam_leap_moves_the_last_note_an_octave_closer():
    cells = [["A3", 0], ["C#4", 4], ["E4", 8], ["E5", 60]]
    got = _fixed(lc.r_seam, _doc(cells), _facts())
    assert ["E4", 60] in got.after["lanes"]["0"]["cells"]


def test_two_lanes_in_one_register_move_the_edited_one_an_octave():
    lead = [["A3", s] for s in range(0, 32, 2)]
    doc = _doc(lead, lanes={"1": [["C#4", s] for s in range(0, 32, 2)]})
    got = lc.r_register(doc, _facts(), "0")
    assert got and {c[0] for c in got.after["lanes"]["0"]["cells"]} == {"A2"}
    assert (
        lc.r_register(doc, _facts(), "1") is None
    )  # said once, on the lane that moves


def test_a_bass_a_sixteenth_off_the_kick_moves_onto_it():
    bass = [["A1", 1], ["A1", 9], ["E2", 17]]
    doc = _doc([["A3", 0]], drums=[["kick", 0], ["kick", 8]], lanes={"1": bass})
    got = _fixed(lc.r_bass_kick, doc, _facts(active=0), key="1")
    starts = sorted(c[1] for c in got.after["lanes"]["1"]["cells"])
    assert starts == [0, 8, 16]  # the beat's one bar plays under every bar


def test_flat_hats_and_an_empty_beat_bar():
    hats = [["hat", s] for s in range(0, 32, 2)] + [["kick", 0], ["kick", 4]]
    f = _facts(drum_total=48, drum_spb=16)
    flat = lc.d_velocity(_doc([], drums=hats), f)
    assert flat and lc.d_velocity(flat.after, f) is None
    empty = lc.d_bars(_doc([], drums=hats), f)
    assert empty and "bar 3" in empty.title.lower()


def test_clean_up_all_makes_every_fix_and_leaves_nothing_to_fix(client):
    s = _st(client)
    doc, f = lc.read(s), lc.facts(s)
    after, said = lc.clean_all(doc, f)
    assert len(said) >= 3
    again, more = lc.clean_all(after, f)
    assert not more and again == after
    assert not [x for x in lc.assess(after, f) if x.after]


def test_every_option_on_the_owners_loop_is_a_change(client):
    s = _st(client)
    doc, f = lc.read(s), lc.facts(s)
    opts = {o.id: o for o in lc.options(s, doc, f)}
    for oid in (
        "tighter",
        "humanised",
        "turnaround",
        "answer",
        "beat_human",
        "beat_fill",
    ):
        assert opts[oid].after is not None and opts[oid].after != doc, oid
    assert opts["chords"].after is None and "chord lane" in opts["chords"].why


# --- the panel and its routes ---------------------------------------------------------


def test_perform_draws_the_coach_closed_with_its_command_and_shortcut(client):
    html = client.get("/view/perform").text
    assert (
        '<section id="coach-dock" class="coach module" data-module="perform.coach"'
        in html
    )
    assert re.search(r'id="coach-dock"[^>]*\bhidden\b', html)
    assert 'data-cmd="Loop coach: open / close"' in html and 'data-keys="Alt+K"' in html
    assert 'id="coach-panel"' in html and "Clean up all" in html
    # no key: the message box says how to add one; 🎙 is not offered
    assert "GEMINI_API_KEY" in html and "data-coach-mic" not in html
    assert 'name="coach_say"' in html


def test_a_proposal_changes_nothing_until_accepted(client):
    s = _st(client)
    before = lc.fingerprint(lc.read(s))
    key = client.app.state.svc.render_inputs("perform").key()
    r = client.post(
        "/fragment/coach/propose", data={"what": "fix:off_key:0"}, headers=HX
    )
    assert r.status_code == 200 and 'id="coach-panel"' in r.text  # the region, OOB
    assert lc.fingerprint(lc.read(s)) == before
    assert (
        client.app.state.svc.render_inputs("perform").key() == key
    )  # no render key moved
    p = _ctx(client)["proposal"]
    assert (
        p["label"].startswith("Fix:")
        and p["grid"]["notes"]["add"]
        and p["grid"]["notes"]["del"]
    )
    assert "data-diff=" in r.text


def test_audition_renders_the_proposal_without_the_session_holding_it(client):
    s = _st(client)
    svc = client.app.state.svc
    client.post("/fragment/coach/propose", data={"what": "fix:all"}, headers=HX)
    now = svc.render("perform").key
    r = client.post("/fragment/coach/audition", data={"which": "proposed"}, headers=HX)
    cue = json.loads(r.headers["HX-Trigger"])["preview-ready"]
    assert cue["autoplay"] and cue["plays"] == "✦ the coach's proposal"
    url = cue["url"]
    assert url != f"/preview/{now}.wav" and client.get(url).status_code == 200
    assert svc.render("perform").key == now  # the loop as it was
    back = json.loads(
        client.post(
            "/fragment/coach/audition", data={"which": "current"}, headers=HX
        ).headers["HX-Trigger"]
    )["preview-ready"]
    assert back["url"] == f"/preview/{now}.wav"
    # accepted, the loop plays the very render ▶ proposed played (the one cache)
    r = client.post("/fragment/coach/accept", headers=HX)
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"] == url
    assert lc.fingerprint(lc.read(s)) != ""


def test_accept_is_one_undo_step_and_ctrl_z_puts_the_loop_back(client):
    s = _st(client)
    before = lc.read(s)
    client.post("/fragment/coach/propose", data={"what": "fix:all"}, headers=HX)
    r = client.post("/fragment/coach/accept", headers=HX)
    assert r.status_code == 200 and "Accepted" in r.headers["HX-Trigger"]
    after = lc.read(s)
    assert (
        after != before and after["lanes"]["1"] == before["lanes"]["1"]
    )  # the bass kept
    assert _ctx(client)["proposal"] is None
    assert any(e["kind"] == "accepted" for e in _ctx(client)["thread"])
    client.post("/fragment/pattern/undo", headers=HX)
    assert lc.fingerprint(lc.read(s)) == lc.fingerprint(before)


def test_reverse_undoes_the_latest_and_restores_an_older_one_as_a_new_step(client):
    s = _st(client)
    before = lc.read(s)
    client.post("/fragment/coach/propose", data={"what": "fix:velocity:0"}, headers=HX)
    client.post("/fragment/coach/accept", headers=HX)
    first = _ctx(client)["thread"][-1]
    assert _ctx(client)["undo"] == first["id"]
    r = client.post(
        "/fragment/coach/reverse", data={"entry": str(first["id"])}, headers=HX
    )
    assert "undone" in r.headers["HX-Trigger"]
    assert lc.fingerprint(lc.read(s)) == lc.fingerprint(before)
    # accepted again, then the grid edited after it: Reverse restores
    client.post("/fragment/coach/propose", data={"what": "fix:velocity:0"}, headers=HX)
    client.post("/fragment/coach/accept", headers=HX)
    second = _ctx(client)["thread"][-1]
    client.post(
        "/fragment/coach/propose", data={"what": "option:beat_fill"}, headers=HX
    )
    client.post("/fragment/coach/accept", headers=HX)
    assert _ctx(client)["undo"] != second["id"]
    r = client.post(
        "/fragment/coach/reverse", data={"entry": str(second["id"])}, headers=HX
    )
    assert "as a new step" in r.headers["HX-Trigger"]
    assert lc.fingerprint(lc.read(s)) == lc.fingerprint(before)
    # ...which is an undo step of its own: Ctrl+Z puts the change back
    client.post("/fragment/pattern/undo", headers=HX)
    assert lc.fingerprint(lc.read(s)) != lc.fingerprint(before)
    assert (
        client.post(
            "/fragment/coach/reverse", data={"entry": str(second["id"])}, headers=HX
        ).status_code
        == 422
    )


def test_discard_and_a_stale_proposal(client):
    s = _st(client)
    client.post("/fragment/coach/propose", data={"what": "fix:all"}, headers=HX)
    assert client.post("/fragment/coach/discard", headers=HX).status_code == 200
    assert _ctx(client)["proposal"] is None
    assert client.post("/fragment/coach/discard", headers=HX).status_code == 422
    client.post("/fragment/coach/propose", data={"what": "fix:all"}, headers=HX)
    s.arrangement = replace(s.arrangement, cells=s.arrangement.cells + [["A3", 63]])
    assert (
        _ctx(client)["proposal"]["stale"] and _ctx(client)["proposal"]["grid"] is None
    )
    r = client.post("/fragment/coach/accept", headers=HX)
    assert r.status_code == 422 and "changed since" in r.text
    assert (
        client.post(
            "/fragment/coach/audition", data={"which": "proposed"}, headers=HX
        ).status_code
        == 422
    )


def test_nothing_to_offer_is_a_toast(client):
    for what in ("fix:nope:0", "option:nope", "nonsense"):
        assert (
            client.post(
                "/fragment/coach/propose", data={"what": what}, headers=HX
            ).status_code
            == 422
        )
    assert client.post("/fragment/coach/accept", headers=HX).status_code == 422
    assert (
        client.post(
            "/fragment/coach/reverse", data={"entry": "x"}, headers=HX
        ).status_code
        == 422
    )


def test_show_them_moves_the_rows_to_the_notes(client):
    s = _st(client)
    was = (s.grid_span, s.grid_oct)
    assert client.post("/fragment/coach/show", headers=HX).status_code == 200
    assert (s.grid_span, s.grid_oct) != was and "hidden:0" not in _ids(client)


def test_an_open_loop_edit_saves_an_accepted_change_to_its_loop(client):
    s = _st(client)
    assert (
        client.post(
            "/loop/save", data={"lane": "1", "bars": "4", "loop": "riff"}, headers=HX
        ).status_code
        == 200
    )
    name = loops.listing(s.data_dir)[0]["name"]
    assert client.post("/loop/edit", data={"name": name}, headers=HX).status_code == 200
    client.post("/fragment/coach/propose", data={"what": "fix:velocity:0"}, headers=HX)
    client.post("/fragment/coach/accept", headers=HX)
    saved = loops.load(s.data_dir, name)["lane"]
    assert saved["velocities"] == s.arrangement.velocities and saved["velocities"]


def test_a_playing_slot_takes_an_accepted_change_and_its_reverse(client):
    """Perform's launcher: a slot that plays is the loop open on
    its lane (`loop_edit`, one a lane) — the coach's Accept saves to it and
    ↶ Reverse puts it back, and the slot stays the one that plays."""
    s = _st(client)
    s.end_loop_edits()
    lane = {k: getattr(s.arrangement, k) for k in ("cells", "velocities")}
    name = loops.save(s.data_dir, "riff slot", s.arrangement, s.sound_payload())
    s.set_slot(0, 0, name)
    r = client.post(
        "/fragment/perform/slot/launch", data={"lane": "0", "row": "0"}, headers=HX
    )
    assert r.status_code == 200 and s.slot_playing(0) == 0
    client.post("/fragment/coach/propose", data={"what": "fix:velocity:0"}, headers=HX)
    client.post("/fragment/coach/accept", headers=HX)
    saved = loops.load(s.data_dir, name)["lane"]
    assert saved["velocities"] and saved["velocities"] != lane["velocities"]
    entry = next(e for e in _ctx(client)["thread"] if e["kind"] == "accepted")
    client.post("/fragment/coach/reverse", data={"entry": str(entry["id"])}, headers=HX)
    assert loops.load(s.data_dir, name)["lane"]["velocities"] == lane["velocities"]
    assert s.slot_playing(0) == 0


def test_a_loaded_session_drops_the_thread(client):
    s = _st(client)
    client.post("/fragment/coach/propose", data={"what": "fix:all"}, headers=HX)
    client.post("/fragment/coach/accept", headers=HX)
    s.load_project(json.loads(json.dumps(s.project_payload())))
    assert s.coach is None


# --- the talk -------------------------------------------------------------------------


class _FakeGemini:
    def __init__(self, reply):
        self.asked: dict = {}
        text = reply if isinstance(reply, str) else json.dumps(reply)

        def generate_content(**kw):
            self.asked = kw
            usage = types.SimpleNamespace(
                prompt_token_count=1200, candidates_token_count=150
            )
            return types.SimpleNamespace(
                text=text, usage_metadata=usage, model_version="gemini-2.5-flash"
            )

        self.models = types.SimpleNamespace(generate_content=generate_content)


@pytest.fixture
def gemini(monkeypatch):
    def use(reply):
        fake = _FakeGemini(reply)
        monkeypatch.setattr(takecoach, "client_for", lambda config: fake)
        monkeypatch.setattr(takecoach, "sdk_available", lambda: True)
        monkeypatch.setattr(
            takecoach,
            "load_config",
            lambda: AgentConfig(api_key="k-secret", source="env"),
        )
        return fake

    return use


def test_without_a_key_the_chat_says_how_and_the_rules_still_work(client):
    r = client.post(
        "/fragment/coach/chat", data={"coach_say": "less busy in bar 3"}, headers=HX
    )
    assert r.status_code == 200
    you, reply = _ctx(client)["thread"][-2:]
    assert you["text"] == "less busy in bar 3"
    assert reply["error"] and "GEMINI_API_KEY" in reply["lines"][0]
    assert _ctx(client)["findings"]
    assert (
        client.post(
            "/fragment/coach/chat", data={"coach_say": "   "}, headers=HX
        ).status_code
        == 422
    )


def test_a_reply_lands_in_the_thread_and_its_checked_edits_are_the_proposal(
    client, gemini
):
    fake = gemini(
        {
            "reply": ["Bar 2's F rubs against the key — moved it to E.", "k-secret"],
            "fixes": ["velocity:0", "nope:9"],
            "edits": [
                {
                    "lane": "1",
                    "op": "move",
                    "pitch": "F4",
                    "step": 22,
                    "to_pitch": "E4",
                    "why": "in key",
                },
                {"lane": "2", "op": "add", "pitch": "E2", "step": 4, "length": 2},
                {"lane": "drums", "op": "add", "voice": "clap", "step": 12},
                {
                    "lane": "1",
                    "op": "add",
                    "pitch": "F4",
                    "step": 1,
                },  # off the key: skipped
                {"lane": "9", "op": "remove", "pitch": "A3", "step": 0},  # no such lane
                {"lane": "1", "op": "remove", "pitch": "G#4", "step": 0},  # not there
            ],
        }
    )
    s = _st(client)
    before = lc.fingerprint(lc.read(s))
    r = client.post(
        "/fragment/coach/chat",
        data={"coach_say": "why does bar 2 sound off?"},
        headers=HX,
    )
    assert r.status_code == 200
    asked = fake.asked["contents"]
    assert "why does bar 2 sound off?" in asked and "A major" in asked
    assert "Lane 2" in asked and "Sub bass" in asked and "kick" in asked
    assert "off_key:0" in asked  # the coach's own findings, by id
    reply = _ctx(client)["thread"][-1]
    assert reply["kind"] == "coach" and reply["skipped"] == 3
    assert "k-secret" not in json.dumps(_ctx(client)) and "[redacted]" in json.dumps(
        reply["lines"]
    )
    assert reply["tokens"] == 1350 and reply["cost"] is not None
    p = _ctx(client)["proposal"]
    assert p and p["label"].startswith("✦")
    assert lc.fingerprint(lc.read(s)) == before  # nothing until Accept
    client.post("/fragment/coach/accept", headers=HX)
    doc = lc.read(s)
    assert ["E4", 22] in doc["lanes"]["0"]["cells"] and ["F4", 22] not in doc["lanes"][
        "0"
    ]["cells"]
    assert ["E2", 4] in doc["lanes"]["1"]["cells"] and ["clap", 12] in doc["drums"][
        "drums"
    ]
    assert doc["lanes"]["0"]["velocities"]  # the named fix, made


def test_a_reply_that_only_explains_proposes_nothing(client, gemini):
    gemini({"reply": ["The D# in bar 4 is a passing note."], "edits": []})
    client.post("/fragment/coach/chat", data={"coach_say": "why bar 4?"}, headers=HX)
    assert _ctx(client)["proposal"] is None
    assert _ctx(client)["thread"][-1]["lines"] == ["The D# in bar 4 is a passing note."]


def test_a_failed_call_is_a_scrubbed_line_in_the_thread(client, monkeypatch, gemini):
    fake = gemini({})

    def boom(**kw):
        raise RuntimeError("401 bad key k-secret")

    fake.models.generate_content = boom
    client.post("/fragment/coach/chat", data={"coach_say": "hi"}, headers=HX)
    line = _ctx(client)["thread"][-1]
    assert line["error"] and "k-secret" not in line["lines"][0]


def _talk_wav(seconds=1.0, sr=48000) -> bytes:
    t = np.arange(int(sr * seconds)) / sr
    pcm = (np.sin(2 * np.pi * 220 * t) * 9000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def test_a_spoken_message_is_sent_as_audio_and_what_was_heard_is_shown(client, gemini):
    fake = gemini(
        {"reply": ["Lighter hats it is."], "heard": "lighter hats", "edits": []}
    )
    r = client.post(
        "/fragment/coach/talk",
        files={"file": ("talk.wav", _talk_wav(), "audio/wav")},
        headers=HX,
    )
    assert r.status_code == 200
    parts = fake.asked["contents"]
    assert isinstance(parts, list) and len(parts) == 2
    assert parts[1].inline_data.mime_type == "audio/wav"
    with wave.open(io.BytesIO(parts[1].inline_data.data)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
    assert _ctx(client)["thread"][-2]["text"] == "🎙 “lighter hats”"


def test_a_spoken_message_refuses_what_is_not_a_recording(client, gemini):
    gemini({"reply": ["?"], "edits": []})
    r = client.post(
        "/fragment/coach/talk",
        files={"file": ("x.wav", b"nope", "audio/wav")},
        headers=HX,
    )
    assert r.status_code == 422
    assert client.post("/fragment/coach/talk", headers=HX).status_code == 422


def test_apply_checks_every_edit():
    f = _facts()
    doc = _doc([["A3", 0], ["A3", 1], ["E4", 4]], drums=[["kick", 0]])
    out, done, skipped = loopchat.apply(
        doc,
        f,
        [
            {
                "lane": "1",
                "op": "add",
                "pitch": "A3",
                "step": 1,
            },  # its pitch is held there
            {"lane": "1", "op": "add", "pitch": "C4", "step": 8},  # off the key
            {
                "lane": "1",
                "op": "add",
                "pitch": "C#4",
                "step": 8,
                "length": 99,
                "velocity": 3,
            },
            {"lane": "1", "op": "velocity", "pitch": "E4", "step": 4, "velocity": 0.4},
            {
                "lane": "1",
                "op": "remove",
                "pitch": "A3",
                "step": 1,
            },  # the note sounding there
            {"lane": "drums", "op": "remove", "voice": "kick", "step": 0},
            {"lane": "drums", "op": "add", "voice": "cowbell", "step": 0},
            {"lane": "1", "op": "add", "pitch": "E4", "step": 64},  # past the end
            "nonsense",
        ],
    )
    assert skipped == 5 and len(done) == 4
    lane = out["lanes"]["0"]
    assert ["A3", 0] not in lane["cells"] and ["C#4", 63] in lane["cells"]
    assert lane["velocities"] == {"8": 1.0, "4": 0.4}
    assert out["drums"]["drums"] == [] and doc["drums"]["drums"] == [["kick", 0]]


def test_the_brief_is_text_with_the_lanes_as_grids(client):
    info = loopchat.info(_st(client))
    text = loopchat.brief(info, "hello")
    assert "Lane 1 (the one being edited)" in text and "Beat (drums):" in text
    assert re.search(r"A3 +[1-9=.|]+", text)
    assert "The player says: hello" in text
