"""Every typing key plays (none of the piano typing rows may be dead), and
the keyboard folds away and back.

Measured before the fix, every key of every row in every surface: a row set
to any of the chord library's nine categories played NOTHING (43 of 43
keys) — its pads are the chord poster's, fetched only when its fold was
first opened (chords.js `ensureLib` now loads it); and the classic layout
left K L ; ' and O P silent (piano.js's map now runs on into the next
octave). Q R I [ ] stay silent there: the gaps in a piano's black keys.

Proves: a key press reaches a note-on (piano.noteOn or songrec's hook) in
each surface. Does not prove what it sounds like.
"""

from __future__ import annotations

import json

import pytest

ROWS = {
    "num": "Digit1 Digit2 Digit3 Digit4 Digit5 Digit6 Digit7 Digit8 Digit9 Digit0",
    "top": "KeyQ KeyW KeyE KeyR KeyT KeyY KeyU KeyI KeyO KeyP BracketLeft BracketRight",
    "home": "KeyA KeyS KeyD KeyF KeyG KeyH KeyJ KeyK KeyL Semicolon Quote",
    "low": "KeyZ KeyX KeyC KeyV KeyB KeyN KeyM Comma Period Slash",
}
HOOK = """() => { window.__hits = 0; const S = window.synthlab, p = S.piano;
  const on = p.noteOn, heard = S.onNoteOn;
  p.noteOn = (id, m, v) => { window.__hits++; return on(id, m, v); };
  S.onNoteOn = (m, v) => { window.__hits++; heard?.(m, v); }; }"""
SETUP = """([rows, by]) => { localStorage.setItem('synthlab.rows', rows);
  localStorage.setItem('synthlab.rowsBy', by);
  localStorage.setItem('kbd-mode', 'note'); }"""


def _dead(page, rows: dict, by: str = "0") -> list[str]:
    page.evaluate(SETUP, [json.dumps(rows), by])
    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator(".view-head h1").click()  # the keys go to the page
    page.evaluate(HOOK)
    dead = []
    for row, codes in ROWS.items():
        if rows[row] == "off":
            continue
        for code in codes.split():
            n = page.evaluate("() => window.__hits")
            page.keyboard.down(code)
            page.wait_for_timeout(40)
            page.keyboard.up(code)
            if page.evaluate("() => window.__hits") == n:
                dead.append(code)
    return dead


@pytest.mark.parametrize(
    "surface", ["notes", "chords", "harmonics", "major", "minor", "dim", "altered"]
)
def test_every_key_of_every_row_plays_in_each_surface(studio, surface):
    page = studio.open("editor")
    studio.settle()
    assert _dead(page, dict.fromkeys(ROWS, surface)) == []


def test_the_classic_split_plays_on_into_the_next_octave(studio):
    page = studio.open("editor")
    studio.settle()
    rows = {"num": "chords", "top": "piano", "home": "piano", "low": "off"}
    dead = _dead(page, rows)
    # a piano's gaps: nothing below C, none between E-F and B-C, nothing past F
    assert sorted(dead) == sorted(
        ["KeyQ", "KeyR", "KeyI", "BracketLeft", "BracketRight"]
    )
    # and the next octave's keys say which letters play them
    hints = page.evaluate(
        "() => [...document.querySelectorAll('.pkey[data-rel=\"1\"] .qhint')]"
        ".map((h) => h.textContent).filter(Boolean)"
    )
    assert set("KOLP;'") <= set("".join(hints))


def test_the_keyboard_folds_away_and_back_and_still_plays(studio):
    page = studio.open("editor")
    studio.settle()
    fold = page.locator(".keys-fold")
    assert fold.get_attribute("aria-expanded") == "true"
    assert page.locator("#piano").is_visible()
    fold.click()
    assert page.locator("#keys-dock.folded").count() == 1
    assert not page.locator("#piano").is_visible()
    assert not page.locator("#chord-bar").is_visible()
    page.reload()  # remembered
    page.wait_for_load_state("networkidle")
    assert page.locator("#keys-dock.folded").count() == 1
    assert page.locator(".keys-fold").is_visible()  # the way back is always there
    page.locator(".view-head h1").click()
    page.evaluate(HOOK)
    page.keyboard.press("KeyA")
    assert page.evaluate("() => window.__hits") >= 1  # typing still plays
    page.locator(".keys-fold").click()
    page.wait_for_selector("#piano", state="visible")  # (it fades back in)


def test_a_piano_row_follows_what_each_key_plays_even_folded(studio):
    """A typing row of piano keys plays what Play says each key plays — a
    chord once a chord type is lit (one click, or `) — and ` still switches
    it with the keyboard folded, saying so in a toast (the segments
    unseen)."""
    page = studio.open("editor")
    studio.settle()
    rows = {"num": "chords", "top": "piano", "home": "piano", "low": "off"}
    page.evaluate(SETUP, [json.dumps(rows), "0"])
    page.reload()
    page.wait_for_load_state("networkidle")
    page.locator(".view-head h1").click()
    page.evaluate(HOOK)
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    assert page.evaluate("() => window.__hits") == 1  # a note
    page.click('[data-plays="triad"]')
    page.locator(".view-head h1").click()
    page.evaluate("() => { window.__hits = 0; }")
    page.keyboard.down("KeyA")
    page.wait_for_timeout(60)  # (a chord's notes start on a timer: its strum)
    page.keyboard.up("KeyA")
    assert page.evaluate("() => window.__hits") >= 3  # the chord in key
    page.locator(".keys-fold").click()
    page.wait_for_selector("#keys-dock.folded")
    page.locator(".view-head h1").click()
    page.keyboard.press("Backquote")
    page.wait_for_selector(".toast:has-text('Each key plays: a note')")
    page.evaluate("() => { window.__hits = 0; }")
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    assert page.evaluate("() => window.__hits") == 1


def test_all_rows_sets_every_row_in_one_click_and_the_strip_says_so(studio):
    """Selecting all chords or all notes again is one move: "all rows" in
    the rows' panel (chords.js) sets the four rows,
    keeps them, lights the choice and the toolbar's strip is one chip."""
    page = studio.open("editor")
    page.click('[popovertarget="kbd-rows"]')
    page.click('[data-rows-all="chords"]')
    for row in ("num", "top", "home", "low"):
        assert page.input_value(f'#kbd-rows select[name="row-{row}"]') == "chords"
    kept = json.loads(page.evaluate("() => localStorage.getItem('synthlab.rows')"))
    assert set(kept.values()) == {"chords"}
    assert page.get_attribute('[data-rows-all="chords"]', "aria-pressed") == "true"
    assert page.get_attribute('[data-rows-all="notes"]', "aria-pressed") == "false"
    chips = page.locator("#kr-strip > span")
    assert chips.count() == 1 and chips.first.inner_text().replace("\n", "").endswith(
        "chords"
    )
    page.click('[data-rows-all="notes"]')
    for row in ("num", "top", "home", "low"):
        assert page.input_value(f'#kbd-rows select[name="row-{row}"]') == "notes"
    assert page.get_attribute('[data-rows-all="chords"]', "aria-pressed") == "false"
