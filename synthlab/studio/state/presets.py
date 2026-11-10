"""Presets on disk, and putting a sound live.

The files: sound presets with their meta, panel presets. The loads:
`load_sound` (the one load every browse surface shares), `load_preset`,
`load_panel_preset`, and "before last load" — `stash_prev` / `swap_prev`
over `slot_prev` (the patch) and `stack_prev` (the lanes, FX and curves a
whole Sound replaced). That stash is the edited slot's: `Layers.select_layer`
swaps it with the slot, `SongBridge.edit_track` sets it aside.

A mixin: a load replaces the session's live sound, lanes and baselines.
"""

from __future__ import annotations

import copy
import json
from dataclasses import fields, replace
from pathlib import Path

from ...synth import Patch
from .. import schema
from ..files import safe_name
from ..paramui import PARAM_GROUPS
from .model import (
    LEGACY_PANELS,
    Arrangement,
    _empty_layer,
    _meta_ok,
    _patch_to_json,
    _read_json,
    log,
    patch_from_json,
    preset_from_json,
)


def preset_meta(patch: Patch) -> dict:
    """`soundmeta.preset_meta`, read off the package on every call: it has
    always been patchable as `synthlab.studio.state.preset_meta`
    (tests/studio/test_state.py), and stays so."""
    from . import preset_meta as measure

    return measure(patch)


class Presets:
    """Preset files, sound loads, and the "before last load" stash."""

    # the session's fields this unit reads or writes (all set in
    # StudioState.__init__; test_state_split.py holds this list to the code)
    data_dir: Path
    patch: Patch
    arrangement: Arrangement
    ab_live: str | None
    slot_prev: Patch | None
    _layer_undo: dict[int, dict]
    curves: dict
    baselines: dict
    layers: list[dict]
    stack_prev: dict | None
    sound_brought: dict | None
    active_layer: int
    layer_focus: bool
    loop_edit: dict[int, dict]

    # -- presets ------------------------------------------------------------
    def preset_dir(self) -> Path:
        d = self.data_dir / "presets"
        d.mkdir(exist_ok=True)
        return d

    def _meta_path(self, name: str) -> Path:
        return self.preset_dir() / f"{safe_name(name)}.meta.json"

    def save_preset(self, name: str, include_layers: bool = False) -> Path:
        # "save as My Bass" makes the sound My Bass: the identity line and a
        # reload both used to keep the old name
        self.patch = replace(self.patch, name=name)
        self.mark_baseline("sound", name)  # saving makes this the "how it was"
        path = self.preset_dir() / f"{safe_name(name)}.json"
        if include_layers:
            # a format-2 file carries the main lane's FX too: they are
            # part of what was saved, so "how it was" moves with them
            self.mark_baseline("fx", name)
            # format 2: the WHOLE sound — what the audition actually played
            # v1 flat-Patch files keep loading unchanged.
            payload: dict = self.sound_payload()
        else:
            payload = _patch_to_json(self.patch)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        meta = preset_meta(self.patch) | {"starred": False, "verified": True}
        self._meta_path(name).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return path

    def sound_payload(self) -> dict:
        """The whole sound as a format-2 preset holds it — the edited
        lane's patch, the other lanes, the main lane's FX, the curves —
        with nothing saved or marked: `save_preset(include_layers=True)`
        writes it, a keys take and a loop keep it (`keytakes`, `loops`)."""
        self.stash_active_layer()
        return {
            "format": 2,
            "patch": _patch_to_json(self.patch),
            "layers": json.loads(json.dumps(self.other_layers())),
            "main_fx": json.loads(
                json.dumps(self.layers[self.active_layer].get("fx") or [])
            ),
            "curves": json.loads(json.dumps(self.curves)),
        }

    def list_presets(self) -> list[str]:
        return sorted(
            p.stem
            for p in self.preset_dir().glob("*.json")
            if not p.name.endswith(".meta.json")
        )

    def _meta_for(self, stem: str) -> dict:
        """Read this preset's meta, backfilling (recomputing + persisting)
        when missing/corrupt/stale-unverified. Targets a single preset —
        callers that need every preset's meta should loop over
        list_presets() themselves."""
        meta_path = self._meta_path(stem)
        meta: dict | None = None
        try:
            meta = _read_json(meta_path)
            if not _meta_ok(meta):
                meta = None
        except (OSError, ValueError):
            meta = None
        if meta is None:
            try:
                patch_path = self.preset_dir() / f"{safe_name(stem)}.json"
                loaded = preset_from_json(_read_json(patch_path))
                if loaded is None:
                    raise ValueError("not a preset")
                meta = preset_meta(loaded[0]) | {"starred": False, "verified": True}
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            except Exception:  # an unreadable preset gets blank meta, never a 500
                log.warning("preset meta backfill failed for %s", stem, exc_info=True)
                meta = {"tags": [], "spectrum": [0.0] * 24, "starred": False}
        return meta

    def list_presets_meta(self) -> list[dict]:
        metas: list[dict] = []
        for stem in self.list_presets():
            meta = self._meta_for(stem)
            metas.append(
                {
                    "name": stem,
                    "tags": meta.get("tags", []),
                    "spectrum": meta.get("spectrum", [0.0] * 24),
                    "starred": bool(meta.get("starred", False)),
                }
            )
        metas.sort(key=lambda m: (not m["starred"], m["name"].lower()))
        return metas

    def toggle_star(self, name: str) -> None:
        if not (self.preset_dir() / f"{safe_name(name)}.json").exists():
            return  # nothing to star — don't write an orphan meta file
        meta = self._meta_for(name)  # targeted backfill, not a full scan
        meta["starred"] = not bool(meta.get("starred", False))
        self._meta_path(name).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def stash_prev(self) -> None:
        """Keep the outgoing patch before a load overwrites it. A load that
        brings a whole Sound stashes its stack too (`load_sound`); any
        other load is patch-only, so an older stack stash is dropped."""
        self.slot_prev = self.patch
        self.stack_prev = None

    def open_slot(self) -> None:
        """A sound picked on purpose has to be heard: unmute the layer slot
        it lands in. Loading into a muted active layer used to leave the new
        sound silent — and every audition of it identical."""
        self.set_layer_muted(self.active_layer, False)
        # a level slider at 0 is the same trap (every sound picked after it
        # landed silent): a closed level opens to 1.0; a quiet one is yours
        self.layers[self.active_layer]["gain"] = self.landing_gain()

    def landing_gain(self) -> float:
        """The edited slot's level once a load opens it (`open_slot`): what
        "in your mix" hears, so it still equals where Use lands."""
        gain = float(self.layers[self.active_layer].get("gain", 1.0))
        return gain if gain > 0.0 else 1.0

    def load_preset(self, name: str) -> None:
        path = self.preset_dir() / f"{safe_name(name)}.json"
        loaded = preset_from_json(_read_json(path))
        if loaded is None:
            return
        self.load_sound(*loaded, label=name)

    def load_sound(
        self, patch: Patch, chain: dict | None = None, *, label: str = ""
    ) -> None:
        """Put a picked sound live — the one load every browse surface
        shares (`sounds.py`; `StudioService.adopt`): the outgoing patch goes
        to "before last load", the slot it lands in is unmuted, and the
        sound becomes the "sound" baseline under `label`, so it is not
        "edited" and Revert returns here. `chain` is a format-2 preset's
        extras (`layers`/`main_fx`/`curves`, conformed by
        `preset_from_json`): they replace the other lanes, the main lane's
        FX and the curves, and the FX become the "fx" baseline too.

        Without a `chain` the lane's FX are yours and stay — unless they
        are exactly the ones the last Sound brought (`sound_fx`): those
        belong to that sound, and left behind they quietly reshaped every
        load after it (a card's 170 Hz highpass gutted each bass picked
        next). They go, and "before last load" brings them back."""
        lingers = self._sound_lingers() if chain is None else {}
        self.sound_brought = None
        self.stash_prev()
        self.open_slot()
        self.ab_live = None  # a new sound: in neither A/B slot
        active = self.layers[self.active_layer]
        inherited = chain is None and active.get("sound_fx") is not None
        if inherited and active.get("fx") != active["sound_fx"]:
            inherited = False  # you changed them: they are yours now
        if chain is not None or inherited or any(lingers.values()):
            # "before last load" takes the whole stack it replaces, so it
            # brings the old lanes, FX and curves back with the patch
            self.stack_prev = self._stack_snapshot()
        active.pop("sound_fx", None)
        self.patch = patch
        if inherited:
            active["fx"] = []
            self.mark_baseline("fx", label)
        # the last Sound's other lanes and curves, untouched since: they are
        # that sound's, as its FX are — left behind, its pad played under
        # every sound picked after it (the previous sound's settings
        # blended over)
        if lingers.get("layers"):
            for i in range(len(self.layers)):
                if i != self.active_layer:
                    self.layers[i] = _empty_layer()
                    self._layer_undo.pop(i, None)
                    self.loop_edit.pop(i, None)  # (its saved loop: never written over)
        if lingers.get("curves"):
            self.curves = {}
        if chain is not None:
            self.curves = copy.deepcopy(chain["curves"])
            active["fx"] = copy.deepcopy(chain["main_fx"])
            if chain["main_fx"]:
                active["sound_fx"] = copy.deepcopy(chain["main_fx"])
            if chain["layers"] is not None:
                # the Sound's main patch lands in the edited slot: it is the
                # main lane its layers follow
                if self.follows(self.active_layer):
                    # the lane it followed is among those replaced: its
                    # notes stay, on the slot that is now the main lane
                    self.arrangement = self.lane_arrangement()
                self._make_active_main()
                # a Sound picked *with* its layers is heard with them: a
                # "This layer" left on from an earlier edit swallowed them
                self.layer_focus = False
                others = copy.deepcopy(list(chain["layers"]))
                for i in range(len(self.layers)):
                    if i == self.active_layer:
                        continue
                    self.layers[i] = others.pop(0) if others else _empty_layer()
                    self._layer_undo.pop(i, None)  # that slot holds a new lane
                    self.loop_edit.pop(i, None)  # (its saved loop: never written over)
            # the file's main-lane FX are now the live ones: "how it was"
            self.mark_baseline("fx", label)
            # what this Sound brought besides its patch and FX, as placed: a
            # next load takes them away again unless they were changed
            self.sound_brought = {
                "active": self.active_layer,
                "layers": None
                if chain["layers"] is None
                else {
                    i: copy.deepcopy(lay)
                    for i, lay in enumerate(self.layers)
                    if i != self.active_layer
                },
                "curves": copy.deepcopy(self.curves),
            }
        self.mark_baseline("sound", label)

    # what a picked sound replaces (`load_sound_as`): "" as a load always
    # has — a Sound with layers brings them, any other replaces the edited
    # lane's sound —, "layer" the edited lane's sound alone, "whole" the
    # whole stack, "add" none: a new layer beside it
    LOAD_AS = ("", "layer", "whole", "add")

    def load_sound_as(
        self,
        patch: Patch,
        chain: dict | None = None,
        *,
        how: str = "",
        label: str = "",
        select: bool = True,
    ) -> bool:
        """`load_sound`, told what the sound replaces (`LOAD_AS`) — with
        two layers, a load that is not told would silently swap the one
        being edited. "layer": the Sound's patch with its own FX
        and curves, the other lanes left alone (a layered Sound's layers
        stay behind). "whole": the other lanes are replaced too — by the
        Sound's layers, or emptied when it brings none (`_load_alone`).
        "add": into a free slot as a layer following the main lane at ±0
        (`add_layer`, `select` as there), its FX with it — False when
        every slot holds a sound, and nothing is loaded."""
        if how == "add":
            fx = (chain or {}).get("main_fx") or []
            return self.add_layer(patch, fx, select=select) is not None
        if how == "layer" and chain is not None:
            chain = {**chain, "layers": None}
        if how == "whole" and (chain is None or chain["layers"] is None):
            self._load_alone(patch, chain, label)
            return True
        self.load_sound(patch, chain, label=label)
        return True

    def _load_alone(self, patch: Patch, chain: dict | None, label: str) -> None:
        """ "The whole sound" for a Sound that brings no layers: loaded as
        any load is, and the other lanes go with what they held — "before
        last load" brings the stack back. An edited follower first takes
        the notes it was playing, as a load bringing layers does."""
        stack = self._stack_snapshot()
        if self.follows(self.active_layer):
            self.arrangement = self.lane_arrangement()
        self.load_sound(patch, chain, label=label)
        for i in range(len(self.layers)):
            if i != self.active_layer:
                self.layers[i] = _empty_layer()
                self._layer_undo.pop(i, None)
                self.loop_edit.pop(i, None)  # (its saved loop: never written over)
        self._make_active_main()
        self.stack_prev = stack

    def _sound_lingers(self) -> dict:
        """Which of what the last Sound brought is still here as it came:
        its other lanes (all of them, unchanged, and the edited slot the
        same one) and its curves (unchanged, and not none)."""
        b = self.sound_brought
        if not b:
            return {}
        same_slot = b["active"] == self.active_layer
        lanes = b["layers"]
        return {
            "layers": same_slot
            and lanes is not None
            and any(lay.get("patch") or lay.get("cells") for lay in lanes.values())
            and all(self.layers[i] == lay for i, lay in lanes.items()),
            "curves": bool(b["curves"]) and self.curves == b["curves"],
        }

    def _stack_snapshot(self) -> dict:
        """Everything a Sound's load replaces besides the patch: the other
        lanes, the main lane's FX, the curves."""
        return {
            "layers": copy.deepcopy(self.layers),
            "main_fx": copy.deepcopy(self.layers[self.active_layer].get("fx") or []),
            "curves": copy.deepcopy(self.curves),
        }

    def swap_prev(self) -> bool:
        """Before last load: swap the live sound with the stash (twice is a
        no-op, never a loss) — the patch, and, when the last load brought a
        whole Sound, its lanes, main FX and curves too. False when nothing
        was loaded yet — into this slot: the stash is the edited slot's own
        (`select_layer` swaps it with the slot), so it always lands where
        it was taken."""
        if self.slot_prev is None:
            return False
        self.slot_prev, self.patch = self.patch, self.slot_prev
        prev = self.stack_prev
        if prev is not None:
            now = self._stack_snapshot()
            n = schema.nodes()
            for i, lay in enumerate(prev["layers"]):
                if i != self.active_layer and i < len(self.layers):
                    self.layers[i] = schema.conform(lay, n.LAYER)
                    self._layer_undo.pop(i, None)
                    self.loop_edit.pop(i, None)  # (its saved loop: never written over)
            active = self.layers[self.active_layer]
            active["fx"] = schema.conform(prev["main_fx"], n.FX)
            self.curves = schema.conform(prev["curves"], n.CURVES)
            self.stack_prev = now
        return True

    def rename_preset(self, old: str, new: str) -> bool:
        """Move a preset's json + meta to a new name. False when the source
        is missing, the target exists, or the safe name collapses."""
        src = self.preset_dir() / f"{safe_name(old)}.json"
        dst = self.preset_dir() / f"{safe_name(new)}.json"
        if not src.exists() or dst.exists() or safe_name(new) == "untitled":
            return False
        src.rename(dst)
        meta = self._meta_path(old)
        if meta.exists():
            meta.rename(self._meta_path(new))
        # the name inside the file, and the live sound when it is this one,
        # follow the rename (loading it back used to bring the old name)
        try:
            data = json.loads(dst.read_text(encoding="utf-8"))
            body = data.get("patch") if data.get("format") == 2 else data
            if isinstance(body, dict):
                body["name"] = new
                dst.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError, AttributeError):
            pass
        if self.patch.name == old:
            # rename the live sound; the baseline keeps its snapshot (unsaved
            # edits stay "edited") and only takes the new label
            self.patch = replace(self.patch, name=new)
            base = self.baselines.get("sound")
            if isinstance(base, dict):
                base["label"] = new
                data = base.get("data")
                if isinstance(data, dict) and isinstance(data.get("patch"), dict):
                    data["patch"]["name"] = new
        return True

    def delete_preset(self, name: str) -> None:
        (self.preset_dir() / f"{safe_name(name)}.json").unlink(missing_ok=True)
        self._meta_path(name).unlink(missing_ok=True)

    # -- panel presets --------------------------------------------------------
    def panel_preset_dir(self, group: str) -> Path:
        d = self.data_dir / "presets" / "panels" / safe_name(group)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _panel_field_names(self, group: str) -> list[str]:
        for name, specs in PARAM_GROUPS:
            if name == group:
                return [spec.name for spec in specs]
        return []

    def save_panel_preset(self, group: str, name: str) -> Path:
        fields = {
            field_name: getattr(self.patch, field_name)
            for field_name in self._panel_field_names(group)
        }
        path = self.panel_preset_dir(group) / f"{safe_name(name)}.json"
        path.write_text(json.dumps(fields, indent=2), encoding="utf-8")
        return path

    def _legacy_panel_dirs(self, group: str) -> list[Path]:
        """Existing preset folders of the panels this one replaced —
        read-only: new saves go to the new name."""
        root = self.data_dir / "presets" / "panels"
        return [
            root / safe_name(old)
            for old in LEGACY_PANELS.get(group, ())
            if (root / safe_name(old)).is_dir()
        ]

    def list_panel_presets(self, group: str) -> list[str]:
        dirs = [self.panel_preset_dir(group), *self._legacy_panel_dirs(group)]
        return sorted({p.stem for d in dirs for p in d.glob("*.json")})

    def load_panel_preset(self, group: str, name: str) -> None:
        path = self.panel_preset_dir(group) / f"{safe_name(name)}.json"
        if not path.is_file():  # saved under a panel name that no longer exists
            for d in self._legacy_panel_dirs(group):
                if (d / f"{safe_name(name)}.json").is_file():
                    path = d / f"{safe_name(name)}.json"
                    break
        loaded = _read_json(path)
        if isinstance(loaded, dict):
            # a panel preset only ever holds its panel's fields, but the panels
            # were regrouped (the filter LFO moved from Filter to Motion), so
            # an older file may carry fields this panel no longer shows:
            # apply every patch field it holds, never the name
            valid = {f.name for f in fields(Patch)} - {"name", "voice_fx"}
            changes = {k: v for k, v in loaded.items() if k in valid}
            # a panel preset is a partial patch file: through the same
            # schema node as any other patch, so a hostile value is clamped
            merged = patch_from_json({**self.patch.to_json(), **changes})
            if merged is not None:
                self.stash_for_load()  # Ctrl+Z / "before last load" reach it
                self.patch = merged
