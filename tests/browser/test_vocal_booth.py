"""The Voice page's vocal booth (4 · Treat) in a browser: a take dropped
in, a treatment pressed — one press to hearing it — the rack's knob moved
without losing its place, and the audition kept as a clip. Audio is
asserted through the master analyser's level; nobody has listened."""

from __future__ import annotations

from tests.browser.conftest import name_it, wav_bytes


def _take(studio) -> None:
    page = studio.page
    studio.open("voice")
    page.set_input_files(
        "#voice-file",
        {"name": "say.wav", "mimeType": "audio/wav", "buffer": wav_bytes(1.5)},
    )
    page.wait_for_selector('#voice-panel .voice-booth[data-has-take="true"]')


def test_a_treatment_is_heard_in_one_press(studio):
    page = studio.page
    _take(studio)
    page.get_by_role("button", name="ADT double").click()
    page.wait_for_selector(".vb-out .audition[data-note^='/preview/booth']")
    assert (
        page.locator(".vb-treats .btn[aria-pressed='true']").inner_text().strip()
        == "ADT double"
    )
    assert studio.level() > 1e-3  # the audition plays through the master
    # the rack drew the treatment's unit, its knobs the booth's
    assert page.locator(".vb-rack .fx-unit[data-unit='doubler']").count() == 1
    studio.settle()


def test_a_knob_in_the_booth_rack_keeps_its_place_and_marks_the_edit(studio):
    page = studio.page
    _take(studio)
    page.get_by_role("button", name="Tape slapback").click()
    page.wait_for_selector(".vb-out .audition")
    knob = page.locator(".vb-rack .knob[aria-label='Tape echo feedback']")
    knob.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "() => document.querySelector('.vb-rack legend .panel-name')"
        ".textContent.includes('edited')"
    )
    # the morph kept the knob: the keyboard is still on it
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == (
        "Tape echo feedback"
    )
    studio.settle()


def test_the_audition_is_kept_as_a_clip(studio):
    page = studio.page
    _take(studio)
    page.get_by_role("button", name="Telephone").click()
    page.wait_for_selector(".vb-out .audition")
    page.get_by_role("button", name="Keep as clip…").click()
    name_it(page, "phone verse")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#clip-library [data-name]')]"
        ".some((el) => el.dataset.name === 'phone verse')"
    )
    studio.settle()
