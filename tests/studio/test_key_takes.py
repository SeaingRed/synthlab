"""Keys takes: what ⏺ Rec in Synths' keyboard dock keeps (`keytakes.py`,
`routes/takes.py`, region `key-takes`).

What is held here: a take is stored with what it is (source "keys", bpm,
bars, the notes played, unkept, when) and a post that is not one is refused
before anything is written; the strip on Synths lists the unkept takes and
Perform's bin every take; ★ Keep names and keeps one (the clip library files
it under Takes, never with the jams or bounces), ✕ and "clear takes" delete;
→ notes writes a take onto the edited lane's note grid — quantised, over the
take's length, the grid grown to its bars, its bpm adopted only onto an empty
grid, one pattern undo step — and → sample makes it the sampler sound,
landing on Perform. A hand-edited meta never breaks any of it.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import cliplib, keytakes, limits, looplib, loops
from synthlab.studio.clips import list_clips, save_clip, write_meta
from synthlab.studio.server import create_app


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _wav(seconds: float = 0.5, sr: int = 44100) -> bytes:
    frames = int(seconds * sr)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x10" * frames * 2)
    return buf.getvalue()


# A1 on beat 1, C2 on beat 2, E2 on bar 2's downbeat: the default key
# (A pentatonic minor from A1) shows all three on its six rows
NOTES = [[0, 1, 33, 0.8], [1, 0.5, 36, 0.6], [4, 2, 40, 1]]


def _take(client, view="editor", notes=NOTES, **over):
    data = {"name": "keys-1", "bpm": "120", "bars": "2", "notes": json.dumps(notes)}
    data |= over
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    return client.post("/take/keys", data=data, files=files, headers=_hx(view))


def _meta(client, name: str) -> dict:
    path = _st(client).data_dir / "clips" / f"{name}.meta.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _notes(client, name: str) -> dict:
    """The take's notes as its sidecar holds them."""
    path = _st(client).data_dir / keytakes.NOTES / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- the take ------------------------------------------------------------------------


def test_a_take_is_kept_with_what_it_is(client):
    r = _take(client)
    assert r.status_code == 200, r.text[:300]
    assert r.headers["X-Clip-Name"] == "keys-1"
    meta = _meta(client, "keys-1")
    # kept from the start (one Rec is a take and its loop)
    assert meta["source"] == "keys" and meta["kept"] is True
    assert meta["bpm"] == 120 and meta["bars"] == 2 and meta["at"] > 0
    assert "beat" not in meta
    # the notes are its sidecar's, never the meta's
    assert "notes" not in meta and _notes(client, "keys-1") == {"notes": NOTES}
    # the strip under the keys lists it (its loop), out of band
    assert 'id="key-takes"' in r.text and "keys-1" in r.text
    assert _take(client, name="keys-1", beat="1").headers["X-Clip-Name"] != "keys-1"


@pytest.mark.parametrize(
    "over",
    [
        {"notes": "not json"},
        {"notes": json.dumps({"a": 1})},
        {"notes": json.dumps([[0, 1, 128, 0.5]])},  # past MIDI
        {"notes": json.dumps([[0, 0, 60, 0.5]])},  # no length
        {"notes": json.dumps([[0, 1, 60, 2]])},  # velocity past 1
        {"notes": json.dumps([[-1, 1, 60, 0.5]])},
        {"notes": json.dumps([[0, 1, 60.5, 0.5]])},
        {"notes": json.dumps([[0, 1, True, 0.5]])},
        {"notes": json.dumps([[0, 1, 60]])},
        {"notes": json.dumps([[0, 1, 60, 0.5]] * (limits.MAX_TAKE_NOTES + 1))},
        {"notes": "[" + "0," * limits.MAX_TAKE_NOTES_CHARS + "0]"},
        {"bpm": "nan"},
        {"bpm": "999"},
        {"bars": "0"},
        {"bars": str(limits.MAX_TAKE_BARS + 1)},
        {"bars": "two"},
    ],
)
def test_a_post_that_is_not_a_take_is_refused_and_nothing_is_written(client, over):
    r = _take(client, **over)
    assert r.status_code == 422
    assert not list((_st(client).data_dir / "clips").glob("*.wav"))


def test_a_take_with_no_file_or_a_bad_one_is_refused(client):
    data = {"bpm": "120", "bars": "1", "notes": "[]"}
    assert client.post("/take/keys", data=data).status_code == 422
    files = {"file": ("take.wav", b"RIFF nope", "audio/wav")}
    assert client.post("/take/keys", data=data, files=files).status_code == 422


def test_a_take_with_nothing_played_is_still_a_take(client):
    assert _take(client, notes=[]).status_code == 200
    assert _notes(client, "keys-1")["notes"] == []


# --- the strip and the bin -----------------------------------------------------------


def test_the_strip_lists_this_sessions_and_perform_every_one(client):
    html = client.get("/view/editor").text
    assert (
        'id="key-takes"' in html
        and 'class="key-takes fold-body" aria-label="Loops"\n         hidden' in html
    )
    d = _st(client).data_dir
    meta = {"source": "keys", "kept": True, "at": keytakes.SESSION_START - 50}
    save_clip(d, "earlier", [[0.1, 0.1]] * 100, meta=meta)
    _take(client, name="loose")
    synths = client.get("/view/editor").text
    strip = synths[synths.index('id="key-takes"') :]
    assert "loose" in strip and "earlier" not in strip.split("</section>")[0]
    perform = client.get("/view/perform").text
    binned = perform[perform.index('id="loops-panel"') :].split("</section>")[0]
    assert "loose" in binned and "earlier" in binned
    # the strip is the keyboard dock's, on every page — Perform's and the
    # Library's too — beside Perform's bin, each with its own id
    for page in (perform, client.get("/view/library").text):
        strip = page[page.index('id="key-takes"') :].split("</section>")[0]
        assert "loose" in strip and "earlier" not in strip
    assert (
        perform.count('id="key-takes"') == 1 and perform.count('id="loops-panel"') == 1
    )


def test_this_sessions_takes_come_first_newest_first(client, monkeypatch):
    d = _st(client).data_dir
    for name, at in [
        ("old", keytakes.SESSION_START - 50),
        ("a", keytakes.SESSION_START),  # this session's, before "b"
    ]:
        meta = {"source": "keys", "kept": False, "at": at}
        save_clip(d, name, [[0.1, 0.1]] * 100, meta=meta)
    _take(client, name="b")
    rows = looplib.context(d, "editor")["rows"]
    assert [t["name"] for t in rows] == ["b", "a", "old"]
    assert [t["earlier"] for t in rows] == [False, False, True]


def test_keep_names_it_and_the_library_files_it_under_takes(client):
    _take(client)
    r = client.post(
        "/take/keys/keep",
        data={"name": "keys-1"},
        headers={**_hx("editor"), "HX-Prompt": "my%20riff"},
    )
    assert r.status_code == 200
    assert "Kept" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    meta = _meta(client, "my riff")
    assert meta["kept"] is True and meta["name"] == "my riff"
    # the notes moved with it: the rename carries the sidecar
    assert _notes(client, "my riff") == {"notes": NOTES}
    d = _st(client).data_dir
    assert not (d / "clips" / "keys-1.wav").exists()
    assert not (d / keytakes.NOTES / "keys-1.json").exists()
    # the strip let it go, the library has it under Takes
    assert (
        'id="key-takes"' in r.text
        and "my riff" not in r.text.split('id="clip-library"')[0]
    )
    kinds = {c["name"]: cliplib.kind_of(c)[0] for c in list_clips(_st(client).data_dir)}
    assert kinds == {"my riff": "take"}


def test_keep_onto_a_taken_name_is_refused(client):
    _take(client)
    save_clip(_st(client).data_dir, "taken", [[0.1, 0.1]] * 10)
    r = client.post("/take/keys/keep", data={"name": "keys-1", "new": "taken"})
    assert r.status_code == 422
    assert _meta(client, "keys-1")["source"] == "keys"  # left as it was


def test_keep_renames_the_clip_everywhere_the_library_would(client, monkeypatch):
    """★ Keep's rename is the clip library's (`rename_clip_everywhere`):
    the session's references, the song's undo ring and the Lab chop the
    clip was kept from follow it."""
    from synthlab.studio import labchops

    s = _st(client)
    _take(client)
    s.arrangement = replace(
        s.arrangement, clips=[{"clip": "keys-1", "at_bar": 0, "gain": 1.0}]
    )
    told = []
    monkeypatch.setattr(labchops, "clip_renamed", lambda *a: told.append(a[1:]))
    client.post("/take/keys/keep", data={"name": "keys-1", "new": "hook"})
    assert told == [("keys-1", "hook")]
    assert [c["clip"] for c in s.arrangement.clips] == ["hook"]


def test_one_plural_for_python_and_the_templates(client):
    from synthlab.studio.words import plural

    assert [plural(n, "bar") for n in (1, 2, 1.0, 1.5, 0)] == [
        "1 bar",
        "2 bars",
        "1 bar",
        "1.5 bars",
        "0 bars",
    ]
    env = client.app.state.svc.templates.env
    assert env.from_string("{{ 4 | plural('bar') }}").render() == "4 bars"


def test_the_library_groups_keys_takes_as_takes_never_jams_or_bounces():
    take = {"source": "keys", "bpm": 120, "bars": 2}
    assert cliplib.kind_of({**take, "kept": False}) == ("take-new", "")
    assert cliplib.kind_of({**take, "kept": True}) == ("take", "")
    assert cliplib.kind_of({"source": "jam"}) == ("jam", "")


def test_the_docks_recordings_from_before_takes_are_jams_not_takes(client):
    """`/clip/upload` once took source "keys" (the dock's ⏺ Rec before
    takes): audio, no notes, no `kept`. Not a kept take with no notes —
    filed with the jams it was recorded like, and never in the bin."""
    d = _st(client).data_dir
    save_clip(d, "old rec", [[0.1, 0.1]] * 100, meta={"source": "keys"})
    assert cliplib.kind_of({"source": "keys"}) == ("jam", "")
    assert not keytakes.is_take({"source": "keys"})
    assert keytakes.takes(d) == []
    assert client.post("/take/keys/notes", data={"name": "old rec"}).status_code == 422


def test_discard_and_clear_delete_and_only_takes(client):
    """✕ deletes a take; "clear takes" the unkept ones a take from before
    one Rec kept them all may still be."""
    d = _st(client).data_dir
    _take(client, name="one")
    save_clip(d, "two", [[0.1, 0.1]] * 10, meta={"source": "keys", "kept": False})
    _take(client, name="kept")
    save_clip(_st(client).data_dir, "a jam", [[0.1, 0.1]] * 10, meta={"source": "jam"})
    assert client.post("/take/keys/discard", data={"name": "a jam"}).status_code == 422
    r = client.post("/take/keys/discard", data={"name": "one"}, headers=_hx("editor"))
    assert r.status_code == 200 and 'id="key-takes"' in r.text
    r = client.post("/take/keys/clear", headers=_hx("editor"))
    assert (
        "Cleared 1 " in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    left = sorted(c["name"] for c in list_clips(_st(client).data_dir))
    assert left == ["a jam", "kept"]


# --- → Perform ---------------------------------------------------------------------


def test_notes_go_onto_the_grid_over_the_take_and_land_on_perform(client):
    s = _st(client)
    _take(client)
    r = client.post("/take/keys/notes", data={"name": "keys-1"}, headers=_hx("editor"))
    assert r.status_code == 200, r.text[:300]
    assert r.headers.get("HX-Push-Url") == "/view/perform"
    assert "Ctrl+Z" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    a = s.arrangement
    assert a.bars == 2  # the take is two bars: the grid grew to them
    assert sorted(map(tuple, a.cells)) == [
        ("A1", 0),
        ("C2", 1),
        ("E2", 4),
        ("E2", 5),
    ]
    assert a.velocities == {"0": 0.803, "1": 0.598, "4": 1.0}
    assert a.bpm == 124.0  # the grid had notes: the loop keeps its tempo
    # one pattern undo step takes it back
    assert s.pop_pattern_undo() is None
    assert s.arrangement.bars == 1 and sorted(map(tuple, s.arrangement.cells)) == [
        ("A1", 0),
        ("A1", 2),
    ]


def test_past_the_take_the_grid_keeps_what_it_showed(client):
    s = _st(client)
    s.arrangement.bars = 4
    s.arrangement.cells = [["G2", 2], ["G2", 9], ["A2", 14]]
    _take(client, notes=[[0, 1, 33, 1]], bars="2")
    client.post("/take/keys/notes", data={"name": "keys-1"}, headers=_hx("perform"))
    a = s.arrangement
    assert a.bars == 4  # longer already
    # bars 1-2 are the take's; bars 3-4 kept theirs
    assert sorted(map(tuple, a.cells)) == [("A1", 0), ("A2", 14), ("G2", 9)]


def test_an_empty_grid_takes_the_takes_tempo_and_out_of_key_widens_the_rows(client):
    s = _st(client)
    s.arrangement.cells = []
    _take(client, notes=[[0, 1, 34, 1]], bpm="96")  # A#1: not in A minor pentatonic
    client.post("/take/keys/notes", data={"name": "keys-1"}, headers=_hx("perform"))
    assert s.arrangement.bpm == 96.0
    assert s.grid_span == 3  # the chromatic rows show it where it was played
    assert [tuple(c) for c in s.arrangement.cells] == [("A#1", 0)]


def test_a_take_with_no_notes_or_on_a_following_lane_changes_nothing(client):
    s = _st(client)
    before = list(map(tuple, s.arrangement.cells))
    _take(client, notes=[])
    r = client.post("/take/keys/notes", data={"name": "keys-1"}, headers=_hx("perform"))
    assert r.status_code == 422 and list(map(tuple, s.arrangement.cells)) == before
    r = client.post("/take/keys/notes", data={"name": "nope"}, headers=_hx("perform"))
    assert r.status_code == 422


def test_as_a_sample_it_is_the_sampler_sound_on_perform(client):
    _take(client)
    r = client.post("/take/keys/sample", data={"name": "keys-1"}, headers=_hx("editor"))
    assert r.status_code == 200, r.text[:300]
    assert r.headers.get("HX-Push-Url") == "/view/perform"
    patch = _st(client).patch
    assert patch.osc1 == "sampler" and patch.sampler_sample


# --- a meta anyone can edit ----------------------------------------------------


def test_a_hand_edited_meta_never_breaks_the_strip(client):
    d = _st(client).data_dir
    save_clip(d, "odd", [[0.1, 0.1]] * 10)
    write_meta(
        d,
        "odd",
        {
            "source": "keys",
            "kept": False,
            "bpm": "fast",
            "bars": 1e9,
            "at": "now",
            "notes": [[0, 1, 60, 0.5], "x", [0, 1, 999, 0.5], [0, 1, 61, 0.5, 9]],
        },
    )
    assert client.get("/view/editor").status_code == 200
    (row,) = keytakes.takes(d)
    assert row["notes"] == 1 and row["bpm"] == 0 and row["bars"] == 0
    assert (
        keytakes.notes_of({"notes": [[0, 1, 60, 0.5]] * 3}) == [[0.0, 1.0, 60, 0.5]] * 3
    )
    # ... nor a hand-edited sidecar: a broken one falls back to the meta
    (d / keytakes.NOTES).mkdir(exist_ok=True)
    (d / keytakes.NOTES / "odd.json").write_text("{nope", encoding="utf-8")
    assert keytakes.read_notes(d, "odd")["notes"] == [[0.0, 1.0, 60, 0.5]]
    (d / keytakes.NOTES / "odd.json").write_text(
        json.dumps({"notes": "x", "raw": [[0, 1, 61, 2]], "fixes": [1, "ok"]}),
        encoding="utf-8",
    )
    assert keytakes.read_notes(d, "odd") == {"notes": [], "raw": [], "fixes": ["ok"]}
    assert client.get("/view/perform").status_code == 200


# --- the notes' sidecar, and the takes from before it ---------------------------------


def test_a_take_from_before_the_sidecar_is_read_and_kept_into_one(client):
    """Its notes in its meta (the round before): read from there, cleaned
    into a sidecar, and moved out of the meta by ★ Keep."""
    d = _st(client).data_dir
    meta = {"source": "keys", "kept": False, "bpm": 120, "bars": 2, "at": 2.0}
    save_clip(d, "legacy", [[0.1, 0.1]] * 100, meta={**meta, "notes": NOTES})
    (row,) = keytakes.takes(d)
    assert row["name"] == "legacy" and row["notes"] == 3
    assert keytakes.read_notes(d, "legacy")["notes"] == NOTES
    r = client.post("/take/keys/notes", data={"name": "legacy"}, headers=_hx("perform"))
    assert r.status_code == 200
    client.post("/take/keys/keep", data={"name": "legacy", "new": "kept one"})
    kept = _meta(client, "kept one")
    assert kept["kept"] is True and "notes" not in kept
    assert _notes(client, "kept one") == {"notes": NOTES}


def test_a_clean_up_writes_the_sidecar_never_the_meta(client):
    _take(client, notes=LATE, bars="1")
    meta_file = _st(client).data_dir / "clips" / "keys-1.meta.json"
    before = (meta_file.read_bytes(), meta_file.stat().st_mtime_ns)
    client.post("/take/keys/clean", data={"name": "keys-1", "ai": "0"})
    assert (meta_file.read_bytes(), meta_file.stat().st_mtime_ns) == before
    assert _notes(client, "keys-1")["raw"] == LATE


def test_delete_and_discard_take_the_sidecar_with_the_clip(client):
    _take(client)
    side = _st(client).data_dir / keytakes.NOTES / "keys-1.json"
    assert side.is_file()
    client.post("/take/keys/discard", data={"name": "keys-1"})
    assert not side.exists()


def test_the_takes_read_only_take_files(client, monkeypatch):
    """`takes` is cached by the takes' own files: another clip's coming
    and going reads no meta and no WAV header again, and a clip that is no
    take never has its header read."""
    from synthlab.studio import clips

    d = _st(client).data_dir
    _take(client)
    keytakes.takes(d)
    read = []
    real = clips.seconds

    def seconds(data_dir, name):
        read.append(name)
        return real(data_dir, name)

    monkeypatch.setattr(clips, "seconds", seconds)
    save_clip(d, "a bounce", [[0.1, 0.1]] * 10, meta={"source": "x", "bpm": 1})
    assert [t["name"] for t in keytakes.takes(d)] == ["keys-1"]
    assert read == []  # nothing of the takes moved
    _take(client, name="keys-2")
    keytakes.takes(d)
    assert "a bounce" not in read and "keys-2" in read


# --- a take is notes + the sound that played it -----------------------------------


def _toast(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def test_a_take_keeps_the_sound_that_played_it_and_brings_it_back(client):
    s = _st(client)
    s.patch = replace(s.patch, name="Glass Lead", cutoff=1234.0)
    _take(client)
    meta = _meta(client, "keys-1")
    assert meta["root"] == "A1" and meta["scale"] == "pentatonic_minor"
    patch, chain = keytakes.load_sound(s.data_dir, meta["sound"])
    assert patch.name == "Glass Lead" and patch.cutoff == 1234.0
    assert chain is not None and "layers" in chain  # the whole stack
    _take(client, name="keys-2")  # the same sound: one file for both
    assert _meta(client, "keys-2")["sound"] == meta["sound"]
    assert len(list((s.data_dir / keytakes.SOUNDS).glob("*.json"))) == 1
    # another sound now; → notes plays the take in the one it was made with
    s.patch = replace(s.patch, name="Other", cutoff=300.0)
    r = client.post("/take/keys/notes", data={"name": "keys-1"}, headers=_hx("editor"))
    assert r.status_code == 200
    assert s.patch.name == "Glass Lead" and s.patch.cutoff == 1234.0
    assert "sound that played it" in _toast(r)


def test_a_missing_or_hostile_sound_id_loads_nothing(client):
    d = _st(client).data_dir
    for sid in (None, 7, "../../etc", "0" * 16, "ZZZZZZZZZZZZZZZZ"):
        assert keytakes.load_sound(d, sid) is None


# --- ✦ Clean up -------------------------------------------------------------------


LATE = [
    [0.1, 0.9, 33, 0.9],
    [1.1, 0.4, 34, 0.5],
    [2.1, 0.9, 36, 0.7],
    [3.1, 0.9, 40, 0.8],
]


def test_clean_up_takes_out_the_lean_and_the_wrong_key_and_undo_gives_it_back(client):
    _take(client, notes=LATE, bars="1")
    r = client.post(
        "/take/keys/clean", data={"name": "keys-1", "ai": "0"}, headers=_hx("editor")
    )
    assert r.status_code == 200, r.text[:300]
    assert "late" in _toast(r) and "A pentatonic minor" in _toast(r)
    doc = _notes(client, "keys-1")
    assert doc["raw"] == LATE and doc["fixes"]
    assert [n[0] for n in doc["notes"]] == [0.0, 1.0, 2.0, 3.0]  # the lean, gone
    assert 34 not in [n[2] for n in doc["notes"]]  # A#1 moved into the key
    assert doc["notes"][0][1] == 1.0  # its length kept, on the grid
    # the Loops list says so, with ↶; the loop's grid is the cleaned notes
    assert "✦ cleaned" in client.get("/view/perform").text
    lane = loops.load(_st(client).data_dir, "keys-1")["lane"]
    assert sorted(int(st) for _p, st in lane["cells"] if _p != "A#1")[:1] == [0]
    again = client.post("/take/keys/clean", data={"name": "keys-1", "ai": "0"})
    assert again.status_code == 200 and _notes(client, "keys-1")["raw"] == LATE
    client.post("/take/keys/unclean", data={"name": "keys-1"})
    assert _notes(client, "keys-1") == {"notes": LATE}


def test_gemini_edits_land_on_the_cleaned_notes(client, monkeypatch):
    from synthlab.studio import takecoach

    _take(client, notes=[[0, 1, 33, 0.8], [2, 1, 36, 0.8]], bars="1")
    monkeypatch.setattr(takecoach, "available", lambda config=None: None)
    edits = [
        {"op": "add", "pitch": "E2", "step": 4, "velocity": 0.7, "why": "a lift"},
        {"op": "remove", "pitch": "C2", "step": 8},
    ]
    monkeypatch.setattr(
        takecoach,
        "coach",
        lambda info, config=None: {"feedback": ["Nice."], "edits": edits, "tokens": 1},
    )
    r = client.post("/take/keys/clean", data={"name": "keys-1", "ai": "1"})
    assert r.status_code == 200, r.text[:300]
    notes = _notes(client, "keys-1")["notes"]
    assert [(n[0], n[2]) for n in notes] == [(0.0, 33), (1.0, 40)]
    assert "Nice." in _toast(r)


# --- loops ------------------------------------------------------------------------


def test_a_take_saved_as_a_loop_keeps_its_bars_notes_and_sound(client):
    s = _st(client)
    s.patch = replace(s.patch, name="Glass Lead")
    _take(client, bpm="96")
    r = client.post(
        "/loop/save",
        data={"name": "keys-1", "bars": "1"},
        headers={**_hx("editor"), "HX-Prompt": "hook"},
    )
    assert r.status_code == 200, r.text[:300]
    assert 'id="key-takes"' in r.text and "hook" in r.text
    loop = loops.load(s.data_dir, "hook")
    assert loop["lane"]["bars"] == 1 and loop["lane"]["steps_per_bar"] == 16
    cells = {tuple(c) for c in loop["lane"]["cells"]}
    # A1 held a beat (4 sixteenths), C2 half a beat; bar 2's E2 is past it
    assert {("A1", 0), ("A1", 3), ("C2", 4), ("C2", 5)} <= cells
    assert not any(p == "E2" for p, _ in cells)
    assert loop["key"]["bpm"] == 96.0 and loop["sound"][0].name == "Glass Lead"


def test_a_loop_goes_into_perform_with_its_sound_and_tempo(client):
    s = _st(client)
    s.patch = replace(s.patch, name="Pad One")
    s.arrangement.cells = [["C2", 0], ["E2", 2]]
    s.arrangement.bpm = 100.0
    client.post("/loop/save", data={"lane": "1", "bars": "2", "loop": "pad"})
    s.patch = replace(s.patch, name="Else")
    s.arrangement.cells = []
    s.arrangement.bpm = 130.0
    r = client.post("/loop/load", data={"name": "pad"}, headers=_hx("editor"))
    assert r.status_code == 200 and r.headers.get("HX-Push-Url") == "/view/perform"
    a = s.arrangement
    assert sorted(map(tuple, a.cells)) == [("C2", 0), ("E2", 2)] and a.bars == 2
    assert a.bpm == 100.0 and s.patch.name == "Pad One"
    assert s.pop_pattern_undo() is None and s.arrangement.cells == []


def test_loops_rename_delete_and_refuse_what_is_not_there(client):
    s = _st(client)
    client.post("/loop/save", data={"lane": "1", "bars": "1", "loop": "a"})
    client.post("/loop/save", data={"lane": "1", "bars": "1", "loop": "a"})
    assert loops.names(s.data_dir) == ["a", "a 2"]
    assert (
        client.post("/loop/rename", data={"name": "a", "new": "a 2"}).status_code == 422
    )
    assert (
        client.post("/loop/rename", data={"name": "a", "new": "b"}).status_code == 200
    )
    assert loops.names(s.data_dir) == ["a 2", "b"]
    # a rename that only changes the case changes it (`files.rename_file`),
    # and a free name is case-folded, as Windows sees it
    r = client.post("/loop/rename", data={"name": "b", "new": "B"})
    assert r.status_code == 200
    assert loops.names(s.data_dir) == ["B", "a 2"]
    assert loops.free_name(s.data_dir, "b") == "b 2"
    client.post("/loop/rename", data={"name": "B", "new": "b"})
    assert client.post("/loop/delete", data={"name": "b"}).status_code == 200
    assert client.post("/loop/delete", data={"name": "b"}).status_code == 422
    assert client.post("/loop/load", data={"name": "nope"}).status_code == 422


def _session(s) -> str:
    """The whole session as it is saved: what a bounce must leave alone."""
    return json.dumps(s.project_payload(), sort_keys=True, default=str)


def test_a_loop_bounces_to_a_clip_of_its_synths_and_touches_nothing(client):
    s, svc = _st(client), client.app.state.svc
    s.patch = replace(s.patch, name="Pad One")
    s.arrangement.cells = [["A1", 0], ["C2", 2]]
    s.arrangement.bpm = 100.0
    client.post("/loop/save", data={"lane": "1", "bars": "2", "loop": "riff"})
    # the session moves on: another sound, other notes, another tempo
    s.patch = replace(s.patch, name="Else", cutoff=300.0)
    s.arrangement.cells = [["E2", 1]]
    s.arrangement.bpm = 130.0
    before = _session(s)
    undo = (len(s._history), len(s._pattern_undo))
    r = client.post("/loop/bounce", data={"name": "riff"}, headers=_hx("perform"))
    assert r.status_code == 200, r.text[:300]
    assert _session(s) == before  # byte for byte
    assert (len(s._history), len(s._pattern_undo)) == undo
    assert "HX-Push-Url" not in r.headers  # no landing: nothing was put anywhere
    assert "Bounced" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    (clip,) = [c for c in list_clips(s.data_dir) if c["name"] == "riff"]
    assert clip["source"] == "loop: riff" and clip["bars"] == 2 and clip["bpm"] == 100
    assert clip["seconds"] == pytest.approx(2 * 4 * 60 / 100, abs=0.6)
    # it is the render → Perform would have played: the loop's sound, notes
    # and tempo on the edited lane, its synths alone
    loop = loops.load(s.data_dir, "riff")
    arr = replace(s.arrangement, **loop["lane"], **loop["key"])
    inp = svc.render_inputs(
        "perform",
        patch=loop["sound"][0],
        chain=loop["sound"][1],
        mix=True,
        arrangement=arr,
        parts=("synth",),
    )
    assert inp.patch.name == "Pad One" and inp.arr.bpm == 100.0
    assert sorted(map(tuple, inp.arr.cells)) == [("A1", 0), ("C2", 2)]
    assert inp.parts == ("synth",)
    assert _session(s) == before


def test_a_hostile_loop_file_is_conformed_or_ignored(client):
    d = _st(client).data_dir
    (loops.loop_dir(d) / "evil.json").write_text(
        json.dumps({"v": 1, "name": 5, "arr": {"bars": 99, "cells": "x"}, "sound": 3}),
        encoding="utf-8",
    )
    (loops.loop_dir(d) / "junk.json").write_text("{not json", encoding="utf-8")
    loop = loops.load(d, "evil")
    assert loop["lane"]["bars"] in limits.BAR_CHOICES and loop["sound"] is None
    assert loops.load(d, "junk") is None
    assert client.get("/view/perform").status_code == 200
    assert [lp["name"] for lp in loops.listing(d)] == ["evil"]


# --- ⟲ Capture: the keys' buffer as a loop, no audio ----------


def _capture(client, view="perform", notes=NOTES, **over):
    data = {"name": "keys-20261108-101010", "bpm": "120", "bars": "2"}
    data |= {"notes": json.dumps(notes)} | over
    return client.post("/take/keys/capture", data=data, headers=_hx(view))


def test_a_capture_is_a_loop_of_the_notes_with_no_audio(client):
    s = _st(client)
    clips_before = list_clips(s.data_dir)
    r = _capture(client, view="editor")
    assert r.status_code == 200, r.text
    name = unquote(r.headers["X-Loop-Name"])
    loop = loops.load(s.data_dir, name)
    assert loop["take"] is None and loop["played"] == NOTES
    assert loop["key"]["bpm"] == 120.0 and loop["lane"]["bars"] == 2
    # named from what it is (the clock asked for that): its sound and
    # what it plays, plainly
    assert name == f"{s.patch.name} riff"
    assert list_clips(s.data_dir) == clips_before  # no audio, no clip
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert toast.startswith("Captured") and "3 notes" in toast
    # the arp's print says so
    r = _capture(client, arp="1")
    assert "Printed the keys arp" in r.headers["HX-Trigger"]


def test_a_capture_of_nothing_or_without_its_tempo_is_refused(client):
    s = _st(client)
    for over in ({"notes": "[]"}, {"bpm": ""}, {"bars": "0"}, {"notes": "x"}):
        r = _capture(client, **over)
        assert r.status_code == 422, over
    assert loops.listing(s.data_dir) == []


def test_a_capture_lands_in_the_selected_slot_as_a_take_does(client):
    s = _st(client)
    s.arrangement = replace(s.arrangement, bars=1, steps_per_bar=16)
    r = _capture(client, bars="1", lane="2", row="1", how="new")
    assert r.status_code == 200
    name = unquote(r.headers["X-Loop-Name"])
    assert s.slot_at(2, 1) == name and s.slot_playing(2) == 1
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["queue"] is True
    # the drums' lane never takes notes: a free loop
    r = _capture(client, bars="1", lane="4", row="0")
    assert r.status_code == 200 and s.slot_at(4, 0) is None


CAPTURE = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
eval(fs.readFileSync(process.argv[2], "utf8")); // no document: the pure half only
const W = window.synthlab.keyCapture.captureWindow;
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const on = (grid, bar) =>
  grid == null ? null : (t) => grid + Math.floor((t - grid) / bar + 1e-9) * bar;
process.stdout.write(JSON.stringify(
  cases.map(([notes, bar, grid, want]) => W(notes, bar, on(grid, bar), want))));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node runs keyrec.js")
def test_the_capture_window_is_the_last_bars_or_the_latest_phrase(tmp_path):
    """keyrec.js `captureWindow`: on the loop's bar lines (from 0.1 s, a
    2 s bar) or free (the phrase's first note the downbeat); the dock's
    bars, else the latest phrase — back to a bar of silence — from its bar
    line, rounded up to 1 / 2 / 4 / 8 bars; a note a hair early is on its
    line."""
    phrase = [[10.3, 10.6], [11.0, 11.4], [12.5, 13.0]]  # bars 6-7 of the loop
    early = [[1.0, 1.2], [14.07, 14.3]]  # (and an old note, long before)
    cases = [
        [phrase, 2, 0.1, 0],  # the phrase: bar line 10.1, 2 bars
        [phrase, 2, 0.1, 4],  # the dock's 4 bars, ending after the newest
        [[[0.1, 0.4], *phrase], 2, 0.1, 0],  # a gap of a bar: not the phrase
        [phrase, 2, None, 0],  # free: from the first note
        [early, 2, 0.1, 0],  # 14.07 is on the 14.1 line: one bar
        [[[i * 2.0, i * 2.0 + 1] for i in range(10)], 2, 0.0, 0],  # 10 bars: the last 8
    ]
    (tmp_path / "c.cjs").write_text(CAPTURE, encoding="utf-8")
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    keyrec = Path("synthlab/studio/static/keyrec.js").resolve()
    out = subprocess.run(
        ["node", str(tmp_path / "c.cjs"), str(keyrec), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    got = [(round(w["start"], 3), w["bars"]) for w in json.loads(out.stdout)]
    assert got == [(10.1, 2), (6.1, 4), (10.1, 2), (10.3, 2), (14.1, 1), (4.0, 8)]
