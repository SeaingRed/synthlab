"""Read a sample kit's SFZ into a map: rows, articulations, layers and mics.

Naked Drums (Wilkinson Audio, CC-BY 4.0) is an SFZ instrument: one file per
(drum, mic) under `Data/group/` holds a `<group>` per articulation, each
pulling its sample codes (`Data/file/`) and velocity splits (`Data/region/`)
in with `#include`. This reads those files directly, with only the opcodes
the kit uses, and answers: for a row and an articulation, which velocity
layers, which round robins, and which file per mic.

What it keeps, by design:

- the keys come from the GM keymap (`User/Naked Drums GM keymap.txt`), the
  kit's own table, so a key means what the kit says it means;
- the **non-variable hi-hat mode** only: a group gated on a controller
  (`locc` / `hicc`), a release trigger or a key switch (`sw_down`) is not a
  hit and is left out, as are the `*silence` regions that only choke;
- snare 2 (key 40, group file suffix `top2`) is out of scope;
- `Articulation.trim_db` is the articulation's own trim (`group_volume`);
  a mic's `master_volume` is a mix balance and left to the mixer;
- a hat or ride has two direct mics (`ch` and `di`) that the map calls one,
  `hats` / `ride`: the `di` take wins.

A row with one articulation (the kick, the toms, the splash, the china) calls
it "hit". Sample paths are relative to the kit directory, so `root / path`
is the file.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_BASE = ("Wilkinson Audio", "Naked Drums")
_KEYMAP = "User/Naked Drums GM keymap.txt"

# the keymap's define -> (row, articulation)
KEYS: dict[str, tuple[str, str]] = {
    "KEY_KICK": ("kick", "hit"),
    "KEY_SNR1": ("snare", "centre"),
    "KEY_SIDE": ("snare", "side"),
    "KEY_TOM1": ("tom", "hit"),
    "KEY_TOM2": ("tom2", "hit"),
    "KEY_TOM3": ("tom3", "hit"),
    "KEY_TOM4": ("tom4", "hit"),
    "KEY_TOM5": ("tom5", "hit"),
    "KEY_RD_BOW": ("ride", "bow"),
    "KEY_RD_BEL": ("ride", "bell"),
    "KEY_CRS1_EDG": ("crash", "edge"),
    "KEY_CRS1_BEL": ("crash", "bell"),
    "KEY_CRS2_EDG": ("crash2", "edge"),
    "KEY_CRS2_BEL": ("crash2", "bell"),
    "KEY_SPL1": ("splash", "hit"),
    "KEY_CHN1": ("china", "hit"),
    "KEY_HH_PDL": ("hat", "pedal"),
    "KEY_HH_TGH": ("hat", "tight"),
    "KEY_HH_CLS": ("hat", "closed"),
    "KEY_HH_HLF": ("hat", "half"),
    "KEY_HH_OPN": ("hat", "open"),
}

# the mics, in the order a kit lists them
MICS = (
    "kick_in",
    "kick_sub",
    "snare_top",
    "snare_bottom",
    "toms",
    "hats",
    "ride",
    "cymbals",
    "overheads",
    "close_room",
    "far_room",
    "mid_side",
)

_ROOM = {"oh": "overheads", "cr": "close_room", "fr": "far_room", "ms": "mid_side"}
_ONE = {"in": "kick_in", "sub": "kick_sub", "top1": "snare_top", "bot": "snare_bottom"}

# a row's choke group (the cymbals and hats; absent = the drum rings out)
_CHOKE = {"hat": "hat", "ride": "ride", "crash": "crash", "crash2": "crash2"}

_DEFINE = re.compile(r"#define\s+(\$\w+)\s+(.*?)(?=\s+#define\s|\s*$)")
_OPCODE = re.compile(r"([A-Za-z_]\w*)=")
_VAR = re.compile(r"\$\w+")
_INCLUDE = re.compile(r'#include\s+"([^"]+)"')
_TAG = re.compile(r"<(\w+)>\s*")


@dataclass(frozen=True)
class Layer:
    lo: int
    hi: int
    takes: tuple[dict[str, str], ...]  # per round robin: {mic: path}


@dataclass(frozen=True)
class Articulation:
    row: str
    name: str
    layers: tuple[Layer, ...]
    trim_db: float
    group: str


@dataclass(frozen=True)
class KitMap:
    arts: dict[tuple[str, str], Articulation]
    mics: tuple[str, ...]


def _expand(text: str, defs: dict[str, str]) -> str:
    """Substitute `$names`, each the longest defined name that starts the token
    (`$MIC_$ART_` is `$MIC` then `$ART`), until nothing changes."""

    def one(m: re.Match[str]) -> str:
        tok = m.group(0)
        for n in range(len(tok), 1, -1):
            if tok[:n] in defs:
                return defs[tok[:n]] + tok[n:]
        return tok

    for _ in range(8):
        new = _VAR.sub(one, text)
        if new == text:
            break
        text = new
    return text


def _lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [ln.split("//", 1)[0].strip() for ln in raw.splitlines()]


def _define(line: str, defs: dict[str, str]) -> bool:
    if not line.startswith("#define"):
        return False
    for name, value in _DEFINE.findall(line):
        defs[name] = _expand(value.strip(), defs)
    return True


def _opcodes(text: str) -> dict[str, str]:
    marks = list(_OPCODE.finditer(text))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end() : end].strip()
    return out


def _walk(path: Path, defs: dict[str, str], anchor: Path, seen: tuple[Path, ...] = ()):
    """Yield over a file and its `#include`s, in order, the defines applied as
    they are met: `("tag", name)` for a header and `("ops", {...})` for the
    opcodes after it, `$names` already substituted. Includes resolve against
    `anchor`, the folder of the SFZ that includes them all (`Stereo/`), as
    the kit's `"../Data/..."` paths expect, not against the including file."""
    if path in seen or len(seen) > 8 or not path.is_file():
        return
    for line in _lines(path):
        if not line or _define(line, defs):
            continue
        inc = _INCLUDE.match(line)
        if inc:
            target = Path(posixpath.normpath((anchor / inc.group(1)).as_posix()))
            yield from _walk(target, defs, anchor, (*seen, path))
            continue
        while line:
            tag = _TAG.match(line)
            if tag:
                yield "tag", tag.group(1)
                line = line[tag.end() :]
                continue
            cut = re.search(r"<\w+>", line)
            body, line = (
                (line[: cut.start()], line[cut.start() :]) if cut else (line, "")
            )
            ops = _opcodes(_expand(body, defs))
            if ops:
                yield "ops", ops


def _global_defines(base: Path) -> dict[str, str]:
    defs: dict[str, str] = {}
    for f in (base / "Data" / "macro.txt", base / _KEYMAP):
        if f.is_file():
            for line in _lines(f):
                _define(line, defs)
    return defs


def _mic(stem: str, suffix: str) -> str | None:
    if suffix in _ROOM:
        return _ROOM[suffix]
    if suffix in _ONE:
        return _ONE[suffix]
    if suffix in ("di", "ch"):
        if stem.startswith("tom"):
            return "toms"
        if stem == "hats":
            return "hats"
        if stem == "ride":
            return "ride"
        if stem.startswith(("crash", "splash", "china")):
            return "cymbals"
    return None


def _gated(ops: dict[str, str]) -> bool:
    """Not a plain hit: a controller range, a release trigger, a key switch."""
    return (
        ops.get("trigger") == "release"
        or "sw_down" in ops
        or any(k.startswith(("locc", "hicc")) for k in ops)
    )


def _num(text: str | None, default: float = 0.0) -> float:
    try:
        return float(text) if text is not None else default
    except ValueError:
        return default


def _group_file(
    path: Path,
    defs: dict[str, str],
    keys: dict[int, tuple[str, str]],
    anchor: Path,
    prefix: str,
):
    """The regions of one group file: (row, art, trim, lo, hi, seq, sample)."""
    out = []
    master: dict[str, str] = {}
    group: dict[str, str] = {}
    region: dict[str, str] = {}
    level = "master"
    for kind, p in _walk(path, dict(defs), anchor):
        if kind == "tag":
            level = p
            if p == "master":
                master, group = {}, {}
            elif p == "group":
                group = {}
            region = {}
            continue
        if level == "master":
            master.update(p)
        elif level == "group":
            group.update(p)
        elif level == "region":
            region.update(p)
            if "sample" not in region:
                continue
            reg = {**master, **group, **region}
            region = {}
            sample = reg["sample"]
            hit = keys.get(int(_num(reg.get("key"), -1)))
            if (
                sample.startswith("*")
                or _gated(reg)
                or hit is None
                or "lovel" not in reg
            ):
                continue
            out.append(
                (
                    *hit,
                    _num(reg.get("group_volume")),
                    int(_num(reg["lovel"])),
                    int(_num(reg.get("hivel"), 127)),
                    int(_num(reg.get("seq_position"), 1)),
                    posixpath.join(prefix, "Samples", sample),
                )
            )
    return out


def _read(base: Path, root: Path) -> KitMap:
    defs = _global_defines(base)
    keys: dict[int, tuple[str, str]] = {}
    for name, ra in KEYS.items():
        if defs.get(f"${name}", "").isdigit():
            keys[int(defs[f"${name}"])] = ra
    prefix = base.relative_to(root).as_posix()
    prefix = "" if prefix == "." else prefix
    files = sorted(
        (base / "Data" / "group").glob("*.txt"),
        key=lambda f: (f.stem.rpartition("_")[2] != "ch", f.name),  # ch before di
    )
    cells: dict[tuple[str, str], dict[tuple[int, int], dict[int, dict[str, str]]]] = {}
    trims: dict[tuple[str, str], float] = {}
    seen_mics: set[str] = set()
    for f in files:
        stem, _, suffix = f.stem.rpartition("_")
        mic = _mic(stem, suffix)
        if mic is None:
            continue
        for row, art, trim, lo, hi, seq, sample in _group_file(
            f, defs, keys, base / "Stereo", prefix
        ):
            seen_mics.add(mic)
            trims.setdefault((row, art), trim)
            takes = cells.setdefault((row, art), {}).setdefault((lo, hi), {})
            takes.setdefault(seq, {})[mic] = sample
    arts = {}
    for (row, art), layers in cells.items():
        built = tuple(
            Layer(lo, hi, tuple(takes[s] for s in sorted(takes)))
            for (lo, hi), takes in sorted(layers.items())
        )
        arts[(row, art)] = Articulation(
            row, art, built, trims[(row, art)], _CHOKE.get(row, "")
        )
    return KitMap(arts, tuple(m for m in MICS if m in seen_mics))


@lru_cache(maxsize=4)
def _cached(root: str) -> KitMap:
    top = Path(root)
    base = top.joinpath(*_BASE)
    return _read(base if base.is_dir() else top, top)


def read_kit(root: Path) -> KitMap:
    """The kit under `root` (the directory `fetch.kit_dir` names), memoised."""
    return _cached(str(Path(root).resolve()))
