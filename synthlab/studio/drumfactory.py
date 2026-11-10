"""Factory drum patterns: the pattern bank's starting contents.

Plain dicts (the bank's on-disk shape) — ``steps`` are ``[voice, step]``
pairs over a 16-step bar. Kept free of studio imports so ``state.py`` can
seed a fresh bank from here.
"""

from __future__ import annotations


def _p(name: str, swing: float = 0.0, **rows: str) -> dict:
    steps = [
        [voice, i]
        for voice, pat in rows.items()
        for i, ch in enumerate(pat)
        if ch == "x"
    ]
    steps.sort(key=lambda d: (d[0], d[1]))
    return {
        "name": name,
        "steps": steps,
        "bars": 1,
        "steps_per_bar": 16,
        "swing": swing,
    }


DRUM_FACTORY: list[dict] = [
    _p(
        "House",
        kick="x...x...x...x...",
        clap="....x.......x...",
        hat="..x...x...x...x.",
    ),
    _p(
        "Deep",
        kick="x...x...x...x...",
        clap="....x.......x...",
        hat="..x...x...x.x.x.",
        snare="...............x",
    ),
    _p(
        "Techno",
        kick="x...x...x...x...",
        hat="xxxxxxxxxxxxxxxx",
        clap="....x.......x...",
    ),
    _p(
        "Breaks",
        kick="x.........x.....",
        snare="....x.......x...",
        hat="x.x.x.x.x.x.x.x.",
    ),
    _p(
        "Trap",
        kick="x.....x...x.....",
        snare="........x.......",
        hat="x.xxx.xxx.xxx.xx",
        clap="........x.......",
    ),
    _p(
        "Garage",
        kick="x.....x...x.....",
        snare="....x.......x...",
        hat="..x...x...x...x.",
        clap="....x......x....",
    ),
    _p(
        "Half-time",
        kick="x.......x.x.....",
        snare="........x.......",
        hat="x..x..x...x..x..",
    ),
    _p("Minimal", kick="x...x...x...x...", hat="......x.......x."),
]

# Genre kits: a pattern *and* the voices shaped for it, loaded together
# (`/fragment/drums/kit`, the Drums bank's "genre kit" select). `params` are
# per-voice Drum Lab values (inside drumlab.DRUM_PARAMS' ranges — pinned by
# tests/studio/test_drum_kits.py); `vel` is the drum velocity lane (step ->
# gain for every voice on that step), which is how a ghost note is quiet;
# `style`/`seed` become the bank's Randomise style and seed, so a re-roll
# stays in the genre.
DRUM_KITS: dict[str, dict] = {
    # ~125 BPM, straight. A round, driven kick; tight bright closed hats
    # rolling on the off-16ths between the kicks (a touch softer); an open
    # hat on each offbeat eighth (the next closed hat chokes it to a 16th
    # "tss"); a wide clap on 2 and 4.
    "Melodic Techno": {
        "style": "melodic-techno",
        "seed": 125,
        "pattern": _p(
            "Melodic Techno",
            kick="x...x...x...x...",
            hat=".x.x.x.x.x.x.x.x",
            ohat="..x...x...x...x.",
            clap="....x.......x...",
            rim="...........x....",
        ),
        "params": {
            "kick": {
                "tune": 48.0,
                "decay": 0.5,
                "punch": 0.85,
                "click": 0.3,
                "drive": 2.6,
            },
            "hat": {"decay": 0.03, "tone": 10500.0, "open_hat": False},
            "ohat": {"decay": 0.22, "tone": 8000.0},
            "clap": {"decay": 0.3, "spread": 0.018},
            "rim": {"tune": 520.0, "decay": 0.04},
        },
        "vel": {str(s): 0.72 for s in range(1, 16, 2)},
    },
    # UK garage 2-step, swung ~58 %. A 909-style kick on 1 and the "and"
    # of 3, with a ghost kick on the 16th before beat 3 (velocity 0.35 —
    # no hat shares that step); a rimshot on every offbeat eighth; closed
    # hats band-passed at ~4.8 kHz (drums.hat band-passes at `tone`); a
    # clap on 2 and 4.
    "Garage Rim": {
        "style": "garage-rim",
        "seed": 132,
        "pattern": _p(
            "Garage Rim",
            swing=0.16,
            kick="x......x..x.....",
            rim="..x...x...x...x.",
            clap="....x.......x...",
            hat=".x.x.x...x.x.x.x",
        ),
        "params": {
            "kick": {
                "tune": 50.0,
                "decay": 0.38,
                "punch": 0.8,
                "click": 0.45,
                "drive": 2.4,
            },
            "rim": {"tune": 450.0, "decay": 0.06},
            "hat": {"decay": 0.045, "tone": 4800.0, "open_hat": False},
            "clap": {"decay": 0.25, "spread": 0.014},
        },
        "vel": {"7": 0.35},
    },
    # --- the four style packs ------------------------------------------------
    # Filter house, ~122 BPM, a touch of shuffle. A round kick on every
    # beat; a wide clap with a snare under it on 2 and 4; a soft, shaker-
    # like closed hat on every 16th (band-passed lower, quiet off the
    # eighths); an open hat on each offbeat eighth, choked a 16th later —
    # the disco "tss". The offbeats are the accent: the off-16ths sit at 0.5.
    "Filter House": {
        "style": "filter-house",
        "seed": 122,
        "pattern": _p(
            "Filter House",
            swing=0.04,
            kick="x...x...x...x...",
            clap="....x.......x...",
            snare="....x.......x...",
            hat="xxxxxxxxxxxxxxxx",
            ohat="..x...x...x...x.",
        ),
        "params": {
            "kick": {
                "tune": 54.0,
                "decay": 0.36,
                "punch": 0.8,
                "click": 0.4,
                "drive": 2.2,
            },
            "clap": {"decay": 0.3, "spread": 0.02},
            "snare": {"tune": 180.0, "decay": 0.18, "tone": 0.4, "snap": 0.6},
            "hat": {"decay": 0.05, "tone": 7000.0, "open_hat": False},
            "ohat": {"decay": 0.24, "tone": 8500.0},
        },
        "vel": {str(s): 0.5 for s in range(1, 16, 2)},
    },
    # Emotive garage 2-step, swung ~59 % (the odd 16ths late: the kick and
    # the clap sit on even steps and stay straight). The kick on 1 and the
    # "and" of 3 — never on 2 or 4; a clap on 2 and 4; closed hats on the
    # offbeat eighths and three swung 16ths; and three ghosts alone on their
    # steps so the velocity lane can hold them down: a rim before beat 2, a
    # snare before beat 3, a rim before the bar line (0.3-0.35).
    "Two-Step": {
        "style": "two-step",
        "seed": 132,
        "pattern": _p(
            "Two-Step",
            swing=0.18,
            kick="x.........x.....",
            clap="....x.......x...",
            hat="..x..xx..x...xx.",
            snare=".......x........",
            rim="...x...........x",
        ),
        "params": {
            "kick": {
                "tune": 49.0,
                "decay": 0.34,
                "punch": 0.8,
                "click": 0.4,
                "drive": 2.2,
            },
            "clap": {"decay": 0.2, "spread": 0.01},
            "hat": {"decay": 0.04, "tone": 6500.0, "open_hat": False},
            "snare": {"tune": 210.0, "decay": 0.1, "tone": 0.5, "snap": 0.6},
            "rim": {"tune": 480.0, "decay": 0.05},
        },
        "vel": {"3": 0.35, "7": 0.3, "15": 0.35},
    },
    # Trance, ~138 BPM, straight. A tight, clicky kick on every beat (short,
    # so the three bass 16ths after it have room); a clap and a short snare
    # stacked on 2 and 4 — the snare is short enough to roll; an open hat on
    # every offbeat eighth; closed hats on the other 16ths, the off-16ths
    # softer. The crash is cut to half a second: the kit's ride, which the
    # "trance-climax" style plays in eighths.
    "Trance": {
        "style": "trance",
        "seed": 138,
        "pattern": _p(
            "Trance",
            kick="x...x...x...x...",
            clap="....x.......x...",
            snare="....x.......x...",
            ohat="..x...x...x...x.",
            hat="xx.xxx.xxx.xxx.x",
        ),
        "params": {
            "kick": {
                "tune": 52.0,
                "decay": 0.3,
                "punch": 0.95,
                "click": 0.55,
                "drive": 2.8,
            },
            "clap": {"decay": 0.22, "spread": 0.012},
            "snare": {"tune": 200.0, "decay": 0.14, "tone": 0.6, "snap": 0.85},
            "hat": {"decay": 0.03, "tone": 10000.0, "open_hat": False},
            "ohat": {"decay": 0.2, "tone": 9000.0},
            "crash": {"decay": 0.5},
        },
        "vel": {str(s): 0.7 for s in range(1, 16, 2)},
    },
    # Just Ice (French electro, ~120): everything driven into the red — a
    # short, hard, clicky kick at full drive, a bright snare that snaps and
    # a clap stacked on it, crunchy closed hats, an open hat that hisses on
    # the offbeat. The pushed kick before the bar line sits lower.
    "Just Ice": {
        "style": "electro",
        "seed": 120,
        "pattern": _p(
            "Just Ice",
            kick="x...x...x...x.x.",
            snare="....x.......x...",
            clap="....x.......x...",
            hat=".x.x.x.x.x.x.x.x",
            ohat="..x...x...x...x.",
        ),
        "params": {
            "kick": {
                "tune": 50.0,
                "decay": 0.28,
                "punch": 1.0,
                "click": 0.7,
                "drive": 4.0,
            },
            "clap": {"decay": 0.24, "spread": 0.018},
            "snare": {"tune": 215.0, "decay": 0.26, "tone": 0.7, "snap": 0.95},
            "hat": {"decay": 0.04, "tone": 10500.0, "open_hat": False},
            "ohat": {"decay": 0.28, "tone": 9500.0},
        },
        "vel": {"14": 0.6} | {str(s): 0.65 for s in range(1, 16, 2)},
    },
    # Nine Inch Wails (industrial, ~105): a machine — a tight, distorted,
    # low kick, a noisy snare that cracks, the tom tuned high and cut short
    # as a struck metal, the rim a clank, the hats raw and harsh
    "Nine Inch Wails": {
        "style": "industrial",
        "seed": 105,
        "pattern": _p(
            "Nine Inch Wails",
            kick="x..x....x.......",
            snare="....x.......x...",
            tom="..x..x...x..x...",
            rim="..x.......x.....",
            hat="x...x...x...x...",
        ),
        "params": {
            "kick": {
                "tune": 44.0,
                "decay": 0.24,
                "punch": 1.0,
                "click": 0.8,
                "drive": 4.0,
            },
            "snare": {"tune": 160.0, "decay": 0.34, "tone": 0.9, "snap": 1.0},
            "tom": {"tune": 290.0, "decay": 0.14},
            "rim": {"tune": 720.0, "decay": 0.03},
            "hat": {"decay": 0.03, "tone": 12000.0, "open_hat": False},
            "crash": {"decay": 1.2},
        },
        "vel": {"2": 0.75, "5": 0.6, "9": 0.7, "12": 1.0},
    },
    # --- neo-psych and 60s studio --------------------------------------------
    # Psych Haze (neo-psych, ~112, swung): a drummer in a soft room — a
    # round, low kick with little click, a loose snare that rings, two
    # ghost snares alone on their steps (held down by the lane), warm
    # eighth hats, the tom low and long for the haze's rolls
    "Psych Haze": {
        "style": "psych",
        "seed": 112,
        "pattern": _p(
            "Psych Haze",
            swing=0.12,
            kick="x.....x...x.....",
            snare="....x..x....x..x",
            hat="x.x.x.x.x.x.x.x.",
            ohat="..............x.",
        ),
        "params": {
            "kick": {
                "tune": 52.0,
                "decay": 0.45,
                "punch": 0.6,
                "click": 0.2,
                "drive": 1.6,
            },
            "snare": {"tune": 185.0, "decay": 0.3, "tone": 0.6, "snap": 0.7},
            "hat": {"decay": 0.06, "tone": 7000.0, "open_hat": False},
            "ohat": {"decay": 0.35, "tone": 7500.0},
            "tom": {"tune": 110.0, "decay": 0.4},
        },
        "vel": {"7": 0.3, "15": 0.3},
    },
    # Sixties Studio (60s studio, ~116, straight): close-miked and dead — a
    # short, dry kick, a tight snare that stops, plain eighth hats, a short
    # bright open hat on the backbeat where the tambourine sits, a floor tom
    # for the loop, a cross-stick
    "Sixties Studio": {
        "style": "sixties",
        "seed": 116,
        "pattern": _p(
            "Sixties Studio",
            kick="x.......x.......",
            snare="....x.......x...",
            hat="x.x.x.x.x.x.x.x.",
            ohat="....x.......x...",
        ),
        "params": {
            "kick": {
                "tune": 58.0,
                "decay": 0.22,
                "punch": 0.5,
                "click": 0.15,
                "drive": 1.0,
            },
            "snare": {"tune": 220.0, "decay": 0.12, "tone": 0.6, "snap": 0.7},
            "hat": {"decay": 0.03, "tone": 8000.0, "open_hat": False},
            "ohat": {"decay": 0.12, "tone": 9500.0},
            "tom": {"tune": 95.0, "decay": 0.3},
            "rim": {"tune": 520.0, "decay": 0.03},
        },
        "vel": {},
    },
    # --- trap, drum & bass, dubstep, synthwave, future bass ------------------
    # Hip-Hop 808 (trap, ~140, half time, straight): a short punchy kick
    # that leaves the 808 (a synth track) the low end, a crisp snare and a
    # clap stacked on 3, tight eighth hats (the rolls are the style's feel),
    # an open hat that hisses
    "Hip-Hop 808": {
        "style": "trap-808",
        "seed": 140,
        "pattern": _p(
            "Hip-Hop 808",
            kick="x.........x.....",
            snare="........x.......",
            clap="........x.......",
            hat="x.x.x.x.x.x.x.x.",
        ),
        "params": {
            "kick": {
                "tune": 54.0,
                "decay": 0.26,
                "punch": 1.0,
                "click": 0.6,
                "drive": 2.4,
            },
            "snare": {"tune": 205.0, "decay": 0.16, "tone": 0.55, "snap": 0.95},
            "clap": {"decay": 0.22, "spread": 0.014},
            "hat": {"decay": 0.03, "tone": 10500.0, "open_hat": False},
            "ohat": {"decay": 0.3, "tone": 9500.0},
        },
        "vel": {},
    },
    # Drum and Bass (~174, the two-step): a short kick, a tight snappy
    # snare that cracks, a ghost snare before beat 3 held down by the lane,
    # eighth hats shuffled (swing 0.08)
    "Drum and Bass": {
        "style": "dnb",
        "seed": 174,
        "pattern": _p(
            "Drum and Bass",
            swing=0.08,
            kick="x.........x.....",
            snare="....x..x....x...",
            hat="x.x.x.x.x.x.x.x.",
        ),
        "params": {
            "kick": {
                "tune": 56.0,
                "decay": 0.2,
                "punch": 0.9,
                "click": 0.65,
                "drive": 2.0,
            },
            "snare": {"tune": 235.0, "decay": 0.13, "tone": 0.6, "snap": 1.0},
            "hat": {"decay": 0.025, "tone": 11000.0, "open_hat": False},
            "ohat": {"decay": 0.2, "tone": 10000.0},
            "tom": {"tune": 160.0, "decay": 0.2},
            "rim": {"tune": 560.0, "decay": 0.03},
        },
        "vel": {"7": 0.3},
    },
    # Dubstep (~140, half time): a punchy, driven kick, a huge snare that
    # rings on 3 with a clap stacked on it, offbeat hats
    "Dubstep": {
        "style": "dubstep",
        "seed": 141,
        "pattern": _p(
            "Dubstep",
            kick="x.........x.....",
            snare="........x.......",
            clap="........x.......",
            hat="..x...x...x...x.",
        ),
        "params": {
            "kick": {
                "tune": 48.0,
                "decay": 0.38,
                "punch": 1.0,
                "click": 0.55,
                "drive": 3.0,
            },
            "snare": {"tune": 175.0, "decay": 0.4, "tone": 0.8, "snap": 1.0},
            "clap": {"decay": 0.3, "spread": 0.02},
            "hat": {"decay": 0.035, "tone": 9500.0, "open_hat": False},
            "ohat": {"decay": 0.25, "tone": 9000.0},
        },
        "vel": {},
    },
    # Synthwave (~100, straight): a drum machine's — a round synth kick, a
    # big snare and a clap on 2 and 4 (the gated room is the bus's chain,
    # Gated Verb), sixteenth hats with the off-16ths held down, a tom for
    # the fills
    "Synthwave": {
        "style": "synthwave",
        "seed": 100,
        "pattern": _p(
            "Synthwave",
            kick="x.......x.......",
            snare="....x.......x...",
            clap="....x.......x...",
            hat="xxxxxxxxxxxxxxxx",
        ),
        "params": {
            "kick": {
                "tune": 50.0,
                "decay": 0.42,
                "punch": 0.7,
                "click": 0.3,
                "drive": 1.4,
            },
            "snare": {"tune": 190.0, "decay": 0.3, "tone": 0.65, "snap": 0.8},
            "clap": {"decay": 0.26, "spread": 0.016},
            "hat": {"decay": 0.045, "tone": 8500.0, "open_hat": False},
            "ohat": {"decay": 0.3, "tone": 8000.0},
            "tom": {"tune": 120.0, "decay": 0.35},
        },
        "vel": {str(s): 0.6 for s in range(1, 16, 2)},
    },
    # Future Bass (~150, half time): a punchy kick, a snare and a wide clap
    # on 3 (the big room is the bus's), eighth hats with rolls
    "Future Bass": {
        "style": "future-bass",
        "seed": 150,
        "pattern": _p(
            "Future Bass",
            kick="x.........x.....",
            snare="........x.......",
            clap="........x.......",
            hat="x.x.x.x.x.x.x.x.",
        ),
        "params": {
            "kick": {
                "tune": 52.0,
                "decay": 0.32,
                "punch": 0.95,
                "click": 0.5,
                "drive": 2.2,
            },
            "snare": {"tune": 210.0, "decay": 0.22, "tone": 0.6, "snap": 0.85},
            "clap": {"decay": 0.3, "spread": 0.022},
            "hat": {"decay": 0.03, "tone": 10000.0, "open_hat": False},
            "ohat": {"decay": 0.25, "tone": 9500.0},
        },
        "vel": {},
    },
    # --- lo-fi, cinematic, chiptune, hardstyle, phonk ------------------------
    # Lo-Fi (~80, swung 0.24): a soft, round kick, a brushed snare (little
    # body, a soft rattle), a rimshot, lazy dark hats; the ghost kick on
    # step 7 held down by the lane
    "Lo-Fi": {
        "style": "lofi",
        "seed": 80,
        "pattern": _p(
            "Lo-Fi",
            swing=0.24,
            kick="x......x..x.....",
            snare="....x.......x...",
            hat="x.x.x.x.x.x.x.x.",
        ),
        "params": {
            "kick": {
                "tune": 50.0,
                "decay": 0.35,
                "punch": 0.5,
                "click": 0.1,
                "drive": 1.3,
            },
            "snare": {"tune": 180.0, "decay": 0.28, "tone": 0.25, "snap": 0.45},
            "rim": {"tune": 380.0, "decay": 0.05},
            "hat": {"decay": 0.04, "tone": 6500.0, "open_hat": False},
            "ohat": {"decay": 0.3, "tone": 8000.0},
        },
        "vel": {"7": 0.45},
    },
    # Cinematic (~70): taiko-ish low toms, a big low kick and a long crash
    # for the hit, a soft snare
    "Cinematic": {
        "style": "cinematic-toms",
        "seed": 70,
        "pattern": _p(
            "Cinematic",
            kick="x.......x.......",
            snare="............x...",
            tom="x..x..x.x.x.xxxx",
            crash="x...............",
        ),
        "params": {
            "kick": {
                "tune": 40.0,
                "decay": 1.0,
                "punch": 0.5,
                "click": 0.1,
                "drive": 1.5,
            },
            "snare": {"tune": 140.0, "decay": 0.45, "tone": 0.8, "snap": 0.35},
            "tom": {"tune": 80.0, "decay": 0.8},
            "hat": {"decay": 0.03, "tone": 7000.0, "open_hat": False},
            "crash": {"decay": 3.0},
        },
        "vel": {},
    },
    # Chiptune (~140, straight): a short square-ish kick (a fast pitch fall
    # driven flat), a snare that is all noise, short noise hats
    "Chiptune": {
        "style": "chiptune",
        "seed": 8,
        "pattern": _p(
            "Chiptune",
            kick="x.......x.......",
            snare="....x.......x...",
            hat="x.x.x.x.x.x.x.x.",
        ),
        "params": {
            "kick": {
                "tune": 65.0,
                "decay": 0.15,
                "punch": 1.0,
                "click": 0.0,
                "drive": 4.0,
            },
            "snare": {"tune": 240.0, "decay": 0.12, "tone": 0.0, "snap": 1.0},
            "hat": {"decay": 0.02, "tone": 8000.0, "open_hat": False},
            "ohat": {"decay": 0.12, "tone": 8000.0},
        },
        "vel": {},
    },
    # Hardstyle (~150): the long, driven, pitched kick the genre is (the
    # bus's Hard Kick Drive takes it further), a clap on 2 and 4, an open
    # hat on every offbeat
    "Hardstyle": {
        "style": "hardstyle",
        "seed": 150,
        "pattern": _p(
            "Hardstyle",
            kick="x...x...x...x...",
            clap="....x.......x...",
            ohat="..x...x...x...x.",
        ),
        "params": {
            "kick": {
                "tune": 55.0,
                "decay": 0.8,
                "punch": 1.0,
                "click": 0.7,
                "drive": 4.0,
            },
            "clap": {"decay": 0.25, "spread": 0.012},
            "hat": {"decay": 0.03, "tone": 10000.0, "open_hat": False},
            "ohat": {"decay": 0.2, "tone": 9000.0},
            "crash": {"decay": 2.0},
        },
        "vel": {},
    },
    # Phonk (~140): a boomy 808 kick, a hard clap, tight rolling hats, and
    # the cowbell — the rim tuned up to 540 Hz (its two partials 540 and
    # 864, the drum machine cowbell's pair near enough; the rim's decay is
    # its own, so it is a short one)
    "Phonk": {
        "style": "drift-phonk",
        "seed": 139,
        "pattern": _p(
            "Phonk",
            kick="x.........x.....",
            clap="....x.......x...",
            hat="xxxxxxxxxxxxxxxx",
            rim="..x.......x..x..",
        ),
        "params": {
            "kick": {
                "tune": 45.0,
                "decay": 0.8,
                "punch": 0.9,
                "click": 0.3,
                "drive": 2.8,
            },
            "snare": {"tune": 200.0, "decay": 0.2, "tone": 0.5, "snap": 0.9},
            "clap": {"decay": 0.18, "spread": 0.01},
            "hat": {"decay": 0.035, "tone": 9000.0, "open_hat": False},
            "rim": {"tune": 540.0, "decay": 0.15},
        },
        "vel": {},
    },
}
