"""The beat flow: pick the beat on Synths and play over it,
switch Perform's rack between the grids, take the loop to the Song.

- Beat ▾ (`beatpick.py`, `POST /fragment/beat/pick`: each select posts
  itself, the sound bar comes back by its watch): a variation, a bank
  slot, a new beat in a style or none — the loop's OWN beat
  (`arr.drums`), the one Perform and Drums show; a kit under it; "over
  the beat" turned on; the phrase render's key moves.
- ▶ beat (`GET /beat.wav`): the Drums view's render, by redirect.
- The phrase's cue names the loop's length over the beat.
- Perform's Notes | Drums | Both switch (client-only: its markup here, its
  behaviour in tests/browser/test_beat_flow.py); the part buttons mute.
- → Song (`POST /song/from-loop` from Perform / Synths): a section, the
  beat's other variations in the pool, a note with the way to Song, and
  the kit offer (`POST /song/drums/loop-kit`).

Nobody has listened to any of it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import beatgen, beatpick, schema, song_bridge
from synthlab.studio.drumfactory import DRUM_KITS
from synthlab.studio.server import create_app
from tests.studio.test_song_routes import use_song


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


def _st(client):
    return client.app.state.studio


def _svc(client):
    return client.app.state.svc


def _pick(client, **data):
    return client.post("/fragment/beat/pick", data=data, headers=_hx("editor"))


def _style_option(client, style: str) -> str:
    page = client.get("/view/editor").text
    return re.search(rf'value="(style:{style}:\d+)"', page).group(1)


# --- Beat ▾ --------------------------------------------------------------------


def test_the_picker_lists_variations_bank_styles_and_none(client):
    page = client.get("/view/editor").text
    sel = re.search(r'<select[^>]*name="beat_pick".*?</select>', page, re.S).group(0)
    assert '<option value="" selected>' in sel  # the beat as it is
    assert 'value="bank:0"' in sel  # the factory bank
    for st in beatgen.STYLES:
        assert f'value="style:{st}:' in sel
    assert 'value="none"' in sel
    # a new beat by family and display name, never a slug as its text
    groups = re.findall(r'<optgroup label="new: ([^"]+)">(.*?)</optgroup>', sel, re.S)
    assert [g for g, _ in groups] == [
        f.lower().replace("&", "&amp;") for f, _ in beatgen.style_groups()
    ]
    assert ">Filter house break<" in sel and ">new: " not in sel
    named = [st for _, sts in beatgen.style_groups() for st in sts]
    assert sorted(named) == sorted(beatgen.STYLES)  # every style once
    assert "the Loops page and Kit" in sel  # it IS the loop's beat, and says so
    kit = re.search(r'<select[^>]*name="beat_kit".*?</select>', page, re.S).group(0)
    for k in DRUM_KITS:
        assert f'value="{k}"' in kit
    # each posts itself, the other blanked, and keeps its input from the
    # sound bar's own post (which would set "over the beat" back)
    for one, other in ((sel, "beat_kit"), (kit, "beat_pick")):
        assert 'hx-post="/fragment/beat/pick"' in one
        assert 'hx-trigger="input consume"' in one
        assert f'hx-vals=\'{{"{other}": ""}}\'' in one


def test_the_sound_bars_own_post_reads_no_pick(client):
    s = _st(client)
    r = client.post(
        "/fragment/arrangement",
        data={"phrase": "riff", "beat_pick": "bank:0", "beat_kit": "Trance"},
        headers=_hx("editor"),
    )
    assert r.status_code == 200 and not s.arrangement.drums and not s.vs_beat
    # a select left on "" has nothing to do
    r = _pick(client, beat_pick="", beat_kit="")
    assert r.status_code == 200 and not s.arrangement.drums


def test_a_new_beat_is_the_loops_beat_and_the_phrase_plays_over_it(client):
    s, svc = _st(client), _svc(client)
    assert not s.arrangement.drums and not s.vs_beat
    before = svc.render_inputs("editor").key()
    r = _pick(client, beat_pick=_style_option(client, "techno"))
    assert r.status_code == 200
    assert s.arrangement.drums and s.vs_beat and s.beat_style == "techno"
    inp = svc.render_inputs("editor")
    assert "drums" in inp.parts and inp.arr.drums == s.arrangement.drums
    assert inp.key() != before
    # the bar comes back (its select on "" again: the next post moves nothing)
    assert 'id="sound-bar"' in r.text and "preview-ready" in r.headers["HX-Trigger"]
    # the same pick posted twice writes the same beat (the seed is in the value)
    drums = s.arrangement.drums
    _pick(client, beat_pick=f"style:techno:{s.beat_seed}")
    assert s.arrangement.drums == drums
    # Perform's rack shows it: it is the same lane
    assert "drum.kick." in client.get("/view/perform").text


def test_a_variation_a_bank_slot_and_none(client):
    s = _st(client)
    assert _pick(client, beat_pick="bank:2").status_code == 200  # Techno
    assert s.active_slot == 2 and s.arrangement.drums and s.vs_beat
    techno = s.arrangement.drums
    client.post("/fragment/drums/var", data={"var": "B"}, headers=_hx("perform"))
    client.post(
        "/fragment/gen", data={"kind": "busier", "seed": "3"}, headers=_hx("perform")
    )
    assert s.beat_var == "B" and s.arrangement.drums != techno
    page = client.get("/view/editor").text
    assert 'value="var:A"' in page and 'value="var:B"' in page
    _pick(client, beat_pick="var:A")
    assert s.beat_var == "A" and s.arrangement.drums == techno
    # none: the phrase alone; the beat stays the loop's
    _pick(client, beat_pick="none")
    assert not s.vs_beat and s.arrangement.drums == techno
    assert "drums" not in _svc(client).render_inputs("editor").parts


@pytest.mark.parametrize(
    "pick", ["var:Z", "var:C", "bank:x", "bank:99", "style:polka:1", "x"]
)
def test_a_bad_pick_changes_nothing(client, pick):
    s = _st(client)
    _pick(client, beat_pick="bank:0")
    was = asdict(s.arrangement)
    r = _pick(client, beat_pick=pick)
    assert r.status_code == 422
    assert asdict(s.arrangement) == was


def test_a_kit_under_the_beat_keeps_the_beat_and_moves_the_key(client):
    s, svc = _st(client), _svc(client)
    _pick(client, beat_pick="bank:0")
    beat, key = s.arrangement.drums, svc.render_inputs("editor").key()
    r = _pick(client, beat_kit="Trance")
    assert r.status_code == 200 and beatpick.kit_of(s) == "Trance"
    assert s.arrangement.drums == beat
    assert s.arrangement.drum_params["kick"] == {
        **s.arrangement.drum_params["kick"],
        **DRUM_KITS["Trance"]["params"]["kick"],
    }
    assert svc.render_inputs("editor").key() != key
    assert _pick(client, beat_kit="Nope").status_code == 422


def test_a_pick_is_one_pattern_undo_step(client):
    s = _st(client)
    _pick(client, beat_pick=_style_option(client, "breaks"))
    assert s.arrangement.drums
    client.post("/fragment/pattern/undo", headers=_hx("perform"))
    assert not s.arrangement.drums


# --- ▶ beat and the cues ----------------------------------------------------------


def test_play_beat_is_the_drums_render(client):
    assert client.get("/beat.wav", follow_redirects=False).status_code == 404
    _pick(client, beat_pick="bank:0")
    r = client.get("/beat.wav", follow_redirects=False)
    assert r.status_code == 303
    key = _svc(client).render("drums").key
    assert r.headers["location"] == f"/preview/{key}.wav"
    wav = client.get(r.headers["location"])
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert _svc(client).render_inputs("drums").parts == ("drums",)


def _beat_key(html: str) -> str:
    return re.search(r'id="play-beat"[^>]*data-beat-key="([^"]*)"', html).group(1)


def test_play_beat_carries_the_beats_key_and_only_a_new_beat_moves_it(client):
    """▶ beat refetches only when the beat it would hear moved: the sound
    bar draws the Drums render's key on #play-beat (`svc.beat_key`) and its
    watch holds it, so a beat pick sends the bar back with a new key while
    a sound edit (every preview-ready used to refetch /beat.wav) leaves it."""
    assert _beat_key(client.get("/view/editor").text) == ""  # no beat yet
    r = _pick(client, beat_pick="bank:0")
    key = _svc(client).render("drums").key
    assert _beat_key(r.text) == key  # the bar came back, out of band, with it
    assert _beat_key(client.get("/view/editor").text) == key
    loc = client.get("/beat.wav", follow_redirects=False).headers["location"]
    assert loc == f"/preview/{key}.wav"
    r = client.post("/fragment/patch", data={"cutoff": "900"}, headers=_hx("editor"))
    assert r.status_code == 200 and 'id="sound-bar"' not in r.text
    assert _svc(client).beat_key() == key
    _pick(client, beat_pick=_style_option(client, "techno"))
    assert _svc(client).beat_key() not in (None, key)


def test_the_cues_name_the_beat_and_the_loops_length(client):
    s = _st(client)
    _pick(client, beat_pick="bank:0")
    client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hx("drums"))
    assert s.arrangement.drum_bars == 2
    page = client.get("/view/editor").text
    label = beatpick.label(s)
    assert label == "the beat · House · 2 bars"
    assert f'data-plays="{label}"' in page  # ▶ beat
    assert 'data-bars="2"' in page  # transport.js reads the phrase's length off it
    cue = re.search(r'data-cue-for="play">([^<]*)<', page).group(1)
    assert cue.endswith("· 2 bars + the beat")


# --- "beat only": the phrase off, ▶ plays the beat alone ---------------------------


def _phrase(client, phrase: str):
    s = _st(client)
    data = {"phrase": phrase, "root": s.arrangement.root}
    data |= {"scale_name": s.arrangement.scale_name, "vs_beat": "on"}
    return client.post("/fragment/arrangement", data=data, headers=_hx("editor"))


def test_beat_only_is_offered_once_there_is_a_beat(client):
    """The beat can play with no phrase: a pill beside the phrases,
    disabled while there is no beat."""
    pill = r'<input type="radio" name="phrase" value="none"[^>]*>'
    assert " disabled" in re.search(pill, client.get("/view/editor").text).group(0)
    _pick(client, beat_pick="bank:0")
    page = client.get("/view/editor").text
    assert " disabled" not in re.search(pill, page).group(0)
    assert ">beat only<" in page


def test_beat_only_plays_the_beat_render_and_a_sound_still_hears_the_phrase(client):
    s, svc = _st(client), _svc(client)
    _pick(client, beat_pick="bank:0")
    phrase_key = svc.render_inputs("editor").key()
    assert _phrase(client, "none").status_code == 200
    assert s.audition == "none"
    # ▶ on Synths IS ▶ beat's render: one cache entry, the drums alone
    assert svc.render_inputs("editor").key() == svc.render_inputs("drums").key()
    assert svc.render_inputs("editor").parts == ("drums",)
    assert svc.render_inputs("editor").key() != phrase_key
    page = client.get("/view/editor").text
    cue = re.search(r'data-cue-for="play">([^<]*)<', page).group(1)
    assert cue == beatpick.label(s)  # "the beat · House · 1 bar"
    assert re.search(r'value="none"\s+checked', page)
    # a sound's audition, and the other bench views, still play the phrase
    from synthlab.synth import Patch

    assert "synth" in svc.render_inputs("editor", patch=Patch(), mix=True).parts
    assert svc.render_inputs("library").arr.cells
    # it persists like a phrase
    assert schema.nodes().PAYLOAD.fields["audition"].conform("none") == "none"
    # Beat ▾'s none (the phrase on its own) brings the phrase back
    _pick(client, beat_pick="none")
    assert s.audition == "hold" and not s.vs_beat
    assert svc.render_inputs("editor").key() != svc.render_inputs("drums").key()


# --- Perform: the rack's focus, the part buttons ------------------------------------


def test_perform_has_the_focus_switch_and_mute_buttons(client):
    page = client.get("/view/perform").text
    for v in ("notes", "drums", "both"):
        assert f'data-rack-show="{v}"' in page
    assert 'data-keys="Alt+G"' in page
    assert "Mute / unmute the synth part of the loop" in page
    assert "Press to mute it" in page


# --- → Song -----------------------------------------------------------------------


def test_to_song_from_perform_carries_every_variation(client):
    s = _st(client)
    _pick(client, beat_pick="bank:2")
    client.post("/fragment/drums/var", data={"var": "B"}, headers=_hx("perform"))
    client.post(
        "/fragment/gen", data={"kind": "busier", "seed": "3"}, headers=_hx("perform")
    )
    client.post("/fragment/drums/var", data={"var": "C"}, headers=_hx("perform"))
    client.post(
        "/fragment/gen", data={"kind": "sparser", "seed": "4"}, headers=_hx("perform")
    )
    lanes = {"A": s.beat_vars["A"]["drums"], "B": s.beat_vars["B"]["drums"]}
    r = client.post("/song/from-loop", headers=_hx("perform"))
    assert r.status_code == 200
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert (
        "Loop 1" in toast
        and "beat C" in toast
        and "A and B are in the song's pool" in toast
    )
    assert 'href="/view/song"' in r.text and "Loop 1" in r.text
    pool = s.song["patterns"]
    names = {p["name"]: p for p in pool.values() if p["kind"] == "drums"}
    assert {"Loop 1 beat A", "Loop 1 beat B", "Loop 1 beat C"} <= set(names)
    for letter, drums in lanes.items():
        assert sorted(map(tuple, names[f"Loop 1 beat {letter}"]["drums"])) == sorted(
            map(tuple, drums)
        )
    sec = s.song["sections"][-1]
    drums_tid = next(t["id"] for t in s.song["tracks"] if t["kind"] == "drums")
    assert pool[sec["cells"][drums_tid]]["name"] == "Loop 1 beat C"


def test_to_song_from_the_song_page_answers_as_before(client):
    r = client.post("/song/from-loop", data={"name": "Jam"}, headers=_hx("song"))
    assert r.status_code == 200 and "tsn-said" not in r.text
    assert "studio-toast" not in json.loads(r.headers["HX-Trigger"])


def test_a_loop_with_no_beat_puts_no_beat_in_the_song(client):
    """→ Song from a loop with no hits: no empty "Loop 1 beat" in the
    section or the pool (Perform's launcher offered it, and it launched
    silence), and the note says so."""
    s = _st(client)
    assert not s.arrangement.drums
    r = client.post("/song/from-loop", headers=_hx("editor"))
    assert r.status_code == 200
    assert not [p for p in s.song["patterns"].values() if p["kind"] == "drums"]
    sec = s.song["sections"][-1]
    drums = {t["id"] for t in s.song["tracks"] if t["kind"] == "drums"}
    assert not drums & set(sec["cells"])
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert "has no beat" in toast and "has no beat" in r.text


def test_a_loop_with_one_beat_pools_nothing_more(client):
    s = _st(client)
    _pick(client, beat_pick="bank:0")
    client.post("/song/from-loop", headers=_hx("perform"))
    drums = [p for p in s.song["patterns"].values() if p["kind"] == "drums"]
    assert [p["name"] for p in drums] == ["Loop 1 beat"]


def test_the_kit_offer(client):
    s = _st(client)
    use_song(client)
    # the same kit: no offer
    track = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    arr = schema.conform(asdict(s.arrangement), schema.nodes().ARRANGEMENT)
    for k in song_bridge.KIT:
        track[k] = arr[k]
    r = client.post("/song/from-loop", headers=_hx("perform"))
    assert "/song/drums/loop-kit" not in r.text
    assert (
        client.post("/song/drums/loop-kit", headers=_hx("perform")).status_code == 422
    )
    # another kit on the loop: the section plays through the song's, offered
    client.post("/fragment/drums/kit", data={"kit": "Trance"}, headers=_hx("drums"))
    r = client.post("/song/from-loop", headers=_hx("perform"))
    assert "/song/drums/loop-kit" in r.text and "Use Trance on" in r.text
    assert (
        "plays through the song's"
        in json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    )
    r = client.post("/song/drums/loop-kit", headers=_hx("perform"))
    assert r.status_code == 200 and "plays Trance now" in r.text
    track = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    assert track["drum_params"]["kick"] == s.arrangement.drum_params["kick"]
    # one song undo step
    client.post("/song/undo", headers=_hx("song"))
    track = next(t for t in s.song["tracks"] if t["kind"] == "drums")
    assert track["drum_params"].get("kick") != s.arrangement.drum_params["kick"]
