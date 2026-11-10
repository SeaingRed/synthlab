"""The clip library: a drawer on every page (`cliplib.py`, region
`clip-library`, `fragments/clip_library.html` + `clip_list.html`).

It replaced the clip shelf Synths and Perform drew. What is held here:
the drawer is on every view and outside `#view`; clips group by where
their meta says they came from, and nothing a hand-edited meta holds
breaks it; the search answers the list only; each page's own action
("Use as take" on Voice, "Place" on Song, "Open in Lab" for a Lab clip
whose song is still there); delete says where a clip is used — the song's
undo ring included — and what then points at a missing clip plays
silence; the Lab's separated songs with their download links; and the
watch that re-sends it reads no file.
"""

from __future__ import annotations

import io
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import stems
from synthlab.audio import read_pcm16, wav_bytes
from synthlab.core import SR
from synthlab.studio import cliplib, limits, sources
from synthlab.studio import clips as clip_store
from synthlab.studio.clips import list_clips, save_clip
from synthlab.studio.server import create_app
from synthlab.studio.service import PAGE_REGIONS, REGIONS
from synthlab.studio.views import VIEW_NAMES
from synthlab.studio.voice import load_take
from tests.studio.test_song_routes import use_song

SID = "0123456789abcdef"  # a source id no store here holds


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


def _tone(seconds: float = 0.25, hz: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return np.column_stack([np.sin(2 * np.pi * hz * t)] * 2) * 0.4


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _clip(client, name: str, meta: dict | None = None, audio=None) -> None:
    audio = _tone() if audio is None else audio
    save_clip(_st(client).data_dir, name, audio, meta=meta)


def _source(data_dir: Path, name: str = "WOMAN IN LOVE", separated: bool = True) -> str:
    """A song in the Lab's source store, with one stem set when `separated`."""
    hz = 200 + sum(map(ord, name)) % 200  # the id is the audio's: one per name
    audio = _tone(1.0, hz)
    sid = sources.add(data_dir, wav_bytes(audio), f"{name}.wav")["id"]
    if separated:
        model = next(iter(stems.MODELS))
        parts = {s: audio for s in sources.stem_names(model)}
        sources.save_stems(data_dir, sid, model, parts, 1.0)
    return sid


def _drawer(html: str) -> str:
    """The drawer's own HTML in a page or a fragment."""
    start = html.index('id="clip-library"')
    return html[start : html.index("</aside>", start)]


def _groups(html: str) -> list[tuple[str, list[str]]]:
    """[(heading, [clip names])] in drawer order — the clip groups: the
    Loops at the drawer's top are looplib's (tests/studio/test_loops.py)."""
    out = []
    for part in html.split('<section class="cd-group')[1:]:
        if part.startswith(" cd-loops"):
            continue
        label = re.search(r'aria-label="([^"]*)"', part).group(1)
        out.append(
            (label, re.findall(r'<li class="cd-clip" data-name="([^"]*)"', part))
        )
    return out


def _fragment(client, view: str = "editor", **query) -> str:
    r = client.get("/fragment/clips", params=query, headers=_hx(view))
    assert r.status_code == 200, r.text[:300]
    return r.text


# --- where it is drawn --------------------------------------------------------------


def test_the_drawer_is_on_every_view_and_outside_the_view(client):
    """One region for every page, in base.html: the rail button with the
    count, and a declarative popover (no script opens it)."""
    _clip(client, "hit")
    assert REGIONS["clip-library"].pages == frozenset(VIEW_NAMES)
    assert all("clip-library" in names for names in PAGE_REGIONS.values())
    assert "clip-shelf" not in REGIONS
    for view in VIEW_NAMES:
        page = client.get(f"/view/{view}").text
        assert page.count('id="clip-library"') == 1, view
        at, main = page.index('id="clip-library"'), page.index('id="view"')
        assert at < main and page.index("</header>", at) < main, view  # in the top bar
        drawer = _drawer(page)
        assert 'popovertarget="clip-drawer"' in drawer and "popover " in drawer
        assert '<span class="clip-count">1</span>' in drawer
        assert 'data-name="hit"' in drawer and "/clip/hit.wav" in drawer
    assert 'id="clip-library"' in client.get("/").text


def test_a_view_swap_carries_the_drawer_for_the_page_it_lands_on(client):
    """`#view` is what a navigation swaps; the drawer is outside it and
    differs by page, so it rides along out of band — morphed, which is what
    keeps an open popover open."""
    _clip(client, "hit")
    r = client.get("/view/voice", headers=_hx("editor"))
    assert r.text.count('id="view"') == 1
    drawer = _drawer(r.text)
    assert (
        'hx-swap-oob="outerMorph"' in r.text[r.text.index('id="clip-library"') - 80 :]
    )
    assert 'hx-post="/voice/take/clip"' in drawer
    plain = _drawer(client.get("/view/drums", headers=_hx("voice")).text)
    assert "/voice/take/clip" not in plain and "cd-place-btn" not in plain


def test_every_redraw_of_the_drawer_morphs(client):
    """An `outerHTML` swap would close the popover and drop the scroll and
    the keyboard's place: the region and everything that targets it morph."""
    _clip(client, "hit")
    svc = client.app.state.svc
    tpl = Path(svc.templates.get_template("fragments/clip_list.html").filename)
    for line in tpl.read_text(encoding="utf-8").split("hx-target=")[1:]:
        if line.startswith('"#clip-library"'):
            assert 'hx-swap="outerMorph"' in line[:200]
    bar = Path(svc.templates.get_template("fragments/sound_bar.html").filename)
    assert 'hx-target="#clip-library" hx-swap="outerMorph"' in bar.read_text("utf-8")
    r = client.post("/clip/upload", data={"name": "jam"}, files=_upload())
    assert 'id="clip-library" hx-swap-oob="outerMorph"' in r.text


def _upload(frames: int = 4410, sr: int = 44100) -> dict:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x10\x00" * frames * 2)
    return {"file": ("jam.wav", buf.getvalue(), "audio/wav")}


# --- groups -------------------------------------------------------------------------


def test_clips_group_by_where_their_meta_says_they_came_from(client):
    lab = {"source": SID, "chop": "c1", "start": 1.0, "seconds": 1.0}
    old = {"source": SID, "start": 0.0, "seconds": 2.0, "stems": [], "fitted": False}
    for name, meta in [
        ("verse 1", {"source": "lab: WOMAN", "lab": {**lab, "stems": ["vocals"]}}),
        ("verse 2", {"source": "lab: WOMAN", "lab": {**lab, "stems": ["vocals"]}}),
        (
            "groove",
            {"source": "lab: WOMAN", "lab": {**lab, "stems": ["drums", "bass"]}},
        ),
        ("region", {"source": "lab: Another", "bpm": 120.0, "bars": 2, "lab": old}),
        ("WOMAN kick", {"source": "lab: WOMAN"}),  # a kit hit: the song, no `lab`
        ("kept", {"source": "voice take"}),
        ("take-20261005", {"source": "take"}),
        ("jam-1", {"source": "jam"}),
        ("bounce", {"source": "Deep Bass", "bpm": 124.0, "bars": 2}),
        (
            "jam bounce",
            {"source": "jam", "bpm": 124.0, "bars": 2},
        ),  # a sound called jam
        ("riser", {"source": "transition", "transition": {"kind": "riser"}}),
        ("stray", None),
    ]:
        _clip(client, name, meta)
    assert _groups(_fragment(client)) == [
        ("Another · mix", ["region"]),
        ("WOMAN", ["WOMAN kick"]),
        ("WOMAN · drums + bass", ["groove"]),
        ("WOMAN · vocals", ["verse 1", "verse 2"]),
        ("Voice takes", ["kept", "take-20261005"]),
        ("Jams", ["jam-1"]),
        ("Bounces", ["bounce", "jam bounce"]),
        ("Risers / impacts", ["riser"]),
        ("Other", ["stray"]),
    ]
    row = _fragment(client).split('data-name="bounce"')[1]
    assert "0.25s · 124 bpm · 2 bars" in row[:400]


@pytest.mark.parametrize(
    "meta",
    [
        '{"lab": "c1"}',
        '{"lab": [1, 2], "source": {"x": 1}, "transition": 7}',
        '{"source": "lab: X", "lab": {"source": 5, "chop": {"a": 1}, "stems": "v"}}',
        '{"source": "lab: X", "lab": {"source": "../../etc", "chop": "a b/c",'
        ' "stems": [1, [2], null]}}',
        '{"source": "lab: <b>x</b>\\"\'", "lab": {"stems": ["vocals", "<i>"]}}',
        '{"bpm": -5, "bars": 1e11, "seconds": "long"}',
        '{"bpm": 1e999, "bars": NaN}',
        '{"source": "' + "lab: " + "x" * 5000 + '"}',
        '["not", "an", "object"]',
        "{broken",
    ],
)
def test_a_meta_of_any_shape_draws_and_never_fails_a_page(tmp_path, meta):
    """A meta is a file anyone can edit (and a bundle can carry): a bad one
    was a 500 on two pages last month. Whatever it holds, the clip is in
    the drawer of every page, nothing it says is markup, and it can still
    be deleted."""
    save_clip(tmp_path, "evil", _tone())
    (tmp_path / "clips" / "evil.meta.json").write_text(meta, encoding="utf-8")
    with TestClient(create_app(data_dir=tmp_path)) as client:
        for view in VIEW_NAMES:
            r = client.get(f"/view/{view}")
            assert r.status_code == 200, (view, r.text[:300])
            drawer = _drawer(r.text)
            assert 'data-name="evil"' in drawer, view
            assert "<b>" not in drawer and "<i>" not in drawer
            assert "/view/lab?source=" not in drawer  # no such song: no link
            assert "-5 bpm" not in drawer and "inf" not in drawer
        (group,) = _groups(_fragment(client))
        assert group[1] == ["evil"]
        r = client.post("/fragment/clip/delete", data={"name": "evil"})
        assert r.status_code == 200 and list_clips(tmp_path) == []


def test_a_meta_of_the_wrong_shape_is_other():
    for meta in ({}, {"lab": "x"}, {"source": 5}, {"source": "lab:x"}, {"bpm": 1}):
        clean = clip_store.clean_meta(meta)
        assert cliplib.kind_of({**clean, "name": "c", "seconds": 1.0})[0] == "other"
    assert cliplib.lab_ref({"lab": {"source": "ZZ", "chop": "ok"}}) == ("", "ok")
    assert cliplib.lab_ref({"lab": {"source": SID, "chop": "a/b"}}) == (SID, "")


# --- search -------------------------------------------------------------------------


def test_search_answers_the_list_only_and_ignores_case(client):
    for name in ("Kick one", "kick two", "snare"):
        _clip(client, name, {"source": "jam"})
    html = _fragment(client, q="KICK")
    assert _groups(html) == [("Jams", ["Kick one", "kick two"])]
    # the box being typed in is not in the answer, nor is the drawer's root
    assert 'id="clip-q"' not in html and 'id="clip-library"' not in html
    assert _groups(_fragment(client, q="  ")) == [
        ("Jams", ["Kick one", "kick two", "snare"])
    ]
    none = _fragment(client, q="zebra")
    assert _groups(none) == [] and "No clip’s name contains “zebra”" in none
    # the box asks as it is typed in, debounced, and swaps the list alone
    drawer = _drawer(client.get("/view/editor").text)
    box = drawer[drawer.index('id="clip-q"') - 200 : drawer.index('id="clip-q"') + 500]
    assert 'hx-get="/fragment/clips"' in box and 'hx-target="#clip-list"' in box
    assert 'hx-trigger="input delay:200ms"' in box and 'name="q"' in box


def test_the_search_is_cut_to_its_cap_and_is_never_markup(client):
    _clip(client, "a" * 70)
    long = "a" * (cliplib.MAX_QUERY_CHARS + 40)
    assert _groups(_fragment(client, q=long)) == [
        ("Other", ["a" * 70])
    ]  # cut, so it matches
    html = _fragment(client, q='<script>alert(1)</script>"')
    assert "<script>" not in html and "&lt;script&gt;" in html
    whole = _fragment(client, q='x" onfocus="y', whole="1")
    assert 'value="x&#34; onfocus=&#34;y"' in whole


def test_a_redraw_from_inside_the_drawer_keeps_the_search(client):
    """Rename and delete answer with the drawer: they carry the box
    (`hx-include`), so the list that comes back is still the filtered one
    and the box still says what was typed."""
    for name in ("kick", "snare"):
        _clip(client, name, {"source": "jam"})
    drawer = _drawer(client.get("/view/editor").text)
    assert drawer.count('hx-include="#clip-q, #clip-place"') >= 5
    r = client.post(
        "/fragment/clip/rename",
        data={"name": "kick", "new": "kick 808", "q": "kick"},
        headers=_hx("editor"),
    )
    assert _groups(r.text) == [("Jams", ["kick 808"])] and 'value="kick"' in r.text
    r = client.post("/fragment/clip/delete", data={"name": "kick 808", "q": "sn"})
    assert _groups(r.text) == [("Jams", ["snare"])]


# --- per page: Voice ----------------------------------------------------------------


def test_use_as_take_is_offered_on_voice_only(client):
    _clip(client, "phrase")
    assert 'hx-post="/voice/take/clip"' in _fragment(client, "voice")
    for view in ("editor", "perform", "song", "lab", "drums"):
        assert "/voice/take/clip" not in _fragment(client, view), view


def test_use_as_take_fills_the_take_with_the_clip_summed_to_mono(client):
    left = np.sin(np.arange(SR // 2) * 2 * np.pi * 220 / SR) * 0.5
    stereo = np.column_stack([left, np.zeros_like(left)])
    _clip(client, "phrase", audio=stereo)
    r = client.post("/voice/take/clip", data={"name": "phrase"}, headers=_hx("voice"))
    assert r.status_code == 200, r.text[:300]
    assert 'id="voice-panel"' in r.text and "Take ready — 0.5s" in r.text
    assert "phrase" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    take = load_take(_st(client).data_dir)
    assert take.ndim == 1 and len(take) == SR // 2
    np.testing.assert_allclose(take, left / 2, atol=2e-4)  # the mean of the two
    # the clip is a copy's source, not moved: still in the store, still stereo
    kept, _sr = clip_store.load_clip(_st(client).data_dir, "phrase")
    assert kept.shape == stereo.shape
    assert client.post("/voice/render", data={"bands": "8"}).status_code == 200


def test_use_as_take_refuses_what_an_upload_refuses(client, monkeypatch):
    data_dir = _st(client).data_dir

    def post(name):
        return client.post(
            "/voice/take/clip", data={"name": name}, headers=_hx("voice")
        )

    for gone in ("nope", "", "../state", "..\\..\\x"):
        r = post(gone)
        assert r.status_code == 422 and "That clip is gone" in r.text, gone
    # a clip file that is not a WAV, and one at a sample rate no take may have
    _clip(client, "junk")
    (data_dir / "clips" / "junk.wav").write_bytes(b"RIFFnope")
    r = post("junk")
    assert r.status_code == 422 and "Not a readable wav file" in r.text
    _clip(client, "fast")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(limits.MAX_SR + 1)
        w.writeframes(b"\x00\x00" * 100)
    (data_dir / "clips" / "fast.wav").write_bytes(buf.getvalue())
    r = post("fast")
    assert r.status_code == 422 and "Unsupported sample rate" in r.text
    _clip(client, "big")
    monkeypatch.setattr(limits, "MAX_TAKE_UPLOAD_BYTES", 1000)
    r = post("big")
    assert r.status_code == 422 and "Take is too large" in r.text
    assert load_take(data_dir) is None  # none of them became the take


def test_a_long_clip_is_cut_like_a_long_take(client, monkeypatch):
    monkeypatch.setattr("synthlab.studio.voice.MAX_TAKE_SECONDS", 0.5)
    _clip(client, "long", audio=_tone(2.0))
    assert client.post("/voice/take/clip", data={"name": "long"}).status_code == 200
    assert len(load_take(_st(client).data_dir)) == SR // 2


# --- per page: Song -----------------------------------------------------------------


def _song_drawer(client, **query) -> str:
    return _fragment(client, "song", whole="1", **query)


def test_place_is_the_songs_clip_picker(client):
    """A "Place into" bar (section, bar, audio track) and a Place button on
    every clip, posting the route that was there with no UI."""
    use_song(client)
    _clip(client, "loop")
    html = _song_drawer(client)
    assert 'id="clip-place"' in html
    assert re.findall(r'<option value="(s\d)"[^>]*>(\w+)', html) == [
        ("s1", "Intro"),
        ("s2", "Verse"),
    ]
    assert 'hx-post="/song/section/s1/clip"' in html  # the first section, until chosen
    assert html.count('<option value="ta"') == 1 and "first audio track" in html
    bars = html.split('name="at_bar"')[1].split("</select>")[0]
    assert re.findall(r'value="(\d+)"[^>]*>bar (\d+)', bars) == [("0", "1"), ("1", "2")]
    # choosing a section asks for the drawer again: the button's path follows
    assert 'name="place_section"' in html and "/fragment/clips?whole=1" in html
    verse = _song_drawer(client, place_section="s2", at_bar="1", track_id="ta")
    assert 'hx-post="/song/section/s2/clip"' in verse
    assert '<option value="s2" selected>' in verse
    assert (
        '<option value="1" selected>' in verse
        and '<option value="ta" selected>' in verse
    )
    # no other page has the bar or the button
    for view in ("editor", "voice", "lab"):
        other = _fragment(client, view, whole="1")
        assert 'id="clip-place"' not in other and "cd-place-btn" not in other


def test_place_lands_a_take_and_ctrl_z_removes_it(client):
    use_song(client)
    _clip(client, "loop")
    before = json.dumps(_st(client).song)
    # what the button posts: its own `clip`, and the bar's fields beside it
    data = {
        "clip": "loop",
        "place_section": "s2",
        "at_bar": "1",
        "track_id": "ta",
        "q": "",
    }
    r = client.post("/song/section/s2/clip", data=data, headers=_hx("song"))
    assert r.status_code == 200, r.text[:300]
    take = {"track_id": "ta", "clip": "loop", "at_bar": 1, "gain": 1.0}
    assert _st(client).song["sections"][1]["audio"] == [take]
    assert (
        "Verse, bar 2" in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    # the drawer comes back (delete now says the clip is used), out of band
    # and still on the section, bar and track that were chosen
    drawer = _drawer(r.text)
    assert 'id="clip-library" hx-swap-oob="outerMorph"' in r.text
    assert (
        '<option value="s2" selected>' in drawer
        and '<option value="1" selected>' in drawer
    )
    assert "1 take in the song" in drawer
    assert _st(client).song_undo_state()["undo"] == "place clip"
    assert client.post("/song/undo", headers=_hx("song")).status_code == 200
    assert json.dumps(_st(client).song) == before


def test_a_song_with_no_section_says_so_and_offers_no_place(client):
    use_song(client)
    s = _st(client)
    s.song = {**s.song, "sections": []}
    _clip(client, "loop")
    html = _song_drawer(client)
    assert "Add a section to the song" in html and "cd-place-btn" not in html


def test_a_hostile_place_choice_falls_back_to_the_first_section(client):
    use_song(client)
    _clip(client, "loop")
    html = _song_drawer(client, place_section='"><x', at_bar="-9", track_id="t1")
    assert 'hx-post="/song/section/s1/clip"' in html and "<x" not in html
    assert '<option value="0" selected>' in html  # the bar is clamped into the section
    assert '<option value="t1"' not in html  # t1 is a synth track
    assert '<option value="99"' not in _song_drawer(
        client, place_section="s1", at_bar="99"
    )


# --- per page: Open in Lab ------------------------------------------------------------


def test_open_in_lab_links_a_lab_clip_to_its_song_and_chop(client):
    data_dir = _st(client).data_dir
    sid = _source(data_dir, separated=False)
    lab = {"source": sid, "start": 0.0, "seconds": 1.0, "stems": ["vocals"]}
    _clip(
        client, "chop", {"source": "lab: WOMAN IN LOVE", "lab": {**lab, "chop": "c1a2"}}
    )
    _clip(
        client, "region", {"source": "lab: WOMAN IN LOVE", "lab": lab}
    )  # today's shape
    _clip(client, "orphan", {"source": "lab: Gone", "lab": {**lab, "source": SID}})
    _clip(client, "jam-1", {"source": "jam"})
    _clip(client, "kick", {"source": "lab: WOMAN IN LOVE"})  # a kit hit: no source
    for view in ("editor", "song", "voice", "lab"):
        html = _fragment(client, view)
        assert f'href="/view/lab?source={sid}&amp;chop=c1a2"' in html, view
        assert f'href="/view/lab?source={sid}"' in html  # no chop: the song alone
        orphan = html.split('data-name="orphan"')[1].split("</li>")[0]
        assert 'aria-disabled="true"' in orphan and "href=" not in orphan
        for name in ("jam-1", "kick"):
            row = html.split(f'data-name="{name}"')[1].split("</li>")[0]
            assert "Open in Lab" not in row, name
    # the song is deleted: the clip stays (it is a copy), its link goes
    sources.delete(data_dir, sid)
    html = _fragment(client)
    assert "/view/lab?source=" not in html and html.count('aria-disabled="true"') == 3


# --- the Stems group ------------------------------------------------------------------


def test_the_stems_group_lists_each_separated_song_with_its_downloads(client):
    data_dir = _st(client).data_dir
    assert "cd-stems" not in _fragment(client)
    plain = _source(data_dir, "not separated", separated=False)
    sid = _source(data_dir, "WOMAN IN LOVE")
    model = next(iter(stems.MODELS))
    html = _fragment(client)
    group = html.split('data-group="stems"')[1]
    assert f'data-source="{sid}"' in group and plain not in group
    assert "WOMAN IN LOVE" in group and f'href="/view/lab?source={sid}"' in group
    for stem in sources.stem_names(model):
        assert f'href="/lab/{sid}/stem/{stem}.flac?model={model}"' in group
    assert f'href="/lab/{sid}/stems.zip?model={model}"' in group
    assert "audition" not in group  # a stem is minutes long: the Lab plays it
    # a second stem set of the same song is listed too, the current one first
    other = [m for m in stems.MODELS if m != model][0]
    audio = _tone(1.0)
    sources.save_stems(
        data_dir, sid, other, {s: audio for s in sources.stem_names(other)}, 1.0
    )
    group = _fragment(client).split('data-group="stems"')[1]
    assert group.index(f"stems.zip?model={other}") < group.index(
        f"stems.zip?model={model}"
    )
    assert f"/stem/{sources.stem_names(other)[-1]}.flac?model={other}" in group
    # a half-written set (its temp name) and a set missing a stem are not sets
    d = sources.source_dir(data_dir, sid)
    (d / "stems-x.tmp-123").mkdir()
    (d / f"stems-{model}" / f"{sources.stem_names(model)[0]}.flac").unlink()
    group = _fragment(client).split('data-group="stems"')[1]
    assert ".tmp" not in group and f"model={model}" not in group
    assert f"model={other}" in group


# --- delete says where a clip is used -----------------------------------------------


def _confirm(client, name: str, view: str = "editor") -> str | None:
    row = _fragment(client, view).split(f'data-name="{name}"')[1].split("</li>")[0]
    m = re.search(r'hx-confirm="([^"]*)"', row)
    return m.group(1) if m else None


def test_an_unused_clip_deletes_as_before_and_a_used_one_says_where(client):
    use_song(client)
    s = _st(client)
    for name in ("free", "loop", "hit"):
        _clip(client, name)
    assert _confirm(client, "free") is None
    client.post("/song/section/s1/clip", data={"clip": "loop"}, headers=_hx("song"))
    client.post("/song/section/s2/clip", data={"clip": "loop"}, headers=_hx("song"))
    s.arrangement.drum_samples = {"kick": "hit"}
    s.arrangement.clips = [{"clip": "hit", "at_bar": 0, "gain": 1.0}]
    said = _confirm(client, "loop")
    assert "“loop” is in use" in said and "2 takes in the song" in said
    # each place was one song edit: both steps' "before" hold fewer takes,
    # and the second's holds the first
    assert "1 undo step of the song" in said
    hit = _confirm(client, "hit")
    assert "1 place on the clip lane" in hit and "1 drum row" in hit
    assert _confirm(client, "free") is None
    assert s.clip_usage()["loop"] == {"song": 2, "undo": 1}
    # deleting is still allowed: the button posts, the confirm is the browser's
    r = client.post("/fragment/clip/delete", data={"name": "loop"}, headers=_hx("song"))
    assert r.status_code == 200 and "loop" not in [
        c["name"] for c in list_clips(s.data_dir)
    ]


def test_delete_counts_the_songs_undo_and_redo_steps(client):
    """Amendment 4: Ctrl+Z must not be how a reference to a deleted file
    comes back unannounced. A clip no longer in the song but still in its
    undo ring says so."""
    use_song(client)
    s = _st(client)
    _clip(client, "loop")
    assert s.song_undo_clips() == {}
    client.post("/song/section/s1/clip", data={"clip": "loop"}, headers=_hx("song"))
    assert s.song_undo_clips() == {}  # the step holds the song BEFORE the take
    client.post("/song/undo", headers=_hx("song"))
    assert s.clip_usage() == {"loop": {"undo": 1}}  # the redo step holds it
    said = _confirm(client, "loop", "song")
    assert "1 undo step of the song (Ctrl+Z would bring it back, silent)" in said
    assert "in the song," not in said
    client.post("/song/redo", headers=_hx("song"))
    client.post("/song/section/s2/rename", data={"name": "V"}, headers=_hx("song"))
    client.post("/song/section/s2/rename", data={"name": "W"}, headers=_hx("song"))
    assert s.clip_usage()["loop"] == {"song": 1, "undo": 2}
    assert "2 undo steps of the song" in _confirm(client, "loop", "song")
    # read only: asking moved nothing on the ring
    assert s.song_undo_state()["depth"] == 3
    # a song replaced behind the ring: its steps lead nowhere, and count for nothing
    use_song(client)
    assert s.song_undo_clips() == {}


def test_a_step_that_points_at_a_deleted_clip_renders_silence_for_it(client):
    """Deleting stays allowed after the warning, so a redo (or an undo) can
    bring back a take whose file is gone: the section renders without it,
    as it does for a missing sample — never a 500."""
    use_song(client)
    s = _st(client)
    _clip(client, "loop", audio=_tone(1.0))
    without = client.get("/song/section/s1.wav")
    assert without.status_code == 200
    client.post("/song/section/s1/clip", data={"clip": "loop"}, headers=_hx("song"))
    with_take = client.get("/song/section/s1.wav")
    assert with_take.content != without.content
    client.post("/song/undo", headers=_hx("song"))
    r = client.post("/fragment/clip/delete", data={"name": "loop"}, headers=_hx("song"))
    assert r.status_code == 200
    assert client.post("/song/redo", headers=_hx("song")).status_code == 200
    assert s.song["sections"][0]["audio"][0]["clip"] == "loop"  # the reference is back
    gone = client.get("/song/section/s1.wav")
    assert gone.status_code == 200 and gone.content != with_take.content
    # a section holding a take renders whole (no pattern cycle to tile):
    # without the take's audio it is the pattern's cycle, twice
    cycle, _sr = read_pcm16(without.content)
    whole, _sr = read_pcm16(gone.content)
    assert len(whole) == 2 * len(cycle)
    np.testing.assert_allclose(whole, np.tile(cycle, (2, 1)), atol=1e-3)
    for view in ("song", "perform"):  # and every page that draws the song still does
        assert client.get(f"/view/{view}").status_code == 200
    assert client.get("/song/plan.json").status_code == 200


# --- the watch ----------------------------------------------------------------------


def test_the_watch_reads_no_clip_and_no_source(client, monkeypatch):
    """It runs around every state-changing request on every page: listings
    and the session only. The metas are read once per store listing (which
    Lab songs the clips name), never again while it stands."""
    data_dir = _st(client).data_dir
    sid = _source(data_dir)
    _clip(client, "chop", {"source": "lab: W", "lab": {"source": sid}})
    svc = client.app.state.svc
    watch = REGIONS["clip-library"].watch
    with svc.state.lock:
        first = watch(svc, "song")
    opened: list = []
    monkeypatch.setattr(cliplib, "list_clips", lambda *a: opened.append("clips") or [])
    monkeypatch.setattr(sources, "read_meta", lambda *a: opened.append("meta"))
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **k: opened.append("read") or "{}"
    )
    monkeypatch.setattr(wave, "open", lambda *a, **k: opened.append("wave"))
    with svc.state.lock:
        for view in VIEW_NAMES:
            watch(svc, view)
        assert watch(svc, "song") == first
    assert opened == []


def test_the_watch_moves_with_everything_the_drawer_draws(client):
    use_song(client)
    data_dir = _st(client).data_dir
    svc = client.app.state.svc

    def watch(view="song"):
        with svc.state.lock:
            return svc.watch_page(view)["clip-library"]

    seen = [watch()]

    def moved(why: str) -> None:
        seen.append(watch())
        assert seen[-1] != seen[-2], why

    def still(why: str) -> None:
        assert watch() == seen[-1], why

    assert watch("editor") != watch("voice") != watch("song")  # the page's own action
    _clip(client, "loop")
    moved("a clip was added")
    sid = _source(data_dir, separated=False)
    still("an imported song with no stems and no clip is not drawn")
    _clip(client, "chop", {"source": "lab: W", "lab": {"source": sid}})
    moved("a clip of that song")
    model = next(iter(stems.MODELS))
    audio = _tone(1.0)
    sources.save_stems(
        data_dir, sid, model, {s: audio for s in sources.stem_names(model)}, 1.0
    )
    moved("the song was separated")
    sources.update_meta(data_dir, sid, lambda m: m.update(opened=1.0))
    still("a meta rewritten (opened, a region, a level) draws nothing new")
    client.post("/song/section/s1/clip", data={"clip": "loop"}, headers=_hx("song"))
    moved("the clip is used now")
    client.post("/song/section/s1/rename", data={"name": "Top"}, headers=_hx("song"))
    moved("the Place bar names the sections")
    sources.delete(data_dir, sid)
    moved("the song is gone: its stems, and the clip's link")


# --- what the shelf did, the drawer still does --------------------------------------


def test_a_bounce_lands_in_the_drawer_and_says_the_name_it_kept(client):
    r = client.post("/clip/bounce", data={"name": "bounce"}, headers=_hx("editor"))
    assert r.headers["x-clip-name"] == "bounce"
    # a bounce always says where it went (never silent)
    first = json.loads(r.headers["HX-Trigger"])["studio-toast"]
    assert first["message"].startswith("Bounced “bounce”") and first["kind"] == "done"
    assert r.text.lstrip().startswith('<div class="clip-library" id="clip-library"')
    assert _groups(r.text) == [("Bounces", ["bounce"])]
    again = client.post("/clip/bounce", data={"name": "bounce"}, headers=_hx("editor"))
    assert again.headers["x-clip-name"] == "bounce 2"
    said = json.loads(again.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "“bounce” is taken — kept as “bounce 2”" in said
    assert _groups(again.text) == [("Bounces", ["bounce", "bounce 2"])]
    assert '<span class="clip-count">2</span>' in again.text


def test_a_jam_and_a_kept_take_come_back_out_of_band(client):
    """Neither posts from inside the drawer (audio.js's fetch, the Voice
    panel's button): the library is re-sent by its watch."""
    r = client.post("/clip/upload", data={"name": "jam-1"}, files=_upload())
    assert r.headers["x-clip-name"] == "jam-1"
    assert _groups(r.text) == [("Jams", ["jam-1"])] and "hx-swap-oob" in r.text
    client.post("/voice/take", files={"file": ("t.wav", _mono(), "audio/wav")})
    r = client.post("/voice/keep", data={"name": "phrase"}, headers=_hx("voice"))
    assert r.headers["x-clip-name"] == "phrase"
    # the button swaps nothing: an answer that WAS the drawer would be dropped
    assert r.text.lstrip().startswith(
        '<div class="clip-library" id="clip-library" hx-swap-oob'
    )
    assert _groups(r.text) == [("Voice takes", ["phrase"]), ("Jams", ["jam-1"])]
    assert 'hx-post="/voice/take/clip"' in r.text  # drawn for the page that asked


def _mono(frames: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.sin(np.arange(frames) * 0.05) * 8000).astype("<i2").tobytes())
    return buf.getvalue()


def test_a_rename_follows_the_clip_everywhere_the_undo_ring_included(client):
    use_song(client)
    s = _st(client)
    _clip(client, "loop")
    client.post("/song/section/s1/clip", data={"clip": "loop"}, headers=_hx("song"))
    client.post("/song/section/s1/rename", data={"name": "Top"}, headers=_hx("song"))
    r = client.post(
        "/fragment/clip/rename",
        data={"name": "loop", "new": "hook"},
        headers=_hx("song"),
    )
    assert r.status_code == 200 and 'data-name="hook"' in r.text
    assert s.song["sections"][0]["audio"][0]["clip"] == "hook"
    assert s.clip_usage() == {"hook": {"song": 1, "undo": 1}}  # the ring follows
    client.post("/song/undo", headers=_hx("song"))
    assert s.song["sections"][0]["audio"][0]["clip"] == "hook"
    taken = client.post("/fragment/clip/rename", data={"name": "hook", "new": " "})
    assert taken.status_code == 422 and "A clip needs a name" in taken.text


def test_every_clip_offers_the_sampler_on_every_page(client):
    _clip(client, "tone", audio=_tone(0.5))
    for view in ("editor", "perform", "song", "voice"):
        row = _fragment(client, view).split('data-name="tone"')[1].split("</li>")[0]
        assert 'hx-post="/fragment/sampler/clip"' in row and "⤷ sampler" in row
    r = client.post(
        "/fragment/sampler/clip", data={"name": "tone"}, headers=_hx("song")
    )
    assert r.status_code == 200 and _st(client).patch.osc1 == "sampler"
    assert 'data-view="editor"' in r.text and r.headers["HX-Push-Url"] == "/view/editor"


def test_thirty_clips_stay_light(client):
    """No waveform, no image, nothing rendered per clip: a row is a line of
    text and its buttons."""
    for i in range(30):
        _clip(client, f"jam-{i:02d}", {"source": "jam"}, audio=_tone(0.05))
    html = _fragment(client, "song", whole="1")
    assert html.count('<li class="cd-clip"') == 30
    assert "<svg" not in html.split('id="clip-list"')[1] and "<canvas" not in html
    assert "<audio" not in html and "<img" not in html
    assert len(html.encode()) < 30 * 2600  # measured: ~2.0 KB a row, uncompressed
