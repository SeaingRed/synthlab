"""Per-area template contexts: what each part of a page draws.

One method per area, each a plain dict built from the session and cheap
enough for a region to be re-sent from it alone: the envelope drawings,
the voice take, the drum lab and its XY, the XY pad and macros, the sound
bar and chain bar, the layer strip, the reset menu, the note grid, the
beat launcher, the chord bar, and the Sample Lab (which reads its own
store). `Page.page_context` assembles a page from them; the regions'
watches and lean contexts (`regions.py`) call them one at a time. The Song
page's are in `songpage.py`.

Also here: the small helpers the routes import beside the service
(`group_specs`, `dxy_spec`, `xy_norm`, `xy_value`) and the one
`SpaceStore`.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from ...explore import SpaceStore
from ...params import PARAMS, denorm, norm
from ...sequencer import midi
from ...synth import Patch
from .. import (
    booth,
    chords,
    cliplib,
    lab,
    labchops,
    labmake,
    limits,
    longaudio,
    meter,
    rollview,
    song_bridge,
    sources,
)
from .. import song_model as sm
from ..chain import chain_summary, motion_lines
from ..clips import clip_hash
from ..drumlab import DRUM_PARAMS, DRUM_VOICES, VOICE_PRESETS, defaults_of, specs_of
from ..fxrack import clean_chain
from ..gen import chordtrack
from ..gen import loop as gen_loop
from ..labjobs import LabJobs
from ..paramui import CONTINUOUS_CHOICES, MACRO_CHOICES, PARAM_GROUPS, spec_of
from ..soundmeta import envelope_svg
from ..state import RESET_SCOPES, RESET_VIEWS, StudioState
from ..state.model import GRID_OCTAVES, grid_rows, materialise
from ..voice import panel_context, read_lab


def _layer_name(lay: dict) -> str | None:
    p = lay.get("patch")
    if p is None:
        return None
    return p.get("name") if isinstance(p, dict) else getattr(p, "name", None)


def _octave(octave: int) -> str:
    """-1 -> "−1 oct", 0 -> "±0" (the strip always names the octave)."""
    return f"{'+' if octave > 0 else '−'}{abs(octave)} oct" if octave else "±0"


# the pages that play the song, not the loop: the status strip names the
# song's tempo and key there
SONG_PAGES = frozenset({"song", "mixer"})

PAD_VOICES = ("kick", "snare", "hat", "ohat", "clap", "tom", "rim", "crash")
DXY_AXES = (("hat", "decay"), ("kick", "punch"))  # x, y


def dxy_spec(voice: str, param: str) -> dict:
    return next(sp for sp in specs_of(voice) if sp["name"] == param)


def group_specs(group: str) -> list | None:
    """The param specs of one PARAM_GROUPS panel (panel reset, dice and
    panel presets), or None for an unknown group."""
    for name, specs in PARAM_GROUPS:
        if name == group:
            return specs
    return None


def xy_norm(name: str, value: float) -> float:
    """The XY pad's 0..1 position for a control's value (params.norm)."""
    return 0.0 if spec_of(name) is None else norm(name, value)


def xy_value(name: str, n: float) -> float:
    assert spec_of(name) is not None
    return denorm(name, n)


class Contexts:
    """The per-area contexts a page and its regions are drawn from."""

    # the service's fields this unit reads (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    data_dir: Path
    state: StudioState
    templates: Jinja2Templates
    _space: SpaceStore
    lab: LabJobs

    # -- context pieces ----------------------------------------------------
    def env_amp_svg(self, p: Patch) -> str:
        return envelope_svg(
            "env-amp", p.attack, p.decay, p.sustain, p.release, p.env_curve
        )

    def env_filter_svg(self, p: Patch) -> str:
        return envelope_svg(
            "env-filter",
            p.filter_attack,
            p.filter_decay,
            p.filter_sustain,
            p.filter_release,
            p.env_curve,
        )

    def voice_context(self) -> dict:
        """Everything `fragments/voice_panel.html` draws: the take on disk,
        the vocoder's settings beside it, the last audition."""
        s = self.state
        ctx = panel_context(s.data_dir, s.patch, s.arrangement)
        ctx["bo"]["place"] = cliplib.place_context(s.song)  # the booth's -> Song
        held = read_lab(s.data_dir)  # the booth's tempo: the loop's or the song's
        ctx["bo"] |= booth.tempo_context(
            held["booth"],
            held["take"],
            s.song,
            s.arrangement,
            s.song_edit_track is not None,
        )
        return ctx

    def drum_voice_values(self, voice: str) -> dict:
        """Engine kwargs for a drum voice: its lab params over the defaults
        (the drum panels' values and a Perform pad's hit are the same)."""
        return defaults_of(voice) | self.state.arrangement.drum_params.get(voice, {})

    def drum_voice_identity(self, voice: str) -> dict:
        """Everything a drum pad's one-shot depends on: the synth hit's
        params, or — when the row plays a clip (`drum_samples`) — that clip
        by content, as the loop plays it (arrange._voice_sample)."""
        ident: dict = {"params": self.drum_voice_values(voice)}
        sample = (self.state.arrangement.drum_samples or {}).get(voice)
        if sample:
            ident["sample"] = [sample, clip_hash(self.state.data_dir, sample)]
        return ident

    def drum_voice_hash(self, voice: str) -> str:
        blob = json.dumps(self.drum_voice_identity(voice), sort_keys=True).encode()
        return hashlib.sha1(blob).hexdigest()[:8]

    def drums_context(self) -> dict:
        return {
            "drum_params": DRUM_PARAMS,
            "voice_presets": VOICE_PRESETS,
            "drum_values": {v: self.drum_voice_values(v) for v in DRUM_VOICES},
            "drum_hash": {v: self.drum_voice_hash(v) for v in DRUM_VOICES},
        }

    def dxy_context(self) -> dict:
        (xv, xp), (yv, yp) = DXY_AXES
        xs, ys = dxy_spec(xv, xp), dxy_spec(yv, yp)
        cur = self.state.arrangement.drum_params
        xval = (cur.get(xv) or {}).get(xp, xs["default"])
        yval = (cur.get(yv) or {}).get(yp, ys["default"])
        return {
            "pad_voices": PAD_VOICES,
            "pad_hash": {v: self.drum_voice_hash(v) for v in PAD_VOICES},
            "dxy": {
                "label_x": f"{xv} {xs['label'].lower()}",
                "label_y": f"{yv} {ys['label'].lower()}",
                "nx": (xval - xs["min"]) / (xs["max"] - xs["min"]),
                "ny": (yval - ys["min"]) / (ys["max"] - ys["min"]),
            },
        }

    def xy_context(self) -> dict:
        s = self.state
        xparam, yparam = s.xy_axes
        return {
            "xy_axes": s.xy_axes,
            "xy_choices": CONTINUOUS_CHOICES,
            "xy_x_spec": spec_of(xparam),
            "xy_y_spec": spec_of(yparam),
            "xy_x_log": PARAMS[xparam].kind == "log",
            "xy_y_log": PARAMS[yparam].kind == "log",
            "xy_nx": xy_norm(xparam, getattr(s.patch, xparam)),
            "xy_ny": xy_norm(yparam, getattr(s.patch, yparam)),
            "xy_gesture": s.xy_gesture,
        }

    def xy_fragment(self, oob: bool = False) -> str:
        return self.templates.get_template("fragments/xypad.html").render(
            **self.xy_context(), oob=oob
        )

    def macro_context(self) -> dict:
        return {"macros": self.state.macros, "macro_choices": MACRO_CHOICES}

    def sound_context(self) -> dict:
        """What the Synths identity line and chain bar draw: cheap, so
        do_render can send both after every edit without a page_context."""
        s = self.state
        return {
            "patch": s.patch,
            "layers": s.layers,
            "active_layer": s.active_layer,
            "sound_dirty": s.is_dirty("sound"),
            "track_hear": self.track_hear(),
            **self.chain_context(),
        }

    def track_hear(self) -> dict | None:
        """Synths while it edits a song track's sound (None otherwise):
        the track, the section ▶ plays it in (None: no section plays it,
        and ▶ plays the phrase in the song's key), what ▶ hears there
        ("mix" / "alone", `StudioService.track_heard`) and the song's tempo
        and key — the sound bar shows these in place of the loop's phrase,
        bpm and key, which ▶ does not play while a track is edited."""
        s = self.state
        if s.track_edit_view() != "editor":
            return None
        song, tid = s.song, s.song_edit_track
        i = sm.track_index(song, tid)
        at = song_bridge.heard_section(song, tid, s.song_drawer)
        sec = song["sections"][at] if at is not None else None
        pid = sec["cells"].get(tid) if sec else None
        return {
            "name": song["tracks"][i]["name"] if i is not None else "",
            "section": sec["name"] if sec else None,
            "pattern": song["patterns"][pid]["name"] if pid in song["patterns"] else "",
            "hear": s.song_edit_hear,
            "bpm": song["bpm"],
            "root": song["root"],
            "scale": song["scale_name"],
        }

    def status_context(self, view: str = "") -> dict:
        """What the status strip draws (`fragments/status_bar.html`): the
        sound, and the tempo and key of what the page plays — the song's
        on Song and Mix (they play the song, never the loop), the loop's
        everywhere else. Two tempos on one screen read as a bug."""
        s = self.state
        song = view in SONG_PAGES
        src = s.song if song else s.arrangement
        get = src.get if song else lambda k: getattr(src, k)
        return {
            "patch": s.patch,
            "sound_dirty": s.is_dirty("sound"),
            "st_of": "song" if song else "loop",
            "st_bpm": get("bpm"),
            # the key, not its register: the octave is the bass's, not the key's
            "st_key": f"{get('root')[:-1]} {get('scale_name').replace('_', ' ')}",
            # its bar when it is not 4/4 (meter.py): the song's own, the loop's
            "st_meter": (src.get("meter") if song else src.meter) or "4/4",
        }

    def lane_follow(self) -> dict | None:
        """When the edited layer follows the main lane: which layers and the
        octave (Perform's grid says it isn't what plays). Else None."""
        s = self.state
        act = s.active_layer
        if not s.follows(act):
            return None
        return {
            "layer": act + 1,
            "main": s.main_index() + 1,
            "octave": s.layers[act]["follow"]["octave"],
        }

    def layer_context(self) -> dict:
        """The Synths layer strip (`fragments/layer_strip.html`): per slot,
        what it is, what it plays and whether ▶ on Synths hears it — and
        why not. Editing (the panels' slot) and hearing are separate facts,
        so the strip shows both."""
        s = self.state
        main, act = s.main_index(), s.active_layer
        any_solo = any(lay.get("solo") for lay in s.layers)
        rows = []
        for i, lay in enumerate(s.layers):
            editing = i == act
            name = s.patch.name if editing else _layer_name(lay)
            empty = not editing and not lay.get("patch")
            octave = lay["follow"]["octave"] if s.follows(i) else None
            notes = len(s.arrangement.cells if editing else lay.get("cells") or [])
            if i == main:
                plays = "main · plays the phrase"
            elif octave is not None:
                plays = f"follows main {_octave(octave)}"
            elif empty:
                plays = "empty"
            elif editing:
                plays = "plays the phrase while edited"
            else:
                # no notes of its own: on Synths it plays the phrase (state.lanes)
                plays = (
                    f"own notes ({notes})"
                    if notes
                    else "plays the phrase (no notes of its own)"
                )
            if empty:
                why = "empty"
            elif s.layer_focus and editing:
                why = ""
            elif lay.get("muted"):
                why = "muted"
            elif float(lay.get("gain", 1.0)) <= 0.0:
                why = "level at 0"
            elif any_solo and not lay.get("solo"):
                why = "another layer is soloed"
            elif s.layer_focus:
                why = "▶ is set to This layer"
            else:
                why = ""
            rows.append(
                {
                    "index": i,
                    "name": name or "Untitled",
                    "role": lay.get("role"),
                    "main": i == main,
                    "octave": octave,
                    "editing": editing,
                    "empty": empty,
                    "hear": not why,
                    "why": why,
                    "plays": plays,
                    "gain": float(lay.get("gain", 1.0)),
                    "muted": bool(lay.get("muted")),
                    "solo": bool(lay.get("solo")),
                }
            )
        hear = self.track_hear()
        return {
            "strip": rows,
            # a song track's edit: the strip says so instead of its slots
            "song_track": hear["name"] if hear else None,
            "layer_focus": s.layer_focus,
            "can_add": s.free_slot() is not None,
            # ✕ on the edited layer moves the panels to another sound
            # (`clear_layer`): there has to be one
            # — and the edited slot has to be the loop's own, not a song
            # track's sound standing in it
            "can_clear_edited": s.song_edit_track is None
            and any(lay.get("patch") for i, lay in enumerate(s.layers) if i != act),
        }

    def chain_context(self) -> dict:
        """The chain bar's stages (patch, the active layer's FX, master FX)
        and what is moving the sound: its template reads nothing else."""
        s = self.state
        fx_layer = clean_chain(s.layers[s.active_layer].get("fx"))
        return {
            "chain": chain_summary(s.patch, fx_layer, clean_chain(s.fx_master)),
            "motion": motion_lines(s.patch),
        }

    def reset_rows(self, view: str) -> tuple[list, bool]:
        s = self.state
        scopes = RESET_VIEWS.get(view, ("sound",))
        rows = [
            (sc, RESET_SCOPES[sc][0], s.baseline_label(sc), s.is_dirty(sc))
            for sc in scopes
        ]
        return rows, any(dirty for *_, dirty in rows)

    def space_store(self) -> SpaceStore:
        """The one SpaceStore (breed tiles, the map): it keeps the log's
        rows in memory, so a tile lookup no longer re-reads the file."""
        return self._space

    def lab_context(self, selected: str = "") -> dict:
        """What the Sample Lab draws: the sources, the selected one's panel
        (`selected`, else the most recently opened), the running job and
        what separation would need. Reads the source store and the job
        only — no session state but what a region would be sent *to*: the
        session tempo a clip is fitted to, the drum grid's, and the song's
        tempo and sections (a take is placed in one)."""
        metas = sources.list_sources(self.data_dir)
        chosen = next((m for m in metas if m["id"] == selected), None)
        if chosen is None and metas:
            chosen = metas[0]
        last = self.lab.last
        song = self.state.song
        on_song = self.state.long_usage()  # whole stems the song plays
        return {
            "said": "",  # what the last action found (routes/lab.py `body`)
            "rows": [],
            "go": {},  # where what it made went, and its undo (`body`'s `go`)
            "song": {
                "bpm": round(float(song["bpm"]), 1),
                "sections": [
                    {"id": sec["id"], "name": sec["name"], "bars": sec["bars"]}
                    for sec in song["sections"]
                ],
            },
            "pattern_bars": labmake.MAX_PATTERN_BARS,
            # "→ drum grid" brings the song's tempo to the loop (when it has one)
            "loop_tempo": labmake.loop_tempo(chosen) if chosen else None,
            "session_bpm": float(self.state.arrangement.bpm),
            "models": self.lab.models(),
            "sources": [
                {
                    "id": m["id"],
                    "name": m["name"],
                    "duration": lab.clock(m["seconds"]),
                    "stems": bool(m["stems"]["model"]),
                    "selected": chosen is not None and m["id"] == chosen["id"],
                    # what deleting it would silence on the song
                    "on_song": longaudio.delete_note(on_song.get(m["id"])),
                }
                for m in metas
            ],
            "selected": chosen["id"] if chosen else "",
            # the panel, with the source's chops (which read the tempos a
            # chop is fitted to and how many places use its clip)
            "source": labchops.source_context(self.data_dir, chosen, self.state)
            if chosen
            else None,
            "job": self.lab.state(),
            "last_error": last["error"] if last else "",
            "separation": self.lab.availability(),
            "codec": sources.available(),
            "max_mb": limits.MAX_SOURCE_UPLOAD_BYTES // 1_000_000,
            "max_minutes": int(limits.MAX_SOURCE_SECONDS // 60),
            "max_sources": limits.MAX_SOURCES,
        }

    def chords_context(self) -> dict:
        """The "chords" section of `page_context`: everything the chord bar
        (`fragments/chord_bar.html`, the `chord-bar` region) draws for the
        key — and nothing else, so a key change re-sends it cheaply."""
        arr = self.state.arrangement
        # chord pads sit at octave 3 (audible, on the keyboard) whatever
        # the grid's (bass) root octave is
        key3 = arr.root.rstrip("-0123456789") + "3"
        return {
            "arrangement": arr,  # the key label and the harmonics' root
            "voicings": list(chords.VOICINGS),
            "chord_sets": {
                v: chords.scale_chords(key3, arr.scale_name, v) for v in chords.VOICINGS
            },
            # note pads: the key's tones over octaves 3-4, the melodic range
            # a thumb reaches for most
            "note_pads": chords.scale_notes(key3, arr.scale_name),
            "harmonic_pads": chords.harmonic_notes(arr.root),
            # (the ~500-button chord poster is /fragment/chordlib's, fetched
            # on first open — no page template renders it)
            # the loop's chord track, step by step, for "chords in key"
            # following the chord under the playhead (chords.js; Perform's
            # chord lane sets it): [lo, hi, degree, name, roman, notes at
            # octave 3, root position] over the note grid's steps
            "loop_track": self.loop_track(key3),
            # the loop's bar in beats (meter.py): audio.js's `S.barBeats`,
            # which `S.nextBar` and the keys arp count bars by
            "bar_beats": meter.bar_beats(arr.meter),
        }

    def loop_track(self, key3: str) -> dict | None:
        """The working loop's chord track as chords.js follows it (`spb`,
        `total` steps, `tiles`); None while the loop has no chords. A
        tile's degree is the pad it lights: the key's tone its root is on
        (in a key that is not seven-tone a chord counts in the parent,
        `chordtrack.chord_scale`, while the pads are the key's own; 0, no
        pad, for a root outside it — A pentatonic minor's F)."""
        a = self.state.arrangement
        tiles = gen_loop.lane(a)
        if not tiles:
            return None
        base = midi(key3)
        pads = chordtrack.scale_of(gen_loop.song_of(a))
        return {
            "spb": int(a.steps_per_bar),
            "total": int(a.steps_per_bar) * max(1, int(a.bars)),
            "tiles": [
                [
                    t["lo"],
                    t["lo"] + t["span"],
                    pads.index(t["semis"][0]) + 1 if t["semis"][0] in pads else 0,
                    t["name"],
                    t["roman"],
                    [base + s for s in t["semis"]],
                ]
                for t in tiles
            ],
        }

    def reset_context(self, view: str) -> dict:
        """The "resets" section of `page_context`: everything
        `reset_menu.html` draws. `do_render` renders the menu from this
        alone after every edit — a whole `page_context` (both envelope
        drawings, the filter curve, the XY and macro contexts) was built
        per slider post only to be thrown away."""
        rows, dirty = self.reset_rows(view)
        prev = self.state.slot_prev
        return {
            "reset_scopes": rows,
            "reset_dirty": dirty,
            "slot_prev_name": prev.name if prev is not None else None,
            # the Drums menu can put the factory patterns back
            "bank_factory": view == "drums",
        }

    def lane_heads(self) -> list[dict]:
        """The lane heads (the Loops page's lane inspector): every layer
        slot that holds a
        sound, and the edited one — its name, whether it follows or is the
        main lane, its M / S (the layer strip's own routes) and whether ▶
        hears it (`audible`: mute wins over solo)."""
        s = self.state
        lanes = []
        for i, lay in enumerate(s.layers):
            editing = i == s.active_layer
            if not (editing or lay.get("patch")):
                continue
            lanes.append(
                {
                    "index": i,
                    "name": s.patch.name if editing else _layer_name(lay),
                    "editing": editing,
                    "follows": s.follows(i),
                    "main": i == s.main_index(),
                    "muted": bool(lay.get("muted")),
                    "solo": bool(lay.get("solo")),
                    "heard": s.audible(i),
                }
            )
        return lanes

    def grid_context(self) -> dict:
        """What the note grid draws beyond its cells: its range, the key's
        root rows (the piano roll's C lines), the notes it keeps but cannot
        show (another key's rows, past a shrunk grid) and whether ×2 fits."""
        s, a = self.state, self.state.arrangement
        rows = grid_rows(a, s.grid_span, s.grid_oct)
        total = a.bars * a.steps_per_bar
        shown = set(rows)
        tonic = a.root.rstrip("-0123456789")
        # the piano roll: the other lanes as ghost notes (a follower's are
        # the main lane's at its octave: what it plays)
        main = s.main_arrangement()
        ghosts, lanes = [], self.lane_heads()
        for ln in lanes:
            lay = s.layers[ln["index"]]
            if not ln["editing"] and not lay.get("muted"):
                plays = materialise(lay, main) if s.follows(ln["index"]) else lay
                ghosts.append((ln["index"], ln["name"], plays["cells"]))
        # the loop's chord track (Perform's chord lane): its
        # tones under each step of the roll, its tiles over it
        song = gen_loop.song_of(a)
        held = (
            chordtrack.resolve(song, None, a.bars, a.steps_per_bar)
            if "chords" in song
            else []
        )
        tones = [(c["lo"], c["hi"], c["root"], c["tones"]) for c in held]
        return {
            "grid_span": s.grid_span,
            "grid_oct": s.grid_oct,
            "oct_range": GRID_OCTAVES,
            "gen_loop_kinds": gen_loop.KINDS,
            "lanes": lanes,
            "lane_room": s.free_slot() is not None,
            # the empty slot a lane head's ＋ ▾ puts a new lane in
            "lane_next": s.free_slot(),
            "roll": rollview.context(
                rows,
                total,
                root=a.root,
                scale_name=a.scale_name,
                ghosts=ghosts,
                chords=tones,
                chord_track=bool(tones),
            ),
            "chord_lane": self.loop_chord_lane(),
            "root_rows": {r for r in rows if r.rstrip("-0123456789") == tonic},
            "hidden_notes": sum(
                1 for c in a.cells if c[0] not in shown or not 0 <= int(c[1]) < total
            ),
            "can_double": a.bars * 2 in limits.BAR_CHOICES,
            "can_double_drums": a.drum_bars * 2 in limits.BAR_CHOICES,
        }

    def loop_chord_lane(self) -> dict:
        """What Perform's chord lane draws (fragments/loop_chords.html, in
        the note grid): the loop's chords as tiles over its steps
        (`gen_loop.lane`), the progressions on offer in its key, each
        degree's triad for a tile's menu, and the track's length — the
        Song page's chord strip and tiles, for the working loop."""
        from ..gen import build

        song = gen_loop.song_of(self.state.arrangement)
        track = chordtrack.track_of(song)
        return {
            "tiles": gen_loop.lane(self.state.arrangement),
            "label": chordtrack.label(song, track) if "chords" in song else "",
            "bars": track["bars"],
            "slots": len(track["slots"]),
            "menu": [
                {"label": m["label"], "bars": m["bars"]}
                for m in build.progressions(song)
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
        }

    def pool_beats(self) -> dict:
        """Perform's Beats launcher: the pool's drums patterns, and the id
        of the one whose hits and clock the working loop's drum lane holds.

        A song with no drums patterns — every fresh studio: the pool starts
        empty — shows the Drums bank's filled slots instead
        (`beat_source` "bank", each beat's `id` its slot number as a
        string), launched through the bank's own load
        (`/fragment/perform/bank/{slot}` -> `StudioState.bank_load`). The
        launcher was an empty box until a pattern was made on the Song
        page, beside a bank full of factory beats."""
        s = self.state
        arr = s.arrangement
        lane = (arr.drum_bars, arr.drum_steps, sorted(map(tuple, arr.drums)))
        pool = [p for p in s.song["patterns"].values() if p["kind"] == "drums"]
        if pool:
            beats, source = pool, "pool"
            clocks = [
                (p["bars"], p["steps"], sorted(map(tuple, p["drums"]))) for p in pool
            ]
        else:
            filled = [(i, p) for i, p in enumerate(s.drum_bank) if p]
            beats = [{"id": str(i), "name": p["name"]} for i, p in filled]
            source = "bank"
            clocks = [
                (p["bars"], p["steps_per_bar"], sorted(map(tuple, p["steps"])))
                for _, p in filled
            ]
        playing = next(
            (p["id"] for p, clock in zip(beats, clocks, strict=True) if clock == lane),
            None,
        )
        return {"pool_beats": beats, "beat_playing": playing, "beat_source": source}
