"""The song's playback plan and its export (export equals playback):
`/song/plan.json` lists each section's WAVs and where they
play; `/export/song.wav` is the same section renders in the same order,
with the same tail folds, repeats and song-wide gain. Stems are one per
track, named by kind and index. Length caps refuse before anything
renders."""

from __future__ import annotations

import io
import time
import wave
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.core import SR
from synthlab.studio import limits, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.server import create_app
from synthlab.synth import Patch

SHORT = Patch(name="short", release=0.05).to_json()
REVERB = [{"kind": "reverb", "params": {"decay": 1.5, "mix": 0.5}}]
# one echo 0.1 s later, nothing after it: the master chain's memory is 0.1 s
ECHO = [
    {
        "kind": "delay",
        "params": {"time": 0.1, "feedback": 0.0, "mix": 0.5, "ping_pong": 0.0},
    }
]


def _notes(cells, bars=1, spb=4) -> dict:
    return {"cells": [list(c) for c in cells], "bars": bars, "steps_per_bar": spb}


def _three_section_song() -> dict:
    """Intro (2 bars, a 1-bar riff: 2 cycles, x2), Verse (2 bars, a 2-bar
    riff over a 1-bar beat, x3) and Outro (1 bar, x1). The intro's track
    has a reverb, so its tail rings into the verse's head."""
    song = sm.new_song(120.0, "A1", "minor")
    wet = sm.add_track(song, "synth", "Wet", patch=SHORT, fx=REVERB)
    dry = sm.add_track(song, "synth", "Dry", patch=SHORT)
    drums = sm.add_track(song, "drums", "Drums")
    intro = sm.add_section(song, "Intro", 2)
    verse = sm.add_section(song, "Verse", 2)
    outro = sm.add_section(song, "Outro", 1)
    riff = sm.add_pattern(song, "notes", "r", _notes((("A2", 0), ("E3", 3))))
    line = sm.add_pattern(song, "notes", "l", _notes((("C3", 1), ("G2", 6)), bars=2))
    tag = sm.add_pattern(song, "notes", "t", _notes((("A3", 2),)))
    beat = sm.add_pattern(
        song, "drums", "b", {"drums": [["kick", 0], ["snare", 2]], "bars": 1}
    )
    sm.set_cell(song, intro, wet, riff)
    sm.set_cell(song, verse, dry, line)
    sm.set_cell(song, verse, drums, beat)
    sm.set_cell(song, outro, dry, tag)
    song["sections"][0]["repeats"] = 2
    song["sections"][1]["repeats"] = 3
    return song


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _use(client, song: dict, master=()) -> None:
    s = client.app.state.studio
    s.song = schema.conform(song, schema.nodes().SONG)
    s.fx_master = list(master)


def _pcm(data: bytes) -> np.ndarray:
    """16-bit WAV bytes -> the raw int16 frames, (n, 2)."""
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 2
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").reshape(-1, 2).astype(np.int32)


def _assemble(client, plan: dict) -> np.ndarray:
    """The song as the browser plays it: per section, its first pass
    (`url`) and then its loop (`loop_url`) until `passes` cycles have
    played, each section starting at `start_sample`."""
    out = []
    at = 0
    for sec in plan["sections"]:
        assert sec["start_sample"] == at
        head = _pcm(client.get(sec["url"]).content)
        loop = _pcm(client.get(sec["loop_url"]).content)
        assert len(head) == len(loop) == sec["cycle_samples"]
        out += [head] + [loop] * (sec["passes"] - 1)
        at += sec["cycle_samples"] * sec["passes"]
    assert at == plan["song_samples"]
    return np.concatenate(out)


def test_the_export_is_the_playback_plan_sample_for_sample(client):
    _use(client, _three_section_song())
    plan = client.get("/song/plan.json").json()
    assert [s["passes"] for s in plan["sections"]] == [4, 3, 1]
    played = _assemble(client, plan)
    r = client.get("/export/song.wav")
    assert r.status_code == 200
    exported = _pcm(r.content)
    assert exported.shape == played.shape
    assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
    assert np.max(np.abs(exported)) > 1000  # not trivially silent
    # the test means something: the intro's reverb rings into the verse's
    # first pass, so its head is not its loop
    verse = plan["sections"][1]
    head = _pcm(client.get(verse["url"]).content)
    loop = _pcm(client.get(verse["loop_url"]).content)
    assert np.max(np.abs(head - loop)) > 100


def test_layer_tracks_own_sequencing_is_in_the_plan_and_the_export(client):
    """Layer tracks playing their own patterns' arp, slides and velocities
    — tiled under the intro's 1-bar riff, on the verse's own grid — still
    export exactly what the player plays."""
    song = _three_section_song()
    arp = sm.add_track(song, "synth", "Arp", patch=SHORT)
    intro, verse = (s["id"] for s in song["sections"][:2])
    arp_pat = _notes((("A3", 0), ("C4", 3), ("E4", 5)), bars=2) | {
        "arp_mode": "up",
        "arp_octaves": 2,
        "velocities": {"0": 0.3, "4": 1.0},
    }
    glide = _notes((("E3", 0), ("G3", 2), ("A3", 5)), bars=2) | {
        "slides": [2, 5],
        "velocities": {"2": 0.4},
    }
    sm.set_cell(song, intro, arp, sm.add_pattern(song, "notes", "a", arp_pat))
    sm.set_cell(song, verse, arp, sm.add_pattern(song, "notes", "g", glide))
    _use(client, song)
    s = client.app.state.studio
    lanes = [sm.section_arrangement(s.song, sec) for sec in s.song["sections"][:2]]
    assert [p.flat for p in lanes] == [True, False]
    assert lanes[0].layers[0]["arp_mode"] == "up"
    assert lanes[1].layers[0]["slides"] == [2, 5]
    plan = client.get("/song/plan.json").json()
    played = _assemble(client, plan)
    exported = _pcm(client.get("/export/song.wav").content)
    assert exported.shape == played.shape
    assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
    assert np.max(np.abs(exported)) > 1000


def test_the_plan_shape(client):
    song = _three_section_song()
    _use(client, song)
    plan = client.get("/song/plan.json").json()
    assert set(plan) == {
        "sr",
        "bpm",
        "gain",
        "song_samples",
        "song_seconds",
        "sections",
    }
    assert plan["sr"] == SR and plan["bpm"] == 120.0
    assert 0 < plan["gain"] and plan["song_seconds"] == plan["song_samples"] / SR
    intro, verse, outro = plan["sections"]
    assert intro["section_id"] == song["sections"][0]["id"]
    assert intro["name"] == "Intro"
    n1 = sm.cycle_samples(1, 120.0)
    assert (intro["bars"], intro["cycle_bars"], intro["cycles"]) == (2, 1, 2)
    assert (intro["repeats"], intro["passes"], intro["cycle_samples"]) == (2, 4, n1)
    assert intro["cycle_seconds"] == n1 / SR
    assert (verse["cycle_bars"], verse["cycles"], verse["passes"]) == (2, 1, 3)
    assert verse["start_sample"] == 4 * n1
    assert verse["start_seconds"] == 4 * n1 / SR
    assert outro["start_sample"] == 4 * n1 + 3 * sm.cycle_samples(2, 120.0)
    assert [s["start_bar"] for s in plan["sections"]] == [0, 4, 10]
    for sec in plan["sections"]:
        assert set(sec) == {
            "section_id",
            "name",
            "index",
            "bars",
            "repeats",
            "start_bar",
            "cycle_bars",
            "cycles",
            "passes",
            "cycle_samples",
            "cycle_seconds",
            "start_sample",
            "start_seconds",
            "url",
            "loop_url",
            # the mixer's meters: what the section's render measured
            # of each stem, {track or return id: [peak, rms]} at the
            # song's gain — there once the section has rendered, which a
            # plan's gain has just made sure of
            "levels",
        }
        assert sec["levels"] and all(
            0 < rms <= peak for peak, rms in sec["levels"].values()
        )
        sid = sec["section_id"]
        assert sec["url"].startswith(f"/song/section/{sid}.wav?")
        assert sec["loop_url"].startswith(f"/song/section/{sid}.wav?loop=1")


def test_an_empty_song_has_an_empty_plan(client):
    _use(client, sm.new_song(120.0, "A1", "minor"))
    plan = client.get("/song/plan.json").json()
    assert plan["sections"] == [] and plan["song_samples"] == 0
    assert client.get("/export/song.wav").status_code == 404


def test_the_song_gain_is_one_number_from_the_loudest_section(client):
    """Loud and Quiet play the same riff; Quiet's track is at gain 0.5. One
    song-wide gain, so Quiet stays half as loud rather than being
    normalized up to Loud's level."""
    song = sm.new_song(120.0, "A1", "minor")
    loud_t = sm.add_track(song, "synth", "Loud", patch=SHORT)
    quiet_t = sm.add_track(song, "synth", "Quiet", patch=SHORT, gain=0.5)
    riff = sm.add_pattern(song, "notes", "r", _notes((("A2", 0), ("C3", 2))))
    loud = sm.add_section(song, "Loud", 1)
    quiet = sm.add_section(song, "Quiet", 1)
    sm.set_cell(song, loud, loud_t, riff)
    sm.set_cell(song, quiet, quiet_t, riff)
    _use(client, song)
    plan = client.get("/song/plan.json").json()
    a = _pcm(client.get(plan["sections"][0]["url"]).content)
    b = _pcm(client.get(plan["sections"][1]["url"]).content)
    peak = np.max(np.abs(a))
    assert 0.8 * 32767 < peak <= 0.97 * 32767  # the loudest lands near the target
    assert np.max(np.abs(2 * b - a)) <= 3  # Quiet is exactly half of Loud


@pytest.mark.parametrize("master", [REVERB, ECHO], ids=["reverb", "echo"])
def test_master_fx_are_part_of_each_section_so_export_equals_playback(client, master):
    """Ruling 7: the master chain runs per section render and its tail folds
    like the stems' tails, so the export is the stitched plan, sample for
    sample, even with a master reverb (whose wet level a whole-song pass
    set from the whole song's peak, 0.3 FS away from the per-section WAVs)."""
    _use(client, _three_section_song())
    dry = _pcm(client.get("/export/song.wav").content)
    _use(client, _three_section_song(), master=master)
    plan = client.get("/song/plan.json").json()
    played = _assemble(client, plan)
    exported = _pcm(client.get("/export/song.wav").content)
    assert exported.shape == played.shape
    assert np.max(np.abs(exported - played)) <= 1  # 16-bit rounding
    assert np.max(np.abs(exported - dry)) > 1000  # the master chain is heard


def test_the_master_chain_runs_once_per_section(client, monkeypatch):
    """Each section's master-processed cycle is cached (`_master_audio`):
    the plan renders every section through the chain once, and its WAVs
    and the export reuse those (they used to run it two or three times a
    section, per request)."""
    from synthlab.studio import service

    runs: list = []
    real = service.section_master_audio
    monkeypatch.setattr(
        service,
        "section_master_audio",
        lambda *a, **k: runs.append(1) or real(*a, **k),
    )
    _use(client, _three_section_song(), master=REVERB)
    plan = client.get("/song/plan.json").json()
    assert len(runs) == len(plan["sections"])
    _assemble(client, plan)
    client.get("/export/song.wav")
    assert len(runs) == len(plan["sections"])


def test_stems_are_named_by_track_kind_and_index(client, tmp_path):
    """drums.wav, synth-N.wav, audio-N.wav: N is the track's place among
    its kind, never its (user-supplied) name."""
    from synthlab.studio.clips import save_clip

    save_clip(tmp_path, "take", 0.1 * np.ones((SR // 2, 2)))
    song = _three_section_song()
    song["tracks"][0]["name"] = "../../evil"
    song["tracks"][1]["name"] = "A / B.wav"
    vox = sm.add_track(song, "audio", "..\\vox")
    song["sections"][2]["audio"].append(
        {"track_id": vox, "clip": "take", "at_bar": 0, "gain": 1.0}
    )
    _use(client, song)
    r = client.get("/export/stems.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        names = z.namelist()
        stems = {n: _pcm(z.read(n)) for n in names}
    assert names == ["synth-1.wav", "synth-2.wav", "drums.wav", "audio-1.wav"]
    song_pcm = _pcm(client.get("/export/song.wav").content)
    for pcm in stems.values():
        assert pcm.shape == song_pcm.shape
        assert np.max(np.abs(pcm)) > 100
    # the stems add up to the song (no master FX here)
    total = sum(stems.values())
    assert np.max(np.abs(total - song_pcm)) <= len(stems)


def test_a_muted_or_empty_track_has_no_stem(client):
    song = _three_section_song()
    sm.add_track(song, "synth", "Unused", patch=SHORT)
    song["tracks"][1]["mute"] = True  # Dry
    _use(client, song)
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        assert z.namelist() == ["synth-1.wav", "drums.wav"]


def _spy_renders(monkeypatch) -> list:
    calls: list = []
    real = render.build_song
    monkeypatch.setattr(
        render, "build_song", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    return calls


def _long_song() -> dict:
    """One 64-bar section at 40 bpm, twice: 768 s, over the song cap
    (480 s since Oct 2026; one pass, 384 s, was over the old 240)."""
    song = sm.new_song(40.0, "A1", "minor")
    t = sm.add_track(song, "synth", "Lead", patch=SHORT)
    sid = sm.add_section(song, "Long", 64)
    sm.set_cell(song, sid, t, sm.add_pattern(song, "notes", "p", _notes((("A2", 0),))))
    song["sections"][0]["repeats"] = 2
    return song


def _over_its_own_cap() -> dict:
    """A 3-bar riff in a 40-bar section at 60 bpm: the lcm does not divide
    the section, so the cycle is all 40 bars (160 s) — under the song cap,
    over the section cap."""
    song = sm.new_song(60.0, "A1", "minor")
    t = sm.add_track(song, "synth", "Lead", patch=SHORT)
    sid = sm.add_section(song, "Long", 40)
    riff = sm.add_pattern(song, "notes", "p", _notes((("A2", 0),), bars=3))
    sm.set_cell(song, sid, t, riff)
    return song


@pytest.mark.parametrize("make", [_long_song, _over_its_own_cap])
def test_an_over_long_song_is_refused_before_anything_renders(
    client, monkeypatch, make
):
    song = make()
    assert limits.MAX_SECTION_SECONDS < 160.0 <= limits.MAX_SONG_SECONDS
    _use(client, song)
    sid = song["sections"][0]["id"]
    calls = _spy_renders(monkeypatch)
    t0 = time.perf_counter()
    for url in (
        "/song/plan.json",
        f"/song/section/{sid}.wav",
        f"/song/section/{sid}.wav?loop=1",
        "/export/song.wav",
        "/export/stems.zip",
    ):
        r = client.get(url)
        assert r.status_code == 422, url
        assert "toast" in r.text
    with pytest.raises(render.RenderError):
        client.app.state.svc.section_result(client.app.state.studio.song, 0)
    assert calls == []
    assert time.perf_counter() - t0 < 2.0


def test_a_bad_section_request_is_a_404_or_422_never_a_500(client):
    _use(client, _three_section_song())
    assert client.get("/song/section/nope.wav").status_code == 404
    sid = client.app.state.studio.song["sections"][0]["id"]
    assert client.get(f"/song/section/{sid}.wav?loop=maybe").status_code == 422


def test_a_section_wav_is_served_from_the_render_cache(client, monkeypatch):
    _use(client, _three_section_song())
    plan = client.get("/song/plan.json").json()
    first = client.get(plan["sections"][1]["url"]).content
    calls = _spy_renders(monkeypatch)
    assert client.get(plan["sections"][1]["url"]).content == first
    assert calls == []


def test_fetching_a_plans_wavs_rekeys_nothing(client, tmp_path, monkeypatch):
    """After one plan, fetching every section WAV neither recomputes a
    section key nor the song gain, and each take file is hashed at most
    once across the plan and all the fetches."""
    from synthlab.studio import clips, service, song_play
    from synthlab.studio.clips import save_clip

    save_clip(tmp_path, "vox", 0.1 * np.ones((SR // 4, 2)))
    song = _three_section_song()
    audio = sm.add_track(song, "audio", "Vox")
    for sec in song["sections"]:
        sec["audio"].append({"track_id": audio, "clip": "vox", "at_bar": 0, "gain": 1})
    _use(client, song)
    keys, gains, hashed = [], [], []
    real_key, real_gain, real_sha = (
        service.section_key,
        song_play.gain_of,
        clips._file_sha1,
    )
    monkeypatch.setattr(
        service, "section_key", lambda *a, **k: keys.append(1) or real_key(*a, **k)
    )
    monkeypatch.setattr(
        song_play, "gain_of", lambda *a, **k: gains.append(1) or real_gain(*a, **k)
    )
    monkeypatch.setattr(
        clips, "_file_sha1", lambda path: hashed.append(path) or real_sha(path)
    )
    plan = client.get("/song/plan.json").json()
    assert len(keys) == len(plan["sections"]) and len(gains) == 1
    keys.clear()
    gains.clear()
    for sec in plan["sections"]:
        assert client.get(sec["url"]).status_code == 200
        assert client.get(sec["loop_url"]).status_code == 200
    assert keys == [] and gains == []
    assert len(hashed) == len(set(hashed)) <= 1


def test_a_muted_drums_track_is_silent_and_keeps_the_cycle(client):
    """Muting the drums track drops its hits from playback, the export and
    the stems, but its pattern still sets the cycle: a 2-bar beat under a
    1-bar riff keeps a 2-bar cycle."""
    song = sm.new_song(120.0, "A1", "minor")
    lead = sm.add_track(song, "synth", "Lead", patch=SHORT)
    drums = sm.add_track(song, "drums", "Drums")
    sid = sm.add_section(song, "S", 2)
    sm.set_cell(
        song, sid, lead, sm.add_pattern(song, "notes", "r", _notes((("A2", 0),)))
    )
    beat = {"drums": [["kick", 0], ["snare", 6]], "bars": 2}
    bid = sm.add_pattern(song, "drums", "b", beat)
    sm.set_cell(song, sid, drums, bid)
    _use(client, song)
    loud = _pcm(client.get("/export/song.wav").content)
    song["tracks"][1]["mute"] = True
    _use(client, song)
    plan = client.get("/song/plan.json").json()
    assert plan["sections"][0]["cycle_bars"] == 2
    muted = _pcm(client.get("/export/song.wav").content)
    assert np.max(np.abs(_assemble(client, plan) - muted)) <= 1
    # the same as the beat with no hits at all (same clock, same cycle)
    song["tracks"][1]["mute"] = False
    song["patterns"][bid]["drums"] = []
    _use(client, song)
    silent_beat = _pcm(client.get("/export/song.wav").content)
    assert np.max(np.abs(muted - silent_beat)) <= 1
    assert np.max(np.abs(muted - loud)) > 1000
    song["tracks"][1]["mute"] = True
    song["patterns"][bid]["drums"] = beat["drums"]
    _use(client, song)
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        assert z.namelist() == ["synth-1.wav"]
