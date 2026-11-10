"""Synths' sound bar and its dialogs: one name popover for every "name
it", a Save sound… you can type in, a
row that never reflows, and the answers that say what happened.

What these journeys prove, in Chromium: ⏺ Bounce asks in the anchored
name popover (prefilled with the sound's name, selected), Enter bounces
and the toast says where it went, Esc asks nothing; Save sound…'s name
field is wide, prefilled and selected, its checkbox a 16 px box; the
identity row keeps ✦ Agent and Save sound… where they were when the
second A/B slot fills and → Song answers (in a popover under its
button); the sounds filter's no-match says so. Nobody has listened.
"""

from __future__ import annotations

BOUNCE = '#sound-bar [hx-post="/clip/bounce"]'

# the field's value, whether all of it is selected and focused, its width
FIELD = """(s) => { const f = document.querySelector(s);
  return {value: f.value, width: f.getBoundingClientRect().width,
    whole: f.selectionStart === 0 && f.selectionEnd === f.value.length,
    focus: document.activeElement === f}; }"""


def _bench(studio):
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    return page


def _box(page, sel: str) -> dict:
    return page.locator(sel).first.bounding_box()


def _ends(page) -> tuple:
    return (_box(page, ".sb-save")["y"], _box(page, ".sb-agent")["y"])


def test_bounce_asks_in_the_name_popover_and_says_where_it_went(studio, server):
    page = _bench(studio)
    page.click(BOUNCE)
    page.wait_for_selector("#name-pop:popover-open")
    got = page.evaluate(FIELD, "#name-pop input")
    assert got["value"] == page.inner_text("#sound-id .sid-name").strip()
    assert got["whole"] and got["focus"]
    assert page.inner_text("#name-pop label span") == "Clip name"
    page.wait_for_timeout(400)  # its entry transition slides it in
    pop, btn = _box(page, "#name-pop"), _box(page, BOUNCE)
    assert 0 <= pop["y"] - (btn["y"] + btn["height"]) < 16  # under its button
    assert abs(pop["x"] - btn["x"]) < 24
    # Esc asks nothing: no request, no clip
    page.keyboard.press("Escape")
    page.wait_for_selector("#name-pop:not(:popover-open)", state="attached")
    assert not list((server.data_dir / "clips").glob("*.wav"))
    # typed (a letter is not a note) and Enter: the bounce, and its toast
    page.click(BOUNCE)
    page.wait_for_selector("#name-pop:popover-open")
    page.keyboard.type("my bounce")
    with page.expect_response("**/clip/bounce") as answer:
        page.keyboard.press("Enter")
    assert answer.value.ok
    assert (server.data_dir / "clips" / "my bounce.wav").exists()
    toast = page.wait_for_selector("#toasts .toast").inner_text()
    assert toast.startswith("Bounced “my bounce” (1 bar, ") and "to Clips" in toast


def test_save_sound_opens_with_the_name_to_type_over(studio):
    page = _bench(studio)
    page.click(".sb-save")
    page.wait_for_selector("#save-dialog:popover-open")
    got = page.evaluate(FIELD, '#save-dialog input[name="name"]')
    assert got["value"] == page.inner_text("#sound-id .sid-name").strip()
    assert got["whole"]
    assert got["width"] > 120  # it was 36 px
    page.wait_for_timeout(400)  # its entry transition scales it in
    check = _box(page, "#save-dialog .check")
    assert (check["width"], check["height"]) == (16, 16)  # a box, not a 6ch pill
    assert page.locator("#save-dialog [type=submit].btn-primary").count() == 1


def test_the_identity_row_keeps_its_end_in_place(studio):
    page = _bench(studio)
    before = _ends(page)
    page.click("#ab-slots button:has-text('A')")
    page.wait_for_selector("#ab-slots .ab-dot")
    page.click("#ab-slots button:has-text('B')")
    page.wait_for_selector("#ab-slots .morph:not(.morph-slot)")
    studio.settle()
    assert _ends(page) == before
    with page.expect_response("**/song/from-loop"):
        page.click("#sound-bar .to-song")
    note = page.wait_for_selector("#sound-bar .to-song-note:popover-open")
    assert "Open Song" in note.inner_text()
    assert _ends(page) == before
    btn = _box(page, "#sound-bar .to-song")
    assert _box(page, "#sound-bar .to-song-note")["y"] >= btn["y"] + btn["height"]


def test_a_filter_that_matches_nothing_says_so(studio):
    page = _bench(studio)
    page.fill("#dock-filter", "zzzz")
    page.wait_for_selector("#dock-none:not([hidden])")
    assert "zzzz" in page.inner_text("#dock-none")
    assert page.locator("#preset-list .dock-shelf:not([hidden])").count() == 0
    page.fill("#dock-filter", "pad")
    page.wait_for_selector("#dock-none[hidden]", state="attached")
    counts = page.locator("#preset-list .dock-shelf:not([hidden]) .lib-section small")
    assert counts.count() > 0
    assert all(" of " in t for t in counts.all_text_contents())
    page.fill("#dock-filter", "")
    first = page.locator("#preset-list .lib-section small").first
    assert " of " not in first.text_content()
