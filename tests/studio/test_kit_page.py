"""The Kit page plays the acoustic kit: its sound (`POST /fragment/drums/
source`), the download (`kitjobs.py`, `/fragment/drums/kit/*`), the credit,
the mic mixer (`POST /fragment/drums/mics`), the rows a grid draws for its
beat's kit (`state.kit_rows`, and the rows it does not draw kept on a post),
the articulation lane and a pad on the kit.

The kit is a fake (no network, no kit on disk): `fetch.availability` and
`play.render_hit` are replaced where a test needs the kit here."""

from __future__ import annotations

import io
import re
import threading
import zipfile
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.kits import fetch, play, sfz
from synthlab.studio import arrange, kitjobs
from synthlab.studio.server import create_app
from synthlab.studio.state import ACOUSTIC_ROWS, DRUM_ROWS, kit_rows
from tests.studio.test_song_routes import use_song

CREDIT = "Naked Drums by Wilkinson Audio, CC-BY 4.0"
URL = "https://github.com/sfzinstruments/WilkinsonAudio.NakedDrums"
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/drums"}


@pytest.fixture
def no_kit(tmp_path, monkeypatch):
    """No kit on this machine (an empty kit folder)."""
    monkeypatch.setenv(fetch.KIT_ENV, str(tmp_path / "no-kit"))
    arrange.forget_kit()
    yield
    arrange.forget_kit()


@pytest.fixture
def kit(tmp_path, monkeypatch):
    """A ready fake kit; yields `render_hit`'s calls."""
    calls: list[tuple] = []

    def render_hit(kmap, root, row, art, velocity, rr, mics, sr=44100):
        calls.append((row, art, velocity, rr))
        return np.full((4410, 2), 0.05)

    monkeypatch.setenv(fetch.KIT_ENV, str(tmp_path / "kit"))
    monkeypatch.setattr(play, "render_hit", render_hit)
    monkeypatch.setattr(fetch, "availability", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(sfz, "read_kit", lambda root: sfz.KitMap({}, ()))
    arrange.forget_kit()
    yield calls
    arrange.forget_kit()


@pytest.fixture
def client(tmp_path, no_kit):
    with TestClient(create_app(data_dir=tmp_path / "data")) as c:
        yield c


def _st(c):
    return c.app.state.studio


def _set(c, **fields):
    s = _st(c)
    s.arrangement = replace(s.arrangement, **fields)


def _rows(html: str, grid: str = "drum-grid") -> list[str]:
    form = html[html.index(f'<form id="{grid}"') :]
    form = form[: form.index("</form>")]
    return re.findall(
        r'class="gridrow drumrow[^"]*"[^>]*data-voice="([a-z0-9]+)"', form
    )


def _source(c, src: str):
    return c.post("/fragment/drums/source", data={"source": src}, headers=HX)


# --- the export's notes ---------------------------------------------------------------


def _notes(c) -> str | None:
    z = zipfile.ZipFile(io.BytesIO(c.get("/project.zip").content))
    return z.read("NOTES.txt").decode() if "NOTES.txt" in z.namelist() else None


def test_a_project_export_using_the_kit_carries_the_credit(client):
    assert _notes(client) is None  # nothing plays the kit
    _set(client, drum_kit="naked")
    notes = _notes(client)
    assert CREDIT in notes and URL in notes
    # a song's pattern alone plays it: still credited
    _set(client, drum_kit="")
    use_song(client)
    assert _notes(client) is None
    _st(client).song["patterns"]["pd"]["kit"] = "naked"
    assert CREDIT in _notes(client)
    # and the bundle reads back (the notes are for people)
    data = client.get("/project.zip").content
    r = client.post(
        "/project/import", files={"file": ("p.zip", data, "application/zip")}
    )
    assert r.status_code < 400, r.text[:200]


# --- the kit's sound ------------------------------------------------------------------


def test_choosing_acoustic_converts_open_hats_in_one_undo_step(client):
    _set(
        client,
        drums=[["hat", 0], ["kick", 0], ["ohat", 2], ["ohat", 6]],
        drum_hits={"ohat.2": {"p": 0.5, "r": 2}},
        drum_mix={"ohat": {"prob": 0.8}},
    )
    s = _st(client)
    undo = len(s._pattern_undo)
    assert _source(client, "naked").status_code == 200
    a = s.arrangement
    assert a.drum_kit == "naked"
    assert sorted(map(tuple, a.drums)) == [
        ("hat", 0),
        ("hat", 2),
        ("hat", 6),
        ("kick", 0),
    ]
    assert a.drum_hits == {
        "hat.2": {"a": "open", "p": 0.4, "r": 2},
        "hat.6": {"a": "open", "p": 0.8},
    }
    assert "ohat" not in a.drum_mix
    assert len(s._pattern_undo) == undo + 1  # one step, kit and all
    client.post("/fragment/pattern/undo", headers=HX)
    a = s.arrangement
    assert a.drum_kit == "" and ["ohat", 2] in a.drums
    assert a.drum_hits == {"ohat.2": {"p": 0.5, "r": 2}}


def test_going_back_to_electronic_puts_the_open_hats_on_their_row(client):
    _set(client, drum_kit="naked", drums=[["hat", 0], ["hat", 4]])
    _set(client, drum_hits={"hat.4": {"a": "open", "v": 0.5}, "hat.0": {"a": "pedal"}})
    _source(client, "")
    a = _st(client).arrangement
    assert a.drum_kit == ""
    assert sorted(map(tuple, a.drums)) == [("hat", 0), ("ohat", 4)]
    # (a pedal stays: it means nothing on the electronic kit, and plays again
    # on the acoustic one)
    assert a.drum_hits == {"ohat.4": {"v": 0.5}, "hat.0": {"a": "pedal"}}


def test_a_bad_kit_sound_changes_nothing(client):
    r = _source(client, "tr808")
    assert r.status_code == 422 and "No such kit sound" in r.text
    assert _st(client).arrangement.drum_kit == ""


def test_the_acoustic_kit_shows_its_rows(client):
    assert _rows(client.get("/view/drums").text) == DRUM_ROWS
    _set(client, drum_kit="naked", drums=[["kick", 0]])
    html = client.get("/view/drums").text
    assert _rows(html) == ACOUSTIC_ROWS  # clap and rim hidden while empty
    _set(client, drums=[["kick", 0], ["clap", 4]])
    assert _rows(client.get("/view/drums").text) == ACOUSTIC_ROWS + ["clap"]
    perform = client.get("/view/perform").text
    assert _rows(perform, "perform-drum-grid") == ACOUSTIC_ROWS + ["clap"]
    assert kit_rows("naked", [["rim", 1], ["ohat", 2]]) == [
        *ACOUSTIC_ROWS,
        "ohat",
        "rim",
    ]


def test_a_grid_post_keeps_the_rows_it_does_not_draw(client):
    """A grid posts the rows it drew (`rows`); a row it did not draw — a
    page drawn before the ride had cells — keeps its cells and hits."""
    _set(
        client,
        drums=[["kick", 0], ["ride", 2], ["ride", 6]],
        drum_hits={"ride.2": {"v": 0.5}, "kick.0": {"v": 0.7}},
    )
    eight = ",".join(DRUM_ROWS)
    r = client.post(
        "/fragment/drums/grid",
        data={"rows": eight, "drum.kick.0": "on", "drum.snare.4": "on", "hits": "{}"},
        headers=HX,
    )
    assert r.status_code == 200
    a = _st(client).arrangement
    assert sorted(map(tuple, a.drums)) == [
        ("kick", 0),
        ("ride", 2),
        ("ride", 6),
        ("snare", 4),
    ]
    assert a.drum_hits == {"ride.2": {"v": 0.5}}
    # the page draws the ride now (it plays: the crash voice) and posts it
    html = client.get("/view/drums").text
    assert _rows(html) == [*DRUM_ROWS, "ride"]
    assert f'name="rows" value="{eight},ride"' in html
    client.post(
        "/fragment/drums/grid",
        data={"rows": f"{eight},ride", "drum.kick.0": "on"},
        headers=HX,
    )
    assert _st(client).arrangement.drums == [["kick", 0]]
    # a post without `rows` (a script's): the rows the beat's kit draws now
    _set(client, drums=[["kick", 0], ["china", 1]], drum_kit="naked")
    client.post("/fragment/drums/grid", data={"drum.kick.0": "on"}, headers=HX)
    assert _st(client).arrangement.drums == [["kick", 0]]


def test_an_electronic_beat_shows_the_further_rows_it_plays():
    assert kit_rows("", [["kick", 0]]) == DRUM_ROWS
    assert kit_rows("", [["china", 1], ["ride", 3]]) == [*DRUM_ROWS, "ride", "china"]


def test_a_song_pattern_post_keeps_the_rows_its_grid_does_not_draw(client):
    use_song(client)
    p = _st(client).song["patterns"]["pd"]
    p["drums"] = [["kick", 0], ["china", 3]]
    r = client.post(
        "/song/pattern/pd/grid",
        data={"rows": ",".join(DRUM_ROWS), "drum.kick.0": "on", "drum.kick.4": "on"},
        headers={**HX, "HX-Current-URL": "http://testserver/view/song"},
    )
    assert r.status_code == 200, r.text[:200]
    got = _st(client).song["patterns"]["pd"]["drums"]
    assert sorted(map(tuple, got)) == [("china", 3), ("kick", 0), ("kick", 4)]


# --- the download ---------------------------------------------------------------------


def test_a_missing_kit_says_so(client):
    html = client.get("/view/drums").text
    assert "Download Naked Drums" not in html  # an electronic beat
    _set(client, drum_kit="naked")
    html = client.get("/view/drums").text
    assert f"Download Naked Drums ({kitjobs.size_label()})" in html
    assert kitjobs.size_label() == "1.3 GB"
    assert "electronic until it lands" in html


def test_a_ready_kit_asks_for_nothing(kit, tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as c:
        _set(c, drum_kit="naked")
        assert "Download Naked Drums" not in c.get("/view/drums").text


class _Fake:
    """`KitJobs.fetch_kit` without a network: progress, then whatever the
    test makes it do (`gate`: wait; `fail`: a KitError)."""

    def __init__(self, fail: str = ""):
        self.fail, self.gate, self.ran = fail, threading.Event(), 0

    def __call__(self, progress, cancel):
        self.ran += 1
        progress(400_000_000, 1_285_000_000)
        self.gate.wait(5)
        if cancel():
            raise fetch.Cancelled("Download cancelled.")
        if self.fail:
            raise fetch.KitError(self.fail)


def test_the_download_runs_once_and_says_how_it_ended(client, monkeypatch):
    svc = client.app.state.svc
    fake = _Fake()
    monkeypatch.setattr(svc.kits, "fetch_kit", fake)
    forgot = []
    monkeypatch.setattr(arrange, "forget_kit", lambda: forgot.append(1))
    _set(client, drum_kit="naked")
    r = client.post("/fragment/drums/kit/download", headers=HX)
    assert r.status_code == 200 and 'id="kit-source"' in r.text
    assert "/fragment/drums/kit/job?watch=1" in r.text  # it polls
    r = client.post("/fragment/drums/kit/download", headers=HX)
    assert "already downloading" in r.text  # one at a time
    line = client.get("/fragment/drums/kit/job?watch=1").text
    assert "400/1285 MB" in line and "drum-panels" not in line
    fake.gate.set()
    svc.kits.join()
    assert fake.ran == 1 and forgot  # the render asks the disk again
    ended = client.get("/fragment/drums/kit/job?watch=1").text
    assert 'id="drum-panels"' in ended and "hx-swap-oob" in ended
    assert "every 1s" not in ended


def test_a_failed_download_shows_its_error_and_a_cancel_stops_it(client, monkeypatch):
    svc = client.app.state.svc
    _set(client, drum_kit="naked")
    fake = _Fake(fail="LICENSE did not match its pinned SHA-256; it was deleted.")
    monkeypatch.setattr(svc.kits, "fetch_kit", fake)
    client.post("/fragment/drums/kit/download", headers=HX)
    fake.gate.set()
    svc.kits.join()
    html = client.get("/view/drums").text
    assert "LICENSE did not match its pinned SHA-256" in html
    assert "Download Naked Drums" in html  # and it can be tried again
    fake = _Fake()
    monkeypatch.setattr(svc.kits, "fetch_kit", fake)
    client.post("/fragment/drums/kit/download", headers=HX)
    client.post("/fragment/drums/kit/cancel", headers=HX)
    fake.gate.set()
    svc.kits.join()
    assert svc.kits.last_error() == "Cancelled."


# --- the credit and the mic mixer -----------------------------------------------------


def test_the_credit_shows_with_the_acoustic_kit(client):
    html = client.get("/view/drums").text
    assert CREDIT not in html and 'id="drumkit"' in html
    _set(client, drum_kit="naked")
    html = client.get("/view/drums").text
    assert CREDIT in html and f'href="{URL}"' in html
    # the mic mixer stands in for the eight strips
    assert 'id="drumkit"' not in html
    for mic in play.MICS:
        assert f'id="mic-{mic}"' in html


def test_mic_preset_and_fader_write_drum_mics(client):
    _set(client, drum_kit="naked")

    def mics(**data):
        r = client.post("/fragment/drums/mics", data=data, headers=HX)
        assert r.status_code == 200, r.text[:200]
        return _st(client).arrangement.drum_mics

    assert mics(preset="close") == {"preset": "close"}
    assert mics(overheads="-9") == {"preset": "close", "overheads": -9.0}
    assert mics(kick_in="off") == {
        "preset": "close",
        "overheads": -9.0,
        "kick_in": None,
    }
    # the preset's own level is not written down; nonsense changes nothing
    assert mics(overheads="-6", nope="3", kick_sub="loud") == {
        "preset": "close",
        "kick_in": None,
    }
    assert mics(far_room="99")["far_room"] == 12.0  # held to the range
    assert mics(preset="studio") == {}  # a preset: every mic at its levels
    r = client.post("/fragment/drums/mics", data={"preset": "arena"}, headers=HX)
    assert r.status_code == 422 and "No such mic preset" in r.text


def test_a_fader_post_is_not_drawn_again_but_a_mute_is(client):
    _set(client, drum_kit="naked")
    r = client.post("/fragment/drums/mics", data={"hats": "-3"}, headers=HX)
    assert 'id="drum-panels"' not in r.text
    r = client.post("/fragment/drums/mics", data={"hats": "off"}, headers=HX)
    assert 'id="drum-panels"' in r.text
    assert re.search(r'data-mic="hats"', r.text)


# --- the grid's articulation lane and a pad on the kit --------------------------------


def test_the_articulation_lane_and_glyphs_are_the_acoustic_kits(client):
    hits = {"hat.0": {"a": "open"}}
    _set(client, drums=[["hat", 0]], drum_hits=hits)
    html = client.get("/view/drums").text
    assert "artrow" not in html and 'data-art="open"' not in html
    _set(client, drum_kit="naked")
    html = client.get("/view/drums").text
    assert html.count('class="velrow artrow"') == 1
    assert 'name="drum.hat.0"' in html and 'data-art="open"' in html
    assert 'data-hit-set="a=open"' in html  # the hit menu lists them
    assert "artrow" in client.get("/view/perform").text


def test_a_pad_on_the_kit_plays_its_default_articulation(kit, tmp_path):
    with TestClient(create_app(data_dir=tmp_path / "data")) as c:
        _set(c, drum_kit="naked")
        for row, art in (("hat", "closed"), ("tom3", "hit"), ("ride", "bow")):
            r = c.get(f"/drum/{row}/hit.wav")
            assert r.status_code == 200 and r.content[:4] == b"RIFF"
            assert kit[-1] == (row, art, round(0.9 * 127) / 127, 0)
        # a further row on an electronic beat plays its stand-in voice
        _set(c, drum_kit="")
        n = len(kit)
        assert c.get("/drum/china/hit.wav").status_code == 200
        assert len(kit) == n
        assert c.get("/drum/nope/hit.wav").status_code == 404


def test_an_electronic_pick_shows_the_kit_sound_it_left(client):
    """A factory beat (or a genre kit, the Lab's or the clip hub's beat) is
    electronic: the kit lives on the beat. The Kit sound select and the
    kit's panel say so at once, out of band."""
    _set(client, drum_kit="naked")
    r = client.post("/fragment/drums/bank/load/0", headers=HX)
    assert _st(client).arrangement.drum_kit == ""
    source = r.text[r.text.index('id="kit-source"') :]
    assert '<option value="" selected>Electronic</option>' in source
    assert 'id="drum-panels"' in r.text and 'id="drumkit"' in r.text


def test_a_beats_pictures_draw_its_further_rows():
    from synthlab.studio import loops
    from synthlab.studio.service.songpage import _thumb

    eight = {"bars": 1, "steps_per_bar": 16, "steps": [["kick", 0], ["crash", 2]]}
    assert loops.beat_roll(eight)["h"] == 8  # as it always was
    more = {**eight, "steps": [["kick", 0], ["ride", 2], ["china", 3]]}
    roll = loops.beat_roll(more)
    assert roll["h"] == 10 and "M2 8.5h0.7" in roll["d"] and "M3 9.5h0.7" in roll["d"]
    p = {"kind": "drums", "bars": 1, "steps": 16, "drums": [["kick", 0], ["tom3", 5]]}
    assert _thumb(p) == {"w": 16, "h": 9, "marks": [(0, 0), (5, 8)]}


def test_a_mute_keeps_the_mics_own_level(client):
    _set(client, drum_kit="naked")

    def mics(**data):
        client.post("/fragment/drums/mics", data=data, headers=HX)
        return _st(client).arrangement.drum_mics

    assert mics(overheads="-3") == {"overheads": -3.0}
    assert mics(**{"mute.overheads": "1"}) == {
        "overheads": -3.0,
        "muted": ["overheads"],
    }
    assert play.levels(_st(client).arrangement.drum_mics)["overheads"] is None
    html = client.get("/view/drums").text
    strip = html[html.index('data-mic="overheads"') :]
    assert 'value="-3.0"' in strip[:600] and '"mute.overheads": "0"' in strip[:1500]
    assert mics(**{"mute.overheads": "0"}) == {"overheads": -3.0}  # back at -3
    # a fader on a muted mic lifts the mute
    mics(**{"mute.hats": "1"})
    assert mics(hats="-6") == {"overheads": -3.0, "hats": -6.0}
    # a preset puts every mic at its levels, mutes and all
    mics(**{"mute.hats": "1"})
    assert mics(preset="room") == {"preset": "room"}


def test_a_damaged_kit_plays_electronic_and_says_so(tmp_path, monkeypatch):
    """A kit there by size whose file is corrupt (or whose SFZ disagrees
    with its files): the render never fails — that row plays its
    electronic voice — and the Kit page offers the download again."""

    def broken(*a, **k):
        raise fetch.KitError("Samples/OH_A2_10_one-CWG6.flac could not be read")

    monkeypatch.setenv(fetch.KIT_ENV, str(tmp_path / "kit"))
    monkeypatch.setattr(play, "render_hit", broken)
    monkeypatch.setattr(fetch, "availability", lambda *a, **k: {"ready": True})
    monkeypatch.setattr(sfz, "read_kit", lambda root: sfz.KitMap({}, ()))
    arrange.forget_kit()
    try:
        with TestClient(create_app(data_dir=tmp_path / "data")) as c:
            _set(c, drum_kit="naked", drums=[["hat", 0], ["kick", 4]])
            assert "The kit is damaged" not in c.get("/view/drums").text
            r = c.post("/fragment/preview", data={"view": "drums"}, headers=HX)
            assert r.status_code == 200, r.text[:200]
            assert "preview-ready" in r.headers.get("HX-Trigger", "")
            # the render's answer carries the kit line, out of band
            assert "The kit is damaged" in r.text
            html = c.get("/view/drums").text
            assert "The kit is damaged" in html and '"repair": "1"' in html
            started = []
            monkeypatch.setattr(
                c.app.state.svc.kits,
                "start",
                lambda repair=False: started.append(repair) or "",
            )
            c.post("/fragment/drums/kit/download", data={"repair": "1"}, headers=HX)
            assert started == [True]
    finally:
        arrange.forget_kit()
    assert arrange.kit_damage() == ""  # a download forgets it


def test_a_stopped_download_says_what_is_left(client, monkeypatch):
    monkeypatch.setattr(
        fetch,
        "availability",
        lambda *a, **k: {"ready": False, "megabytes": 240, "reason": "x"},
    )
    arrange.forget_kit()
    _set(client, drum_kit="naked")
    assert (
        "Finish downloading Naked Drums (240 MB left)" in client.get("/view/drums").text
    )


def test_the_pages_subtitle_names_its_kit(client):
    assert "eight voices" in client.get("/view/drums").text
    _set(client, drum_kit="naked")
    html = client.get("/view/drums").text
    assert "eight voices" not in html and "multi-sampled acoustic kit" in html
