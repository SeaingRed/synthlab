"""The drum pattern bank and the drum pattern library on disk.

A mixin over `arrangement` (the drum lane), `drum_bank` / `active_slot`
(persisted) and `beat_style` (the last Randomise, session-only).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

from ..drumfactory import DRUM_FACTORY
from ..files import dir_sig, safe_name
from .model import BANK_SLOTS, DRUM_LANE, Arrangement, _clean_pattern, _read_json

# the beat library's patterns as last read, per data dir: (dir_sig, patterns)
_LIB_CACHE: dict[Path, tuple] = {}


class DrumBank:
    """Bank slots, pattern <-> arrangement, the pattern library files."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    data_dir: Path
    arrangement: Arrangement
    beat_style: str
    drum_bank: list
    active_slot: int | None
    beat_vars: dict[str, dict]
    beat_var: str
    beat_gen: tuple[str, dict] | None

    # -- the beat's variations A-D ---------------------------------------------
    # The grid holds ONE variation (the arrangement's drum lane, the only
    # render input); the others wait here by letter. Session-only: a
    # restart keeps the one on the grid, and "Save to slot" keeps any.
    VARIANTS = ("A", "B", "C", "D")
    # what a variation is: the drum lane's cells, lanes, shape, per-hit
    # data and row swings (the kit, the tempo and the kit's swing are the
    # beat family's, shared)
    _VAR_FIELDS = (
        "drums",
        "drum_vel",
        "drum_bars",
        "drum_steps",
        "drum_hits",
        "drum_row_swing",
    )

    def _var_lane(self) -> dict:
        a = self.arrangement
        return {k: copy.deepcopy(getattr(a, k)) for k in self._VAR_FIELDS}

    def beat_variation(self, letter: str, copy_from: str | None = None) -> bool:
        """Switch the grid to variation `letter` (A-D). The one on the grid
        is set aside under its own letter first. A letter that holds
        nothing yet starts as a copy of the grid ("copy A -> B" is one
        click on an empty B); `copy_from` overwrites `letter` with that
        variation's beat (the grid's own, when it is the one showing).
        False for an unknown letter."""
        if letter not in self.VARIANTS or copy_from not in (None, *self.VARIANTS):
            return False
        self.beat_vars[self.beat_var] = self._var_lane()
        if copy_from is not None:
            src = self.beat_vars.get(copy_from)
            if src is None:
                return False
            self.beat_vars[letter] = copy.deepcopy(src)
        lane = self.beat_vars.get(letter)
        if lane is not None:
            self.arrangement = replace(self.arrangement, **copy.deepcopy(lane))
        self.beat_var = letter
        self.beat_vars.pop(letter, None)  # the grid holds it now
        return True

    def variation_names(self) -> list[tuple[str, bool, bool]]:
        """(letter, is it the one on the grid, does it hold a beat)."""
        return [
            (v, v == self.beat_var, v == self.beat_var or v in self.beat_vars)
            for v in self.VARIANTS
        ]

    def drop_variations(self) -> None:
        """The loop was replaced whole (a section opened in Perform, a
        loaded session): the variations were the old loop's. A bank slot, a
        kit or a pool beat loads into the variation on the grid and leaves
        the others."""
        self.beat_vars, self.beat_var, self.beat_gen = {}, "A", None

    def variation_undone(self, letter: str | None) -> None:
        """A pattern undo is about to put back a grid that was variation
        `letter`'s: the one showing is set aside under its own letter."""
        if letter in self.VARIANTS and letter != self.beat_var:
            self.beat_vars[self.beat_var] = self._var_lane()
            self.beat_vars.pop(letter, None)
            self.beat_var = letter

    # -- a generator's roll: one undo step however often it is rolled -----------
    def gen_again(self, key: str, lane: dict | None = None) -> bool:
        """Is this roll of generator `key` on the grid the same generator
        left? Then it joins that roll's undo step (the route pushes none).
        `lane`: what the generator wrote on (default: the drum lane; a
        note generator passes the note grid's, `gen/loop.py`)."""
        last = self.beat_gen
        now = self._var_lane() if lane is None else lane
        return last is not None and last[0] == key and last[1] == now

    def gen_done(self, key: str, lane: dict | None = None) -> None:
        self.beat_gen = (key, self._var_lane() if lane is None else lane)

    # -- drum pattern bank ----------------------------------------------------
    def pattern_from_arrangement(self, name: str) -> dict:
        arr = self.arrangement
        return _clean_pattern(
            {
                "name": name,
                "steps": [list(d) for d in arr.drums],
                "bars": arr.drum_bars,
                "steps_per_bar": arr.drum_steps,
                "swing": arr.drum_swing,
                "hits": copy.deepcopy(arr.drum_hits),
                "row_swing": dict(arr.drum_row_swing),
            }
        )

    @staticmethod
    def pattern_fields(pattern: dict) -> dict:
        """A pattern as the drum lane's fields (drums + bars/steps/swing,
        and its per-hit data and row swings — none, for a pattern without
        any): what `apply_pattern` puts on the grid, and what a beat loop
        renders with (`routes/takes.py`)."""
        p = _clean_pattern(pattern)
        return {
            "drums": [list(d) for d in p["steps"]],
            "drum_bars": p["bars"],
            "drum_steps": p["steps_per_bar"],
            "drum_swing": p["swing"],
            "drum_hits": copy.deepcopy(p.get("hits") or {}),
            "drum_row_swing": dict(p.get("row_swing") or {}),
        }

    def apply_pattern(self, pattern: dict) -> None:
        """Put a pattern on the grid (`pattern_fields`)."""
        self.arrangement = replace(self.arrangement, **self.pattern_fields(pattern))
        self.mark_baseline("pattern", str(pattern.get("name") or "pattern"))

    def default_save_slot(self) -> int:
        """Where "Save to slot" points unless told otherwise: the first
        empty slot, and the active one only when none is empty — not the
        active slot first, so saving a randomised beat never silently
        replaces the factory pattern it started from."""
        empty = next((i for i, p in enumerate(self.drum_bank) if p is None), None)
        if empty is not None:
            return empty
        return self.active_slot if self.active_slot is not None else 0

    def default_pattern_name(self) -> str:
        """An unnamed save is called after its style ("techno", then
        "techno 2", ...) — never "slot N", which said nothing about the
        beat and read like an empty slot's label."""
        taken = {p["name"] for p in self.drum_bank if p}
        base = self.beat_style or "beat"
        n = 1
        name = base
        while name in taken:
            n += 1
            name = f"{base} {n}"
        return name

    def bank_save(self, slot: int, name: str) -> bool:
        if not 0 <= slot < BANK_SLOTS:
            return False
        self.drum_bank[slot] = self.pattern_from_arrangement(
            name or self.default_pattern_name()
        )
        self.active_slot = slot
        self.mark_baseline("pattern", self.drum_bank[slot]["name"])
        return True

    def bank_restore_factory(self) -> None:
        """Put the factory patterns back in their slots (so a save over
        "Techno" never loses it for good)."""
        factory = [_clean_pattern(dict(p)) for p in DRUM_FACTORY[:BANK_SLOTS]]
        self.drum_bank = factory + [None] * (BANK_SLOTS - len(factory))
        self.active_slot = None

    def bank_load(self, slot: int) -> bool:
        if not 0 <= slot < BANK_SLOTS or self.drum_bank[slot] is None:
            return False
        self.apply_pattern(self.drum_bank[slot])
        self.active_slot = slot
        self.end_loop_edit(DRUM_LANE)  # the bank's beat now, not a beat loop's
        return True

    def drum_lib_dir(self) -> Path:
        d = self.data_dir / "presets" / "drums"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_drum_pattern(self, name: str) -> Path:
        path = self.drum_lib_dir() / f"{safe_name(name)}.json"
        pattern = self.pattern_from_arrangement(safe_name(name))
        path.write_text(json.dumps(pattern, indent=2), encoding="utf-8")
        return path

    def list_drum_patterns(self) -> list[str]:
        return sorted(p.stem for p in self.drum_lib_dir().glob("*.json"))

    def read_drum_pattern(self, name: str) -> dict | None:
        """A beat of the library (`presets/drums`), conformed — or None.
        Nothing is applied."""
        path = self.drum_lib_dir() / f"{safe_name(name)}.json"
        try:
            loaded = _read_json(path)
        except (OSError, ValueError):
            return None
        if not isinstance(loaded, dict):
            return None
        return _clean_pattern({**loaded, "name": path.stem})

    def drum_lib_patterns(self) -> list[dict]:
        """Every beat of the library, by name — what the Loops panel and the
        Kit page's drawer list as beats (the Kit page has no library list
        of its own; nothing is copied or deleted).
        Read again only when the folder moved (`dir_sig`)."""
        sig = dir_sig(self.drum_lib_dir(), ".json")
        hit = _LIB_CACHE.get(self.data_dir)
        if hit is None or hit[0] != sig:
            read = (self.read_drum_pattern(n[:-5]) for n, *_ in sig)
            hit = (sig, [p for p in read if p is not None])
            _LIB_CACHE[self.data_dir] = hit
        return [copy.deepcopy(p) for p in hit[1]]

    def load_drum_pattern(self, name: str) -> dict | None:
        pattern = self.read_drum_pattern(name)
        if pattern is None:
            return None
        self.apply_pattern(pattern)
        self.active_slot = None
        return pattern

    def delete_drum_pattern(self, name: str) -> None:
        (self.drum_lib_dir() / f"{safe_name(name)}.json").unlink(missing_ok=True)
