"""Perform's launcher: lanes x rows of slots, each a saved loop (studio
`docs/loop-and-perform.md`, "Slots and scenes").

The lanes are the working loop's own: the four layer slots, then the drums
(`DRUM_LANE`). A row is a scene. `perform_slots` (persisted, absent while
empty) is the grid of loop names; what PLAYS is the working loop itself —
**the slots compile to it**: launching one puts its loop on its lane (a
loop of notes: its notes and its sound into that layer slot; a beat: the
drum lane), and the loop renders as it always has. No render path and no
render key of an unchanged session moves.

A launch, of a slot or of a whole scene, is ONE pattern undo step holding
the whole loop (`_whole_loop`, as a section opened is), however many lanes
it moves. Each lane it launched is a loop open on that lane (`loop_edit`,
one a lane): the playing slot is the open one, and an edit there saves to
its loop. Placing, moving and clearing a slot is a step of its own that
puts the grid back and touches nothing that plays.

The lanes share one note grid (bars and steps a bar): a launch fits them
to the longest loop that plays — a shorter one repeats across it, a finer
grid takes every lane with it (a whole multiple: one meter's steps are).
A lane with nothing launched keeps its notes; past a shorter grid they
are hidden, never deleted.

A mixin: it reads and replaces the loop's live fields as `SongBridge`
does.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from ...synth import Patch
from .. import limits, schema
from .. import patternops as po
from .model import (
    DRUM_LANE,
    NOTE_LANE,
    SLOT_LANES,
    Arrangement,
    _patch_to_json,
    loop_window,
    regrid_lane,
)


def _row(slots=None, name: str = "") -> dict:
    return {"slots": list(slots or [None] * SLOT_LANES), "name": name}


class Slots:
    """The launcher's grid, and launching its slots and scenes."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    patch: Patch
    arrangement: Arrangement
    layers: list[dict]
    active_layer: int
    perform_slots: list[dict] | None
    perform_parts: list[str]
    active_slot: int | None
    curves: dict
    fx_drums: list

    # the launcher's lanes: the layer slots, then the drums (the last)
    SLOT_LANES = SLOT_LANES
    DRUM_LANE = DRUM_LANE

    # -- the grid -----------------------------------------------------------------
    def slot_rows(self) -> list[dict]:
        """The launcher's rows as stored (copies): `{slots, name}`."""
        return copy.deepcopy(self.perform_slots or [])

    def slot_at(self, lane: int, row: int) -> str | None:
        rows = self.perform_slots or []
        return rows[row]["slots"][lane] if 0 <= row < len(rows) else None

    def _set_rows(self, rows: list[dict], *, undo: bool = True) -> None:
        """The grid becomes `rows` (conformed: the trailing empty ones go),
        one pattern undo step that puts the old one back."""
        if undo:
            self.push_pattern_undo(
                self.pattern_snapshot() | {"slots": self.perform_slots}
            )
        while rows and not rows[-1]["name"] and not any(rows[-1]["slots"]):
            rows.pop()
        node = schema.nodes().PAYLOAD.fields["perform_slots"]
        self.perform_slots = schema.conform(rows, node) or None

    def _rows_to(self, row: int) -> list[dict]:
        rows = self.slot_rows()
        while len(rows) <= row:
            rows.append(_row())
        return rows

    def set_slot(
        self, lane: int, row: int, name: str | None, *, undo: bool = True
    ) -> None:
        """Slot (`lane`, `row`) holds the loop `name` (None: empty) — an
        undo step of its own unless `undo` is False (a take landing in it:
        inside its launch's step, `slotrec.land`)."""
        rows = self._rows_to(row)
        rows[row]["slots"][lane] = name
        self._set_rows(rows, undo=undo)

    def move_slot(self, frm: tuple[int, int], to: tuple[int, int], keep: bool) -> None:
        """A slot dragged onto another (of a lane of its kind): the loop
        goes there, and — `keep`, a Ctrl-drag — stays where it was too."""
        rows = self._rows_to(max(frm[1], to[1]))
        name = rows[frm[1]]["slots"][frm[0]]
        if not keep:
            rows[frm[1]]["slots"][frm[0]] = None
        rows[to[1]]["slots"][to[0]] = name
        self._set_rows(rows)

    def name_scene(self, row: int, name: str) -> None:
        rows = self._rows_to(row)
        rows[row]["name"] = name
        self._set_rows(rows)

    def free_scene_row(self) -> int | None:
        """The first row that holds nothing, or None when all of them do."""
        rows = self.perform_slots or []
        for i, r in enumerate(rows):
            if not r["name"] and not any(r["slots"]):
                return i
        return len(rows) if len(rows) < limits.MAX_SLOT_ROWS else None

    def slots_renamed(self, old: str, new: str | None) -> bool:
        """A loop renamed (`new`) or deleted (None) in the store: every
        slot holding it follows. Not an undo step (the store moved)."""
        rows = self.slot_rows()
        hit = False
        for r in rows:
            for i, name in enumerate(r["slots"]):
                if name == old:
                    r["slots"][i], hit = new, True
        if hit:
            self._set_rows(rows, undo=False)
        return hit

    def fill_scene(self, row: int, slots: list, name: str) -> None:
        """Row `row` holds `slots` under `name` (an idea or a section
        opened as a scene: inside its launch's undo step, so none of its
        own)."""
        rows = self._rows_to(row)
        rows[row] = _row(slots, name)
        self._set_rows(rows, undo=False)

    def slot_playing(self, lane: int) -> int | None:
        """The launcher's row whose slot is open (it plays) on `lane`."""
        e = self.loop_edit_of(lane)
        return e.get("row") if e is not None else None

    def open_slot_loop(self, name: str, lane: int, row: int) -> None:
        """`lane` plays the loop `name` from slot (`lane`, `row`) as it is
        now (its ＋ kept it from the lane): open on it, as a launch opens."""
        own = None if lane == DRUM_LANE else self.arrangement.bars
        self.open_loop_edit(name, lane, row=row, bars=own)

    def lane_sound_payload(self, lane: int) -> dict | None:
        """One lane's sound as a format-2 preset holds it — its patch, its
        FX as the main lane's, its curves, no other lanes: what a loop kept
        from that lane plays (`loops.save`). None for a slot with no sound."""
        if lane == self.active_layer:
            patch, curves = _patch_to_json(self.patch), self.curves
        else:
            patch, curves = (
                self.layers[lane].get("patch"),
                self.layers[lane].get("curves"),
            )
        if not patch:
            return None
        fx = self.layers[lane].get("fx") or []
        return copy.deepcopy(
            {
                "format": 2,
                "patch": patch,
                "layers": [],
                "main_fx": fx,
                "curves": curves or {},
            }
        )

    def adopt_tempo(self, key: dict, *, kit: dict | None = None, fx_drums=None) -> None:
        """An idea's or a section's tempo, key and meter (`key`: bpm,
        root, scale_name, meter — another meter fits every lane to it first,
        `set_meter`) and a section's kit and drum-bus FX, on the loop."""
        if key.get("meter", self.arrangement.meter) != self.arrangement.meter:
            self.set_meter(key["meter"])
        tempo = {k: key[k] for k in ("bpm", "root", "scale_name") if k in key}
        self.arrangement = replace(self.arrangement, **tempo, **(kit or {}))
        if fx_drums is not None:
            self.fx_drums = schema.conform(fx_drums, schema.nodes().FX)

    def overdub_lane(self, lane: int, notes, bar: int) -> int:
        """A take's notes (`slotrec.fold`: begun on bar `bar` of the loop)
        merged into note lane `lane` — on its open loop's own bars, then
        repeated across the grid as a launch lays it, so the loop is still
        written back at its own length; what lies past the grid stays
        hidden. ONE pattern undo step holding the whole loop (the loop open
        on the lane saves it, `mirror_loop_edit`). The attacks it merged
        (0: nothing, and no step)."""
        from .. import slotrec  # lazy: slotrec reads this package

        a = self.arrangement
        e = self.loop_edit_of(lane)
        own = min(max(1, (e or {}).get("bars") or a.bars), a.bars)
        attacks, tails = slotrec.fold(notes, bar, a.meter, own, a.steps_per_bar)
        if not attacks:
            return 0
        step = self.whole_loop_step()
        now = self.lane_state(lane)
        total = a.bars * a.steps_per_bar
        mine = slotrec.overdub(loop_window(now, own), attacks, tails)
        merged = loop_window(mine, a.bars)
        for k in ("cells", "cuts"):  # past the grid: hidden, never deleted
            merged[k] += [c for c in now[k] if int(c[1]) >= total]
        merged["velocities"] |= {
            k: v for k, v in now["velocities"].items() if int(k) >= total
        }
        self._put_lane(lane, merged)
        self.push_pattern_undo(step)
        return len(attacks)

    def lane_stopped(self, lane: int) -> bool:
        """▶ does not hear the lane: a muted layer slot, the drums out."""
        if lane == DRUM_LANE:
            return "drums" not in self.perform_parts
        return bool(self.layers[lane].get("muted"))

    # -- launching ------------------------------------------------------------------
    def launch_refusal(self, picks: dict[int, dict | None]) -> str:
        """Why `picks` (lane -> a loaded loop, `loops.load`; None stops the
        lane) cannot launch, or "": a beat on a note lane or notes on the
        drums, another meter, a grid past its cap or a loop past the
        length cap."""
        a = self.arrangement
        for lane, lp in picks.items():
            if lp is None:
                continue
            # (named as the Loops lists name it: routes/slots.py sets `title`)
            said = lp.get("title", lp["name"])
            if (lane == DRUM_LANE) != (lp["kind"] == "beat") or lp["kind"] == "idea":
                kind = "a beat" if lp["kind"] == "beat" else "notes"
                return f"“{said}” is {kind}: it goes in a lane of its kind."
            if lp["meter"] != a.meter:
                return f"“{said}” is in {lp['meter']}: the loop is in {a.meter}."
        bars, spb = self._grid_for(picks)
        if bars * spb > limits.MAX_GRID_STEPS:
            cap = limits.MAX_GRID_STEPS
            return f"{bars} bars of {spb} steps is past the grid's {cap}."
        from ..render import loop_refusal  # lazy: render imports this package

        return loop_refusal(a, replace(a, bars=bars, steps_per_bar=spb))

    def _grid_for(self, picks: dict[int, dict | None]) -> tuple[int, int]:
        """The note grid a launch of `picks` leaves: the finest steps of
        what plays, and the longest bars — a launched loop's own, a lane
        that keeps playing its slot's, any other heard lane's the grid's."""
        a = self.arrangement
        notes = [lp["lane"] for ln, lp in picks.items() if lp and ln != DRUM_LANE]
        if not notes:  # a beat alone, or stops: the note grid stays as it is
            return a.bars, a.steps_per_bar
        spb = max([a.steps_per_bar] + [x["steps_per_bar"] for x in notes])
        bars = [x["bars"] for x in notes]
        for lane in range(DRUM_LANE):
            if (
                lane in picks
                or self.lane_stopped(lane)
                or not self._lane_has_notes(lane)
            ):
                continue
            e = self.loop_edit_of(lane)
            own = e.get("bars") if e is not None and e.get("row") is not None else None
            bars.append(own or a.bars)
        return max([1, *bars]), spb

    def _lane_has_notes(self, lane: int) -> bool:
        if lane == self.active_layer:
            return bool(self.arrangement.cells)
        return bool(self.layers[lane].get("cells"))

    def _put_lane(self, lane: int, fields: dict) -> None:
        """A note lane's `NOTE_LANE` content (on the grid's bars and steps)
        into slot `lane`: the arrangement's for the edited slot, its cells
        and `seq` for another. Its chance lane is the fields' own (a loop
        without one plays every note: none is left from what was there)."""
        chance = dict(fields.get("note_chance") or {})
        if lane == self.active_layer:
            own = {k: copy.deepcopy(fields.get(k) or {}) for k in ("note_nudge",)}
            own |= {k: copy.deepcopy(fields[k]) for k in NOTE_LANE[2:] if k not in own}
            self.arrangement = replace(self.arrangement, **own, note_chance=chance)
            return
        lay = dict(self.layers[lane])
        seq = dict(lay.get("seq") or {"arp_mode": "off", "arp_octaves": 1})
        seq |= {k: copy.deepcopy(fields[k]) for k in ("velocities", "slides", "cuts")}
        seq.pop("note_nudge", None)  # timing inside a step: absent when unset
        if fields.get("note_nudge"):
            seq["note_nudge"] = dict(fields["note_nudge"])
        seq.pop("note_chance", None)
        seq |= {"note_chance": chance} if chance else {}
        lay |= {"cells": [list(c) for c in fields["cells"]], "seq": seq}
        self.layers[lane] = lay

    def _fit_lanes(self, picks: dict[int, dict | None], bars: int, spb: int) -> None:
        """Every note lane onto the launch's grid: a finer one regrids them
        all (a whole multiple, nothing lost), a longer one repeats each
        lane that keeps playing (`patternops.repeat_into`: hidden notes
        there come back instead), a shorter one hides what lies past it."""
        a = self.arrangement
        finer, was, total = spb != a.steps_per_bar, a.bars * spb, bars * spb
        for lane in range(DRUM_LANE):
            grows = total > was and not self.lane_stopped(lane)
            if picks.get(lane) is not None or not (finer or grows):
                continue  # a launched lane is written whole (`launch`)
            f = self.lane_state(lane)
            if finer:
                f = regrid_lane(f, spb)
            if grows:
                f |= {
                    "cells": po.repeat_into(f["cells"], was, total),
                    "cuts": po.repeat_into(f["cuts"], was, total),
                    "velocities": po.repeat_lane(f["velocities"], was, total),
                    "slides": po.repeat_steps(f["slides"], was, total),
                    "note_nudge": po.repeat_lane(f.get("note_nudge") or {}, was, total),
                    "note_chance": po.repeat_lane(f["note_chance"], was, total),
                }
            self._put_lane(lane, f)
        self.arrangement = replace(self.arrangement, bars=bars, steps_per_bar=spb)

    def _put_sound(self, lane: int, sound, label: str) -> None:
        """A loop's sound — its patch, the main lane's FX and curves (its
        other lanes stay behind: a lane is one layer) — into slot `lane`:
        the edited slot's as a load ("this layer", one sound undo step);
        another's written into its slot. Nothing when it is that already."""
        lay = self.layers[lane]
        if sound is None:
            if not lay.get("patch") and lane != self.active_layer:
                # an empty slot plays the notes in the sound on Synths
                self.layers[lane] = {**lay, "patch": _patch_to_json(self.patch)}
            return
        patch, extras = sound
        extras = extras or {}
        fx = schema.conform(extras.get("main_fx") or [], schema.nodes().FX)
        now = (
            _patch_to_json(self.patch)
            if lane == self.active_layer
            else lay.get("patch")
        )
        if now == _patch_to_json(patch) and (lay.get("fx") or []) == fx:
            return
        curves = schema.conform(extras.get("curves") or {}, schema.nodes().CURVES)
        if lane == self.active_layer:
            before = self.patch_snapshot()
            chain = {"layers": None, "main_fx": fx, "curves": curves}
            self.load_sound_as(patch, chain, how="layer", label=label)
            self.push_undo_step(before)
            return
        self.layers[lane] = {
            **lay,
            "patch": _patch_to_json(patch),
            "fx": fx,
            "curves": curves,
        }

    def launch(
        self,
        picks: dict[int, dict | None],
        rows: dict[int, int | None],
        *,
        setup=None,
    ) -> str:
        """Launch `picks` (lane -> a loaded loop, or None to stop the lane)
        from the launcher's `rows` (lane -> the row it plays from; None: a
        loop put there from elsewhere) — `setup()` first, inside the same
        step (an idea's tempo and key, its row). One pattern undo step
        holding the whole loop; each launched lane is unmuted, its own (not
        following), and the loop open on it from now on. Why it could not
        (`launch_refusal`: nothing changed), or ""."""
        step = self.pattern_snapshot() | {"whole": self._whole_loop()}
        top = self.top_undo_step()
        if setup is not None:
            setup()
        refused = self.launch_refusal(picks)
        if refused:
            if setup is not None:
                self.restore_whole_loop(step["whole"])
            return refused
        bars, spb = self._grid_for(picks)
        self._fit_lanes(picks, bars, spb)
        opened = []
        for lane, lp in sorted(picks.items()):
            if lp is None:
                self.end_loop_edit(lane)
                if lane == DRUM_LANE:
                    parts = [p for p in self.perform_parts if p != "drums"]
                    self.perform_parts = parts or ["synth"]
                elif lane == self.active_layer or self.layers[lane].get("patch"):
                    # (a slot with no sound has nothing to stop)
                    self.layers[lane] = {**self.layers[lane], "muted": True}
                continue
            if lane == DRUM_LANE:
                self.apply_pattern(lp["beat"])
                self.active_slot = None
                heard = {*self.perform_parts, "drums"}
                self.perform_parts = [p for p in ("synth", "drums") if p in heard]
                opened.append((lane, lp, lp["beat"]["bars"]))
                continue
            self._put_sound(lane, lp["sound"], lp["name"])
            fields = loop_window(regrid_lane(lp["lane"], spb), bars)
            self._put_lane(lane, fields)
            lay = {k: v for k, v in self.layers[lane].items() if k != "follow"}
            self.layers[lane] = lay | {"muted": False, "silent": False}
            opened.append((lane, lp, lp["lane"]["bars"]))
        if self.top_undo_step() is not top:
            step["whole"]["sound_step"] = self.top_undo_step()
        self.push_pattern_undo(step)
        self.loop_edit_synced()  # the lanes it only fitted: no edit of theirs
        for lane, lp, own in opened:
            self.open_loop_edit(lp["name"], lane, row=rows.get(lane), bars=own)
        if picks.get(self.active_layer) is not None:
            self.show_notes()  # the roll's window on the notes launched
        return ""
