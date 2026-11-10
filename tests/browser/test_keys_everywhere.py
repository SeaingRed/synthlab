"""The keyboard dock on every page: the key layout and sound set on Synths
carry through to every other page that plays synths.

What these journeys prove, in Chromium: the dock is the page frame's
(base.html, after #view): a view swap keeps the very same node, so the
layout, Play's choice and the octave set on Synths are the ones Drums,
Perform, Song, Mix, the Library and the Lab play — the columns layout's
chords reach `synthlab.piano.noteOn` there, and the live voice sounds them
(the CPU read-out's voice count); a note held while the page changes keeps
sounding until its keyup, then stops; a page's own keys stay its own (L
loops on Song and Mix, a focused step grid keeps its digits); Perform's
typing plays the keys by default, and typing → pads (a click, or Alt+P)
gives Q W E R / A S D F back to the pads; a window-high view ends above
the dock (--keys-h, measured) and nothing scrolls; the fold is kept per
page, and folded the bar says what the keys play. Notes are read off
`synthlab.piano.noteOn`, the voice off its own meter — nobody has listened.
"""

from __future__ import annotations

from .test_letters import HOOK, LIT

PAGES = ("drums", "perform", "song", "mixer", "library", "lab")
OCT = "() => window.synthlab.piano.octave()"
KEYS_H = """() => parseFloat(getComputedStyle(document.documentElement)
  .getPropertyValue('--keys-h'))"""
SILENT = (
    "() => window.__live.size === 0 && !document.querySelector('#piano .pkey.down')"
)


def _strike(page, key: str) -> list[int]:
    page.evaluate("() => { window.__notes = []; }")
    page.keyboard.down(key)
    notes = sorted(page.evaluate("() => window.__notes"))
    page.keyboard.up(key)
    page.wait_for_function(SILENT)
    return notes


def _columns(studio, view: str):
    """A page in the columns layout (every key it plays reaches the hook:
    the rows layout's classic split plays inside piano.js)."""
    page = studio.open(view)
    page.evaluate("() => localStorage.setItem('synthlab.layout', 'columns')")
    page.reload()
    page.wait_for_function(
        "() => document.documentElement.dataset.keylayout === 'columns'"
    )
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.evaluate(HOOK)
    return page


def _columns_triad_up(studio):
    """On Synths: the columns layout, Play = triad, the octave one up."""
    page = studio.open("editor")
    studio.settle()
    page.click(".kbd-rows-btn")
    page.click('#kbd-layout [data-layout="columns"]')
    page.keyboard.press("Escape")
    page.wait_for_selector("#kbd-rows:not(:popover-open)", state="attached")
    page.click('[data-plays="triad"]')
    page.click('.oct-btn[data-dir="1"]')
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.evaluate(HOOK)
    page.evaluate("() => { window.__dock = document.getElementById('keys-dock'); }")
    return page


def test_the_dock_carries_its_layout_play_and_octave_to_every_page(studio):
    page = _columns_triad_up(studio)
    octave = page.evaluate(OCT)
    studio.start_voice()
    for view in PAGES:
        studio.nav(view)
        studio.settle()
        # the very same dock: never redrawn by the swap
        assert page.evaluate(
            "() => document.getElementById('keys-dock') === window.__dock"
            " && !document.querySelector('#view #keys-dock')"
        ), view
        assert page.locator("#keys-dock .pkey").first.is_visible(), view
        assert page.evaluate("() => document.documentElement.dataset.keylayout") == (
            "columns"
        ), view
        assert page.get_attribute('#kbd-plays [aria-pressed="true"]', "data-plays") == (
            "triad"
        ), view
        assert page.evaluate(OCT) == octave, view
        assert page.inner_text("#oct-label").endswith(f"C{octave}"), view
        c = 12 * (octave + 1)
        # columns: Q is the major on the dock's C, Z the minor
        assert _strike(page, "KeyQ") == [c, c + 4, c + 7], view
        assert _strike(page, "KeyZ") == [c, c + 3, c + 7], view
        # Play = triad: an on-screen key plays the chord in key on it
        want = sorted(page.evaluate(f"() => window.synthlab.plays.chordOf({c}).notes"))
        page.evaluate("() => { window.__notes = []; }")
        studio.pointer_down('.pkey[data-rel="0"][data-note="0"]')
        got = sorted(page.evaluate("() => window.__notes"))
        studio.pointer_up()
        page.wait_for_function(SILENT)
        assert got == want and len(got) == 3, view
    # the live voice sounds them, on a page that is not Synths (the Library)
    page.keyboard.down("KeyQ")
    studio.wait_meter(r"\b3 voices")
    page.keyboard.up("KeyQ")
    studio.wait_meter(r"\b0 voices")


def test_a_note_held_across_a_page_change_sounds_until_its_keyup(studio):
    page = _columns(studio, "editor")
    studio.start_voice()
    page.keyboard.down("KeyA")  # the dock's C
    page.wait_for_function("() => window.__live.size === 1")
    studio.wait_meter(r"\b1 voice\b")
    studio.nav("drums")
    studio.settle()
    page.wait_for_timeout(300)
    assert page.evaluate("() => window.__live.size") == 1
    assert page.evaluate(LIT) == [12 * (page.evaluate(OCT) + 1)]
    studio.wait_meter(r"\b1 voice\b")
    studio.nav("song")
    studio.settle()
    assert page.evaluate("() => window.__live.size") == 1
    page.keyboard.up("KeyA")
    page.wait_for_function(SILENT)
    studio.wait_meter(r"\b0 voices")


def test_a_pages_own_keys_stay_its_own(studio):
    page = _columns(studio, "editor")
    assert _strike(page, "KeyL") == [
        12 * (page.evaluate(OCT) + 2) + 2
    ]  # D, an octave up
    # Song and Mix: L loops the section, so it plays no note there
    for view in ("song", "mixer"):
        studio.nav(view)
        studio.settle()
        assert page.get_attribute("#view", "data-own-keys") == "KeyL"
        assert _strike(page, "KeyL") == [], view
        assert _strike(page, "KeyA") != [], view
    # a step grid that has the focus keeps its keys (1-4: a hit's ratchet)
    studio.nav("drums")
    studio.settle()
    page.locator("#view .grid .cell").first.focus()
    assert _strike(page, "Digit1") == []
    assert _strike(page, "KeyA") == []
    page.locator(".view-head h1").click()
    assert _strike(page, "KeyA") != []


def test_perform_typing_plays_the_keys_or_the_pads(studio):
    page = _columns(studio, "perform")
    seg = page.locator("#kbd-typeto")
    assert seg.is_visible()
    assert (
        page.get_attribute('#kbd-typeto [data-typeto="keys"]', "aria-pressed") == "true"
    )
    hits: list[str] = []
    page.on("request", lambda r: hits.append(r.url) if "/hit.wav" in r.url else None)
    # keys (the default): A is the dock's C, no pad
    assert _strike(page, "KeyA") == [12 * (page.evaluate(OCT) + 1)]
    page.wait_for_timeout(200)
    assert not hits
    # pads: one click (or Alt+P) — A strikes the clap pad, no note
    page.click('#kbd-typeto [data-typeto="pads"]')
    assert page.evaluate("() => localStorage.getItem('synthlab.typeto')") == "pads"
    with page.expect_request("**/hit.wav*"):
        assert _strike(page, "KeyA") == []
    assert _strike(page, "KeyZ") == []  # the dock's typing stands down whole
    page.keyboard.press("Alt+KeyP")
    page.wait_for_selector(".toast:has-text('Typing plays the keys')")
    assert (
        page.get_attribute('#kbd-typeto [data-typeto="keys"]', "aria-pressed") == "true"
    )
    assert _strike(page, "KeyA") != []
    page.keyboard.press("Alt+KeyP")
    page.wait_for_selector(".toast:has-text('Typing plays the pads')")
    # the choice is Perform's alone: elsewhere the switch is not shown, the keys play
    studio.nav("drums")
    studio.settle()
    assert not seg.is_visible()
    assert _strike(page, "KeyA") != []
    page.evaluate("() => localStorage.setItem('synthlab.typeto', 'keys')")


def test_a_window_high_view_ends_above_the_dock(studio):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    for view in ("library", "song", "perform", "mixer", "lab", "clips"):
        studio.open(view)
        studio.settle()
        dock = "document.getElementById('keys-dock')"
        page.wait_for_function(f"() => ({KEYS_H})() === {dock}.offsetHeight")
        box = page.evaluate(
            """() => {
              const v = document.getElementById('view').getBoundingClientRect(),
                d = document.getElementById('keys-dock').getBoundingClientRect();
              return { view: v.bottom, dock: d.top, dockBottom: d.bottom,
                scroll: document.documentElement.scrollHeight, inner: innerHeight };
            }"""
        )
        assert box["view"] <= box["dock"] + 1, (view, box)
        assert box["dockBottom"] <= box["inner"] + 1, (view, box)
        assert box["scroll"] <= box["inner"] + 1, (view, box)
    # Synths: the dock stands where its grid area was — right of the sounds rack
    studio.open("editor")
    studio.settle()
    page.wait_for_timeout(200)
    rack = page.locator("#view > .dock").bounding_box()
    dock = page.locator("#keys-dock").bounding_box()
    slot = page.locator("#keys-slot").bounding_box()
    assert dock["x"] >= rack["x"] + rack["width"] - 1
    assert abs(dock["x"] - slot["x"]) <= 1 and abs(dock["width"] - slot["width"]) <= 1


def test_the_fold_is_kept_per_page_and_says_what_the_keys_play(studio):
    page = studio.open("library")
    studio.settle()
    page.locator(".view-head h1").click()
    page.evaluate(HOOK)
    key = page.locator("#keys-dock .pkey").first
    tall = page.evaluate("() => document.getElementById('keys-dock').offsetHeight")
    assert key.is_visible() and not page.locator("#kbd-sum").is_visible()
    page.click("#keys-dock .keys-fold")
    assert not key.is_visible()
    # folded: a slim bar that still says what the keys play
    page.wait_for_function(f"() => ({KEYS_H})() < {tall} / 2")
    assert page.locator("#kbd-sum").is_visible()
    octave = page.evaluate(OCT)
    assert page.text_content("#kbd-sum") == f"note · rows · C{octave}"
    page.keyboard.down("KeyA")  # typing still plays while it is folded
    page.wait_for_function(
        f"() => document.getElementById('kbd-sum').value.startsWith('C{octave}')"
    )
    page.keyboard.up("KeyA")
    page.wait_for_function(SILENT)
    # per page: Song has its own fold (open), the Library keeps its own
    studio.nav("song")
    studio.settle()
    key.wait_for(state="visible")
    studio.nav("library")
    studio.settle()
    assert not key.is_visible()
    assert (
        page.evaluate("() => localStorage.getItem('synthlab.fold.keys-dock@library')")
        == "1"
    )
    page.click("#keys-dock .keys-fold")
    key.wait_for(state="visible")  # (the fold's entrance fades the keys in)
