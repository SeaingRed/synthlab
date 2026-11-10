"""Recompute the stored metadata of the factory, library and recipe sounds.

The patches themselves do not change — only the numbers describing how they
measure. Run this after any engine change that moves what a patch renders
to (the DC blocker did), so the drift checks in
`tests/studio/test_factory.py` and `tests/studio/test_library.py` compare
against fresh readings of the same sounds.

    uv run python tools/refresh_sound_meta.py

Deliberately NOT `python -m synthlab.studio.library`: rebuilding the library
re-draws all 96 candidates and keeps whichever spread best on the *new*
measurements, so every sound would change identity and name. Here the
sounds stay exactly as they are.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from synthlab.analysis.features import extract
from synthlab.studio.library import LIBRARY_PATH
from synthlab.studio.soundmeta import (
    band_magnitudes,
    derive_tags,
    note_duration,
    preset_meta,
)
from synthlab.synth import Patch, Synth

FACTORY_PY = Path(__file__).resolve().parents[1] / "synthlab" / "studio" / "factory.py"


def _fmt(vals: list[float]) -> str:
    return "[" + ", ".join(f"{v:.3f}" for v in vals) + "]"


def refresh_library() -> int:
    """Remeasure every stored sound at its own family's audition pitch.

    Brightness, the shade tag and the shelf order are all *derived* from the
    spectral centroid, so remeasuring means re-ranking: a sound whose
    centroid moved has to move with it, or the shelf stops reading dark ->
    bright and `test_library_json_is_complete_and_ordered` says so.
    """
    from synthlab.studio.library import FAMILIES

    entries = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    for e in entries:
        patch = Patch(**e["patch"])
        audio = Synth(patch).note(FAMILIES[e["family"]]["pitch"], note_duration(patch))
        e["_feats"] = extract(audio)
        e["centroid"] = round(e["_feats"].centroid)
        e["spectrum"] = band_magnitudes(audio)

    out: list[dict] = []
    for family in FAMILIES:
        members = sorted(
            (e for e in entries if e["family"] == family), key=lambda e: e["centroid"]
        )
        for rank, e in enumerate(members):
            bright = rank / max(1, len(members) - 1)
            shade = "dark" if bright < 0.34 else "bright" if bright > 0.66 else "mid"
            e["brightness"] = round(bright, 3)
            e["tags"] = list(
                dict.fromkeys([family, shade] + derive_tags(e.pop("_feats")))
            )
            out.append(e)
    LIBRARY_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return len(out)


def refresh_factory() -> int:
    from synthlab.studio.factory import FACTORY

    src = FACTORY_PY.read_text(encoding="utf-8")
    done = 0
    for entry in FACTORY:
        patch = Patch(**entry["patch"])
        fresh = _fmt(preset_meta(patch)["spectrum"])
        # each factory entry is a dict literal with a "spectrum": [...] line
        pattern = re.compile(
            r'("name":\s*"'
            + re.escape(entry["name"])
            + r'".*?"spectrum":\s*)\[[^\]]*\]',
            re.S,
        )
        src, n = pattern.subn(lambda m, spec=fresh: m.group(1) + spec, src, count=1)
        done += n
    FACTORY_PY.write_text(src, encoding="utf-8")
    return done


def refresh_recipes() -> int:
    """Remeasure the genre recipes (`studio/recipes.py`): each one's whole
    stack — main with FX and curves, layers at their octaves and gains —
    re-ranked dark -> bright, into `presets/recipes_meta.json`."""
    from synthlab.studio.recipes import META_PATH, measure_all

    meta = measure_all()
    META_PATH.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return len(meta)


def refresh_map_points() -> int:
    """Remeasure the Map's faint field (`studio/mapview.py` SHELF_PATH):
    every shipped sound on the breeder's probe, into
    `presets/map_points.json`."""
    from synthlab.studio.mapview import SHELF_PATH, measure_shelf

    rows = measure_shelf()
    body = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)  # a sound a line
    SHELF_PATH.write_text(f"[\n{body}\n]\n", encoding="utf-8")
    return len(rows)


if __name__ == "__main__":
    print(f"library: refreshed {refresh_library()} sounds")
    print(f"factory: refreshed {refresh_factory()} sounds")
    print(f"recipes: refreshed {refresh_recipes()} sounds")
    print(f"map: measured {refresh_map_points()} sounds")
