"""The stem cache's keys carry everything a stem's render reads.

Each case renders twice through one StemCache with a single input changed
between the two, and holds the second (possibly cache-served) render to a
fresh render of the same inputs with no cache at all. A key that misses an
input serves the first render's stem for the second: stale audio.
"""

from dataclasses import replace

import numpy as np

from synthlab.studio.render import RenderInputs, StemCache, render_audio
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch

SIDECHAIN = [{"kind": "sidechain", "params": {"depth": 1.0, "release": 0.3}}]
# held notes, so ducking has a sustained signal to act on
PAD = Patch(name="pad", attack=0.01, release=0.1)
LAYER = {
    "patch": Patch(name="lay", osc1="saw", release=0.05).to_json(),
    "cells": [["A2", 0], ["C3", 4], ["E3", 8], ["A2", 12]],
    "muted": False,
    "gain": 1.0,
}
# 16 steps: the layer's notes sit on steps 4-12, and a note past the grid
# never plays (arrange._playing_steps)
ARR = Arrangement(
    steps_per_bar=16,
    cells=[["A2", s] for s in range(16)],
    drums=[["kick", 0], ["kick", 8], ["hat", 4]],
)


def _cached_then_fresh(first: RenderInputs, second: RenderInputs):
    cache = StemCache()
    render_audio(first, stem_cache=cache)
    return render_audio(second, stem_cache=cache), render_audio(second)


def test_a_layer_stem_is_keyed_on_the_velocity_lane():
    """`arrange._row_events` reads `arr.velocities` for every layer, so a
    velocity edit must re-render the layer stems, not serve the old ones."""
    inp = RenderInputs(patch=PAD, arr=ARR, layers=[LAYER], main_muted=True)
    loud = replace(inp, arr=replace(ARR, velocities={"0": 0.2, "4": 0.2, "8": 1.0}))
    got, want = _cached_then_fresh(inp, loud)
    np.testing.assert_array_equal(got, want)


def test_a_layer_stem_is_keyed_on_its_own_sequencing():
    """A layer plays its own velocities, slides and arp (the render keys
    `song_bridge.lane_render` puts on its dict, `arrange.layer_arrangement`
    reads): changing any of them must re-render its stem."""
    base = {**LAYER, "velocities": {"0": 0.3, "4": 1.0}}
    inp = RenderInputs(patch=PAD, arr=ARR, layers=[base], main_muted=True)
    for change in (
        {"velocities": {"0": 1.0, "4": 0.3}},
        {"slides": [4, 8]},
        {"arp_mode": "up", "arp_octaves": 1},
        {"arp_mode": "up", "arp_octaves": 3},
        {"arp_mode": "down", "arp_octaves": 1, "arp": [["A2", "E3"], 2, 8]},
    ):
        edited = replace(inp, layers=[{**base, **change}])
        assert edited.key() != inp.key()
        got, want = _cached_then_fresh(inp, edited)
        np.testing.assert_array_equal(got, want)
        assert not np.allclose(want, render_audio(inp))


def test_a_sidechained_stem_is_keyed_on_the_kicks():
    """A Sidechain in a lane's FX ducks on the drum machine's kicks
    (`fxrack._kick_times`): moving a kick must re-render the lane."""
    inp = RenderInputs(patch=PAD, arr=ARR, main_fx=SIDECHAIN, parts=("synth", "drums"))
    moved = replace(
        inp, arr=replace(ARR, drums=[["kick", 4], ["kick", 12], ["hat", 4]])
    )
    got, want = _cached_then_fresh(inp, moved)
    np.testing.assert_array_equal(got, want)
    lay = {**LAYER, "fx": SIDECHAIN}
    inp = RenderInputs(
        patch=PAD, arr=ARR, layers=[lay], main_muted=True, parts=("synth", "drums")
    )
    got, want = _cached_then_fresh(inp, replace(inp, arr=moved.arr))
    np.testing.assert_array_equal(got, want)


def test_a_sidechained_stem_is_keyed_on_the_drum_clock():
    inp = RenderInputs(patch=PAD, arr=ARR, main_fx=SIDECHAIN, parts=("synth", "drums"))
    slower = replace(inp, arr=replace(ARR, drum_steps=8))
    got, want = _cached_then_fresh(inp, slower)
    np.testing.assert_array_equal(got, want)


def test_sidechain_does_nothing_without_drums_in_the_parts():
    """`RenderInputs.key()` drops the drums when they aren't among the
    parts; the render must too, or two loops with one key sound different
    (a synth-only render ducked on a kit it wasn't playing)."""
    for fx in ({"main_fx": SIDECHAIN}, {"master_fx": SIDECHAIN}):
        with_kit = RenderInputs(patch=PAD, arr=ARR, parts=("synth",), **fx)
        no_kit = replace(with_kit, arr=replace(ARR, drums=[]))
        assert with_kit.key() == no_kit.key()
        np.testing.assert_array_equal(render_audio(with_kit), render_audio(no_kit))
        # ... and with the drums playing it does duck (the test means something)
        both = replace(with_kit, parts=("synth", "drums"))
        dry = replace(both, main_fx=(), master_fx=())
        assert not np.allclose(render_audio(both), render_audio(dry))


def test_a_sidechain_ignores_a_kick_hidden_past_the_drum_grid():
    """The drum machine drops a hit past its own grid (kept, hidden, after
    a shrink: `arrange._schedule_drums`), so a sidechain must not duck on
    it — it used to, at a step where no kick plays — and the stem key reads
    the same kicks the chain does."""
    from synthlab.studio.render import _chain_clock

    shown = replace(ARR, drums=[["kick", 0], ["hat", 4]])
    hidden = replace(shown, drums=[*shown.drums, ["kick", 24]])  # a 1 x 16 grid
    assert _chain_clock(SIDECHAIN, hidden) == _chain_clock(SIDECHAIN, shown)
    for fx in ({"main_fx": SIDECHAIN}, {"master_fx": SIDECHAIN}):
        a = RenderInputs(patch=PAD, arr=shown, parts=("synth", "drums"), **fx)
        b = replace(a, arr=hidden)
        np.testing.assert_array_equal(render_audio(b), render_audio(a))
    # a kick inside the grid still ducks, and still moves the key
    inside = replace(shown, drums=[*shown.drums, ["kick", 8]])
    assert _chain_clock(SIDECHAIN, inside) != _chain_clock(SIDECHAIN, shown)


def _main_stem(inp: RenderInputs):
    """(the main lane's stem cache payload, its rendered stem)."""
    from synthlab.studio.render import _stem_jobs, render_stems

    (job,) = [j for j in _stem_jobs(inp) if j.name == "main"]
    return job.cache_payload(), dict(render_stems(inp))["main"]


def test_a_muted_or_soloed_out_kick_does_not_duck_the_sidechain():
    """The sidechain ducked on every kick in the grid, so a muted kick row —
    or one silenced by another row's solo — still pumped the synth under a
    kick nobody heard. It follows the rows the drum machine plays
    (`state.drum_row_sounds`, `arrange.drum_hits`' own rule), and the stem
    key reads the same kicks the chain does."""
    from synthlab.studio.arrange import drum_hits
    from synthlab.studio.fxrack import kick_steps
    from synthlab.studio.render import _chain_clock

    base = replace(ARR, drums=[*ARR.drums, ["snare", 4]])
    no_kick = [d for d in base.drums if d[0] != "kick"]
    silent = {
        "muted": {"kick": {"mute": True}},
        "soloed out": {"snare": {"solo": True}},
        "muted and soloed": {"kick": {"mute": True, "solo": True}},
        "never rolls": {"kick": {"prob": 0.0}},
    }
    sounding = {
        "soloed itself": {"kick": {"solo": True}},
        "another row muted": {"hat": {"mute": True}},
        "a level": {"kick": {"level": 0.6}},
        "two solos": {"kick": {"solo": True}, "snare": {"solo": True}},
    }
    # what the chain adds to a stem key, as it was before the rule: a
    # session with no muted / soloed-out kick keeps its keys
    clock = {"kicks": [0, 8], "kick_bpm": 124.0, "kick_bars": 1, "kick_steps": 16}
    assert _chain_clock(SIDECHAIN, base) == clock
    inp = RenderInputs(patch=PAD, arr=base, parts=("synth", "drums"), main_fx=SIDECHAIN)
    key, ducked = _main_stem(inp)
    _, dry = _main_stem(replace(inp, arr=replace(base, drums=no_kick)))
    assert not np.allclose(ducked, dry)  # the duck is there to lose
    for why, mix in sounding.items():
        arr = replace(base, drum_mix=mix)
        assert kick_steps(arr) == [0, 8] and "kick" in dict(drum_hits(arr)), why
        got_key, got = _main_stem(replace(inp, arr=arr))
        assert got_key == key, why
        np.testing.assert_array_equal(got, ducked, why)
    for why, mix in silent.items():
        arr = replace(base, drum_mix=mix)
        assert kick_steps(arr) == [] and not dict(drum_hits(arr)).get("kick"), why
        got_key, got = _main_stem(replace(inp, arr=arr))
        # the key of the same lane with no kick programmed at all
        assert got_key == _main_stem(replace(inp, arr=replace(arr, drums=no_kick)))[0]
        assert got_key != key, why
        np.testing.assert_array_equal(got, dry, why)
        # a stem cached while the kick sounded is not served for it
        for fx in ({"main_fx": SIDECHAIN}, {"master_fx": SIDECHAIN}):
            a = RenderInputs(patch=PAD, arr=base, parts=("synth", "drums"), **fx)
            b = replace(a, arr=arr)
            cached, fresh = _cached_then_fresh(a, b)
            np.testing.assert_array_equal(cached, fresh, why)
            # the whole loop: as if the kick's steps were not there
            gone = replace(b, arr=replace(arr, drums=no_kick))
            np.testing.assert_array_equal(fresh, render_audio(gone), why)


def test_a_sidechain_ducks_on_every_step_of_a_kick_with_a_probability():
    """Not followed, on purpose: the roll is seeded from the arrangement
    `drum_hits` is handed, and a song section rolls its drums per chunk
    while its chain runs once over the whole cycle — there is no one roll
    for the duck to share. A kick row that sounds ducks on all its steps."""
    from synthlab.studio.arrange import drum_hits
    from synthlab.studio.fxrack import kick_steps

    arr = replace(
        ARR,
        drums=[["kick", s] for s in range(0, 16, 2)],
        drum_mix={"kick": {"prob": 0.5}},
    )
    played = dict(drum_hits(arr))["kick"]
    assert 0 < len(played) < 8  # the roll does drop some of them
    assert kick_steps(arr) == list(range(0, 16, 2))


def test_a_clip_is_placed_in_the_loop_being_rendered(tmp_path):
    """Perform with the drums off, a 1-bar synth loop, a clip at bar 2: it
    is past the loop and must not play. The placement's bar cap read both
    clocks whatever the parts, so with an (idle) 2-bar drum machine the
    clip played, folded onto bar 1 — and the stem key never saw drum_bars,
    so one StemCache served whichever of the two rendered first."""
    from synthlab.core import SR
    from synthlab.studio.clips import save_clip

    save_clip(tmp_path, "hit", np.full((SR // 4, 2), 0.25))
    arr = replace(
        ARR, cells=[], drums=[], clips=[{"clip": "hit", "at_bar": 1, "gain": 1.0}]
    )
    one = RenderInputs(patch=PAD, arr=arr, parts=("synth", "clips"), clip_root=tmp_path)
    two = replace(one, arr=replace(arr, drum_bars=2))
    assert not np.any(render_audio(one))
    assert not np.any(render_audio(two))
    got, want = _cached_then_fresh(two, one)
    np.testing.assert_array_equal(got, want)
    # with the drums in the loop it is two bars long, and the clip plays
    assert np.any(render_audio(replace(two, parts=("synth", "drums", "clips"))))


# --- the loop key: the drum lane only when something reads it ----------------------

DRUM_EDITS = (
    {"drum_vel": {"0": 0.3}},
    {"drum_mix": {"kick": {"level": 0.5}}},
    {"drum_bars": 2},
    {"drum_steps": 8},
    {"drum_swing": 0.4},
    {"drum_humanize": 0.5},
    {"drum_params": {"kick": {"decay": 0.2}}},
    {"drums": [["kick", 4]]},
)
REPEAT = [{"kind": "beat_repeat", "params": {"chance": 1.0, "mix": 1.0}}]


def test_a_synth_only_key_carries_no_drum_state():
    """The Synths bench and every card audition render `parts=("synth",)`.
    Their key hashed the drum velocities, mixer and clock all the same, so
    a drum edit re-keyed and re-rendered them for identical audio."""
    inp = RenderInputs(patch=PAD, arr=ARR, layers=[LAYER], parts=("synth",))
    want = render_audio(inp)
    for edit in DRUM_EDITS:
        edited = replace(inp, arr=replace(ARR, **edit))
        assert edited.key() == inp.key(), edit
        np.testing.assert_array_equal(render_audio(edited), want)
        # with the drums playing, every one of them is an input
        both = replace(inp, parts=("synth", "drums"))
        assert replace(both, arr=edited.arr).key() != both.key(), edit


def test_a_synth_only_key_keeps_what_its_chains_read_off_the_drum_clock():
    """A beat repeat's period is gcd(bars, drum_bars) whether or not the
    drums play (`fxrack.repeat_period`), and a sidechain reads the drum
    grid: `_chain_clock`, the stem keys' own rule, stays in the loop key —
    for the main lane's chain, a layer's and the master's."""
    arr = replace(ARR, bars=2)
    lay = {**LAYER, "fx": REPEAT}
    for fx in ({"main_fx": REPEAT}, {"master_fx": REPEAT}, {"layers": [lay]}):
        inp = RenderInputs(patch=PAD, arr=arr, parts=("synth",), **fx)
        longer = replace(inp, arr=replace(arr, drum_bars=2))  # period 4 -> 8 beats
        assert longer.key() != inp.key(), fx
        assert not np.array_equal(render_audio(longer), render_audio(inp)), fx
        # what the chain does not read still stays out
        quiet = replace(inp, arr=replace(arr, drum_vel={"0": 0.3}, drum_swing=0.3))
        assert quiet.key() == inp.key(), fx
    for fx in ({"main_fx": SIDECHAIN}, {"master_fx": SIDECHAIN}):
        inp = RenderInputs(patch=PAD, arr=ARR, parts=("synth",), **fx)
        assert replace(inp, arr=replace(ARR, drum_steps=8)).key() != inp.key()
        assert replace(inp, arr=replace(ARR, drum_vel={"0": 0.3})).key() == inp.key()


def test_the_render_revision_moves_every_preview_key(monkeypatch):
    """`/preview/{key}.wav` is immutable: an engine change that moves what
    a key's inputs render to bumps `RENDER_REV`, and every loop key — and
    each section key, which holds one — changes with it."""
    from synthlab.studio import render
    from synthlab.studio import song_model as sm

    song = sm.new_song(120.0, "A1", "minor")
    sec = sm.add_section(song, "A", 1)
    track = sm.add_track(song, "synth", "Lead", patch=PAD.to_json())
    notes = {"bars": 1, "steps_per_bar": 16, "cells": [["A2", 0]]}
    sm.set_cell(song, sec, track, sm.add_pattern(song, "notes", "n", notes))
    parts = sm.section_arrangement(song, song["sections"][0])
    loops = [
        RenderInputs(patch=PAD, arr=ARR, parts=p)
        for p in (("synth",), ("drums",), ("synth", "drums"))
    ]
    before = [inp.key() for inp in loops] + [render.section_key(parts, None)]
    monkeypatch.setattr(render, "RENDER_REV", render.RENDER_REV + 1)
    after = [inp.key() for inp in loops] + [render.section_key(parts, None)]
    assert all(a != b for a, b in zip(before, after, strict=True))


def test_the_newer_oscillator_fields_stay_out_of_older_keys_and_move_their_own():
    """Hard sync, fm4 and drift are KEY_ADDED_FIELDS: at their
    defaults a patch's key is the key it had before they existed, and off
    them the key moves (and a stem keyed on it renders afresh)."""
    from synthlab.studio.render import KEY_ADDED_FIELDS, keyed_patch, patch_key

    new = ("osc2_sync", "sync_pitch", "drift", "fm4_algo", "fm4_feedback")
    new += tuple(
        f"fm4_{k}{i}" for k in ("ratio", "level", "decay") for i in range(1, 5)
    )
    assert set(new) <= set(KEY_ADDED_FIELDS)
    for name in new:
        assert KEY_ADDED_FIELDS[name] == getattr(Patch(), name), name
    assert not set(new) & set(keyed_patch(Patch()))
    for moved in (
        replace(PAD, osc2="saw", osc2_sync="on"),
        replace(PAD, drift=0.3),
        replace(PAD, osc1="fm4", fm4_level3=0.2),
    ):
        assert patch_key(moved) != patch_key(
            replace(moved, **{k: KEY_ADDED_FIELDS[k] for k in new})
        )
        stale, fresh = _cached_then_fresh(
            RenderInputs(patch=PAD, arr=ARR, parts=("synth",)),
            RenderInputs(patch=moved, arr=ARR, parts=("synth",)),
        )
        np.testing.assert_array_equal(stale, fresh)


def test_timing_inside_a_step_keys_only_the_stems_that_play_it():
    """`Arrangement.note_nudge` (free record) is absent when
    unset: a loop without it keeps every key it had (`RENDER_REV` did not
    move); with it the main stem — and a layer playing its own — is keyed
    on it and renders afresh, and the drums stem is not touched."""
    from synthlab.studio.render import _stem_jobs
    from synthlab.studio.schema import arr_dict

    plain = RenderInputs(patch=PAD, arr=ARR, parts=("synth", "drums"))
    assert "note_nudge" not in arr_dict(ARR)
    assert all("note_nudge" not in j.payload for j in _stem_jobs(plain))
    nudged = replace(plain, arr=replace(ARR, note_nudge={"0": 0.3}))  # (one held A2)
    assert nudged.key() != plain.key()
    jobs = {j.name: j for j in _stem_jobs(nudged)}
    assert jobs["main"].payload["note_nudge"] == {"0": 0.3}
    assert (
        jobs["drums"].payload == {j.name: j for j in _stem_jobs(plain)}["drums"].payload
    )
    got, want = _cached_then_fresh(plain, nudged)
    np.testing.assert_array_equal(got, want)
    assert not np.allclose(want, render_audio(plain))
    # a layer's own timing, as its own velocities: keyed on its dict
    inp = RenderInputs(patch=PAD, arr=ARR, layers=[LAYER], main_muted=True)
    lay = replace(inp, layers=[{**LAYER, "note_nudge": {"4": -0.25}}])
    assert lay.key() != inp.key()
    got, want = _cached_then_fresh(inp, lay)
    np.testing.assert_array_equal(got, want)
