"""The layer slots: which one is edited, which one is the main lane, who
follows it, and what sounds.

`select_layer` is where a slot's whole belongings change hands — patch,
cells, sequencing, curves and, runtime only, its undo ring, its "before last
load" stash and its sound / fx / sequence baselines (`_layer_undo`, by slot
index). Anything that puts a new lane in a slot drops that slot's entry.

A mixin: a slot's belongings ARE the session's live fields (`patch`,
`arrangement`, `curves`, `baselines`, `slot_prev`, `_history`) while it is
the edited one, so there is no data here that is the layers' alone.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from ...synth import Patch
from .. import schema
from .model import (
    LAYER_SCOPES,
    Arrangement,
    Lane,
    _empty_layer,
    _patch_to_json,
    is_follower,
    materialise,
    patch_from_json,
    render_layer,
    seq_changes,
    seq_of,
    sounds_in_loop,
)


class Layers:
    """Slots, the main lane, followers, the mix, the layer strip's actions."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    patch: Patch
    arrangement: Arrangement
    slot_prev: Patch | None
    _history: list[dict]
    _history_at: float
    _layer_undo: dict[int, dict]
    curves: dict
    baselines: dict
    layers: list[dict]
    stack_prev: dict | None
    active_layer: int
    main_layer: int | None
    fx_master: list
    fx_drums: list
    beat_vars: dict[str, dict]

    # -- layers --------------------------------------------------------------
    def stash_active_layer(self) -> None:
        """Copy the live patch/cells — and the slot's own sequencing
        (velocities, slides, arp) and curves — into the active layer's
        slot."""
        arr = self.arrangement
        self.layers[self.active_layer] = {
            **self.layers[self.active_layer],
            "patch": _patch_to_json(self.patch),
            "cells": [list(c) for c in arr.cells],
            "seq": seq_of(arr),
            "curves": copy.deepcopy(self.curves),
        }

    def select_layer(self, index: int) -> None:
        """Make slot `index` the live lane. Everything that belongs to a
        slot travels with it: patch, cells, velocities, slides, arp,
        curves — and, runtime only, its undo ring, its "before last load"
        stash (`slot_prev` / `stack_prev`) and its sound / fx / sequence
        baselines, so undo, swap-back and revert never reach another slot
        and switching alone never lights "changed". Selecting a slot and
        back is a no-op on the loop. (state.json keeps the edited slot's
        stash only, as it keeps no slot's undo ring.)"""
        # a layer is the loop's: switching one ends any track-sound edit
        # first, so the loop's own patch is the one stashed
        self.end_track_edit()
        if not 0 <= index < len(self.layers) or index == self.active_layer:
            return
        # the main lane stays where it is: pin it before the edited lane
        # moves, so followers keep following it and which slot is being
        # edited stays inaudible (it used to detach every follower here)
        if self.main_layer is None:
            self.main_layer = self.main_index()
        self.stash_active_layer()
        self._layer_undo[self.active_layer] = {
            "history": self._history,
            "baselines": {k: self.baselines.get(k) for k in LAYER_SCOPES},
            # "before last load" is the slot's too: swapped back on another
            # slot it put this one's old sound (and its stack's lanes and
            # FX) there
            "prev": (self.slot_prev, self.stack_prev),
        }
        self.active_layer = index
        lay = self.layers[index]
        loaded = patch_from_json(lay.get("patch"))
        if loaded is not None:
            self.patch = loaded
        # a never-used layer inherits the current patch (and its curves)
        # with an empty grid, so layering starts from the sound you were
        # just shaping; a slot never stashed keeps the live arp, and its
        # velocities/slides only if it has notes they could belong to
        changes: dict = {"cells": [list(c) for c in lay.get("cells", [])]}
        seq = lay.get("seq")
        if isinstance(seq, dict):
            changes |= seq_changes(seq)
        elif not changes["cells"]:
            changes |= {"velocities": {}, "slides": [], "cuts": []}
        self.arrangement = replace(self.arrangement, **changes)
        if isinstance(lay.get("curves"), dict):
            self.curves = copy.deepcopy(lay["curves"])
        saved = self._layer_undo.pop(index, None)
        self._history, self._history_at = (saved or {}).get("history") or [], -1e9
        self.slot_prev, self.stack_prev = (saved or {}).get("prev") or (None, None)
        if saved is None:
            self.mark_baselines(LAYER_SCOPES, f"Layer {index + 1}")
            return
        for scope, base in saved["baselines"].items():
            if base is None:
                self.baselines.pop(scope, None)
            else:
                self.baselines[scope] = base

    def fx_chain(self, scope: str) -> list | None:
        """The live list a session FX scope's chain is stored in — the
        edited layer's ("layer"), the master bus's, the drum bus's — or
        None for a scope there is none of. Edited in place (`chain[:] =`),
        so the slot that holds it keeps holding it."""
        if scope == "master":
            return self.fx_master
        if scope == "layer":
            return self.layers[self.active_layer].setdefault("fx", [])
        if scope == "drums":
            return self.fx_drums
        return None

    # -- the main lane and the mix ---------------------------------------------
    # The main lane is the slot following layers follow (`main_layer`; None
    # until one is pinned: the active slot). It never follows itself, and it
    # does not move when another slot is selected for editing: followers
    # keep playing its notes whichever slot the panels edit. A following
    # slot being edited keeps following — it plays the main lane's notes at
    # its octave; its own cells wait, untouched, for Follow off.
    def main_index(self) -> int:
        """The main lane's slot: the pinned one while it is a plain slot,
        else the active slot unless that follows, else the first plain slot
        with a sound — else the active one (whose Follow is then moot)."""
        m, act = self.main_layer, self.active_layer
        if m is not None and 0 <= m < len(self.layers):
            if not is_follower(self.layers[m]):
                return m
        if not is_follower(self.layers[act]):
            return act
        for i, lay in enumerate(self.layers):
            if lay.get("patch") and not is_follower(lay):
                return i
        return act

    def follows(self, index: int) -> bool:
        """Slot `index` plays the main lane's notes (it can't follow itself)."""
        return is_follower(self.layers[index]) and index != self.main_index()

    def main_arrangement(self) -> Arrangement:
        """The main lane's notes and sequencing on the loop's clock: what a
        follower follows in a loop render."""
        m = self.main_index()
        if m == self.active_layer:
            return self.arrangement
        lay = self.layers[m]
        changes: dict = {"cells": [list(c) for c in lay.get("cells", [])]}
        seq = lay.get("seq")
        if isinstance(seq, dict):
            changes |= seq_changes(seq)
        return replace(self.arrangement, **changes)

    def _lane_over(self, src: Arrangement, lane: Arrangement) -> Arrangement:
        """The edited lane's arrangement: `lane`, or — when the edited slot
        follows — `src`'s notes at its octave with `src`'s sequencing."""
        act = self.active_layer
        if not self.follows(act):
            return lane
        m = materialise({**self.layers[act], "cells": [], "seq": None}, src)
        return replace(lane, cells=m["cells"], **seq_changes(m["seq"]))

    def lane_arrangement(self) -> Arrangement:
        """What the edited lane plays in the loop (Perform, the song)."""
        return self._lane_over(self.main_arrangement(), self.arrangement)

    def _render_slot(
        self, ln: Lane, src: Arrangement, lane: Arrangement, bench: bool
    ) -> dict | None:
        """`ln`'s slot (not the edited one) as the renderer takes it, or None
        when it has nothing to play. `src` is the main lane's notes (the
        phrase on a bench view), `lane` what the edited lane renders. A
        follower's own cells and sequencing never reach the render."""
        m, lay = self.main_index(), self.layers[ln.slot]
        if ln.octave is not None and not is_follower(lay):
            # `lanes` gave it the phrase (the main lane on a bench view, or a
            # slot there with no notes of its own): it plays it as a follower
            lay = {**lay, "follow": {"octave": ln.octave}}
        if not sounds_in_loop(lay):
            return None
        if not is_follower(lay):
            # a plain slot plays its own notes, as it always did
            return render_layer(lay, self.arrangement if bench else lane)
        lay = {**lay, "cells": [], "seq": None}
        if m == self.active_layer:
            # following the lane being rendered: resolved at render time
            # (render._stem_jobs), exactly as a bred Sound's layers always were
            return render_layer(lay, self.arrangement)
        return render_layer(materialise(lay, src), lane)

    def lanes(
        self,
        *,
        bench: bool,
        focus: bool = False,
        keys: bool = False,
        solo: bool = True,
    ) -> list[Lane]:
        """The slots that sound, in slot order — the one predicate behind
        ▶ (`loop_mix`), the preset/direct-render layers (`other_layers`,
        `solo=False`) and the live keys (`keys=True`, `fxlive.live_stack`
        and `/note`), via `audible_layers`.

        Heard: not muted, and soloed whenever any slot is (mute wins;
        `solo=False` ignores solo). `focus` ("This layer"): only the edited
        slot, heard even when muted. Of the heard slots:
        - the edited slot always — at its follow octave when it follows;
        - a follower with a sound, at its octave;
        - the main lane (when not edited) with a sound — on a bench view
          the phrase stands in for its notes, and for the keys the key
          does, so it needs no cells of its own there;
        - the same for any slot with a sound and no notes of its own: on a
          bench view and for the keys it plays the phrase / the key at its
          own pitch (in a loop it has nothing to play and stays silent);
        - a plain slot with notes of its own — **never for the keys**: it
          plays its own pattern, not the keyboard's (a key is the main
          lane's note, played by hand)."""
        act, m = self.active_layer, self.main_index()
        by_key = bench or keys  # the main lane's notes are the phrase / key

        def octave(i: int) -> int:
            return int(self.layers[i]["follow"].get("octave", 0))

        edited = Lane(
            act, octave(act) if self.follows(act) else (0 if by_key else None), True
        )
        if focus:
            return [edited]
        out = []
        for i, lay in enumerate(self.layers):
            if lay.get("muted") or (solo and not self.audible(i)):
                continue
            if i == act:
                out.append(edited)
            elif self.follows(i) or (by_key and (i == m or not lay.get("cells"))):
                if lay.get("patch"):
                    out.append(Lane(i, octave(i) if self.follows(i) else 0, False))
            elif not keys and sounds_in_loop(lay):
                out.append(Lane(i, None, False))
        return out

    def _mix(
        self, src: Arrangement, lane: Arrangement, lanes: list[Lane], bench: bool
    ) -> list[dict]:
        """`lanes`' non-edited slots in render form."""
        out = []
        for ln in lanes:
            if ln.edited:
                continue
            r = self._render_slot(ln, src, lane, bench)
            if r is not None:
                out.append(r)
        return out

    def other_layers(self) -> list[dict]:
        """Inactive, unmuted layers with a sound and notes to play (their
        own, or the main lane's) in render form — the loop's other lanes as
        a preset or a direct render takes them. Solo is monitoring and is
        left to `loop_mix`."""
        src = self.main_arrangement()
        lane = self._lane_over(src, self.arrangement)
        return self._mix(src, lane, self.lanes(bench=False, solo=False), False)

    def audible(self, index: int) -> bool:
        """Slot `index` is heard in the mix: not muted, and soloed whenever
        any slot is (mute wins over solo)."""
        lay = self.layers[index]
        if lay.get("muted"):
            return False
        return bool(lay.get("solo")) or not any(x.get("solo") for x in self.layers)

    def loop_mix(
        self,
        src: Arrangement,
        *,
        bench: bool = False,
        lanes: list[Lane] | None = None,
        lane: Arrangement | None = None,
    ) -> tuple[Arrangement, list[dict], bool]:
        """(the edited lane's arrangement, the other lanes in render form,
        whether the edited lane is silent) over the main lane's notes `src`:
        `main_arrangement()` in a loop, the reference phrase on a bench view
        — where the edited lane plays the phrase (at its octave when it
        follows) and so does the main lane when another one is being
        edited. `lanes` (default `self.lanes(bench=bench)`; the view's
        `audible_layers` from `render_inputs`) decides who is heard; `lane`
        is what the edited lane holds in place of the session's own (a
        bounce's loop: `render_inputs(arrangement=)`)."""
        if lane is None:
            lane = src if bench else self.arrangement
        lane = self._lane_over(src, lane)
        if lanes is None:
            lanes = self.lanes(bench=bench)
        out = self._mix(src, lane, lanes, bench)
        return lane, out, not any(ln.edited for ln in lanes)

    # -- the layer strip's actions ----------------------------------------------
    def set_follow(self, index: int, octave: int | None) -> None:
        """Slot `index` follows the main lane at `octave` (None: it plays
        its own notes again — they were kept while it followed). The main
        lane is pinned first, so no toggle ever moves it. The caller keeps
        the main lane itself from following."""
        if self.main_layer is None:
            self.main_layer = self.main_index()
        lay = self.layers[index]
        if octave is None:
            lay.pop("follow", None)
        else:
            lay["follow"] = {"octave": int(octave)}

    def set_layer_solo(self, index: int, solo: bool) -> None:
        if solo:
            self.layers[index]["solo"] = True
        else:
            self.layers[index].pop("solo", None)

    def set_layer_gain(self, index: int, gain: float) -> None:
        node = schema.nodes().LAYER.fields["gain"]
        self.layers[index]["gain"] = float(schema.conform(gain, node))

    def free_slot(self) -> int | None:
        """The first slot holding no sound (never the edited one)."""
        for i, lay in enumerate(self.layers):
            if i != self.active_layer and not lay.get("patch"):
                return i
        return None

    def add_layer(
        self,
        patch: Patch,
        fx: list | None = None,
        *,
        select: bool = True,
        octave: int = 0,
        gain: float = 1.0,
        role: str | None = None,
        slot: int | None = None,
    ) -> int | None:
        """Put `patch` (and `fx`) in a free slot, following the main lane at
        `octave`, and — `select` — make it the slot being edited, so ▶
        plays it stacked on the rest at once (the sound agent adds without
        selecting: the panels stay on the sound it was asked about). None
        when every slot holds a sound. `slot`: that empty slot rather than
        the first (a Perform lane head's own ▾ on an empty lane)."""
        i = self.free_slot() if slot is None else slot
        if i is None:
            return None
        if self.main_layer is None:
            self.main_layer = self.main_index()
        nodes = schema.nodes().LAYER.fields
        lay = {
            **_empty_layer(),
            "patch": _patch_to_json(patch),
            # the caller's chain as the rack keeps one (every unit with all
            # its parameters): FX units are hashed into the loop's key as
            # they stand, so a partial unit here keyed the loop differently
            # before a restart than after it. A clean chain is unchanged.
            "fx": schema.conform(list(fx or []), nodes["fx"]),
            "follow": {"octave": int(octave)},
        }
        node = nodes["gain"]
        lay["gain"] = float(schema.conform(gain, node))
        if role is not None:
            lay["role"] = role
        self.layers[i] = lay
        self._layer_undo.pop(i, None)
        if select:
            self.select_layer(i)
        return i

    def clear_layer(self, index: int) -> bool:
        """Empty slot `index`. The edited one hands the panels to the
        nearest slot with a sound first (the one above it on a tie), so it
        is refused only when it is the one sound there is: the panels
        always show a sound.
        Clearing the main lane hands its notes to a follower, which becomes
        the main lane (`_inherit_main`); with no follower it is un-pinned
        (`main_index` picks again)."""
        if index == self.active_layer:
            others = [
                i
                for i, lay in enumerate(self.layers)
                if i != index and lay.get("patch")
            ]
            if not others:
                return False
            self.select_layer(min(others, key=lambda i: (abs(i - index), i)))
        heir = self._inherit_main() if index == self.main_index() else None
        self.layers[index] = _empty_layer()
        self._layer_undo.pop(index, None)
        if heir is not None or self.main_layer == index:
            self.main_layer = heir
        return True

    def _inherit_main(self) -> int | None:
        """The main lane is about to go: the edited slot when it follows,
        else the first follower with a sound, takes the notes it was playing
        (the main lane's, at its octave, with their sequencing) as its own
        and stops following; the other followers' octaves are re-based on
        it. The stack sounds as it did less the lane removed — the notes
        used to leave with the main lane, and what was left looped silence.
        Returns the heir's slot, None when nothing follows."""
        act = self.active_layer
        followers = [i for i, lay in enumerate(self.layers) if self.follows(i)]
        heirs = [i for i in followers if i == act or self.layers[i].get("patch")]
        if not heirs:
            return None
        from ..arrange import FOLLOW_OCTAVES  # lazy: arrange imports this module

        lo, hi = FOLLOW_OCTAVES[0], FOLLOW_OCTAVES[-1]
        heir = act if act in heirs else heirs[0]
        base = int(self.layers[heir]["follow"].get("octave", 0))
        if heir == act:
            self.arrangement = self.lane_arrangement()
            self.layers[act].pop("follow", None)
        else:
            self.layers[heir] = materialise(self.layers[heir], self.main_arrangement())
        for i in followers:
            if i != heir:
                octave = int(self.layers[i]["follow"].get("octave", 0)) - base
                self.layers[i]["follow"]["octave"] = min(hi, max(lo, octave))
        return heir

    def set_layer_muted(self, index: int, muted: bool) -> None:
        """The layer strip's own mute: it owns the slot's mute from now on,
        so a mute the song's mix put there (`silent`) is cleared."""
        self.layers[index]["muted"] = bool(muted)
        self.layers[index]["silent"] = False

    def _make_active_main(self) -> None:
        """A load that replaces the other lanes: the edited slot is the main
        lane again — no longer following, not soloed."""
        active = self.layers[self.active_layer]
        active.pop("follow", None)
        active.pop("solo", None)
        self.main_layer = None

    def lanes_with_notes(self) -> int:
        """Other slots holding notes of their own (a follower holds none):
        what a load bringing its own lanes would replace ("replaces 2
        lanes", the breed card's confirm)."""
        return sum(
            1
            for i, lay in enumerate(self.layers)
            if i != self.active_layer
            and lay.get("patch")
            and lay.get("cells")
            and not is_follower(lay)
        )

    # -- the bar --------------------------------------------------------------

    def fit_other_lanes(self, frm: str, to: str) -> None:
        """Every lane a pattern undo step does not hold — the other layer
        slots' cells and sequencing, the beat's variations set aside — from
        meter `frm` into `to` (`meterops`), on the loop's note grid."""
        from .. import meterops

        spb = self.arrangement.steps_per_bar
        self.layers = [
            meterops.fit_layer(lay, spb, frm, to) if i != self.active_layer else lay
            for i, lay in enumerate(self.layers)
        ]
        self.beat_vars = {
            k: meterops.fit_drum_lane(v, frm, to) for k, v in self.beat_vars.items()
        }

    def set_meter(self, to: str) -> int:
        """The working loop in meter `to` (`meter.METERS`): its note grid,
        its beat, every layer and every variation of the beat fitted bar by
        bar (`meterops`) — each bar keeps its first beats, the step length
        kept. Returns how many of the grids' notes and hits fell past the
        new bar's end (4/4 -> 3/4: each bar's fourth beat)."""
        from .. import meter as _meter
        from .. import meterops

        a = self.arrangement
        frm = _meter.clean(a.meter)
        to = _meter.clean(to)
        if frm == to:
            return 0
        spb = _meter.refit(a.steps_per_bar, frm, to)[0]
        dspb = _meter.refit(a.drum_steps, frm, to)[0]
        lost = sum(
            _meter.refit_step(int(st), a.steps_per_bar, frm, spb, to) is None
            for _p, st in a.cells
        ) + sum(
            _meter.refit_step(int(st), a.drum_steps, frm, dspb, to) is None
            for _v, st in a.drums
        )
        self.fit_other_lanes(frm, to)
        self.arrangement = meterops.fit_arrangement(a, to)
        return lost
