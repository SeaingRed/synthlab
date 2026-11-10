"""The mixer's render (studio docs/song-render-export.md,
"The mixer"): a track's pan and sends and the return buses, as
`render._cycle_stems` applies them to the finished stems.

Pinned here: absent means absent (a song with no pan and no send keys and
renders as it did, and a pan of 0 or a send of 0 is absent); the pan law;
a send's level; a return through a linear chain against the same chain
worked by hand, and the superposition of two senders; that the delay
return adds echoes and no dry level; that a looped section's fold is exact
through a return; the cache; the levels a render records; stems that sum
to the mix."""

from __future__ import annotations

import json

import numpy as np
import pytest

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio import mixer, render, schema
from synthlab.studio import song_model as sm
from synthlab.studio.fxrack import build_chain, clean_chain
from synthlab.studio.render import (
    StemCache,
    fold_tail,
    section_audio,
    section_key,
    section_master_audio,
)
from tests.studio.test_song_automation import _only, _parts, _rms, _sec, _song

N = schema.nodes()
RID = "0a0b0c0d"
ECHO = [
    {
        "kind": "delay",
        "params": {"time": 0.25, "feedback": 0.5, "mix": 1.0, "ping_pong": 0.0},
    }
]
ROOM = [{"kind": "reverb", "params": {"decay": 0.6, "mix": 1.0, "size": 0.5}}]


def _track(s, tid) -> dict:
    return s["tracks"][sm.track_index(s, tid)]


def _bus(s, fx_units, rid=RID, gain=1.0) -> str:
    s.setdefault("returns", []).append(
        {"id": rid, "name": "Bus", "fx": clean_chain(fx_units), "gain": gain}
    )
    return rid


def _clean(s) -> dict:
    return schema.conform(json.loads(json.dumps(s)), N.SONG)


def _stems(s, sid) -> dict:
    """The section's cycle stems by name, as `_cycle_stems` hands them on."""
    parts = _parts(s, sid)
    n = sm.cycle_samples(parts.cycle_bars, parts.arr.bpm)
    return {
        name: (stem, ring)
        for name, stem, ring in render._cycle_stems(parts, n, None, None)
    }


# --- absent means absent ----------------------------------------------------------


def test_a_song_without_pan_or_sends_keys_and_renders_as_it_did():
    s, sid, ids = _song()
    before = _parts(s, sid)
    key = section_key(before, None)
    audio = section_audio(before)
    # the unset values, written out: the schema drops them, the key and
    # the samples do not move
    for t in s["tracks"]:
        t["pan"] = 0.0
        t["sends"] = {}
    _bus(s, ROOM)  # a return nothing feeds
    _track(s, ids["lead"])["sends"] = {RID: 0.0}
    s = _clean(s)
    assert all("pan" not in t and "sends" not in t for t in s["tracks"])
    after = _parts(s, sid)
    assert after.mix == {} and after.returns == ()
    assert section_key(after, None) == key
    again = section_audio(after)
    assert np.array_equal(again.base, audio.base)
    assert np.array_equal(again.tail, audio.tail)


def test_a_send_names_a_return_the_song_has_and_a_muted_track_sends_nothing():
    s, sid, ids = _song()
    _track(s, ids["lead"])["sends"] = {"deadbeef": 0.5}
    assert "sends" not in _track(_clean(s), ids["lead"])  # no such return
    _bus(s, ROOM)
    _track(s, ids["lead"])["sends"] = {RID: 0.5}
    s = _clean(s)
    assert _parts(s, sid).mix == {"main": {"sends": {RID: 0.5}}}
    _track(s, ids["lead"])["mute"] = True
    parts = _parts(s, sid)
    assert parts.mix == {} and parts.returns == ()
    # ... nor to a return whose fader is down
    _track(s, ids["lead"])["mute"] = False
    s["returns"][0]["gain"] = 0.0
    assert _parts(s, sid).returns == ()


def test_pan_and_a_send_change_the_key_and_only_then():
    s, sid, ids = _song()
    key = section_key(_parts(s, sid), None)
    _track(s, ids["pad"])["pan"] = 0.25
    panned = section_key(_parts(s, sid), None)
    assert panned != key
    _track(s, ids["pad"])["pan"] = -0.25
    assert section_key(_parts(s, sid), None) not in (key, panned)
    _track(s, ids["pad"])["pan"] = 0.0
    assert section_key(_parts(s, sid), None) == key
    _bus(s, ROOM)
    assert section_key(_parts(s, sid), None) == key  # an unfed return
    _track(s, ids["pad"])["sends"] = {RID: 0.3}
    sent = section_key(_parts(s, sid), None)
    assert sent != key
    s["returns"][0]["fx"][0]["params"]["decay"] = 1.2
    assert section_key(_parts(s, sid), None) not in (key, sent)


# --- pan --------------------------------------------------------------------------


@pytest.mark.parametrize("pos", [-1.0, -0.5, 0.3, 1.0])
def test_the_pan_law_is_equal_power_and_keeps_the_image(pos):
    s, sid, ids = _song()
    _only(s, ids, "lead")
    dry, _ = _stems(s, sid)["main"]
    _track(s, ids["lead"])["pan"] = pos
    wet, _ = _stems(s, sid)["main"]
    angle = (pos + 1) * np.pi / 4
    left, right = np.cos(angle) * np.sqrt(2), np.sin(angle) * np.sqrt(2)
    assert np.allclose(wet[:, 0], dry[:, 0] * left, atol=1e-12)
    assert np.allclose(wet[:, 1], dry[:, 1] * right, atol=1e-12)
    # equal power: the two gains' squares sum to 2 (1 a side at centre)
    assert left**2 + right**2 == pytest.approx(2.0)


def test_only_the_panned_track_moves():
    s, sid, ids = _song()
    before = _stems(s, sid)
    _track(s, ids["pad"])["pan"] = -1.0
    after = _stems(s, sid)
    for name in ("main", "drums"):
        assert np.array_equal(after[name][0], before[name][0])
    assert _rms(after["layer2"][0][:, 1]) == 0.0  # hard left
    assert _rms(after["layer2"][0][:, 0]) > 0.0


def test_the_drums_pan_too():
    s, sid, ids = _song()
    _only(s, ids, "drums")
    _track(s, ids["drums"])["pan"] = 1.0
    stem, _ = _stems(s, sid)["drums"]
    # cos(pi / 2) is 6e-17 in floating point, not 0
    assert _rms(stem[:, 0]) < 1e-12 < _rms(stem[:, 1])


# --- sends and returns ------------------------------------------------------------


def test_a_return_is_its_chain_on_the_senders_stem_times_the_send():
    """A delay is linear: the return's stem is the chain worked by hand
    on the sender's stem, to 1e-9 — and it carries no copy of the dry
    signal (the delay's direct term is taken out)."""
    s, sid, ids = _song()
    _only(s, ids, "lead")
    _bus(s, ECHO)
    _track(s, ids["lead"])["sends"] = {RID: 0.4}
    stems = _stems(s, sid)
    dry, _ = stems["main"]
    got, _ = stems[f"return:{RID}"]
    arr = _parts(s, sid).arr
    n = sm.cycle_samples(_parts(s, sid).cycle_bars, 120.0)
    x = np.zeros((2 * n, 2))
    x[: len(dry)] = 0.4 * dry
    full = fx.chain(x, build_chain(ECHO, arr))
    direct = fx._delay(x, time=0.25, feedback=0.0, mix=1.0, ping_pong=False)
    want = full - direct
    assert np.max(np.abs(got - want[: len(got)])) < 1e-9
    assert len(got) == 2 * n  # the echoes ring one cycle past the end, no more
    # an echo, not the note: nothing sounds before the delay time
    d = int(0.25 * SR)
    first = int(np.flatnonzero(np.any(dry != 0.0, axis=1))[0])
    assert np.max(np.abs(got[: first + d - 2])) < 1e-9
    assert _rms(got) > 0.01


def test_a_ping_pong_return_starts_on_the_right_and_holds_no_dry_left():
    s, sid, ids = _song()
    _only(s, ids, "lead")
    unit = {
        "kind": "delay",
        "params": {"time": 0.25, "feedback": 0.3, "mix": 1.0, "ping_pong": 1.0},
    }
    _bus(s, [unit])
    _track(s, ids["lead"])["sends"] = {RID: 1.0}
    stems = _stems(s, sid)
    dry, _ = stems["main"]
    got, _ = stems[f"return:{RID}"]
    first = int(np.flatnonzero(np.any(dry != 0.0, axis=1))[0])
    d = int(0.25 * SR)
    assert np.max(np.abs(got[: first + d - 2])) < 1e-9  # no dry on either side
    # the first echo is the right line's (one delay late), the left's two
    assert np.max(np.abs(got[first + d : first + 2 * d - 2, 1])) > 1e-4
    assert np.max(np.abs(got[: first + 2 * d - 2, 0])) < 1e-9


def test_two_senders_superpose_and_the_send_scales_the_return():
    s, sid, ids = _song()
    _only(s, ids, "lead", "pad")
    _bus(s, ROOM)

    def ret(lead, pad):
        _track(s, ids["lead"])["sends"] = {RID: lead}
        _track(s, ids["pad"])["sends"] = {RID: pad}
        stem, _ = _stems(_clean(s), sid)[f"return:{RID}"]
        n = sm.cycle_samples(_parts(s, sid).cycle_bars, 120.0)
        return render._pad_trim(stem, 2 * n)

    both, lead, pad = ret(0.5, 0.25), ret(0.5, 0.0), ret(0.0, 0.25)
    assert np.max(np.abs(both - (lead + pad))) < 1e-9
    assert np.max(np.abs(ret(1.0, 0.0) - 2 * lead)) < 1e-9


def test_a_return_reads_the_stem_past_its_fader_and_pan():
    s, sid, ids = _song()
    _only(s, ids, "lead")
    _bus(s, ECHO)
    _track(s, ids["lead"])["sends"] = {RID: 0.5}
    centre, _ = _stems(s, sid)[f"return:{RID}"]
    _track(s, ids["lead"])["pan"] = -1.0
    left, _ = _stems(s, sid)[f"return:{RID}"]
    assert _rms(left[:, 1]) < 1e-12 < _rms(left[:, 0])
    assert np.allclose(left[:, 0], centre[:, 0] * np.sqrt(2), atol=1e-9)
    # the return's own fader
    s["returns"][0]["gain"] = 0.5
    half, _ = _stems(s, sid)[f"return:{RID}"]
    assert np.allclose(half, left * 0.5, atol=1e-12)


def test_a_return_chain_is_rendered_fully_wet_whatever_its_mix_says():
    half = [{"kind": "reverb", "params": {"decay": 0.6, "mix": 0.5, "size": 0.5}}]
    assert mixer.wet_chain(half) == clean_chain(ROOM)
    s, sid, ids = _song()
    _bus(s, half)
    _track(s, ids["lead"])["sends"] = {RID: 0.5}
    assert _parts(s, sid).returns[0]["fx"][0]["params"]["mix"] == 1.0


def test_a_looped_section_folds_its_return_exactly():
    """One pass with its own tail folded in == the middle pass of the same
    material played three times in one section (the return is linear and
    runs once over the whole of it there)."""
    s, sid, ids = _song(bars=2)  # the Pad's 2-bar clock sets the cycle
    _only(s, ids, "lead")
    _bus(s, ROOM)
    _track(s, ids["lead"])["sends"] = {RID: 0.6}
    one = section_audio(_parts(s, sid))
    n = len(one.base)
    assert n == sm.cycle_samples(2, 120.0)
    looped = fold_tail(one.base, one.tail)
    long = json.loads(json.dumps(s))
    long["sections"][0]["bars"] = 6
    # a lane on a muted track makes the cycle the whole section
    sm.set_lane(long, sid, ids["pad"], "cutoff", [[0.0, 0.0], [1.0, 1.0]])
    three = section_audio(_parts(long, sid))
    assert len(three.base) == 3 * n
    assert np.max(np.abs(three.base[n : 2 * n] - looped)) < 1e-9


def _native() -> tuple[dict, str, dict]:
    """A section that is NOT flat: a 1-bar riff and a 1-bar beat, twice."""
    s = sm.new_song(120.0, "A1", "minor")
    ids = {
        "lead": sm.add_track(s, "synth", "Lead"),
        "drums": sm.add_track(s, "drums", "Drums"),
    }
    sid = sm.add_section(s, "Loop", 2)
    riff = {
        "cells": [["A2", st] for st in range(0, 16, 2)],
        "bars": 1,
        "steps_per_bar": 16,
    }
    beat = {"drums": [["kick", 0], ["snare", 2]], "bars": 1, "steps": 4}
    sm.set_cell(s, sid, ids["lead"], sm.add_pattern(s, "notes", "riff", riff))
    sm.set_cell(s, sid, ids["drums"], sm.add_pattern(s, "drums", "beat", beat))
    return s, sid, ids


def test_a_section_with_a_send_need_not_be_flat_and_matches_the_flat_render():
    s, sid, ids = _native()
    parts = _parts(s, sid)
    assert not parts.flat and render._own_stems(parts)
    plain = section_audio(parts)
    _bus(s, ROOM)
    _track(s, ids["lead"])["sends"] = {RID: 0.5}
    sent = _parts(s, sid)
    # the renderer flattens it, not the model
    assert not sent.flat and sent.mix and not render._own_stems(sent)
    with_return = section_audio(sent)
    wet, _ = _stems(s, sid)[f"return:{RID}"]
    n = len(plain.base)
    # the dry tracks are what they were (their own stems, tiled)
    diff = with_return.base - plain.base - wet[:n]
    assert np.max(np.abs(diff)) < 1e-9


def test_the_return_is_cached_and_a_send_re_renders_no_track():
    s, sid, ids = _song()
    _bus(s, ROOM)
    _track(s, ids["pad"])["sends"] = {RID: 0.5}
    cache = StemCache()
    first = section_audio(_parts(s, sid), stem_cache=cache)
    kinds = [k.split(":")[0] for k in cache._items]  # noqa: SLF001
    assert kinds.count("return") == 1
    held = len(cache._items)  # noqa: SLF001
    again = section_audio(_parts(s, sid), stem_cache=cache)
    assert len(cache._items) == held  # noqa: SLF001
    assert np.array_equal(again.base, first.base)
    # another send level, another pan: one new return each, no new track stem
    _track(s, ids["pad"])["sends"] = {RID: 0.8}
    section_audio(_parts(s, sid), stem_cache=cache)
    _track(s, ids["pad"])["pan"] = 0.5
    section_audio(_parts(s, sid), stem_cache=cache)
    kinds = [k.split(":")[0] for k in cache._items]  # noqa: SLF001
    assert kinds.count("return") == 3 and len(kinds) == held + 2


def test_a_sender_under_an_fx_lane_gives_its_return_two_rings():
    s, sid, ids = _song()
    _only(s, ids, "lead")
    unit = {"kind": "lowpass", "params": {"cutoff": 800.0, "q": 0.7}, "id": "a1b2c3d4"}
    _track(s, ids["lead"])["fx"] = clean_chain([unit])
    sm.set_lane(
        s, sid, ids["lead"], "fx:a1b2c3d4:lowpass:cutoff", [[0.0, 0.1], [1.0, 1.0]]
    )
    _bus(s, ROOM)
    _track(s, ids["lead"])["sends"] = {RID: 0.5}
    stem, ring = _stems(s, sid)[f"return:{RID}"]
    n = sm.cycle_samples(_parts(s, sid).cycle_bars, 120.0)
    assert ring is not None and len(ring) == len(stem) - n
    audio = section_audio(_parts(s, sid))
    assert audio.out is not None and np.all(np.isfinite(audio.out))


# --- levels -----------------------------------------------------------------------


def test_a_render_records_each_stems_level_and_whose_it_is():
    s, sid, ids = _song()
    parts = _parts(s, sid)
    assert parts.tracks == {
        "main": [ids["lead"]],
        "layer2": [ids["pad"]],
        "drums": [ids["drums"]],
    }
    audio = section_audio(parts)
    assert set(audio.levels) == {"main", "layer2", "drums"}
    for peak, rms in audio.levels.values():
        assert 0 < rms < peak
    # the same numbers on the cycle path, and the return's beside them
    _bus(s, ROOM)
    _track(s, ids["pad"])["sends"] = {RID: 0.5}
    sent = section_audio(_parts(s, sid))
    assert set(sent.levels) == {"main", "layer2", "drums", f"return:{RID}"}
    assert sent.levels["drums"][1] == pytest.approx(audio.levels["drums"][1], rel=1e-3)
    # the master chain keeps them (they are the stems', before it)
    comp = [{"kind": "compress", "params": {}}]
    assert section_master_audio(_parts(s, sid), comp).levels == sent.levels
    # a level is the track's: half the gain, half the level (a dry track)
    quiet = json.loads(json.dumps(s))
    _only(quiet, ids, "drums")
    full = section_audio(_parts(quiet, sid)).levels["drums"]
    _track(quiet, ids["drums"])["gain"] = 0.5
    half = section_audio(_parts(quiet, sid)).levels["drums"]
    assert half[1] == pytest.approx(full[1] / 2, rel=1e-6)


# --- the model's helpers ----------------------------------------------------------


def test_the_first_send_makes_the_default_returns_up_to_its_slot():
    s, _sid, ids = _song()
    said, key = mixer.set_strip(s, ids["lead"], {"send.1": "0.4"})
    assert (said, key) == ("send B", f"send1:{ids['lead']}")
    names = [r["name"] for r in s["returns"]]
    assert names == ["A Reverb", "B Delay"]
    assert _track(s, ids["lead"])["sends"] == {s["returns"][1]["id"]: 0.4}
    assert schema.conform_strict(s, N.SONG) == s
    # a send of 0 on a song without returns makes none
    t, _sid, tids = _song()
    mixer.set_strip(t, tids["lead"], {"send.0": "0"})
    assert "returns" not in _clean(t)
    with pytest.raises(sm.SongError):
        mixer.add_return(s)  # two is the cap


def test_removing_a_return_drops_every_send_to_it():
    s, _sid, ids = _song()
    mixer.set_strip(s, ids["lead"], {"send.0": "0.4"})
    mixer.set_strip(s, ids["pad"], {"send.0": "0.2"})
    rid = s["returns"][0]["id"]
    mixer.remove_return(s, rid)
    s = _clean(s)
    assert "returns" not in s
    assert all("sends" not in t for t in s["tracks"])


def test_duck_and_eq_are_the_tracks_own_units_added_on_first_touch():
    s, _sid, ids = _song(bpm=138.0)
    lead = _track(s, ids["lead"])
    mixer.set_strip(s, ids["lead"], {"duck": "0", "eq.low": "0"})
    assert lead["fx"] == []  # nothing to add for a control left at rest
    mixer.set_strip(s, ids["lead"], {"duck": "0.8"})
    assert lead["fx"] == [
        {"kind": "sidechain", "params": {"depth": 0.8, "release": 0.18}}
    ]
    assert mixer.duck_release(122.0) == 0.28
    mixer.set_strip(s, ids["lead"], {"eq.low": "-3", "eq.high": "40"})
    eq = lead["fx"][1]
    assert eq["kind"] == "eq" and eq["params"]["low"] == -3.0
    assert eq["params"]["high"] == 12.0  # clamped to the unit's range
    assert mixer.eq_of(lead) == {"low": -3.0, "mid": 0.0, "high": 12.0}
    mixer.set_strip(s, ids["lead"], {"duck": "0"})  # the unit stays, at 0
    assert mixer.duck_of(lead) == 0.0 and len(lead["fx"]) == 2
    with pytest.raises(sm.SongError, match="synth"):
        mixer.set_strip(s, ids["drums"], {"duck": "0.5"})
    with pytest.raises(sm.SongError, match="audio"):
        mixer.set_strip(s, ids["vox"], {"pan": "0.5"})
    lead["fx"] = clean_chain([{"kind": "drive", "params": {}}] * 6)
    with pytest.raises(sm.SongError, match="full"):
        mixer.set_strip(s, ids["lead"], {"duck": "0.5"})


def test_balance_puts_each_role_at_its_offset_under_the_drums():
    s, sid, ids = _song()
    _track(s, ids["lead"])["role"] = "lead"
    _track(s, ids["pad"])["role"] = "pad"
    levels = {
        sid: {ids["drums"]: (0.9, 0.2), ids["lead"]: (0.5, 0.1), ids["pad"]: (0.5, 0.2)}
    }
    moved = mixer.balance(s, levels)
    assert [m[0] for m in moved] == ["Lead", "Pad"]
    # lead: 0.1 -> 0.2 x 10^(-3/20) = 0.1416; pad: 0.2 -> 0.2 x 10^(-9/20)
    assert _track(s, ids["lead"])["gain"] == pytest.approx(1.416, abs=1e-3)
    assert _track(s, ids["pad"])["gain"] == pytest.approx(0.355, abs=1e-3)
    assert mixer.balance(s, {}) == []
    # a track without a role is left alone, and so are the drums
    assert _track(s, ids["drums"])["gain"] == 1.0


def test_balance_lands_on_its_targets_in_a_render():
    s, sid, ids = _song()
    _track(s, ids["lead"])["role"] = "lead"
    _track(s, ids["pad"])["role"] = "pad"

    def levels():
        parts = _parts(s, sid)
        got = section_audio(parts).levels
        return {sid: {parts.tracks[name][0]: lv for name, lv in got.items()}}

    mixer.balance(s, levels())
    now = levels()[sid]
    drums = now[ids["drums"]][1]
    assert mixer.db(now[ids["lead"]][1] / drums) == pytest.approx(-3.0, abs=0.1)
    assert mixer.db(now[ids["pad"]][1] / drums) == pytest.approx(-9.0, abs=0.1)


def test_the_meter_scale():
    assert mixer.meter_pct(0.0) == 0.0
    assert mixer.meter_pct(1.0) == 100.0
    assert mixer.meter_pct(10 ** (-24 / 20)) == 50.0
    assert mixer.meter_pct(2.0) == 100.0


def test_the_view_context_draws_every_track_and_return():
    s, sid, ids = _song()
    ctx = mixer.context(s)
    assert [x["name"] for x in ctx["strips"]] == ["Lead", "Pad", "Drums", "Vox"]
    assert ctx["returns"] == [] and ctx["can_add_return"] and not ctx["metered"]
    assert [x["chained"] for x in ctx["strips"]] == [True, True, True, False]
    mixer.set_strip(s, ids["pad"], {"send.0": "0.5", "pan": "-0.3"})
    rid = s["returns"][0]["id"]
    ctx = mixer.context(s, {sid: {ids["pad"]: (0.5, 0.25), rid: (0.1, 0.05)}})
    pad = ctx["strips"][1]
    assert pad["sends"] == [0.5, 0.0] and pad["pan"] == -0.3
    assert pad["level"] == mixer.meter_pct(0.25) and ctx["metered"]
    assert [r["slot"] for r in ctx["returns"]] == ["A"]
    assert ctx["returns"][0]["feeds"] == 1 and ctx["returns"][0]["level"] > 0
    assert [p[0] for p in ctx["returns"][0]["params"]] == ["decay", "size"]
    assert ctx["section"] == sid


def test_sec_helper_is_the_songs_section():  # the helpers this file leans on
    s, sid, _ids = _song()
    assert _sec(s, sid)["id"] == sid
