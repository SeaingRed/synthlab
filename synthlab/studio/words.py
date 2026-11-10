"""Words the studio says about counts: one rule for "1 bar" / "2 bars".

`plural` is also the templates' filter (`app.make_templates`):
`{{ n | plural("bar") }}`.
"""

from __future__ import annotations


def clock(seconds: float) -> str:
    """A length as a clock reads it: "0:03", "1:20" (whole seconds)."""
    m, s = divmod(round(max(0.0, seconds)), 60)
    return f"{m}:{s:02d}"


def plural(n: float, word: str) -> str:
    """ "1 bar", "2 bars", "1.5 bars": `n` as it reads (`:g`, so a whole
    float drops its ".0") and `word` with an s unless `n` is exactly 1."""
    return f"{n:g} {word}{'' if n == 1 else 's'}"


def hz(value: float) -> str:
    """A frequency as a read-out says it, never wider than "12.7 kHz":
    whole hertz under 1 kHz ("233 Hz"), else kilohertz to a tenth
    ("1.9 kHz"). The Library said "12682 Hz", which ran into the name
    beside it. Also the templates' filter (`{{ e.centroid | hz }}`)."""
    v = max(0.0, float(value or 0))
    return f"{v / 1000:.1f} kHz" if round(v) >= 1000 else f"{round(v)} Hz"
