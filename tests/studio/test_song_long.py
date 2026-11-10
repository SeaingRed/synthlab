"""Long audio: a whole stem of a Sample Lab source on a Song audio track
(`song_model`'s "long audio", `longaudio.py`, `POST /song/long`).

- the model: an item sits at a bar of the TIMELINE, so a repeat plays the
  next bars of it and a section added before it leaves it where it is;
- the schema: hostile values clamped or dropped, absent when empty;
- the render: the export is the source's own samples, the plan's WAVs
  stitched are the export, the loop is the first pass, a missing source is
  silence under another key;
- a song WITHOUT long audio keeps its idents, its plan and its saved state;
- the route, undo, the Lab's delete saying where a source is used.

The "stems" here are noise written straight into the source store: no
separator, no model. Not proven here: how it sounds, or that a seam
between two passes is inaudible in the browser's player.
"""

from __future__ import annotations

import copy
import io
import json
import re
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.audio import wav_bytes
from synthlab.core import SR
from synthlab.studio import limits, longaudio, schema, song_play, sources
from synthlab.studio import song_model as sm
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import HX, song_dict, use_song

MODEL = stems.HTDEMUCS.id
SECONDS = 12
BPM = 200.0  # a bar is 1.2 s: short renders
BAR = sm.bar_sample(1, BPM)
FADE = int(limits.LONG_FADE_SECONDS * SR)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def make_source(data_dir, seed: int = 1, seconds: float = SECONDS) -> str:
    """A source with a 4-stem set on disk: noise, each stem its own."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    mix = (rng.standard_normal((n, 2)) * 0.1).clip(-1, 1)
    sid = sources.add(data_dir, wav_bytes(mix), "tune.wav")["id"]
    parts = {s: rng.standard_normal((n, 2)) * 0.05 for s in stems.SOURCES}
    sources.save_stems(data_dir, sid, MODEL, parts, 1.0)
    return sid


def stem(data_dir, sid: str, name: str = "vocals") -> np.ndarray:
    """A stem as the store holds it (16-bit, so not the floats written)."""
    meta = sources.read_meta(data_dir, sid)
    return sources.read(data_dir, meta, name).astype(np.float64)


def audio_song(*sections, bpm: float = BPM) -> dict:
    """A song of empty sections (bars, repeats) over one audio track "ta"."""
    song = sm.new_song(bpm, "A1", "minor")
    song["tracks"] = [{"id": "ta", "kind": "audio", "name": "Vox"}]
    song["sections"] = [
        {"id": f"s{i + 1}", "name": f"S{i + 1}", "bars": bars, "repeats": reps}
        for i, (bars, reps) in enumerate(sections)
    ]
    return schema.conform(song, schema.nodes().SONG)


def place(song: dict, data_dir, sid: str, stems_=("vocals",), **fields) -> str:
    item = longaudio.from_source(data_dir, sid, MODEL, list(stems_), {})
    item |= fields
    new = sm.add_long(song, "ta", item.pop("source"), **item)
    song.update(schema.conform(song, schema.nodes().SONG))
    return new


def svc_of(client):
    return client.app.state.svc


# --- the model -------------------------------------------------------------------


def test_an_item_runs_on_through_a_sections_repeats(tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 3), (2, 1))
    place(song, tmp_path, sid, at_bar=1, start=0.5)
    cuts = sm.pass_cuts(song)
    # section 1 is a 1-bar cycle three times; the item starts at bar 2
    assert [len(c) for c in cuts[0]] == [0, 1, 1]
    first, second = cuts[0][1][0], cuts[0][2][0]
    assert first["frm"] == int(0.5 * SR) and first["a"] == 0
    # the repeat plays the NEXT bar of the source, not the same one
    assert second["frm"] == first["frm"] + BAR and second["a"] == -BAR
    # and it carries on into the next section's passes
    assert cuts[1][0][0]["frm"] == first["frm"] + 2 * BAR


def test_a_section_added_before_it_leaves_the_item_at_its_bar(tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((2, 1), (2, 1))
    item = place(song, tmp_path, sid, at_bar=2)
    before = sm.long_spans(song)[item]
    sm.add_section(song, "Intro", 4, at=0)
    assert sm.long_items(song)[0]["at_bar"] == 2  # the timeline's bar 3, still
    assert sm.long_spans(song)[item][0] == before[0]
    assert 0 in sm.pass_cuts(song)  # it now plays under the new intro


def test_the_song_is_never_lengthened_and_an_item_past_its_end_is_silent(tmp_path):
    sid = make_source(tmp_path)  # 12 s = 10 bars at 200 bpm
    song = audio_song((2, 1))
    item = place(song, tmp_path, sid)
    a, b = sm.long_spans(song)[item]
    assert (a, b) == (0, 2 * BAR)  # cut at the song's end
    sm.set_long(song, item, at_bar=5)
    assert sm.long_spans(song) == {} and sm.pass_cuts(song) == {}
    assert sm.song_seconds(song) == pytest.approx(2.4)
    row = longaudio.rows(tmp_path, song)["items"][0]
    assert row["bars"] is None


def test_mute_solo_and_the_tracks_gain_reach_the_cut(tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 1))
    item = place(song, tmp_path, sid, gain=0.5)
    song["tracks"][0]["gain"] = 0.8
    assert sm.pass_cuts(song)[0][0][0]["gain"] == pytest.approx(0.4)
    sm.set_long(song, item, mute=True)
    assert sm.pass_cuts(song) == {}
    sm.set_long(song, item, mute=False)
    song["tracks"][0]["mute"] = True
    assert sm.pass_cuts(song) == {}
    song["tracks"][0]["mute"] = False
    song["tracks"].append({"id": "tb", "kind": "audio", "name": "B", "solo": True})
    song.update(schema.conform(song, schema.nodes().SONG))
    assert sm.pass_cuts(song) == {}  # another track is soloed


def test_trim_moves_the_bar_and_the_start_together(tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((4, 1))
    item = place(song, tmp_path, sid, at_bar=1, start=1.0)
    whole = sm.pass_cuts(song)[0]
    sm.trim_long(song, item, 1)
    x = sm.long_items(song)[0]
    assert (x["at_bar"], x["start"]) == (2, pytest.approx(1.0 + 1.2))
    # what still plays is the same source sample at the same pass sample
    for was, now in zip(whole, sm.pass_cuts(song)[0], strict=True):
        if now:
            assert was[0]["frm"] == now[0]["frm"]
    sm.trim_long(song, item, -99)  # back, held at the source's first bar line
    x = sm.long_items(song)[0]
    assert x["at_bar"] >= 0 and x["start"] >= limits.LONG_START_RANGE[0]
    assert x["start"] == pytest.approx(1.0 - (1 - x["at_bar"]) * 1.2)


def test_a_track_holding_a_stem_is_not_removed_and_a_fifth_is_refused(tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 1))
    for _ in range(limits.MAX_LONG_AUDIO):
        place(song, tmp_path, sid)
    with pytest.raises(sm.SongError, match="at most"):
        place(song, tmp_path, sid)
    assert "stem" in sm.remove_track(song, "ta")[0]
    assert sm.track_index(song, "ta") is not None


# --- the schema ------------------------------------------------------------------


def test_hostile_items_are_clamped_or_dropped_never_an_error():
    song = audio_song((1, 1))
    good = {"id": "aa", "track_id": "ta", "source": "0123456789abcdef"}
    song["long"] = [
        good
        | {
            "model": MODEL,
            "stems": ["vocals", "kazoo", "vocals", 3],
            "levels": {"vocals": -99, "drums": 3, "kazoo": 1},
            "at_bar": -4,
            "start": 1e12,
            "length": float("nan"),
            "gain": "loud",
            "mute": "yes",
            "bpm": -1,
            "seconds": 1e9,
            "extra": {"x": 1},
        },
        good | {"id": "aa"},  # a repeated id
        good | {"id": "bb", "track_id": "nope"},  # no such track
        good | {"id": "cc", "source": "../../etc"},  # not a source id
        good | {"id": "dd", "model": "../x"},  # a bad model: the recording
        "junk",
        *[good | {"id": f"e{i}"} for i in range(40)],
    ]
    out = schema.conform(song, schema.nodes().SONG)
    # the cap is taken first, then the item on a track that is not there goes
    assert [x["id"] for x in out["long"]] == ["aa", "dd", "e0"]
    first = out["long"][0]
    assert first["stems"] == ["vocals"] and first["levels"] == {"vocals": -24.0}
    assert first["at_bar"] == 0 and first["start"] == limits.LONG_START_RANGE[1]
    assert (
        first["length"] == 0.0
        and first["gain"] == 1.0
        and first["mute"] in (True, False)
    )
    assert first["seconds"] == limits.MAX_SOURCE_SECONDS and "extra" not in first
    assert out["long"][1]["model"] == "" and out["long"][1]["stems"] == []
    assert schema.conform(out, schema.nodes().SONG) == out  # a fixed point


def test_a_song_without_long_audio_has_no_such_key():
    node = schema.nodes().SONG
    assert "long" not in schema.conform(song_dict(), node)
    assert "long" not in node.default()
    emptied = schema.conform(song_dict() | {"long": []}, node)
    assert "long" not in emptied
    # an item on a track that is not audio goes, and takes the key with it
    bad = {"id": "aa", "track_id": "t1", "source": "0123456789abcdef"}
    assert "long" not in schema.conform(song_dict() | {"long": [bad]}, node)


# --- the render ------------------------------------------------------------------


def test_the_export_is_the_stem_sample_for_sample(client, tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 3), (2, 1))  # 5 bars; the first section repeats
    place(song, tmp_path, sid, at_bar=1, start=0.5)
    svc = svc_of(client)
    mix = svc.song_mixdown(song)
    gain = svc.song_gain(song)
    assert len(mix) == 5 * BAR
    src = stem(tmp_path, sid)[int(0.5 * SR) :]
    want = np.zeros_like(mix)
    want[BAR:] = src[: len(mix) - BAR] * gain
    # all of it but the item's own fades (its first 5 ms, and the song's end)
    body = slice(BAR + FADE, len(mix) - FADE)
    assert np.max(np.abs(mix[body] - want[body])) < 1e-12
    assert not mix[:BAR].any()  # nothing before its bar
    assert abs(mix[BAR]).max() < abs(want[BAR + FADE]).max()  # faded in
    assert abs(mix[-1]).max() < 1e-3  # and out at the song's end


def test_the_plans_wavs_stitched_are_the_export(client, tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 3), (2, 1))
    place(song, tmp_path, sid, at_bar=1)
    use_song(client, song)
    plan = client.get("/song/plan.json").json()
    first, second = plan["sections"]
    assert len(first["pass_urls"]) == first["passes"] == 3
    assert first["pass_urls"][0] == first["url"]
    assert len(set(first["pass_urls"])) == 3  # no two passes are one WAV
    pieces = []
    for sec in plan["sections"]:
        for url in sec["pass_urls"]:
            with wave.open(io.BytesIO(client.get(url).content)) as w:
                raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            pieces.append(raw.reshape(-1, 2) / 32767.0)
    played = np.concatenate(pieces)
    export = svc_of(client).song_mixdown(client.app.state.studio.song)
    assert played.shape == export.shape
    assert np.max(np.abs(played - export)) < 1.0 / 32000  # 16-bit rounding
    # the loop is the FIRST pass's slice (here: silence, the item starts at bar 2)
    with wave.open(io.BytesIO(client.get(second["loop_url"]).content)) as w:
        loop = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
    assert np.array_equal(loop, np.round(pieces[3] * 32767).astype("<i2"))
    assert client.get(first["url"] + "&p=9").status_code == 422  # no such pass


def test_a_song_without_long_audio_keeps_its_idents_and_its_plan(client, tmp_path):
    sid = make_source(tmp_path)
    svc = svc_of(client)
    plain = schema.conform(song_dict(), schema.nodes().SONG)
    before = copy.deepcopy(svc._song_idents(plain, ())["passes"])
    assert svc._song_idents(plain, ())["each"] == {}
    plan = svc.song_plan(plain)
    assert all("pass_urls" not in sec for sec in plan["sections"])
    # with an item over the second section only: the first keeps its idents
    # and its WAVs, the second gets a WAV a pass
    held = copy.deepcopy(plain)
    place(held, tmp_path, sid, at_bar=2)
    entry = svc._song_idents(held, ())
    assert entry["passes"][0][0][0] == before[0][0][0]
    assert entry["passes"][0][1][0] == before[0][1][0]
    assert list(entry["each"]) == [1]
    assert entry["passes"][1][0][0]["own"] != before[1][0][0]["own"]
    # taken off again, every ident is the one it was
    sm.remove_long(held, sm.long_items(held)[0]["id"])
    held = schema.conform(held, schema.nodes().SONG)
    assert held == plain
    after = svc._song_idents(held, ())["passes"]
    assert [[p[0] for p in row] for row in after] == [
        [p[0] for p in row] for row in before
    ]


def test_a_missing_source_is_silence_under_another_key(client, tmp_path):
    sid = make_source(tmp_path)
    song = audio_song((1, 2))
    place(song, tmp_path, sid)
    svc = svc_of(client)
    there = svc._song_idents(song, ())["passes"][0][0][0]["own"]
    assert svc.song_mixdown(song).any()
    assert longaudio.status(tmp_path, song["long"][0]) == ""
    assert sources.delete(tmp_path, sid)
    assert "import" in longaudio.status(tmp_path, song["long"][0])
    gone = svc._song_idents(song, ())["passes"][0][0][0]["own"]
    assert gone != there
    assert not svc.song_mixdown(song).any()  # silence, never an error
    # the same file imported and separated again is the same audio again
    assert make_source(tmp_path) == sid
    assert svc._song_idents(song, ())["passes"][0][0][0]["own"] == there


def test_levels_weight_the_stems_and_the_mix_is_the_recording(tmp_path):
    sid = make_source(tmp_path)
    cut = {"source": sid, "model": MODEL, "stems": ["vocals", "drums"]}
    cut |= {
        "levels": {"drums": -6.0},
        "frm": 100,
        "a": -(10**6),
        "b": 10**9,
        "gain": 1.0,
    }
    got = longaudio.read(tmp_path, cut, 2000)
    want = stem(tmp_path, sid)[100:2100] + stem(tmp_path, sid, "drums")[
        100:2100
    ] * 10 ** (-6 / 20)
    assert np.max(np.abs(got - want)) < 1e-7
    mix = cut | {"model": "", "stems": [], "levels": {}}
    assert np.allclose(
        longaudio.read(tmp_path, mix, 2000), stem(tmp_path, sid, "mix")[100:2100]
    )
    # before the source's first sample and past its last: silence
    early = longaudio.read(tmp_path, cut | {"frm": -500}, 1000)
    assert not early[:500].any() and early[600:].any()
    late = longaudio.read(tmp_path, cut | {"frm": SECONDS * SR - 300}, 1000)
    assert late[:300].any() and not late[300:].any()
    # one stem of the set gone: the mix of two is not played as one
    sources.stem_path(tmp_path, sid, "drums", MODEL).unlink()
    assert not longaudio.read(tmp_path, cut, 2000).any()


def test_the_stems_export_holds_it_and_the_master_chain_sees_it(client, tmp_path):
    sid = make_source(tmp_path)
    song = schema.conform(song_dict(), schema.nodes().SONG)
    place(song, tmp_path, sid)
    svc = svc_of(client)
    by_name = dict(svc.song_stems(song))
    assert "audio-1.wav" in by_name and by_name["audio-1.wav"].any()
    gain = svc.song_gain(song)
    src = stem(tmp_path, sid)
    n = len(by_name["audio-1.wav"])
    body = slice(FADE, min(n, len(src)) - FADE)
    assert np.max(np.abs(by_name["audio-1.wav"][body] - src[body] * gain)) < 1e-12
    # the stems add up to the mix (no master chain)
    total = sum(by_name.values())
    assert np.max(np.abs(total - svc.song_mixdown(song))) < 1e-9
    # a muted item's track no longer sounds
    sm.set_long(song, song["long"][0]["id"], mute=True)
    assert not song_play._sounds(song, song["tracks"][3])
    sm.set_long(song, song["long"][0]["id"], mute=False)
    # the master chain runs over the long audio too
    dry = svc.song_mixdown(song)
    wet = svc.song_mixdown(song, [{"kind": "drive", "params": {}}])
    assert wet.shape == dry.shape and not np.allclose(wet, dry)


def test_solo_this_cell_plays_no_long_audio(client, tmp_path):
    sid = make_source(tmp_path)
    song = schema.conform(song_dict(), schema.nodes().SONG)
    place(song, tmp_path, sid)
    svc = svc_of(client)
    solo = song_play.solo_song(song, "t1")
    assert sm.pass_cuts(solo) == {}
    plain = schema.conform(song_dict(), schema.nodes().SONG)
    assert (
        svc._song_idents(solo, ())["passes"][0][1][0]
        == svc._song_idents(song_play.solo_song(plain, "t1"), ())["passes"][0][1][0]
    )


# --- the route -------------------------------------------------------------------


def post(client, **data):
    return client.post("/song/long", data=data, headers=HX)


def toast(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]


def long(client) -> list[dict]:
    return sm.long_items(client.app.state.studio.song)


def test_add_places_a_stem_and_the_song_takes_its_tempo(client, tmp_path):
    sid = make_source(tmp_path)
    sources.update_meta(
        tmp_path, sid, lambda m: m["user"].update(bpm=93.5, offset=0.25)
    )
    use_song(client, audio_song((2, 1), (2, 2), bpm=120))
    r = post(
        client, op="add", source=sid, model=MODEL, stems="vocals", levels="-3",
        section_id="s2", at_bar="1", tempo="on",
    )  # fmt: skip
    assert r.status_code == 200, r.text
    (item,) = long(client)
    assert item["stems"] == ["vocals"] and item["levels"] == {"vocals": -3.0}
    assert item["at_bar"] == 3  # bar 2 of the second section's first pass
    assert item["start"] == 0.25 and item["bpm"] == 93.5  # the Lab's bar line
    assert item["seconds"] == pytest.approx(SECONDS)
    assert client.app.state.studio.song["bpm"] == 93.5
    assert "bars 4-6" in toast(r) and "93.5 bpm" in toast(r)
    assert "song-arranger" in r.text and "long-part" in r.text


def test_add_from_the_lab_reads_the_lanes_and_makes_an_audio_track(client, tmp_path):
    sid = make_source(tmp_path)

    def lanes(m):
        m["picked"] = ["drums", "bass"]
        m["levels"] = {MODEL: {"bass": 2.0}}

    sources.update_meta(tmp_path, sid, lanes)
    song = audio_song((2, 1))
    song["tracks"] = []
    use_song(client, song)
    # the Lab's form rides along: its own `stems` flag and a 1-based bar
    r = post(client, op="add", source=sid, mix="lanes", stems="1",
             section_id="s1", at_bar="2", bar_base="1")  # fmt: skip
    assert r.status_code == 200, r.text
    (item,) = long(client)
    assert item["stems"] == ["drums", "bass"] and item["levels"] == {"bass": 2.0}
    assert item["at_bar"] == 1
    tracks = client.app.state.studio.song["tracks"]
    assert [t["kind"] for t in tracks] == ["audio"]
    assert client.app.state.studio.song["bpm"] == BPM  # no tempo asked for


def test_set_remove_tempo_and_what_is_refused(client, tmp_path):
    sid = make_source(tmp_path)
    use_song(client, audio_song((4, 1), (4, 1)))
    assert (
        post(client, op="add", source=sid, model=MODEL, stems="drums").status_code
        == 200
    )
    item = long(client)[0]["id"]
    assert (
        post(client, op="set", id=item, bar="3", gain="0.5", name="Kit").status_code
        == 200
    )
    x = long(client)[0]
    assert (x["at_bar"], x["gain"], x["name"]) == (2, 0.5, "Kit")
    assert post(client, op="set", id=item, section_id="s2").status_code == 200
    assert long(client)[0]["at_bar"] == 4
    assert post(client, op="set", id=item, bars="2").status_code == 200
    assert long(client)[0]["length"] == pytest.approx(2.4)
    assert post(client, op="set", id=item, bars="").status_code == 200
    assert long(client)[0]["length"] == 0.0
    assert post(client, op="set", id=item, mute="on").status_code == 200
    assert long(client)[0]["mute"] is True
    assert post(client, op="set", id=item, trim="1").status_code == 200
    assert long(client)[0]["at_bar"] == 5
    # refusals: a 422 toast, nothing changed
    before = copy.deepcopy(long(client))
    for bad in (
        {"op": "set", "id": "nope", "bar": "1"},
        {"op": "set", "id": item, "bar": "x"},
        {"op": "set", "id": item, "gain": "nan"},
        {"op": "add", "source": "0" * 16},
        {"op": "add", "source": sid, "model": stems.HTDEMUCS_6S.id, "stems": "piano"},
        {"op": "tempo", "id": item},  # the Lab has no tempo for noise
        {"op": "remove", "id": "nope"},
    ):
        assert post(client, **bad).status_code == 422, bad
    assert post(client, op="what").status_code == 422
    assert long(client) == before
    assert post(client, op="remove", id=item).status_code == 200
    assert long(client) == [] and "long" not in client.app.state.studio.song


def test_every_edit_is_one_undo_step(client, tmp_path):
    sid = make_source(tmp_path)
    use_song(client, audio_song((2, 1)))
    post(client, op="add", source=sid, model=MODEL, stems="vocals")
    item = long(client)[0]["id"]
    post(client, op="set", id=item, bar="2")
    assert client.post("/song/undo", headers=HX).status_code == 200
    assert long(client)[0]["at_bar"] == 0
    assert client.post("/song/undo", headers=HX).status_code == 200
    assert long(client) == []
    assert client.post("/song/redo", headers=HX).status_code == 200
    assert [x["id"] for x in long(client)] == [item]


def test_the_project_bundle_names_the_source_and_never_carries_it(client, tmp_path):
    """Sources never travel (the Lab's rule, kept): a bundle of a song under
    a whole stem holds the item, by the source's id and name, and no audio
    of it; on another machine it is "missing" until that song is imported
    and separated there — same content, same id, so it finds it again."""
    import zipfile

    sid = make_source(tmp_path)
    use_song(client, audio_song((2, 1)))
    post(client, op="add", source=sid, model=MODEL, stems="vocals")
    bundle = client.get("/project.zip")
    assert bundle.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert not [n for n in names if "source" in n or n.endswith(".flac")]
    assert len(bundle.content) < 200_000  # the stems alone are megabytes
    with TestClient(create_app(data_dir=tmp_path / "elsewhere")) as other:
        files = {"file": ("p.zip", bundle.content, "application/zip")}
        assert other.post("/project/import", files=files, headers=HX).status_code == 200
        (item,) = long(other)
        assert item["source"] == sid and item["name"].startswith("tune")
        svc = svc_of(other)
        assert not svc.song_mixdown(other.app.state.studio.song).any()
        assert "long-row missing" in other.get("/view/song").text
        assert make_source(tmp_path / "elsewhere") == sid  # imported there
        assert svc.song_mixdown(other.app.state.studio.song).any()
        assert "long-row missing" not in other.get("/view/song").text


def test_the_labs_delete_says_where_the_song_plays_the_source(client, tmp_path):
    sid = make_source(tmp_path)
    lab = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/lab"}
    assert "which will go silent" not in client.get("/view/lab").text
    use_song(client, audio_song((2, 1)))
    post(client, op="add", source=sid, model=MODEL, stems="vocals")
    name = long(client)[0]["name"]
    page = client.get("/view/lab").text
    assert "which will go silent" in page and name.split(" ")[0] in page
    # taken off the song, the undo ring still holds it: said too
    post(client, op="remove", id=long(client)[0]["id"])
    assert client.app.state.studio.long_usage()[sid] == {"names": [], "undo": 1}
    assert "undo step" in client.get("/view/lab").text
    # and the delete is allowed: the item stays, marked missing
    client.post("/song/undo", headers=HX)
    r = client.post("/lab/delete", data={"source": sid}, headers=lab)
    assert r.status_code == 200
    assert len(long(client)) == 1
    page = client.get("/view/song").text
    assert "long-row missing" in page and "import this song in the Lab again" in page


def test_placing_changes_no_state_until_it_is_placed_and_saves_and_loads(
    client, tmp_path
):
    sid = make_source(tmp_path)
    s = client.app.state.studio
    before = json.dumps(s._payload())
    assert sources.read_meta(tmp_path, sid) is not None  # a source alone: no state
    assert json.dumps(s._payload()) == before
    use_song(client, audio_song((2, 1)))
    post(client, op="add", source=sid, model=MODEL, stems="vocals")
    saved = json.loads(json.dumps(s._payload()))
    held = schema.conform(saved["song"], schema.nodes().SONG)
    assert held["long"] == s.song["long"]


# --- the playlist's drags (project 3): a drop names the timeline's bar ---------------


def test_add_takes_the_timelines_bar_for_a_drop_on_a_repeats_pass(client, tmp_path):
    """`section_id` + `at_bar` can only name a section's FIRST pass. A stem
    dropped on the playlist may land on an echo, so `add` also takes `bar`
    (1-based, the timeline's own — as `set` does)."""
    sid = make_source(tmp_path)
    use_song(client, audio_song((2, 1), (2, 2), bpm=120))
    r = post(client, op="add", source=sid, model=MODEL, stems="vocals", bar="6")
    assert r.status_code == 200, r.text
    (item,) = long(client)
    assert item["at_bar"] == 5  # bar 2 of the second section's SECOND pass
    # `bar` wins over a section named beside it; nonsense is a toast
    r = post(
        client, op="add", source=sid, model=MODEL, stems="vocals",
        bar="2", section_id="s2", at_bar="1",
    )  # fmt: skip
    assert long(client)[1]["at_bar"] == 1
    r = post(client, op="add", source=sid, model=MODEL, stems="vocals", bar="x")
    assert r.status_code == 422 and len(long(client)) == 2
    r = post(client, op="add", source=sid, model=MODEL, stems="vocals", bar="-3")
    assert r.status_code == 200 and long(client)[2]["at_bar"] == 0


def test_a_strip_carries_what_a_drag_and_its_edges_need(client, tmp_path):
    """Each `.long-part` says which item it is, the item's first bar, how
    many bars it plays and how many of them lie before this strip (so a
    drag keeps the bar it was grabbed by); the strip it starts in has the
    head's handle, the one it ends in the tail's."""
    sid = make_source(tmp_path)
    use_song(client, audio_song((2, 1), (2, 2), bpm=120))
    post(client, op="add", source=sid, model=MODEL, stems="vocals", bar="2")
    (item,) = long(client)
    post(client, op="set", id=item["id"], bars="4")  # bars 2-5: two sections
    rows = longaudio.rows(tmp_path, client.app.state.studio.song)
    first, second = rows["parts"][("s1", "ta")][0], rows["parts"][("s2", "ta")][0]
    assert (first["head"], first["tail"], first["skip"]) == (True, False, 0)
    assert (second["head"], second["tail"], second["skip"]) == (False, True, 1)
    assert first["at"] == second["at"] == 1 and first["bars"] == second["bars"] == 4
    html = client.get("/view/song").text
    parts = re.findall(r'<div class="long-part[^>]*>', html)
    assert len(parts) == 2 and all('draggable="true"' in p for p in parts)
    assert 'data-at="1" data-bars="4" data-skip="0"' in parts[0]
    assert 'data-at="1" data-bars="4" data-skip="1"' in parts[1]
    assert html.count("long-edge long-edge-l") == 1
    assert html.count("long-edge long-edge-r") == 1
    assert html.index("long-edge-l") < html.index("long-edge-r")
