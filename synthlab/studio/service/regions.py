"""The region table: what each out-of-band region shows, and its watch.

`REGIONS` is the one table (`Region`: template, sections, pages, watch,
lean context, `inside`); the `_w_*` functions are the watches — a cheap
signature of exactly the state a region draws, never a render — and the
`_ctx_*` the lean contexts. Declarative: nothing here sends anything.
`refresh.py` (`Refresh`) is the machinery that compares watches and
appends the regions that moved; `routing.StudioRoute` drives it.

No mixin: a watch takes the service as an argument (`svc`) and reads the
session and the per-area contexts through it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from fastapi import Request

from .. import (
    beatpick,
    cliphub,
    cliplib,
    clips,
    keytakes,
    longaudio,
    loopcoach,
    looplib,
    samplerview,
)
from .. import song_model as sm
from ..files import dir_sig
from ..soundmeta import filter_response_svg
from ..views import VIEW_PARTS
from .page import (
    BANK_NEEDS,
    BAR_NEEDS,
    CLIPLIB_NEEDS,
    PANEL_PAGES,
    SONG_NEEDS,
    TAKES_NEEDS,
    song_sections,
)
from .songpage import _thumb

if TYPE_CHECKING:
    from . import StudioService


# -- out-of-band regions ------------------------------------------------------
# One page draws the same state in several places — the drum bars in the
# transport chips *and* the grid, the sections on the Song page *and*
# Perform's section launcher, the patch in the panels *and* the chain bar,
# the "edited" line and the reset menu's dots. Routes used to have to
# remember every region their change touched, and the ones that forgot left
# them stale until a reload (an FX add left the chain bar and the reset
# menu behind). Now each region says, once, what it shows:
#
#   template  its fragment, whose root carries the page's own id and
#             `hx-swap-oob` when rendered with `oob`
#   needs     the `page_context` sections it renders from (unless `context`)
#   pages     the views whose page draws it
#   watch     (svc, view) -> a cheap signature of exactly the state it shows
#             (never a render); a region whose signature moved is stale
#   context   an optional lean context (svc, request, view) -> dict for the
#             regions re-sent on hot paths, instead of a `page_context`
#   inside    the region it is drawn inside, if any: its watch leaves the
#             inner region's state out, and a re-sent outer one carries it
#
# `routing.StudioRoute` snapshots the watches of the requesting page around
# every state-changing request and appends the regions whose watch moved
# (`StudioService.refresh`), so a route never names a region.
# `tests/studio/test_oob_refresh.py` holds every POST route to "exactly the
# regions whose HTML changed" and every watch to its region's HTML.
ALL_VIEWS = frozenset(VIEW_PARTS)


def _sig(*values) -> str:
    """A watch value: `repr` of what a region shows. A string, so it is a
    copy taken now (state is mutated in place all over) and cheap to
    compare."""
    return repr(values)


def _clips_sig(svc: StudioService) -> tuple:
    # a clip is its WAV plus a .meta.json (bpm, source): both count (read
    # once a `watch_page`: `cliplib.sig_memo`)
    return cliplib.clips_sig(svc.state.data_dir)


def _median(x) -> float:
    if isinstance(x, int | float):
        return float(x)
    return float(np.median(np.asarray(x, dtype=np.float64)))


def _w_reset(svc: StudioService, view: str) -> str:
    rows, _dirty = svc.reset_rows(view)
    prev = svc.state.slot_prev
    return _sig(rows, prev.name if prev is not None else None, view == "drums")


def _w_track_edit(svc: StudioService, view: str) -> str:
    s = svc.state
    tid = s.song_edit_track
    if tid is None:
        return _sig(None)
    i = sm.track_index(s.song, tid)
    # the id too: two tracks may share a name, and the Song page's dock
    # frames whichever one is edited
    return _sig(tid, s.song["tracks"][i]["name"] if i is not None else None)


def _w_sound_bar(svc: StudioService, view: str) -> str:
    # everything sound_bar.html draws outside its #sound-id
    s, arr = svc.state, svc.state.arrangement
    return _sig(
        arr.bpm,
        arr.root,
        arr.scale_name,
        s.audition,
        s.vs_beat,
        # Beat ▾, its kit, ▶ beat's cue and the phrase's length over the beat
        beatpick.watch(s),
        # ▶ beat's data-beat-key: the beat as the Drums view renders it
        svc.beat_key(),
        s.slot_a.name if s.slot_a is not None else None,
        s.slot_b.name if s.slot_b is not None else None,
        s.ab_live,
        # a song track's edit: ▶'s "in the mix / alone" and the song's key
        svc.track_hear(),
    )


def _w_sound_id(svc: StudioService, view: str) -> str:
    s = svc.state
    return _sig(
        s.patch.name,
        s.is_dirty("sound"),
        s.active_layer,
        len(s.layers),
        svc.track_hear(),
    )


def _w_chord_bar(svc: StudioService, view: str) -> str:
    # the key label, the pads and data-chords: all of it is the key — and
    # data-track, the loop's chords over its grid (chords.js follows them)
    a = svc.state.arrangement
    # (the grid's shape only while it has chords: no loop without moves)
    held = (a.chords, a.bars, a.steps_per_bar) if a.chords else ()
    # data-bar-beats: the loop's bar (S.barBeats: the keys arp, the follow)
    return _sig(a.root, a.scale_name, *held, *((a.meter,) if a.meter != "4/4" else ()))


def _w_chain(svc: StudioService, view: str) -> str:
    ctx = svc.chain_context()
    return _sig(ctx["chain"], ctx["motion"])


def _w_filter_viz(svc: StudioService, view: str) -> str:
    p = svc.state.patch
    # what the drawing reads: a drawn curve's median (soundmeta), and
    # filter 2 while it plays
    two = ()
    if p.filter2_route != "off":
        two = (
            p.filter2_route,
            p.filter2_type,
            _median(p.filter2_cutoff),
            _median(p.filter2_resonance),
        )
    return _sig(p.filter_type, _median(p.cutoff), _median(p.resonance), *two)


def _w_amp_viz(svc: StudioService, view: str) -> str:
    p = svc.state.patch
    return _sig(p.attack, p.decay, p.sustain, p.release, p.env_curve)


def _w_filter_env_viz(svc: StudioService, view: str) -> str:
    p = svc.state.patch
    return _sig(
        p.filter_attack, p.filter_decay, p.filter_sustain, p.filter_release, p.env_curve
    )


def _w_sampler(svc: StudioService, view: str) -> str:
    return _sig(samplerview.watch(svc.state.patch))


def _ctx_sampler(svc: StudioService, request: Request, view: str) -> dict:
    return {"sampler": samplerview.context(svc.state.patch)}


def _w_clips(svc: StudioService, view: str) -> str:
    # the drum rows' clip menus show the clips' names, nothing else: not a
    # file's mtime or size (a meta rewritten — a keys take kept — or a WAV
    # recorded again moves nothing they draw)
    return _sig(tuple(n for n, *_ in _clips_sig(svc) if n.endswith(".wav")))


def _w_status(svc: StudioService, view: str) -> str:
    """The status strip in the top dock, on every page: the sound being
    edited, whether it has moved since it was loaded or saved, and the
    tempo and key of what the page plays: the song's on Song and Mix."""
    st = svc.status_context(view)
    return _sig(
        svc.state.patch.name,
        st["sound_dirty"],
        st["st_bpm"],
        st["st_key"],
        st["st_meter"],
    )


def _ctx_status(svc: StudioService, request: Request, view: str) -> dict:
    return svc.status_context(view)


def _w_clip_library(svc: StudioService, view: str) -> str:
    """The drawer on every page: the store's listing, where the session
    (and the song's undo ring) holds each clip — delete says so — the Lab's
    separated songs and the ones a clip came from, and what differs by
    page: the action each clip offers, and on Song the sections and audio
    tracks it can be placed into. No file is opened (`cliplib.sources_sig`).
    A keys take is one of the Loops at its top (`looplib.watch`): its files
    and where it is used count there, and in the rail's count — not in the
    clip groups, which never draw it (★ Keep's meta rewrite, a take placed
    in the song re-send nothing of theirs)."""
    s = svc.state
    listing = _clips_sig(svc)
    takes = {t["name"] for t in keytakes.takes(s.data_dir)}

    def mine(file: str) -> bool:
        return file.removesuffix(".meta.json").removesuffix(".wav") not in takes

    return _sig(
        tuple(f for f in listing if mine(f[0])),
        sum(f[0].endswith(".wav") for f in listing),  # the rail's count
        view,
        # the Loops at its top (the search narrows them, but a redraw from
        # anywhere else draws the whole list)
        looplib.watch(svc.loops_context(view)),
        sorted((k, v) for k, v in s.clip_usage().items() if k not in takes),
        cliplib.sources_sig(s.data_dir, listing),
        # on Song: where a clip can go, and (a dragged row's preview is its
        # length in song bars) the tempo
        (cliplib.place_sig(s.song), s.song["bpm"]) if view == "song" else None,
    )


def _ctx_clip_library(svc: StudioService, request: Request, view: str) -> dict:
    return svc.clip_library_context(request, view)


def _w_hub(svc: StudioService, view: str) -> str:
    """The clip hub's list: the clip store's listing (a meta rewritten
    moves it), the sample store's, where the session uses each clip and
    each sample, which Lab songs are separated or still there, which clips
    have been measured and to what, and the hub's ↶ Undo. The filter and
    the selection are the page's URL, which no post moves. No file is
    opened."""
    s = svc.state
    listing = _clips_sig(svc)
    return _sig(
        listing,
        cliphub.samples_sig(s.data_dir),
        sorted(s.clip_usage().items()),
        sorted(cliphub.sample_usage(s).items()),
        cliplib.sources_sig(s.data_dir, listing),
        cliphub.facts_sig(s.data_dir, listing),
        svc.hub_undo_label(),
    )


def _ctx_hub(svc: StudioService, request: Request, view: str) -> dict:
    return {"hub": svc.hub_context(request, bench=False)}


def _w_key_takes(svc: StudioService, view: str) -> str:
    """The keyboard dock's strip (every page): this session's loops and
    takes, as drawn (`looplib.watch`: the one open on the grid by name
    only, so a stroke there sends no list). A loop or take is read again
    only when its own files moved (`loops.listing`, `keytakes.takes`). Not
    the clip store's listing itself: any clip's bounce, rename or delete
    moves that, and the strip would be re-sent with nothing of its own
    changed (tests/studio/test_oob_refresh.py)."""
    return _sig(looplib.watch(svc.loops_context(view, session=True)))


def _ctx_key_takes(svc: StudioService, request: Request, view: str) -> dict:
    return {"key_takes": svc.loops_context(view, session=True)}


def _w_loops_panel(svc: StudioService, view: str) -> str:
    """Perform's Loops panel: every loop, take and Drums bank beat
    (`looplib.watch`; the bank's slots are the session's, so a Save to
    slot moves it). Its search, sort and filters are the page's
    (loopspanel.js), never posted: a redraw is the whole list."""
    return _sig(looplib.watch(svc.loops_context(view, bank=True)))


def _ctx_loops_panel(svc: StudioService, request: Request, view: str) -> dict:
    return {"loops_panel": svc.loops_context(view, bank=True)}


def _w_coach(svc: StudioService, view: str) -> str:
    """Perform's ✦ Coach: what its rules find on the loop, the options, the
    proposal and its diff, the thread — `loopcoach.context` itself (memoised
    on what it reads), so it moves exactly when the panel would draw
    something else. The dock round it is the page's."""
    return loopcoach.watch(svc.state)


def _ctx_coach(svc: StudioService, request: Request, view: str) -> dict:
    return {"coach": loopcoach.context(svc.state)}


def _w_drum_transport(svc: StudioService, view: str) -> str:
    a = svc.state.arrangement
    return _sig(
        a.bpm, a.drum_bars, a.drum_steps, a.drum_swing, a.drum_humanize, a.bars, a.meter
    )


def _w_meter(svc: StudioService, view: str) -> str:
    return svc.state.arrangement.meter


def _ctx_meter(svc: StudioService, request: Request, view: str) -> dict:
    return {"arrangement": svc.state.arrangement}


def _w_drum_bank(svc: StudioService, view: str) -> str:
    s, a = svc.state, svc.state.arrangement
    return _sig(
        s.drum_bank,
        s.active_slot,
        a.drums,
        a.drum_vel,
        a.drum_hits,
        a.drum_row_swing,
        a.drum_bars,
        a.drum_steps,
        a.meter,
        # the grid's rows draw each voice's mute and solo, not its level
        sorted(
            (v, bool(m.get("mute")), bool(m.get("solo")))
            for v, m in a.drum_mix.items()
            if m.get("mute") or m.get("solo")
        ),
        a.drum_samples,
        s.beat_style,
        s.beat_seed,
        s.beat_var,
        sorted(s.beat_vars),
        # a row head's knobs: its level, and the kit's swing its own rests on
        sorted((v, m.get("level")) for v, m in a.drum_mix.items() if "level" in m),
        a.drum_swing,
        # the clip select per row, and the beat library listing
        _clips_sig(svc),
        dir_sig(s.data_dir / "presets" / "drums", ".json"),
        # an empty grid names the song's sections to open here
        song_sections(s.song) if not a.drums else (),
    )


def _w_perform_drums(svc: StudioService, view: str) -> str:
    """Perform's drum rack: the grid (cells, velocities, clock, the rows'
    mute/solo and sample marks, the clips its menus offer) and the tool
    bar's ×2 / randomise labels."""
    s, a = svc.state, svc.state.arrangement
    return _sig(
        a.drums,
        a.drum_vel,
        a.drum_hits,
        a.drum_row_swing,
        a.drum_bars,
        a.drum_steps,
        a.meter,
        sorted(
            (v, bool(m.get("mute")), bool(m.get("solo")))
            for v, m in a.drum_mix.items()
            if m.get("mute") or m.get("solo")
        ),
        a.drum_samples,
        s.beat_style,
        s.beat_var,
        sorted(s.beat_vars),
        # a row head's knobs: its level, and the kit's swing its own rests on
        sorted((v, m.get("level")) for v, m in a.drum_mix.items() if "level" in m),
        a.drum_swing,
        _w_clips(svc, view),
    )


def _w_transport(svc: StudioService, view: str) -> str:
    a = svc.state.arrangement
    g = svc.grid_context()  # the roll: its window, the lanes, the ghosts
    return _sig(
        svc.state.grid_span,
        svc.state.grid_oct,
        # the lane it edits (its heads are the lane inspector's)
        svc.state.active_layer,
        svc.state.patch.name,
        sorted(g["roll"]["marks"].items()),
        g["roll"]["ghost_names"],  # the ghosts toggle's title names them
        svc.lane_follow(),
        a.bars,
        a.steps_per_bar,
        a.meter,
        a.root,
        a.scale_name,
        a.swing,
        a.arp_mode,
        a.arp_octaves,
        a.strum,
        a.cells,
        a.cuts,
        a.slides,
        a.velocities,
        a.note_chance,  # the chance lane under the velocities
        a.chords,  # the chord lane over the roll (its tones are in the marks)
        svc.loop_editing(),  # the saved loop the grid edits: its banner
    )


def _w_launcher(svc: StudioService, view: str) -> str:
    # exactly what it draws: the heading's source, each beat's id and name,
    # the playing mark — so a bank rename shows while the pool is empty,
    # and never once the pool holds a beat
    b = svc.pool_beats()
    return _sig(
        b["beat_source"],
        [(p["id"], p["name"]) for p in b["pool_beats"]],
        b["beat_playing"],
    )


def _w_slots(svc: StudioService, view: str) -> str:
    # Perform's launcher, exactly as drawn: its lanes (sound, stopped, the
    # row each plays), its scenes' slots (name, title, length, gone,
    # playing) — so a stroke on a playing slot's lane, which writes its
    # loop's file, moves it only when the loop's length or title does
    return _sig(svc.slots_context())


def _ctx_slots(svc: StudioService, request: Request, view: str) -> dict:
    return {"slots": svc.slots_context()}


def _w_lane(svc: StudioService, view: str) -> str:
    # the Loops page's lane inspector, exactly as drawn: the lane heads,
    # the selected lane's facts, mix and FX, the slots playing on it and
    # on the drums (`lane_context` itself: four slots, cheap)
    return _sig(svc.lane_context())


def _ctx_lane(svc: StudioService, request: Request, view: str) -> dict:
    return {"insp": svc.lane_context()}


def _song_bars(song: dict) -> int:
    return sum(sec["bars"] * sec["repeats"] for sec in song["sections"])


def _w_song_arranger(svc: StudioService, view: str) -> str:
    song = svc.state.song
    # a cell draws its pattern's name, colour and bars (how often its
    # thumbnail repeats across the section); the pool's others don't show.
    # What the thumbnail draws is the `song-thumbs` region's, not this one's
    pats = song["patterns"]
    used = {pid for sec in song["sections"] for pid in sec["cells"].values()}
    shown = sorted(
        (pid, pats[pid]["name"], pats[pid]["color"], pats[pid]["bars"])
        for pid in used & set(pats)
    )
    # whole stems on the timeline (long audio): the items, the tempo they
    # are compared with, and whether their audio is on disk — nothing
    # added for a song without one, so its watch is the one it had
    held = song.get("long")
    more = (held, longaudio.sig(svc.state.data_dir, song)) if held else ()
    # the playlist: the ruler's cap mark and a take's width follow the
    # tempo; a take is as wide as its clip is long ("Update clip" can
    # change that), so the files of the clips the song holds — and only
    # those — are in the watch
    takes = sorted({a["clip"] for sec in song["sections"] for a in sec["audio"]})
    listed = _take_files(svc.state.data_dir, takes) if takes else ()
    return _sig(
        song["sections"],
        song["tracks"],
        shown,
        bool(pats),
        song["bpm"],
        *more,
        listed,
        *_songcraft(svc, song, sheet=not song["sections"]),
    )


def _songcraft(svc: StudioService, song: dict, sheet: bool) -> tuple:
    """Song creation's share of a Song watch: the key and the chord track
    (the chord strip, each section head's chords), how the song was
    started (which card leads, whether it can be rolled again), and —
    where the starter sheet is drawn (`sheet`) — what "From my loop"
    would arrange. Nothing is added for a song that has none of it but
    the key, so an old song's watch moves exactly when it did."""
    out: tuple = (song["root"], song["scale_name"])
    for key in ("chords", "gen"):
        if song.get(key):
            out += (key, song[key])
    if sheet:
        s = svc.state
        a = s.arrangement
        lanes = sum(bool(x.get("patch") and x.get("cells")) for x in s.layers[1:])
        out += (math.lcm(max(1, a.bars), max(1, a.drum_bars)), lanes, bool(a.drums))
    return out


_CHECKED: dict = {}


def _checked(song: dict) -> str:
    """The checklist's findings as a signature, worked out once a song
    object (a commit makes a new one): the topbar's badge and popover
    follow the whole song, and the watch runs twice a request."""
    if _CHECKED.get("song") is not song:
        from ..gen import checklist

        ctx = checklist.context(song)
        _CHECKED.update(song=song, sig=_sig(ctx["found"], ctx["signature"]))
    return _CHECKED["sig"]


def _take_files(data_dir, names: list[str]) -> tuple:
    """(name, mtime, size) of each take's clip file; None for a missing one."""
    out = []
    for name in names:
        try:
            st = (clips.clip_dir(data_dir) / f"{clips.clip_safe(name)}.wav").stat()
            out.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((name, None, None))
    return tuple(out)


def _w_song_thumbs(svc: StudioService, view: str) -> str:
    return _sig([(pid, _thumb(p)) for pid, p in svc.state.song["patterns"].items()])


def _w_song_pool(svc: StudioService, view: str) -> str:
    song = svc.state.song
    return _sig(
        [
            (
                pid,
                p["kind"],
                p["name"],
                p["bars"],
                p["color"],
                _thumb(p),
                len(sm.pattern_usage(song, pid)),
            )
            for pid, p in song["patterns"].items()
        ]
    )


def _w_song_topbar(svc: StudioService, view: str) -> str:
    s = svc.state
    song = s.song
    secs = song["sections"]
    return _sig(
        secs[0]["name"] if secs else None,
        _song_bars(song),
        round(sm.song_seconds(song), 3),
        song["bpm"],
        song["root"],
        song["scale_name"],
        bool(s.take_history),
        svc.calibration()["latency_ms"],
        # the undo / redo buttons: what each would move
        (undo := s.song_undo_state())["undo"],
        undo["redo"],
        # song creation: its name, the checklist, and (in the Start…
        # popover, once the song has sections) the starter sheet
        song.get("title"),
        _checked(song),
        *_songcraft(svc, song, sheet=bool(secs)),
    )


def _w_song_drawer(svc: StudioService, view: str) -> str:
    song = svc.state.song
    d = svc._song_drawer()
    if d is None:
        return _sig(None)
    p = d["pattern"]
    kind = p["kind"] if p else None
    if d["track"] is not None:
        kind = "drums" if d["track"]["kind"] == "drums" else "notes"
    choices = [
        (c["id"], c["name"], c["bars"], c["color"])
        for c in song["patterns"].values()
        if c["kind"] == kind
    ]
    # `d` carries the sections sharing the pattern by name (`shared_in`)
    return _sig(d, choices)


def _w_breed_parent(svc: StudioService, view: str) -> str:
    s = svc.state
    return _sig(len(set(s.breed_starred)), s.patch.name)


def _w_gen_strip(svc: StudioService, view: str) -> str:
    s = svc.state
    firsts = [gen[0].get("id") if gen else None for gen in s.breed_history]
    return _sig(firsts, s.breed_tiles[0].get("id") if s.breed_tiles else None)


def _ctx_reset(svc: StudioService, request: Request, view: str) -> dict:
    return svc.reset_context(view)


def _ctx_sound(svc: StudioService, request: Request, view: str) -> dict:
    return svc.sound_context()


def _ctx_layers(svc: StudioService, request: Request, view: str) -> dict:
    return svc.layer_context()


def _w_layer_strip(svc: StudioService, view: str) -> str:
    # everything the strip draws: layer_context is cheap (four slots)
    return _sig(svc.layer_context())


def _ctx_chords(svc: StudioService, request: Request, view: str) -> dict:
    return svc.chords_context()


def _ctx_viz(viz_id: str, draw: Callable[[StudioService], str]):
    def ctx(svc: StudioService, request: Request, view: str) -> dict:
        return {"viz_id": viz_id, "svg": draw(svc)}

    return ctx


def _ctx_breed(svc: StudioService, request: Request, view: str) -> dict:
    s = svc.state
    return {
        "starred": set(s.breed_starred),
        "patch": s.patch,
        "history": s.breed_history,
        "tiles": s.breed_tiles,  # the strip marks the generation on the board
    }


def _w_mixer(svc: StudioService, view: str) -> str:
    """The Mix view's strips: every track's mix (its level, mute, solo,
    pan, sends, role and its chain — the duck, the EQ and the rack are
    read off it), the returns, the sections its meters can read, and the
    undo buttons' labels. Never a level: the meters are the page's own
    (mixer.js sets them from the plan)."""
    s = svc.state
    song = s.song
    mix = [
        (
            t["id"],
            t["name"],
            t["kind"],
            t["mute"],
            t["solo"],
            t["gain"],
            t.get("role"),
            t.get("pan"),
            t.get("sends"),
            t["fx"],
        )
        for t in song["tracks"]
    ]
    undo = s.song_undo_state()
    return _sig(
        mix,
        song.get("returns"),
        [(sec["id"], sec["name"]) for sec in song["sections"]],
        undo["undo"],
        undo["redo"],
    )


def _ctx_mixer(svc: StudioService, request: Request, view: str) -> dict:
    return svc.mixer_context()


@dataclass(frozen=True)
class Region:
    template: str
    needs: frozenset[str]
    pages: frozenset[str]
    watch: Callable[[StudioService, str], Hashable]
    context: Callable[[StudioService, Request, str], dict] | None = None
    inside: str | None = None


_EDITOR = frozenset({"editor"})
# table order is send order: an outer region before the one drawn inside it
REGIONS: dict[str, Region] = {
    "reset-menu": Region(
        "fragments/reset_menu.html",
        frozenset({"resets"}),
        ALL_VIEWS,
        _w_reset,
        _ctx_reset,
    ),
    # Synths: the sound bar (with the sound's name/"edited" line inside it),
    # the chain bar and the three drawings summarise the patch, which the
    # panels post with hx-swap="none"
    "track-edit-banner": Region(
        "fragments/track_edit_banner.html",
        SONG_NEEDS,
        frozenset({"editor", "drums"}),  # Synths: a sound; the Drum Lab: the kit
        _w_track_edit,
    ),
    # the same edit on the Song page: Synths in a frame beside the playlist
    "song-dock": Region(
        "fragments/song_dock.html", SONG_NEEDS, frozenset({"song"}), _w_track_edit
    ),
    "sound-bar": Region("fragments/sound_bar.html", BAR_NEEDS, _EDITOR, _w_sound_bar),
    "sound-id": Region(
        "fragments/sound_id.html",
        frozenset(),
        _EDITOR,
        _w_sound_id,
        _ctx_sound,
        inside="sound-bar",
    ),
    # the layer strip: what each slot is and plays, and whether ▶ hears it
    "layer-strip": Region(
        "fragments/layer_strip.html",
        frozenset(),
        _EDITOR,
        _w_layer_strip,
        _ctx_layers,
    ),
    "signal-chain": Region(
        "fragments/chain.html", frozenset(), _EDITOR, _w_chain, _ctx_sound
    ),
    "filter-response-wrap": Region(
        "fragments/panel_viz.html",
        frozenset(),
        _EDITOR,
        _w_filter_viz,
        _ctx_viz(
            "filter-response-wrap", lambda svc: filter_response_svg(svc.state.patch)
        ),
    ),
    "env-amp-wrap": Region(
        "fragments/panel_viz.html",
        frozenset(),
        _EDITOR,
        _w_amp_viz,
        _ctx_viz("env-amp-wrap", lambda svc: svc.env_amp_svg(svc.state.patch)),
    ),
    "env-filter-wrap": Region(
        "fragments/panel_viz.html",
        frozenset(),
        _EDITOR,
        _w_filter_env_viz,
        _ctx_viz("env-filter-wrap", lambda svc: svc.env_filter_svg(svc.state.patch)),
    ),
    # the Source panel's sampler block: waveform, markers, chops (drawn
    # only while an oscillator is "sampler")
    "sampler-panel": Region(
        "fragments/sampler_panel.html",
        frozenset(),
        _EDITOR,
        _w_sampler,
        _ctx_sampler,
    ),
    # the keyboard dock's chord bar draws the key (label, note/chord/harmonic
    # pads, data-chords): a root or scale change re-sends it, morphed so
    # the voicing, strum and code line keep what the player set. The dock
    # is base.html's, outside #view: every page
    "chord-bar": Region(
        "fragments/chord_bar.html",
        frozenset({"chords"}),
        ALL_VIEWS,
        _w_chord_bar,
        _ctx_chords,
    ),
    # the clip library: a drawer in base.html, outside #view, on every page
    # (it replaced the clip shelf Synths and Perform drew)
    "clip-library": Region(
        "fragments/clip_library.html",
        CLIPLIB_NEEDS,
        ALL_VIEWS,
        _w_clip_library,
        _ctx_clip_library,
    ),
    # the status strip: in base.html's top dock, outside #view, on every
    # page; its tempo and key are the song's on Song and Mix, so a view
    # swap carries it (routes/views._view_response)
    "status-bar": Region(
        "fragments/status_bar.html",
        frozenset(),
        ALL_VIEWS,
        _w_status,
        _ctx_status,
    ),
    # Drums
    "drum-transport": Region(
        "fragments/drum_transport.html",
        frozenset(),
        frozenset({"drums"}),
        _w_drum_transport,
    ),
    "drum-bank": Region(
        "fragments/drum_bank.html", BANK_NEEDS, frozenset({"drums"}), _w_drum_bank
    ),
    # Perform
    "perform-drums": Region(
        "fragments/perform_drums.html",
        BANK_NEEDS,
        frozenset({"perform"}),
        _w_perform_drums,
    ),
    "transport": Region(
        "fragments/transport.html", BAR_NEEDS, frozenset({"perform"}), _w_transport
    ),
    # the loop's meter by Perform's bpm (the live strip is no region)
    "meter-pick": Region(
        "fragments/meter_pick.html",
        frozenset(),
        frozenset({"perform"}),
        _w_meter,
        _ctx_meter,
    ),
    "launcher": Region(
        "fragments/beat_launcher.html",
        frozenset(),
        frozenset({"perform"}),
        _w_launcher,
    ),
    # Perform's launcher: lanes x scenes of slots (the Loops panel lists
    # the sections)
    "slots": Region(
        "fragments/slots.html",
        frozenset(),
        frozenset({"perform"}),
        _w_slots,
        _ctx_slots,
    ),
    # the lane inspector: the lane heads and the selected lane;
    # the coach's dock beside it in the inspector is the page's
    "lane-inspector": Region(
        "fragments/lane_inspector.html",
        frozenset(),
        frozenset({"perform"}),
        _w_lane,
        _ctx_lane,
    ),
    # the Song page (routes/song.py, views/song.html): `song_context`
    "song-arranger": Region(
        "fragments/song_arranger.html",
        SONG_NEEDS,
        frozenset({"song"}),
        _w_song_arranger,
    ),
    "song-pool": Region(
        "fragments/song_pool.html", SONG_NEEDS, frozenset({"song"}), _w_song_pool
    ),
    "song-topbar": Region(
        "fragments/song_topbar.html", SONG_NEEDS, frozenset({"song"}), _w_song_topbar
    ),
    # every pool pattern's thumbnail as an SVG <symbol>: the arranger's
    # cells and the drawer's pick list <use> them, so a step edit re-sends
    # these few rects and never the arranger
    "song-thumbs": Region(
        "fragments/song_thumbs.html", SONG_NEEDS, frozenset({"song"}), _w_song_thumbs
    ),
    "song-drawer": Region(
        "fragments/song_drawer.html", SONG_NEEDS, frozenset({"song"}), _w_song_drawer
    ),
    # the Mix view (routes/mixer.py, views/mixer.html): `mixer_context`
    "mixer-strips": Region(
        "fragments/mixer_strips.html",
        frozenset(),
        frozenset({"mixer"}),
        _w_mixer,
        _ctx_mixer,
    ),
    # Breed: which sounds are parents, and the generation strip (the board
    # itself is what the breed routes swap)
    "breed-parent": Region(
        "fragments/breed_parent.html",
        frozenset(),
        frozenset({"breed"}),
        _w_breed_parent,
        _ctx_breed,
    ),
    "gen-strip": Region(
        "fragments/gen_strip.html",
        frozenset(),
        frozenset({"breed"}),
        _w_gen_strip,
        _ctx_breed,
    ),
    # the keys takes ⏺ Rec keeps (keytakes.py): the strip at the head of
    # the keyboard dock (base.html: every page), and Perform's Loops panel
    # (its list: the panel's frame, search and filters are the page's)
    "key-takes": Region(
        "fragments/key_takes.html",
        TAKES_NEEDS,
        ALL_VIEWS,
        _w_key_takes,
        _ctx_key_takes,
    ),
    "loops-panel": Region(
        "fragments/loops_panel.html",
        TAKES_NEEDS,
        PANEL_PAGES,
        _w_loops_panel,
        _ctx_loops_panel,
    ),
    # Perform's loop coach (loopcoach.py): its body; the dock, the message
    # box and the 🎙 are the page's
    "coach-panel": Region(
        "fragments/coach_panel.html",
        frozenset(),
        PANEL_PAGES,
        _w_coach,
        _ctx_coach,
    ),
    # the clip hub: its list (every clip, sample and Lab stem, where each
    # is used — a send from the bench moves it — and the hub's ↶ Undo)
    "hub-list": Region(
        "fragments/hub_list.html",
        frozenset(),
        frozenset({"clips"}),
        _w_hub,
        _ctx_hub,
    ),
}
# each page's regions, in send order
PAGE_REGIONS: dict[str, tuple[str, ...]] = {
    v: tuple(n for n, r in REGIONS.items() if v in r.pages) for v in VIEW_PARTS
}
