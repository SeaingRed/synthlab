"""The Sample Lab (`routes/lab.py`, `sources.py`, `lab.py`): importing a
song, its grid and region, the audition, and sending a region to the clip
store and the sampler. Separation is `test_lab_stems.py`."""

from __future__ import annotations

import io
import json
import re
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.audio import read_pcm16, wav_bytes
from synthlab.core import SR
from synthlab.studio import files, lab, limits, sources
from synthlab.studio.clips import list_clips, load_clip
from synthlab.studio.server import create_app
from tests.studio.lab_helpers import BARS, BPM, LAB, song_wav, source_id, upload

BAR = 4 * 60.0 / BPM  # seconds


@pytest.fixture(scope="module")
def wav() -> bytes:
    return song_wav()


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


@pytest.fixture
def loaded(client, wav) -> TestClient:
    assert upload(client, wav).status_code == 200
    return client


def _meta(client) -> dict:
    return sources.list_sources(client.app.state.svc.data_dir)[0]


def _post(client, path: str, **data):
    return client.post(path, data={"source": source_id(client), **data}, headers=LAB)


# --- the page ----------------------------------------------------------------


def test_the_lab_is_a_view_in_the_nav_with_its_rights_line(client):
    page = client.get("/view/lab").text
    assert 'href="/view/lab"' in page and 'data-view="lab"' in page
    assert "for you to check" in page  # the one line about rights
    assert "lab.js" in page and "<script>" not in page  # one island, nothing inline
    frag = client.get("/view/lab", headers=LAB)
    # the tab's name first (htmx applies a swap's <title>), then the view
    head = "<title>Lab · synthlab studio</title>\n<main"
    assert frag.text.lstrip().startswith(head)
    assert "No songs yet" in frag.text
    assert "HX-Trigger" not in frag.headers  # no loop preview is rendered for it


# --- importing ---------------------------------------------------------------


def test_an_import_is_stored_by_content_with_its_tempo_and_key(loaded, tmp_path):
    meta = _meta(loaded)
    assert sources.valid_id(meta["id"]) and meta["name"] == "my song"
    assert abs(meta["seconds"] - BARS * BAR) < 0.01
    an = meta["analysis"]
    assert abs(an["bpm"] - BPM) / BPM < 0.01 and an["key"] == "A minor"
    d = tmp_path / "sources" / meta["id"]
    assert sorted(p.name for p in d.iterdir()) == [
        "meta.json",
        "mix.flac",
        "mix.peaks.npy",
        "mix.rms.npy",  # the waveform's outline and its body
    ]
    page = loaded.get("/view/lab").text
    assert "% sure" in page and "detected: A minor" in page and "lab-svg" in page
    assert "% sure of the scale" in page  # the key's own confidence, in words


def test_the_key_can_be_named_by_hand_and_given_back_to_the_detector(loaded):
    """The override labels and hints; it is a name from a fixed list."""
    r = _post(loaded, "/lab/key", key="D minor")
    assert r.status_code == 200 and "set by you" in r.text
    meta = _meta(loaded)
    assert meta["user"]["key"] == "D minor" and meta["analysis"]["key"] == "A minor"
    assert lab.key_in_force(meta) == "D minor"
    assert '<option value="D minor" selected>' in r.text
    # a tempo reset is not a key reset
    _post(loaded, "/lab/grid", action="reset")
    assert _meta(loaded)["user"]["key"] == "D minor"
    assert "set by you" not in _post(loaded, "/lab/key", key="").text
    assert lab.key_in_force(_meta(loaded)) == "A minor"


@pytest.mark.parametrize("key", ["H minor", "D", "<b>", "d minor", "A minor" * 400])
def test_a_key_that_is_not_one_of_the_24_is_refused(loaded, key):
    assert _post(loaded, "/lab/key", key=key).status_code == 422
    assert _meta(loaded)["user"]["key"] is None


def test_the_same_audio_twice_is_one_source(loaded, wav):
    assert upload(loaded, wav, "another name.wav").status_code == 200
    assert len(sources.list_sources(loaded.app.state.svc.data_dir)) == 1


def test_an_import_touches_no_session_state_and_no_render_key(client, wav):
    svc = client.app.state.svc
    before = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    upload(client, wav)
    _post(client, "/lab/grid", action="double")
    _post(client, "/lab/region", mode="bars", bar="2", bars="2")
    # chops are the source's too, and a kept chop is a clip nothing places
    # yet: cut, moved, cleaned, selected, kept and updated, none of it is
    # session state or part of a render key (more in test_lab_chops.py)
    assert _post(client, "/lab/chop", op="auto", rule="beats").status_code == 200
    chop = _meta(client)["chops"][0]["id"]
    for data in (
        {"op": "move", "edge": "end", "at": "3.3", "free": "1"},
        {"op": "clean", "trim_db": "-40", "level": "normalise"},
        {"op": "select"},
        {"op": "rename", "name": "hook"},
    ):
        assert _post(client, "/lab/chop", chop=chop, **data).status_code == 200
    assert _post(client, "/lab/chop/keep", chop=chop).status_code == 200
    assert _post(client, "/lab/chop/keep", chop=chop, update="1").status_code == 200
    assert [c["name"] for c in list_clips(svc.data_dir)] == ["hook"]
    after = (svc.render_inputs("perform").key(), json.dumps(svc.state._payload()))
    assert before == after


@pytest.mark.parametrize("fmt", ["FLAC", "OGG", "MP3"])
def test_compressed_formats_decode(client, fmt):
    import soundfile as sf

    t = np.arange(SR * 2) / SR
    x = np.column_stack([np.sin(2 * np.pi * 330 * t)] * 2) * 0.5
    buf = io.BytesIO()
    sf.write(buf, x.astype(np.float32), SR, format=fmt)
    assert upload(client, buf.getvalue(), f"tone.{fmt.lower()}").status_code == 200
    assert abs(_meta(client)["seconds"] - 2.0) < 0.1


def test_another_sample_rate_and_mono_are_brought_to_stereo_at_sr(client):
    t = np.arange(48000 * 2) / 48000
    mono = (np.sin(2 * np.pi * 440 * t) * 0.5).astype(np.float64)
    upload(client, wav_bytes(mono, sr=48000), "mono48.wav")
    meta = _meta(client)
    audio = sources.read(client.app.state.svc.data_dir, meta)
    assert audio.shape == (2 * SR, 2) and np.allclose(audio[:, 0], audio[:, 1])
    spectrum = np.abs(np.fft.rfft(audio[:, 0]))
    assert abs(np.argmax(spectrum) * SR / len(audio) - 440) < 1.0  # still 440 Hz


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not audio at all",
        b"RIFF\x00\x00\x00\x00WAVEfmt ",
        b"\xff\xfb\x90\x00" + b"\x00" * 400,  # an MP3 frame header, then nothing
        b"fLaC" + b"\x00" * 64,
        wav_bytes(np.zeros((SR, 2))),  # silent
    ],
    ids=["empty", "text", "cut-wav", "cut-mp3", "cut-flac", "silent"],
)
def test_an_unreadable_upload_is_a_toast_never_a_500(client, data):
    r = upload(client, data, "bad.wav")
    assert r.status_code == 422 and 'class="toast error"' in r.text
    assert sources.list_sources(client.app.state.svc.data_dir) == []


def test_a_header_that_lies_about_its_length_is_read_to_the_cap(client, monkeypatch):
    """The cap is on frames actually decoded, never on what the header says."""
    monkeypatch.setattr(limits, "MAX_SOURCE_SECONDS", 2.0)
    honest = wav_bytes(np.full((SR * 3, 2), 0.25))
    lying = bytearray(honest)
    lying[40:44] = (16).to_bytes(4, "little")  # "data" chunk: four frames
    assert upload(client, bytes(lying)).status_code in (200, 422)  # no 500
    r = upload(client, honest)
    assert r.status_code == 422 and "longer than" in r.text


def test_the_body_cap_refuses_an_oversized_upload_before_decoding(client, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SOURCE_UPLOAD_BYTES", 50_000)
    r = upload(client, wav_bytes(np.full((SR, 2), 0.25)))
    assert r.status_code == 413


def test_the_store_refuses_one_song_too_many_with_a_reason(client, monkeypatch):
    monkeypatch.setattr(limits, "MAX_SOURCES", 1)
    t = np.arange(SR) / SR
    for i, hz in enumerate((220, 330)):
        tone = np.column_stack([np.sin(2 * np.pi * hz * t)] * 2) * 0.4
        r = upload(client, wav_bytes(tone), f"{i}.wav")
    assert r.status_code == 422 and "delete one first" in r.text
    monkeypatch.setattr(limits, "MAX_SOURCES", 4)
    monkeypatch.setattr(limits, "MAX_SOURCES_BYTES", 1000)
    r = upload(client, wav_bytes(tone), "again.wav")
    assert r.status_code == 422 and "disk budget" in r.text


def test_a_hostile_file_name_becomes_a_safe_one(client, wav):
    upload(client, wav, '..\\..\\<script>"x".wav')
    name = _meta(client)["name"]
    assert name and not set(name) & set('<>"/\\') and ".." not in name
    assert "<script>" not in client.get("/view/lab").text.split("</head>")[1]


def test_a_meta_edited_on_disk_cannot_break_the_page(loaded, tmp_path):
    path = tmp_path / "sources" / source_id(loaded) / "meta.json"
    path.write_text(
        json.dumps(
            {
                "name": {"x": 1},
                "seconds": "long",
                "analysis": {"bpm": 1e300, "key": 7, "key_scores": "x"},
                "user": {"bpm": True, "offset": float("1e400")},
                "region": {"mode": "x", "start": -5, "length": None},
                "picked": ["vocals", "../x"],
                "stems": {"model": "../../etc"},
            }
        ),
        encoding="utf-8",
    )
    assert loaded.get("/view/lab").status_code == 200
    meta = _meta(loaded)
    assert meta["picked"] == [] and meta["stems"]["model"] == ""  # no stems on disk
    path.write_text("{ not json", encoding="utf-8")
    assert loaded.get("/view/lab").status_code == 200  # the source is skipped


def test_deleting_removes_the_folder_and_ids_never_reach_a_path(loaded, tmp_path):
    for bad in ("", "..", "../../x", "A" * 16, "0" * 15, "0" * 16 + "/.."):
        r = loaded.post("/lab/delete", data={"source": bad}, headers=LAB)
        assert r.status_code == 422, bad
        assert loaded.get(f"/lab/{bad}/region.wav").status_code in (404, 405)
    sid = source_id(loaded)
    assert _post(loaded, "/lab/delete").status_code == 200
    assert not (tmp_path / "sources" / sid).exists()


# --- grid and region ---------------------------------------------------------


def test_the_tempo_can_be_typed_halved_doubled_nudged_and_reset(loaded):
    _post(loaded, "/lab/grid", action="set", bpm="100")
    assert lab.grid(_meta(loaded))["bpm"] == 100
    _post(loaded, "/lab/grid", action="half")
    assert lab.grid(_meta(loaded))["bpm"] == 50
    _post(loaded, "/lab/grid", action="double")
    _post(loaded, "/lab/grid", action="double")
    assert lab.grid(_meta(loaded))["bpm"] == 200
    assert _post(loaded, "/lab/grid", action="double").status_code == 200
    assert lab.grid(_meta(loaded))["bpm"] == 200  # 400 is out of range: unchanged
    _post(loaded, "/lab/grid", action="reset")
    g = lab.grid(_meta(loaded))
    assert abs(g["bpm"] - BPM) < 1 and not g["user_bpm"]
    before = g["offset"]
    _post(loaded, "/lab/grid", action="nudge", beats="0.25")
    moved = lab.grid(_meta(loaded))
    assert abs((moved["offset"] - before) % moved["bar"] - moved["beat"] / 4) < 1e-6


@pytest.mark.parametrize(
    "data",
    [
        {"action": "set", "bpm": "nan"},
        {"action": "set", "bpm": "-3"},
        {"action": "set", "bpm": "fast"},
        {"action": "nudge", "beats": "9999"},
        {"action": "nudge", "beats": "inf"},
        {"action": "explode"},
    ],
)
def test_a_bad_grid_post_changes_nothing(loaded, data):
    before = _meta(loaded)["user"]
    assert _post(loaded, "/lab/grid", **data).status_code == 422
    assert _meta(loaded)["user"] == before


def test_a_typed_tempo_is_clamped_to_the_range(loaded):
    _post(loaded, "/lab/grid", action="set", bpm="100000")
    assert lab.grid(_meta(loaded))["bpm"] == limits.LAB_BPM_RANGE[1]


def test_a_bar_region_sits_on_the_grid_and_follows_a_tempo_fix(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="2", bars="2")
    meta = _meta(loaded)
    g = lab.grid(meta)
    start, length = lab.span(meta)
    assert abs(g["offset"]) < 0.03  # the loop starts on its downbeat
    assert abs(start - (g["offset"] + g["bar"])) < 1e-6
    assert abs(length - 2 * g["bar"]) < 1e-6 and abs(g["bar"] - BAR) < 0.02
    _post(loaded, "/lab/grid", action="half")
    assert abs(lab.span(_meta(loaded))[1] - 4 * BAR) < 0.05  # two bars, twice as long


def test_a_drag_snaps_to_the_bars_it_touched_or_stays_free(loaded):
    _post(loaded, "/lab/region", drag_a=f"{BAR * 1.3}", drag_b=f"{BAR * 2.2}")
    r = _meta(loaded)["region"]
    assert (r["mode"], r["start"], r["length"]) == ("bars", 1.0, 2.0)
    _post(loaded, "/lab/region", mode="seconds")
    # Alt held (`free`): where it was let go, dragged leftwards
    _post(loaded, "/lab/region", drag_b="1.25", drag_a="3.5", free="1")
    r = _meta(loaded)["region"]
    assert r["mode"] == "seconds" and abs(r["start"] - 1.25) < 1e-6
    assert abs(r["length"] - 2.25) < 1e-6
    # else each end lands on the nearest beat line of the grid in force
    g = lab.grid(_meta(loaded))
    beat, off = g["beat"], g["offset"]
    _post(
        loaded,
        "/lab/region",
        drag_a=f"{off + beat * 2.2}",
        drag_b=f"{off + beat * 6.7}",
    )
    start, length = lab.span(_meta(loaded))
    assert abs(start - (off + 2 * beat)) < 1e-6 and abs(length - 5 * beat) < 1e-6
    # a drag inside one beat is that beat, never nothing
    _post(
        loaded,
        "/lab/region",
        drag_a=f"{off + beat * 3.1}",
        drag_b=f"{off + beat * 3.3}",
    )
    start, length = lab.span(_meta(loaded))
    assert abs(start - (off + 3 * beat)) < 1e-6 and abs(length - beat) < 1e-6


def test_the_lanes_carry_the_grid_and_the_region_is_written_over_them(loaded):
    """Where the sample comes from, in bars: the page hands labview.js the
    grid in force (it numbers the bars and draws the lines for whatever
    span is shown: tests/browser/test_lab.py), and the region is a band on
    the ruler and words in the selection bar."""
    r = _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")
    meta = _meta(loaded)
    g = lab.grid(meta)
    assert f'data-beat="{round(g["beat"], 6)}"' in r.text
    assert f'data-offset="{round(g["offset"], 6)}"' in r.text
    assert f'data-bars="{lab.bars_in(meta)}"' in r.text and 'data-per-bar="4"' in r.text
    assert lab.region_label(meta) == "bars 3–4 · 2 bars"
    start, length = lab.span(meta)
    band = re.search(
        r'class="lab-ruler-region lab-at" data-at="([\d.]+)" data-to="([\d.]+)"', r.text
    )
    assert float(band[1]) == pytest.approx(start, abs=1e-3)
    assert float(band[2]) == pytest.approx(start + length, abs=1e-3)
    assert "bars 3–4 · 2 bars" in r.text and 'class="lab-ruler"' in r.text
    # the one-press lengths post the region from its own first bar
    assert 'class="seg lab-lengths"' in r.text
    assert '"mode": "bars", "bar": "3", "bars": "4"' in r.text
    # seconds: its clock times and its length in beats
    _post(loaded, "/lab/region", mode="seconds", start="1", length="1.5")
    assert lab.region_label(_meta(loaded)).endswith("· 3 beats")


def test_what_is_made_is_named_for_what_it_plays_and_where(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")
    assert lab.pick_name(_meta(loaded)) == "mix bars 3-4"
    _post(loaded, "/lab/region", mode="bars", bar="5", bars="1")
    assert lab.pick_name(_meta(loaded)) == "mix bar 5"
    g = lab.grid(_meta(loaded))
    a = g["offset"] + g["bar"] * 2 + g["beat"] * 2  # bar 3, beat 3
    _post(
        loaded, "/lab/region", mode="seconds", start=f"{a}", length=f"{g['beat'] * 6}"
    )
    assert lab.pick_name(_meta(loaded)) == "mix bar 3.3 (6 beats)"
    # a clip name's characters, and the default name of what is kept
    assert files.safe_name(lab.pick_name(_meta(loaded))) == lab.pick_name(_meta(loaded))
    r = _post(loaded, "/lab/send", target="clip")
    assert r.headers["X-Clip-Name"] == "mix bar 3.3 (6 beats)"


def test_the_keys_move_and_size_the_region_by_beats_and_bars(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")
    g = lab.grid(_meta(loaded))
    _post(loaded, "/lab/region", step="move", by="4")  # Shift+→: a bar, still bars
    r = _meta(loaded)["region"]
    assert (r["mode"], r["start"], r["length"]) == ("bars", 3.0, 2.0)
    _post(loaded, "/lab/region", step="grow", by="1")  # ]: a bar longer
    assert _meta(loaded)["region"]["length"] == 3.0
    _post(loaded, "/lab/region", step="grow", by="-9")  # never under one bar
    assert _meta(loaded)["region"]["length"] == 1.0
    before = lab.span(_meta(loaded))
    _post(loaded, "/lab/region", step="move", by="-1")  # ←: a beat, off the bar lines
    start, length = lab.span(_meta(loaded))
    assert _meta(loaded)["region"]["mode"] == "seconds"
    assert start == pytest.approx(before[0] - g["beat"]) and length == pytest.approx(
        before[1]
    )
    _post(loaded, "/lab/region", step="grow", by="2")  # in beats now
    assert lab.span(_meta(loaded))[1] == pytest.approx(before[1] + 2 * g["beat"])
    for _ in range(30):  # never out of the song
        _post(loaded, "/lab/region", step="move", by="-64")
    assert lab.span(_meta(loaded))[0] == 0.0
    for bad in ("x", "nan", "", "1e99"):
        assert _post(loaded, "/lab/region", step="move", by=bad).status_code == 200
    assert _post(loaded, "/lab/region", step="sideways", by="1").status_code == 200


def test_an_edge_of_the_region_drags_to_a_bar_line_or_free(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="3", bars="2")  # bars 3-4
    g = lab.grid(_meta(loaded))
    bar = lambda n: g["offset"] + (n - 1) * g["bar"]  # noqa: E731 - bar n's line
    _post(loaded, "/lab/region", edge="end", at=f"{bar(7) - 0.2 * g['bar']}")
    r = _meta(loaded)["region"]
    assert (r["start"], r["length"]) == (2.0, 4.0)  # to the nearest line: bars 3-6
    _post(loaded, "/lab/region", edge="start", at=f"{bar(9)}")  # never past the end
    r = _meta(loaded)["region"]
    assert (r["start"], r["length"]) == (5.0, 1.0)
    # Alt held: where it was let go, and the region is in seconds now
    _post(loaded, "/lab/region", edge="start", at=f"{bar(5) + 0.123}", free="1")
    start, _length = lab.span(_meta(loaded))
    assert _meta(loaded)["region"]["mode"] == "seconds"
    assert start == pytest.approx(bar(5) + 0.123, abs=1e-4)
    # in beats, an edge lands on a beat line
    _post(loaded, "/lab/region", edge="end", at=f"{bar(6) + 1.4 * g['beat']}")
    start, length = lab.span(_meta(loaded))
    assert start + length == pytest.approx(bar(6) + g["beat"], abs=1e-6)


def test_zoom_halves_what_is_shown_around_the_region_and_stays_in_the_song(loaded):
    total = _meta(loaded)["seconds"]  # 8 bars of 2 s
    _post(loaded, "/lab/region", mode="bars", bar="6", bars="1")  # 10..12 s
    r = _post(loaded, "/lab/view", action="in")
    start, length = lab.view(_meta(loaded))
    assert length == pytest.approx(total / 2) and start <= 10 < 12 <= start + length
    assert (
        f'data-view-seconds="{round(length, 4)}' in r.text and ">×2</output>" in r.text
    )
    for _ in range(12):  # as far in as it goes: never under the floor
        _post(loaded, "/lab/view", action="in")
    start, length = lab.view(_meta(loaded))
    assert length == pytest.approx(max(lab.MIN_VIEW_SECONDS, total / lab.MAX_ZOOM))
    assert start <= 11 <= start + length  # still on the region
    for _ in range(40):
        _post(loaded, "/lab/view", action="right")
    start, length = lab.view(_meta(loaded))
    assert start + length == pytest.approx(total)  # scrolled to the end, not past
    _post(loaded, "/lab/view", action="region")
    start, length = lab.view(_meta(loaded))
    assert start < 10 and start + length > 12
    assert length == pytest.approx(2.5, rel=0.01)
    r = _post(loaded, "/lab/view", action="all")
    assert (
        lab.view(_meta(loaded)) == (0.0, total)
        and 'lab-zoom-readout"></output>' in r.text
    )
    assert _post(loaded, "/lab/view", action="out").status_code == 200  # a no-op
    assert _post(loaded, "/lab/view", action="sideways").status_code == 422


def test_the_page_says_which_span_it_zoomed_to_and_the_server_holds_it(loaded):
    """Ctrl + wheel and the overview's box zoom on the page at once and
    post where they ended (labview.js): `set` keeps that span, held inside
    the song and over the floor."""
    total = _meta(loaded)["seconds"]
    r = _post(loaded, "/lab/view", action="set", start="3.25", seconds="5.5")
    assert lab.view(_meta(loaded)) == pytest.approx((3.25, 5.5))
    assert 'data-view-start="3.25"' in r.text and 'data-view-seconds="5.5"' in r.text
    _post(loaded, "/lab/view", action="set", start="-4", seconds="0.01")
    start, length = lab.view(_meta(loaded))
    assert start == 0.0 and length == pytest.approx(lab.MIN_VIEW_SECONDS)
    _post(loaded, "/lab/view", action="set", start=f"{total}", seconds="4")
    start, length = lab.view(_meta(loaded))
    assert start + length == pytest.approx(total)
    _post(loaded, "/lab/view", action="set", start="0", seconds=f"{total * 3}")
    assert lab.view(_meta(loaded)) == (0.0, total)  # wider than the song: all of it
    for bad in (
        {"start": "x", "seconds": "2"},
        {"start": "1"},
        {"start": "1", "seconds": "-2"},
    ):
        assert _post(loaded, "/lab/view", action="set", **bad).status_code == 422


def test_a_zoomed_waveform_draws_its_own_span_and_a_drag_is_in_song_time(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="6", bars="1")
    for _ in range(2):
        r = _post(loaded, "/lab/view", action="in")  # 4 s: bars 5..6 or so
    start, length = lab.view(_meta(loaded))
    assert length == pytest.approx(4.0)
    # the region's band is drawn over the span shown, wide (on the lanes;
    # the overview strip draws it over the whole song)
    lanes = r.text.split('class="lab-svg"', 1)[1]
    wide = float(re.search(r'lw-region" x="[\d.]+" y="0" width="([\d.]+)', lanes)[1])
    assert wide == pytest.approx(lab.W * 2.0 / length, abs=2)
    # every lane: the peaks as its outline, the RMS as its body
    assert r.text.count('class="lw-wave"') >= 2 and 'class="lw-rms"' in r.text
    # a drag is in seconds of the song, whatever is shown
    _post(
        loaded,
        "/lab/region",
        drag_a=f"{start + 0.1}",
        drag_b=f"{start + length / 4:.3f}",
    )
    a, _b = lab.span(_meta(loaded))
    assert start - BAR < a <= start + 0.2
    # the peaks of a span are read from the audio, and are not flat
    data_dir = loaded.app.state.svc.data_dir
    pk = sources.window_peaks(data_dir, _meta(loaded), "mix", 2, 4)
    assert pk.shape == (1200,) and pk.max() > 0.1
    cols = sources.window_columns(data_dir, _meta(loaded), "mix", 2, 4)
    assert cols.shape == (2, 1200) and np.all(cols[1] <= cols[0] + 1e-6)
    assert cols[1].max() > 0.05  # the body is there, under the outline
    assert sources.window_peaks(data_dir, _meta(loaded), "nope", 0, 1).max() == 0


def test_a_source_stored_before_the_rms_gets_it_once(loaded, tmp_path):
    meta = _meta(loaded)
    rms = tmp_path / "sources" / meta["id"] / "mix.rms.npy"
    rms.unlink()
    cols = sources.overview(loaded.app.state.svc.data_dir, meta)
    assert rms.is_file() and cols.shape == (2, 1200)
    assert np.allclose(cols[0], sources.peaks(loaded.app.state.svc.data_dir, meta))
    assert 0 < cols[1].max() <= cols[0].max()
    assert lab.display_gain(cols[0]) == pytest.approx(min(8, 0.95 / cols[0].max()))
    assert lab.display_gain(np.zeros(4)) == 1.0


def test_a_view_edited_on_disk_is_held_inside_the_song(loaded, tmp_path):
    sid = source_id(loaded)
    path = tmp_path / "sources" / sid / "meta.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for bad in (
        {"start": 1e9, "length": 3},
        {"start": "x", "length": -5},
        "wide",
        None,
    ):
        raw["view"] = bad
        path.write_text(json.dumps(raw), encoding="utf-8")
        start, length = lab.view(_meta(loaded))
        assert 0 <= start and start + length <= _meta(loaded)["seconds"] + 1e-6
        assert loaded.get("/view/lab").status_code == 200


def test_switching_units_keeps_the_audio_the_region_covers(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="2", bars="3")
    before = lab.span(_meta(loaded))
    _post(loaded, "/lab/region", mode="seconds")
    assert _meta(loaded)["region"]["mode"] == "seconds"
    assert np.allclose(lab.span(_meta(loaded)), before, atol=1e-6)


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "bars", "bar": "-40", "bars": "1e9"},
        {"mode": "seconds", "start": "nan", "length": "inf"},
        {"mode": "seconds", "start": "1e12", "length": "-1"},
        {"drag_a": "-5", "drag_b": "1e30"},
        {"mode": "sideways", "start": "x", "length": ""},
    ],
)
def test_any_region_post_leaves_a_region_inside_the_song(loaded, data):
    assert _post(loaded, "/lab/region", **data).status_code == 200
    meta = _meta(loaded)
    start, length = lab.span(meta)
    assert 0 <= start <= meta["seconds"] and 0 <= length
    assert start + length <= meta["seconds"] + 1e-6
    assert loaded.get("/view/lab").status_code == 200


def test_the_audition_is_the_region_as_a_wav_and_is_bounded(loaded):
    sid = source_id(loaded)
    r = loaded.get(f"/lab/{sid}/region.wav?start={BAR}&length={BAR}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    audio, sr = read_pcm16(r.content)
    assert sr == SR and abs(len(audio) - BAR * SR) <= 1
    whole = sources.read(loaded.app.state.svc.data_dir, _meta(loaded))
    a = int(round(BAR * SR))
    assert np.allclose(audio[:1000], whole[a : a + 1000], atol=2e-4)
    # clamped, never an error: a huge length is cut, junk reads as nothing
    long = loaded.get(f"/lab/{sid}/region.wav?start=0&length=1e9")
    with wave.open(io.BytesIO(long.content)) as w:
        assert w.getnframes() / SR <= limits.MAX_CLIP_SECONDS
    for q in ("start=nan&length=2", "start=-9&length=x", "start=1e9&length=1", ""):
        assert loaded.get(f"/lab/{sid}/region.wav?{q}").status_code in (200, 404)
    assert loaded.get("/lab/0123456789abcdef/region.wav").status_code == 404


def test_the_audition_is_refused_to_another_site(loaded):
    sid = source_id(loaded)
    r = loaded.get(
        f"/lab/{sid}/region.wav?start=0&length=1",
        headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},
    )
    assert r.status_code == 403


# --- sending -----------------------------------------------------------------


def test_a_region_becomes_a_clip_that_records_where_it_came_from(loaded):
    loaded.app.state.svc.state.arrangement.bpm = float(BPM)
    _post(loaded, "/lab/region", mode="bars", bar="2", bars="2")
    r = _post(loaded, "/lab/send", target="clip", name="hook")
    assert r.status_code == 200 and r.headers["X-Clip-Name"] == "hook"
    data_dir = loaded.app.state.svc.data_dir
    clip = next(c for c in list_clips(data_dir) if c["name"] == "hook")
    assert clip["bars"] == 2 and abs(clip["bpm"] - BPM) < 1
    assert clip["lab"]["source"] == source_id(loaded) and not clip["lab"]["fitted"]
    audio, _sr = load_clip(data_dir, "hook")
    assert abs(len(audio) / SR - 2 * BAR) < 0.05
    again = _post(loaded, "/lab/send", target="clip", name="hook")
    assert again.headers["X-Clip-Name"] == "hook 2"  # never over a clip that exists


def test_fit_stretches_the_clip_so_its_bars_are_the_sessions_bars(loaded):
    """4 bars at 120 bpm, sent into a 100 bpm session: 4 bars at 100."""
    svc = loaded.app.state.svc
    svc.state.arrangement.bpm = 100.0
    _post(loaded, "/lab/region", mode="bars", bar="1", bars="4")
    # the selection bar's Keep clip posts it (a chop's Keep is the other way, with its
    # own cleaning: test_lab_chops.py), its "fit" box beside it
    page = loaded.get("/view/lab").text
    assert 'id="lab-keep"' in page and "Keep clip</button>" in page
    assert 'name="fit"' in page
    r = _post(loaded, "/lab/send", target="clip", name="fitted", fit="on")
    assert r.status_code == 200
    audio, _sr = load_clip(svc.data_dir, "fitted")
    assert abs(len(audio) / SR - 4 * 4 * 60 / 100) < 0.08
    clip = next(c for c in list_clips(svc.data_dir) if c["name"] == "fitted")
    assert clip["bpm"] == 100 and clip["bars"] == 4 and clip["lab"]["fitted"]
    # the pitch stays: the stretch moves time only
    from synthlab.analysis import detect_key

    assert detect_key(audio).tonic == "A"
    _post(loaded, "/lab/send", target="clip", name="plain")
    plain, _sr = load_clip(svc.data_dir, "plain")
    assert abs(len(plain) / SR - 4 * BAR) < 0.05


def test_a_long_region_is_refused_with_its_length_and_the_cap(client):
    long = np.tile(np.sin(2 * np.pi * 220 * np.arange(SR) / SR) * 0.4, 40)
    upload(client, wav_bytes(np.column_stack([long, long])), "long.wav")
    _post(client, "/lab/region", mode="seconds", start="0", length="35")
    for target in ("clip", "sampler"):
        r = _post(client, "/lab/send", target=target)
        assert r.status_code == 422 and "35.0 s" in r.text and "30 s" in r.text
    assert "too long to" in client.get("/view/lab").text
    assert list_clips(client.app.state.svc.data_dir) == []


def test_a_region_becomes_a_sampler_sound_on_synths(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="1", bars="1")
    r = _post(loaded, "/lab/send", target="sampler", name="bar one")
    assert r.status_code == 200 and r.headers["HX-Push-Url"] == "/view/editor"
    patch = loaded.app.state.svc.state.patch
    assert patch.osc1 == "sampler" and patch.name == "bar one"
    assert abs(sampler.seconds(patch.sampler_sample) - BAR) < 0.03
    assert patch.sampler_slices == ""


def test_a_pitched_region_takes_its_detected_root(client):
    t = np.arange(SR * 2) / SR
    tone = np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.5  # A3
    upload(client, wav_bytes(tone), "a3.wav")
    _post(client, "/lab/region", mode="seconds", start="0.2", length="1.0")
    assert _post(client, "/lab/send", target="sampler").status_code == 200
    assert client.app.state.svc.state.patch.sampler_root == 57


@pytest.mark.parametrize(("mode", "count"), [("beats", 8), ("bars", 2)])
def test_auto_chop_lays_a_slice_on_every_beat_or_bar(loaded, mode, count):
    _post(loaded, "/lab/region", mode="bars", bar="2", bars="2")
    assert _post(loaded, "/lab/send", target="sampler", chop=mode).status_code == 200
    slices = sampler.parse_slices(loaded.app.state.svc.state.patch.sampler_slices)
    assert len(slices) == count
    edges = [s.a for s in slices] + [slices[-1].b]
    assert np.allclose(edges, np.linspace(0, 1, count + 1), atol=1e-3)


def test_transient_chop_finds_the_drum_hits(loaded):
    _post(loaded, "/lab/region", mode="bars", bar="1", bars="1")
    r = _post(loaded, "/lab/send", target="sampler", chop="transients")
    assert r.status_code == 200
    slices = sampler.parse_slices(loaded.app.state.svc.state.patch.sampler_slices)
    assert 4 <= len(slices) <= 16


def test_a_free_region_chops_on_the_grid_lines_inside_it(loaded):
    meta = _meta(loaded)
    g = lab.grid(meta)
    lab.set_region(meta, "seconds", g["offset"] + 0.5 * g["beat"], 2 * g["beat"])
    text, _note = lab.chop_slices(meta, "beats")
    edges = [s.a for s in sampler.parse_slices(text)]
    assert np.allclose(edges, [0.0, 0.25, 0.75], atol=1e-3)


@pytest.mark.parametrize(
    "data", [{"target": "moon"}, {"target": "sampler", "chop": "x"}, {}]
)
def test_an_unknown_target_or_chop_is_refused(loaded, data):
    assert _post(loaded, "/lab/send", **data).status_code == 422


def test_every_route_answers_a_missing_song_with_a_toast(client):
    gone = {"source": "0123456789abcdef"}
    for path, extra in [
        ("/lab/select", {}),
        ("/lab/delete", {}),
        ("/lab/grid", {"action": "half"}),
        ("/lab/region", {"mode": "bars"}),
        ("/lab/send", {"target": "clip"}),
        ("/lab/separate", {}),
    ]:
        r = client.post(path, data={**gone, **extra}, headers=LAB)
        assert r.status_code == 422 and "toast" in r.text, path


def test_the_engines_stretch_keeps_pitch_and_changes_length():
    t = np.arange(SR * 2) / SR
    tone = np.sin(2 * np.pi * 440 * t) * 0.5
    out = sampler.time_stretch(tone, 1.25)
    assert out.shape == (int(SR * 2.5), 2)
    spectrum = np.abs(np.fft.rfft(out[:, 0]))
    assert abs(np.argmax(spectrum) * SR / len(out) - 440) < 2.0
    assert sampler.time_stretch(tone, 1.0).shape == (SR * 2, 2)
