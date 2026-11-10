"""The song's audio: idents, sections, the song-wide gain, plan, export.

Everything here takes the song (and the master chain) as arguments and
reads no session state but `state.data_dir` (where clips live), which is
what lets the `@render_outside_lock` routes run it with the studio lock
released: `song_snapshot` is the one method that reads the live song, and
it is called under the lock to take the copy the rest renders from. What
it writes are the self-locking caches, keyed from that same copy:
`song_memo` (a song's section idents and gain), `song_levels` (each pass's
levels at gain 1), `stem_levels` (each section's stems' levels, for the
mixer) and `stems` (each section's master-processed cycle); a
section's WAV goes through `LoopRender._ensure`, like any loop. Progress
steps (`events.Task`) sit here and nowhere below.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace

import numpy as np

from ...core import SR, scan
from .. import longaudio, song_bridge, song_play
from .. import meter as meter_
from .. import song_model as sm
from ..events import QUIET, EventBus, Task
from ..fxrack import clean_chain
from ..lru import LRU
from ..render import (
    RenderError,
    RenderResult,
    SectionAudio,
    SectionJob,
    StemCache,
    fold_tail,
    result_of,
    section_pool,
)
from ..state import StudioState


def section_key(*args, **kwargs):
    """`render.section_key`, read off the package on every call: it has
    always been patchable as `synthlab.studio.service.section_key`
    (tests/studio/test_song_export.py), and stays so."""
    from . import section_key as real

    return real(*args, **kwargs)


def section_master_audio(*args, **kwargs):
    """`render.section_master_audio`, read off the package on every call:
    patchable as `synthlab.studio.service.section_master_audio`
    (tests/studio/test_concurrency.py, test_song_export.py,
    test_song_routes.py), as it has always been."""
    from . import section_master_audio as real

    return real(*args, **kwargs)


class SongRender:
    """The song's sections, gain, plan, mixdown and stems."""

    # the service's fields this unit reads or writes (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    state: StudioState
    stems: StemCache
    song_levels: LRU[tuple[float, float]]
    song_memo: LRU[dict]
    stem_levels: LRU[dict]
    events: EventBus

    def song_snapshot(self) -> tuple[dict, list]:
        """A private copy of the song and the master chain, taken under the
        studio lock by a `@render_outside_lock` route, which then renders
        from it with the lock released: an edit landing mid-render changes
        `state.song`, never the copy being rendered."""
        s = self.state
        return copy.deepcopy(s.song), copy.deepcopy(list(s.fx_master))

    def pattern_heard(
        self, song: dict, master_fx, pattern_id: str, track_id: str
    ) -> RenderResult:
        """One loop of a pool pattern through a track's sound, alone, at the
        song's level: the pattern editor's ▶ Play on a pattern no section
        plays yet (or plays on another lane). A section of its own — the
        pattern's bars, every song-wide value the song's — appended to a
        copy of `song`, then `/song/cell/..wav`'s render of it
        (`song_play.solo_song`, no master chain): the track's own sound,
        FX and mixer strip. Nothing here is a second render path."""
        trial = copy.deepcopy(song)
        bars = trial["patterns"][pattern_id]["bars"]
        sid = sm.add_section(trial, "Audition", bars)
        sm.set_cell(trial, sid, track_id, pattern_id)
        index = len(trial["sections"]) - 1
        # the song's level; a song with no section yet has none, and the
        # audition is levelled as a song of its own would be
        gain = self.song_head_gain(song if song["sections"] else trial, master_fx)
        solo = song_play.solo_song(trial, track_id)
        return self.section_result(solo, index, loop=True, gain=gain)

    def track_heard(self) -> RenderResult | None:
        """What Synths' ▶ plays while it edits a synth track's sound: one
        loop of the block open in the Song page's drawer, or the first
        section that plays the track (`song_bridge.heard_section`), at the
        song's own level — the track's cell on its own
        (`state.song_edit_hear` "alone", the default: `/song/cell/..wav`'s
        render) or the section as the song plays it ("mix": every track,
        the mixer, the master chain; `/song/section/{id}.wav?loop=1`'s
        render). Nothing here is a second render path. None when no synth
        track is edited or no section plays it: ▶ then plays
        `render_inputs`' loop. It used to be the track alone at unity gain,
        some 18 dB over where the song has it, and it sounded like another
        sound. It renders a copy of the song holding the working sound
        (`working_track_sound`) — routing mirrors after the handler, and
        only for a request from Synths, so this never writes the track. Cached under
        the section's key plus ":meter" — Synths' readouts need the
        analysis a section's render leaves out."""
        s = self.state
        if s.track_edit_view() != "editor":
            return None
        tid = s.song_edit_track
        song, master = self.song_snapshot()
        i = sm.track_index(song, tid)
        if i is None:
            return None
        song["tracks"][i].update(s.working_track_sound())
        # a pool pattern open in the drawer that this lane does not play
        # anywhere: ▶ plays it through this lane's sound (`pattern_heard`)
        pid = (s.song_drawer or {}).get("pattern_id")
        if (
            pid in song["patterns"]
            and song["patterns"][pid]["kind"] == "notes"
            and not any(sec["cells"].get(tid) == pid for sec in song["sections"])
        ):
            out = self.pattern_heard(song, master, pid, tid)
            key = hashlib.sha1(f"{out.key}:meter".encode()).hexdigest()[:12]
            return self._ensure(SectionJob(key, lambda _: result_of(key, out.audio())))
        index = song_bridge.heard_section(song, tid, s.song_drawer)
        if index is None:
            return None
        gain = self.song_head_gain(song, master)
        if s.song_edit_hear == "alone":
            solo = song_play.solo_song(song, tid)
            out = self.section_result(solo, index, loop=True, gain=gain)
        else:
            out = self.section_result(
                song, index, loop=True, gain=gain, master_fx=master
            )
        key = hashlib.sha1(f"{out.key}:meter".encode()).hexdigest()[:12]
        return self._ensure(SectionJob(key, lambda _: result_of(key, out.audio())))

    def _song_content_key(self, song: dict, master_fx) -> str:
        """Everything a song's section renders depend on, cheaply: the song
        itself, the master chain, and each placed clip's content hash
        (`clip_hash`, memoized by file stat, so this reads no audio)."""
        from ..clips import clip_hash

        root = self.state.data_dir
        names = {a["clip"] for sec in song["sections"] for a in sec["audio"]}
        for t in song["tracks"]:
            names |= {str(v) for v in (t.get("drum_samples") or {}).values()}
        blob = json.dumps(
            {
                "song": song,
                "master": clean_chain(list(master_fx)),
                "clips": {n: clip_hash(root, n) for n in sorted(names)},
                # a song under long audio: which of it is on disk (the
                # song names the source; the store can lose it)
                **({"long": longaudio.sig(root, song)} if song.get("long") else {}),
            },
            sort_keys=True,
            default=str,
        ).encode()
        return hashlib.sha1(blob).hexdigest()

    def _song_idents(self, song: dict, master_fx) -> dict:
        """Every section's two passes as (ident, parts, previous parts):
        `passes[i] = (head, loop)`, and — only for a section the song's
        long audio crosses (`sm.pass_cuts`) — `each[i]`, one such triple
        per pass of its timeline, because there every pass plays another
        slice: pass 0 is `head`, pass j folds pass j-1's own tail, `loop`
        is pass 0's slice folded onto itself, and the next section folds
        the LAST pass. An ident is what the pass's audio
        depends on before its gain: the cycle's own `section_key`, what
        folds into its head ("start", "loop", or the previous section's
        `section_key`) and the master chain (only when there is one, so
        Task 3's keys are unchanged). Computed once per song content and
        memoized (`song_memo`) with the song's gain, so fetching a plan's
        WAVs re-keys nothing. Checks the song's length caps first: nothing
        renders for an over-long song."""
        song_play.check_length(song)
        ckey = self._song_content_key(song, master_fx)
        entry = self.song_memo.get(ckey)
        if entry is not None:
            return entry
        root = self.state.data_dir
        master = clean_chain(list(master_fx))
        extra = {"master": master} if master else {}
        cuts = sm.pass_cuts(song)  # {} for a song without long audio
        passes, each, prev, prev_key = [], {}, None, None
        for i, sec in enumerate(song["sections"]):
            parts = sm.section_arrangement(song, sec)
            own = section_key(parts, root)
            fold = prev_key if prev is not None else "start"
            # a section whose notes rest by chance rolls every pass afresh
            # (`sm.repass`): there, too, each pass is its own WAV
            count = len(sm.section_timeline(song, sec))
            rolls = count > 1 and sm.parts_chance(parts)
            if i not in cuts and not rolls:
                head = ({"own": own, "fold": fold} | extra, parts, prev)
                loop = ({"own": own, "fold": "loop"} | extra, parts, None)
                passes.append((head, loop))
                prev, prev_key = parts, own
                continue
            row: list[tuple] = []
            for j, cut in enumerate(cuts.get(i) or [None] * count):
                mine = sm.repass(parts, j) if rolls else parts
                mine = replace(mine, under=cut) if cut else mine
                key = section_key(mine, root) if mine is not parts else own
                if row and key == prev_key:  # two passes alike: a plain loop
                    row.append(({"own": key, "fold": "loop"} | extra, mine, None))
                else:
                    ident = {"own": key, "fold": fold} | ({"same": 1} if row else {})
                    row.append((ident | extra, mine, prev))
                prev, prev_key, fold = mine, key, key
            each[i] = row
            first = row[0]
            loop = ({"own": first[0]["own"], "fold": "loop"} | extra, first[1], None)
            passes.append((first, loop))
        entry = {"passes": passes, "each": each, "master": master, "gain": None}
        self.song_memo.put(ckey, entry)
        return entry

    def _master_audio(self, own_key: str, parts, master_fx) -> SectionAudio:
        """`section_master_audio` for a section whose `section_key` is
        `own_key`, cached in `svc.stems` (byte-bounded) by that key and the
        cleaned master chain. A song reads it for the section's first pass,
        its loop, the next section's head (its tail) and the gain, and the
        export and the stems read it again: the master chain used to run
        two or three times per section per request, and never cached. The
        arrays are shared, so they are made read-only (`fold_tail`
        copies)."""
        master = clean_chain(list(master_fx))
        blob = json.dumps({"own": own_key, "master": master}, sort_keys=True)
        key = "master-section:" + hashlib.sha1(blob.encode()).hexdigest()[:16]
        audio = self.stems.get(key)
        if audio is None:
            audio = section_master_audio(
                parts, master, stem_cache=self.stems, clip_root=self.state.data_dir
            )
            audio.base.flags.writeable = False
            audio.tail.flags.writeable = False
            if audio.out is not None:
                audio.out.flags.writeable = False
            self.stems.put(key, audio)
            self._keep_levels(own_key, parts, audio)
        return audio

    def _keep_levels(self, own_key: str, parts, audio: SectionAudio) -> None:
        """Remember what a section's render saw of each stem
        (`SectionAudio.levels`, by stem name) under the tracks and returns
        they belong to (`parts.tracks`; takes are one stem, so every audio
        track with a take here shows their level). By the section's key:
        the plan reads it back without a render."""
        found: dict = {}
        for name, level in getattr(audio, "levels", {}).items():
            if name.startswith("return:"):
                found[name.removeprefix("return:")] = level
            for tid in getattr(parts, "tracks", {}).get(name, ()):
                found[tid] = level
        if found:
            self.stem_levels.put(own_key, found)

    def song_stem_levels(self, song: dict, master_fx=()) -> dict:
        """{section id: {track or return id: (peak, RMS)}} at the song's
        gain, for every section whose render is remembered. Renders
        nothing: a section never rendered (or forgotten) is absent, and so
        is everything for a song that is too long to play."""
        try:
            entry = self._song_idents(song, master_fx)
        except RenderError:
            return {}
        gain = entry["gain"] or entry.get("head_gain") or 1.0
        out = {}
        rows = zip(song["sections"], entry["passes"], strict=True)
        for sec, (head, _loop) in rows:
            found = self.stem_levels.get(head[0]["own"])
            if found:
                out[sec["id"]] = {
                    k: (peak * gain, rms * gain) for k, (peak, rms) in found.items()
                }
        return out

    @staticmethod
    def _section_cache_key(ident: dict, gain: float) -> str:
        blob = json.dumps(ident | {"gain": float(gain)}).encode()
        return "section:" + hashlib.sha1(blob).hexdigest()[:16]

    def _section_mix(
        self, ident: dict, parts, prev, master_fx, task: Task = QUIET
    ) -> np.ndarray:
        """One pass at gain 1: the section's cycle through the master chain
        (`section_master_audio`), with a master-processed tail folded into
        its head: its own for a loop, the previous section's for a song's
        first pass, none at the song's start. With no master chain this is
        exactly `render_section`'s fold. Its levels are remembered
        (`song_levels`) for the song-wide gain."""
        own = self._master_audio(ident["own"], parts, master_fx)
        task.step()
        if ident["fold"] == "loop":
            tail = own.tail
        elif prev is not None:
            # a head pass's "fold" is the previous section's section_key
            # (its ring into ANOTHER section: `SectionAudio.handoff`); a
            # later pass under long audio folds the pass before it, which
            # is the section's own ("same": its ring into its next pass)
            before = self._master_audio(ident["fold"], prev, master_fx)
            tail = before.tail if ident.get("same") else before.handoff
            task.step()
        else:
            tail = np.zeros((0, 2))
        out = fold_tail(own.base, tail)
        lv = song_play.levels(out)  # one pass: NaN when any sample is
        if not math.isfinite(lv[0]):
            raise RenderError("Render produced NaN/inf — back an effect off.")
        self.song_levels.put(self._section_cache_key(ident, 1.0), lv)
        return out

    def _pass_levels(self, ident: dict, parts, prev, master_fx) -> tuple:
        """A pass's (peak, body) at gain 1: remembered, or measured by the
        `_section_mix` that renders it."""
        key = self._section_cache_key(ident, 1.0)
        lv = self.song_levels.get(key)
        if lv is None:
            mix = self._section_mix(ident, parts, prev, master_fx)
            lv = self.song_levels.get(key) or song_play.levels(mix)
        return lv

    def section_result(
        self,
        song: dict,
        index: int,
        *,
        loop: bool = False,
        gain: float = 1.0,
        master_fx=(),
        at: int | None = None,
        after: int | None = None,
    ) -> RenderResult:
        """One cycle of section `index` of `song`, as the player fetches it
        (`/song/section/{id}.wav`): rendered and cached through
        `_ensure` like any loop, under a `section:` key.

        - `loop` False: the section's first pass in the song, with the
          previous section's tail folded into its head (nothing for the
          first section). The key carries the previous section's
          `section_key`, so editing the verse changes the next chorus's key
          (its stems still come from the StemCache).
        - `loop` True: the section folded onto itself (every later pass,
          and looping the section on its own).
        - `at`: pass `at` of a section under long audio (`_song_idents`'
          `each`: there no two passes are one WAV). Ignored for any other
          section and with `loop`; past the section's last pass: refused.
        - `after`: the wrap of a range loop — the section's FIRST pass
          as it sounds straight after section `after` (an index): that
          section's last pass rings into its head, where the song's own
          first pass folds the section before it. Nothing renders that a
          plan did not (both cycles are in the stem cache); only the fold
          is new. The song's gain was found without this pass, so one that
          would peak over `song_play.CEILING` is scaled to it (a range
          loop's wrap never clips; it may sit a fraction of a dB low).
          Ignored with `loop`.
        - `master_fx`: the master chain, part of each section's render
          (`render.section_master_audio`); none by default.
        - `gain` (finite, > 0) scales the result before encoding; the song
          passes its one song-wide gain (`song_gain`). Part of the key.

        No normalization: that is song-wide. Refuses (RenderError) an
        over-long song, or a bad or bool index, before rendering anything."""
        if isinstance(gain, bool) or not isinstance(gain, int | float):
            raise RenderError("Section gain must be a number.")
        if not (math.isfinite(gain) and gain > 0):
            raise RenderError("Section gain must be finite and above 0.")
        if isinstance(index, bool) or not isinstance(index, int):
            raise RenderError("No such section.")
        if not 0 <= index < len(song["sections"]):
            raise RenderError("No such section.")
        entry = self._song_idents(song, master_fx)
        ident, parts, prev = entry["passes"][index][1 if loop else 0]
        row = entry["each"].get(index)
        if row is not None and at is not None and not loop:
            if isinstance(at, bool) or not isinstance(at, int):
                raise RenderError("No such pass.")
            if not 0 <= at < len(row):
                raise RenderError("No such pass.")
            ident, parts, prev = row[at]
        wrap = after is not None and not loop
        if wrap:
            if isinstance(after, bool) or not isinstance(after, int):
                raise RenderError("No such section.")
            if not 0 <= after < len(song["sections"]):
                raise RenderError("No such section.")
            # what rings in: the last pass of `after` (under long audio,
            # its own last slice), as any section's head folds its
            # neighbour's — `handoff`, its ring into ANOTHER section
            last = entry["each"].get(after, entry["passes"][after][:1])[-1]
            head = entry["passes"][index][0][0]
            ident = {k: v for k, v in head.items() if k != "same"}
            ident = ident | {"fold": last[0]["own"], "wrap": 1}
            parts, prev = entry["passes"][index][0][1], last[1]
        key = self._section_cache_key(ident, gain)

        def make(stems: StemCache | None) -> RenderResult:
            # a cache miss only: the cycle, and for a first pass the
            # previous section's (its tail)
            name = song["sections"][index]["name"]
            with self.events.task(f"Rendering {name}", 1 if prev is None else 2) as t:
                out = self._section_mix(ident, parts, prev, master_fx, t)
            if gain != 1.0:
                out = out * gain
            if wrap and (peak := scan(out).peak) > (song_play.CEILING):
                out = out * (song_play.CEILING / peak)
            return result_of(key, out, analyse=False)

        return self._ensure(SectionJob(key, make))

    def _warm(self, entry: dict, master_fx) -> None:
        """Render the sections the gain is about to ask for and the cache
        does not hold, side by side (`render.section_pool`): the loop in
        `song_gain` then finds each one cached. Sections are independent
        (each is its own stems through its own run of the master chain),
        so this is the serial loop's audio sample for sample; one section
        missing renders where it always did."""
        todo: dict[str, object] = {}
        for i, head_loop in enumerate(entry["passes"]):
            for ident, parts, prev in (*head_loop, *entry["each"].get(i, ())[1:]):
                if self.song_levels.get(self._section_cache_key(ident, 1.0)):
                    continue
                todo.setdefault(ident["own"], parts)
                if prev is not None and ident["fold"] != "loop":
                    todo.setdefault(ident["fold"], prev)
        if len(todo) < 2:
            return
        # a song whose stems the cache cannot hold gains nothing here: what
        # is warmed is evicted before the loop reads it, and renders twice
        need = sum(
            32 * parts.clock.samples(parts.cycle_bars) * (len(parts.tracks) + 2)
            for parts in todo.values()
        )
        if self.stems.max_bytes and need > self.stems.max_bytes:
            return
        jobs = [
            section_pool().submit(self._master_audio, own, parts, master_fx)
            for own, parts in todo.items()
        ]
        for job in jobs:  # in order: the first error is the serial loop's
            job.result()

    def song_gain(self, song: dict, master_fx=(), task: Task = QUIET) -> float:
        """The one gain every section of `song` plays at, and the export
        too: `song_play.gain_of` over every section's first pass and loop
        (the very arrays the WAVs and the export are made of). Memoized
        with the song's idents; a pass's levels are remembered by content,
        so only what changed renders again."""
        entry = self._song_idents(song, master_fx)
        if entry["gain"] is None:
            self._warm(entry, master_fx)
            found = []
            for i, head_loop in enumerate(entry["passes"]):
                # under long audio, every later pass too (`each`)
                more = entry["each"].get(i, ())[1:]
                for ident, parts, prev in (*head_loop, *more):
                    lv = self._pass_levels(ident, parts, prev, master_fx)
                    found.append(lv)
                    task.step()  # one per pass, rendered or remembered
            entry["gain"] = song_play.gain_of(found)
        else:
            task.step(2 * len(entry["passes"]))
        return entry["gain"]

    def song_head_gain(self, song: dict, master_fx=(), task: Task = QUIET) -> float:
        """The gain a song can START at before all of it is rendered: the
        song-wide gain when that is known, else `gain_of` over the first
        section and the fullest one (its `energy`, which a starter and an
        auto-arrange set) — the two a starter needs first. The song-wide
        gain is the minimum over every pass, so it is never higher than
        this; where the fullest section is the loudest it is this exactly
        and the whole plan takes over without a step in level, and where it
        is not the level drops at that swap (measured over the starters:
        0 dB in 12 of 20, up to 1 dB — docs/songcraft.md). A song with no
        `energy` has only its first section to go by."""
        entry = self._song_idents(song, master_fx)
        if entry["gain"] is not None:
            return entry["gain"]
        if entry.get("head_gain") is None:
            secs = song["sections"]
            full = max(
                range(len(secs)), key=lambda i: (secs[i].get("energy") or 0.0, -i)
            )
            found = []
            for i in dict.fromkeys((0, full)):
                for ident, parts, prev in entry["passes"][i]:
                    lv = self._pass_levels(ident, parts, prev, master_fx)
                    found.append(lv)
                    task.step()
            entry["head_gain"] = song_play.gain_of(found)
        return entry["head_gain"]

    # how many passes of its first section a head plan offers: the section
    # loops until the whole plan arrives (it never should need to)
    HEAD_PASSES = 64

    def song_plan(
        self, song: dict, master_fx=(), task: Task = QUIET, head: bool = False
    ) -> dict:
        """`/song/plan.json`: where every section plays and the URLs of its
        two WAVs (first pass, loop), at the song-wide gain. Each URL carries
        its render key, so an edit changes the URL of exactly the passes
        whose audio it changes. Refuses an over-long song before
        rendering.

        `head`: the plan a new song starts on while the rest renders — its
        FIRST section alone, at `song_head_gain`, looping (`HEAD_PASSES`)
        until the player swaps the whole plan in at a pass start. Its URLs
        say `head=1`, which is how the section WAV knows to play at that
        gain without waiting for every section."""
        entry = self._song_idents(song, master_fx)
        if head and entry["gain"] is None and song["sections"]:
            gain = self.song_head_gain(song, master_fx, task)
        else:
            gain, head = self.song_gain(song, master_fx, task), False
        mark = "&head=1" if head else ""
        bpm = song["bpm"]
        # what each section's render saw of its stems, at this gain (the
        # mixer's meters); a section's row is absent until it has rendered
        levels = self.song_stem_levels(song, master_fx)
        out, start, start_bar = [], 0, 0
        for i, sec in enumerate(song["sections"]):
            cycle = sm.cycle_bars(song, sec)
            clock = sm.section_clock(song, sec)
            n = clock.samples(cycle)
            passes = len(sm.section_timeline(song, sec))
            keys = [
                self._section_cache_key(ident, gain).removeprefix("section:")
                for ident, _parts, _prev in entry["passes"][i]
            ]
            url = f"/song/section/{sec['id']}.wav"
            # under long audio every pass is its own WAV (absent otherwise)
            own = [
                f"{url}?p={j}&k="
                + self._section_cache_key(ident, gain).removeprefix("section:")
                for j, (ident, _parts, _prev) in enumerate(entry["each"].get(i, ()))
            ]
            if own:
                own[0] = f"{url}?k={keys[0]}"
            out.append(
                {
                    "section_id": sec["id"],
                    "name": sec["name"],
                    "index": i,
                    "bars": sec["bars"],
                    "repeats": sec["repeats"],
                    "start_bar": start_bar,
                    "cycle_bars": cycle,
                    "cycles": sec["bars"] // cycle,
                    "passes": passes,
                    "cycle_samples": n,
                    "cycle_seconds": n / SR,
                    "start_sample": start,
                    "start_seconds": start / SR,
                    "url": f"{url}?k={keys[0]}{mark}",
                    "loop_url": f"{url}?loop=1&k={keys[1]}{mark}",
                }
                | ({"pass_urls": own} if own and not head else {})
                # its own bar and tempo (absent for a 4/4 section at the
                # song's tempo): what the player's bars, the click and the
                # playhead count in — a ramp's bar lines, one pass's
                | (
                    self._plan_clock(song, sec, clock, cycle)
                    if sm.timed(song, sec)
                    else {}
                )
                | (
                    {
                        "levels": {
                            k: [round(p, 5), round(r, 5)]
                            for k, (p, r) in levels[sec["id"]].items()
                        }
                    }
                    if sec["id"] in levels
                    else {}
                )
            )
            start += n * passes
            start_bar += sec["bars"] * sec["repeats"]
            if head:  # the first section alone, looping until the rest is in
                out[0]["passes"] = self.HEAD_PASSES
                break
        return {
            "sr": SR,
            "bpm": bpm,
            "gain": gain,
            "song_samples": start,
            "song_seconds": start / SR,
            "sections": out,
        } | ({"head": True} if head else {})

    @staticmethod
    def _plan_clock(song: dict, sec: dict, clock, cycle: int) -> dict:
        """A timed section's clock in the plan (`song_plan`): its tempo,
        meter, a bar in beats, the metronome's clicks in a bar (`meter.
        beats`, in beats: [at, level]) and, under a ramp, where each bar
        of a pass starts (samples; the last is the pass's end)."""
        m = sm.section_meter(song, sec)
        out = {
            "bpm": clock.bpm,
            "meter": m,
            "bar_beats": clock.bar_beats,
            "clicks": [[at / 4.0, lvl] for at, lvl in meter_.beats(m)],
        }
        if clock.ramp:
            out["ramp"] = float(sec["ramp"])
            out["bar_starts"] = [clock.bar_sample(b) for b in range(cycle)] + [
                clock.samples(cycle)
            ]
        return out

    def _song_raw(self, song: dict, master_fx=(), task: Task = QUIET) -> np.ndarray:
        """THE stitching: the song's passes at gain 1, in
        `section_timeline` order: each section's first pass, then its loop
        for every later pass. The same `_section_mix` arrays the section
        WAVs encode, so export == playback. Feeds the export (with the
        master chain) and the stems (without)."""
        entry = self._song_idents(song, master_fx)
        pieces: list[np.ndarray] = []
        rows = zip(song["sections"], entry["passes"], strict=True)
        for i, (sec, (head, loop)) in enumerate(rows):
            count = len(sm.section_timeline(song, sec))
            if i in entry["each"]:  # under long audio: every pass its own
                for one in entry["each"][i]:
                    pieces.append(self._section_mix(*one, master_fx))
            else:
                pieces.append(self._section_mix(*head, master_fx))
                if count > 1:
                    pieces += [self._section_mix(*loop, master_fx)] * (count - 1)
            task.step()  # one per section
        return np.concatenate(pieces) if pieces else np.zeros((0, 2))

    def song_mixdown(self, song: dict, master_fx=(), task: Task = QUIET) -> np.ndarray:
        """`/export/song.wav`: the stitched plan (`_song_raw`, master chain
        included per section) at the song-wide gain: the playback plan
        sample for sample. Refuses an over-long song before rendering."""
        gain = self.song_gain(song, master_fx, task)
        raw = self._song_raw(song, master_fx, task)
        return raw * gain if gain != 1.0 else raw

    def song_stems(
        self, song: dict, master_fx=(), task: Task = QUIET
    ) -> list[tuple[str, np.ndarray]]:
        """`/export/stems.zip`: one (entry name, audio) per sounding track
        (`song_play.stem_songs`), each the whole song with only that track
        playing, at the mix's song-wide gain and without master FX (a stem
        is for mixing elsewhere). Refuses an over-long song before
        rendering."""
        solos = song_play.stem_songs(song)
        task.plan(1 + len(solos))  # the mix's gain, then a step per stem
        gain = self.song_gain(song, master_fx)
        task.step()
        out = []
        for name, solo in solos:
            raw = self._song_raw(solo)
            out.append((name, raw * gain if gain != 1.0 else raw))
            task.step()
        return out
