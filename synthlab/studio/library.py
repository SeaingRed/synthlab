"""synthlab.studio.library — the generated sound library.

A large bank of ready-to-use patches, built deterministically from
per-family recipes (bass, lead, pad, keys, pluck, fx): every family is
rendered, measured, de-duplicated, and then picked so its members spread
evenly from darkest to brightest. The result is frozen in
``presets/library.json`` (patch + tags + spectrum + brightness) so the
Library page never renders audio just to draw itself.

    uv run python -m synthlab.studio.library          # rebuild the JSON
    LIBRARY                                            # list[dict], loaded
    get_library("Dark Wide Sine Pad") -> Patch | None   # or its old name

Families are *recipes*, not fixed patches: each member is a seeded draw
inside the family's musical ranges, so rebuilding with another seed gives
a different but equally playable bank.
"""

from __future__ import annotations

import json
import logging
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..analysis import extract
from ..analysis.features import Features
from ..synth import Patch, Synth
from .namegen import sound_name
from .soundmeta import band_magnitudes, derive_tags, note_duration

log = logging.getLogger(__name__)

LIBRARY_PATH = Path(__file__).with_name("presets") / "library.json"

# (lo, hi) draws are uniform; "log" ranges are log-uniform; lists are picks.
FAMILIES: dict[str, dict] = {
    "bass": dict(
        pitch="A1",
        osc1=["saw", "square", "sine", "fm", "triangle", "supersaw"],
        osc1_octave=[-1, -1, 0],
        osc2=[None, None, "saw", "square", "sine"],
        osc2_octave=[-1, 0, -2],
        sub_level=(0.2, 0.9),
        cutoff=("log", 90.0, 1800.0),
        resonance=(0.05, 0.65),
        filter_env=(0.0, 2500.0),
        filter_decay=(0.05, 0.5),
        attack=(0.002, 0.03),
        decay=(0.08, 0.6),
        sustain=(0.3, 0.9),
        release=(0.05, 0.4),
        drive=(1.0, 2.6),
        glide=[0.0, 0.0, 0.04, 0.1],
        unison=[1, 1, 2],
        noise_level=(0.0, 0.05),
    ),
    "lead": dict(
        pitch="C4",
        osc1=["saw", "square", "supersaw", "fm", "triangle"],
        osc1_octave=[0, 0, 1],
        osc2=[None, "saw", "square", "triangle"],
        osc2_octave=[0, 1, -1],
        cutoff=("log", 500.0, 8000.0),
        resonance=(0.05, 0.6),
        filter_env=(0.0, 4000.0),
        attack=(0.002, 0.08),
        decay=(0.1, 0.6),
        sustain=(0.4, 0.9),
        release=(0.1, 0.6),
        vibrato_depth=[0.0, 0.0, 0.15, 0.3],
        vibrato_rate=(4.0, 6.5),
        unison=[1, 1, 2, 3],
        unison_detune=(6.0, 24.0),
        drive=(1.0, 2.0),
        glide=[0.0, 0.0, 0.0, 0.06],
        pwm_depth=[0.0, 0.0, 0.4],
    ),
    "pad": dict(
        pitch="C3",
        osc1=["supersaw", "saw", "triangle", "square", "sine"],
        osc2=["saw", "triangle", "sine", None],
        osc2_detune=(-14.0, 14.0),
        osc2_octave=[0, 0, 1, -1],
        cutoff=("log", 300.0, 5000.0),
        resonance=(0.0, 0.4),
        filter_env=(0.0, 1500.0),
        filter_attack=(0.2, 1.2),
        attack=(0.25, 1.2),
        decay=(0.3, 1.5),
        sustain=(0.6, 1.0),
        release=(0.8, 2.8),
        filter_lfo_depth=[0.0, 0.0, 300.0, 900.0],
        filter_lfo_rate=(0.08, 0.6),
        vibrato_depth=[0.0, 0.05, 0.1],
        vibrato_rate=(3.0, 5.5),
        unison=[1, 2, 3, 4],
        unison_detune=(8.0, 26.0),
        unison_spread=(0.5, 1.0),
        noise_level=(0.0, 0.06),
    ),
    "keys": dict(
        pitch="C4",
        osc1=["fm", "triangle", "sine", "square", "saw"],
        fm_ratio=[1.0, 2.0, 3.0, 3.5, 4.0, 7.0],
        fm_index=(0.5, 6.0),
        fm_decay=(0.1, 1.2),
        osc2=[None, None, "sine", "triangle"],
        osc2_octave=[1, 0, 2],
        cutoff=("log", 800.0, 7000.0),
        resonance=(0.0, 0.35),
        filter_env=(0.0, 3000.0),
        filter_decay=(0.15, 0.9),
        attack=(0.002, 0.02),
        decay=(0.3, 1.4),
        sustain=(0.1, 0.6),
        release=(0.2, 1.0),
        tremolo_depth=[0.0, 0.0, 0.2],
        tremolo_rate=(3.0, 6.0),
    ),
    "pluck": dict(
        pitch="C4",
        osc1=["pluck", "pluck", "fm", "saw", "square", "triangle"],
        pluck_damping=(0.2, 0.9),
        pluck_bright=(0.3, 1.0),
        fm_ratio=[1.0, 2.0, 3.0, 5.0],
        fm_index=(1.0, 8.0),
        fm_decay=(0.05, 0.4),
        cutoff=("log", 600.0, 8000.0),
        resonance=(0.0, 0.5),
        filter_env=(500.0, 5000.0),
        filter_decay=(0.04, 0.3),
        filter_sustain=(0.0, 0.2),
        attack=(0.001, 0.01),
        decay=(0.08, 0.5),
        sustain=(0.0, 0.05),
        release=(0.08, 0.5),
        osc2=[None, None, "sine", "square"],
        osc2_octave=[1, 0, -1],
    ),
    "fx": dict(
        pitch="C3",
        osc1=["noise", "saw", "square", "fm", "supersaw", "sine"],
        osc2=["sine", "saw", "fm", None],
        osc2_detune=(-30.0, 30.0),
        osc2_octave=[-2, -1, 0, 1, 2],
        ring_mod=[0.0, 0.0, 0.5, 1.0],
        fm_amount=[0.0, 0.3, 0.8],
        noise_level=(0.0, 0.4),
        noise_color=["white", "pink", "brown"],
        filter_type=["ladder", "lowpass", "bandpass"],
        cutoff=("log", 120.0, 8000.0),
        resonance=(0.2, 0.9),
        filter_env=(0.0, 6000.0),
        filter_lfo_depth=[0.0, 800.0, 2500.0],
        filter_lfo_rate=(0.2, 8.0),
        pitch_env=(-24.0, 24.0),
        pitch_env_decay=(0.05, 0.8),
        attack=(0.002, 0.6),
        decay=(0.2, 2.0),
        sustain=(0.0, 0.8),
        release=(0.3, 2.5),
        drive=(1.0, 3.5),
        mod1_src=["none", "lfo", "fenv", "aenv"],
        mod1_dst=["cutoff", "resonance", "osc1_detune", "pwm_depth"],
        mod1_amt=(-1.0, 1.0),
        mod_lfo_rate=(0.1, 10.0),
    ),
}
PER_FAMILY = 16
_SEP = 0.12  # members of one family must sit at least this far apart


def _draw(spec, r: np.random.Generator):
    if isinstance(spec, list):
        return spec[int(r.integers(len(spec)))]
    if isinstance(spec, tuple) and spec and spec[0] == "log":
        return float(np.exp(r.uniform(np.log(spec[1]), np.log(spec[2]))))
    lo, hi = spec
    if isinstance(lo, int) and isinstance(hi, int):
        return int(r.integers(lo, hi + 1))
    return float(r.uniform(lo, hi))


def draw_patch(family: str, r: np.random.Generator) -> Patch:
    """One seeded member of a family recipe (unset fields stay default)."""
    recipe = {k: v for k, v in FAMILIES[family].items() if k != "pitch"}
    return replace(Patch(), **{k: _draw(v, r) for k, v in recipe.items()})


def _genome_key(p: Patch) -> str:
    d = p.to_json()
    d.pop("name", None)
    return json.dumps(d, sort_keys=True)


def _render(p: Patch, pitch: str) -> tuple[np.ndarray, Features] | None:
    try:
        audio = Synth(p).note(pitch, note_duration(p))
    except Exception:
        log.exception("library: candidate patch %r failed to render", p.name)
        return None
    if not np.all(np.isfinite(audio)) or float(np.abs(audio).max()) < 1e-3:
        return None
    return audio, extract(audio)


def build_family(
    family: str, n: int = PER_FAMILY, seed: int = 7, oversample: int = 4
) -> list[dict]:
    """Draw `n * oversample` candidates, drop soundalikes, keep `n` spread
    evenly across the family's spectral-centroid range (dark -> bright)."""
    r = np.random.default_rng(
        zlib.crc32(f"{family}:{seed}".encode())
    )  # stable, unlike hash()
    pitch = FAMILIES[family]["pitch"]
    kept: list[tuple[Patch, np.ndarray, Features]] = []
    for _ in range(n * oversample):
        p = draw_patch(family, r)
        rendered = _render(p, pitch)
        if rendered is None:
            continue
        audio, f = rendered
        v = f.vector()
        if any(float(np.linalg.norm(v - kv)) < _SEP for _, kv, _ in kept):
            continue
        kept.append((p, v, f))
    kept.sort(key=lambda t: t[2].centroid)
    if len(kept) > n:  # evenly spaced picks along the centroid ordering
        idx = np.linspace(0, len(kept) - 1, n).round().astype(int)
        kept = [kept[int(i)] for i in dict.fromkeys(idx.tolist())]
    out = []
    for rank, (p, _, f) in enumerate(kept):
        bright = rank / max(1, len(kept) - 1)
        shade = "dark" if bright < 0.34 else "bright" if bright > 0.66 else "mid"
        name = sound_name(f, _genome_key(p))
        audio = Synth(replace(p, name=name)).note(pitch, note_duration(p))
        out.append(
            {
                "name": name,
                "family": family,
                "tags": list(dict.fromkeys([family, shade] + derive_tags(f))),
                "brightness": round(bright, 3),
                "centroid": round(f.centroid),
                "spectrum": band_magnitudes(audio),
                "patch": replace(p, name=name).to_json(),
            }
        )
    return out


# --- names: what a sound measures as ----------------------------------------
# The draws were named by `namegen` ("Obsidian Bed", "Wave Beam 2"): a word
# for the shade and a made-up noun, which said nothing about the sound. A
# name now reads off the stored entry alone — its shade, one trait of its
# patch, the oscillator it is built on, its family — so it is the same on
# every machine and a test can re-derive it (`describe`).
_SHADE = {"dark": "Dark", "mid": "Mellow", "bright": "Bright"}
_SOURCE = {
    "sine": "Sine",
    "triangle": "Triangle",
    "saw": "Saw",
    "square": "Square",
    "supersaw": "Supersaw",
    "fm": "FM",
    "pluck": "String",
    "noise": "Noise",
    "wavetable": "Wavetable",
}
NOUNS = {
    "bass": "Bass",
    "lead": "Lead",
    "pad": "Pad",
    "keys": "Keys",
    "pluck": "Pluck",
    "fx": "FX",
}


def traits(p: dict) -> list[str]:
    """A patch's audible traits, the most defining first: what a name may
    say besides its shade, source and family."""
    out: list[str] = []
    osc2 = p.get("osc2") and float(p.get("osc2_level", 0)) > 0.05
    if osc2 and float(p.get("ring_mod", 0)) > 0.2:
        out.append("Ringing")
    if osc2 and float(p.get("fm_amount", 0)) > 0.2:
        out.append("Growling")
    if abs(float(p.get("pitch_env", 0))) >= 6:
        out.append("Rising" if float(p["pitch_env"]) > 0 else "Falling")
    if p.get("mod1_src", "none") != "none" or p.get("mod2_src", "none") != "none":
        out.append("Moving")
    if float(p.get("filter_lfo_depth", 0)) > 0:
        out.append("Sweeping")
    if int(p.get("unison", 1)) >= 3:
        out.append("Wide")
    if float(p.get("resonance", 0)) > 0.5:
        out.append("Resonant")
    if float(p.get("drive", 1)) > 2.0:
        out.append("Driven")
    if float(p.get("glide", 0)) > 0:
        out.append("Gliding")
    if float(p.get("vibrato_depth", 0)) >= 0.1:
        out.append("Vibrato")
    if float(p.get("tremolo_depth", 0)) >= 0.1:
        out.append("Tremolo")
    if float(p.get("pwm_depth", 0)) > 0.1 and "square" in (
        p.get("osc1"),
        p.get("osc2"),
    ):
        out.append("Pulsing")
    if float(p.get("noise_level", 0)) > 0.05:
        out.append("Breathy")
    if int(p.get("unison", 1)) == 2 or (osc2 and abs(float(p["osc2_detune"])) >= 8):
        out.append("Detuned")
    if osc2 and int(p.get("osc2_octave", 0)) < 0:
        out.append("Octave-Down")
    if osc2 and int(p.get("osc2_octave", 0)) > 0:
        out.append("Octave")
    if float(p.get("sub_level", 0)) > 0.5:
        out.append("Subby")
    if float(p.get("filter_env", 0)) > 1500 and float(p.get("filter_decay", 1)) < 0.3:
        out.append("Snappy")
    if float(p.get("attack", 0)) > 0.5:
        out.append("Slow")
    if float(p.get("cutoff", 20000)) < 600:
        out.append("Muted")
    shapes = (p.get("osc1"), p.get("osc2") if osc2 else None)
    if "fm" in shapes and float(p.get("fm_ratio", 2)) % 1:
        out.append("Bell")  # an inharmonic ratio
    if "fm" in shapes and float(p.get("fm_index", 2)) > 4:
        out.append("Metallic")
    if "pluck" in shapes and float(p.get("pluck_damping", 0.5)) > 0.6:
        out.append("Damped")
    if "pluck" in shapes and float(p.get("pluck_damping", 0.5)) < 0.35:
        out.append("Open")
    if float(p.get("sustain", 0)) > 0.5:
        out.append("Sustained")
    if float(p.get("sustain", 1)) < 0.2:
        out.append("Percussive")
    if float(p.get("release", 0)) > 0.8:
        out.append("Lingering")
    return out + ["Plain"]


def describe(entries: list[dict]) -> list[str]:
    """A unique name for each entry, from what it measures as: "{shade}
    {trait} {source} {family}" ("Dark Wide Saw Pad"), the first of its
    traits no earlier entry took. Entries are taken in order (the library's:
    family by family, dark -> bright), so the names are stable."""
    taken: set[str] = set()
    names = []
    for e in entries:
        p = e["patch"]
        shade = next((_SHADE[t] for t in e.get("tags", ()) if t in _SHADE), "")
        source = _SOURCE.get(p.get("osc1"), str(p.get("osc1", "")).title())
        noun = NOUNS[e["family"]]
        for t in traits(p):
            word = "" if t == "Plain" else f"{t} "
            name = f"{shade} {word}{source} {noun}".strip()
            if name not in taken:
                break
        else:  # every trait taken: the source's second word
            name = f"{shade} {source} {p.get('osc2') or 'Solo'} {noun}".title()
        taken.add(name)
        names.append(name)
    return names


def build_library(seed: int = 7, per_family: int = PER_FAMILY) -> list[dict]:
    entries = [e for family in FAMILIES for e in build_family(family, per_family, seed)]
    # named for what each measures as (`describe`: unique by construction),
    # not the draw's made-up name
    for e, name in zip(entries, describe(entries), strict=True):
        e["name"] = e["patch"]["name"] = name
    return entries


def _load() -> list[dict]:
    if not LIBRARY_PATH.exists():
        return []
    with open(LIBRARY_PATH, encoding="utf-8") as f:
        return json.load(f)


LIBRARY: list[dict] = _load()
_BY_NAME = {e["name"]: e for e in LIBRARY}
# a sound's older name (`was`): what an older saved session, a URL, a
# clip-hub plan or the sound agent may still call it
_WAS = {e["was"]: e for e in LIBRARY if e.get("was")}


def get_library(name: str) -> Patch | None:
    """The sound by its name, or by the name it had before (its `was`):
    found either way, it loads under its name now."""
    e = _BY_NAME.get(name) or _WAS.get(name)
    return Patch.from_dict(e["patch"]) if e else None


def by_family() -> list[tuple[str, list[dict]]]:
    """[(family, members dark->bright)] in recipe order, for the page."""
    return [
        (
            fam,
            sorted(
                (e for e in LIBRARY if e["family"] == fam),
                key=lambda e: e["brightness"],
            ),
        )
        for fam in FAMILIES
    ]


def tags() -> list[str]:
    """Filter chips for the Library page: the six families in recipe order,
    then every other tag the library actually uses, alphabetically."""
    seen = {t for e in LIBRARY for t in e.get("tags", ())}
    return list(FAMILIES) + sorted(seen - set(FAMILIES))


if __name__ == "__main__":  # pragma: no cover - build tool
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    lib = build_library(seed=seed)
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LIBRARY_PATH, "w", encoding="utf-8") as fh:
        json.dump(lib, fh, indent=1)
    fams = {e["family"] for e in lib}
    print(f"wrote {len(lib)} sounds in {len(fams)} families -> {LIBRARY_PATH}")
