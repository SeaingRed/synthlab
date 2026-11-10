"""The keyboard dock's columns layout: the home row A through ' plays the
plain notes (A is C), the row above the major triads and the row below the
minors in the same order, and the number row a choice that can be swapped.

What these journeys prove, in Chromium: the typing layout's third choice —
rows | letters | columns, in the typing rows' panel, or Alt+L cycling all
three — is kept per browser, and the strip's button shows the columns' rows
in the rows' place without the QWERTY group changing width; in columns each
keyboard column is a root: the home row A..' plays C D E F G A B C D E F
from the dock's C (asserted against `synthlab.piano.octave()`), the row
above the major triad on the note under it, the row below the minor; the
number row plays its choice (7, maj7, m7, sus4, ♯, octave — the panel's
segment, or Alt+N), the strip saying which; - / = step the octave and the
keys' labels follow it; no key-up order leaves a note sounding, nor a
layout switched mid-hold, nor a lost focus; Space still plays / stops, Left
Shift is the pedal, Right Shift latches; Ctrl+A and Ctrl+C reach the
browser and play nothing. Notes are read off `synthlab.piano.noteOn` —
nobody has listened.
"""

from __future__ import annotations

from .test_letters import FOCUS, HOOK, LIT, PREVENTED, QWERTY


def _open(studio, layout: str = "columns"):
    page = studio.open("editor")
    studio.settle()
    page.click(".kbd-rows-btn")  # the rows' panel: its layout
    page.click(f'#kbd-layout [data-layout="{layout}"]')
    page.keyboard.press("Escape")
    page.wait_for_selector("#kbd-rows:not(:popover-open)", state="attached")
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.evaluate(HOOK)
    return page


def _base(page) -> int:
    """MIDI of C in the dock's own octave."""
    return 12 * (page.evaluate("() => window.synthlab.piano.octave()") + 1)


def _strike(page, key: str, *mods: str) -> list[int]:
    """Hold the modifiers, press the key: the notes it started."""
    page.evaluate("() => { window.__notes = []; }")
    for m in mods:
        page.keyboard.down(m)
    page.keyboard.down(key)
    notes = sorted(page.evaluate("() => window.__notes"))
    page.keyboard.up(key)
    for m in reversed(mods):
        page.keyboard.up(m)
    return notes


def _silent(page) -> None:
    page.wait_for_function(
        "() => window.__live.size === 0 && !document.querySelector('#piano .pkey.down')"
    )


def _num(page, choice: str) -> None:
    page.click(".kbd-rows-btn")
    page.click(f'#kbd-colnum [data-colnum="{choice}"]')
    page.keyboard.press("Escape")
    page.wait_for_selector("#kbd-rows:not(:popover-open)", state="attached")


def _major(m: int) -> list[int]:
    return [m, m + 4, m + 7]


def _minor(m: int) -> list[int]:
    return [m, m + 3, m + 7]


def test_each_column_is_a_root_note_major_and_minor(studio):
    page = _open(studio)
    assert page.get_attribute('[data-layout="columns"]', "aria-pressed") == "true"
    assert page.evaluate("() => document.documentElement.dataset.keylayout") == (
        "columns"
    )
    c = _base(page)
    # the home row: the naturals from the dock's C, single notes
    home = {"KeyA": 0, "KeyS": 2, "KeyD": 4, "KeyF": 5, "KeyG": 7, "KeyH": 9}
    home |= {"KeyJ": 11, "KeyK": 12, "KeyL": 14, "Semicolon": 16, "Quote": 17}
    for key, step in home.items():
        assert _strike(page, key) == [c + step], key
    # the row above: the major on the note under it, column by column
    assert _strike(page, "KeyQ") == _major(c)  # C
    assert _strike(page, "KeyW") == _major(c + 2)  # D
    assert _strike(page, "KeyE") == _major(c + 4)  # E
    assert _strike(page, "KeyU") == _major(c + 11)  # B
    assert _strike(page, "KeyI") == _major(c + 12)  # the C above
    assert _strike(page, "BracketRight") == _major(c + 19)  # G, the column past '
    # the row below: the minor
    assert _strike(page, "KeyZ") == _minor(c)  # Cm
    assert _strike(page, "KeyX") == _minor(c + 2)  # Dm
    assert _strike(page, "KeyN") == _minor(c + 9)  # Am
    assert _strike(page, "Slash") == _minor(c + 16)  # Em, an octave up
    # the number row's default: the dominant 7th
    assert _strike(page, "Digit1") == [c, c + 4, c + 7, c + 10]  # C7
    assert _strike(page, "Digit5") == [c + 7, c + 11, c + 14, c + 17]  # G7
    # held: its name beside the keys, its keys lit
    page.keyboard.down("KeyX")
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.text_content("#kbd-now") == "Dm"
    assert page.evaluate(LIT) == _minor(c + 2)
    page.keyboard.up("KeyX")
    _silent(page)
    # what Play says never turns the home row into chords here
    page.click('[data-plays="triad"]')
    assert _strike(page, "KeyA") == [c]
    assert _strike(page, "KeyQ") == _major(c)
    _silent(page)


def test_the_number_row_is_a_choice_and_alt_n_steps_it(studio):
    page = _open(studio)
    c = _base(page)
    assert page.inner_text("#kbd-colnum-face") == "7"
    _num(page, "maj7")
    assert _strike(page, "Digit1") == [c, c + 4, c + 7, c + 11]  # Cmaj7
    assert page.inner_text("#kbd-colnum-face") == "maj7"
    _num(page, "m7")
    assert _strike(page, "Digit2") == [c + 2, c + 5, c + 9, c + 12]  # Dm7
    _num(page, "sharp")  # the black keys: the column's note a semitone up
    assert _strike(page, "Digit1") == [c + 1]  # C#
    assert _strike(page, "Digit3") == [c + 5]  # E# is F
    assert _strike(page, "Digit7") == [c + 12]  # B# the C above
    assert _strike(page, "Digit8") == [c + 13]
    assert page.inner_text("#kbd-colnum-face") == "♯"
    # Alt+N: the next choice, a toast saying what it plays
    page.keyboard.press("Alt+KeyN")
    page.wait_for_selector(".toast:has-text('octave')")
    assert page.evaluate("() => localStorage.getItem('synthlab.colnum')") == "octave"
    assert page.inner_text("#kbd-colnum-face") == "oct"
    octave = "() => window.synthlab.piano.octave()"
    page.keyboard.press("Digit2")
    assert page.evaluate(octave) == 2
    assert page.inner_text("#oct-label").endswith("C2")
    assert _strike(page, "KeyA") == [12 * 3]
    page.keyboard.press("Digit3")
    assert page.evaluate(octave) == 3
    page.evaluate("() => { window.__notes = []; }")
    page.keyboard.press("Digit9")  # 8 9 0 play nothing in this choice
    assert page.evaluate(octave) == 3
    assert page.evaluate("() => window.__notes") == []
    page.keyboard.press("Alt+KeyN")  # round to the 7th again
    page.wait_for_selector(".toast:has-text('7th chord')")
    assert page.get_attribute('#kbd-colnum [data-colnum="7"]', "aria-pressed") == "true"
    assert _strike(page, "Digit1") == [48, 52, 55, 58]  # C7 at C3
    # kept per browser
    _num(page, "sus4")
    page.reload()
    page.wait_for_selector("#kbd-layout", state="attached")
    studio.settle()
    page.locator(".view-head h1").click()
    page.evaluate(HOOK)
    c = _base(page)
    assert (
        page.get_attribute('#kbd-colnum [data-colnum="sus4"]', "aria-pressed") == "true"
    )
    assert _strike(page, "Digit4") == [c + 5, c + 10, c + 12]  # Fsus4
    _silent(page)


LABEL = """(sel) => getComputedStyle(document.querySelector(sel), '::after').content"""


def test_minus_equal_step_the_octave_and_the_labels_follow(studio):
    page = _open(studio)
    octave = "() => window.synthlab.piano.octave()"
    start = page.evaluate(octave)
    page.keyboard.press("Equal")
    assert page.evaluate(octave) == start + 1
    assert _strike(page, "KeyA") == [_base(page)]
    assert _strike(page, "KeyQ") == _major(_base(page))
    page.keyboard.press("Minus")
    page.keyboard.press("Minus")
    assert page.evaluate(octave) == start - 1
    assert _strike(page, "KeyZ") == _minor(_base(page))
    # the keys say which home-row key plays them, in the rows' labels' place
    a = '.pkey[data-col="A"]'
    assert page.get_attribute(a, "aria-label") == f"C{start - 1}"
    assert page.evaluate(LABEL, '.pkey[data-rel="0"][data-note="0"]') == '"A"'
    assert page.evaluate(LABEL, '.pkey[data-rel="0"][data-note="11"]') == '"J"'
    assert page.evaluate(LABEL, '.pkey[data-rel="1"][data-note="0"]') == '"K"'
    assert page.evaluate(LABEL, '.pkey[data-rel="1"][data-note="5"]') == '"\'"'
    hint = "() => getComputedStyle(document.querySelector('.pkey .qhint')).display"
    assert page.evaluate(hint) == "none"
    page.keyboard.press("Equal")  # the labels move with the octave: A is still its C
    assert page.get_attribute(a, "aria-label") == f"C{start}"
    page.keyboard.down("KeyA")
    assert page.evaluate(LIT) == [_base(page)]
    lit = page.get_attribute("#piano .pkey.down", "data-col")
    page.keyboard.up("KeyA")
    assert lit == "A"
    # a black key names its digit only while the number row plays the sharps
    black = '.pkey[data-rel="0"][data-note="1"]'
    assert page.evaluate(LABEL, black) == "none"
    _num(page, "sharp")
    assert page.evaluate(LABEL, black) == '"1"'
    assert page.evaluate(LABEL, '.pkey[data-rel="1"][data-note="3"]') == '"9"'
    _silent(page)


def test_no_key_up_order_or_layout_switch_leaves_a_note_sounding(studio):
    page = _open(studio)
    kb = page.keyboard
    live = "() => window.__live.size"
    # three held together: a major, a minor and a note, let go out of order
    kb.down("KeyQ")
    kb.down("KeyZ")
    kb.down("KeyA")
    assert page.evaluate(live) == 7
    kb.up("KeyZ")
    assert page.evaluate(live) == 4
    kb.up("KeyA")
    kb.up("KeyQ")
    _silent(page)
    # the octave moved under a held chord: its own notes still end
    kb.down("KeyW")
    kb.press("Equal")
    kb.up("KeyW")
    _silent(page)
    kb.press("Minus")
    # a switch to rows mid-hold: the key's keyup still ends its chord
    kb.down("KeyE")
    kb.press("Alt+KeyL")
    page.wait_for_selector(".toast:has-text('Typing: rows')")
    kb.up("KeyE")
    _silent(page)
    # ... and from rows into columns: rows' own key ends as rows plays it
    kb.down("KeyS")  # rows: the classic split's D, piano.js's own
    page.wait_for_selector("#piano .pkey.down")
    kb.press("Alt+KeyL")
    kb.press("Alt+KeyL")  # letters, then columns
    page.wait_for_selector(".toast:has-text('Typing: columns')")
    kb.up("KeyS")
    _silent(page)
    # from letters: a chord made with Left Shift, switched away mid-hold
    kb.press("Alt+KeyL")
    kb.press("Alt+KeyL")  # rows, then letters
    page.wait_for_selector(".toast:has-text('Typing: letters')")
    kb.down("ShiftLeft")
    kb.down("KeyC")
    kb.up("ShiftLeft")
    kb.press("Alt+KeyL")  # columns
    page.wait_for_selector(".toast:has-text('Typing: columns')")
    kb.up("KeyC")
    _silent(page)
    # a lost focus lets everything go
    kb.down("KeyQ")
    kb.down("Digit1")
    page.evaluate("() => window.dispatchEvent(new Event('blur'))")
    _silent(page)
    kb.up("Digit1")
    kb.up("KeyQ")
    assert _strike(page, "KeyA") == [_base(page)]  # and plays on after it
    _silent(page)


def test_space_shift_and_the_browsers_own_keys(studio):
    studio.page.add_init_script(PREVENTED)
    page = _open(studio)
    page.keyboard.press("Space")  # the page's ▶
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20_000)
    page.keyboard.press("Space")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    # Left Shift is the sustain pedal here, and a key struck under it plays
    page.keyboard.down("ShiftLeft")
    assert page.evaluate("() => window.synthlab.pedal()") is True
    page.keyboard.down("KeyQ")
    page.keyboard.up("KeyQ")
    page.wait_for_timeout(50)
    assert (
        page.evaluate("() => document.querySelectorAll('#piano .pkey.held').length")
        == 3
    )
    page.keyboard.up("ShiftLeft")
    assert page.evaluate("() => window.synthlab.pedal()") is False
    # Right Shift latches
    page.keyboard.press("ShiftRight")
    assert page.evaluate("() => window.synthlab.latch()") == {"armed": True, "notes": 0}
    _strike(page, "KeyA")
    page.wait_for_function("() => window.synthlab.latch().notes === 1")
    page.keyboard.press("ShiftRight")
    page.wait_for_function("() => window.synthlab.latch().notes === 0")
    _silent(page)
    # Ctrl+A / Ctrl+C are the browser's: nothing plays, nothing is prevented
    before = page.evaluate(FOCUS)
    n = page.evaluate("() => window.__notes.length")
    page.keyboard.press("Control+a")
    page.keyboard.press("Control+c")
    page.wait_for_function("() => 'Ctrl+KeyC' in window.__prevented")
    got = page.evaluate("() => window.__prevented")
    assert got["Ctrl+KeyA"] is False and got["Ctrl+KeyC"] is False, got
    assert page.evaluate("() => window.getSelection().toString()") != ""  # all of it
    assert page.evaluate("() => window.__notes.length") == n
    assert page.evaluate(FOCUS) == before
    # typing off: nothing
    page.evaluate("() => window.getSelection().removeAllRanges()")
    page.click("#keys-toggle")
    assert _strike(page, "KeyA") == []
    assert _strike(page, "KeyQ") == []
    page.click("#keys-toggle")
    _silent(page)


def test_the_strip_says_it_and_the_group_keeps_its_width(studio):
    page = _open(studio, "rows")
    group = page.evaluate(QWERTY)
    page.keyboard.press("Alt+KeyL")
    page.wait_for_selector(".toast:has-text('Typing: letters')")
    assert page.evaluate(QWERTY) == group
    page.keyboard.press("Alt+KeyL")
    page.wait_for_selector(".toast:has-text('Typing: columns')")
    assert page.evaluate(QWERTY) == group
    assert page.evaluate("() => localStorage.getItem('synthlab.layout')") == "columns"
    assert page.locator("#kbd-columns-key").is_visible()
    assert not page.locator("#kr-strip").is_visible()
    assert not page.locator("#kbd-letters-key").is_visible()
    # "A C · Q maj · Z min · 1 7": A is C, the number row's choice last
    chips = page.eval_on_selector_all(
        "#kbd-columns-key > span", "(s) => s.map((c) => c.textContent)"
    )
    assert chips == ["AC", "Qmaj", "Zmin", "17"]
    # ... in a room its longest choice fits, so a choice never moves the toolbar
    pick = "(v) => document.querySelector(`#kbd-colnum [data-colnum='${v}']`).click()"
    for choice in ("maj7", "sus4", "octave"):
        page.evaluate(pick, choice)
        assert page.evaluate(QWERTY) == group, choice
    page.evaluate(pick, "7")
    assert page.get_attribute("#kbd-columns-key", "data-keys") == "A–'"
    assert page.get_attribute("#kbd-colnum", "data-keys") == "Alt+N"
    # the panel: what only rows reads dimmed, the number row lit
    page.click(".kbd-rows-btn")
    opacity = "(s) => getComputedStyle(document.querySelector(s)).opacity"
    assert page.evaluate(opacity, ".kr-grid") == "0.45"
    assert page.evaluate(opacity, ".kr-colnum") == "1"
    page.keyboard.press("Escape")
    page.keyboard.press("Alt+KeyL")  # rows: the columns' number row dimmed there
    page.wait_for_selector(".toast:has-text('Typing: rows')")
    page.click(".kbd-rows-btn")
    assert page.evaluate(opacity, ".kr-colnum") == "0.45"
    assert page.evaluate(opacity, ".kr-grid") == "1"
    page.keyboard.press("Escape")
    # Alt+N belongs to columns alone
    page.keyboard.press("Alt+KeyN")
    page.wait_for_timeout(100)
    assert page.get_attribute('#kbd-colnum [data-colnum="7"]', "aria-pressed") == "true"


NOW = """() => { const o = document.getElementById('kbd-now');
  return [o.textContent, o.dataset.state]; }"""


def test_a_note_names_itself_beside_the_keys_and_on_its_key(studio):
    # in columns mode a played note names itself in the box beside the keys
    # and on its key, for the A-' row's single notes as well as the chords
    page = _open(studio)
    c, octave = _base(page), page.evaluate("() => window.synthlab.piano.octave()")
    assert page.evaluate(NOW) == ["note", "rest"]
    # the home row's single note: lit, named beside the keys and on its key
    page.keyboard.down("KeyD")  # E in the dock's octave
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.evaluate(LIT) == [c + 4]
    assert page.evaluate(NOW) == [f"E{octave}", "held"]
    e = '.pkey[data-rel="0"][data-note="4"]'
    assert page.evaluate(LABEL, e) == f'"E{octave}"'  # its letter, D, while it is up
    hint = "(s) => getComputedStyle(document.querySelector(s + ' .qhint')).display"
    assert page.evaluate(hint, e) == "none"
    page.keyboard.up("KeyD")
    _silent(page)
    assert page.evaluate(NOW) == ["note", "rest"]
    assert page.evaluate(LABEL, e) == '"D"'
    # a chord: its name beside the keys, each lit key its own note
    page.keyboard.down("KeyW")  # D major
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.text_content("#kbd-now") == "D"
    names = page.eval_on_selector_all(
        "#piano .pkey.down",
        "(ks) => ks.map((k) => getComputedStyle(k, '::after').content)",
    )
    assert names == [
        f'"D{octave}"',
        f'"A{octave}"',
        f'"F#{octave}"',
    ]  # whites, then the black
    page.keyboard.up("KeyW")
    _silent(page)
    # two notes held: the last one struck is named; the first again when it is alone
    page.keyboard.down("KeyA")
    page.keyboard.down("KeyG")
    page.wait_for_function(
        f"() => document.getElementById('kbd-now').textContent === 'G{octave}'"
    )
    page.keyboard.up("KeyG")
    page.wait_for_function(
        f"() => document.getElementById('kbd-now').textContent === 'C{octave}'"
    )
    page.keyboard.up("KeyA")
    _silent(page)
    # a note no drawn key holds (above the keys): named beside them all the same
    page.evaluate("() => window.synthlab.piano.noteOn('midi:120', 120, 1)")
    page.wait_for_function(
        "() => document.getElementById('kbd-now').textContent === 'C9'"
    )
    page.evaluate("() => window.synthlab.piano.noteOff('midi:120')")
    page.wait_for_function(
        "() => document.getElementById('kbd-now').dataset.state === 'rest'"
    )
    # and in the rows layout, the classic split's A: C, named the same way
    page.keyboard.press("Alt+KeyL")  # columns -> rows
    page.wait_for_selector(".toast:has-text('Typing: rows')")
    page.keyboard.down("KeyA")
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.evaluate(NOW) == [f"C{octave}", "held"]
    assert page.evaluate(LABEL, '.pkey[data-rel="0"][data-note="0"]') == f'"C{octave}"'
    page.keyboard.up("KeyA")
    _silent(page)
