"""Impulse responses for the convolution unit (`effects.convolve`).

A clip from the clip store picked on the unit is made into a sample —
`sampler.save` into the session's sample store, named by the sha1 of its
16-bit PCM — and the unit stores that id (`"ir"` on the unit,
`fxrack.clean_chain`). So every render key holding the unit holds the IR
by content, a project bundle carries it as it carries a sampler's sample
(`projectzip.sample_refs`), and an id this studio does not have is a dry
pass, never an error.

Bounded (`limits.MAX_IR_SECONDS`, `MAX_IR_BYTES`): a clip longer or larger
is not offered, and refused in words if posted. The rack's select posts
`clip:<name>`; every rack route hands its form to `resolve` before
`fxrack.edit_chain`, which takes only an id.
"""

from __future__ import annotations

from pathlib import Path

from .. import sampler
from . import limits
from .clips import clip_dir, clip_hash, clip_safe, list_clips, load_clip
from .clips import seconds as clips_seconds
from .lru import LRU

PREFIX = "clip:"


class IRError(ValueError):
    """A clip refused as an impulse, in words a toast can say."""


# a clip's content hash -> the sample id it was made into: which clip the
# unit's impulse came from, for the select (a restart forgets: the IR then
# reads by its id until it is picked again)
_MADE: LRU[str] = LRU(256)


def _fits(data_dir: Path, name: str, seconds: float) -> bool:
    path = clip_dir(data_dir) / f"{clip_safe(name)}.wav"
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return 0.0 < seconds <= limits.MAX_IR_SECONDS and size <= limits.MAX_IR_BYTES


def choices(data_dir: Path, current: str) -> list[tuple[str, str, bool]]:
    """The convolution unit's select: `(value, label, selected)` — "none",
    the impulse it holds when no listed clip is known to be it (by its id;
    "missing" when this studio does not have that sample), then every clip
    under the caps as `clip:<name>`."""
    rows = [("", "no impulse", not current)]
    clips = []
    for c in list_clips(data_dir):
        if not _fits(data_dir, c["name"], float(c.get("seconds") or 0.0)):
            continue
        made = _MADE.get(clip_hash(data_dir, c["name"]))
        label = f"{c['name']} · {float(c['seconds']):.1f} s"
        clips.append((PREFIX + c["name"], label, bool(current) and made == current))
    if current and not any(sel for _v, _l, sel in clips):
        gone = sampler.get(current) is None
        rows.append(
            (current, f"impulse {current[:6]}" + (" (missing)" if gone else ""), True)
        )
    return rows + clips


def load(data_dir: Path, sample_dir: Path, name: str) -> str:
    """The clip `name` made into a sample (at most `MAX_IR_SECONDS`): its
    id. Refused (`IRError`): no such clip, over a cap, not readable."""
    path = clip_dir(data_dir) / f"{clip_safe(name)}.wav"
    seconds = float(clips_seconds(data_dir, name))
    if not path.is_file():
        raise IRError("That clip is gone.")
    if path.stat().st_size > limits.MAX_IR_BYTES or seconds > limits.MAX_IR_SECONDS:
        raise IRError(
            f"An impulse is at most {limits.MAX_IR_SECONDS:g} s"
            f" ({limits.MAX_IR_BYTES // 1_000_000} MB): that clip is longer."
        )
    loaded = load_clip(data_dir, name)
    if loaded is None or not len(loaded[0]):
        raise IRError("That clip could not be read as an impulse.")
    audio, sr = loaded
    if len(audio) > limits.MAX_IR_SECONDS * sr:  # the header's word, checked
        raise IRError(f"An impulse is at most {limits.MAX_IR_SECONDS:g} s.")
    sid = sampler.save(sample_dir, audio, sr)
    _MADE.put(clip_hash(data_dir, name), sid)
    return sid


def resolve(data_dir: Path, sample_dir: Path, values) -> dict:
    """A rack post's values with a picked clip (`name=ir`, `value`
    `clip:<name>`) made into its sample id; every other post as it came."""
    out = dict(values)
    value = str(out.get("value", ""))
    if out.get("name") == "ir" and value.startswith(PREFIX):
        out["value"] = load(data_dir, sample_dir, value[len(PREFIX) :])
    return out
