"""Full motion has a pace (frontend.md, "Motion: full or calm"): every loop
that draws itself each frame while something plays asks
`synthlab.paced(key, now)` (colours.js) and draws ~60 times a second on any
display, all on the same frames. On a 240 Hz screen they drew 240 times a
second and the Loops page lagged the whole machine."""

from __future__ import annotations

from pathlib import Path

STATIC = Path("synthlab/studio/static")

PACED = {
    "motion.js": "motion",
    "grid.js": "grid",
    "transport.js": "transport",
    "loopdeck.js": "loopdeck",
    "deckui.js": "deckui",
    "mixer.js": "mixer",
}


def test_colours_defines_one_pace_for_every_loop():
    js = (STATIC / "colours.js").read_text(encoding="utf-8")
    assert "window.synthlab.paced = (key, now) =>" in js
    assert "PACE = 1000 / 64" in js  # a slot a 1/64 s: every frame at 60 Hz


def test_each_self_drawing_loop_asks_the_pace():
    for name, key in PACED.items():
        js = (STATIC / name).read_text(encoding="utf-8")
        assert f'paced?.("{key}", ' in js, name


def test_the_step_leds_cool_in_steps():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    rule = ".cell:checked:not(.now) { transition: box-shadow .5s steps(8, end); }"
    assert rule in css
