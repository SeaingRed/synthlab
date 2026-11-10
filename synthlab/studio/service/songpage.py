"""The Song page's context: the song, its drawer, lanes and thumbnails.

`song_context` is the "song" section of `Page.page_context` (and Synths'
track-edit banner); `_song_drawer` / `_drawer_of` resolve what the drawer
has open (`state.song_drawer`) into what it draws, `_lane_row` an
automation lane, `_thumb` a pool pattern's thumbnail; `calibration` is the
mic latency file. Drawing only: the song's audio is `song.py`.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import json
import math

from ...sequencer import midi, name_of
from .. import clips, limits, longaudio, rollview, schema, song_bridge, song_play
from .. import song_model as sm
from ..curves import GAIN, LANE_TARGETS, lane_range, parse_fx
from ..fxrack import FX_UNITS, MAX_UNITS, rack_rows
from ..gen import chordtrack
from ..paramui import spec_of
from ..state import DRUM_ROWS, ROOTS, SCALE_CHOICES, StudioState

# what an FX parameter's readout says after its number (the rack itself
# shows bare numbers); a share (mix, feedback) reads as a percentage
FX_PARAM_UNITS = {
    "cutoff": "Hz",
    "mid_freq": "Hz",
    "low": "dB",
    "mid_gain": "dB",
    "high": "dB",
    "mix": "%",
    "feedback": "%",
    "depth": "%",
}


def lane_label(target: str) -> str:
    """An automation target as the Song page names it: "Level" for the
    track's gain, a field's own panel label (`paramui`), "Reverb mix" for
    an FX unit's parameter."""
    if target == GAIN:
        return "Level"
    if fx := parse_fx(target):
        return f"{FX_UNITS[fx[1]]['label']} {fx[2].replace('_', ' ')}"
    spec = spec_of(target)
    return spec.label if spec else target


def _cell_lane_row(lane: dict, track: dict) -> dict:
    """`_lane_row` for a lane on one of `track`'s cells. An FX lane names
    its unit as the drawer's menu does ("Reverb 2 mix" when the chain holds
    two); `missing`: an FX lane whose unit the track no longer has — it
    plays nothing, and the drawer offers only its removal."""
    row = _lane_row(lane)
    target = lane["target"]
    row["missing"] = target not in sm.lane_targets(track)
    for unit, choices in sm.fx_lane_menu(track):
        for t, param in choices:
            if t == target:
                row["label"] = f"{unit} {param.replace('_', ' ')}"
    return row


def _lane_text(target: str, v: float) -> str:
    """A lane value (0..1 of its range) as the drawer reads it out: the
    level as a percentage of the track's own, a field or an FX parameter
    in its unit."""
    lo, hi = lane_range(target)
    x = lo + v * (hi - lo)
    if target == GAIN:
        return f"{x * 100:.0f}%"
    if fx := parse_fx(target):
        unit = FX_PARAM_UNITS.get(fx[2], "")
        if unit == "%":
            return f"{x * 100:.0f}%"
        text = f"{x:.0f}" if hi - lo >= 100 else f"{x:.1f}" if unit else f"{x:.2f}"
        return f"{text} {unit}".strip()
    spec = spec_of(target)
    text = f"{x:.0f}" if hi - lo >= 100 else f"{x:.2f}"
    return f"{text} {spec.unit}".strip() if spec else text


def _lane_row(lane: dict) -> dict:
    """One lane for the drawer: its start and end values, the mid
    breakpoint the sliders edit (a three-point lane's; a longer lane, only
    ever imported, shows its ends), readouts and a thumbnail's points."""
    target, pts = lane["target"], lane["points"]
    mid = pts[1] if len(pts) == 3 else None
    return {
        "target": target,
        "label": lane_label(target),
        "points": len(pts),
        "start": pts[0][1],
        "end": pts[-1][1],
        "mid": mid,
        "start_text": _lane_text(target, pts[0][1]),
        "end_text": _lane_text(target, pts[-1][1]),
        "mid_text": _lane_text(target, mid[1]) if mid else "",
        # the polyline `curves.curve_svg` draws (viewBox 0 0 100 40), as
        # plain numbers: the Song templates mark nothing safe
        "shape": " ".join(f"{t * 100:.1f},{40 - v * 40:.1f}" for t, v in pts),
    }


def _thumb(p: dict) -> dict:
    """A pool pattern's mini thumbnail, drawn as an SVG in `song_pool.html`:
    `w` steps by `h` rows and one `(x, y)` mark per hit or note. Drums: a
    row per voice (DRUM_ROWS order). Notes: a row per semitone between the
    lowest and highest note, highest on top."""
    if p["kind"] == "drums":
        w = p["bars"] * p["steps"]
        order = {v: i for i, v in enumerate(DRUM_ROWS)}
        marks = [
            (int(step), order[voice])
            for voice, step in p["drums"]
            if voice in order and 0 <= int(step) < w
        ]
        return {"w": max(w, 1), "h": len(DRUM_ROWS), "marks": marks}
    w = p["bars"] * p["steps_per_bar"]
    notes = []
    for pitch, step in p["cells"]:
        try:
            notes.append((int(step), midi(str(pitch))))
        except (ValueError, KeyError, IndexError):
            continue
    notes = [(x, m) for x, m in notes if 0 <= x < w]
    if not notes:
        return {"w": max(w, 1), "h": 1, "marks": []}
    top = max(m for _, m in notes)
    h = max(top - min(m for _, m in notes) + 1, 5)
    return {"w": max(w, 1), "h": h, "marks": [(x, top - m) for x, m in notes]}


def time_true(song: dict) -> dict | None:
    """The playlist's time-true axis when a section keeps its own meter or
    tempo (`sm.timed`): {section id: {"x0", "bs", "w"}} — where its first
    bar starts and how wide one of its bars is, in BASE bars (a 4/4 bar at
    the song's tempo, `--bar-w` px), and its whole width. A bar of 6/8 at
    the same tempo is 0.75 of one, a section at half the tempo twice as
    wide, a ramp's bars their average. None for a song whose every section
    is 4/4 at its tempo: the playlist it always was, bar for bar."""
    if not any(sm.timed(song, s) for s in song["sections"]):
        return None
    ref = 240.0 / float(song["bpm"])
    x, out = 0.0, {}
    for sec in song["sections"]:
        bars = max(1, int(sec["bars"]))
        bs = round(sm.section_clock(song, sec).seconds(bars) / bars / ref, 4)
        w = bs * bars * int(sec["repeats"])
        out[sec["id"]] = {"x0": round(x, 4), "bs": bs, "w": round(w, 4)}
        x += w
    return out


def ruler(song: dict) -> dict:
    """The bar ruler over the playlist: `bars` (every bar of every repeat),
    the `numbers` it prints — every 4th bar from 1, the step doubled until
    there are at most `MAX_RULER_NUMBERS` — and `cap`, where the song's
    length cap falls. A bar is 240 / bpm seconds, so `MAX_SONG_SECONDS` is
    the bar line after bar `bpm * MAX_SONG_SECONDS / 240`: drawn when it is
    inside the ruler or within 16 bars of its end, else None.

    On a time-true axis (`time_true`: a section in its own meter or tempo)
    every x is in base bars: `at` {number: its x + 1}, `segs` the sections'
    own tick runs ({x, n: width, bs}) and `width` the whole; the cap is the
    same seconds, so the same base bar. Absent otherwise."""
    bars = sm.song_bars(song)
    step = 4
    while bars / step > limits.MAX_RULER_NUMBERS:
        step *= 2
    cap = round(song["bpm"] * limits.MAX_SONG_SECONDS / 240.0, 3)
    out = {
        "bars": bars,
        "step": step,
        "numbers": list(range(1, bars + 1, step)),
        "cap": cap,
    }
    axis = time_true(song)
    width = bars
    if axis:
        at, start = {}, 0
        for sec in song["sections"]:
            span = sec["bars"] * sec["repeats"]
            a = axis[sec["id"]]
            for n in out["numbers"]:
                if start < n <= start + span:
                    at[n] = round(a["x0"] + (n - 1 - start) * a["bs"] + 1, 4)
            start += span
        width = round(sum(a["w"] for a in axis.values()), 4)
        out |= {
            "at": at,
            "segs": [{"x": a["x0"], "n": a["w"], "bs": a["bs"]} for a in axis.values()],
            "width": width,
        }
    out["cap"] = cap if bars and cap <= width + 16 else None
    return out


def take_bars(data_dir, song: dict) -> dict[str, float]:
    """{clip name: its length in song bars} for every take the song holds:
    seconds x bpm / 240, two decimals — what a take's chip is as wide as.
    A header read a clip (`clips.seconds`); a missing clip is 0."""
    names = {a["clip"] for sec in song["sections"] for a in sec["audio"]}
    bpm = song["bpm"]
    return {n: round(clips.seconds(data_dir, n) * bpm / 240.0, 2) for n in names}


def blocks(song: dict) -> dict[tuple[str, str], dict]:
    """What each filled cell draws on the playlist, {(section id, track
    id): {at, len, tiles}} in bars: where its block starts in a pass of the
    section (`sm.cell_span`: its span, else the whole section), how long it
    is, and how many times its pattern's thumbnail is
    tiled across it — 0 once the fragment has drawn
    `limits.MAX_PLAYLIST_TILES` (an over-cap import: the rest are plain
    colour). An echo of a block costs one more (a `<use>` of its tiles)."""
    out, drawn = {}, 0
    pats = song["patterns"]
    for sec in song["sections"]:
        for tid, pid in sec["cells"].items():
            p = pats.get(pid) if pid else None
            if p is None:
                continue
            at, n = sm.cell_span(sec, tid)
            tiles = math.ceil(n / sm.pattern_bars(p) - 1e-9)
            cost = tiles + sec["repeats"] - 1
            if drawn + cost > limits.MAX_PLAYLIST_TILES:
                tiles = 0
            else:
                drawn += cost
            out[(sec["id"], tid)] = {"at": at, "len": n, "tiles": tiles}
    return out


# what a track's "write…" menu offers: a generator and what it is called
GEN_KINDS = (
    ("bass_rolling", "rolling bass"),
    ("bass_root", "long roots"),
    ("bass_offbeat", "offbeat bass"),
    ("bass_walk", "funk bass"),
    ("bass_2step", "two-step sub"),
    ("chords", "chords"),
    ("pad", "pad"),
    ("arp", "arp"),
    ("lead", "lead"),
    ("chops", "chops"),
)


def chord_context(song: dict, scope: str = "song") -> dict:
    """What `chord_strip.html` draws for `scope` ("song", or a section id —
    anything else is the song): the chord track in force there, its slots
    in order with each chord's name, the progressions on offer in this key
    and each degree's triad. The song's own also carries `by_section` (each
    section's chords in one line, for its head) and `own` (this, for every
    section with chords of its own: its ⋯ menu edits them)."""
    from ..gen import build, chordtrack

    sec = next((x for x in song["sections"] if x["id"] == scope), None)
    track = chordtrack.track_of(song, sec)
    slots = sorted(track["slots"], key=lambda x: x["at"])
    out = {
        "scope": sec["id"] if sec else "song",
        "name": sec["name"] if sec else "the song",
        "bars": track["bars"],
        "label": chordtrack.label(song, track),
        "slots": [
            {
                "deg": x["deg"],
                "q": x["q"],
                "inv": x["inv"],
                "beats": round(x["len"], 2),
                "roman": chordtrack.roman(song, x),
                "name": chordtrack.name(song, x),
            }
            for x in slots
        ],
        "degrees": [
            {
                "deg": d,
                "roman": chordtrack.roman(song, {"deg": d, "q": "triad"}),
                "name": chordtrack.name(song, {"deg": d, "q": "triad"}),
            }
            for d in range(1, 8)
        ],
        "qualities": sm.CHORD_QUALITIES,
        "menu": [
            {"label": m["label"], "bars": m["bars"]} for m in build.progressions(song)
        ],
    }
    if sec is None:
        out["by_section"] = {
            x["id"]: chordtrack.label(song, chordtrack.track_of(song, x))
            for x in song["sections"]
        }
        out["own"] = {
            x["id"]: chord_context(song, x["id"])
            for x in song["sections"]
            if (x.get("chords") or {}).get("slots")
        }
    return out


# how many chord tiles the playlist draws before a section falls back to
# its chords in one line (an over-cap import; a legal song is far under it)
MAX_CHORD_TILES = 640


def playlist_context(song: dict) -> dict:
    """The playlist tools' share of the Song page's context:

    - `chord_tiles`: {section id: {scope, own, label, tiles}} — the chord
      track on the time axis. `tiles` are `chordtrack.tiles` over one pass
      (each with its slot's place, degree and the beat its slot starts on),
      `scope` what an edit of one changes ("song", or the section's id when
      it has chords of its own); None past `MAX_CHORD_TILES`.
    - `song_twins`: {section id: names} — the sections that share EVERY
      block with it (`songops.twins`): the head says so.
    - `bound_trends`: {section id: "rising" | "falling" | "level"}, the
      energy step INTO each section but the first (the + on its boundary).
    - `bound_kinds`: the boundary menu's rows.
    - `long_names`: the whole stems an insert or a delete would move the
      arrangement under (they keep their bar): the confirm names them."""
    from .. import songops

    secs = song["sections"]
    tiles: dict = {}
    drawn = 0
    for sec in secs:
        own = bool((sec.get("chords") or {}).get("slots"))
        track = chordtrack.track_of(song, sec)
        slots = sorted(track["slots"], key=lambda s: s["at"])
        got = chordtrack.tiles(song, sec)
        drawn += len(got)
        tiles[sec["id"]] = {
            "scope": sec["id"] if own else "song",
            "own": own,
            "label": chordtrack.label(song, track),
            "tiles": [
                {
                    **t,
                    "deg": slots[t["index"]]["deg"] if t["index"] < len(slots) else 1,
                    # its quality and inversion: the menu marks them pressed
                    "q": slots[t["index"]].get("q", "triad")
                    if t["index"] < len(slots)
                    else "triad",
                    "inv": int(slots[t["index"]].get("inv", 0))
                    if t["index"] < len(slots)
                    else 0,
                    # where the slot starts in its progression, in beats: what
                    # a drag of the tile's left edge moves
                    "beat": round(t["at"] * 4, 2),
                }
                for t in got
            ]
            if drawn <= MAX_CHORD_TILES
            else None,
        }
    return {
        "chord_tiles": tiles,
        "song_twins": songops.twins(song),
        "bound_trends": {
            b["id"]: songops.trend(a, b) for a, b in zip(secs, secs[1:], strict=False)
        },
        "bound_kinds": songops.BOUNDARY_KINDS,
        "long_names": [x["name"] for x in sm.long_items(song)],
    }


_PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def key_choices(root: str | None = None) -> list[tuple[str, str]]:
    """(value, label) for a key select: the twelve keys by name, C to B —
    a key is a name ("E minor"), its octave is the bass's register, not the
    key's. Each value is the root nearest `root` (the song's now: a change
    of key moves the bass the shortest way), or, with none, in the
    starters' own register (E1 to D#2, where every style's keys sit); held
    inside `ROOTS`."""
    lo, hi = midi(ROOTS[0]), midi(ROOTS[-1])
    at = midi(root) - 6 if root else midi("E1")
    at = min(max(at, lo), hi - 11)
    window = {m % 12: name_of(m) for m in range(at, at + 12)}
    return [(window[i], _PITCH_CLASSES[i]) for i in range(12)]


def songcraft_context(song: dict, arr, layers) -> dict:
    """Song creation's share of the Song page's context: the starter
    sheet's cards (`song_styles`) and the song's own style, the chord
    strip, the checklist, every key and scale, the roles a track can have,
    the generators a track's menu offers, the transition kinds, and what
    "From my loop" would arrange (`loop_info`)."""
    from ... import transitions
    from ..gen import checklist, styles

    gen = song.get("gen") or {}
    own = (gen.get("params") or {}).get("style")
    return {
        "song_styles": [styles.card(k) for k in styles.STYLES],
        "song_style": own if own in styles.STYLES else None,
        "song_style_last": None,  # the browser's last (`page_context` reads the cookie)
        "song_started": gen.get("kind") == "start" and own in styles.STYLES,
        "song_seed": gen.get("seed"),
        # the topbar's key select (nearest the song's root) and the starter
        # sheet's (the starters' register): names, never octave numbers
        "song_keys": key_choices(song.get("root") or None),
        "start_keys": key_choices(),
        "song_scales": SCALE_CHOICES,
        "song_roles": [(r, sm.ROLE_LABELS[r]) for r in sm.TRACK_ROLES],
        "gen_kinds": GEN_KINDS,
        "transition_kinds": list(transitions.GENERATORS),
        "song_check": checklist.context(song),
        "chord_strip": chord_context(song),
        **playlist_context(song),
        "loop_info": {
            "bars": math.lcm(max(1, arr.bars), max(1, arr.drum_bars)),
            "lanes": 1
            + sum(bool(x.get("patch") and x.get("cells")) for x in layers[1:]),
            "drums": bool(arr.drums),
        },
    }


class SongPage:
    """What the Song page's regions draw."""

    # the service's field this unit reads (set in StudioService.__init__;
    # test_service_split.py holds this list to the code)
    state: StudioState

    def song_context(self) -> dict:
        """What the Song page's regions (and Synths' track-edit banner)
        draw, all cheap: the song itself, which sections use each pattern,
        the song's length, the track whose sound Synths is editing, and the
        drawer's pattern.

        - `song`: the song dict (tracks, patterns, sections).
        - `song_usage`: {pattern id: [section ids using it]}.
        - `song_bars` / `song_seconds`: every bar of every repeat.
        - `song_edit`: the track being edited on Synths, or None.
        - `song_drawer`: {pattern, section, track} or None. A cell's drawer
          shows whatever the cell holds now (pattern None for an empty
          cell); a pool pattern's has section and track None. `hidden_count`
          counts a notes pattern's notes outside the key's rows; `rows` are
          the note grid's rows (the song key's).
        - `song_thumbs`: {pattern id: `_thumb`} for the pool's thumbnails.
        - `song_takes`: how many note takes can be undone.
        - `song_undo`: the song ring's next steps by label
          (`StudioState.song_undo_state`): {undo, redo, ...}, None = none.
        - `song_latency_ms`: the mic latency calibration.
        - `loudness_targets`: the export's loudness choices
          (`song_play.LOUDNESS_TARGETS`).
        - `lane_labels`: {automation target: its name} (`lane_label`), for
          the arranger's cell markers; the drawer's lanes are in
          `song_drawer` (`lanes`, `lane_choices`).
        - `song_long`: the whole stems on the timeline (`longaudio.rows`):
          `items` for the list under the arranger, `parts` for the strip
          each section's audio cell shows.
        - `song_ruler`: the playlist's bar ruler (`ruler`).
        - `song_blocks`: each filled cell's block on the playlist (`blocks`).
        - `take_bars`: {clip: bars} for the song's takes (`take_bars`).

        Only in contexts that name the "song" section (`SONG_NEEDS`)."""
        s = self.state
        song = s.song
        edit = None
        if s.song_edit_track is not None:
            i = sm.track_index(song, s.song_edit_track)
            edit = song["tracks"][i] if i is not None else None
        return {
            "song": song,
            "song_usage": {
                pid: sm.pattern_usage(song, pid) for pid in song["patterns"]
            },
            "song_bars": sum(sec["bars"] * sec["repeats"] for sec in song["sections"]),
            # the time-true axis (`time_true`): None for a 4/4 song at one
            # tempo, else each section's place and bar width in base bars
            "song_time": time_true(song),
            # the sections that keep their own clock (`sm.timed`): the jam
            # cannot launch them (its one bar clock is the song's)
            "song_timed": {s["id"] for s in song["sections"] if sm.timed(song, s)},
            "song_seconds": sm.song_seconds(song),
            "song_edit": edit,
            # the section whose part of that track Synths' ▶ plays
            # (`render_inputs`): None = the phrase, in the song's key
            "song_edit_from": (
                (song_bridge.track_alone(song, edit["id"]) or (None, None))[1]
                if edit
                else None
            ),
            "song_drawer": self._song_drawer(),
            "song_thumbs": {pid: _thumb(p) for pid, p in song["patterns"].items()},
            "song_takes": len(s.take_history),
            "song_undo": s.song_undo_state(),
            "song_latency_ms": self.calibration()["latency_ms"],
            "loudness_targets": song_play.LOUDNESS_TARGETS,
            "song_long": longaudio.rows(self.state.data_dir, song),
            "song_ruler": ruler(song),
            "song_blocks": blocks(song),
            "take_bars": take_bars(self.state.data_dir, song),
            # song creation: the starter sheet, the chord strip, the
            # checklist, the song's own key choices (`songcraft_context`)
            **songcraft_context(song, self.state.arrangement, self.state.layers),
            "lane_labels": {t: lane_label(t) for t in LANE_TARGETS}
            | {
                ln["target"]: lane_label(ln["target"])
                for sec in song["sections"]
                for lanes in sec.get("auto", {}).values()
                for ln in lanes
            },
        }

    def calibration(self) -> dict:
        """This machine's mic latency calibration
        (studio_data/calibration.json, schema.CALIBRATION): 0 ms when the
        file is missing or hostile."""
        try:
            path = self.state.data_dir / "calibration.json"
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        return schema.conform(raw, schema.nodes().CALIBRATION)

    def _song_drawer(self) -> dict | None:
        s = self.state
        want = s.song_drawer or {}
        song = s.song
        si = sm.section_index(song, want.get("section_id"))
        ti = sm.track_index(song, want.get("track_id"))
        if si is not None and ti is not None:
            sec, track = song["sections"][si], song["tracks"][ti]
            p = song["patterns"].get(sec["cells"].get(track["id"]))
            return self._drawer_of(p, sec, track)
        pid = want.get("pattern_id")
        p = song["patterns"].get(pid) if isinstance(pid, str) else None
        return self._drawer_of(p, None, None) if p else None

    def drawer_octave(self, p: dict, span: int) -> int:
        """The octave window the drawer shows a notes pattern at: the one
        chosen with the roll's ▲ / ▼ while this drawer is open
        (`song_drawer["oct"]`), else the one holding the most of its notes
        (`sm.window_octave`)."""
        chosen = (self.state.song_drawer or {}).get("oct")
        if isinstance(chosen, int):
            return chosen
        return sm.window_octave(self.state.song, p, span)

    def _drawer_roll(self, p: dict, sec, track, rows: list[str]) -> dict:
        """The drawer's roll: the key's rows marked, the chord track's
        tones under each step, and — on a cell — the section's other
        synth tracks as ghost notes, each regridded to this pattern's
        steps and tiled over its length (`sm._regrid`)."""
        song = self.state.song
        spb, total = p["steps_per_bar"], p["bars"] * p["steps_per_bar"]
        ghosts = []
        if sec is not None and track is not None:
            for i, other in enumerate(song["tracks"]):
                q = song["patterns"].get(sec["cells"].get(other["id"]))
                if other["id"] == track["id"] or not q or q["kind"] != "notes":
                    continue
                cells = sm._regrid(q["cells"], q["bars"], q["steps_per_bar"], spb)
                own = q["bars"] * spb
                tiled = [
                    [c[0], c[1] + at]
                    for at in range(0, total, max(1, own))
                    for c in cells
                    if c[1] + at < total
                ]
                ghosts.append((i, other["name"], tiled))
        chords = chordtrack.resolve(song, sec, p["bars"], spb)
        return rollview.context(
            rows,
            total,
            root=song["root"],
            scale_name=song["scale_name"],
            ghosts=ghosts,
            chords=[(c["lo"], c["hi"], c["root"], c["tones"]) for c in chords],
            chord_track=True,
        )

    def _drawer_of(self, p: dict | None, sec, track) -> dict:
        """The drawer's context. `hidden_count`: a notes pattern's notes on
        pitches outside the song key's grid rows (written under another
        key) — kept by the grid, but not drawn ("N notes outside the key")."""
        song = self.state.song
        span = self.state.grid_span
        hidden = 0
        # the octave window the grid is drawn at: the one holding the
        # most of the pattern's notes (`sm.window_octave`; 0 = from the
        # song's root, every grid before there was a window)
        octave = 0
        roll = None
        if p is not None and p["kind"] == "notes":
            octave = self.drawer_octave(p, span)
            shown = sm.window_rows(song, span, octave)
            rows = set(shown)
            hidden = sum(c[0] not in rows for c in p["cells"])
            roll = self._drawer_roll(p, sec, track, shown)
        # an audio track's row in a section: its rack and its lanes, which
        # take a take of it in the section as a cell's take a pattern
        audio = bool(sec and track and track["kind"] == "audio")
        lanes = (
            sm.cell_lanes(sec, track["id"]) if sec and track and (p or audio) else []
        )
        used = {ln["target"] for ln in lanes}
        live = bool(p and sec) or (audio and sm.has_takes(sec, track["id"]))
        room = live and len(lanes) < limits.MAX_CELL_LANES
        using = set(sm.pattern_usage(song, p["id"])) if p else set()
        return {
            "pattern": p,
            "section": sec,
            "track": track,
            # an audio track's row: no pattern, its rack and its lanes
            "audio": audio,
            "takes": audio and sm.has_takes(sec, track["id"]),
            "rack": rack_rows(track.get("fx")) if audio else [],
            "rack_units": (
                [(k, spec["label"]) for k, spec in FX_UNITS.items()] if audio else []
            ),
            "rack_full": audio and len(track.get("fx") or []) >= MAX_UNITS,
            # the sections playing this pattern, by name in song order: an
            # edit changes all of them ("Make unique" is for more than one)
            "shared_in": [s["name"] for s in song["sections"] if s["id"] in using],
            # the cell's automation lanes as the drawer draws them, and the
            # targets it can still add (none on a pool pattern)
            "lanes": [_cell_lane_row(ln, track) for ln in lanes],
            "lane_choices": [
                (t, lane_label(t))
                for t in (sm.lane_targets(track) if room else ())
                if t not in used and not parse_fx(t)
            ],
            # ... and its FX units' parameters, a group per unit
            "lane_fx_choices": [
                (unit, left)
                for unit, choices in (sm.fx_lane_menu(track) if room else ())
                if (left := [(t, name) for t, name in choices if t not in used])
            ],
            "hidden_count": hidden,
            # how many bars one loop of the cell's section is (the solo
            # audition's playhead: its WAV is one cycle)
            "cycle_bars": sm.cycle_bars(song, sec) if sec else None,
            # the block's place in its section, in bars: (at, len), and
            # whether it is trimmed (the drawer's from / to / Fill row, and
            # where the two playheads count the pattern from)
            "block": sm.cell_span(sec, track["id"]) if sec and p else None,
            "trimmed": bool(sec and p and track["id"] in (sec.get("spans") or {})),
            # the note grid's rows: the song key's at Perform's range
            # (`state.grid_span`, one switch for both grids)
            "rows": sm.window_rows(song, span, octave),
            "span": span,
            "oct": octave,
            # what the piano roll draws behind the notes (`rollview`)
            "roll": roll,
            # the bars this pattern may have at its resolution (8 while
            # bars x steps fits the grid)
            "bar_choices": sm.bar_choices(
                p.get("steps", p.get("steps_per_bar", 16)) if p else 16
            ),
            # a beat's family by letter (A-D and its fill): the pool
            # pattern that is each variant, and which one this cell plays
            "variants": sm.family(song, p) if p and p["kind"] == "drums" else [],
            # a pool pattern's ▶ Play: the lane it is heard through (the
            # one chosen, else a synth lane being edited in the dock, else
            # `song_bridge.pattern_lane`'s) and the lanes it could be
            "through": self._through(p) if p and not sec else None,
            # ... and with no lane of its kind, the working loop's stands in
            "stand_in": bool(p and not sec and self._through(p) is None),
            "through_choices": [
                t
                for t in song["tracks"]
                if p and t["kind"] == ("drums" if p["kind"] == "drums" else "synth")
            ],
            # the synth track whose sound the pattern plays with (the
            # cell's, or a pool pattern's "through" lane): the head's sound
            # picker swaps it (POST /song/track/{id}/sound), ✎ docks it
            "sound_track": self._sound_track(p, sec, track),
        }

    def _sound_track(self, p: dict | None, sec, track) -> dict | None:
        if p is None or p["kind"] != "notes":
            return None
        if sec is None:
            through = self._through(p)
            ti = sm.track_index(self.state.song, through) if through else None
            track = self.state.song["tracks"][ti] if ti is not None else None
        return track if track is not None and track["kind"] == "synth" else None

    def _through(self, p: dict) -> str | None:
        s = self.state
        want = (s.song_drawer or {}).get("through")
        if want is None and s.track_edit_view() == "editor":
            want = s.song_edit_track
        return song_bridge.pattern_lane(s.song, p["id"], want)
