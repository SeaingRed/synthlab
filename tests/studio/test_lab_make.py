"""What a Lab region becomes beyond a clip (`labmake.py`, `routes/lab.py`
`/lab/vocal`, `/lab/drums`, `/lab/place`): a vocal instrument and vocal
chops, a drum pattern on the grid and in the song's pool, a kit of
one-shots, and a take on the song.

The material is known-answer audio (`tests/songs.py`): a sung-like line and
a drum loop the studio's own drum machine rendered. A part imported on its
own is read from the mix, which is the Lab's own fallback; where a test is
about the *stem* being read, a fake separator hands the whole mix to one
stem (`OneStem`)."""

from __future__ import annotations

import html
import json
import re
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import Synth, sampler
from synthlab.audio import wav_bytes
from synthlab.core import SR
from synthlab.studio import limits, sources
from synthlab.studio.clips import list_clips, load_clip
from synthlab.studio.server import create_app
from tests.songs import VOICE_HELD, VOICE_LINE, drum_loop, voice_line
from tests.studio.lab_helpers import LAB, OneStem, separate, source_id, upload

BPM = 120.0
BAR = 240.0 / BPM
BEAT_ROWS = {
    "kick": "x.....x...x.....x.....x...x.....",
    "snare": "....x.......x.......x.......x...",
    "hat": "..x...x...x...x...x...x...x...x.",
}


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _post(client, path: str, **data):
    return client.post(path, data={"source": source_id(client), **data}, headers=LAB)


def _said(r) -> str:
    """What the page says the last action did (`#lab-said`), as text."""
    m = re.search(r'<div class="lab-said"[^>]*>(.*?)</div>', r.text, re.S)
    return html.unescape(m.group(1)) if m else ""


def _toast(r) -> str:
    return json.loads(r.headers.get("HX-Trigger") or "{}")["studio-toast"]["message"]


def _on_grid(client, bars: int) -> None:
    """Put the song on a 120 bpm grid from its first sample, region = all."""
    sources.update_meta(
        client.app.state.svc.data_dir,
        source_id(client),
        lambda m: m["user"].update(bpm=BPM, offset=0.0),
    )
    r = _post(client, "/lab/region", mode="bars", bar="1", bars=str(bars))
    assert r.status_code == 200


@pytest.fixture
def sung(client) -> TestClient:
    assert upload(client, wav_bytes(voice_line())).status_code == 200
    whole = sum(seconds for _n, seconds in VOICE_LINE)
    _post(client, "/lab/region", mode="seconds", start="0", length=str(whole))
    return client


@pytest.fixture
def beat(client) -> TestClient:
    assert (
        upload(client, wav_bytes(drum_loop(BEAT_ROWS, bpm=BPM)[0])).status_code == 200
    )
    _on_grid(client, 2)
    return client


def _steps(rows: dict, lane: str) -> list[int]:
    return [i for i, ch in enumerate(rows.get(lane, "")) if ch == "x"]


# --- the vocal -----------------------------------------------------------------


def test_the_voice_is_the_held_note_looped_rooted_and_in_formant_mode(sung):
    r = _post(sung, "/lab/vocal", what="instrument", name="la")
    assert r.status_code == 200 and r.headers["HX-Push-Url"] == "/view/editor"
    said = _toast(r)
    assert "held C4" in said and "+12 cents" in said and "% periodic" in said
    assert "not a vocal stem" in said  # no stems: it says where it read from
    p = sung.app.state.svc.state.patch
    assert p.name == "la voice" and p.osc1 == "sampler"
    assert p.sampler_mode == "formant" and p.sampler_loop == "on"
    assert p.sampler_root == round(VOICE_HELD) and abs(p.osc1_detune + 12) < 5
    # the loop sits inside the held note, the note starts at its start
    total = sampler.seconds(p.sampler_sample)
    held_at = VOICE_LINE[0][1] + VOICE_LINE[1][1]
    held_end = held_at + VOICE_LINE[2][1]
    assert abs(p.sampler_start * total - held_at) < 0.06
    assert held_at < p.sampler_loop_start * total < p.sampler_loop_end * total
    assert p.sampler_loop_end * total <= held_end and p.sampler_end <= 1.0
    assert (p.sampler_loop_end - p.sampler_loop_start) * total > 0.07
    assert p.sustain > 0.5 and p.release > 0.1  # it holds, and lets go


@pytest.mark.parametrize("up", [-12, -5, 0, 7, 12])
def test_the_voice_plays_in_tune_on_every_key_and_holds_past_its_note(sung, up):
    """The loop is why a 0.9 s note outlasts the recording's; formant mode
    is why an octave up is the same singer. Both are the engine's: this
    holds the fields the Lab set to what they are for."""
    _post(sung, "/lab/vocal", what="instrument")
    p = sung.app.state.svc.state.patch
    key = p.sampler_root + up
    audio = Synth(p).note(key, 3.0)
    late = audio[int(2.0 * SR) : int(2.6 * SR)]  # after the held note's own length
    played, sure = sampler.detect_pitch(late)
    assert sure > 0.6 and abs(played - key) < 0.15, (played, sure)
    hop = SR // 50
    mono = late.mean(axis=1)[: len(late) // hop * hop].reshape(-1, hop)
    level = 10 * np.log10(np.mean(mono**2, axis=1))
    assert level.max() - level.min() < 9.0  # a loop seam, not a gap or a click


def test_nothing_pitched_falls_back_to_the_plain_region_and_says_so(beat):
    r = _post(beat, "/lab/vocal", what="instrument", name="thump")
    assert r.status_code == 200
    assert "No steady sung note" in _toast(r) and "tape-style" in _toast(r)
    p = beat.app.state.svc.state.patch
    assert p.name == "thump" and p.sampler_mode == "resample"
    assert p.sampler_loop == "off" and p.sampler_start == 0.0 and p.sampler_end == 1.0


def test_vocal_chops_put_each_syllable_on_a_key(sung):
    r = _post(sung, "/lab/vocal", what="chops", name="la")
    assert r.status_code == 200 and "3 vocal chops" in _toast(r)
    p = sung.app.state.svc.state.patch
    slices = sampler.parse_slices(p.sampler_slices)
    assert p.name == "la chops" and len(slices) == 3 and p.sampler_mode == "resample"
    total = sampler.seconds(p.sampler_sample)
    at = 0.0
    sung_spans = []
    for note, seconds in VOICE_LINE:
        if note is not None:
            sung_spans.append((at, at + seconds))
        at += seconds
    for s, (a, b) in zip(slices, sung_spans, strict=True):
        assert abs(s.a * total - a) < 0.04 and abs(s.b * total - b) < 0.04


def test_one_syllable_is_the_region_whole_and_says_so(client):
    t = np.arange(int(SR * 1.5)) / SR
    tone = np.sin(2 * np.pi * 220 * t) * np.minimum(1, np.minimum(t, 1.5 - t) / 0.05)
    assert upload(client, wav_bytes(tone * 0.5)).status_code == 200
    _post(client, "/lab/region", mode="seconds", start="0", length="1.5")
    r = _post(client, "/lab/vocal", what="chops")
    assert r.status_code == 200 and "No separate syllables" in _toast(r)
    assert client.app.state.svc.state.patch.sampler_slices == ""


def test_the_vocal_actions_read_the_vocals_stem_whatever_is_ticked(sung, monkeypatch):
    separate(sung, monkeypatch, bank=OneStem("vocals"))
    _post(sung, "/lab/region", **{"stems": "1", "stem.drums": "on"})
    assert sources.list_sources(sung.app.state.svc.data_dir)[0]["picked"] == ["drums"]
    r = _post(sung, "/lab/vocal", what="instrument")
    assert r.status_code == 200 and "not a vocal stem" not in _toast(r)
    assert sung.app.state.svc.state.patch.sampler_mode == "formant"
    page = sung.get("/view/lab").text
    assert "from vocals stem" in page and "from drums stem" in page


def test_a_voice_is_one_undo_step_back_to_the_sound_before(sung):
    before = sung.app.state.svc.state.patch
    _post(sung, "/lab/vocal", what="instrument")
    state = sung.app.state.svc.state
    assert state.patch != before and state.pop_history()
    assert state.patch == before


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/lab/vocal", {"what": "aria"}),
        ("/lab/vocal", {}),
        ("/lab/drums", {"to": "tape"}),
        ("/lab/drums", {}),
    ],
)
def test_an_unknown_action_is_refused(sung, path, data):
    assert _post(sung, path, **data).status_code == 422


def test_the_new_actions_answer_a_missing_song_and_a_long_region_with_a_toast(client):
    for path, data in (
        ("/lab/vocal", {"what": "instrument"}),
        ("/lab/drums", {"to": "grid"}),
        ("/lab/place", {"section_id": "x"}),
    ):
        r = client.post(path, data={"source": "0" * 16, **data}, headers=LAB)
        assert r.status_code == 422 and "gone" in r.text
    t = np.arange(SR * 40) / SR
    assert (
        upload(client, wav_bytes(np.sin(2 * np.pi * 220 * t) * 0.3)).status_code == 200
    )
    _post(client, "/lab/region", mode="seconds", start="0", length="40")
    for path, data in (
        ("/lab/vocal", {"what": "chops"}),
        ("/lab/drums", {"to": "kit"}),
    ):
        r = _post(client, path, **data)
        assert r.status_code == 422 and "Pick a shorter one" in r.text


# --- the drums -----------------------------------------------------------------


def test_a_drum_region_is_written_to_the_drum_grid_through_the_patterns_own_path(beat):
    state = beat.app.state.svc.state
    before = state.pattern_snapshot()
    r = _post(beat, "/lab/drums", to="grid")
    assert r.status_code == 200, r.text[:300]
    arr = state.arrangement
    assert (arr.drum_bars, arr.drum_steps, arr.drum_swing) == (2, 16, 0.0)
    for lane in ("kick", "snare", "hat"):
        assert [s for v, s in arr.drums if v == lane] == _steps(BEAT_ROWS, lane), lane
    assert state.active_slot is None
    # the page keeps what was written: counts, and the rows to read
    assert (
        "On the drum grid" in r.text
        and "2 bars: 6 kick, 4 snare, 8 closed hat" in r.text
    )
    assert "kick  " + BEAT_ROWS["kick"] in r.text and 'id="lab-said"' in r.text
    # one pattern undo step, like any drum edit
    assert state.can_undo_pattern() and state.pop_pattern_undo() is None
    assert state.pattern_snapshot()["arr"] == before["arr"]


def test_the_drum_grid_brings_the_songs_tempo_to_the_loop_when_asked(beat):
    """A beat is steps on a grid: at another tempo it is another beat. A
    79.7 bpm song's, left at the session's 200, ran 2.5x fast."""
    state = beat.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=200.0)
    assert "the loop takes this song’s 120 bpm" in beat.get("/view/lab").text
    r = _post(beat, "/lab/drums", to="grid")  # the box unticked
    assert state.arrangement.bpm == 200.0
    assert "plays at the loop's 200 bpm, not the song's 120" in _said(r)
    r = _post(beat, "/lab/drums", to="grid", loop_tempo="on")
    assert r.status_code == 200 and state.arrangement.bpm == BPM
    assert "The loop's tempo is now 120 bpm" in _said(r) and "it was 200" in _said(r)
    # and the loop renders there: 2 bars at 120 bpm
    assert (
        beat.post(
            "/fragment/preview",
            headers={**LAB, "HX-Current-URL": "http://x/view/drums"},
        ).status_code
        == 200
    )


def test_the_grid_takes_the_regions_first_bars_and_needs_it_on_the_bar_lines(beat):
    four = np.concatenate([drum_loop(BEAT_ROWS, bpm=BPM)[0]] * 4)  # 8 bars
    assert upload(beat, wav_bytes(four), "long.wav").status_code == 200
    _on_grid(beat, 8)
    assert _post(beat, "/lab/drums", to="grid").status_code == 200
    arr = beat.app.state.svc.state.arrangement
    assert arr.drum_bars == max(limits.BAR_CHOICES) == 4
    assert max(s for _v, s in arr.drums) < 4 * 16
    _post(beat, "/lab/region", mode="seconds", start="0.1", length="3")
    r = _post(beat, "/lab/drums", to="grid")
    assert r.status_code == 422 and "in bars first" in r.text
    # the card offers the way out: the region onto the bar lines
    page = beat.get("/view/lab").text
    assert "snap the selection to bars</button>" in page
    assert "→ drum grid</button>" not in page


def test_a_region_with_no_drum_hits_writes_nothing(beat, monkeypatch):
    from synthlab.analysis import drumhits
    from synthlab.studio import labmake

    nothing = (drumhits.Pattern(bars=2), [])
    monkeypatch.setattr(labmake, "drum_pattern", lambda *a: nothing)
    state = beat.app.state.svc.state
    before = (state.pattern_snapshot(), dict(state.song["patterns"]))
    for to in ("grid", "pool"):
        r = _post(beat, "/lab/drums", to=to)
        assert r.status_code == 422 and "No drum hits" in r.text
    assert (state.pattern_snapshot(), dict(state.song["patterns"])) == before
    assert not state.can_undo_pattern()


def test_swing_and_ghost_notes_reach_the_grid(client):
    rows = {
        "kick": "x.........x.....",
        "snare": "....x..x....x...",
        "hat": ".x...x...x...x.x",
    }
    audio, _ = drum_loop(rows, bpm=BPM, swing=0.16, vel={"7": 0.35})
    assert upload(client, wav_bytes(audio)).status_code == 200
    _on_grid(client, 1)
    r = _post(client, "/lab/drums", to="grid")
    arr = client.app.state.svc.state.arrangement
    assert abs(arr.drum_swing - 0.16) < 0.03 and "swing 0.1" in r.text
    assert [s for v, s in arr.drums if v == "hat"] == _steps(rows, "hat")
    assert 0.2 <= arr.drum_vel["7"] <= 0.5


def test_a_drum_region_becomes_a_beat_in_the_songs_pool(beat):
    song = beat.app.state.svc.state.song
    had = set(song["patterns"])
    r = _post(beat, "/lab/drums", to="pool")
    assert r.status_code == 200 and "pattern pool" in r.text
    song = beat.app.state.svc.state.song
    (pid,) = set(song["patterns"]) - had
    p = song["patterns"][pid]
    assert p["kind"] == "drums" and p["bars"] == 2 and p["steps"] == 16
    assert [s for v, s in p["drums"] if v == "kick"] == _steps(BEAT_ROWS, "kick")
    assert p["name"].startswith("my song b1")
    # the loop's own grid is not touched by a pool pattern
    assert not beat.app.state.svc.state.can_undo_pattern()


def test_a_full_pool_refuses_the_beat_with_the_songs_own_message(beat, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PATTERNS", 0)
    r = _post(beat, "/lab/drums", to="pool")
    assert r.status_code == 422 and "at most" in r.text


def test_a_kit_is_one_clean_hit_a_drum_on_the_rows_own_sample_slots(beat):
    data_dir = beat.app.state.svc.state.data_dir
    r = _post(beat, "/lab/drums", to="kit")
    assert r.status_code == 200 and "Kit from the" in r.text
    samples = beat.app.state.svc.state.arrangement.drum_samples
    assert set(samples) == {"kick", "snare", "hat"}
    assert "No ohat stood alone" in r.text
    names = {c["name"] for c in list_clips(data_dir)}
    for lane, clip in samples.items():
        assert clip in names and lane in clip
        audio, sr = load_clip(data_dir, clip)
        assert sr == SR and 0.05 < len(audio) / SR <= 0.61
        assert np.max(np.abs(audio)) > 0.05 and abs(audio[-1]).max() < 1e-3

    # the kick's one-shot is low, the hat's is high
    def centroid(name: str) -> float:
        x = load_clip(data_dir, samples[name])[0].mean(axis=1)
        mag = np.abs(np.fft.rfft(x))
        return float((np.fft.rfftfreq(len(x), 1 / SR) * mag).sum() / mag.sum())

    assert centroid("kick") < 1500 < 4000 < centroid("hat")
    # a second kit never writes over the first one's clips
    _post(beat, "/lab/drums", to="kit")
    again = beat.app.state.svc.state.arrangement.drum_samples
    assert set(again.values()).isdisjoint(samples.values())


def test_the_kit_plays_on_the_drum_rows(beat):
    """The row's sampled hit is what the pad plays: the existing slot."""
    _post(beat, "/lab/drums", to="kit")
    state = beat.app.state.svc.state
    hit = beat.get("/drum/kick/hit.wav")
    assert hit.status_code == 200
    state.arrangement = replace(state.arrangement, drum_samples={})
    assert beat.get("/drum/kick/hit.wav").content != hit.content


def test_a_region_with_no_lone_hit_makes_no_kit(beat, monkeypatch):
    from synthlab.analysis import drumhits

    monkeypatch.setattr(drumhits, "exemplars", lambda hits: {})
    r = _post(beat, "/lab/drums", to="kit")
    assert r.status_code == 422 and "stands alone" in r.text
    assert beat.app.state.svc.state.arrangement.drum_samples == {}
    assert list_clips(beat.app.state.svc.state.data_dir) == []


# --- onto the song -------------------------------------------------------------


def _song(client) -> dict:
    return client.app.state.svc.state.song


def _section(client, bars: int = 4) -> str:
    r = client.post(
        "/song/section/add",
        data={"name": "Verse", "bars": str(bars)},
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/view/song"},
    )
    assert r.status_code == 200
    return _song(client)["sections"][-1]["id"]


def test_a_region_lands_on_the_song_stretched_to_its_tempo_in_whole_bars(beat):
    state = beat.app.state.svc.state
    state.song["bpm"] = 100.0  # the song's tempo, not the loop's or the recording's
    sid = _section(beat)
    r = _post(beat, "/lab/place", section_id=sid, at_bar="2", name="groove")
    assert r.status_code == 200, r.text[:300]
    (take,) = next(s for s in _song(beat)["sections"] if s["id"] == sid)["audio"]
    assert take["clip"] == "groove" and take["at_bar"] == 1 and take["gain"] == 1.0
    track = next(t for t in _song(beat)["tracks"] if t["id"] == take["track_id"])
    assert track["kind"] == "audio"
    audio, sr = load_clip(state.data_dir, "groove")
    assert sr == SR and len(audio) == round(2 * 240.0 / 100.0 * SR)  # 2 bars at 100
    meta = next(c for c in list_clips(state.data_dir) if c["name"] == "groove")
    assert meta["bpm"] == 100.0 and meta["bars"] == 2 and meta["lab"]["fitted"]
    assert "Placed groove in Verse, bar 2" in _said(r)
    assert "2 bars at the song's 100 bpm, stretched from 120" in _said(r)
    # the kick still lands on the stretched grid: bar 1, beat 1 and bar 2
    mono = np.abs(audio.mean(axis=1))
    bar = int(240.0 / 100.0 * SR)
    assert mono[:2000].max() > 0.2 and mono[bar : bar + 2000].max() > 0.2


def test_a_region_already_at_the_songs_tempo_is_placed_as_it_is(beat):
    beat.app.state.svc.state.song["bpm"] = BPM
    sid = _section(beat)
    r = _post(beat, "/lab/place", section_id=sid)
    assert r.status_code == 200 and "stretched" not in _said(r)
    assert "2 bars at the song's 120 bpm." in _said(r)
    clip = next(s for s in _song(beat)["sections"] if s["id"] == sid)["audio"][0][
        "clip"
    ]
    audio, _sr = load_clip(beat.app.state.svc.state.data_dir, clip)
    assert len(audio) == round(2 * BAR * SR)


def test_a_free_region_is_held_to_whole_bars_of_the_song(beat):
    beat.app.state.svc.state.song["bpm"] = BPM
    sid = _section(beat)
    _post(beat, "/lab/region", mode="seconds", start="0", length="2.6")  # 1.3 bars
    assert _post(beat, "/lab/place", section_id=sid, name="bit").status_code == 200
    audio, _sr = load_clip(beat.app.state.svc.state.data_dir, "bit")
    assert len(audio) == round(1 * BAR * SR)


def test_a_song_that_refuses_the_take_leaves_no_clip_behind(beat):
    state = beat.app.state.svc.state
    before = {c["name"] for c in list_clips(state.data_dir)}
    r = _post(beat, "/lab/place", section_id="nope")
    assert r.status_code == 422 and "Pick a section" in r.text
    sid = _section(beat)
    for _ in range(limits.MAX_LANE_CLIPS):
        assert _post(beat, "/lab/place", section_id=sid, name="fill").status_code == 200
    full = {c["name"] for c in list_clips(state.data_dir)}
    assert len(full - before) == limits.MAX_LANE_CLIPS
    r = _post(beat, "/lab/place", section_id=sid, name="one too many")
    assert r.status_code == 422 and "at most" in r.text
    assert {c["name"] for c in list_clips(state.data_dir)} == full


@pytest.mark.parametrize("at_bar", ["0", "-3", "999", "nan", "1e400", "two", ""])
def test_any_bar_number_lands_inside_the_section(beat, at_bar):
    beat.app.state.svc.state.song["bpm"] = BPM
    sid = _section(beat, bars=4)
    r = _post(beat, "/lab/place", section_id=sid, at_bar=at_bar)
    assert r.status_code == 200
    take = next(s for s in _song(beat)["sections"] if s["id"] == sid)["audio"][0]
    assert 0 <= take["at_bar"] <= 3


def test_the_panel_lists_the_songs_sections_or_says_there_are_none(beat):
    page = beat.get("/view/lab").text
    if not _song(beat)["sections"]:
        assert "The song has no sections yet" in page
    sid = _section(beat)
    page = beat.get("/view/lab").text
    assert f'<option value="{sid}">Verse · 4 bars</option>' in page
    assert "→ Song</button>" in page and "→ drum grid</button>" in page
    assert "Make voice ⤷ Synths</button>" in page and "→ drum rows</button>" in page


# --- where it went, and its undo (`go`, POST /lab/undo) ---------------------------


def _undo_fields(r) -> dict:
    """The fields the said line's Undo posts (its hx-vals), as a dict."""
    m = re.search(r'id="lab-undo" hx-post="/lab/undo"\s+hx-vals=\'([^\']+)\'', r.text)
    assert m, "no Undo on the said line"
    return json.loads(html.unescape(m.group(1)))


def test_a_beat_on_the_grid_says_where_it_went_and_undoes_with_its_tempo(beat):
    state = beat.app.state.svc.state
    state.arrangement = replace(state.arrangement, bpm=200.0)
    before = state.pattern_snapshot()["arr"]
    r = _post(beat, "/lab/drums", to="grid", loop_tempo="on")
    assert state.arrangement.bpm == BPM and 'href="/view/drums"' in r.text
    assert "Open on Kit" in _said(r)
    fields = _undo_fields(r)
    assert fields["what"] == "pattern" and fields["bpm"] == "200"
    r = beat.post("/lab/undo", data=fields, headers=LAB)
    assert r.status_code == 200 and "Undone" in _said(r)
    assert state.pattern_snapshot()["arr"] == before and state.arrangement.bpm == 200.0


def test_a_kept_region_opens_in_clips_and_its_undo_deletes_it(beat):
    data_dir = beat.app.state.svc.data_dir
    r = _post(beat, "/lab/send", target="clip", name="loop")
    assert 'popovertarget="clip-drawer"' in r.text and "Open in Clips" in _said(r)
    fields = _undo_fields(r)
    assert fields == {"what": "clip", "clip": "loop", "source": source_id(beat)}
    r = beat.post("/lab/undo", data=fields, headers=LAB)
    assert r.status_code == 200 and not list_clips(data_dir)
    # a clip the Lab did not make from this song is never deleted from here
    from synthlab.studio.clips import save_clip

    save_clip(data_dir, "mine", np.zeros((SR // 10, 2), np.float32), meta={})
    r = beat.post("/lab/undo", data={**fields, "clip": "mine"}, headers=LAB)
    assert r.status_code == 422
    assert [c["name"] for c in list_clips(data_dir)] == ["mine"]


def test_a_take_on_the_song_undoes_with_its_clip_unless_the_song_moved_on(beat):
    sid = _section(beat)
    data_dir = beat.app.state.svc.data_dir
    r = _post(beat, "/lab/place", section_id=sid, at_bar="1")
    assert 'href="/view/song"' in r.text
    fields = _undo_fields(r)
    assert fields["what"] == "song" and fields["clip"]
    # another song edit since: the Lab's undo leaves it to the Song page
    _section(beat)
    r = beat.post("/lab/undo", data=fields, headers=LAB)
    assert r.status_code == 422 and "Song page" in r.text
    beat.app.state.svc.state.song_undo()  # the section, undone on Song
    r = beat.post("/lab/undo", data=fields, headers=LAB)
    assert r.status_code == 200 and "song-changed" in r.headers["HX-Trigger"]
    assert "Undone: the take is off the song" in _said(r)
    assert fields["clip"] not in json.dumps(_song(beat))
    # its clip stays while the song's redo step holds it: a redo must never
    # bring back a reference to a deleted file
    assert "stays in Clips" in _said(r)
    assert fields["clip"] in {c["name"] for c in list_clips(data_dir)}


def test_a_kit_undoes_to_the_rows_it_replaced_and_only_those_still_its(beat):
    state = beat.app.state.svc.state
    state.arrangement = replace(state.arrangement, drum_samples={"kick": "old kick"})
    r = _post(beat, "/lab/drums", to="kit")
    fields = _undo_fields(r)
    assert set(state.arrangement.drum_samples) == {"kick", "snare", "hat"}
    # the snare's row set by hand since: it stays
    hand = {**state.arrangement.drum_samples, "snare": "by hand"}
    state.arrangement = replace(state.arrangement, drum_samples=hand)
    r = beat.post("/lab/undo", data=fields, headers=LAB)
    assert r.status_code == 200
    assert state.arrangement.drum_samples == {"kick": "old kick", "snare": "by hand"}
