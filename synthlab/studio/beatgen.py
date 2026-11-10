"""Styled, seeded drum-pattern generation for the pattern bank.

Every style is a table of per-voice recipes written in 16ths for one bar:
``forced`` steps always hit, an optional Euclidean layer adds evenly
spaced pulses, and ``prob`` sprinkles extra hits. Patterns are scaled to
the arrangement's ``steps_per_bar`` so a 4-step grid gets beats, not
noise. Deterministic for a seed (tests pin this) — the bank's Randomise
button shows the seed so a good roll can be re-rolled.

A style is written in ONE meter's bar (`STYLE_METER`; 4/4 unless it says
otherwise: 16 sixteenths, the waltz's 12, the 7/8's 14), and generated
onto a grid in a meter of its own (`meter`): a bar keeps where each hit
sits in it, so a 4/4 style on a 3/4 grid keeps its first three beats and
a 3/4 style on a 4/4 grid leaves the fourth empty — as
`song_model.fit_pattern` fits a pattern. In 4/4, onto 4/4, every style
draws exactly what it always did.
"""

from __future__ import annotations

import random
import zlib

from ..sequencer import euclid
from . import meter as _meter
from .state import DRUM_ROWS

# voice -> (forced 16th steps, (euclid hits, rotate) | None, extra-hit probability)
_STYLES: dict[str, dict[str, tuple[tuple[int, ...], tuple[int, int] | None, float]]] = {
    "four-floor": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.25),
        "snare": ((), None, 0.05),
    },
    "techno": {
        "kick": ((0, 4, 8, 12), None, 0.08),
        "hat": ((), (8, 1), 0.3),
        "clap": ((4, 12), None, 0.1),
        "snare": ((), None, 0.08),
    },
    "breaks": {
        "kick": ((0, 10), (3, 0), 0.1),
        "snare": ((4, 12), None, 0.12),
        "hat": ((), (6, 0), 0.3),
        "clap": ((), None, 0.05),
    },
    "trap": {
        "kick": ((0,), (3, 0), 0.12),
        "snare": ((8,), None, 0.0),
        "hat": ((), (8, 0), 0.45),
        "clap": ((8,), None, 0.05),
    },
    "sparse": {
        "kick": ((0,), None, 0.12),
        "snare": ((8,), None, 0.06),
        "hat": ((), (3, 1), 0.0),
        "clap": ((), None, 0.04),
    },
    # ~125 BPM, straight: four on the floor, closed hats rolling on every
    # 16th between the kicks, the open hat on each offbeat eighth (the
    # next closed hat chokes it to a 16th "tss"), a sparse clap and rim
    "melodic-techno": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "hat": ((1, 3, 5, 7, 9, 11, 13, 15), None, 0.25),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "clap": ((4, 12), None, 0.06),
        "rim": ((), None, 0.07),
    },
    # 2-step: the kick on 1 and the "and" of 3 (skipping beat 3), clap on
    # 2 and 4, a rimshot on every offbeat eighth, closed hats sprinkled on
    # the off-16ths, swung (STYLE_SWING) — the ghost kick's quiet 16th is
    # a velocity, which a kit (drumfactory.DRUM_KITS) carries, not a style
    "garage-rim": {
        "kick": ((0, 10), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "rim": ((2, 6, 10, 14), None, 0.0),
        "hat": ((1, 5, 9, 13), None, 0.3),
    },
    # --- the four style packs. Each is what the drum model can
    # say today: a step either hits or does not, a velocity is a step's
    # (a kit carries it, not a style), swing is the whole kit's and moves
    # the odd 16ths. Per-hit velocity, chance, ratchets and a swing per row
    # are layered on separately; the notes say where they land.
    #
    # ~122 BPM: kick on every beat, a clap with a snare under it on 2 and
    # 4, a shaker-like hat on every 16th, an open hat on each offbeat
    # eighth; a rim and a spare clap sprinkled in
    "filter-house": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.04),
        "snare": ((4, 12), None, 0.0),
        "hat": (tuple(range(16)), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "rim": ((), None, 0.08),
    },
    # the same kit with the kick taken down to the bar's downbeat (a style
    # always writes one kick; a bar with none is an arrangement's doing):
    # hats, the open hat and the clap carry it
    "filter-house-break": {
        "kick": ((0,), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": (tuple(range(16)), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "rim": ((), None, 0.1),
    },
    # 2-step for the verses: the kick on 1 and the "and" of 3 (never on 2
    # or 4), a clap on 2 and 4, hats on the offbeat eighths with swung
    # 16ths sprinkled between (STYLE_SWING), a rim and a snare now and
    # then — ghosts once a hit can be quiet on its own (the Two-Step kit
    # keeps three of them alone on their steps for that)
    "two-step": {
        "kick": ((0, 10), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": ((2, 6, 14), None, 0.25),
        "rim": ((), None, 0.08),
        "snare": ((), None, 0.05),
    },
    # the drops' hybrid: four on the floor under the same swung hats and
    # clap, a rim on the 16th before beats 2 and 4
    "garage-4x4": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.3),
        "rim": ((3, 11), None, 0.05),
        "snare": ((), None, 0.05),
    },
    # melodic techno's percussion bed: kick on every beat, an offbeat hat,
    # a clap on 4 alone, and a rim (3 in 16) and a tom (5 in 16, turned)
    # in Euclidean figures. The cells are certain; each rim and tom hit's
    # CHANCE is the style's feel (`STYLE_FEEL`, `dress`), so over a pattern
    # of several bars the figure never quite repeats
    "hypnotic-perc": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.0),
        "clap": ((12,), None, 0.0),
        "rim": ((), (3, 0), 0.05),
        "tom": ((), (5, 2), 0.05),
    },
    # ~138 BPM, straight: kick on every beat, clap and snare stacked on 2
    # and 4, an open hat on every offbeat eighth, closed hats on the other
    # 16ths (the one after each open hat chokes it). The grid is the genre:
    # a re-roll only moves a sparse rim
    "trance": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "hat": ((0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 15), None, 0.0),
        "rim": ((), None, 0.06),
    },
    # the climax: the same, with the ride (the kit's shortened crash) in
    # eighths
    "trance-climax": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "hat": ((0, 1, 3, 4, 5, 7, 8, 9, 11, 12, 13, 15), None, 0.0),
        "rim": ((), None, 0.06),
        "crash": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.0),
    },
    # an intro or a way out: the kick and the hats alone, a clap on 4
    "trance-break": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "hat": ((1, 3, 5, 7, 9, 11, 13, 15), None, 0.1),
        "clap": ((12,), None, 0.0),
    },
    # --- Just Ice (French electro, ~120): a driven four on the
    # floor with a pushed kick before the bar line, the snare AND the clap
    # cracking on 2 and 4, closed hats on every off-16th, the open hat on
    # the offbeat eighths; a sprinkle of extra kicks keeps it lurching
    "electro": {
        "kick": ((0, 4, 8, 12, 14), None, 0.04),
        "snare": ((4, 12), None, 0.0),
        "clap": ((4, 12), None, 0.04),
        "hat": ((1, 3, 5, 7, 9, 11, 13, 15), None, 0.2),
        "ohat": ((2, 6, 10, 14), None, 0.0),
    },
    # the stomp: half-time — the kick on 1 and the "and" of 2, one huge
    # snare + clap on 3, the hats in eighths — for the heavy middle
    "electro-stomp": {
        "kick": ((0, 6), None, 0.0),
        "snare": ((8,), None, 0.0),
        "clap": ((8,), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.1),
        "crash": ((), None, 0.0),
    },
    # in and out: the kick and the hats alone, a clap on 4
    "electro-break": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.15),
        "clap": ((12,), None, 0.0),
    },
    # --- Nine Inch Wails (industrial, ~105): machine-driven and lopsided —
    # the kick on 1, the "a" of 1 and 3, a cracking snare on 2 and 4, metal
    # (the tom, tuned high and short) on a Euclidean 5 against the 16ths,
    # the rim clanking on the off-beats, the hats raw and sparse
    "industrial": {
        "kick": ((0, 3, 8), None, 0.1),
        "snare": ((4, 12), None, 0.04),
        "tom": ((), (5, 2), 0.0),
        "rim": ((2, 10), None, 0.12),
        "hat": ((), (7, 0), 0.25),
    },
    # the march: every beat a kick, the snare doubled on 4, metal on the
    # offbeats — the loud chorus of a song that was quiet before
    "industrial-march": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": ((4, 12, 14), None, 0.0),
        "tom": ((2, 6, 10, 14), None, 0.0),
        "hat": ((), (11, 0), 0.2),
        "crash": ((0,), None, 0.0),
    },
    # the score: a heartbeat kick and a ticking rim under a piano or a drone
    "industrial-pulse": {
        "kick": ((0, 10), None, 0.0),
        "rim": ((), (3, 1), 0.08),
    },
    # --- Psych Haze (neo-psych, ~112): a drummer's loose groove
    # — the kick on 1 and the "and" of 2 and 3, the snare on 2 and 4, the
    # hats in eighths with an off-16th now and then (swung: STYLE_SWING), an
    # open hat sprinkled in; the ghost snares are the feel's
    "psych": {
        "kick": ((0, 6, 10), None, 0.06),
        "snare": ((4, 12), None, 0.03),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.15),
        "ohat": ((), None, 0.06),
    },
    # the disco-psych groove for the big middle: four on the floor, the
    # snare on 2 and 4, the open hat on every offbeat eighth
    "psych-disco": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "hat": ((1, 5, 9, 13), None, 0.25),
    },
    # the haze: half time — the kick on 1, one snare on 3, the toms rolling
    # round the kit on a Euclidean 5, quarter-note hats
    "psych-haze": {
        "kick": ((0,), None, 0.08),
        "snare": ((8,), None, 0.0),
        "tom": ((), (5, 1), 0.0),
        "hat": ((0, 4, 8, 12), None, 0.15),
    },
    # --- Sixties Studio (60s studio, ~116): tight, dry and
    # plain — the kick on 1 and 3 (the "and" of 3 now and then), the snare
    # on 2 and 4, straight eighth hats, a bright short open hat on the
    # backbeat where a tambourine would be
    "sixties": {
        "kick": ((0, 8), None, 0.05),
        "snare": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.0),
        "ohat": ((4, 12), None, 0.0),
    },
    # the tom loop: a floor tom's rolling figure under the kick on 1 and the
    # "and" of 2 and a backbeat snare — a hypnotic studio drum loop
    "sixties-toms": {
        "kick": ((0, 6), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "tom": ((2, 3, 10, 11), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.08),
    },
    # the verse's hush: the kick on 1 and 3, a cross-stick on 2 and 4,
    # quarter-note hats
    "sixties-break": {
        "kick": ((0, 8), None, 0.0),
        "rim": ((4, 12), None, 0.0),
        "hat": ((0, 4, 8, 12), None, 0.1),
    },
    # --- hip-hop / trap, drum & bass, dubstep,
    # synthwave, future bass. Half time on a sixteenth grid is a snare on
    # step 8 (beat 3), never on 4 or 12.
    #
    # trap, half time at ~140: the kick on 1 and the "and" of 3, one snare
    # and a clap stacked on 3, hats in eighths with sixteenths sprinkled
    # between — the rolls (a hat ratcheted to 32nds and triplets inside its
    # step) are the feel's (`STYLE_FEEL` "rolls" / "ratchet")
    "trap-808": {
        "kick": ((0, 10), None, 0.06),
        "snare": ((8,), None, 0.0),
        "clap": ((8,), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.35),
        "ohat": ((), None, 0.03),
    },
    # boom bap, ~90 and swung: the kick on 1 and the "and" of 3, the snare
    # on 2 and 4, eighth hats; the kick before the backbeat is a ghost
    "boom-bap": {
        "kick": ((0, 10), None, 0.06),
        "snare": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.1),
        "ohat": ((), None, 0.04),
        "rim": ((), None, 0.03),
    },
    # drum & bass, ~174: the two-step — the kick on 1 and the "and" of 3,
    # the snare on 2 and 4 — over eighth hats with shuffled sixteenths
    "dnb": {
        "kick": ((0, 10), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.3),
        "ohat": ((), None, 0.05),
    },
    # the roller (liquid): the same two-step under hats on every sixteenth,
    # a rim now and then
    "dnb-roller": {
        "kick": ((0, 10), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "hat": (tuple(range(16)), None, 0.0),
        "ohat": ((), None, 0.03),
        "rim": ((), (3, 1), 0.04),
    },
    # jungle-ish, busier (synthesised, no break): a third kick, extra snares
    # sprinkled, toms on a Euclidean 3 — the ghosts and the edits (a snare
    # ratcheted now and then) are the feel's
    "jungle": {
        "kick": ((0, 6, 10), None, 0.06),
        "snare": ((4, 12), None, 0.1),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.3),
        "ohat": ((), None, 0.05),
        "tom": ((), (3, 2), 0.04),
    },
    # dubstep, half time at ~140: the kick on 1 and the "and" of 3, one huge
    # snare and a clap on 3, offbeat hats
    "dubstep": {
        "kick": ((0, 10), None, 0.0),
        "snare": ((8,), None, 0.0),
        "clap": ((8,), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.15),
        "ohat": ((), None, 0.04),
    },
    # riddim: sparser still — the kick on 1, the snare on 3, three hats a
    # bar on a Euclidean figure; the bass fills the space
    "riddim": {
        "kick": ((0,), None, 0.0),
        "snare": ((8,), None, 0.0),
        "clap": ((8,), None, 0.0),
        "hat": ((), (3, 2), 0.0),
    },
    # synthwave / 80s, straight, ~100: the kick on 1 and 3, the snare (the
    # gated room is the bus's chain) and a clap on 2 and 4, sixteenth hats
    # accented on the eighths
    "synthwave": {
        "kick": ((0, 8), None, 0.04),
        "snare": ((4, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": (tuple(range(16)), None, 0.0),
        "tom": ((), None, 0.02),
    },
    # the driving synthpop: four on the floor, the backbeat, sixteenth hats,
    # an open hat on the offbeats
    "synthpop": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": ((4, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": ((1, 3, 5, 7, 9, 11, 13, 15), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
    },
    # future bass, half time at ~150: the kick on 1 and the "and" of 3, a
    # snare and a clap on 3, eighth hats with sixteenths and rolls (the
    # feel's, as trap's)
    "future-bass": {
        "kick": ((0, 10), None, 0.0),
        "snare": ((8,), None, 0.0),
        "clap": ((8,), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.3),
        "ohat": ((), None, 0.04),
    },
    # the build into its drop: a kick on every beat under a snare in
    # eighths whose velocity and pitch climb across the pattern (its feel
    # is the roll's), the clap on 2 and 4
    "future-build": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.0),
        "clap": ((4, 12), None, 0.0),
    },
    # --- lo-fi hip-hop, ambient /
    # cinematic, chiptune, hardstyle, phonk.
    #
    # lo-fi, ~80 and swung hard: the kick on 1 and the "and" of 3, a soft
    # snare (brushed, the kit's) on 2 and 4, lazy eighth hats a rim now and
    # then — the ghost kick and the hats' lag are the feel's
    "lofi": {
        "kick": ((0, 10), None, 0.04),
        "snare": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.08),
        "rim": ((), None, 0.04),
    },
    # the chill variant: a kick on 1, the rim on 2 and the snare on 4,
    # quarter hats that sometimes do not come
    "lofi-chill": {
        "kick": ((0,), None, 0.05),
        "rim": ((4,), None, 0.0),
        "snare": ((12,), None, 0.0),
        "hat": ((0, 4, 8, 12), None, 0.1),
    },
    # ambient's pulse: a soft kick on the bar, a low tom on a Euclidean 3,
    # a hat on a Euclidean 5 — each only sometimes (the feel's chances)
    "ambient-pulse": {
        "kick": ((0,), None, 0.0),
        "tom": ((), (3, 1), 0.0),
        "hat": ((), (5, 2), 0.0),
    },
    # the cinematic build: low toms (the taiko-ish kit's) gathering into a
    # run at the bar's end under a kick on 1 and 3, the snare on 4
    "cinematic-toms": {
        "kick": ((0, 8), None, 0.0),
        "tom": ((0, 3, 6, 8, 10, 12, 13, 14, 15), None, 0.0),
        "snare": ((12,), None, 0.0),
    },
    # chiptune, straight at ~140: the kick on 1 and 3, the noise snare on 2
    # and 4, eighth noise hats — a drum machine with three channels
    "chiptune": {
        "kick": ((0, 8), None, 0.03),
        "snare": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.0),
    },
    # the fast chase: four on the floor, sixteenth hats, the snare on 2 and
    # 4 with a pickup on the last sixteenth (now and then a double, the
    # feel's)
    "chip-chase": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": ((4, 12, 15), None, 0.05),
        "hat": (tuple(range(16)), None, 0.0),
    },
    # hardstyle, ~150: four on the floor (the kick IS the genre: the kit's,
    # through the bus's Hard Kick Drive), a clap on 2 and 4, an open hat on
    # every offbeat — the reverse bass fills between the kicks
    "hardstyle": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "ohat": ((2, 6, 10, 14), None, 0.0),
        "hat": ((), None, 0.1),
    },
    # rawstyle: the same four kicks, a clap on 4 alone, closed hats on the
    # offbeats, a rim on a Euclidean 3 — and now and then a kick doubled
    # (the feel's ratchet)
    "rawstyle": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "clap": ((12,), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.0),
        "rim": ((), (3, 2), 0.0),
    },
    # drift phonk, ~140: the kick on 1 and the "and" of 3, a hard clap on 2
    # and 4, sixteenth hats rolling (the feel's), a cowbell — the kit's
    # rim, tuned up to one — on a Euclidean 5
    "drift-phonk": {
        "kick": ((0, 10), None, 0.06),
        "clap": ((4, 12), None, 0.0),
        "hat": (tuple(range(16)), None, 0.0),
        "rim": ((), (5, 0), 0.0),
    },
    # memphis-style: a third kick before beat 2's "and", the snare and the
    # clap on 2 and 4, eighth hats with triplet rolls, the cowbell on a
    # Euclidean 3
    "memphis": {
        "kick": ((0, 6, 10), None, 0.05),
        "snare": ((4, 12), None, 0.0),
        "clap": ((4, 12), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12, 14), None, 0.15),
        "rim": ((), (3, 1), 0.0),
    },
    # a build: the snare on every sixteenth over a kick on the beat and an
    # offbeat hat. On its own it is a flat roll; its FEEL (`STYLE_FEEL`,
    # `dress`) is what makes it a build — the roll's velocity and pitch
    # climbing across the whole pattern and its last beats doubled to 32nds.
    # (`gen/drums.build` is the generator that also changes the roll's rate
    # bar by bar; this is the bank's one-bar-at-a-time form of it.)
    "roll": {
        "kick": ((0, 4, 8, 12), None, 0.0),
        "snare": (tuple(range(16)), None, 0.0),
        "hat": ((2, 6, 10, 14), None, 0.0),
    },
    # -- other meters (`STYLE_METER`): written in their own
    # bar's sixteenths. A waltz: the kick on one, the snare (or the clap)
    # on two and three — "oom-pah-pah"
    "waltz": {
        "kick": ((0,), None, 0.06),
        "snare": ((4, 8), None, 0.0),
        "hat": ((0, 4, 8), None, 0.2),
        "rim": ((), None, 0.05),
    },
    # a jazz waltz: the ride's skip on every beat and the "and" of two,
    # the kick feathered on one, the snare's comp sparse
    "jazz-waltz": {
        "kick": ((0,), None, 0.08),
        "hat": ((0, 4, 6, 8), None, 0.0),
        "ohat": ((), None, 0.04),
        "snare": ((), None, 0.1),
        "rim": ((8,), None, 0.06),
    },
    # 6/8: two groups of three eighths — the kick on one, the snare on the
    # second group's head, the hats on every eighth (the shuffle's feel is
    # the 3+3, not a swing)
    "six-eight": {
        "kick": ((0,), None, 0.08),
        "snare": ((6,), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10), None, 0.15),
        "rim": ((), None, 0.05),
    },
    # an Afro 6/8 bell figure (x.x.xx.x.x.x in eighths of 12/8, here its
    # first half twice) over a kick on the groups' heads
    "afro-68": {
        "kick": ((0, 6), None, 0.05),
        "rim": ((0, 4, 6, 10), None, 0.0),
        "hat": ((2, 8), None, 0.2),
        "tom": ((), None, 0.06),
    },
    # 7/8 as 2+2+3: a kick on each group's head, the snare on the third
    "seven-eight": {
        "kick": ((0, 4), None, 0.05),
        "snare": ((8,), None, 0.0),
        "hat": ((0, 2, 4, 6, 8, 10, 12), None, 0.15),
    },
}
STYLES: tuple[str, ...] = tuple(_STYLES)

# The bar a style is written in (meter.py): every other style is 4/4
STYLE_METER: dict[str, str] = {
    "waltz": "3/4",
    "jazz-waltz": "3/4",
    "six-eight": "6/8",
    "afro-68": "6/8",
    "seven-eight": "7/8",
}


def style_meter(style: str) -> str:
    return STYLE_METER.get(style, _meter.DEFAULT)


def styles_in(m: str) -> list[str]:
    """The styles written in meter `m`."""
    return [st for st in STYLES if style_meter(st) == m]


# How a menu shows the styles (Synths' Beat ▾, the Drums page's Randomise):
# by family, each by a display name — 28 slugs in one list ("new:
# filter-house-break") read as a dump. A style no family names goes under
# "More" (`style_groups`), so a new one is never lost from a menu.
STYLE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "House & techno",
        (
            "four-floor",
            "techno",
            "melodic-techno",
            "filter-house",
            "filter-house-break",
            "hypnotic-perc",
        ),
    ),
    ("Breaks & garage", ("breaks", "two-step", "garage-rim", "garage-4x4")),
    ("Trance", ("trance", "trance-climax", "trance-break")),
    (
        "Electro & industrial",
        (
            "electro",
            "electro-stomp",
            "electro-break",
            "industrial",
            "industrial-march",
            "industrial-pulse",
        ),
    ),
    (
        "Psych & sixties",
        (
            "psych",
            "psych-disco",
            "psych-haze",
            "sixties",
            "sixties-toms",
            "sixties-break",
        ),
    ),
    ("Hip-hop & trap", ("trap-808", "boom-bap")),
    ("Drum & bass", ("dnb", "dnb-roller", "jungle")),
    ("Bass music", ("dubstep", "riddim", "future-bass", "future-build")),
    ("Synthwave", ("synthwave", "synthpop")),
    ("Lo-fi & phonk", ("lofi", "lofi-chill", "drift-phonk", "memphis")),
    ("Ambient & cinematic", ("ambient-pulse", "cinematic-toms")),
    ("Chiptune & hardstyle", ("chiptune", "chip-chase", "hardstyle", "rawstyle")),
    ("Trap & sparse", ("trap", "sparse", "roll")),
    ("Other meters", ("waltz", "jazz-waltz", "six-eight", "afro-68", "seven-eight")),
)
_STYLE_NAMES = {
    "four-floor": "Four on the floor",
    "two-step": "2-step",
    "garage-4x4": "Garage 4×4",
    "hypnotic-perc": "Hypnotic percussion",
    "trap-808": "Trap 808",
    "dnb": "Drum & bass",
    "dnb-roller": "Drum & bass roller",
    "lofi": "Lo-fi",
    "lofi-chill": "Lo-fi chill",
    "waltz": "Waltz (3/4)",
    "jazz-waltz": "Jazz waltz (3/4)",
    "six-eight": "6/8 shuffle",
    "afro-68": "Afro 6/8",
    "seven-eight": "7/8 (2+2+3)",
}


def style_name(style: str) -> str:
    """A style as a menu shows it: "Filter house break", not its slug."""
    return _STYLE_NAMES.get(style) or style.replace("-", " ").capitalize()


def style_groups() -> list[tuple[str, list[str]]]:
    """(family, its styles) in menu order; every style exactly once."""
    named = {st for _, sts in STYLE_FAMILIES for st in sts}
    out = [(fam, [st for st in sts if st in _STYLES]) for fam, sts in STYLE_FAMILIES]
    if rest := [st for st in STYLES if st not in named]:
        out.append(("More", rest))
    return [(fam, sts) for fam, sts in out if sts]


# A style's own feel: Randomise in one of these styles also sets the drum
# clock's swing (`drum_swing`, a fraction of a 16th that every odd 16th is
# late by). The styles above this table never set it, so their Randomise is
# unchanged. Garage's "57%" shuffle — the off-16th at 57 % of an eighth
# rather than 50 % — is 2 x 0.07 = 0.14 of a 16th late; 0.16 is ~58 %.
STYLE_SWING: dict[str, float] = {
    "melodic-techno": 0.0,
    "garage-rim": 0.16,
    # filter house: hats may swing a little (0-0.08)
    "filter-house": 0.04,
    "filter-house-break": 0.04,
    # emotive garage: 0.14-0.20; only odd 16ths move, so the kick and the
    # clap (even steps) stay straight without a swing of their own
    "two-step": 0.18,
    "garage-4x4": 0.16,
    "hypnotic-perc": 0.0,
    "trance": 0.0,
    "trance-climax": 0.0,
    "trance-break": 0.0,
    # Just Ice: straight, the drive is in the sound; Nine Inch Wails: a
    # machine's, dead straight
    "electro": 0.0,
    "electro-stomp": 0.0,
    "electro-break": 0.0,
    "industrial": 0.0,
    "industrial-march": 0.0,
    "industrial-pulse": 0.0,
    # Psych Haze: a loose drummer, the off-16ths late; Sixties Studio: tight
    # and straight
    "psych": 0.12,
    "psych-disco": 0.08,
    "psych-haze": 0.12,
    "sixties": 0.0,
    "sixties-toms": 0.0,
    "sixties-break": 0.0,
    # trap straight (its swing is the rolls); boom bap heavily
    # swung (0.2: a 60 % shuffle); drum & bass's hats shuffled a little,
    # jungle's more; dubstep, synthwave and future bass straight
    "trap-808": 0.0,
    "boom-bap": 0.2,
    "dnb": 0.08,
    "dnb-roller": 0.06,
    "jungle": 0.1,
    "dubstep": 0.0,
    "riddim": 0.0,
    "synthwave": 0.0,
    "synthpop": 0.0,
    "future-bass": 0.0,
    "future-build": 0.0,
    # lo-fi swung hardest of all (0.24: a 62 % shuffle, the hats
    # lazy); the rest straight
    "lofi": 0.24,
    "lofi-chill": 0.24,
    "ambient-pulse": 0.0,
    "cinematic-toms": 0.0,
    "chiptune": 0.0,
    "chip-chase": 0.0,
    "hardstyle": 0.0,
    "rawstyle": 0.0,
    "drift-phonk": 0.0,
    "memphis": 0.0,
    "roll": 0.0,
}

# A style's FEEL: what it says per hit and per row, beside its cells
# (`dress`). `generate` is untouched by it — a style's cells are what they
# always were, and a beat nobody dressed carries no per-hit data at all.
#   swing   {voice: 0..0.6}: that row's own swing over the kit's. Garage:
#           the hats shuffle, the kick and the clap never do — wherever a
#           hit of theirs is drawn later.
#   ghosts  ((voice, sixteenth, velocity), ...): quiet hits beside the
#           backbeat, ADDED to the style's cells with a velocity of their
#           own (0.25-0.4) — a hit's velocity, not the step's, so a ghost
#           can share its step.
#   soft    {voice: velocity}: that voice's hits on the odd sixteenths.
#   chance  {voice: (lo, hi)}: each of that voice's hits gets a chance drawn
#           from the range (in twentieths), seeded.
#   rolls   ((voice, sixteenth, ratchet), ...): that hit, where the cells
#           have it, struck `ratchet` times inside its step in every bar —
#           a trap hat roll.
#   ratchet {voice: (p, (ratchets...))}: each of that voice's other hits
#           rolled with chance p (seeded), its ratchet drawn from the list.
#   roll    the build: velocity 0.4 -> 1 and tune 0 -> +7 on the snare
#           across the whole pattern, its last two beats ratcheted x2.
STYLE_FEEL: dict[str, dict] = {
    "melodic-techno": {"soft": {"hat": 0.72}, "chance": {"rim": (0.5, 0.8)}},
    "garage-rim": {
        "swing": {"kick": 0.0, "clap": 0.0},
        "ghosts": (("kick", 7, 0.35),),
        "soft": {"hat": 0.7},
    },
    "filter-house": {"swing": {"kick": 0.0, "clap": 0.0, "snare": 0.0}},
    "filter-house-break": {"swing": {"kick": 0.0, "clap": 0.0}},
    "two-step": {
        "swing": {"kick": 0.0, "clap": 0.0},
        "ghosts": (("rim", 3, 0.35), ("snare", 7, 0.3), ("rim", 15, 0.35)),
        "soft": {"hat": 0.7},
    },
    "garage-4x4": {
        "swing": {"kick": 0.0, "clap": 0.0},
        "ghosts": (("rim", 3, 0.4), ("snare", 7, 0.3), ("rim", 11, 0.4)),
        "soft": {"hat": 0.7},
    },
    "hypnotic-perc": {"chance": {"rim": (0.5, 0.8), "tom": (0.5, 0.8)}},
    "trance": {"soft": {"hat": 0.7}},
    "trance-climax": {"soft": {"hat": 0.7}},
    "trance-break": {"soft": {"hat": 0.7}},
    # Just Ice: the pushed kick before the bar line is a ghost of a push,
    # the off-16th hats held down so the eighths drive
    "electro": {"soft": {"hat": 0.65}, "ghosts": (("kick", 14, 0.6),)},
    "electro-stomp": {"soft": {"hat": 0.75}},
    "electro-break": {"soft": {"hat": 0.7}},
    # Nine Inch Wails: the metal and the clank only sometimes — a machine
    # that slips — and the rim's tick soft under the pulse
    "industrial": {"chance": {"tom": (0.6, 0.9), "rim": (0.5, 0.8)}},
    "industrial-march": {"soft": {"hat": 0.6}},
    "industrial-pulse": {"chance": {"rim": (0.4, 0.7)}, "soft": {"rim": 0.6}},
    # Psych Haze: the kick and the snare never swing (only the hats lag),
    # ghost snares before the backbeat, the off-16th hats held down, the
    # haze's toms only sometimes
    "psych": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("snare", 7, 0.3), ("snare", 15, 0.3)),
        "soft": {"hat": 0.6},
    },
    "psych-disco": {"swing": {"kick": 0.0, "snare": 0.0}, "soft": {"hat": 0.65}},
    "psych-haze": {"chance": {"tom": (0.6, 0.9)}},
    # Sixties Studio: a ghost before the last backbeat, the loop's toms all
    # but always
    "sixties": {"ghosts": (("snare", 14, 0.3),)},
    "sixties-toms": {"chance": {"tom": (0.75, 0.95)}},
    # Trap and future bass: the hat rolls — the
    # last eighth of every bar rolled in triplets, the "and" of 2 in 32nds,
    # and any other hat rolled now and then (two to four inside its step)
    "trap-808": {
        "soft": {"hat": 0.65},
        "rolls": (("hat", 6, 2), ("hat", 14, 3)),
        "ratchet": {"hat": (0.12, (2, 3, 4))},
    },
    # boom bap: the kick and the snare never swing, the hats lag; a ghost
    # kick before the backbeat and a ghost snare before the bar line
    "boom-bap": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("kick", 7, 0.5), ("snare", 15, 0.3)),
        "soft": {"hat": 0.6},
    },
    # drum & bass: the kick and the snare straight, the hats shuffled and
    # their off-16ths held down, a ghost snare before beat 3
    "dnb": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("snare", 7, 0.3),),
        "soft": {"hat": 0.6},
    },
    "dnb-roller": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("snare", 9, 0.3), ("snare", 15, 0.3)),
        "soft": {"hat": 0.55},
        "chance": {"rim": (0.5, 0.8)},
    },
    # jungle: three ghosts around the backbeat, the toms only sometimes, a
    # snare now and then cut into two (an edit)
    "jungle": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("snare", 7, 0.3), ("snare", 9, 0.35), ("snare", 15, 0.3)),
        "soft": {"hat": 0.6},
        "chance": {"tom": (0.5, 0.8)},
        "ratchet": {"snare": (0.12, (2,))},
    },
    "dubstep": {"soft": {"hat": 0.7}},
    "riddim": {"chance": {"hat": (0.6, 0.9)}},
    # synthwave: sixteenth hats, the off-16ths held down (the eighths
    # accented, a drum machine's); synthpop the same
    "synthwave": {"soft": {"hat": 0.6}},
    "synthpop": {"soft": {"hat": 0.65}},
    "future-bass": {
        "soft": {"hat": 0.6},
        "rolls": (("hat", 14, 3),),
        "ratchet": {"hat": (0.1, (2, 3))},
    },
    "future-build": {"roll": True},
    # Lo-fi: the kick and the snare never swing, the
    # hats lag behind them, a ghost kick before beat 3; the chill variant's
    # hats only sometimes
    "lofi": {
        "swing": {"kick": 0.0, "snare": 0.0},
        "ghosts": (("kick", 7, 0.45),),
        "soft": {"hat": 0.55},
    },
    "lofi-chill": {
        "swing": {"kick": 0.0, "snare": 0.0, "rim": 0.0},
        "chance": {"hat": (0.6, 0.95)},
    },
    # ambient: nothing for certain but the kick
    "ambient-pulse": {"chance": {"tom": (0.5, 0.85), "hat": (0.4, 0.8)}},
    # the tom run's off-sixteenths held down: it gathers into the bar line
    "cinematic-toms": {"soft": {"tom": 0.6}},
    "chip-chase": {"soft": {"hat": 0.6}, "ratchet": {"snare": (0.15, (2,))}},
    "hardstyle": {"chance": {"hat": (0.5, 0.8)}},
    "rawstyle": {"ratchet": {"kick": (0.08, (2,))}},
    # phonk: drift phonk's hats roll as trap's (the last eighth in
    # triplets); memphis rolls two eighths a bar in triplets
    "drift-phonk": {
        "soft": {"hat": 0.6},
        "rolls": (("hat", 14, 3),),
        "ratchet": {"hat": (0.1, (2, 3))},
    },
    "memphis": {
        "soft": {"hat": 0.65},
        "rolls": (("hat", 6, 3), ("hat", 14, 3)),
    },
    "roll": {"roll": True},
}
ROLL_VELOCITY = (0.4, 1.0)  # a build's snare, first hit to last
ROLL_TUNE = 7.0  # semitones the snare climbs across a build


def dress(
    style: str,
    seed: int,
    cells: list[list],
    bars: int = 1,
    steps_per_bar: int = 16,
    vel: dict | None = None,
    meter: str = "4/4",
) -> dict:
    """A style's feel on a beat it generated: `{"drums", "hits", "swing"}`
    — the cells with the style's ghosts added, the per-hit table and the
    rows' own swing (`STYLE_FEEL`). Pure and seeded (crc32, as
    `generate_row`); a style with no feel returns the cells and two empty
    dicts. `vel` is the step velocity lane the beat already has: a step it
    holds down keeps its value (a kit's lane softens its own off-steps)."""
    if style not in _STYLES:
        raise ValueError(f"unknown style {style!r}")
    feel = STYLE_FEEL.get(style) or {}
    total = bars * steps_per_bar
    six = _meter.parse(meter).sixteenths  # 16 in 4/4
    scale = steps_per_bar / six
    on = {(v, int(s)) for v, s in cells if 0 <= int(s) < total}
    hits: dict[str, dict] = {}
    held = {int(k) for k in (vel or {})}
    for voice, s16, v in feel.get("ghosts", ()):
        if not (s16 * scale).is_integer() or s16 >= six:
            continue
        for bar in range(bars):
            step = bar * steps_per_bar + int(s16 * scale)
            on.add((voice, step))
            hits[f"{voice}.{step}"] = {"v": v}
    for voice, v in feel.get("soft", {}).items():
        for w, s in on:
            if w == voice and (s * six // steps_per_bar) % 2 == 1 and s not in held:
                hits.setdefault(f"{w}.{s}", {}).setdefault("v", v)
    rng = random.Random(zlib.crc32(f"{int(seed)}:feel:{style}".encode()))
    for voice, (lo, hi) in sorted(feel.get("chance", {}).items()):
        for s in sorted(s for w, s in on if w == voice):
            p = round(rng.uniform(lo, hi) * 20) / 20
            hits.setdefault(f"{voice}.{s}", {})["p"] = p
    # the rolls: drawn after the chances, so a style with
    # neither key draws exactly what it always did
    rolled = set()
    for voice, s16, r in feel.get("rolls", ()):
        if not (s16 * scale).is_integer() or s16 >= six:
            continue
        for bar in range(bars):
            step = bar * steps_per_bar + int(s16 * scale)
            if (voice, step) in on:
                hits.setdefault(f"{voice}.{step}", {})["r"] = int(r)
                rolled.add((voice, step))
    for voice, (p, choices) in sorted(feel.get("ratchet", {}).items()):
        for s in sorted(s for w, s in on if w == voice):
            hit_p, r = rng.random(), rng.choice(choices)
            if (voice, s) not in rolled and hit_p < p:
                hits.setdefault(f"{voice}.{s}", {})["r"] = int(r)
    if feel.get("roll"):
        roll = sorted(s for w, s in on if w == "snare")
        for i, s in enumerate(roll):
            t = i / max(1, len(roll) - 1)
            hit = {
                "v": round(
                    ROLL_VELOCITY[0] + (ROLL_VELOCITY[1] - ROLL_VELOCITY[0]) * t, 2
                )
            }
            if t > 0:
                hit["t"] = round(ROLL_TUNE * t, 1)
            if s >= total - steps_per_bar // 2:
                hit["r"] = 2
            hits[f"snare.{s}"] = hit
    return {
        "drums": sorted([v, s] for v, s in on),
        "hits": {k: hits[k] for k in sorted(hits)},
        "swing": dict(feel.get("swing", {})),
    }


def _bar(
    style: str,
    rng: random.Random,
    steps_per_bar: int,
    density: float | None = None,
    meter: str = "4/4",
) -> set[tuple[str, int]]:
    table = _STYLES[style]
    # a step in sixteenths of the grid's bar; the style's own bar may be
    # longer (a hit past the grid's bar is not drawn) or shorter
    six = _meter.parse(meter).sixteenths
    scale = steps_per_bar / six
    written = _meter.parse(style_meter(style)).sixteenths
    # density 0..1 scales the sprinkles: 0.5 is the style as written, 0 none
    # of them, 1 twice as many. The draws are the same ones either way (a
    # voice with no sprinkle draws none), so a seed keeps its groove.
    lift = 1.0 if density is None else 2.0 * min(1.0, max(0.0, float(density)))
    cells: set[tuple[str, int]] = set()
    for voice, (forced, euc, base) in table.items():
        prob = base * lift
        for s16 in forced:
            if (s16 * scale).is_integer() and s16 < six:
                cells.add((voice, int(s16 * scale)))
        if euc is not None:
            hits, rotate = euc
            hits = max(1, min(steps_per_bar, round(hits * steps_per_bar / written)))
            pattern = euclid(hits, steps_per_bar, rotate=rotate)
            cells.update((voice, i) for i, ch in enumerate(pattern) if ch == "x")
        for i in range(steps_per_bar):
            if base and rng.random() < prob:
                cells.add((voice, i))
    if not any(v == "kick" for v, _ in cells):
        cells.add(("kick", 0))
    return cells


def generate(
    style: str,
    seed: int,
    bars: int = 1,
    steps_per_bar: int = 16,
    density: float | None = None,
    meter: str = "4/4",
) -> list[list]:
    """Sorted ``[voice, step]`` pairs spanning ``bars * steps_per_bar``
    of a grid in `meter` (the style's own bar fitted to it, above).

    Every bar is filled: each one re-applies the style's forced and
    Euclidean hits — the groove — and re-rolls only the ``prob`` sprinkles
    from the one seeded stream, so bar 2 is a light variation on bar 1
    rather than a copy or an empty bar. Bar 1 is exactly the 1-bar pattern
    for the same seed (pinned by test_beatgen).

    ``density`` (0..1, song creation's "busier / sparser") scales every
    voice's sprinkle probability — None or 0.5 is the style as written, and
    the forced and Euclidean hits never move.
    """
    if style not in _STYLES:
        raise ValueError(f"unknown style {style!r}")
    rng = random.Random(seed)
    cells: set[tuple[str, int]] = set()
    for bar in range(bars):
        for voice, step in _bar(style, rng, steps_per_bar, density, meter):
            cells.add((voice, step + bar * steps_per_bar))
    return sorted([v, s] for v, s in cells)


def generate_row(
    voice: str,
    style: str,
    seed: int,
    bars: int = 1,
    steps_per_bar: int = 16,
    meter: str = "4/4",
) -> list[list]:
    """``[voice, step]`` pairs for one row only. Voices the style table
    knows follow it; the others (open hat, toms, rim, crash) get a seeded
    euclidean figure so every row can be re-rolled on its own."""
    if style not in _STYLES:
        raise ValueError(f"unknown style {style!r}")
    if voice in _STYLES[style]:
        return [
            c
            for c in generate(style, seed, bars, steps_per_bar, meter=meter)
            if c[0] == voice
        ]
    # crc32, not hash(): a str's hash is salted per process, so the same
    # seed drew a different figure every time the studio started
    rng = random.Random(zlib.crc32(f"{seed}:{voice}".encode()))
    cells: set[tuple[str, int]] = set()
    for bar in range(bars):
        hits = rng.randint(1, max(1, steps_per_bar // 4))
        if voice == "crash":  # crashes are accents, not grooves
            cells.add((voice, bar * steps_per_bar))
            continue
        pattern = euclid(hits, steps_per_bar, rotate=rng.randrange(steps_per_bar))
        cells.update(
            (voice, i + bar * steps_per_bar)
            for i, ch in enumerate(pattern)
            if ch == "x"
        )
    return sorted([v, s] for v, s in cells)


def extend_bars(
    steps: list[list], old_bars: int, new_bars: int, steps_per_bar: int
) -> list[list]:
    """Grow a pattern from ``old_bars`` to ``new_bars`` by repeating the
    bars it has (the loop-extension convention): 1 -> 4 bars copies
    bar 1 into bars 2-4; 2 -> 4 repeats the 2-bar phrase. Raising the bar
    count used to add empty bars, so a beat went silent halfway round.
    Shrinking (or no change) just drops the hits past the new end.

    A repeat only lands where there is nothing: a block of the new range
    that already holds hits (a shrunk grid keeps its later bars, hidden)
    comes back as it was — growing used to drop them and copy bar 1 over
    the top, where the note grid shows them again
    (`patternops.repeat_into`). Hits still past the new end stay hidden."""
    old_total = max(1, old_bars) * steps_per_bar
    total = new_bars * steps_per_bar
    if new_bars <= old_bars:
        return sorted([v, s] for v, s in {(v, s) for v, s in steps if 0 <= s < total})
    cells = {(v, s) for v, s in steps if s >= 0}
    cells |= {
        (v, s + k)
        for v, s in list(cells)
        if s < old_total
        for k in repeat_offsets(steps, old_total, total)
        if s + k < total
    }
    return sorted([v, s] for v, s in cells)


def repeat_offsets(steps: list[list], old_total: int, total: int) -> list[int]:
    """Where growing a grid from `old_total` to `total` steps puts a repeat
    of the phrase: the start of each phrase-long block of the new range
    that holds no hit. The velocity lane follows the same blocks
    (`routes/drums._extend_vel`)."""
    return [
        k
        for k in range(old_total, total, old_total)
        if not any(k <= s < min(k + old_total, total) for _v, s in steps)
    ]


def mutate(
    steps: list[list],
    seed: int,
    bars: int,
    steps_per_bar: int,
    rate: float = 0.1,
    voice: str | None = None,
) -> list[list]:
    """Flip roughly ``rate`` of the grid's cells (at least one); with
    ``voice`` only that row's cells are touched."""
    rng = random.Random(seed)
    total = bars * steps_per_bar
    cells = {(v, s) for v, s in steps if v in DRUM_ROWS and 0 <= s < total}
    rows = [voice] if voice else DRUM_ROWS
    grid = [(v, s) for v in rows for s in range(total)]
    flips = [c for c in grid if rng.random() < rate] or [rng.choice(grid)]
    for c in flips:
        cells.symmetric_difference_update({c})
    return sorted([v, s] for v, s in cells)
