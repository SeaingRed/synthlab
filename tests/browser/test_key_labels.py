"""The on-screen keyboard's labels are the typing keys that play each key.

The user problem: the labels and the keyboard disagreed. Only the first
octave had a label slot, only a "piano" row ever wrote one, and rows by
sound overwrote each other's — so a row of notes in key, or a row tiled
onto the second octave, lit keys that named nothing, and a stray "[ ]" sat
after the number row's "0".

The rule held here, for every typing key of every row, in every layout a
row can be given: **the keys that light are exactly the visible keys that
carry that key's letter.** A key that plays above the on-screen range
lights nothing and is named nowhere. A key several typed keys play shows
ONE letter, the lowest row's, and carries the rest in its `data-typed` and
its title (the "VK" / "Q1" piles read as noise).
"""

from __future__ import annotations

import json

import pytest

ROWS = {
    "num": (
        "Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0",
        "1234567890",
    ),
    "top": (
        "KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight",
        "QWERTYUIOP[]",
    ),
    "home": (
        "KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote",
        "ASDFGHJKL;'",
    ),
    "low": ("KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash", "ZXCVBNM,./"),
}
# piano.js's own letters, when it owns the two rows: A..J, then K O L P ; '
# into the next octave
CLASSIC = set("AWSEDFTGYHUJKOLP;'")

LAYOUTS = {
    "classic": ({"num": "chords", "top": "piano", "home": "piano", "low": "off"}, "0"),
    "every row notes in key": (dict.fromkeys(ROWS, "notes"), "0"),
    "piano rows around the classic pair": (dict.fromkeys(ROWS, "piano"), "0"),
    "one piano row, tiled notes": (
        {"num": "notes", "top": "piano", "home": "notes", "low": "off"},
        "0",
    ),
    "rows by sound": (
        {"num": "piano", "top": "piano", "home": "notes", "low": "notes"},
        "1",
    ),
}

# A key shows ONE letter (the lowest row's to reach it); every typed key
# that plays it is in its data-typed (and its title): "carries" means that
KEYS = """() => [...document.querySelectorAll('.pkey')]
  .filter((k) => k.offsetParent)
  .map((k) => ({
    at: `${k.dataset.rel}/${k.dataset.note}`,
    shown: k.querySelector('.qhint')?.textContent || '',
    typed: (k.dataset.typed || '').split(' ').filter(Boolean),
    title: k.title,
    down: k.classList.contains('down') }))"""
# a key is down, or no visible key carries the letter (nothing to wait for)
LANDED = """(cap) => !!document.querySelector('.pkey.down')
  || ![...document.querySelectorAll('.pkey')]
       .some((k) => k.offsetParent
         && (k.dataset.typed || '').split(' ').includes(cap))"""
SETUP = """([rows, by]) => {
  localStorage.setItem('synthlab.rows', rows);
  localStorage.setItem('synthlab.rowsBy', by);
  localStorage.setItem('kbd-mode', 'note'); }"""


@pytest.mark.parametrize("name", list(LAYOUTS))
def test_a_typed_key_lights_the_keys_that_carry_its_letter(studio, name):
    rows, by_sound = LAYOUTS[name]
    page = studio.open("editor")
    page.evaluate(SETUP, [json.dumps(rows), by_sound])
    page.reload()
    page.wait_for_selector(".pkey")
    studio.settle()
    page.locator(".view-head h1").click()  # the keys go to the page, not a field
    classic = rows["top"] == rows["home"] == "piano" and by_sound == "0"
    checked = 0
    for row, (codes, caps) in ROWS.items():
        if rows[row] not in ("piano", "notes"):
            continue  # off, or chords: a chord has no one key to name
        for code, cap in zip(codes.split(), caps, strict=True):
            if classic and row in ("top", "home") and cap not in CLASSIC:
                continue  # piano.js's rows: its other letters play nothing
            page.keyboard.down(code)
            page.wait_for_function(LANDED, arg=cap)
            keys = page.evaluate(KEYS)
            page.keyboard.up(code)
            page.wait_for_function("() => !document.querySelector('.pkey.down')")
            lit = sorted(k["at"] for k in keys if k["down"])
            named = sorted(k["at"] for k in keys if cap in k["typed"])
            for k in keys:  # one letter on a key, never a "VK" pile
                assert len(k["shown"]) <= 1, f"{name}: {k['at']} shows {k['shown']!r}"
                assert k["shown"] == (k["typed"][0] if k["typed"] else ""), k
                if len(k["typed"]) > 1:
                    assert all(c in k["title"] for c in k["typed"]), k
            assert lit == named, f"{name}: {cap} lights {lit}, is written on {named}"
            assert len(lit) <= 1, f"{name}: {cap} lights {lit}"
            checked += bool(lit)
    assert checked >= 10, f"{name}: only {checked} typed keys reached the keyboard"
