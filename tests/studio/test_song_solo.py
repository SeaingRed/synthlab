"""The drawer's "solo this cell" (studio docs/song.md, "Solo this cell").

`GET /song/cell/{section}/{track}.wav` is one loop of the section with only
that track playing. It is no second render path: the song with every other
track muted (`song_play.solo_song`, the stems export's idea) goes through
the same `section_result` a section's loop does, at the mix's gain and
without the master chain. Pinned: the solos of a section add up to its mix,
the mix's mute and solo flags don't silence an audition, the section's own
WAV is untouched by it, and the page offers it for a filled cell only.
"""

from __future__ import annotations

import numpy as np

from synthlab.audio import read_pcm16
from synthlab.studio import song_play
from tests.studio import test_song_routes as sr
from tests.studio.test_song_routes import post, song

client = sr.client  # Intro (s1): Beat + Riff; Verse (s2): Beat + Riff + Hook


def _audio(r) -> np.ndarray:
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "audio/wav"
    audio, _sr = read_pcm16(r.content)
    return np.asarray(audio, dtype=float)


def solo(client, sid: str, tid: str):
    return client.get(f"/song/cell/{sid}/{tid}.wav")


def loop(client, sid: str):
    return client.get(f"/song/section/{sid}.wav?loop=1")


def test_solo_song_leaves_one_track_playing_whatever_the_mix_says():
    base = sr.song_dict()
    base["tracks"][1]["mute"] = True  # Bass is muted in the mix
    base["tracks"][2]["solo"] = True  # and Lead is soloed
    out = song_play.solo_song(base, "t1")
    assert [(t["id"], t["mute"], t["solo"]) for t in out["tracks"]] == [
        ("td", True, False),
        ("t1", False, False),
        ("t2", True, False),
        ("ta", True, False),
    ]
    # a copy: the song itself is not touched, and nothing else moved
    assert base["tracks"][1]["mute"] is True and base["tracks"][2]["solo"] is True
    assert out["sections"] == base["sections"] and out["patterns"] == base["patterns"]


def test_the_solos_of_a_section_add_up_to_its_loop(client):
    """Each is the stems render of one track at the mix's gain, so with no
    master chain their sum is the section's own loop (to 16-bit rounding)."""
    mix = _audio(loop(client, "s2"))
    parts = [_audio(solo(client, "s2", tid)) for tid in ("td", "t1", "t2")]
    assert all(p.shape == mix.shape for p in parts)
    assert all(np.max(np.abs(p)) > 0.01 for p in parts)
    assert np.max(np.abs(sum(parts) - mix)) < 4 / 32768


def test_a_solo_is_that_track_and_no_other(client):
    bass = _audio(solo(client, "s1", "t1"))
    drums = _audio(solo(client, "s1", "td"))
    mix = _audio(loop(client, "s1"))
    assert not np.allclose(bass, drums) and not np.allclose(bass, mix)
    # emptying the drums' pattern changes the mix and the drums' solo, not
    # the bass on its own
    post(client, "/song/pattern/pd/tool/clear")
    assert np.max(np.abs(_audio(solo(client, "s1", "td")))) == 0.0
    again = _audio(solo(client, "s1", "t1"))
    # the same notes; only the song-wide gain may have moved with the mix
    scale = np.max(np.abs(again)) / np.max(np.abs(bass))
    assert np.allclose(again, bass * scale, atol=3 / 32768)


def test_the_mixs_mute_and_solo_do_not_silence_an_audition(client):
    plain = _audio(solo(client, "s1", "t1"))
    post(client, "/song/track/t1/mix", {"mute": "on"})
    post(client, "/song/track/td/mix", {"solo": "on"})
    muted = _audio(solo(client, "s1", "t1"))
    assert np.max(np.abs(muted)) > 0.01
    scale = np.max(np.abs(muted)) / np.max(np.abs(plain))
    assert np.allclose(muted, plain * scale, atol=3 / 32768)


def test_an_audition_leaves_the_sections_own_render_alone(client):
    """Its cache key is the solo song's own (`section_key` of the parts
    with the other tracks muted): it neither stands in for the section's
    normal WAV nor moves it, and it changes no state."""
    before = sr.plan_urls(client)
    head, lp = client.get(before["s2"][0]).content, client.get(before["s2"][1]).content
    was = song(client)
    one = solo(client, "s2", "t2").content
    assert one not in (head, lp)
    assert sr.plan_urls(client) == before
    assert client.get(before["s2"][0]).content == head
    assert client.get(before["s2"][1]).content == lp
    assert song(client) is was  # no edit, no undo step
    assert client.app.state.studio.song_undo_state()["depth"] == 0
    assert solo(client, "s2", "t2").content == one  # deterministic (and cached)


def test_the_master_chain_is_not_in_an_audition(client):
    """A solo is for hearing the track's own sound and FX. The master chain
    still sets the song-wide gain (the level the cell has in the mix), so
    compare shapes, not levels."""
    dry = _audio(solo(client, "s1", "t1"))
    client.app.state.studio.fx_master = [
        {"kind": "reverb", "params": {"mix": 0.6, "size": 0.9}}
    ]
    wet = _audio(solo(client, "s1", "t1"))
    scale = np.max(np.abs(wet)) / np.max(np.abs(dry))
    assert np.allclose(wet, dry * scale, atol=3 / 32768)
    mix = _audio(loop(client, "s1"))
    assert mix.shape == wet.shape


def test_unknown_and_empty_cells_have_no_audition(client):
    assert solo(client, "nope", "t1").status_code == 404
    assert solo(client, "s1", "nope").status_code == 404
    assert solo(client, "s1", "t2").status_code == 404  # an empty cell
    assert solo(client, "s1", "ta").status_code == 404  # an audio track's


def _drawer(client) -> str:
    html = client.get("/view/song", headers=sr.HX).text
    return html[html.index('id="song-drawer"') :]


def test_the_drawer_offers_the_audition_for_a_filled_cell_and_a_pool_pattern(client):
    post(client, "/song/drawer", {"section_id": "s2", "track_id": "t2"})
    d = _drawer(client)
    assert 'data-cell-solo="/song/cell/s2/t2.wav"' in d
    assert 'data-cycle-bars="2"' in d  # Hook is 2 bars: the loop is 2
    assert 'aria-pressed="false"' in d[d.index("drawer-solo") :][:200]
    # a real <button>: reachable and pressed from the keyboard
    # (the view's ▶, prominent: `.btn-play`, "▶ Play")
    assert (
        '<button type="button" class="btn btn-play btn-toggle menu-btn drawer-solo"'
        in d
    )
    post(client, "/song/drawer", {"section_id": "s1", "track_id": "t2"})  # empty
    assert "data-cell-solo" not in _drawer(client)
    # a pool pattern plays too, through a lane: its own route, not a cell's
    post(client, "/song/drawer", {"pattern_id": "p3"})
    d = _drawer(client)
    assert 'data-cell-solo="/song/pattern/p3/play.wav?track=' in d
    assert "/song/cell/" not in d
