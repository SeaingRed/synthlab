"""Reset / revert: the scopes (`RESET_SCOPES`), their baselines, "changed".

Owns `baselines` as a whole (every unit marks one after a load or a save
through `mark_baseline`) and the `is_dirty` memo (`_dirty_memo`, used
nowhere else in the package).

A mixin: a scope's keys name the session's own fields (`patch`, `curves`,
`arr.<field>`, `state.<attr>`), read and assigned by name.
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import replace

from ...synth import Patch
from .. import schema
from .model import RESET_SCOPES, Arrangement, _patch_to_json

_ARR_OPTIONAL = frozenset(f"arr.{k}" for k in schema.ARR_OPTIONAL)
# every scope key that is absent from a snapshot while unset: those, and
# Perform's launcher (the session's `perform_slots`, None while empty)
_OPTIONAL = _ARR_OPTIONAL | {"state.perform_slots"}


def _unset(key: str, value) -> bool:
    if key in _ARR_OPTIONAL:
        return schema.arr_unset(key[4:], value)
    return not value


class Scopes:
    """Scope snapshots, baselines, is_dirty, revert and factory defaults."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    patch: Patch
    arrangement: Arrangement
    take_history: list[dict]
    board_epoch: int
    _dirty_memo: dict[str, bool] | None
    curves: dict
    baselines: dict
    layers: list[dict]
    active_layer: int

    # -- reset / revert -------------------------------------------------------
    # Two moves per scope: revert to the snapshot taken when it was last
    # loaded or saved ("put it back how it was"), or reset to the factory
    # defaults. Baselines persist, so revert survives a restart.

    def _scope_get(self, key: str):
        if key == "patch":
            return _patch_to_json(self.patch)
        if key == "curves":
            return copy.deepcopy(self.curves)
        if key == "layer_fx":
            return copy.deepcopy(self.layers[self.active_layer].get("fx") or [])
        kind, name = key.split(".", 1)
        src = self.arrangement if kind == "arr" else self
        return copy.deepcopy(getattr(src, name))

    def _scope_set(self, key: str, value) -> None:
        """Apply a scope value, whatever its source — a live edit, a
        baseline captured earlier this session, or one restored from an
        imported project's `baselines`. A baseline is just stored JSON
        until this runs it, so validation belongs here, not at import
        time: it must not matter how a hostile value got in."""
        value = schema.conform(value, schema.nodes().SCOPES[key])  # fresh copy
        if key == "patch":
            self.patch = Patch.from_dict(value)
        elif key == "curves":
            self.curves = value
        elif key == "layer_fx":
            self.layers[self.active_layer]["fx"] = value
        else:
            kind, name = key.split(".", 1)
            if kind == "arr":
                self.arrangement = replace(self.arrangement, **{name: value})
            else:
                setattr(self, name, value)
            if name == "song":
                # a take's undo restores ITS pattern by id: in a reverted or
                # reset song that id is another pattern, or none
                self.take_history = []
            if key in RESET_SCOPES["board"][1]:
                # the board was put back or emptied: a litter still breeding
                # from the one it replaced is stale (routes/breed.py)
                self.board_epoch += 1

    @staticmethod
    def _scope_default(key: str):
        return schema.nodes().SCOPES[key].default()

    def snapshot(self, scope: str) -> dict:
        """Everything `scope` owns, right now. An optional arrangement
        field that is unset is left out (`_OPTIONAL`: Perform's launcher
        too): a baseline from before it existed, and the session file that
        holds one, read the same."""
        out = {k: self._scope_get(k) for k in RESET_SCOPES[scope][1]}
        return {k: v for k, v in out.items() if k not in _OPTIONAL or not _unset(k, v)}

    def mark_baseline(self, scope: str, label: str = "") -> None:
        """Remember the current values as this scope's 'how it was'."""
        if scope in RESET_SCOPES:
            self.baselines[scope] = {"label": label, "data": self.snapshot(scope)}

    def mark_baselines(self, scopes, label: str = "") -> None:
        for scope in scopes:
            self.mark_baseline(scope, label)

    def _baseline_data(self, scope: str) -> dict | None:
        """This scope's stored snapshot, or None. Baselines are conformed on
        load (schema.BASELINES); the type checks here only guard code that
        writes self.baselines directly."""
        base = self.baselines.get(scope)
        data = base.get("data") if isinstance(base, dict) else None
        return data if isinstance(data, dict) else None

    def baseline_label(self, scope: str) -> str:
        base = self.baselines.get(scope)
        return str(base.get("label") or "") if isinstance(base, dict) else ""

    def is_dirty(self, scope: str) -> bool:
        """Has this scope moved since its baseline? (False when there is no
        baseline: nothing to compare against.)"""
        memo = self._dirty_memo
        if memo is not None and scope in memo:
            return memo[scope]
        data = self._baseline_data(scope)
        dirty = bool(data) and json.dumps(
            data, sort_keys=True, default=str
        ) != json.dumps(self.snapshot(scope), sort_keys=True, default=str)
        if memo is not None:
            memo[scope] = dirty
        return dirty

    @contextmanager
    def dirty_memo(self):
        """Answer each `is_dirty` once inside the block — for readers that
        change nothing (the route wrapper's region watches ask for "sound"
        twice, via the reset menu and the "edited" line)."""
        outer = self._dirty_memo
        if outer is None:
            self._dirty_memo = {}
        try:
            yield
        finally:
            self._dirty_memo = outer

    def revert(self, scope: str) -> bool:
        """Put a scope back to its baseline. False when there isn't one."""
        if scope not in RESET_SCOPES or not self.baselines.get(scope):
            return False
        data = self._baseline_data(scope) or {}
        for key, value in data.items():
            if key in RESET_SCOPES[scope][1]:
                self._scope_set(key, value)
        for key in RESET_SCOPES[scope][1]:
            if key in _OPTIONAL and key not in data:
                # unset then: unset again ({} for the drum tables, 0 for
                # the strum, no launcher rows: the field's own default)
                self._scope_set(key, self._scope_default(key))
        return True

    def reset_defaults(self, scope: str) -> bool:
        """Factory values for a scope. The baseline moves with it, so an
        immediate revert doesn't undo the reset behind your back."""
        if scope not in RESET_SCOPES:
            return False
        for key in RESET_SCOPES[scope][1]:
            self._scope_set(key, self._scope_default(key))
        self.mark_baseline(scope, "defaults")
        return True
