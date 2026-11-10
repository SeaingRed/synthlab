"""The keyboard dock's letters layout: the letter pressed is the note (A
plays an A), and holding Space, Shift or Ctrl changes the chord's quality.

What these journeys prove, in Chromium: the typing layout — rows |
letters, in the typing rows' panel, or Alt+L from anywhere — is kept per
browser, and the strip's button shows the letters' modifiers in the rows'
place without the QWERTY group changing width; in letters A-G play that note in the
dock's octave (asserted against `synthlab.piano.octave()`), and what is
held as the letter goes down builds the chord on it — Left Shift major,
Space minor, Ctrl 7, Shift+Ctrl maj7, Space+Ctrl m7, Shift+Space sus4,
Tab sharp — named beside the keys while held; 1-7 set the octave; Ctrl+A
selects nothing (its default prevented), Tab keeps the focus; Space
tapped alone still plays the page's ▶, held with a letter it does not;
Left Shift is not the pedal there, Right Shift still latches; no key-up
order leaves a note sounding, nor does a lost focus; typing off plays
nothing; rows is as it was. Notes are read off `synthlab.piano.noteOn` —
nobody has listened.
"""

from __future__ import annotations

HOOK = """() => { const p = window.synthlab.piano, on = p.noteOn, off = p.noteOff;
  window.__notes = []; window.__live = new Set();
  p.noteOn = (id, m, v) => {
    window.__notes.push(m); window.__live.add(id); return on(id, m, v); };
  p.noteOff = (id) => { window.__live.delete(id); return off(id); }; }"""
QWERTY = """() => {
  const r = document.querySelector('.kbd-type').getBoundingClientRect();
  return [Math.round(r.x), Math.round(r.width)]; }"""
FOCUS = """() => { const el = document.activeElement;
  return `${el?.tagName}#${el?.id}.${el?.className}`; }"""
LIT = """() => [...document.querySelectorAll('#piano .pkey.down')]
  .map((k) => window.synthlab.piano.midiOf(k)).sort((a, b) => a - b)"""


def _letters(studio):
    page = studio.open("editor")
    studio.settle()
    page.click(".kbd-rows-btn")  # the rows' panel: its layout
    page.click('#kbd-layout [data-layout="letters"]')
    page.keyboard.press("Escape")
    page.wait_for_selector("#kbd-rows:not(:popover-open)", state="attached")
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.evaluate(HOOK)
    return page


def _base(page) -> int:
    """MIDI of C in the dock's own octave."""
    return 12 * (page.evaluate("() => window.synthlab.piano.octave()") + 1)


def _strike(page, key: str, *mods: str) -> list[int]:
    """Hold the modifiers, press the letter: the notes it started."""
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


def test_a_letter_plays_its_note_in_the_docks_octave(studio):
    page = _letters(studio)
    assert page.get_attribute('[data-layout="letters"]', "aria-pressed") == "true"
    assert page.evaluate("() => document.documentElement.dataset.keylayout") == (
        "letters"
    )
    c = _base(page)
    assert _strike(page, "KeyA") == [c + 9]  # A4 in the C4 octave: 69
    assert _strike(page, "KeyC") == [c]
    assert _strike(page, "KeyB") == [c + 11]
    assert _strike(page, "KeyE") == [c + 4]
    # the rest of the rows play nothing here (and the number row's 8-0 none)
    for key in ("KeyH", "KeyW", "KeyZ", "KeyQ", "Semicolon", "Digit9"):
        assert _strike(page, key) == [], key
    page.click('.oct-btn[data-dir="1"]')
    assert _strike(page, "KeyA") == [_base(page) + 9]
    _silent(page)


def test_the_modifiers_make_the_chord_on_the_letter(studio):
    page = _letters(studio)
    c = _base(page)
    assert _strike(page, "KeyC", "ShiftLeft") == [c, c + 4, c + 7]  # C
    assert _strike(page, "KeyD", "Space") == [c + 2, c + 5, c + 9]  # Dm
    g = c + 7
    assert _strike(page, "KeyG", "ControlLeft") == [g, g + 4, g + 7, g + 10]  # G7
    a = c + 9
    assert _strike(page, "KeyA", "Space", "ControlLeft") == [a, a + 3, a + 7, a + 10]
    maj7 = [c, c + 4, c + 7, c + 11]
    assert _strike(page, "KeyC", "ShiftLeft", "ControlLeft") == maj7
    assert _strike(page, "KeyD", "ShiftLeft", "Space") == [c + 2, c + 7, c + 9]  # Dsus4
    assert _strike(page, "KeyF", "Tab") == [c + 6]  # F#
    assert _strike(page, "KeyE", "Tab") == [c + 5]  # E# is F
    assert _strike(page, "KeyB", "Tab") == [c + 12]  # B# the C above
    assert _strike(page, "KeyF", "Tab", "Space") == [c + 6, c + 9, c + 13]  # F#m
    # held: its name beside the keys, its keys lit
    page.keyboard.down("Space")
    page.keyboard.down("ControlLeft")
    page.keyboard.down("KeyD")
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.text_content("#kbd-now") == "Dm7"
    assert page.evaluate(LIT) == [c + 2, c + 5, c + 9, c + 12]
    page.keyboard.up("KeyD")
    page.keyboard.up("ControlLeft")
    page.keyboard.up("Space")
    _silent(page)
    # none held: what Play says — a chord in key on the note
    page.click('[data-plays="triad"]')
    want = page.evaluate(f"() => window.synthlab.plays.chordOf({c + 9}).notes")
    assert _strike(page, "KeyA") == sorted(want)
    _silent(page)


# before the page's scripts, in capture, and read once the key's dispatch
# is over: whether anything prevented the browser's own action
PREVENTED = """window.__prevented = {};
window.addEventListener('keydown', (e) => setTimeout(() => {
  const k = (e.ctrlKey ? 'Ctrl+' : '') + e.code;
  window.__prevented[k] = e.defaultPrevented; }), true);"""


def test_ctrl_letters_and_tab_never_reach_the_browser(studio):
    studio.page.add_init_script(PREVENTED)
    page = _letters(studio)
    for key in "ABCDEFG":
        page.keyboard.down("ControlLeft")
        page.keyboard.down(f"Key{key}")
        page.keyboard.up(f"Key{key}")
        page.keyboard.up("ControlLeft")
    # (Chromium runs the timers after the input it is handed: wait for the last)
    page.wait_for_function("() => 'Ctrl+KeyG' in window.__prevented")
    got = page.evaluate("() => window.__prevented")
    assert all(got[f"Ctrl+Key{k}"] for k in "ABCDEFG"), got
    assert page.evaluate("() => window.getSelection().toString()") == ""
    # Tab is the sharp: the focus stays where it was
    before = page.evaluate(FOCUS)
    page.keyboard.press("Tab")
    page.wait_for_function("() => 'Tab' in window.__prevented")
    assert page.evaluate("() => window.__prevented.Tab") is True
    assert page.evaluate(FOCUS) == before
    _silent(page)
    # ... and in a field Ctrl+A is the field's own select-all
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    page.keyboard.type("abc")
    page.evaluate("() => { window.__prevented = {}; }")
    n = page.evaluate("() => window.__notes.length")
    page.keyboard.press("Control+a")
    page.wait_for_function("() => 'Ctrl+KeyA' in window.__prevented")
    assert page.evaluate("() => window.__prevented['Ctrl+KeyA']") is False
    assert page.evaluate("() => window.__notes.length") == n  # nothing played
    page.keyboard.press("Escape")


def test_a_space_tap_plays_and_a_space_held_with_a_letter_does_not(studio):
    page = _letters(studio)
    page.keyboard.press("Space")  # a tap: the page's ▶, on its keyup
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20_000)
    page.keyboard.press("Space")  # and again: stopped
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    page.keyboard.down("Space")
    page.keyboard.down("KeyD")
    page.keyboard.up("KeyD")
    page.keyboard.up("Space")
    page.wait_for_timeout(400)
    assert not page.evaluate("() => window.synthlab.isPlaying()")
    _silent(page)


def test_number_keys_set_the_octave_and_minus_equal_still_step(studio):
    page = _letters(studio)
    octave = "() => window.synthlab.piano.octave()"
    page.keyboard.press("Digit2")
    assert page.evaluate(octave) == 2
    assert page.inner_text("#oct-label").endswith("C2")
    assert _strike(page, "KeyA") == [12 * 3 + 9]
    page.keyboard.press("Digit3")
    assert page.evaluate(octave) == 3
    page.keyboard.press("Equal")
    assert page.evaluate(octave) == 4
    page.keyboard.press("Minus")
    assert page.evaluate(octave) == 3
    page.keyboard.press("Digit7")  # as high as + would go with these keys showing
    top = page.evaluate(octave)
    page.click('.oct-btn[data-dir="1"]')
    assert page.evaluate(octave) == top
    page.keyboard.press("Digit1")
    assert page.evaluate(octave) == 1
    # the A alone: the digits played nothing
    assert page.evaluate("() => window.__notes.length") == 1


def test_no_key_up_order_leaves_a_note_sounding(studio):
    page = _letters(studio)
    kb = page.keyboard
    # the modifier up first, then the letter
    kb.down("ShiftLeft")
    kb.down("KeyC")
    kb.up("ShiftLeft")
    page.wait_for_timeout(50)
    assert page.evaluate("() => window.__live.size") == 3  # still held by its letter
    kb.up("KeyC")
    _silent(page)
    # the letter up first
    kb.down("Space")
    kb.down("ControlLeft")
    kb.down("KeyA")
    kb.up("KeyA")
    kb.up("ControlLeft")
    kb.up("Space")
    _silent(page)
    # two letters overlapping, a modifier let go between them
    kb.down("ShiftLeft")
    kb.down("KeyA")
    kb.up("ShiftLeft")
    kb.down("KeyE")
    assert page.evaluate("() => window.__live.size") == 4  # A major + E
    kb.up("KeyA")
    assert page.evaluate("() => window.__live.size") == 1
    kb.up("KeyE")
    _silent(page)
    # Tab let go before the letter
    kb.down("Tab")
    kb.down("KeyG")
    kb.up("Tab")
    kb.up("KeyG")
    _silent(page)
    # a lost focus lets everything go
    kb.down("Space")
    kb.down("KeyF")
    page.evaluate("() => window.dispatchEvent(new Event('blur'))")
    _silent(page)
    kb.up("KeyF")
    kb.up("Space")
    page.wait_for_timeout(300)
    assert not page.evaluate("() => window.synthlab.isPlaying()")  # no tap after a blur


def test_left_shift_is_major_not_the_pedal_and_right_shift_still_latches(studio):
    page = _letters(studio)
    page.keyboard.down("ShiftLeft")
    assert page.evaluate("() => window.synthlab.pedal()") is False
    page.keyboard.up("ShiftLeft")
    page.keyboard.press("ShiftRight")
    assert page.evaluate("() => window.synthlab.latch()") == {"armed": True, "notes": 0}
    _strike(page, "KeyA")
    page.wait_for_function("() => window.synthlab.latch().notes === 1")
    page.keyboard.press("ShiftRight")  # let go
    page.wait_for_function("() => window.synthlab.latch().notes === 0")
    _silent(page)


def test_typing_off_or_a_field_plays_nothing(studio):
    page = _letters(studio)
    page.click("#keys-toggle")
    assert _strike(page, "KeyA") == []
    assert _strike(page, "KeyC", "ShiftLeft") == []
    page.click("#keys-toggle")
    assert _strike(page, "KeyA") != []
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    assert _strike(page, "KeyA") == []
    assert _strike(page, "KeyD", "Space") == []
    page.keyboard.press("Escape")
    _silent(page)


def test_the_layout_is_kept_shows_on_the_keys_and_rows_is_as_it_was(studio):
    page = _letters(studio)
    group = page.evaluate(QWERTY)
    # the letters' face in the strip's place: the group keeps its width
    assert page.locator("#kbd-letters-key").is_visible()
    assert not page.locator("#kr-strip").is_visible()
    assert "maj" in page.inner_text("#kbd-letters-key")
    # in the panel, what only rows reads is dimmed; the layout is lit
    page.click(".kbd-rows-btn")
    assert page.get_attribute('[data-layout="letters"]', "aria-pressed") == "true"
    assert (
        page.evaluate(
            "() => getComputedStyle(document.querySelector('.kr-grid')).opacity"
        )
        == "0.45"
    )
    page.keyboard.press("Escape")
    # the dock's octave named by letter, the rows' labels hidden
    letter = page.evaluate(
        """() => {
        const k = document.querySelector('.pkey[data-rel="0"][data-note="9"]');
        return [getComputedStyle(k, '::after').content,
                getComputedStyle(k.querySelector('.qhint')).display]; }"""
    )
    assert letter == ['"A"', "none"]
    sharp = page.evaluate(
        """() => getComputedStyle(document.querySelector(
          '.pkey[data-rel="0"][data-note="6"]'), '::after').content"""
    )
    assert sharp == '"⇥F"'
    # the shortcut sheet lists the letters' keys
    assert page.get_attribute("#kbd-letters-key", "data-keys") == "A–G"
    assert page.get_attribute("#kbd-layout", "data-keys") == "Alt+L"
    page.reload()
    page.wait_for_selector("#kbd-layout", state="attached")
    studio.settle()
    assert page.get_attribute('[data-layout="letters"]', "aria-pressed") == "true"
    page.locator(".view-head h1").click()
    page.evaluate(HOOK)
    assert _strike(page, "KeyA") == [_base(page) + 9]
    # rows: as it always was — A is the classic split's C, Shift the pedal
    # from anywhere: on through columns (the third) back to
    # rows, and a toast says which
    page.keyboard.press("Alt+KeyL")
    page.wait_for_selector(".toast:has-text('Typing: columns')")
    page.keyboard.press("Alt+KeyL")
    page.wait_for_selector(".toast:has-text('Typing: rows')")
    assert page.get_attribute('[data-layout="rows"]', "aria-pressed") == "true"
    assert page.evaluate(QWERTY) == group
    assert page.locator("#kr-strip").is_visible()
    assert not page.locator("#kbd-letters-key").is_visible()
    assert page.evaluate("() => localStorage.getItem('synthlab.layout')") == "rows"
    # (piano.js plays the classic split itself, not through the bridge)
    page.keyboard.down("KeyA")
    lit = page.evaluate(LIT)
    page.keyboard.up("KeyA")
    assert lit == [_base(page)]
    page.keyboard.down("ShiftLeft")
    assert page.evaluate("() => window.synthlab.pedal()") is True
    page.keyboard.up("ShiftLeft")
    page.keyboard.press("Space")  # rows: Space plays on its keydown, as before
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20_000)
    page.keyboard.press("Shift+Space")
    page.wait_for_function("() => !window.synthlab.isPlaying()")
    _silent(page)


def test_a_letter_names_its_note_beside_the_keys_and_on_its_key(studio):
    # a note sounding is named beside the keys ("D4", not
    # Play's "note") and on its key in place of its letter, as a chord is
    page = _letters(studio)
    octave = page.evaluate("() => window.synthlab.piano.octave()")
    d = '.pkey[data-rel="0"][data-note="2"]'
    label = f"() => getComputedStyle(document.querySelector('{d}'), '::after').content"
    assert page.evaluate(label) == '"D"'
    page.keyboard.down("KeyD")
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.text_content("#kbd-now") == f"D{octave}"
    assert page.evaluate(label) == f'"D{octave}"'
    page.keyboard.up("KeyD")
    _silent(page)
    assert page.evaluate(label) == '"D"'
    assert page.text_content("#kbd-now") == "note"
