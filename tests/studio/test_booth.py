"""The vocal booth (the Voice page's "4 · Treat"; `booth.py`,
`routes/booth.py`; `docs/sampler-and-clips.md`, "The vocal booth"):
treatments as data, the offline moves, a render held to keep, the rack
edited as the Song audio track's is, and the two ways out — a clip, a take
on the song. What a treatment sounds like is not tested here: the units'
own measurements are `tests/test_effects_booth.py`'s; nobody has listened."""

from __future__ import annotations

import io
import json
import re
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.audio import read_pcm16
from synthlab.core import SR
from synthlab.studio import booth, cliplib, limits, voice
from synthlab.studio.clips import list_clips, save_clip
from synthlab.studio.fxrack import clean_chain
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from tests.studio.test_song_routes import use_song

ARR = Arrangement(bpm=120.0)


def _hx() -> dict:
    return {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/voice"}


def _wav(samples) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _speech(seconds: float = 1.0) -> np.ndarray:
    n = int(SR * seconds)
    gate = np.sin(2 * np.pi * 2 * np.arange(n) / SR) > 0
    return np.random.default_rng(0).standard_normal(n) * 0.4 * gate


@pytest.fixture
def client(tmp_path):
    voice.RESULTS = voice.LRU(4)
    booth.RESULTS = booth.LRU(4)
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _take(client, samples=None) -> None:
    samples = _speech() if samples is None else samples
    r = client.post(
        "/voice/take", files={"file": ("t.wav", _wav(samples), "audio/wav")}
    )
    assert r.status_code == 200, r.text[:300]


def _lab(client) -> dict:
    return voice.read_lab(client.app.state.studio.data_dir)


def _heard(r) -> np.ndarray:
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]
    assert url.startswith("/preview/booth:")
    return url


def _treated(client, name: str):
    return client.post("/voice/booth/render", data={"treatment": name}, headers=_hx())


# --- treatments as data --------------------------------------------------------


def test_every_treatment_is_clean_named_by_technique_and_distinct():
    labels = [t["label"] for t in booth.TREATMENTS.values()]
    assert len(set(labels)) == len(labels)
    for name, t in booth.TREATMENTS.items():
        assert clean_chain(t["chain"]) == t["chain"], name  # stored clean
        assert booth.clean_moves(t["moves"]) == booth.treatment(name)["moves"]
        assert t["title"] and len(t["title"]) < 160, name
        # named by technique, never by band (engine.md, "Genre layer")
        for band in ("beatles", "tame", "impala", "lennon", "parker"):
            assert band not in (t["title"] + t["label"]).casefold(), name
    # the treatments the booth must offer
    want = {
        "Leslie vocal",
        "ADT double",
        "Tape slapback",
        "Phaser-and-echo wash",
        "Octave double",
        "Telephone",
        "Backwards swell",
        "Varispeed tape",
        "Dreamy plate",
    }
    assert want <= set(labels)
    assert booth.treatment("nope") is None and booth.treatment(None) is None


FAST = [{"kind": "rotary", "params": {"speed": 7}}]


@pytest.mark.parametrize(
    "raw",
    [None, 7, "x", [], {"treatment": "nope", "source": "mic", "chain": "x"},
     {"moves": {"rate": 1e400, "swell": "loud", "reverse": "maybe"}},
     {"moves": {"rate": -3, "swell": 9}, "chain": FAST}],
)  # fmt: skip
def test_a_booth_of_any_shape_is_cleaned(raw):
    b = booth.clean_booth(raw)
    assert set(b) == {"treatment", "source", "chain", "moves", "tempo"}
    assert b["treatment"] in ("", *booth.TREATMENTS)
    assert b["source"] in booth.SOURCES and b["tempo"] in booth.TEMPOS
    lo, hi = booth.RATE_RANGE
    assert lo <= b["moves"]["rate"] <= hi and 0 <= b["moves"]["swell"] <= 1
    assert isinstance(b["moves"]["reverse"], bool)
    for u in b["chain"]:
        assert u["params"].get("speed", 0) <= 1


# --- the audio ------------------------------------------------------------------


def test_moves_reverse_varispeed_and_the_swell():
    x = np.zeros(SR)
    x[1000:3000] = np.hanning(2000)
    st = booth.stereo(x)
    still = booth.MOVES
    np.testing.assert_array_equal(booth.apply_moves(st, still), st)
    back = booth.apply_moves(st, {**still, "reverse": True})
    np.testing.assert_allclose(back, st[::-1])
    fast = booth.apply_moves(st, {**still, "rate": 2.0})
    assert abs(len(fast) - SR // 2) <= 2  # the tape run twice as fast
    sw = booth.apply_moves(st, {**still, "swell": 1.0})
    lead = int(round(booth.SWELL_LEAD * SR))
    assert len(sw) == SR + lead
    # the voice, moved later by the lead; the swell rises in front of it
    voice_at = lead + 1000
    before = sw[lead:voice_at, 0]
    assert np.max(np.abs(before)) > 0.01
    rise = np.abs(sw[:voice_at, 0]).reshape(-1, 50).max(axis=1)
    assert rise[-5:].mean() > rise[: len(rise) // 4].mean() * 3  # it swells


def test_treat_rings_on_cuts_the_quiet_tail_and_peaks_at_the_audition_level():
    x = _speech(0.5)
    plate = [{"kind": "plate", "params": {"decay": 1.0, "mix": 0.5}}]
    out = booth.treat(x, plate, booth.MOVES, ARR)
    assert out.shape[1] == 2 and len(out) > len(x)  # the plate's ring is kept
    assert len(out) < len(x) + booth.TAIL * SR  # ...cut where it falls quiet
    assert np.max(np.abs(out)) == pytest.approx(booth.PEAK)
    dry = booth.treat(x, [], booth.MOVES, ARR)
    assert len(dry) == len(x)  # nothing rings: the take's own length
    # a whole 30 s take keeps its last words and rings on past them
    take = _speech(limits.MAX_CLIP_SECONDS)
    long = booth.treat(take, plate, booth.MOVES, ARR)
    assert len(take) < len(long) <= int(booth.MAX_SECONDS * SR)


def test_a_backwards_swell_never_costs_a_full_take_its_tail():
    """The swell starts SWELL_LEAD before the voice, so the result is that
    much longer: it used to be cut at a clip's 30 s, and a full take lost
    its last 1.5 s. The cap is MAX_SECONDS (the take, the lead, the ring)."""
    n = int(limits.MAX_CLIP_SECONDS * SR)
    take = np.zeros(n)
    take[-SR // 10 :] = 0.5 * np.sin(2 * np.pi * 220 * np.arange(SR // 10) / SR)
    swell = {**booth.MOVES, "swell": 0.8}
    out = booth.treat(take, [], swell, ARR)
    lead = int(round(booth.SWELL_LEAD * SR))
    assert len(out) >= n + lead
    last = out[lead + n - SR // 10 : lead + n, 0]  # the take's last words
    assert np.max(np.abs(last)) > 0.3
    # with a ring too: still all there, within the cap
    plate = [{"kind": "plate", "params": {"decay": 2.0, "mix": 0.5}}]
    rung = booth.treat(take, plate, swell, ARR)
    assert n + lead < len(rung) <= int(booth.MAX_SECONDS * SR)
    assert booth.MAX_SECONDS == limits.MAX_TREATED_SECONDS
    assert limits.MAX_TREATED_SECONDS >= (
        limits.MAX_CLIP_SECONDS + booth.SWELL_LEAD + booth.TAIL
    )


def test_keep_holds_a_treated_clip_longer_than_a_clip(tmp_path):
    """save_clip's cap: a clip's 30 s unless the caller says (the booth's
    results), and never past MAX_STORED_CLIP_SECONDS whatever it says (the
    free take's 60 s sets the ceiling)."""
    long = np.zeros(int((limits.MAX_STORED_CLIP_SECONDS + 5) * SR))
    save_clip(tmp_path, "plain", long)
    save_clip(tmp_path, "treated", long, cap=booth.MAX_SECONDS)
    save_clip(tmp_path, "greedy", long, cap=99.0)
    lens = {c["name"]: c["seconds"] for c in list_clips(tmp_path)}
    assert lens["plain"] == pytest.approx(limits.MAX_CLIP_SECONDS)
    assert lens["treated"] == pytest.approx(booth.MAX_SECONDS)
    assert lens["greedy"] == pytest.approx(limits.MAX_STORED_CLIP_SECONDS)


def test_synced_units_follow_the_song_for_a_song_take_else_the_loop():
    """`booth.follows`: the booth's choice, or on auto the song's tempo for
    a take the song plays or while a song track's sound is edited."""
    song = {"bpm": 96.0, "sections": [{"audio": [{"clip": "verse vox"}]}]}
    b = booth.clean_booth({})
    assert b["tempo"] == "auto"
    take = {"kind": "clip", "name": "verse vox", "hash": ""}
    other = {"kind": "clip", "name": "jam 3", "hash": ""}
    rec = {"kind": "recording", "name": "", "hash": ""}
    assert booth.follows(b, take, song) == "song"
    assert booth.follows(b, other, song) == "loop"
    assert booth.follows(b, rec, song) == "loop"
    assert booth.follows(b, rec, song, song_edit=True) == "song"
    assert booth.follows({**b, "tempo": "loop"}, take, song) == "loop"
    assert booth.follows({**b, "tempo": "song"}, rec, song) == "song"
    assert booth.tempo_arr(ARR, song, "song").bpm == 96.0
    assert booth.tempo_arr(ARR, song, "loop") is ARR
    # a synced echo lands on the tempo it follows
    echo = [{"kind": "tape_echo", "params": {"sync": 2.0, "feedback": 0.0}}]
    click = np.zeros(SR)
    click[0] = 1.0
    for which, bpm in (("loop", ARR.bpm), ("song", 96.0)):
        out = booth.treat(click, echo, booth.MOVES, booth.tempo_arr(ARR, song, which))
        rep = np.argmax(np.abs(out[SR // 50 :, 0])) + SR // 50
        assert abs(rep / SR - 0.5 * 60.0 / bpm) < 0.02, which  # an eighth


def test_the_key_moves_with_the_chain_the_moves_the_source_and_the_tempo():
    x = _speech(0.3)
    b = booth.clean_booth({"chain": booth.TREATMENTS["slapback"]["chain"]})
    k = booth.audition_key(x, b, ARR)
    assert k.startswith("booth:")
    assert booth.audition_key(x, b, ARR) == k
    assert booth.audition_key(x * 0.5, b, ARR) != k
    assert booth.audition_key(x, {**b, "moves": {**b["moves"], "rate": 1.1}}, ARR) != k
    assert booth.audition_key(x, {**b, "chain": []}, ARR) != k
    assert booth.audition_key(x, b, Arrangement(bpm=90.0)) != k


# --- the routes -----------------------------------------------------------------


def test_a_treatment_is_one_press_from_the_take_to_hearing_it(client):
    _take(client)
    r = client.post("/voice/booth/render", data={"treatment": "adt"}, headers=_hx())
    assert r.status_code == 200
    url = _heard(r)
    audio = read_pcm16(client.get(url).content)[0]
    assert audio.shape[1] == 2 and len(audio) >= SR
    # the doubler panned them apart: the channels differ
    assert np.max(np.abs(audio[:, 0] - audio[:, 1])) > 0.05
    lab = _lab(client)
    assert lab["booth"]["treatment"] == "adt"
    assert lab["booth"]["chain"] == booth.treatment("adt")["chain"]
    assert lab["booth_last"] == url[len("/preview/") : -len(".wav")]
    html = r.text
    assert 'aria-pressed="true"' in html and "ADT double" in html
    assert "Keep as clip" in html and "/voice/booth/keep" in html
    # pressed again: the same audition, answered without rendering
    again = client.post("/voice/booth/render", data={"treatment": "adt"}, headers=_hx())
    assert _heard(again) == url


def test_the_rack_and_the_moves_edit_the_booth_and_say_it_was_edited(client):
    _take(client)
    client.post("/voice/booth/render", data={"treatment": "slapback"}, headers=_hx())
    r = client.post(
        "/voice/booth/fx",
        data={"op": "set", "index": "0", "name": "feedback", "value": "0.7"},
        headers=_hx(),
    )
    assert r.status_code == 200 and "Tape slapback (edited)" in r.text
    assert _lab(client)["booth"]["chain"][0]["params"]["feedback"] == 0.7
    r = client.post("/voice/booth/fx", data={"op": "add", "kind": "rotary"})
    assert [u["kind"] for u in _lab(client)["booth"]["chain"]] == [
        "tape_echo",
        "rotary",
    ]
    for bad in (
        {"op": "add", "kind": "nope"},
        {"op": "remove", "index": "9"},
        {"op": "set", "index": "0", "name": "nope", "value": "1"},
        {"op": "set", "index": "0", "name": "mix", "value": "loud"},
        {"op": "explode"},
    ):
        assert client.post("/voice/booth/fx", data=bad).status_code == 422, bad
    r = client.post(
        "/voice/booth/moves", data={"reverse": "backwards", "swell": "0.5", "rate": "9"}
    )
    moves = _lab(client)["booth"]["moves"]
    assert moves == {"reverse": True, "swell": 0.5, "rate": booth.RATE_RANGE[1]}
    assert 'value="backwards" selected' in r.text
    # a render without a treatment keeps the edits
    r = client.post("/voice/booth/render", data={}, headers=_hx())
    assert (
        r.status_code == 200 and _lab(client)["booth"]["chain"][1]["kind"] == "rotary"
    )


def test_keep_saves_what_was_heard_as_a_voice_take(client):
    _take(client)
    r = client.post(
        "/voice/booth/render", data={"treatment": "telephone"}, headers=_hx()
    )
    heard = read_pcm16(client.get(_heard(r)).content)[0]
    r = client.post("/voice/booth/keep", data={"name": "phone line"}, headers=_hx())
    assert r.status_code == 200 and r.headers["X-Clip-Name"] == "phone line"
    data_dir = client.app.state.studio.data_dir
    (clip,) = [c for c in list_clips(data_dir) if c["name"] == "phone line"]
    assert clip["source"] == "booth" and clip["booth"]["treatment"] == "Telephone"
    assert clip["booth"]["from"] == "take" and not clip["booth"]["edited"]
    assert cliplib.kind_of(clip)[0] == "voice"
    kept = read_pcm16((data_dir / "clips" / "phone line.wav").read_bytes())[0]
    np.testing.assert_allclose(kept, heard, atol=1 / 32767)
    gone = client.post("/voice/booth/keep", data={"key": "booth:nope"})
    assert gone.status_code == 422


def test_to_song_keeps_it_and_places_it_on_the_first_audio_track(client):
    use_song(client)
    s = client.app.state.studio
    _take(client)
    client.post("/voice/booth/render", data={"treatment": "leslie"}, headers=_hx())
    r = client.post("/voice/booth/song", data={"name": "swirl"}, headers=_hx())
    assert r.status_code == 200, r.text[:300]
    first = s.song["sections"][0]
    (take,) = [a for a in first["audio"] if a["clip"] == "swirl"]
    assert take["at_bar"] == 0
    track = next(t for t in s.song["tracks"] if t["id"] == take["track_id"])
    assert track["kind"] == "audio"
    assert (
        "Placed swirl" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    assert any(c["name"] == "swirl" for c in list_clips(s.data_dir))


def test_to_song_with_no_section_is_a_toast_and_keeps_nothing(client):
    _take(client)
    client.post("/voice/booth/render", data={"treatment": "adt"}, headers=_hx())
    s = client.app.state.studio
    s.song["sections"] = []
    before = len(list_clips(s.data_dir))
    r = client.post("/voice/booth/song", data={"name": "lost"}, headers=_hx())
    assert r.status_code == 422 and "no section" in r.text
    assert len(list_clips(s.data_dir)) == before


def test_the_vocoded_result_can_be_treated_too(client):
    _take(client)
    page = client.get("/view/voice").text
    # nothing vocoded yet: "the vocoded result" cannot be picked
    assert re.search(r'<option value="result"\s+disabled>', page)
    vocoded = client.post("/voice/render", data={"bands": "8"}, headers=_hx())
    assert vocoded.status_code == 200
    r = client.post(
        "/voice/booth/render",
        data={"treatment": "dreamy", "source": "result"},
        headers=_hx(),
    )
    assert r.status_code == 200 and _lab(client)["booth"]["source"] == "result"
    client.post("/voice/booth/keep", data={"name": "dream robot"})
    (clip,) = [
        c
        for c in list_clips(client.app.state.studio.data_dir)
        if c["name"] == "dream robot"
    ]
    assert clip["booth"]["from"] == "result"
    # the result gone (a restart): treating it is a toast
    voice.RESULTS = voice.LRU(4)
    r = client.post("/voice/booth/render", data={"source": "result"}, headers=_hx())
    assert r.status_code == 422 and "Vocode it again" in r.text


def test_a_talkbox_take_is_one_press_from_being_the_take(client):
    data_dir = client.app.state.studio.data_dir
    save_clip(data_dir, "talk-1200", _speech(0.4), meta={"source": "talkbox"})
    save_clip(data_dir, "not talk", _speech(0.4), meta={"source": "jam"})
    page = client.get("/view/voice").text
    talk = page[page.index('class="vb-talk"') :]
    talk = talk[: talk.index("</p>")]
    assert "talk-1200" in talk and "not talk" not in talk
    assert (
        cliplib.kind_of({"source": "talkbox", "name": "x", "seconds": 1.0})[0]
        == "voice"
    )
    r = client.post("/voice/take/clip", data={"name": "talk-1200"}, headers=_hx())
    assert r.status_code == 200
    assert _lab(client)["take"]["name"] == "talk-1200"
    r = client.post(
        "/voice/booth/render", data={"treatment": "slapback"}, headers=_hx()
    )
    assert r.status_code == 200


def test_the_tempo_select_is_drawn_kept_and_moves_the_audition(client):
    use_song(client)
    s = client.app.state.studio
    s.song["bpm"] = 97.0
    _take(client)
    page = client.get("/view/voice").text
    form = page[page.index('id="booth-moves"') :]
    form = form[: form.index("</form>")]
    assert 'name="tempo"' in form and "the song&#39;s (97 bpm)" in form
    assert f"auto (the loop&#39;s, {s.arrangement.bpm:g} bpm)" in form
    loop = _heard(_treated(client, "psych_wash"))
    r = client.post("/voice/booth/moves", data={"tempo": "song"}, headers=_hx())
    assert _lab(client)["booth"]["tempo"] == "song"
    assert '<option value="song" selected>' in r.text
    song = _heard(client.post("/voice/booth/render", data={}, headers=_hx()))
    assert song != loop  # the synced echo moved with the tempo
    # a treatment pressed with the select in the form keeps it
    r = client.post(
        "/voice/booth/render",
        data={"treatment": "psych_wash", "tempo": "loop"},
        headers=_hx(),
    )
    assert _lab(client)["booth"]["tempo"] == "loop" and _heard(r) == loop
    client.post("/voice/booth/moves", data={"tempo": "nope"})
    assert _lab(client)["booth"]["tempo"] == "loop"


def test_auto_follows_the_song_for_a_take_the_song_plays(client):
    use_song(client)
    s = client.app.state.studio
    s.song["bpm"] = 97.0
    save_clip(s.data_dir, "verse vox", _speech(0.6), meta={"source": "take"})
    client.post("/voice/take/clip", data={"name": "verse vox"}, headers=_hx())
    loop = _heard(_treated(client, "psych_wash"))
    s.song["sections"][0]["audio"].append(
        {"clip": "verse vox", "track_id": s.song["tracks"][0]["id"], "at_bar": 0}
    )
    page = client.get("/view/voice").text
    assert "auto (the song&#39;s, 97 bpm)" in page
    song = _heard(_treated(client, "psych_wash"))
    assert song != loop


def test_the_talkbox_records_straight_into_the_booth(client):
    """ "⏺ → booth" (talkbox.js): the take kept in Clips, then posted here
    with `booth` — it is the Voice take, and the booth treats the take,
    not a vocoded result of the last one."""
    data_dir = client.app.state.studio.data_dir
    _take(client)
    client.post("/voice/render", data={"bands": "8"}, headers=_hx())
    client.post("/voice/booth/moves", data={"source": "result"})
    assert _lab(client)["booth"]["source"] == "result"
    save_clip(data_dir, "talk-0101", _speech(0.4), meta={"source": "talkbox"})
    r = client.post(
        "/voice/take/clip", data={"name": "talk-0101", "booth": "1"}, headers=_hx()
    )
    assert r.status_code == 200
    lab = _lab(client)
    assert lab["take"]["name"] == "talk-0101" and lab["booth"]["source"] == "take"
    said = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "4 · Treat" in said
    assert 'class="voice-booth"' in r.text
    keys = client.get("/").text  # the keyboard dock's talkbox panel
    assert 'id="talk-rec-booth"' in keys and "⏺ → booth" in keys


def test_the_page_draws_the_booth_with_no_take_and_with_a_hostile_file(client):
    page = client.get("/view/voice").text
    assert "4 · Treat" in page and 'class="voice-booth"' in page
    # a press a treatment, ▶ Hear it, and the empty rack's "+ Plate ▶"
    assert page.count('hx-post="/voice/booth/render"') == len(booth.TREATMENTS) + 2
    data_dir = client.app.state.studio.data_dir
    (data_dir / voice.LAB_NAME).write_text(
        json.dumps(
            {"booth": {"chain": [{"kind": "x"}], "moves": [1]}, "booth_last": 7}
        ),
        encoding="utf-8",
    )
    assert client.get("/view/voice").status_code == 200
    r = client.post("/voice/booth/render", data={"treatment": "adt"}, headers=_hx())
    assert r.status_code == 422  # no take
