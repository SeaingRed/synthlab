"""The four style packs: filter house, emotive garage, melodic techno and
trance — recipe Sounds, kits, beat styles, chain presets, a Voice preset —
and the others in the same tables: French electro and industrial,
neo-psych and the 60s studio (their measured table is in
`docs/sounds-and-breeding.md`, "Style packs: psych and sixties"), the
sounds five styles' records share, the drop FX and the dynamics chains
("Style packs: garage, techno, house, electro, trance"), hip-hop / trap,
drum & bass, dubstep, synthwave, future bass ("Style packs: hip-hop, drum
& bass, dubstep, synthwave, future bass") and lo-fi hip-hop, ambient /
cinematic, chiptune, hardstyle, phonk, on hard sync, 4-op FM, the newer
wavetables and drift ("Style packs: lo-fi, ambient, chiptune, hardstyle,
phonk"). Generic names only; everything is synthesised from engine
parameters and FX units.

What is pinned here is what can be *measured*: each sound sits where its
role says (a sub is under 120 Hz, a lead is not), has no DC, peaks under
full scale, and holds or dies as intended; the rolling bass leaves the
kick's sixteenth; each kit and beat style has the grammar its style names.
None of it says whether a pack SOUNDS like its style — that takes ears.

Measured (one note of the whole recipe at its card pitch and
tempo, `recipes.stack_audio`; dBFS; "<120" the share of energy under
120 Hz; "hold" the dry main patch's level 0.6-0.9 s into a 1 s note against
its loudest 50 ms; "free" the peak with the recipe's pump / gate taken out
— a pumped sound's card note starts on the beat, at its most ducked):

    recipe             style   role  note  peak   rms   free  <120  hold   live keys
    Chopped Stab Loop  house   keys  C4   -11.0  -28.8  -1.5  0.00  gone   worklet
    Slide Funk Bass    house   bass  A1    -7.5  -19.6  -0.9  0.76  -4.9   worklet
    Talkbox Carrier    house   lead  C3    -2.7  -15.0        0.00  -0.2   worklet
    Vowel Lead         house   lead  C4    -0.8  -12.3        0.00  -2.3   worklet
    Phaser String Pad  house   pad   C3    -8.2  -22.5        0.00  -3.4   worklet
    Felt Keys          garage  keys  C4    -2.4  -13.4        0.00  -6.1   WAV (vinyl)
    Vox Pluck          garage  lead  C4    -6.8  -32.8        0.00  gone   worklet
    Ducked Slide Sub   garage  bass  A1    -2.7   -7.1  -1.8  1.00  -0.4   worklet
    Chop Bed Pad       garage  pad   C3    -4.1  -17.4  -4.2  0.00  -0.6   WAV (vinyl)
    Evolving Pluck     techno  lead  C4    -6.7  -33.6        0.00  gone   worklet
    Wide Unison Stab   techno  keys  C4    -6.3  -24.9        0.00  -16.6  worklet
    Rolling Mid Bass   trance  bass  A2    -4.0  -16.3  -1.0  0.48  -2.2   worklet
    Rolling Sub        trance  bass  A1    -2.5   -6.5  -1.5  1.00  -0.3   worklet
    Anthem Supersaw    trance  lead  C4    -2.0  -14.4        0.00  -4.8   worklet
    Stack Pad          trance  pad   C3    -4.0  -19.0        0.17  -2.7   worklet
    Gated Pad          trance  pad   C3    -1.7  -17.7  -1.7  0.02  -8.2   worklet
    Dotted Pluck       trance  lead  C4    -4.4  -29.9        0.00  gone   worklet

(The mid bass's 0.48 under 120 Hz at A2 is its fundamental, 110 Hz: under
80 Hz it holds 2 %.) No recipe has DC: |mean| / peak < 0.005 and under
1 % of its energy below 20 Hz, every one.

The rolling bass at 138 bpm against the Trance kit (F#2 / F#1, three
sixteenths after every kick, a cut between them), level by sixteenth:

                     kick's step   the three after   gap      without the duck
    Rolling Mid Bass   -37.7 dB      -15.6 dB        22.1 dB   12.5 dB
    Rolling Sub        -24.7 dB       -4.9 dB        19.8 dB   11.7 dB
    kick (the kit)      -4.7 dB

and between its notes the mid bass falls 16-18 dB (three notes, not one), the
sub 6-8 dB (a 46 Hz sine is five cycles a sixteenth: it rounds off)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from synthlab.analysis import extract
from synthlab.core import SR
from synthlab.effects import chain as fx_chain
from synthlab.studio import beatgen, fxlive, recipes, voice, worklet
from synthlab.studio.curves import FX_LANES
from synthlab.studio.drumfactory import DRUM_KITS
from synthlab.studio.fxrack import (
    CHAIN_PRESETS,
    GATE_PATTERNS,
    build_chain,
    chain_preset,
)
from synthlab.studio.recipes import RECIPES, get_recipe, recipe_patch, stack_audio
from synthlab.studio.render import RenderInputs, render_stems
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, Synth, at_bpm

NEW = [e["name"] for e in RECIPES[12:]]
STYLE_OF = {
    "house": 122.0,
    "garage": 132.0,
    "techno": 125.0,
    "trance": 138.0,
    # Just Ice and Nine Inch Wails
    "electro": 120.0,
    "industrial": 105.0,
    # Psych Haze and Sixties Studio
    "psych": 112.0,
    "sixties": 116.0,
    # the drop FX, every style's
    "drops": 128.0,
    # hip-hop / trap, drum & bass, dubstep, synthwave, future bass
    "hiphop": 140.0,
    "dnb": 174.0,
    "dubstep": 140.0,
    "synthwave": 100.0,
    "future": 150.0,
    # lo-fi, ambient, chiptune, hardstyle, phonk
    "lofi": 80.0,
    "ambient": 70.0,
    "chiptune": 140.0,
    "hardstyle": 150.0,
    "phonk": 140.0,
}
# what each pack must hold: role -> at least this many sounds
PACKS = {
    "house": {"keys": 1, "bass": 1, "lead": 2, "pad": 1},
    "garage": {"keys": 4, "bass": 2, "lead": 1, "pad": 2},
    "techno": {"lead": 2, "bass": 2, "pad": 2, "keys": 1, "fx": 2},
    "trance": {"bass": 2, "lead": 2, "pad": 2},
    "electro": {"bass": 2, "keys": 1, "pad": 1, "lead": 1, "fx": 1},
    "industrial": {"bass": 1, "keys": 2, "pad": 1, "lead": 1, "fx": 1},
    "psych": {"bass": 1, "keys": 2, "pad": 1, "lead": 1, "fx": 1},
    "sixties": {"bass": 1, "keys": 1, "pad": 1, "lead": 2, "fx": 1},
    "drops": {"fx": 4},
    "hiphop": {"bass": 2, "keys": 1, "pad": 1, "lead": 1},
    "dnb": {"bass": 2, "pad": 1, "keys": 1},
    "dubstep": {"bass": 3, "lead": 1},
    "synthwave": {"pad": 2, "keys": 1, "bass": 1, "lead": 1},
    "future": {"keys": 1, "lead": 2, "pad": 1},
    "lofi": {"keys": 1, "pad": 1, "bass": 1, "lead": 1},
    "ambient": {"pad": 3, "keys": 1, "bass": 1, "fx": 1},
    "chiptune": {"lead": 4, "bass": 1},
    "hardstyle": {"lead": 2, "bass": 1, "pad": 1},
    "phonk": {"lead": 2, "bass": 1, "pad": 1},
}
# the sub drop is a sub (mono, under 120 Hz, held while the key is down);
# the impact a boom with a crash over it; the long 808 (its soft drive
# leaves it a sub) and two plain subs; the soft sub and the triangle bass
# (a triangle's harmonics are weak: 0.98 of it is under 120 Hz at A1).
# The braam is a boom
SUBS = (
    "Rolling Sub",
    "Ducked Slide Sub",
    "Sub Drop",
    "808 Long",
    "Rolling DnB Sub",
    "Half-Time Sub",
    "Soft Sub",
    "Triangle Bass",
)
BOOMS = ("Impact", "Cinematic Braam")
PLUCKS = (
    "Chopped Stab Loop",
    "Vox Pluck",
    "Evolving Pluck",
    "Dotted Pluck",
    "Gated Disco Stab",
    "Machine Pulse",
    "Struck Metal",
    "Phase Arp",
    "Dark Arp Pluck",
    "Bent Pluck",
    "Muted Pluck",
    "Thin Pulse Arp",
    "Phonk Cowbell",
    # the core library: every one gone 0.6 s into a held key
    "Xylophone",
    "Brass Stab",
    "Pizzicato",
    "Orchestra Hit",
    "Koto",
    "Sitar Pluck",
    "Bell Pluck",
    "Synth Pluck",
)
PADS = (
    "Phaser String Pad",
    "Chop Bed Pad",
    "Stack Pad",
    "Gated Pad",
    "Cathedral Choir Pad",
    "Static Drone",
    "Haze Pad",
    "Tape Strings",
    "Big Room Pad",
    "String Machine",
    "Squashed Vocal Pad",
    "Dark Choir Pad",
    "Atmos Pad",
    "Poly Chorus Pad",
    "Lush Strings",
    "Future Pad",
    "Jazz Chord Pad",
    "Scanning Pad",
    "Vowel Drift Pad",
    "Shimmer Bed",
    "Euphoric Pad",
    "Dark Vowel Pad",
    # the core library: its pads and strings, and the two held fx
    "Synth Strings",
    "Synth Choir",
    "Soft Horns",
    "Warm Analog Pad",
    "Glass Pad",
    "PWM Pad",
    "Sweep Pad",
    "Soft Pad",
    "Ambience Bed",
    "Wind",
)
# the two whose chain holds an offline unit: keys play the dry WAV
WAV_ONLY = {
    "Felt Keys": ["fx:vinyl"],
    "Chop Bed Pad": ["fx:vinyl"],
    # Nine Inch Wails' score keys, on the Tape Wear chain
    "Ghost Keys": ["fx:vinyl", "fx:compress"],
    # the ambient shimmer bed, on the Shimmer Wash (a whole-buffer shimmer)
    "Shimmer Bed": ["fx:shimmer"],
}


def _db(v) -> float:
    return float(20 * np.log10(max(float(v), 1e-12)))


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def _share(x, lo: float, hi: float) -> float:
    m = x.mean(axis=1) if x.ndim == 2 else x
    p = np.abs(np.fft.rfft(m)) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float(p[(f >= lo) & (f < hi)].sum() / p.sum())


def _free(entry: dict) -> dict:
    """The recipe with its pump / gate taken out: what it peaks at when no
    duck is holding it down."""
    bare = copy.deepcopy(entry)
    bare["main_fx"] = [u for u in bare["main_fx"] if u["kind"] not in ("pump", "gate")]
    return bare


def _hold_db(entry: dict) -> float:
    p = at_bpm(recipe_patch(entry), entry["bpm"])
    note = np.asarray(Synth(p).note(entry["pitch"], 1.4, gate=1.0), dtype=float)
    m = note.mean(axis=1) if note.ndim == 2 else note
    w = int(0.05 * SR)
    env = np.array([_rms(m[k : k + w]) for k in range(0, len(m) - w, w)])
    return _db(env[12:18].mean() / env.max())


# --- the packs' sounds ----------------------------------------------------------


def test_each_pack_holds_its_roles_at_its_tempo():
    for genre, bpm in STYLE_OF.items():
        sounds = [e for e in RECIPES if genre in e["tags"]]
        assert {e["bpm"] for e in sounds} == {bpm}, genre
        for role, n in PACKS[genre].items():
            have = [e["name"] for e in sounds if role in e["tags"]]
            assert len(have) >= n, (genre, role, have)
    assert len(NEW) == 148 and len(set(NEW)) == 148


@pytest.mark.parametrize("name", NEW)
def test_a_new_recipe_has_no_dc_and_peaks_under_full_scale(name):
    e = get_recipe(name)
    audio = stack_audio(e)
    assert np.all(np.isfinite(audio))
    peak = float(np.abs(audio).max())
    assert 0.2 < peak <= 1.0, (name, _db(peak))
    # with nothing ducking it either
    assert float(np.abs(stack_audio(_free(e))).max()) <= 1.0, name
    assert abs(float(audio.mean())) / peak < 0.005, name
    assert _share(audio, 0.0, 20.0) < 0.01, name


@pytest.mark.parametrize("name", NEW)
def test_a_new_recipe_sits_where_its_role_says(name):
    e = get_recipe(name)
    audio = stack_audio(e)
    low = _share(audio, 0.0, 120.0)
    if name in SUBS:
        assert low > 0.9, (name, low)  # a sub is under 120 Hz
        np.testing.assert_allclose(audio[:, 0], audio[:, 1])  # and mono
    elif "bass" in e["tags"]:
        assert 0.3 < low < 0.9, (name, low)  # a bass with harmonics over it
    elif name in BOOMS:
        assert 0.5 < low < 0.97, (name, low)  # the boom, the crash over it
    else:
        assert low < 0.25, (name, low)  # anything else leaves the lows alone
    hold = _hold_db(e)
    if name in PLUCKS:
        assert hold < -40.0, (name, hold)  # gone 0.6 s into a held key
    if name in PADS or name in SUBS:
        assert hold > -9.0, (name, hold)  # holds while the key is down


def test_the_live_keys_play_every_new_recipe_but_the_four_offline():
    """`worklet.blockers` for the main patch and each layer, and the chain's
    offline units (`fxlive.offline_units`): empty means the AudioWorklet
    plays the sound and its FX; the three with a `vinyl` unit (seeded noise
    over the whole buffer) and the shimmer bed (a whole-buffer shimmer)
    fall back to the dry key WAV. Hard sync, fm4, the newer tables and
    drift play live in every one of the lo-fi to phonk packs'."""
    for name in NEW:
        e = get_recipe(name)
        why = list(worklet.blockers(recipe_patch(e)))
        why += [f"fx:{k}" for k in fxlive.offline_units(e["main_fx"])]
        for lay in e["layers"] or []:
            why += worklet.blockers(Patch.from_dict(lay["patch"]))
            why += [f"fx:{k}" for k in fxlive.offline_units(lay["fx"])]
        assert why == WAV_ONLY.get(name, []), (name, why)


# --- the techniques, by pack ----------------------------------------------------


def _unit(entry: dict, kind: str) -> dict:
    return next(u for u in entry["main_fx"] if u["kind"] == kind)


def test_filter_house_sounds_do_what_they_are_named_for():
    loop = get_recipe("Chopped Stab Loop")
    kinds = [u["kind"] for u in loop["main_fx"]]
    assert kinds == ["lowpass", "phaser", "pump"]
    # the low-pass is the one a song sweeps: its cutoff and q take a lane,
    # it is resonant, and it starts open (the loop sounds as written)
    assert {"cutoff", "q"} <= set(FX_LANES["lowpass"])
    assert _unit(loop, "lowpass")["params"] == {"cutoff": 9000.0, "q": 2.5}
    assert _unit(loop, "pump")["params"]["depth"] >= 0.8
    assert _unit(loop, "phaser")["params"]["rate"] <= 0.25  # slow
    bass = get_recipe("Slide Funk Bass")
    p = recipe_patch(bass)
    assert p.glide == pytest.approx(0.05) and p.sub_level > 0 and p.osc1 == "saw"
    assert p.filter_decay <= 0.12  # a short filter envelope
    assert _unit(bass, "pump")["params"]["depth"] >= 0.8  # ducked hard
    # the carrier: one voice, glide, bright, nothing moving
    carrier = recipe_patch(get_recipe("Talkbox Carrier"))
    assert carrier.unison == 1 and carrier.glide > 0 and carrier.drive > 1.5
    assert (carrier.osc1, carrier.osc2) == ("saw", "square")
    assert carrier.vibrato_depth == carrier.filter_lfo_depth == 0.0
    assert carrier.sustain == 1.0  # it holds for as long as the words last
    audio = stack_audio(get_recipe("Talkbox Carrier"))
    assert _share(audio, 2000.0, 12000.0) > 0.02  # harmonics up there to shape
    for name in ("Vowel Lead", "Vox Pluck"):
        e = get_recipe(name)
        assert recipe_patch(e).formant_amount >= 0.8
        ys = [pt[1] for pt in e["curves"]["formant_vowel"]]
        assert max(ys) - min(ys) >= 0.5, name  # it talks: a to o and back


def test_the_vowel_lead_says_more_than_one_vowel():
    """The drawn vowel is heard: the first and the last third of the note
    have different formant balances (measured: the 600-1200 Hz band's share
    moves by more than a factor of 1.5)."""
    audio = stack_audio(get_recipe("Vowel Lead"))
    n = len(audio)
    first = _share(audio[: n // 3], 600.0, 1200.0)
    last = _share(audio[n // 3 : 2 * n // 3], 600.0, 1200.0)
    assert max(first, last) > 1.5 * min(first, last), (first, last)


def test_garage_top_up_sounds_do_what_they_are_named_for():
    felt = get_recipe("Felt Keys")
    p = recipe_patch(felt)
    # 2-op FM, honestly: ratio 1, a low index that decays fast, a soft
    # attack, shut at 1.8 kHz — and a sine an octave under
    assert (p.osc1, p.fm_ratio) == ("fm", 1.0) and p.fm_index <= 1.5
    assert 0.0 < p.fm_decay <= 0.4 and p.attack >= 0.01 and p.cutoff == 1800.0
    assert [lay["role"] for lay in felt["layers"]] == ["sub"]
    assert felt["layers"][0]["follow"] == {"octave": -1}
    assert Patch.from_dict(felt["layers"][0]["patch"]).osc1 == "sine"
    assert felt["main_fx"] == chain_preset("tape_lofi")
    assert "piano" in felt["how"] and "Not a piano" in felt["how"]
    audio = stack_audio(felt)
    assert _share(audio, 4000.0, 22050.0) < 0.02  # felted: no top
    sub = get_recipe("Ducked Slide Sub")
    assert recipe_patch(sub).osc1 == "sine" and recipe_patch(sub).glide > 0.05
    assert [u["kind"] for u in sub["main_fx"]] == ["pump", "width"]
    pad = stack_audio(get_recipe("Chop Bed Pad"))
    # the band a vocal chop lives in is left empty
    assert _share(pad, 1000.0, 4000.0) < 0.05
    assert "vinyl" in [u["kind"] for u in get_recipe("Chop Bed Pad")["main_fx"]]


def test_melodic_techno_top_up_sounds_do_what_they_are_named_for():
    pluck = get_recipe("Evolving Pluck")
    p = recipe_patch(pluck)
    assert (p.osc1, p.osc2) == ("pluck", "saw")
    delay = _unit(pluck, "delay")["params"]
    assert delay["sync"] == 3.0 and delay["ping_pong"] == 1.0  # dotted, ping-pong
    assert _unit(pluck, "reverb")["params"]["decay"] >= 3.0  # a hall
    stab = get_recipe("Wide Unison Stab")
    p = recipe_patch(stab)
    assert p.osc1 == "supersaw" and p.unison == 5 and p.unison_spread == 1.0
    assert _unit(stab, "width")["params"]["amount"] == 1.8
    audio = stack_audio(stab)
    assert extract(audio).stereo_width > 0.8  # measured 1.10
    assert _hold_db(stab) < -12.0  # short


def test_trance_sounds_do_what_they_are_named_for():
    mid, sub = get_recipe("Rolling Mid Bass"), get_recipe("Rolling Sub")
    pm, ps = recipe_patch(mid), recipe_patch(sub)
    assert pm.osc1 == "saw" and pm.cutoff == 700.0 and pm.release == 0.07
    assert ps.osc1 == "sine" and ps.release == 0.09
    for e in (mid, sub):
        pump = _unit(e, "pump")["params"]
        assert (pump["depth"], pump["release"], pump["beats"]) == (0.85, 0.18, 1.0)
    # the pair shares the low end out: the sub under 120 Hz, the mid bass
    # out of the bottom octave and three times as bright
    a_mid, a_sub = stack_audio(mid), stack_audio(sub)
    assert _share(a_mid, 0.0, 80.0) < 0.05 and _share(a_sub, 0.0, 120.0) > 0.95
    assert extract(a_mid).centroid > 3 * extract(a_sub).centroid
    lead = get_recipe("Anthem Supersaw")
    p = recipe_patch(lead)
    assert p.osc1 == "supersaw" and p.unison >= 5 and p.unison_detune == 24.0
    assert p.unison_spread == 1.0
    assert [(lay["role"], lay["follow"]["octave"]) for lay in lead["layers"]] == [
        ("air", 1)
    ]
    assert extract(stack_audio(lead)).stereo_width > 0.7  # measured 0.94
    stack = get_recipe("Stack Pad")
    assert _unit(stack, "reverb")["params"]["decay"] >= 3.0
    assert [lay["follow"]["octave"] for lay in stack["layers"]] == [-1]
    assert recipe_patch(stack).osc2_octave == 1  # an octave over, one under
    gated = get_recipe("Gated Pad")
    gate = _unit(gated, "gate")
    assert gate["pattern"] == GATE_PATTERNS["trance"]["pattern"]
    assert gate["params"]["depth"] == 1.0 and "depth" in FX_LANES["gate"]
    pluck = get_recipe("Dotted Pluck")
    delay = _unit(pluck, "delay")["params"]
    assert delay["sync"] == 3.0 and delay["ping_pong"] == 1.0


def test_the_gated_pad_is_chopped_on_the_trance_figure():
    """One note of the Gated Pad at 138 bpm: its level by sixteenth follows
    x.xxx.x.x.xxx.x. — the shut steps at least 6 dB under the open ones
    (the echo and the room fill the gaps; measured ~10 dB)."""
    e = get_recipe("Gated Pad")
    audio = stack_audio(e)
    step = 60.0 / e["bpm"] / 4.0 * SR
    pat = GATE_PATTERNS["trance"]["pattern"]
    lv = []
    held = int(0.8 * len(audio) / step)  # the steps the note is held over
    assert held >= 8
    for i in range(2, held):  # past the pad's attack
        a = int(round(i * step + 0.25 * step))
        b = int(round(i * step + 0.85 * step))
        lv.append((_rms(audio[a:b]), pat[i % 16]))
    opened = np.mean([v for v, ch in lv if ch == "x"])
    shut = np.mean([v for v, ch in lv if ch == "."])
    assert _db(shut / opened) < -6.0


# --- the rolling bass against the kick ------------------------------------------

ROLL_BPM = 138.0
ROLL = [s for beat in range(4) for s in (4 * beat + 1, 4 * beat + 2, 4 * beat + 3)]
CUTS = [s for beat in range(4) for s in (4 * beat + 2, 4 * beat + 3)]


def _rolling(duck: bool = True, sidechain: bool = False) -> dict:
    """One bar of the rolling figure — steps 1, 2 and 3 of every beat,
    never step 0, a cut before the second and the third — on F#2 (the mid
    bass) and F#1 (the sub), with the Trance kit playing: the stems."""
    mid, sub = get_recipe("Rolling Mid Bass"), get_recipe("Rolling Sub")
    kit = DRUM_KITS["Trance"]

    def fx(entry):
        units = [dict(u) for u in entry["main_fx"]]
        if not duck:
            return [u for u in units if u["kind"] != "pump"]
        if sidechain:  # the kit's own kick instead of the clock
            return [
                {"kind": "sidechain", "params": {"depth": 0.85, "release": 0.18}}
                if u["kind"] == "pump"
                else u
                for u in units
            ]
        return units

    arr = Arrangement(
        bpm=ROLL_BPM,
        bars=1,
        steps_per_bar=16,
        cells=[["F#2", s] for s in ROLL],
        cuts=[["F#2", s] for s in CUTS],
        drums=[list(c) for c in kit["pattern"]["steps"]],
        drum_params={v: dict(p) for v, p in kit["params"].items()},
        drum_vel=dict(kit["vel"]),
        drum_bars=1,
        drum_steps=16,
    )
    layer = {
        "patch": sub["patch"],
        "cells": [["F#1", s] for s in ROLL],
        "cuts": [["F#1", s] for s in CUTS],
        "fx": fx(sub),
        "gain": 1.0,
        "muted": False,
    }
    inp = RenderInputs(
        patch=recipe_patch(mid), arr=arr, layers=[layer], main_fx=fx(mid)
    )
    return dict(render_stems(inp))


def _by_step(x) -> np.ndarray:
    step = 60.0 / ROLL_BPM / 4.0 * SR
    return np.array(
        [_rms(x[int(round(s * step)) : int(round((s + 1) * step))]) for s in range(16)]
    )


def test_the_rolling_bass_leaves_the_kicks_sixteenth_twice_over():
    """Once by the figure (no note starts on the kick's step: 12 dB under
    the other three, the last note's release ringing into it) and again by
    the duck (another 8-10 dB): both tracks at least 18 dB down where the
    kick is."""
    ducked, bare = _rolling(), _rolling(duck=False)
    kick = _by_step(ducked["drums"])[0::4].mean()
    for name, floor in (("main", 18.0), ("layer2", 18.0)):
        lv = _by_step(ducked[name])
        on, off = lv[0::4].mean(), lv.reshape(4, 4)[:, 1:].mean()
        assert _db(off / on) > floor, (name, _db(off / on))
        raw = _by_step(bare[name])
        raw_gap = _db(raw.reshape(4, 4)[:, 1:].mean() / raw[0::4].mean())
        assert 8.0 < raw_gap < _db(off / on) - 6.0, (name, raw_gap)
        # every one of the three sixteenths sounds: none is 3 dB under the rest
        assert _db(lv.reshape(4, 4)[:, 1:].min() / off) > -3.0, name
        assert on < 0.1 * kick  # the kick has its step to itself
    assert _db(kick) > -8.0


def test_the_pump_is_the_kits_kick_on_a_four_on_the_floor_beat():
    """The recipes duck on the clock (a `pump`: it works on Synths, where
    no drums render, and through a bar where the kick drops out). Against
    the Trance kit that is the same duck a `sidechain` at 0.85 / 0.18 takes
    from the kit's own kick."""
    a, b = _rolling(), _rolling(sidechain=True)
    for name in ("main", "layer2"):
        np.testing.assert_allclose(a[name], b[name], atol=1e-9)


def test_three_sixteenths_stay_three_notes():
    """The short release is what keeps them apart: between two notes the
    mid bass (70 ms) falls more than 12 dB (measured 16-18), the sub
    (90 ms, a 46 Hz sine) more than 4 (measured 6-8)."""
    stems = _rolling(duck=False)
    step = 60.0 / ROLL_BPM / 4.0 * SR
    w = int(0.004 * SR)
    for name, least in (("main", 12.0), ("layer2", 4.0)):
        x = np.abs(stems[name]).max(axis=1)
        env = np.array([x[i : i + w].max() for i in range(0, len(x) - w, w)])
        t = np.arange(len(env)) * w
        notes = [
            env[(t >= (1 + k) * step + 0.3 * step) & (t < (1 + k) * step + 0.9 * step)]
            for k in range(3)
        ]
        level = np.mean([seg.max() for seg in notes])
        for k in range(2):  # the two cuts of the first beat
            at = (2 + k) * step
            dip = env[(t >= at - 0.12 * step) & (t < at + 0.02 * step)].min()
            assert _db(dip / level) < -least, (name, k, _db(dip / level))


# --- kits and beat styles: the drum grammar each style names --------------------


def _hits(steps, voice) -> list[int]:
    return sorted(s for v, s in steps if v == voice)


BEATS, OFFBEATS = [0, 4, 8, 12], [2, 6, 10, 14]


def test_the_trance_kit_is_the_trance_grammar():
    kit = DRUM_KITS["Trance"]
    steps = kit["pattern"]["steps"]
    assert _hits(steps, "kick") == BEATS  # a kick on every beat
    assert _hits(steps, "clap") == _hits(steps, "snare") == [4, 12]  # stacked
    assert _hits(steps, "ohat") == OFFBEATS  # an open hat on every offbeat
    # closed hats on the other sixteenths: the one after each open hat
    # chokes it
    assert _hits(steps, "hat") == [s for s in range(16) if s not in OFFBEATS]
    assert kit["pattern"]["swing"] == 0.0  # straight
    # a tight kick, a snare short enough to roll, the crash cut to a ride
    assert kit["params"]["kick"]["decay"] <= 0.32
    assert kit["params"]["snare"]["decay"] <= 0.15
    assert kit["params"]["crash"] == {"decay": 0.5}
    assert set(kit["vel"]) == {str(s) for s in range(1, 16, 2)}  # soft off-16ths


def test_the_filter_house_kit_is_the_filter_house_grammar():
    kit = DRUM_KITS["Filter House"]
    steps = kit["pattern"]["steps"]
    assert _hits(steps, "kick") == BEATS
    assert _hits(steps, "clap") == _hits(steps, "snare") == [4, 12]
    assert _hits(steps, "ohat") == OFFBEATS
    assert _hits(steps, "hat") == list(range(16))  # sixteenths
    assert 0.0 <= kit["pattern"]["swing"] <= 0.08
    # the offbeats are the accent: the off-16ths are held down, they are not
    assert all(kit["vel"][str(s)] == 0.5 for s in range(1, 16, 2))
    assert not set(kit["vel"]) & {str(s) for s in OFFBEATS + BEATS}
    assert kit["params"]["hat"]["tone"] < 8000.0  # a shaker more than a hat


def test_the_two_step_kit_is_the_two_step_grammar():
    kit = DRUM_KITS["Two-Step"]
    steps = kit["pattern"]["steps"]
    kick = _hits(steps, "kick")
    assert kit["pattern"]["swing"] >= 0.12  # swung...
    # ...and the kick straight: swing moves the odd sixteenths only
    assert all(s % 2 == 0 for s in kick + _hits(steps, "clap"))
    assert any(s % 2 == 1 for s in _hits(steps, "hat"))  # the hats are not
    assert not set(kick) & {4, 12}  # no kick on beat 2 or 4
    assert 10 in kick and 0 in kick  # one on the "and" of 3
    assert _hits(steps, "clap") == [4, 12]
    # ghosts: at least two hits a bar at 0.4 or under, each alone on its
    # step (the velocity lane is the step's, not the hit's)
    ghosts = {int(s): v for s, v in kit["vel"].items() if v <= 0.4}
    assert len(ghosts) >= 2
    for s in ghosts:
        on_step = [v for v, st in steps if st == s]
        assert len(on_step) == 1 and on_step[0] in ("snare", "rim"), s
    # just before a beat, never on one
    assert all(s % 4 == 3 for s in ghosts)


@pytest.mark.parametrize("seed", range(6))
def test_the_beat_styles_keep_their_grammar_on_every_roll(seed):
    def gen(style):
        return beatgen.generate(style, seed)

    for style in ("trance", "trance-climax", "trance-break"):
        steps = gen(style)
        assert _hits(steps, "kick") == BEATS, style
        assert _hits(steps, "ohat") == OFFBEATS, style
        assert beatgen.STYLE_SWING[style] == 0.0
    for style in ("trance", "trance-climax"):
        steps = gen(style)
        assert _hits(steps, "clap") == _hits(steps, "snare") == [4, 12], style
        assert not set(_hits(steps, "hat")) & set(OFFBEATS), style
    # the ride, in eighths, through the climax only
    assert _hits(gen("trance-climax"), "crash") == list(range(0, 16, 2))
    assert _hits(gen("trance"), "crash") == []
    assert _hits(gen("trance-break"), "snare") == []

    house = gen("filter-house")
    assert _hits(house, "kick") == BEATS and _hits(house, "ohat") == OFFBEATS
    assert _hits(house, "hat") == list(range(16))
    assert {4, 12} <= set(_hits(house, "clap")) and _hits(house, "snare") == [4, 12]
    assert _hits(gen("filter-house-break"), "kick") == [0]  # the kick steps back
    assert 0.0 <= beatgen.STYLE_SWING["filter-house"] <= 0.08

    two = gen("two-step")
    assert _hits(two, "kick") == [0, 10] and _hits(two, "clap") == [4, 12]
    assert {2, 6, 14} <= set(_hits(two, "hat"))
    assert 0.14 <= beatgen.STYLE_SWING["two-step"] <= 0.20
    four = gen("garage-4x4")
    assert _hits(four, "kick") == BEATS and _hits(four, "clap") == [4, 12]
    assert 0.14 <= beatgen.STYLE_SWING["garage-4x4"] <= 0.20

    perc = gen("hypnotic-perc")
    assert _hits(perc, "kick") == BEATS and _hits(perc, "hat") == OFFBEATS
    assert _hits(perc, "clap") == [12]  # on 4 alone
    assert len(_hits(perc, "rim")) >= 3 and len(_hits(perc, "tom")) >= 5
    assert {5, 10, 15} <= set(_hits(perc, "rim"))  # 3 in 16, Euclidean
    assert beatgen.STYLE_SWING["hypnotic-perc"] == 0.0


def test_the_new_styles_are_registered_after_the_old_ones():
    assert beatgen.STYLES == (
        "four-floor",
        "techno",
        "breaks",
        "trap",
        "sparse",
        "melodic-techno",
        "garage-rim",
        "filter-house",
        "filter-house-break",
        "two-step",
        "garage-4x4",
        "hypnotic-perc",
        "trance",
        "trance-climax",
        "trance-break",
        "electro",
        "electro-stomp",
        "electro-break",
        "industrial",
        "industrial-march",
        "industrial-pulse",
        "psych",
        "psych-disco",
        "psych-haze",
        "sixties",
        "sixties-toms",
        "sixties-break",
        # hip-hop / trap, drum & bass, dubstep, synthwave, future bass
        "trap-808",
        "boom-bap",
        "dnb",
        "dnb-roller",
        "jungle",
        "dubstep",
        "riddim",
        "synthwave",
        "synthpop",
        "future-bass",
        "future-build",
        # lo-fi, ambient, chiptune, hardstyle, phonk
        "lofi",
        "lofi-chill",
        "ambient-pulse",
        "cinematic-toms",
        "chiptune",
        "chip-chase",
        "hardstyle",
        "rawstyle",
        "drift-phonk",
        "memphis",
        "roll",  # the beat workflow's build / roll style: last, after them all
    )
    # the styles that existed still roll what they rolled (their tables and
    # the one rng stream are untouched): pinned elsewhere, spot-checked here
    assert beatgen.generate("melodic-techno", 125)[:4] == [
        ["clap", 4],
        ["clap", 12],
        ["clap", 14],
        ["hat", 1],
    ]
    for kit in (
        "Filter House",
        "Two-Step",
        "Trance",
        "Just Ice",
        "Nine Inch Wails",
        "Psych Haze",
        "Sixties Studio",
        "Hip-Hop 808",
        "Drum and Bass",
        "Dubstep",
        "Synthwave",
        "Future Bass",
        "Lo-Fi",
        "Cinematic",
        "Chiptune",
        "Hardstyle",
        "Phonk",
    ):
        assert DRUM_KITS[kit]["style"] in beatgen.STYLE_SWING
        assert (
            beatgen.STYLE_SWING[DRUM_KITS[kit]["style"]]
            == DRUM_KITS[kit]["pattern"]["swing"]
        )


def test_the_trance_kits_open_hat_is_choked_a_sixteenth_later():
    """Rendered: the open hat on an offbeat eighth is cut by the closed hat
    on the next sixteenth, so the sixteenth after THAT holds no open-hat
    ring — against the same kit with the closed hats taken out."""
    kit = DRUM_KITS["Trance"]

    def drums(steps):
        arr = Arrangement(
            bpm=138.0,
            cells=[],
            drums=steps,
            drum_params={v: dict(p) for v, p in kit["params"].items()},
        )
        inp = RenderInputs(patch=Patch(), arr=arr, parts=("drums",))
        return dict(render_stems(inp))["drums"]

    ohat = [["ohat", s] for s in OFFBEATS]
    hats = [[v, s] for v, s in kit["pattern"]["steps"] if v == "hat"]
    step = 60.0 / 138.0 / 4.0 * SR
    late = slice(int(4.3 * step), int(4.9 * step))  # the 16th after the choke
    ringing = _rms(drums(ohat)[late])
    choked = _rms(drums(ohat + [h for h in hats if h[1] not in (4, 5)])[late])
    assert choked < 0.2 * ringing


# --- chain presets --------------------------------------------------------------

BPM = 125.0


def _run(units, x, arr=None):
    return np.asarray(fx_chain(x, build_chain(units, arr or Arrangement(bpm=BPM))))


def _tone(seconds: float = 4.0, hz: float = 220.0) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = sum(np.sin(2 * np.pi * hz * k * t) / k for k in range(1, 30))
    return np.column_stack([x, x]) * 0.2


def test_filter_disco_is_a_resonant_lowpass_a_slow_phaser_and_the_pump():
    units = chain_preset("filter_disco")
    assert [u["kind"] for u in units] == ["lowpass", "phaser", "pump"]
    assert units[0]["params"]["q"] == 2.5 and units[1]["params"]["rate"] == 0.2
    x = _tone()
    y = _run(units, x)
    assert _share(y, 5000.0, 22050.0) < 0.2 * _share(x, 5000.0, 22050.0)
    beat = int(60.0 / BPM * SR)
    on = _rms(y[4 * beat : 4 * beat + beat // 8])
    off = _rms(y[4 * beat + 3 * beat // 4 : 5 * beat - beat // 16])
    assert _db(on / off) < -6.0  # it pumps (measured -7.7 dB)


def test_hard_duck_is_the_kicks_and_nothing_without_one():
    units = chain_preset("hard_duck")
    assert units == [{"kind": "sidechain", "params": {"depth": 0.9, "release": 0.3}}]
    x = _tone(2.0)
    kicks = Arrangement(bpm=BPM, drums=[["kick", s] for s in BEATS])
    y = _run(units, x, kicks)
    beat = int(60.0 / BPM * SR)
    on = _rms(y[2 * beat : 2 * beat + 256])  # the 6 ms on the hit
    off = _rms(y[2 * beat + 3 * beat // 4 : 3 * beat - beat // 8])
    assert _db(on / off) < -12.0  # all but gone on the hit
    assert np.array_equal(_run(units, x, Arrangement(bpm=BPM)), x)  # no kick


def test_crush_bus_squashes_and_dirties():
    units = chain_preset("crush_bus")
    assert [u["kind"] for u in units] == ["bitcrush", "compress", "drive"]
    t = np.arange(2 * SR) / SR
    x = np.sin(2 * np.pi * 220 * t) * (0.1 + 0.6 * (np.sin(2 * np.pi * 1.5 * t) > 0))
    x = np.column_stack([x, x])
    y = _run(units, x)
    quiet, loud = (
        slice(int(0.4 * SR), int(0.6 * SR)),
        slice(int(0.05 * SR), int(0.25 * SR)),
    )
    before = _db(_rms(x[loud]) / _rms(x[quiet]))
    after = _db(_rms(y[loud]) / _rms(y[quiet]))
    assert after < before - 6.0  # 16.9 dB of dynamics in, under 11 out
    # ... and harmonics that were not there
    assert _share(y, 400.0, 8000.0) > 3 * _share(x, 400.0, 8000.0)  # measured 4.2x


def test_dotted_ping_pong_echoes_three_sixteenths_apart_side_to_side():
    units = chain_preset("dotted_ping_pong")
    click = np.zeros((2 * SR, 2))
    click[:200] = np.hanning(200)[:, None]
    y = _run(units, click) - click * (1 - units[0]["params"]["mix"])
    gap = int(round(0.75 * 60.0 / BPM * SR))  # a dotted eighth
    first, second = y[gap : gap + 400], y[2 * gap : 2 * gap + 400]
    assert np.abs(first).max() > 0.05 and np.abs(second).max() > 0.02
    # one side, then the other
    side = lambda seg: int(np.argmax(np.abs(seg).max(axis=0)))  # noqa: E731
    assert side(first) != side(second)
    assert np.abs(y[gap // 2 : gap // 2 + 400]).max() < 0.01  # nothing between


def test_trance_gate_is_the_named_pattern_into_a_room():
    units = chain_preset("trance_gate")
    assert [u["kind"] for u in units] == ["gate", "reverb"]
    assert units[0]["pattern"] == GATE_PATTERNS["trance"]["pattern"]
    assert units[1]["params"]["decay"] >= 3.0


def test_tape_wear_rolls_the_top_off_and_keeps_every_bit():
    units = chain_preset("tape_wear")
    assert [u["kind"] for u in units] == ["vinyl", "lowpass", "compress"]
    assert "bitcrush" not in [u["kind"] for u in units]  # gentler than Tape Lo-fi
    assert units[0]["params"]["wow"] > units[0]["params"]["crackle"]
    x = _tone(2.0, 440.0)
    y = _run(units, x)
    assert _share(y, 9000.0, 22050.0) < 0.3 * _share(x, 9000.0, 22050.0)


def test_wide_hall_widens_and_keeps_the_lows_out_of_the_room():
    units = chain_preset("wide_hall")
    assert [u["kind"] for u in units] == ["highpass", "reverb", "width"]
    assert units[1]["params"]["decay"] == 6.0
    rng = np.random.default_rng(1)
    x = (
        rng.standard_normal((SR, 2))
        * 0.1
        * np.exp(-np.arange(SR) / (0.05 * SR))[:, None]
    )
    x = np.vstack([x, np.zeros((3 * SR, 2))])
    y = _run(units, x)
    assert _rms(y[2 * SR : 3 * SR]) > 20 * _rms(x[2 * SR : 3 * SR] + 1e-9)  # it rings
    assert extract(y).stereo_width > extract(x).stereo_width
    # the high-pass is first: a 55 Hz sine barely reaches the room
    sub = np.column_stack([np.sin(2 * np.pi * 55 * np.arange(SR) / SR)] * 2)
    assert _rms(_run(units, sub)) < 0.3 * _rms(sub)


def test_tape_stop_is_the_stop_unit_on_the_last_beat():
    units = chain_preset("tape_stop")
    assert units == [
        {"kind": "stop", "params": {"beats": 1.0, "fall": 1.0, "mix": 1.0}}
    ]
    assert CHAIN_PRESETS["tape_stop"]["label"] == "Tape Stop"
    assert "mix" in FX_LANES["stop"]  # how a song places it on one bar


# --- the Voice preset -----------------------------------------------------------


def test_the_talkbox_preset_is_clean_settings_and_names_its_carrier():
    preset = voice.PRESETS["talkbox"]
    assert preset["label"] == "Talkbox"
    assert recipes.get_recipe(preset["sound"]) is not None
    got = voice.preset_settings("talkbox")
    assert got == voice.clean_settings(got)  # nothing left to clamp
    assert got["carrier"] == "sound" and got["shift"] == 2.0 and got["smooth"] == 30.0
    assert got["bands"] in voice.BAND_CHOICES
    # laid over the saved settings: what it does not name stays
    base = voice.clean_settings({"notes": "C3 E3 G3", "step": "2", "octave": 1})
    over = voice.preset_settings("talkbox", base)
    assert (over["notes"], over["step"], over["octave"]) == ("C3 E3 G3", "2", 1)
    assert over["shift"] == 2.0
    for bad in ("", "nope", "TALKBOX", None, 7, ["talkbox"], "talkbox\x00"):
        assert voice.preset_settings(bad) is None, bad


# --- Just Ice and Nine Inch Wails ----------------------------------


@pytest.mark.parametrize("name", ["blown_out", "machine_rust"])
def test_the_dirt_chains_distort_and_hold_the_ceiling_with_no_offset(name):
    """A loud, lopsided pair of sines (220 Hz and its octave: nothing above
    600 Hz) through each: harmonics added up there, the -1 dB trim holding
    the peak, and no offset left (the high-pass after the first clipper
    takes the one a clipped lopsided wave keeps)."""
    t = np.arange(SR) / SR
    tone = 0.9 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(2 * np.pi * 440 * t)
    x = np.stack([tone, tone], axis=1)
    y = fx_chain(x, build_chain(chain_preset(name), Arrangement()))
    assert np.max(np.abs(y)) <= 10 ** (-1 / 20) + 1e-9
    assert abs(float(y.mean())) / float(np.max(np.abs(y))) < 0.005
    assert _share(x, 600, 20000) < 1e-6 and _share(y, 600, 20000) > 0.05


# --- Psych Haze and Sixties Studio --------------------------------

EVENS = list(range(0, 16, 2))


def _kinds(entry: dict) -> list[str]:
    return [u["kind"] for u in entry["main_fx"]]


def test_psych_haze_sounds_do_what_they_are_named_for():
    """Phased, flanged, tape-saturated, chorused leads and bass, heavy
    reverb and delay — each said by the chain, and the ones a number can
    hear measured."""
    bass = get_recipe("Chorus Fuzz Bass")
    assert _kinds(bass) == ["drive", "highpass", "chorus", "phaser"]
    assert _unit(bass, "drive")["params"]["mode"] == 3.0  # tube: tape-warm
    lead = get_recipe("Phased Fuzz Lead")
    assert _unit(lead, "drive")["params"]["mode"] == 2.0  # fuzz
    assert {"flanger", "tape_echo"} <= set(_kinds(lead))
    assert _unit(lead, "tape_echo")["params"]["sync"] == 3.0  # the dotted eighth
    assert recipe_patch(lead).glide > 0 and recipe_patch(lead).vibrato_depth > 0
    pad = get_recipe("Haze Pad")
    assert {"phaser", "chorus", "tape_echo", "reverb", "tremolo"} <= set(_kinds(pad))
    assert _unit(pad, "tremolo")["params"]["phase"] == 180.0  # an auto-pan
    assert _unit(pad, "reverb")["params"]["decay"] >= 4.0  # drowned
    assert extract(stack_audio(pad)).stereo_width > 0.4  # measured 0.66
    keys = get_recipe("Wobble Keys")
    p = recipe_patch(keys)
    assert (p.osc1, p.fm_ratio) == ("fm", 1.0) and p.tremolo_depth > 0
    assert _unit(keys, "tape_echo")["params"]["drive"] >= 0.5  # saturated tape
    assert "not an electric piano" in keys["how"]
    arp = get_recipe("Phase Arp")
    delay = _unit(arp, "delay")["params"]
    assert delay["sync"] == 3.0 and delay["ping_pong"] == 1.0
    assert "phaser" in _kinds(arp)
    swell = get_recipe("Tape Swell")
    ys = [pt[1] for pt in swell["curves"]["cutoff"]]
    assert ys == sorted(ys) and ys[-1] - ys[0] >= 0.5  # it opens
    assert "flanger" in _kinds(swell)
    assert _unit(swell, "tape")["params"]["wow"] > 0  # the tape it swells on


def test_sixties_studio_sounds_do_what_they_are_named_for():
    organ = get_recipe("Rotary Organ")
    assert _kinds(organ) == ["rotary", "plate"]
    p = recipe_patch(organ)
    assert (p.osc1, p.osc2, p.osc2_octave) == ("sine", "sine", 1) and p.sub_level > 0
    assert p.sustain == 1.0  # an organ holds
    strings, flute = get_recipe("Tape Strings"), get_recipe("Tape Flute")
    for e in (strings, flute):
        assert recipe_patch(e).vibrato_depth > 0  # the tape's waver
        assert "plate" in _kinds(e) and "tape-replay keyboard" in e["how"]
        assert _unit(e, "tape")["params"]["flutter"] > 0  # the replay tape
    assert recipe_patch(strings).vibrato_delay == 0.0  # from the first moment
    assert recipe_patch(flute).noise_level > 0  # breath
    assert _unit(flute, "tape_echo")["params"]["time"] <= 0.15  # slapback
    lead = get_recipe("Double Track Lead")
    assert _kinds(lead)[:4] == ["drive", "doubler", "tape_echo", "plate"]
    assert extract(stack_audio(lead)).stereo_width > 0.8  # measured 1.23
    bass = get_recipe("Round Bass")
    assert not {"pump", "sidechain"} & set(_kinds(bass))  # the style tucks it
    cymbal = get_recipe("Backwards Cymbal")
    p = recipe_patch(cymbal)
    assert p.osc1 == "noise" and p.attack >= 1.0 and p.release <= 0.05
    # it grows out of nothing: its last fifth 12 dB over its first (17.6)
    a = stack_audio(cymbal).mean(axis=1)
    n = int(1.2 * SR)  # the attack
    first, last = _rms(a[: n // 5]), _rms(a[n - n // 5 : n])
    assert _db(last / first) > 12.0


@pytest.mark.parametrize("seed", range(6))
def test_the_psych_and_sixties_beats_keep_their_grammar_on_every_roll(seed):
    def gen(style):
        return beatgen.generate(style, seed)

    psych = gen("psych")
    assert {0, 6, 10} <= set(_hits(psych, "kick")) and 4 not in _hits(psych, "kick")
    assert {4, 12} <= set(_hits(psych, "snare"))
    assert set(EVENS) <= set(_hits(psych, "hat"))
    assert beatgen.STYLE_SWING["psych"] >= 0.08  # loose
    feel = beatgen.dress("psych", seed, psych)
    assert feel["swing"] == {"kick": 0.0, "snare": 0.0}  # only the hats lag
    assert feel["hits"]["snare.7"]["v"] <= 0.35  # a ghost before the backbeat
    disco = gen("psych-disco")
    assert _hits(disco, "kick") == BEATS and _hits(disco, "ohat") == OFFBEATS
    haze = gen("psych-haze")
    assert _hits(haze, "snare") == [8] and len(_hits(haze, "tom")) >= 5
    sixties = gen("sixties")
    assert {0, 8} <= set(_hits(sixties, "kick")) and _hits(sixties, "snare") == [4, 12]
    assert _hits(sixties, "hat") == EVENS and _hits(sixties, "ohat") == [4, 12]
    assert beatgen.STYLE_SWING["sixties"] == 0.0  # straight
    assert {2, 3, 10, 11} <= set(_hits(gen("sixties-toms"), "tom"))
    hush = gen("sixties-break")
    assert _hits(hush, "rim") == [4, 12] and _hits(hush, "snare") == []


def test_the_psych_and_sixties_kits_are_their_grammar():
    psych = DRUM_KITS["Psych Haze"]
    steps = psych["pattern"]["steps"]
    assert psych["pattern"]["swing"] >= 0.08
    # two ghost snares, alone on their steps, held down by the lane
    for s in (7, 15):
        assert [v for v, x in steps if x == s] == ["snare"]
        assert psych["vel"][str(s)] <= 0.35
    assert psych["params"]["kick"]["click"] <= 0.3  # round, not clicky
    sixties = DRUM_KITS["Sixties Studio"]
    assert sixties["pattern"]["swing"] == 0.0
    # tight and dry: short kick and snare, a short bright "tambourine" hat
    assert sixties["params"]["kick"]["decay"] <= 0.25
    assert sixties["params"]["snare"]["decay"] <= 0.15
    assert sixties["params"]["ohat"]["decay"] <= 0.15
    on = sorted(x for v, x in sixties["pattern"]["steps"] if v == "ohat")
    assert on == [4, 12]


def test_the_psych_and_sixties_chains_do_what_they_are_named_for():
    wash = chain_preset("phase_wash")
    assert [u["kind"] for u in wash] == ["phaser", "tape_echo", "reverb"]
    assert wash[1]["params"]["sync"] == 3.0 and wash[2]["params"]["decay"] >= 4.0
    haze = chain_preset("tape_haze")
    assert [u["kind"] for u in haze] == ["chorus", "flanger", "drive", "highpass"]
    x = _tone(2.0)
    y = _run(haze, x)
    assert abs(float(y.mean())) / float(np.abs(y).max()) < 0.005  # no offset
    # a mono tone comes out of the double track wide
    double = _run(chain_preset("double_track"), x)
    assert extract(x).stereo_width < 0.01 and extract(double).stereo_width > 0.3
    # one slap ~110 ms behind a click, then the plate
    click = np.zeros((SR, 2))
    click[0] = 1.0
    slap = _run(chain_preset("slap_plate"), click)[:, 0]
    lo, hi = int(0.08 * SR), int(0.2 * SR)
    at = int(np.argmax(np.abs(slap[lo:hi]))) + lo
    assert abs(at / SR - 0.11) < 0.01
    # the rotary room swirls: the level of a steady tone moves
    spun = _run(chain_preset("rotary_room"), _tone(3.0))[SR:, 0]
    w = SR // 50
    env = np.array([_rms(spun[k : k + w]) for k in range(0, len(spun) - w, w)])
    assert _db(env.max() / env.min()) > 1.0


def test_the_modulation_and_tape_chains_do_what_they_are_named_for():
    """The chains that bring the modulation and tape units into the packs:
    the filter-house sweep opens and closes over two bars,
    the psych tape pans, the sixties spring rings on past a click, the
    barber pole moves a tone off its pitch, the robot voice is in key."""
    x = _tone(4.0)
    arr = Arrangement(bpm=120.0)
    # the sweep: the top's share of a bright tone at its trough (bar 1's
    # start) and its crest (bar 2's start: two bars a cycle)
    swept = _run(chain_preset("filter_sweep"), x, arr)[:, 0]
    w = SR // 20

    def top(at: float) -> float:
        k = int(at * SR)
        return _share(swept[k : k + w], 2000.0, 20000.0)

    assert top(2.0) > 4 * top(0.05)
    # the tape's quarter-note pan: the sides take turns
    panned = _run(chain_preset("psych_tape"), x, arr)
    blk = SR // 100
    env = [np.abs(panned[k : k + blk]).max(axis=0) for k in range(SR, 3 * SR, blk)]
    left, right = np.array(env).T
    assert np.corrcoef(left, right)[0, 1] < -0.5
    # the spring: a click still rings 0.5 s on, within 24 dB of its start
    click = np.zeros((2 * SR, 2))
    click[0] = 1.0
    ring = _run(chain_preset("sixties_spring"), click)[:, 0]
    early, late = (_rms(ring[int(t * SR) : int((t + 0.1) * SR)]) for t in (0.03, 0.5))
    assert _db(late / early) > -24.0
    # the barber pole: a 440 Hz sine comes out with 443 Hz beside it
    sine = np.sin(2 * np.pi * 440.0 * np.arange(3 * SR) / SR) * 0.3
    pole = _run(chain_preset("barber_pole"), np.column_stack([sine, sine]))[SR:, 0]
    spec = np.abs(np.fft.rfft(pole * np.hanning(len(pole))))
    f = np.fft.rfftfreq(len(pole), 1 / SR)
    assert spec[np.argmin(np.abs(f - 443.0))] > 0.1 * spec[np.argmin(np.abs(f - 440.0))]
    # the robot voice's carrier is the song's tonic triad
    key = Arrangement(bpm=120.0, root="C3", scale_name="major")
    unit = fxlive.live_chain(chain_preset("robot_voice"), key, False)[0]
    assert unit["notes"] == pytest.approx([130.81, 164.81, 196.0], abs=0.01)


# --- garage, techno, house, electro, trance: the shared sounds --------------


def _centroid(x) -> float:
    m = x.mean(axis=1) if x.ndim == 2 else x
    p = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    f = np.fft.rfftfreq(len(m), 1 / SR)
    return float((f * p).sum() / max(p.sum(), 1e-30))


def test_melodic_techno_and_trance_sounds_do_what_they_are_named_for():
    reese = get_recipe("Reese Bass")
    p = recipe_patch(reese)
    assert (p.osc1, p.osc2, p.osc2_octave) == ("saw", "saw", 0)
    assert p.osc2_detune >= 10.0  # the two saws phase against each other
    assert p.filter_lfo_depth > 0 and p.filter_lfo_rate < 0.5  # a slow mover
    assert "pump" in _kinds(reese)
    arp = get_recipe("Dark Arp Pluck")
    delay = _unit(arp, "delay")["params"]
    assert delay["sync"] == 3.0 and delay["ping_pong"] == 1.0  # dotted, side to side
    assert recipe_patch(arp).osc1 == "fm" and recipe_patch(arp).fm_decay < 0.15
    choir = get_recipe("Cinematic Choir Lead")
    p = recipe_patch(choir)
    assert p.formant_amount > 0.5 and p.vibrato_depth > 0 and p.glide > 0
    ys = [pt[1] for pt in choir["curves"]["formant_vowel"]]
    assert ys[-1] - ys[0] >= 0.3  # the vowel moves across the phrase
    assert _unit(choir, "reverb")["params"]["decay"] >= 5.0
    # what the gated choir pad does not: no gate, a line that glides
    assert "gate" not in _kinds(choir)
    pluck = get_recipe("Trance Pluck Lead")
    p = recipe_patch(pluck)
    assert p.osc1 == "supersaw" and p.filter_env > 2 * p.cutoff  # it snaps open
    assert p.sustain <= 0.3  # and falls back: a pluck-lead
    delay = _unit(pluck, "delay")["params"]
    assert delay["sync"] == 3.0 and delay["ping_pong"] == 1.0
    pad = get_recipe("Big Room Pad")
    assert pad["main_fx"] == chain_preset("big_room_hall")
    assert extract(stack_audio(pad)).stereo_width > 0.9  # measured 1.36


def test_house_and_garage_sounds_do_what_they_are_named_for():
    strings = get_recipe("String Machine")
    rates = [u["params"]["rate"] for u in strings["main_fx"] if u["kind"] == "chorus"]
    assert len(rates) == 2 and max(rates) >= 3 * min(rates)  # the ensemble
    p = recipe_patch(strings)
    assert (p.osc1, p.osc2) == ("saw", "square") and p.pwm_depth > 0
    funk = get_recipe("Envelope Funk Bass")
    env = _unit(funk, "autofilter")["params"]
    assert env["env"] == 1.0 and env["mode"] == 0.0  # a low-pass the input opens
    assert _unit(funk, "pump")["params"]["depth"] >= 0.8
    # the harder the note, the brighter: the same note 12 dB down is darker
    arr = Arrangement(bpm=funk["bpm"])
    note = np.asarray(Synth(at_bpm(recipe_patch(funk), funk["bpm"])).note("A1", 0.5))
    chain = build_chain([_unit(funk, "autofilter")], arr)
    loud = fx_chain(np.column_stack([note, note]), chain)
    soft = fx_chain(np.column_stack([note, note]) * 0.25, chain)
    w = slice(int(0.02 * SR), int(0.15 * SR))
    assert _centroid(loud[w]) > 1.5 * _centroid(soft[w])
    keys = get_recipe("Felt Piano Keys")
    p = recipe_patch(keys)
    assert p.osc1 == "pluck" and p.unison == 2 and p.sustain == 0.0
    assert "Not a sampled piano" in keys["how"]
    assert "tape" in _kinds(keys) and not set(_kinds(keys)) & {"vinyl", "compress"}
    vox = get_recipe("Squashed Vocal Pad")
    assert recipe_patch(vox).formant_amount > 0.5 and "ott" in _kinds(vox)
    # the squash lifts the release tail against the note: the tail's level
    # under the note's, with and without the OTT
    a = stack_audio(vox).mean(axis=1)
    bare = copy.deepcopy(vox)
    bare["main_fx"] = [u for u in bare["main_fx"] if u["kind"] != "ott"]
    b = stack_audio(bare).mean(axis=1)
    off = int((recipe_patch(vox).attack + recipe_patch(vox).decay + 0.25) * SR)

    def tail(x):
        return _db(_rms(x[off + SR // 2 : off + SR]) / _rms(x[SR // 2 : off]))

    assert tail(a) - tail(b) > 1.5  # measured 2.5 dB (the room after it rings on)


def test_the_drop_fx_do_what_they_are_named_for():
    impact = get_recipe("Impact")
    p = recipe_patch(impact)
    assert p.osc1 == "sine" and p.pitch_env >= 12.0 and p.pitch_env_decay <= 0.2
    # it lands: the boom's first 40 ms cross zero half as often again as
    # where it settles (the dry patch: 11 against 6)
    boom = np.asarray(Synth(p).note("C2", 1.0), dtype=float)
    boom = boom.mean(axis=1) if boom.ndim == 2 else boom

    def crossings(seg) -> int:
        return int(np.sum(np.abs(np.diff(np.sign(seg))) > 0))

    k = int(0.04 * SR)
    assert crossings(boom[:k]) > 1.5 * crossings(
        boom[int(0.5 * SR) : int(0.5 * SR) + k]
    )
    assert [lay["role"] for lay in impact["layers"]] == ["air"]  # the crash
    sub = get_recipe("Sub Drop")
    p = recipe_patch(sub)
    assert p.pitch_env > 0 and p.bend_range == 12.0
    ys = [pt[1] for pt in sub["curves"]["pitch_bend"]]
    assert ys[0] == 0.5 and ys[-1] < ys[0]  # on down across the phrase
    m = stack_audio(sub)[:, 0]
    early, late = m[: SR // 4], m[2 * SR : 2 * SR + SR // 4]
    assert crossings(early) > 1.5 * crossings(late)  # falling all the way
    down = get_recipe("Noise Downlifter")
    ys = [pt[1] for pt in down["curves"]["cutoff"]]
    assert ys == sorted(ys, reverse=True) and ys[0] - ys[-1] >= 0.5
    rising = get_recipe("Rising Air")["curves"]["cutoff"]
    assert [pt[1] for pt in rising][::-1] == ys  # Rising Air's, mirrored
    d = stack_audio(down)
    # it falls: 13.4 kHz of centroid in its first half second, 7.7 two s on
    assert _centroid(d[: SR // 2]) > 1.5 * _centroid(d[2 * SR : 2 * SR + SR // 2])
    crash = get_recipe("Reverse Crash")
    p, cym = recipe_patch(crash), recipe_patch(get_recipe("Backwards Cymbal"))
    assert p.attack > cym.attack and p.release <= 0.05  # longer, stops dead
    c = stack_audio(crash).mean(axis=1)
    n = int(p.attack * SR)
    assert _db(_rms(c[n - n // 5 : n]) / _rms(c[: n // 5])) > 12.0  # it swells
    for e in (impact, sub, down, crash):
        assert e["tags"] == ["fx", "drops"]


def test_the_dynamics_chains_do_what_they_are_named_for():
    """OTT Squash pulls a quiet passage toward a loud one; Big Room Hall
    widens and keeps the lows out of its room; Ducked Verb's room dips on
    every beat; Drum Punch holds a ceiling; Master Glue's limiter holds
    -1 dBFS whatever goes in."""
    arr = Arrangement(bpm=120.0)
    t = np.arange(2 * SR) / SR
    hits = 0.6 * np.sin(2 * np.pi * 180.0 * t) * np.exp(-((t % 0.5) / 0.08))
    x = np.column_stack([hits, hits])
    # a second at -6 dBFS, a second at -46: 40 dB apart, 25 after the squash
    # (its last quarter, once the followers have let go; measured 24.4)
    amp = np.where(np.arange(2 * SR) < SR, 10 ** (-6 / 20), 10 ** (-46 / 20))
    tone = np.sin(2 * np.pi * 500.0 * t) * amp
    sq = _run(chain_preset("ott_squash"), np.column_stack([tone, tone]), arr)[:, 0]
    loud = _db(np.abs(sq[3 * SR // 4 : SR]).max())
    quiet = _db(np.abs(sq[7 * SR // 4 :]).max())
    assert loud - quiet < 30.0
    hall = chain_preset("big_room_hall")
    assert [u["kind"] for u in hall] == ["highpass", "reverb", "eq", "width"]
    assert hall[1]["params"]["decay"] >= 5.0 and hall[3]["params"]["amount"] > 1.6
    tone = _tone(2.0)
    wide = _run(hall, tone, arr)
    assert extract(wide).stereo_width > 0.3 and _share(wide, 0.0, 150.0) < 0.05
    verb = _run(chain_preset("ducked_verb"), _tone(3.0), arr)
    beat = SR // 2  # 120 bpm
    on = _rms(verb[2 * beat : 2 * beat + beat // 8])
    off = _rms(verb[2 * beat + beat // 2 : 3 * beat - beat // 16])
    assert _db(on / off) < -6.0
    punch = _run(chain_preset("drum_punch"), x * 2.0, arr)[:, 0]
    assert np.abs(punch).max() <= 10 ** (-1 / 20) + 1e-9
    assert [u["kind"] for u in chain_preset("drum_punch")] == [
        "transient",
        "compress",
        "clip",
    ]
    glue = chain_preset("master_glue")
    assert [u["kind"] for u in glue] == ["eq", "ott", "limiter"]
    loud = _run(glue, x * 1.5, arr)
    assert np.abs(loud).max() <= 10 ** (-1 / 20) * (1 + 1e-9)


# --- hip-hop / trap, drum & bass, dubstep, synthwave, future bass ------------
# mostly without hard sync or 4-op FM; measured table in
# `docs/sounds-and-breeding.md`, "Style packs: hip-hop, drum & bass,
# dubstep, synthwave, future bass"


def _crossings(seg) -> int:
    return int(np.sum(np.abs(np.diff(np.sign(seg))) > 0))


def _dry(entry: dict, pitch: str, dur: float, **tweak) -> np.ndarray:
    p = recipe_patch(entry)
    if tweak:
        p = p.tweak(**tweak)
    note = np.asarray(Synth(at_bpm(p, entry["bpm"])).note(pitch, dur), dtype=float)
    return note.mean(axis=1) if note.ndim == 2 else note


def _centroid_rate(x: np.ndarray) -> float:
    """The rate (Hz) at which a held note's brightness moves: the strongest
    line, 1-20 Hz, in the spectrum of its 10 ms centroids."""
    w = SR // 100
    c = np.array([_centroid(x[k : k + w]) for k in range(0, len(x) - w, w)])
    c = c - c.mean()
    spec = np.abs(np.fft.rfft(c * np.hanning(len(c)), n=8192))
    f = np.fft.rfftfreq(8192, 0.01)
    band = (f >= 1.0) & (f <= 20.0)
    return float(f[band][np.argmax(spec[band])])


def test_hiphop_and_dnb_sounds_do_what_they_are_named_for():
    long808 = get_recipe("808 Long")
    p = recipe_patch(long808)
    assert p.osc1 == "sine" and p.pitch_env > 0 and p.pitch_env_decay <= 0.1
    assert p.glide > 0 and p.decay >= 1.0  # long, and it slides
    # it falls onto its note: the first 20 ms cross zero more often than
    # where it settles (at C5, where 20 ms holds enough cycles to count:
    # 26 against 21)
    thud = _dry(long808, "C5", 1.0)
    k = int(0.02 * SR)
    assert _crossings(thud[:k]) > 1.15 * _crossings(thud[SR // 2 : SR // 2 + k])
    dist = get_recipe("808 Distorted")
    assert dist["main_fx"] == chain_preset("saturated_808")
    # the grit: far more of it over 120 Hz than the long one has
    assert _share(stack_audio(dist), 120.0, 20000.0) > 5 * _share(
        stack_audio(long808), 120.0, 20000.0
    )
    bell = recipe_patch(get_recipe("Dark Bell Keys"))
    assert bell.osc1 == "fm" and bell.fm_ratio % 1 > 0.05 and bell.sustain == 0.0
    choir = get_recipe("Dark Choir Pad")
    assert recipe_patch(choir).formant_amount > 0.5
    assert _unit(choir, "reverb")["params"]["decay"] >= 4.0
    flute = get_recipe("Airy Flute Lead")
    p = recipe_patch(flute)
    assert p.noise_level > 0 and p.vibrato_depth > 0 and "not a flute" in flute["how"]
    neuro = get_recipe("Neuro Reese")
    p, reese = recipe_patch(neuro), recipe_patch(get_recipe("Reese Bass"))
    assert (p.osc1, p.osc2) == ("saw", "saw") and p.osc2_detune > reese.osc2_detune
    assert p.drift > 0  # drift: each note's saws start and wander apart
    assert p.filter_lfo_sync == "1/4" and p.filter_lfo_depth > 0
    assert neuro["main_fx"] == chain_preset("neuro_grind")
    atmos = get_recipe("Atmos Pad")
    assert _unit(atmos, "reverb")["params"]["decay"] >= 5.0
    assert extract(stack_audio(atmos)).stereo_width > 0.9  # measured 1.09
    stab = _unit(get_recipe("Liquid Stab"), "delay")["params"]
    assert stab["sync"] == 3.0 and stab["ping_pong"] == 1.0


def test_the_wobble_moves_on_the_clock_at_any_sync_division():
    """The wobble is the filter LFO on the clock: a held note's brightness
    moves at the eighth (140 bpm: 4.67 Hz), and the same patch at the eighth
    triplet (7 Hz) and the quarter (2.33 Hz) moves at those — the existing
    sync divisions, nothing new."""
    wob = get_recipe("Wobble Bass")
    assert recipe_patch(wob).filter_lfo_sync == "1/8"
    beat = wob["bpm"] / 60.0
    for sync, per_beat in (("1/8", 2.0), ("1/8T", 3.0), ("1/4", 1.0)):
        x = _dry(wob, "A1", 3.0, filter_lfo_sync=sync)[SR // 4 : int(2.75 * SR)]
        assert _centroid_rate(x) == pytest.approx(beat * per_beat, abs=0.25), sync


def test_bass_music_sounds_do_what_they_are_named_for():
    # the growl a 4-operator stack (an octave-down modulator, op 4 feeding
    # back) and the screech hard sync, rather than 2-op FM and cross-FM —
    # measured as well or better
    growl = get_recipe("Growl Bass")
    p = recipe_patch(growl)
    assert p.osc1 == "fm4" and p.fm4_algo == "stack" and p.fm4_ratio2 == 0.5
    assert p.fm4_feedback > 0 and p.formant_amount > 0
    assert p.mod1_src == "lfo" and p.lfo_sync == "1/4"  # the saw steps in and out
    assert growl["main_fx"] == chain_preset("growl_squash")
    screech = get_recipe("Screech Lead")
    p = recipe_patch(screech)
    assert p.osc2_sync == "on" and p.fm_amount == 0
    assert (p.mod1_src, p.mod1_dst, p.lfo_sync) == ("lfo", "sync_pitch", "1/16")
    assert p.filter_type == "bandpass" and p.resonance >= 0.6
    for e in (growl, screech):
        assert "stand" not in e["how"] and "would" not in e["how"]
    chords = get_recipe("Supersaw Chord Stab")
    assert recipe_patch(chords).unison == 7
    assert chords["main_fx"][1:] == chain_preset("future_pump")
    chop = get_recipe("Vocal Chop Lead")
    assert recipe_patch(chop).formant_amount > 0.5
    ys = [pt[1] for pt in chop["curves"]["formant_vowel"]]
    assert ys[-1] - ys[0] >= 0.5  # each hit another vowel
    bent = recipe_patch(get_recipe("Bent Pluck"))
    assert bent.pitch_env < 0  # bent up into the note
    assert (bent.mod1_src, bent.mod1_dst, bent.lfo_sync) == ("lfo", "pitch", "1/16")
    pad = get_recipe("Future Pad")
    assert {"ott", "pump"} <= set(_kinds(pad))


def test_synthwave_sounds_do_what_they_are_named_for():
    pad = get_recipe("Poly Chorus Pad")
    assert _kinds(pad).count("chorus") == 2 and recipe_patch(pad).pwm_depth > 0
    brass = get_recipe("Poly Brass Stab")
    p = recipe_patch(brass)
    assert p.filter_attack >= 0.02 and p.filter_env > 2 * p.cutoff
    assert p.drift == 0  # drift would soften the swell (below)
    # the swell: more over 2 kHz 60 ms in than at the very front (2.7x)
    x = _dry(brass, "C4", 0.5)
    w, at = int(0.02 * SR), int(0.06 * SR)
    assert _share(x[at : at + w], 2000.0, 20000.0) > 2 * _share(x[:w], 2000.0, 20000.0)
    lead = get_recipe("Bright Saw Lead")
    at = _kinds(lead).index("chorus")
    assert lead["main_fx"][at : at + 3] == chain_preset("eighties_space")
    strings, machine = get_recipe("Lush Strings"), get_recipe("String Machine")
    a, b = recipe_patch(strings), recipe_patch(machine)
    assert (a.osc1, a.osc2) == (b.osc1, b.osc2) and a.attack > b.attack

    def rates(e):
        return sorted(
            u["params"]["rate"] for u in e["main_fx"] if u["kind"] == "chorus"
        )

    assert rates(strings) == rates(machine)  # the same ensemble
    assert (
        _unit(strings, "reverb")["params"]["decay"]
        > _unit(machine, "reverb")["params"]["decay"]
    )
    bass = recipe_patch(get_recipe("Eighties Arp Bass"))
    assert bass.decay <= 0.2 and bass.release <= 0.1  # short: it sequences


def _rolled(style: str, seed: int, cells, voice: str) -> set[int]:
    hits = beatgen.dress(style, seed, cells)["hits"]
    return {
        int(k.partition(".")[2])
        for k, h in hits.items()
        if k.partition(".")[0] == voice and h.get("r", 1) >= 2
    }


@pytest.mark.parametrize("seed", range(6))
def test_the_hiphop_to_future_bass_beats_keep_their_grammar_on_every_roll(seed):
    def gen(style):
        return beatgen.generate(style, seed)

    trap = gen("trap-808")
    assert {0, 10} <= set(_hits(trap, "kick"))
    assert _hits(trap, "snare") == _hits(trap, "clap") == [8]  # half time
    assert set(EVENS) <= set(_hits(trap, "hat"))
    assert {6, 14} <= _rolled("trap-808", seed, trap, "hat")  # rolls, every seed
    boom = gen("boom-bap")
    assert _hits(boom, "snare") == [4, 12] and {0, 10} <= set(_hits(boom, "kick"))
    assert beatgen.STYLE_SWING["boom-bap"] >= 0.16  # swung hard
    assert beatgen.dress("boom-bap", seed, boom)["swing"] == {
        "kick": 0.0,
        "snare": 0.0,
    }
    for style in ("dnb", "dnb-roller"):
        two = gen(style)
        assert _hits(two, "kick") == [0, 10] and _hits(two, "snare") == [4, 12]
        assert 0.05 <= beatgen.STYLE_SWING[style] <= 0.1  # shuffled
    assert _hits(gen("dnb-roller"), "hat") == list(range(16))
    jungle = gen("jungle")
    assert {0, 6, 10} <= set(_hits(jungle, "kick"))
    hits = beatgen.dress("jungle", seed, jungle)["hits"]
    soft = [
        k for k, h in hits.items() if k.startswith("snare.") and h.get("v", 1) <= 0.4
    ]
    assert len(soft) >= 3  # the ghosts
    dub = gen("dubstep")
    assert _hits(dub, "kick") == [0, 10] and _hits(dub, "snare") == [8]
    riddim = gen("riddim")
    assert _hits(riddim, "kick") == [0] and len(_hits(riddim, "hat")) == 3
    wave = gen("synthwave")
    assert {0, 8} <= set(_hits(wave, "kick")) and _hits(wave, "snare") == [4, 12]
    assert _hits(wave, "hat") == list(range(16))
    assert beatgen.STYLE_SWING["synthwave"] == 0.0
    pop = gen("synthpop")
    assert _hits(pop, "kick") == BEATS and _hits(pop, "ohat") == OFFBEATS
    fut = gen("future-bass")
    assert _hits(fut, "snare") == [8] and 14 in _rolled("future-bass", seed, fut, "hat")
    build = beatgen.dress("future-build", seed, gen("future-build"))
    tunes = [build["hits"][f"snare.{s}"].get("t", 0.0) for s in EVENS]
    assert tunes == sorted(tunes) and tunes[-1] >= 5.0  # it climbs into the drop
    assert build["hits"]["snare.14"]["r"] == 2


def test_the_rolls_are_drawn_only_by_the_styles_that_have_them():
    """`rolls` and `ratchet` are drawn after every older key, so a style
    with neither dresses as it did: no ratchet but a build's."""
    for style in beatgen.STYLES:
        feel = beatgen.STYLE_FEEL.get(style, {})
        if "rolls" in feel or "ratchet" in feel or feel.get("roll"):
            continue
        for seed in range(3):
            got = beatgen.dress(style, seed, beatgen.generate(style, seed))
            assert all(h.get("r", 1) == 1 for h in got["hits"].values()), style


def test_the_hiphop_to_future_bass_kits_are_their_grammar():
    trap = DRUM_KITS["Hip-Hop 808"]["pattern"]["steps"]
    assert _hits(trap, "snare") == _hits(trap, "clap") == [8]
    assert _hits(trap, "kick") == [0, 10]
    dnb = DRUM_KITS["Drum and Bass"]
    steps = dnb["pattern"]["steps"]
    assert _hits(steps, "kick") == [0, 10] and {4, 12} <= set(_hits(steps, "snare"))
    assert [v for v, s in steps if s == 7] == ["snare"] and dnb["vel"]["7"] <= 0.35
    assert dnb["params"]["snare"]["decay"] <= 0.15  # tight
    dub = DRUM_KITS["Dubstep"]
    assert _hits(dub["pattern"]["steps"], "snare") == [8]
    assert dub["params"]["snare"]["decay"] >= 0.35  # huge
    wave = DRUM_KITS["Synthwave"]
    assert _hits(wave["pattern"]["steps"], "snare") == [4, 12]
    assert all(wave["vel"][str(s)] < 0.7 for s in range(1, 16, 2))  # accents
    assert _hits(DRUM_KITS["Future Bass"]["pattern"]["steps"], "snare") == [8]


@pytest.mark.parametrize(
    "name",
    [
        "saturated_808",
        "neuro_grind",
        "growl_squash",
        # hardstyle and phonk
        "hard_kick_drive",
        "phonk_crush",
    ],
)
def test_the_dirt_chains_hold_the_ceiling_with_no_offset(name):
    t = np.arange(SR) / SR
    tone = 0.9 * np.sin(2 * np.pi * 110 * t) + 0.3 * np.sin(2 * np.pi * 220 * t)
    x = np.stack([tone, tone], axis=1)
    y = fx_chain(x, build_chain(chain_preset(name), Arrangement()))
    assert np.max(np.abs(y)) <= 10 ** (-1 / 20) + 1e-9
    assert abs(float(y.mean())) / float(np.max(np.abs(y))) < 0.005
    # (300 Hz: the 808's tube makes mostly low harmonics — 0.10 over 300 Hz,
    # 0.02 over 600)
    assert _share(x, 300, 20000) < 1e-6 and _share(y, 300, 20000) > 0.05


def test_the_hiphop_to_future_bass_chains_do_what_they_are_named_for():
    """Gated Verb's room is cut dead where the room alone rings on; Boom
    Bap Crunch holds a ceiling; Eighties Space is a chorus, a dotted echo
    and a room; Future Pump ducks on every beat."""
    arr = Arrangement(bpm=100.0)
    n = int(0.08 * SR)
    burst = np.random.default_rng(5).standard_normal(n)
    hit = np.zeros((2 * SR, 2))
    hit[:n] = (0.8 * burst * np.exp(-np.arange(n) / (0.02 * SR)))[:, None]
    gated = chain_preset("gated_verb")
    assert [u["kind"] for u in gated] == ["reverb", "vgate"]
    room, cut = _run(gated[:1], hit, arr), _run(gated, hit, arr)
    # a burst at -2 dBFS: the room passes whole for 100 ms, is 15 dB down
    # by 200-300 ms and 56 dB down by 300-500 (the room alone: -30 dBFS)
    front, tail = slice(0, int(0.1 * SR)), slice(int(0.3 * SR), int(0.5 * SR))
    assert _rms(cut[front]) > 0.9 * _rms(room[front])  # the hit and its burst
    assert _rms(cut[tail]) < 0.01 * _rms(room[tail])  # the tail: gone
    crunch = chain_preset("boom_bap_crunch")
    assert [u["kind"] for u in crunch] == ["bitcrush", "lowpass", "transient", "clip"]
    y = _run(crunch, _tone(1.0, 440.0) * 4.0, arr)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9
    space = chain_preset("eighties_space")
    assert [u["kind"] for u in space] == ["chorus", "delay", "reverb"]
    assert space[1]["params"]["sync"] == 3.0 and space[1]["params"]["ping_pong"] == 1.0
    pump = chain_preset("future_pump")
    assert [u["kind"] for u in pump] == ["ott", "reverb", "pump", "width"]
    y = _run(pump, _tone(3.0), Arrangement(bpm=120.0))
    beat = SR // 2
    on = _rms(y[2 * beat : 2 * beat + beat // 8])
    off = _rms(y[2 * beat + beat // 2 : 3 * beat - beat // 16])
    assert _db(on / off) < -6.0


# --- lo-fi hip-hop, ambient / cinematic, chiptune, hardstyle, phonk ----------
# on hard sync, fm4, the newer wavetables and drift; measured table in
# `docs/sounds-and-breeding.md`, "Style packs: lo-fi, ambient, chiptune,
# hardstyle, phonk"


def _in_order(entry: dict, chain: str) -> bool:
    """The chain preset's units appear, in order, in the recipe's chain."""
    want, have = chain_preset(chain), entry["main_fx"]
    return any(have[i : i + len(want)] == want for i in range(len(have)))


def test_lofi_and_ambient_sounds_do_what_they_are_named_for():
    keys = get_recipe("Dusty Keys")
    p = recipe_patch(keys)
    # the e-piano's halves: a body pair, a high tine gone in a tenth
    assert p.osc1 == "fm4" and p.fm4_algo == "pairs"
    assert p.fm4_ratio4 >= 10 and p.fm4_decay4 <= 0.15 and p.drift > 0
    assert _in_order(keys, "dusty_tape") and "not an electric piano" in keys["how"]
    tape = _unit(keys, "tape")["params"]
    assert tape["wow"] > 0 and tape["flutter"] > 0 and tape["hiss"] > 0
    pad = get_recipe("Jazz Chord Pad")
    assert recipe_patch(pad).drift > 0 and _in_order(pad, "dusty_tape")
    assert recipe_patch(get_recipe("Soft Sub")).attack >= 0.01  # no click
    pluck = recipe_patch(get_recipe("Muted Pluck"))
    assert pluck.osc1 == "pluck" and pluck.pluck_damping >= 0.5
    # the scanning pad's band of harmonics climbs across the phrase: its
    # brightness halfway through is over its brightness as it swells in
    # (measured 709 -> 1017 Hz)
    scan = get_recipe("Scanning Pad")
    p = recipe_patch(scan)
    assert (p.osc1, p.wt_table) == ("wavetable", "scan") and p.drift > 0
    ys = [pt[1] for pt in scan["curves"]["wt_pos"]]
    assert ys[-1] - ys[0] >= 0.5
    a = stack_audio(scan)
    early, mid = a[int(1.5 * SR) : int(2.5 * SR)], a[len(a) // 2 : len(a) // 2 + SR]
    assert _centroid(mid) > 1.2 * _centroid(early)
    vowel = get_recipe("Vowel Drift Pad")
    assert recipe_patch(vowel).wt_table == "formant"
    assert [pt[1] for pt in vowel["curves"]["wt_pos"]] == [0.0, 1.0]  # a to u
    assert get_recipe("Shimmer Bed")["main_fx"] == chain_preset("shimmer_wash")
    bell = recipe_patch(get_recipe("Glass Bell Texture"))
    assert bell.osc1 == "fm4" and bell.sustain == 0.0
    assert any(getattr(bell, f"fm4_ratio{k}") % 1 > 0.05 for k in range(1, 5))
    drone = recipe_patch(get_recipe("Deep Drone"))
    assert drone.drift >= 0.5 and drone.attack >= 1.5 and drone.sustain == 1.0
    braam = recipe_patch(get_recipe("Cinematic Braam"))
    assert braam.filter_attack >= 0.05 and braam.filter_env > 0
    assert braam.sustain == 0.0 and braam.osc2_octave == -1


def test_chiptune_hardstyle_and_phonk_sounds_do_what_they_are_named_for():
    # the three duties, read off the wave: the fraction of each cycle it
    # sits high (measured 0.50 / 0.30 / 0.15 — the pulse table's frames,
    # the nearest it holds to the console's 25 and 12.5 %)
    duty = []
    for name in ("Square Lead", "Pulse Lead", "Thin Pulse Arp"):
        x = _dry(get_recipe(name), "C4", 0.5)[int(0.05 * SR) : int(0.08 * SR)]
        duty.append(float(np.mean(x - x.mean() > 0)))
        assert recipe_patch(get_recipe(name)).filter_type == "none"
    assert duty == pytest.approx([0.5, 0.3, 0.15], abs=0.02)
    tri = get_recipe("Triangle Bass")
    assert recipe_patch(tri).osc1 == "triangle"
    assert _unit(tri, "bitcrush")["params"]["bits"] == 4.0  # sixteen steps
    crush = get_recipe("Crush Lead")
    assert recipe_patch(crush).wt_table == "crush"
    assert crush["main_fx"] == chain_preset("chip_crush")
    # the screech: hard sync, its pitch swept on the sixteenth (150 bpm:
    # 10 Hz, the brightness moving at exactly that)
    screech = get_recipe("Sync Screech")
    p = recipe_patch(screech)
    assert p.osc2_sync == "on" and p.mod1_dst == "sync_pitch"
    x = _dry(screech, "C4", 3.0)[SR // 4 : int(2.75 * SR)]
    assert _centroid_rate(x) == pytest.approx(screech["bpm"] / 60 * 4, abs=0.25)
    saw = recipe_patch(get_recipe("Euphoric Supersaw"))
    assert saw.osc1 == "supersaw" and saw.unison == 7
    # the reverse bass swells into the next kick: 120 ms in it is 14.5 dB
    # over its first 20 ms
    rev = _dry(get_recipe("Reverse Bass"), "A1", 0.5)
    front, swelled = rev[: int(0.02 * SR)], rev[int(0.12 * SR) : int(0.14 * SR)]
    assert _db(_rms(swelled) / _rms(front)) > 10.0
    assert "pump" in _kinds(get_recipe("Euphoric Pad"))
    # the cowbell: its two tones (1 : 1.48) within 6 dB of the loudest
    # partial (measured: the high one loudest, the low 4 dB under)
    bell = get_recipe("Phonk Cowbell")
    p = recipe_patch(bell)
    assert p.osc1 == "fm4" and p.fm4_ratio3 / p.fm4_ratio1 == pytest.approx(1.48)
    x = _dry(bell, "C5", 0.3)
    m = int(0.15 * SR)
    spec = np.abs(np.fft.rfft(x[:m] * np.hanning(m)))
    f = np.fft.rfftfreq(m, 1 / SR)
    c5 = 523.25

    def near(hz):
        return spec[(f > hz * 0.96) & (f < hz * 1.04)].max()

    assert _db(near(c5) / spec.max()) > -6 and _db(near(1.48 * c5) / spec.max()) > -6
    e808 = get_recipe("Phonk 808")
    assert e808["main_fx"] == chain_preset("phonk_crush")
    p = recipe_patch(e808)
    assert p.osc1 == "sine" and p.pitch_env > 0 and p.glide > 0
    assert _share(stack_audio(e808), 120.0, 20000.0) > 5 * _share(
        stack_audio(get_recipe("808 Long")), 120.0, 20000.0
    )
    pad = recipe_patch(get_recipe("Dark Vowel Pad"))
    assert pad.wt_table == "formant" and pad.cutoff <= 2500 and pad.drift > 0
    assert recipe_patch(get_recipe("Hard Phonk Lead")).osc2_sync == "on"


@pytest.mark.parametrize("seed", range(6))
def test_the_lofi_to_phonk_beats_keep_their_grammar_on_every_roll(seed):
    def gen(style):
        return beatgen.generate(style, seed)

    lofi = gen("lofi")
    assert {0, 10} <= set(_hits(lofi, "kick")) and _hits(lofi, "snare") == [4, 12]
    feel = beatgen.dress("lofi", seed, lofi)
    assert feel["swing"] == {"kick": 0.0, "snare": 0.0}  # the hats lag alone
    assert feel["hits"]["kick.7"]["v"] <= 0.5  # the ghost kick
    assert beatgen.STYLE_SWING["lofi"] >= 0.2
    chill = gen("lofi-chill")
    assert _hits(chill, "rim") == [4] and _hits(chill, "snare") == [12]
    pulse = beatgen.dress("ambient-pulse", seed, gen("ambient-pulse"))
    assert [s for v, s in pulse["drums"] if v == "kick"] == [0]
    # nothing but the kick is certain
    assert all(
        pulse["hits"][f"{v}.{s}"]["p"] < 1.0 for v, s in pulse["drums"] if v != "kick"
    )
    toms = gen("cinematic-toms")
    assert len(_hits(toms, "tom")) >= 9 and {12, 13, 14, 15} <= set(_hits(toms, "tom"))
    chip = gen("chiptune")
    assert _hits(chip, "snare") == [4, 12] and {0, 8} <= set(_hits(chip, "kick"))
    assert beatgen.STYLE_SWING["chiptune"] == 0.0
    chase = gen("chip-chase")
    assert _hits(chase, "kick") == BEATS and _hits(chase, "hat") == list(range(16))
    hard = gen("hardstyle")
    assert _hits(hard, "kick") == BEATS and _hits(hard, "clap") == [4, 12]
    assert _hits(hard, "ohat") == OFFBEATS
    raw = gen("rawstyle")
    assert _hits(raw, "kick") == BEATS and _hits(raw, "clap") == [12]
    drift = gen("drift-phonk")
    assert _hits(drift, "clap") == [4, 12] and _hits(drift, "hat") == list(range(16))
    assert 14 in _rolled("drift-phonk", seed, drift, "hat")
    memphis = gen("memphis")
    assert {0, 6, 10} <= set(_hits(memphis, "kick"))
    assert {6, 14} <= _rolled("memphis", seed, memphis, "hat")


def test_the_lofi_to_phonk_kits_are_their_grammar():
    lofi = DRUM_KITS["Lo-Fi"]
    assert lofi["pattern"]["swing"] >= 0.2
    steps = lofi["pattern"]["steps"]
    assert [v for v, s in steps if s == 7] == ["kick"] and lofi["vel"]["7"] <= 0.5
    assert lofi["params"]["snare"]["tone"] <= 0.3  # brushed: little body
    film = DRUM_KITS["Cinematic"]["params"]
    assert film["tom"]["tune"] <= 90 and film["tom"]["decay"] >= 0.6  # taiko-ish
    assert film["crash"]["decay"] >= 2.5 and film["kick"]["decay"] >= 0.9
    chip = DRUM_KITS["Chiptune"]["params"]
    assert chip["snare"]["tone"] == 0.0  # all noise
    hard = DRUM_KITS["Hardstyle"]
    assert _hits(hard["pattern"]["steps"], "kick") == BEATS
    assert (
        hard["params"]["kick"]["decay"] >= 0.6
        and hard["params"]["kick"]["drive"] >= 3.5
    )
    phonk = DRUM_KITS["Phonk"]
    assert phonk["params"]["rim"]["tune"] >= 500  # the cowbell
    assert _hits(phonk["pattern"]["steps"], "rim")
    assert _hits(phonk["pattern"]["steps"], "clap") == [4, 12]


def test_the_lofi_to_phonk_chains_do_what_they_are_named_for():
    """Dusty Tape wears (wow, flutter, hiss, the top, a few bits); Huge
    Hall rings 2 s on at -26 dB where a 2 s room is at -54; Shimmer Wash
    puts an octave over what it is given; 8-Bit Crush is six bits at a
    third of the rate under the ceiling."""
    arr = Arrangement(bpm=70.0)
    tape = chain_preset("dusty_tape")
    assert [u["kind"] for u in tape] == ["tape", "lowpass", "bitcrush"]
    assert tape[0]["params"]["wow"] > 0 and tape[0]["params"]["hiss"] > 0
    n = int(0.05 * SR)
    burst = np.zeros((4 * SR, 2))
    burst[:n] = (0.8 * np.random.default_rng(3).standard_normal(n))[:, None]
    hall = _run(chain_preset("huge_hall"), burst, arr)
    room = _run(
        [{"kind": "reverb", "params": {"decay": 2.0, "mix": 0.5, "size": 0.8}}],
        burst,
        arr,
    )
    tail = slice(2 * SR, int(2.5 * SR))
    assert _db(_rms(hall[tail]) / _rms(hall[:n])) > -35.0
    assert _rms(hall[tail]) > 10 * _rms(room[tail])  # 28 dB over it
    t = np.arange(SR) / SR
    tone = np.zeros((4 * SR, 2))
    tone[:SR] = (0.5 * np.sin(2 * np.pi * 220 * t))[:, None]
    shimmer = chain_preset("shimmer_wash")
    assert [u["kind"] for u in shimmer] == ["highpass", "shimmer", "width"]
    wash = _run(shimmer, tone, arr)
    assert _share(tone, 420, 460) < 1e-3 and _share(wash, 420, 460) > 0.03
    crush = chain_preset("chip_crush")
    assert crush[0]["params"] == {"bits": 6.0, "downsample": 3.0}
    y = _run(crush, _tone(1.0, 440.0) * 4.0, arr)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9
