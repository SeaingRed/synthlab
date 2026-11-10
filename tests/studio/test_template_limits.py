"""Templates iterate the named limits.py globals instead of repeating the
grid/tempo literals. Every RED assertion here also has a
byte-identical-markup companion: the option/chip markup rendered by a
live app must be pixel-for-pixel the same before and after the templates
stopped hardcoding [1, 2, 3, 4] / [4, 8, 16, 32] / 40..200, captured from
the app as it stood immediately before this change."""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import STUDIO_DIR, create_app

TEMPLATES = STUDIO_DIR / "templates"


def test_templates_use_limit_globals():
    for t in (
        "fragments/transport.html",
        "fragments/drum_transport.html",
        "fragments/sound_bar.html",
    ):
        src = (TEMPLATES / t).read_text(encoding="utf-8")
        assert "(4, 8, 16, 32)" not in src
        assert "[4, 8, 16, 32]" not in src


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


# Golden markup, captured from the app immediately before this change (the
# templates still had [1, 2, 3, 4] / [4, 8, 16, 32] / min="40" max="200"
# literally in them). Every byte below must still come out identical once
# the templates read limits.BAR_CHOICES / STEP_CHOICES / DRUM_STEP_CHOICES
# / BPM_RANGE instead.

SOUND_BAR_BPM = (
    '<label>bpm <input type="number" id="t-bpm" name="bpm" min="40" max="200"\n'
    '                        value="124">'
)

DRUM_BPM = (
    '<label>bpm <input class="field field-s" type="number" name="bpm" '
    'min="40" max="200" value="124">'
)

_DRUM_POST = 'hx-post="/fragment/drums/transport"'


def _drum_barchip(n: int, on: bool) -> str:
    cls = "barchip on" if on else "barchip "
    pressed = "true" if on else "false"
    return (
        f'    <button type="button" class="{cls}"\n'
        f'            {_DRUM_POST} hx-vals=\'{{"bars": "{n}"}}\' hx-swap="none"\n'
        f'            aria-pressed="{pressed}">{n}</button>\n'
        "    \n"
    )


DRUM_BARS = (
    '<div class="seg barchips" role="group" aria-label="drum bars">\n'
    "    \n"
    + _drum_barchip(1, True)
    + _drum_barchip(2, False)
    + _drum_barchip(3, False)
    + _drum_barchip(4, False)
    + "  </div>"
)

DRUM_STEPS = (
    '<label>steps <select class="field field-s" name="steps">\n'
    "    <option >4</option><option >8</option>"
    "<option selected>16</option><option >32</option>\n"
    "  </select></label>"
)

TRANSPORT_BARS = (
    '<div class="seg barchips">\n'
    "        \n"
    '        <button type="button" class="barchip on"\n'
    '                hx-post="/fragment/arrangement" hx-include="#transport"\n'
    '                hx-vals=\'{"bars": "1"}\' hx-swap="none"\n'
    '                aria-pressed="true">1</button>\n'
    "        \n"
    '        <button type="button" class="barchip "\n'
    '                hx-post="/fragment/arrangement" hx-include="#transport"\n'
    '                hx-vals=\'{"bars": "2"}\' hx-swap="none"\n'
    '                aria-pressed="false">2</button>\n'
    "        \n"
    '        <button type="button" class="barchip "\n'
    '                hx-post="/fragment/arrangement" hx-include="#transport"\n'
    '                hx-vals=\'{"bars": "3"}\' hx-swap="none"\n'
    '                aria-pressed="false">3</button>\n'
    "        \n"
    '        <button type="button" class="barchip "\n'
    '                hx-post="/fragment/arrangement" hx-include="#transport"\n'
    '                hx-vals=\'{"bars": "4"}\' hx-swap="none"\n'
    '                aria-pressed="false">4</button>\n'
    "        \n"
    "      </div>"
)

TRANSPORT_STEPS = (
    '<label>steps <select class="field field-s" name="steps">\n'
    "        \n"
    "        <option selected>4</option>\n"
    "        \n"
    "        <option >8</option>\n"
    "        \n"
    "        <option >16</option>\n"
    "        \n"
    "        <option >32</option>\n"
    "        \n"
    "      </select></label>"
)


def test_sound_bar_and_drum_transport_render_byte_identical_markup(tmp_path):
    with _client(tmp_path) as c:
        html = c.get("/").text
        m = re.search(
            r'<label>bpm <input type="number" id="t-bpm".*?value="124">', html, re.S
        )
        assert m and m.group(0) == SOUND_BAR_BPM

        html2 = c.get("/view/drums", headers={"HX-Request": "true"}).text
        m2 = re.search(
            r'<label>bpm <input class="field field-s" type="number" name="bpm".*?>',
            html2,
        )
        assert m2 and m2.group(0) == DRUM_BPM

        m3 = re.search(
            r'<div class="seg barchips" role="group" aria-label="drum bars">.*?</div>',
            html2,
            re.S,
        )
        assert m3 and m3.group(0) == DRUM_BARS

        m4 = re.search(
            r'<label>steps <select class="field field-s" name="steps">'
            r".*?</select></label>",
            html2,
            re.S,
        )
        assert m4 and m4.group(0) == DRUM_STEPS

        html3 = c.get("/view/perform", headers={"HX-Request": "true"}).text
        m5 = re.search(r'<div class="seg barchips">.*?</div>', html3, re.S)
        assert m5 and m5.group(0) == TRANSPORT_BARS

        m6 = re.search(
            r'<label>steps <select class="field field-s" name="steps">'
            r".*?</select></label>",
            html3,
            re.S,
        )
        assert m6 and m6.group(0) == TRANSPORT_STEPS
