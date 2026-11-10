"""Synths' keyboard dock, its toolbar (grouped):
Play · QWERTY · Rec · Talk · Tools, the switches' state lights, ⏺ Rec, and
what ▶ says it plays.

What these journeys prove, in Chromium: what each key plays is one row of
segments in Play — a note or a chord type is ONE click (it was a select:
two, and a second copy of it in What plays), ` switches note / chord and
Alt+` steps the chord type; a chord on it makes a key sound three or more
voices, and the old Note|Chord setting comes back as a triad; chord mode
lights the keys of the chord under the pointer and names it beside the
keys ("Am7", bright while it sounds), note mode the one key; typing and
latch are one click in the toolbar; the typing rows read on the dock
(their strip) and their panel opens beside it, the layer column live only
with rows → sounds; no group moves when a state changes; the deck's Keys
page presses the same segments; ⏺ Rec counts in (or not), shows REC in the
dock and the top dock's read-out, and its take (the keys, with the notes:
test_key_takes.py holds the rest) lands in the Loops strip, whose Edit
puts it on Perform's grid as that loop; the phrase ▶ names the phrase it plays.
Audio is asserted through the page's own objects (the worklet's voice
count, the clip store) — nobody has listened.
"""

from __future__ import annotations

import json

from synthlab.studio import keytakes

C4 = '.pkey[data-rel="0"][data-note="0"]'
A4 = '.pkey[data-rel="0"][data-note="9"]'


def _bench(studio):
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    return page


def _plays(page) -> str:
    """What each key plays: the lit segment of Play (the one control)."""
    return page.get_attribute('#kbd-plays [aria-pressed="true"]', "data-plays")


def _near(page, button: str, pop: str) -> float:
    """How far the popover's nearest edge is from its button, in px."""
    return page.evaluate(
        """([b, p]) => { const a = document.querySelector(b).getBoundingClientRect(),
            o = document.querySelector(p).getBoundingClientRect();
          const dx = Math.max(0, o.left - a.right, a.left - o.right);
          const dy = Math.max(0, o.top - a.bottom, a.top - o.bottom);
          return Math.hypot(dx, dy); }""",
        [button, pop],
    )


_GROUPS = """() => [...document.querySelectorAll('.kbd-bar > .kbd-grp')].map((g) => {
  const r = g.getBoundingClientRect();
  return [g.dataset.menuSection,
          Math.round(r.x), Math.round(r.y), Math.round(r.width)]; })"""


def test_the_toolbar_reads_in_groups_that_never_move(studio):
    """Play · QWERTY · Rec · Talk · Tools, each under its caption and a
    section of its own (its right-click menu); a chord type, latch, typing
    off, the octave: no group moves or changes width."""
    page = _bench(studio)
    before = page.evaluate(_GROUPS)
    assert [g[0] for g in before] == ["Play", "QWERTY", "Rec", "Talk", "Tools"]
    caps = page.locator(".kbd-bar > .kbd-grp > .caps").all_inner_texts()
    assert [c.lower() for c in caps] == ["play", "qwerty", "rec", "talk", "tools"]
    # the live vocal chain's opener stays in Talk, beside the talkbox
    assert page.locator(".kbd-talk .vox-open[data-cmd]").count() == 1
    for sel in (
        '[data-plays="9th"]',
        "#latch-btn",
        "#keys-toggle",
        '.oct-btn[data-dir="1"]',
    ):
        page.click(sel)
        assert page.evaluate(_GROUPS) == before, sel
    page.click("#latch-btn")  # (disarmed again)


def test_typing_rows_read_on_the_dock_and_their_panel_opens_beside_them(studio):
    page = _bench(studio)
    # what each row plays, without opening anything
    strip = page.inner_text("#kr-strip")
    assert "chords" in strip and "piano" in strip
    page.click(".kbd-rows-btn")
    page.wait_for_selector("#kbd-rows:popover-open")
    assert _near(page, ".kbd-rows-btn", "#kbd-rows") < 40
    # rows → sounds in its head; typing is the toolbar's own, one click
    assert page.locator("#kbd-rows #rows-toggle").is_visible()
    assert page.locator("#kbd-rows #keys-toggle").count() == 0
    names = page.locator("#kbd-rows .kr-name").all_inner_texts()
    assert names == ["Keys & MIDI", "1 – =", "Q – ]", "A – '", "Z – /"]
    # what the keys play is Play's alone: read back here, no second select
    assert page.locator("select.kbd-plays, #kbd-rows [data-plays]").count() == 0
    assert page.inner_text("#kr-keys") == "a note (Play)"
    # a row changed here says so on the strip
    page.select_option('#kbd-rows select[name="row-low"]', "notes")
    page.wait_for_function(
        "() => document.getElementById('kr-strip').textContent.includes('notes')"
    )
    # the layer column means something only with rows → sounds
    lanes = page.locator('#kbd-rows select[name^="lane-"]')
    assert all(lanes.nth(i).is_disabled() for i in range(4))
    assert page.locator("#kr-strip .kr-layer").count() == 0
    page.click("#rows-toggle")
    page.wait_for_selector('#rows-toggle[aria-pressed="true"]')
    assert not lanes.first.is_disabled()
    assert page.locator("#kbd-rows:popover-open").count() == 1  # still open
    # a row on its own layer says which, on the dock: no hidden state
    page.select_option('#kbd-rows select[name="lane-low"]', "2")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#kr-strip > span')].some((c) =>"
        " c.textContent.startsWith('Z')"
        " && c.querySelector('.kr-layer')?.textContent === 'L2')"
    )
    # a state light, not an "on"/"off" word
    assert page.evaluate(
        """() => getComputedStyle(document.querySelector('#rows-toggle .led'))
             .boxShadow !== 'none'"""
    )
    assert page.inner_text("#rows-toggle").strip() == "rows → sounds"
    page.keyboard.press("Escape")
    assert page.locator("#kbd-rows:popover-open").count() == 0
    # the arrow bend's settings: a popover beside its own button too
    page.click(".bend-set")
    page.wait_for_selector("#bend-pop:popover-open")
    assert _near(page, ".bend-set", "#bend-pop") < 40


def test_a_chord_type_is_one_click_and_a_key_plays_it(studio):
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    assert _plays(page) == "note"
    labels = page.locator("#kbd-plays [data-plays]").all_inner_texts()
    assert labels[:3] == ["note", "triad", "7th"]  # every choice in sight
    page.click('[data-plays="triad"]')  # note -> chord: one click
    assert _plays(page) == "triad"
    page.click('[data-plays="7th"]')  # the chord type: one click
    assert _plays(page) == "7th"
    assert page.evaluate("() => localStorage.getItem('synthlab.plays')") == "7th"
    page.click('[data-plays="triad"]')
    studio.pointer_down(C4)
    studio.wait_meter(r"[3-9] voices")
    studio.pointer_up()
    studio.wait_meter(r"0 voices")
    page.reload()
    page.wait_for_selector("#kbd-plays")
    studio.settle()
    assert _plays(page) == "triad"  # remembered


def test_backquote_switches_note_and_chord_and_alt_steps_the_chord(studio):
    """` (keysbar.js): note -> the last chord the keys played -> note;
    Alt+` the next chord type. Declared (`data-keys`), and never from a
    text field."""
    page = _bench(studio)
    assert page.get_attribute("#kbd-plays", "data-keys") == "`"
    assert _plays(page) == "note"
    page.keyboard.press("Backquote")
    assert _plays(page) == "triad"  # the first chord, nothing played before
    page.keyboard.press("Alt+Backquote")
    assert _plays(page) == "7th"
    page.keyboard.press("Backquote")
    assert _plays(page) == "note"
    page.keyboard.press("Backquote")
    assert _plays(page) == "7th"  # the last chord, remembered
    page.keyboard.press("Control+k")  # the palette's box: a text field
    page.wait_for_selector("#palette[open]")
    page.keyboard.press("Backquote")
    page.keyboard.press("Escape")
    assert _plays(page) == "7th"


_LIT = """() => [...document.querySelectorAll('#piano .pkey.tone')]
  .map((k) => window.synthlab.piano.midiOf(k)).sort((a, b) => a - b)"""


def test_chord_mode_lights_the_chords_keys_and_names_it(studio):
    """The pointer on a key lights what it plays: in chord mode the keys of
    the chord in key built on it (and its name and degree beside the keys),
    in note mode that key alone; held, the name is bright. In chord mode
    the keys a chord is built on carry its degree."""
    page = _bench(studio)
    page.locator(A4).hover()
    assert page.evaluate(_LIT) == [69]  # a note: the key alone
    assert page.text_content("#kbd-now") == "A4"
    assert page.locator("#piano .pkey[data-deg]").count() == 0
    page.click('[data-plays="7th"]')
    page.locator(A4).hover()
    want = page.evaluate("() => window.synthlab.plays.chordOf(69)")
    assert len(want["notes"]) >= 3
    assert page.evaluate(_LIT) == sorted(want["notes"])
    assert page.text_content("#kbd-now") == want["name"]
    assert page.inner_text("#kbd-now-deg") == want["roman"]
    assert page.get_attribute("#kbd-now", "data-state") == "hover"
    assert (
        page.locator("#piano .pkey[data-deg]").count() >= 5
    )  # a degree a chord in key
    page.mouse.down()
    page.wait_for_selector('#kbd-now[data-state="held"]')
    assert page.text_content("#kbd-now") == want["name"]
    page.mouse.up()
    page.wait_for_selector('#kbd-now:not([data-state="held"])')
    page.locator(".view-head h1").hover()  # off the keys: the choice, nothing lit
    page.wait_for_selector('#kbd-now[data-state="rest"]')
    assert page.text_content("#kbd-now") == "7th"
    assert page.evaluate(_LIT) == []


def test_typing_and_latch_are_one_click_in_the_toolbar(studio):
    page = _bench(studio)
    # typing off: one click, and a typed key plays nothing
    page.click("#keys-toggle")
    assert page.get_attribute("#keys-toggle", "aria-pressed") == "false"
    assert not page.evaluate("() => window.synthlab.keysOn()")
    page.keyboard.down("KeyA")
    page.wait_for_timeout(150)
    assert page.locator("#piano .pkey.down").count() == 0
    page.keyboard.up("KeyA")
    page.click("#keys-toggle")
    assert page.evaluate("() => window.synthlab.keysOn()")
    # latch: the button is a tap of Right Shift — armed, the next note holds
    page.click("#latch-btn")
    assert page.evaluate("() => window.synthlab.latch()") == {"armed": True, "notes": 0}
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    page.wait_for_selector('#latch-btn[aria-pressed="true"]')
    assert page.evaluate("() => window.synthlab.latch()")["notes"] == 1
    page.click("#latch-btn")  # again: let go
    page.wait_for_function("() => window.synthlab.latch().notes === 0")
    assert page.get_attribute("#latch-btn", "aria-pressed") == "false"


def test_the_decks_keys_page_presses_the_segments(studio):
    """The deck's seventh page of keys (deck.js KEYS): Note, Triad, 7th,
    Power, Hold, Latch, Typing — bound by name to the segments' data-cmd,
    lit while each is the choice."""
    page = _bench(studio)
    d = "window.synthlab.deck"
    page.evaluate(f"() => {d}.set({{ keys: 6 }})")
    keys = page.evaluate(f"() => {d}.face().keys")
    assert [k["label"] for k in keys] == [
        "Note",
        "Triad",
        "7th",
        "Power",
        "Hold",
        "Latch",
        "Typing",
        "More",
    ]
    assert not any(k["off"] for k in keys[:7]), keys
    assert keys[0]["on"] and not keys[1]["on"]
    page.evaluate(f"() => {d}.handle({{ type: 'keydown', index: 1 }})")
    assert _plays(page) == "triad"
    keys = page.evaluate(f"() => {d}.face().keys")
    assert keys[1]["on"] and not keys[0]["on"]


# every note-on the piano island is asked for, stamped (chords.js calls it)
_STAMP = """() => { const p = window.synthlab.piano, on = p.noteOn; window.__ons = [];
  p.noteOn = (id, m, v) => {
    window.__ons.push([m, performance.now()]); return on(id, m, v); }; }"""


def test_strum_is_every_chords_the_pads_too_and_is_kept(studio):
    """Strum (chords.js) shows whatever a key plays: the chord pads strum
    by it while the keys play single notes (they used to strum only while
    the keys played chords, with the control hidden), and it is kept per
    browser across a reload."""
    page = _bench(studio)
    assert _plays(page) == "note"
    strum = page.locator("#kbd-strum")
    assert strum.is_visible()
    page.evaluate(
        "() => window.synthlab.tweak(document.getElementById('kbd-strum'), 60)"
    )
    page.click('[popovertarget="play-tools"]')
    page.locator("#pads-chords").evaluate("(d) => { d.open = true; }")
    page.evaluate(_STAMP)
    page.locator("#chord-bar .chord-pad").first.hover()
    page.mouse.down()
    page.wait_for_function("() => window.__ons.length >= 3")
    page.mouse.up()
    ons = page.evaluate("() => window.__ons")
    assert ons[-1][1] - ons[0][1] >= 100, ons  # three notes 60 ms apart
    page.reload()
    page.wait_for_selector("#kbd-strum")
    studio.settle()
    assert page.input_value("#kbd-strum") == "60"  # kept


def test_the_old_chord_mode_comes_back_as_a_triad(studio):
    page = studio.open("editor")
    page.evaluate(
        "() => { localStorage.removeItem('synthlab.plays');"
        " localStorage.setItem('kbd-mode', 'chord'); }"
    )
    page.reload()
    page.wait_for_selector("#kbd-plays")
    studio.settle()
    assert _plays(page) == "triad"


def test_a_key_shows_one_typed_letter_and_names_the_rest(studio):
    page = studio.open("editor")
    rows = {"num": "piano", "top": "piano", "home": "notes", "low": "notes"}
    page.evaluate(
        "(r) => { localStorage.setItem('synthlab.rows', r);"
        " localStorage.setItem('synthlab.rowsBy', '1'); }",
        json.dumps(rows),
    )
    page.reload()
    page.wait_for_selector(".pkey")
    studio.settle()
    keys = page.evaluate(
        """() => [...document.querySelectorAll('.pkey')].map((k) =>
          [k.querySelector('.qhint').textContent, k.dataset.typed || '', k.title])"""
    )
    assert all(len(shown) <= 1 for shown, _, _ in keys), keys
    piled = [k for k in keys if len(k[1].split()) > 1]
    assert piled, "rows by sound should put several typed keys on one key"
    for shown, typed, title in piled:
        assert shown == typed.split()[0] and all(c in title for c in typed.split())


def test_rec_takes_what_plays_into_clips_and_offers_the_sampler(studio, server):
    page = _bench(studio)
    page.evaluate("() => localStorage.setItem('synthlab.keyRecCount', '0')")
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()
    assert page.get_attribute("#key-count", "aria-pressed") == "false"
    page.click("#key-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    page.wait_for_selector("#now-playing[data-rec]")
    page.wait_for_function(
        "() => document.querySelector('#now-playing').textContent.includes('Recording')"
    )
    page.keyboard.down("KeyA")  # play while it records
    page.wait_for_timeout(900)
    page.keyboard.up("KeyA")
    assert page.inner_text("#key-rec").startswith("■ 0:0")
    with page.expect_response("**/take/keys"):
        page.keyboard.press("Alt+KeyR")  # the shortcut stops it too
    page.wait_for_selector('#key-rec[aria-pressed="false"]')
    assert page.locator("#now-playing[data-rec]").count() == 0
    assert page.inner_text("#key-rec") == "⏺ Rec"
    clips = sorted((server.data_dir / "clips").glob("keys-*.wav"))
    assert clips, "no keys take in the clip store"
    meta = json.loads(clips[-1].with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "keys" and meta["kept"] is True
    notes = keytakes.read_notes(server.data_dir, clips[-1].stem)["notes"]
    assert 60 in [n[2] for n in notes]  # A: C4, held while it recorded
    # the Loops strip under the keys has it, as a loop; Edit puts it on
    # Perform's grid as that loop
    row = page.locator(f'#key-takes [data-ctx="loop {clips[-1].stem}"]')
    row.wait_for()
    with page.expect_response("**/loop/edit") as answer:
        row.locator(".lp-edit").click()
    assert answer.value.ok
    page.wait_for_selector('#view[data-view="perform"]')
    studio.settle()
    assert any(c[0] == "C4" for c in server.state.arrangement.cells)
    assert server.state.loop_edit_of()["name"] == clips[-1].stem


def test_a_page_change_ends_the_take_as_stop_does(studio, server):
    """Recording does not continue across pages. A page change
    ends ⏺ Rec as ■ does — kept, holding only the notes played before the
    change — and an armed Rec that has not started is cancelled. (With the
    dock on every page, nothing else would stop it.)"""
    page = _bench(studio)
    page.evaluate("() => localStorage.setItem('synthlab.keyRecCount', '0')")
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()
    page.click("#key-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    page.keyboard.down("KeyA")
    page.wait_for_timeout(600)
    page.keyboard.up("KeyA")
    # nothing plays: a free-length take, so it ends at the change itself
    with page.expect_response("**/take/keys") as answer:
        studio.nav("drums")
    assert answer.value.ok
    page.wait_for_selector(".toast:has-text('The page changed, so ⏺ Rec stopped')")
    assert page.get_attribute("#key-rec", "aria-pressed") == "false"
    assert page.locator("#now-playing[data-rec]").count() == 0
    page.keyboard.down("KeyD")  # after the change: not the take's
    page.wait_for_timeout(300)
    page.keyboard.up("KeyD")
    clips = sorted((server.data_dir / "clips").glob("keys-*.wav"))
    assert clips, "the take was not kept"
    notes = keytakes.read_notes(server.data_dir, clips[-1].stem)["notes"]
    assert {n[2] for n in notes} == {60}
    # armed, its count-in running: a page change cancels it, nothing taken
    page.evaluate("() => localStorage.setItem('synthlab.keyRecCount', '1')")
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()
    page.click("#key-rec")
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('count-in')"
    )
    studio.nav("song")
    page.wait_for_selector('#key-rec[aria-pressed="false"]')
    page.wait_for_selector(".toast:has-text('nothing taken')")
    page.wait_for_timeout(300)
    assert sorted((server.data_dir / "clips").glob("keys-*.wav")) == clips


def test_rec_counts_one_bar_in_first(studio):
    page = _bench(studio)
    assert page.get_attribute("#key-count", "aria-pressed") == "true"  # on by default
    page.click("#key-rec")
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('count-in')"
    )
    page.wait_for_function(
        "() => document.getElementById('now-playing').dataset.state === 'count-in'"
    )
    page.click("#key-rec")  # stopped in the count-in: nothing was taken
    page.wait_for_selector('#key-rec[aria-pressed="false"]')
    page.wait_for_timeout(300)
    assert not list((studio.server.data_dir / "clips").glob("keys-*.wav"))


def test_the_phrase_play_names_the_phrase_it_plays(studio):
    page = studio.open("editor")
    studio.settle()
    cue = page.locator('[data-cue-for="play"]')
    assert cue.text_content().startswith("the phrase · hold · 1 bar")
    page.locator('#sound-bar label:has(input[value="riff"])').click()
    page.wait_for_function(
        "() => document.querySelector('[data-cue-for=play]').textContent"
        ".startsWith('the phrase · riff · 1 bar')"
    )
    page.locator("#sound-bar .vsbeat label").click()
    page.wait_for_function(
        "() => document.querySelector('[data-cue-for=play]').textContent"
        ".includes('riff · 1 bar + the beat')"
    )
    assert "riff" in page.get_attribute("#play", "aria-label")
    assert "riff" in page.inner_text("#now-playing .np-label")  # Space plays …


_FIRST_SCREEN = """() => {
  const top = (s) => document.querySelector(s).getBoundingClientRect().top;
  const dock = document.getElementById('keys-dock');
  return {dock: dock.getBoundingClientRect().top,
          folded: dock.classList.contains('folded'),
          heads: ['#panel-source', '#panel-filter', '#panel-amp-envelope'].map(top)};
}"""


def _editor(chromium, server, w, h):
    ctx = chromium.new_context(viewport={"width": w, "height": h})
    page = ctx.new_page()
    page.goto(f"{server.url}/view/editor")
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    return ctx, page


def test_a_laptop_window_starts_the_dock_folded_and_shows_three_panels(
    chromium, server
):
    """At 1366 x 768 the dock's 150 px would leave the panels 280, and
    Filter under Source, below the fold. Until it is pressed,
    the dock starts folded under 900 px tall (or under a finger); a press
    is remembered; the panels stand Source | Filter | Amp."""
    ctx, page = _editor(chromium, server, 1366, 768)
    got = page.evaluate(_FIRST_SCREEN)
    assert got["folded"], got
    src, flt, amp = got["heads"]
    assert abs(src - flt) <= 1 and abs(src - amp) <= 1, got  # one row of heads
    assert src < got["dock"] - 150, got  # with room under them on the first screen
    page.click(".keys-fold")  # opened by hand: remembered over the media query
    page.reload()
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    assert not page.evaluate(_FIRST_SCREEN)["folded"]
    ctx.close()
    ctx, page = _editor(chromium, server, 1920, 1080)  # a tall window: open
    assert not page.evaluate(_FIRST_SCREEN)["folded"]
    ctx.close()


def test_play_tools_open_above_their_button_which_stays_put(studio):
    """Play tools are a manual popover above the dock, at its right end (as a
    <details> they opened as a line of their own and the button jumped to
    the left), the chords in key open the first time, and they open again
    on the next page if they were left open."""
    page = _bench(studio)
    btn = '[popovertarget="play-tools"]'
    box = page.locator(btn).bounding_box()
    page.click(btn)
    page.wait_for_selector("#play-tools:popover-open")
    assert page.locator(btn).bounding_box() == box
    assert page.evaluate("() => document.getElementById('pads-chords').open")
    pop = page.locator("#play-tools").bounding_box()
    assert pop["y"] + pop["height"] <= box["y"] + 1  # above it
    page.keyboard.press("KeyA")  # a key played leaves it open
    assert page.locator("#play-tools:popover-open").count() == 1
    page.reload()
    page.wait_for_selector("#play-tools:popover-open")
    page.click(btn)
    page.wait_for_selector("#play-tools:not(:popover-open)", state="attached")


def test_a_take_grows_the_dock_upward_and_reads_as_time(studio, server):
    """The Takes strip is the dock's head: the first take grows the dock
    upward, and ⏺ Rec stays under the hand that pressed it. Its length
    reads "0:0n · n.n bars" in the toast and the strip."""
    page = _bench(studio)
    page.evaluate("() => localStorage.setItem('synthlab.keyRecCount', '0')")
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()
    top = page.locator("#key-rec").bounding_box()["y"]
    page.click("#key-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    page.keyboard.down("KeyA")
    page.wait_for_timeout(700)
    page.keyboard.up("KeyA")
    with page.expect_response("**/take/keys"):
        page.click("#key-rec")
    page.wait_for_selector("#key-takes:not([hidden]) .lp-row")
    # (off the button: a hovered .btn lifts 1 px, and `top` was read unhovered)
    page.mouse.move(2, 2)
    page.wait_for_timeout(300)
    assert abs(page.locator("#key-rec").bounding_box()["y"] - top) < 1
    # (the facts are aligned cells — length, grid, bpm,
    # key, time — not one "0:0n · n.n bars" string)
    row = "#key-takes .lp-name small"
    assert page.inner_text(f"{row} .c-time").startswith("0:0")
    assert " bar" in page.inner_text(f"{row} .c-len")
    toast = page.wait_for_selector("#toasts .toast").inner_text()
    assert ": 0:0" in toast and "1.305" not in toast


def test_a_latched_note_goes_with_stop_all_and_with_another_sound(studio):
    """Sounds must not stick when a different sound is picked: a latched
    note must not outlive ■ / Shift+Space or a new sound, sounding the old
    one. A tweak keeps it (the same sound)."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").first.click()  # a gesture; nothing focused
    latched = "() => window.synthlab.latch().notes"
    page.click("#latch-btn")
    page.locator(".view-head h1").first.click()
    page.keyboard.press("a")
    page.wait_for_function(f"{latched} === 1")
    page.keyboard.press("Shift+Space")  # stop everything
    page.wait_for_function(f"{latched} === 0")
    page.click("#latch-btn")
    page.locator(".view-head h1").first.click()
    page.keyboard.press("a")
    page.wait_for_function(f"{latched} === 1")
    now = page.evaluate("() => window.synthlab.piano.patch().name")
    other = page.locator(
        f'#preset-list .sound-card:not([data-name="{now}"]) .load'
    ).nth(2)
    with page.expect_response("**/load"):
        other.click()
    page.wait_for_function(f"{latched} === 0")
