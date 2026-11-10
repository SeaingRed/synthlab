"""The live vocal chain: a mic sung into over the music, heard
through a rack of live FX units as it is sung, recorded in time.

The browser does the audio (static/vocal.js: the mic -> input gain and a
high-pass -> the chain, built by livefx.js's own stages -> the monitor and
the recorder). What the server holds is the chain: `voice_lab.json`'s
`live` (`clean_live`), beside the take and the booth — never the session,
never a render key — edited with the shared rack (`fxrack.edit_chain`) and
loaded whole from a vocal style (`STYLES`). `GET /voice/live.json` hands it
to the browser resolved (`fxlive.live_chain`, the keys' own numbers).

A style is data, like the booth's treatments: a label (a nod is
allowed), a title naming the techniques, and a chain of ordinary units —
every one live (`fxlive.FX_LIVE` "js" or "native"), so what is heard while
singing is what the chain is. A style is a technique, never a record.

⏺ keeps two clips (`POST /take/vocal`): the processed take, its meta the
chain it was sung through (`take_meta`), and the dry one beside it
(`dry_name`), which "→ booth" makes the Voice take so the booth can treat
it again.
"""

from __future__ import annotations

import copy
import time
from dataclasses import replace
from pathlib import Path

from .clips import clip_dir, list_clips
from .fxlive import FX_LIVE, keys_bpm, live_chain
from .fxrack import FX_UNITS, MAX_UNITS, clean_chain, rack_rows

SOURCE = "vocal"  # a processed take's clip meta source
DRY_SOURCE = "vocal dry"  # the dry take kept beside it
DRY_SUFFIX = " dry"


def _u(kind: str, **params) -> dict:
    return {"kind": kind, "params": params}


# label, a title naming the techniques (never a record), the chain (live
# units only; at most fxrack.MAX_UNITS)
STYLES: dict[str, dict] = {
    "psych_haze": {
        "label": "Psych Haze vocal",
        "title": "Modern neo-psych: the voice squashed hard, doubled, slowly "
        "phased, into a wavering tape echo on the dotted eighth and a long plate",
        "chain": [
            _u("vox_comp", threshold=-30.0, ratio=8.0, attack=0.002, makeup=8.0),
            _u("doubler", time=0.03, wobble=0.6, spread=0.8, mix=1.0),
            _u("phaser", rate=0.15, depth=0.8, mix=0.45),
            _u("tape_echo", sync=3.0, feedback=0.5, wow=0.45, tone=0.35, mix=0.35),
            _u("plate", decay=3.5, tone=0.7, predelay=0.025, mix=0.32),
        ],
    },
    "sixties": {
        "label": "Sixties Studio vocal",
        "title": "Late-'60s desk tricks: ADT doubling, a touch of rotating "
        "speaker, a quick tape slap (its wow the tape's own wander) and a "
        "bright plate",
        "chain": [
            _u("vox_comp", threshold=-20.0, ratio=4.0, makeup=4.0),
            _u("doubler", time=0.028, wobble=0.7, spread=0.7, mix=1.0),
            _u("rotary", speed=0.0, drive=0.6, mix=0.35),
            _u("tape_echo", time=0.11, feedback=0.12, wow=0.5, tone=0.45, mix=0.35),
            _u("plate", decay=1.8, tone=0.6, predelay=0.012, mix=0.22),
        ],
    },
    "robot": {
        "label": "Robot / filter house vocal",
        "title": "French-touch robot voice: auto-tune snapping hard to the key, "
        "an octave under it, squeezed, through a resonant low-pass and a phaser",
        "chain": [
            _u("autotune", speed=0.0),
            _u("harmony", first=5.0, first_level=0.45, first_pan=0.0),
            _u("vox_comp", threshold=-24.0, ratio=6.0, makeup=6.0),
            _u("lowpass", cutoff=2600.0, q=3.0),
            _u("phaser", rate=0.25, depth=0.6, mix=0.3),
        ],
    },
    "pop": {
        "label": "Pop polish",
        "title": "Clean modern pop: the room gated out, gentle auto-tune, "
        "even compression, the esses tamed, a short vocal plate",
        "chain": [
            _u("vgate", threshold=-50.0, ratio=2.0, depth=20.0),
            _u("autotune", speed=0.2, humanize=0.15),
            _u("vox_comp", threshold=-22.0, ratio=4.0, makeup=5.0),
            _u("deess", threshold=-30.0, depth=8.0),
            _u("plate", decay=1.6, tone=0.7, predelay=0.02, mix=0.2),
        ],
    },
    "telephone": {
        "label": "Telephone verse",
        "title": "A verse sung from far away: squeezed, the lows and the air "
        "gone down a phone line, one short slap behind it",
        "chain": [
            _u("vox_comp", threshold=-24.0, ratio=6.0, makeup=6.0),
            _u("telephone", centre=1300.0, width=1.8, drive=3.0, mix=1.0),
            _u("tape_echo", time=0.09, feedback=0.1, wow=0.2, tone=0.4, mix=0.25),
        ],
    },
    "choir": {
        "label": "Choir",
        "title": "One voice as three: a harmonizer a third and a fifth up, "
        "spread wide, doubled and into a big room",
        "chain": [
            _u("autotune", speed=0.4, humanize=0.3),
            _u("harmony", first=0.0, first_pan=-0.7, second=1.0, second_pan=0.7),
            _u("doubler", time=0.035, wobble=0.5, spread=0.5, mix=0.6),
            _u("reverb", decay=3.2, mix=0.35, size=0.9),
        ],
    },
    "clean": {
        "label": "Clean",
        "title": "Only the fixes: the room gated out, even compression, the "
        "esses tamed — build your own from + add…",
        "chain": [
            _u("vgate", threshold=-50.0),
            _u("vox_comp"),
            _u("deess"),
        ],
    },
}
DEFAULT_STYLE = "clean"

assert all(len(s["chain"]) <= MAX_UNITS for s in STYLES.values())
assert all(
    FX_LIVE[u["kind"]] in ("js", "native") for s in STYLES.values() for u in s["chain"]
), "a vocal style is heard as it is sung: live units only"


def style(name) -> dict | None:
    """What a style sets: its name and a clean copy of its chain."""
    st = STYLES.get(name) if isinstance(name, str) else None
    if st is None:
        return None
    return {"style": name, "chain": clean_chain(copy.deepcopy(st["chain"]))}


def clean_live(raw) -> dict:
    """`voice_lab.json`'s `live`: the style last loaded (a name, or "")
    and the chain. Missing, it is the default style's."""
    if not isinstance(raw, dict):
        return style(DEFAULT_STYLE)
    name = raw.get("style")
    return {
        "style": name if isinstance(name, str) and name in STYLES else "",
        "chain": clean_chain(raw.get("chain")),
    }


def edited(live: dict) -> bool:
    st = style(live["style"])
    return st is not None and st["chain"] != live["chain"]


def label(live: dict) -> str:
    st = STYLES.get(live["style"])
    name = st["label"] if st else "Your chain"
    return f"{name} (edited)" if st and edited(live) else name


def live_units() -> list[tuple[str, str]]:
    """What the vocal rack's adder offers: every unit heard live."""
    return [
        (k, s["label"]) for k, s in FX_UNITS.items() if FX_LIVE[k] in ("js", "native")
    ]


def live_json(live: dict, s) -> dict:
    """`GET /voice/live.json`: the chain as the browser builds it — the
    keys' own resolution (`fxlive.live_chain`) at the keys' tempo, on the
    session's key (an auto-tune's "song key"), with no drums to duck under
    — and its words."""
    arr = replace(s.arrangement, drums=[], bpm=keys_bpm(s))
    return {
        "fx": live_chain(live["chain"], arr, drums=False),
        "label": label(live),
        "style": live["style"],
    }


def dry_name(name: str) -> str:
    return f"{name}{DRY_SUFFIX}"


def take_meta(live: dict, bpm: float, bars: float, dry: str, s) -> dict:
    """A processed take's clip meta: what it was sung through, at which
    tempo and key, and the dry take kept beside it."""
    a = s.arrangement
    return {
        "source": SOURCE,
        "at": round(time.time(), 3),
        "bpm": round(bpm, 3),
        "bars": round(bars, 3),
        "root": a.root,
        "scale": a.scale_name,
        "dry": dry,
        "vocal": {
            "style": live["style"],
            "label": label(live),
            "chain": copy.deepcopy(live["chain"]),
        },
    }


def takes(data_dir: Path, limit: int = 3) -> list[dict]:
    """The newest processed vocal takes (newest first), each with its dry
    twin's name while that is there."""
    root = clip_dir(data_dir)
    found = []
    for c in list_clips(data_dir):
        if c.get("source") != SOURCE:
            continue
        try:
            at = (root / f"{c['name']}.wav").stat().st_mtime
        except OSError:
            continue
        dry = c.get("dry") if isinstance(c.get("dry"), str) else ""
        there = bool(dry) and (root / f"{dry}.wav").is_file()
        found.append(
            (
                at,
                {
                    "name": c["name"],
                    "seconds": c.get("seconds", 0.0),
                    "dry": dry if there else "",
                },
            )
        )
    return [c for _at, c in sorted(found, key=lambda f: -f[0])[:limit]]


def panel_context(data_dir: Path, live: dict, song: dict) -> dict:
    """What `fragments/vocal_panel.html` draws (`vx`)."""
    return {
        "styles": [(k, st["label"], st["title"]) for k, st in STYLES.items()],
        "style": live["style"],
        "label": label(live),
        # ↺ reset: the style's own chain again (yours: the default style's)
        "reset_to": live["style"] or DEFAULT_STYLE,
        "reset_label": STYLES[live["style"] or DEFAULT_STYLE]["label"],
        "rack": rack_rows(live["chain"]),
        "units": live_units(),
        "rack_full": len(live["chain"]) >= MAX_UNITS,
        "takes": takes(data_dir),
        "sections": [
            {"id": x["id"], "name": x["name"]} for x in song.get("sections", [])
        ],
    }
