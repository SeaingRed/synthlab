"""The arp pattern in Chromium (fragments/arp_pattern.html, static/arppat.js;
studio docs/loop-and-perform.md, "The arp pattern").

What these journeys prove: the grid arp's ▦ on the Loops page opens the
editor, a field changed there is the edited lane's pattern on the server
and the popover stays open across its morph; a step ticked off is a rest;
an empty slot saves the pattern and lights; the grid arp's "▦ pattern"
mode is a choice of its select. The keys arp in its ▦ mode plays the
pattern the server holds: a held triad at 1/8 and 120 bpm sends C, a rest,
E twice (two repeats), G… each `at` where the pattern puts it and each
gate the step's. The notes are read off the live voice's port — nobody
has listened.
"""

from __future__ import annotations

import json
from dataclasses import replace

from synthlab.studio import arppattern as ap
from tests.browser.test_keys_arp import HOOK, NOTES, _pitch, _triad_down, _triad_up

# a step's value as the editor the server drew holds it (what arppat.js reads)
STEP = """([i, k, v]) => JSON.parse(
  document.querySelector('#arp-pat').dataset.pattern).steps[i][k] === v"""


def test_the_grid_arps_editor_writes_the_lanes_pattern_and_saves_a_slot(studio, server):
    s = server.state
    page = studio.open("perform")
    studio.settle()
    assert (
        page.locator(
            '#transport select[name="arp_mode"] option[value="pattern"]'
        ).count()
        == 1
    )
    page.click('#transport [popovertarget="arp-pat"]')
    pop = page.locator("#arp-pat")
    assert pop.evaluate("(el) => el.matches(':popover-open')")
    pop.locator('select[name="rate"]').select_option("1/8")
    page.wait_for_function(
        "() => document.querySelector('#arp-pat').dataset.pattern.includes('1/8')"
    )
    assert s.arrangement.arp_pattern["rate"] == "1/8"
    assert pop.evaluate("(el) => el.matches(':popover-open')")  # morphed, still open
    pop.locator('input[name="on.1"]').uncheck()
    page.wait_for_function(STEP, arg=[1, "on", False])
    assert s.arrangement.arp_pattern["steps"][1]["on"] is False
    pop.locator('input[name="tr.2"]').fill("3")
    pop.locator('input[name="tr.2"]').press("Tab")
    page.wait_for_function(STEP, arg=[2, "tr", 3])
    pop.locator(".ap-slot").nth(2).click()  # empty: saves into it
    page.wait_for_function(
        "() => document.querySelectorAll('.ap-slot[data-filled]').length === 1"
    )
    assert s.arp_slots[2] == s.arrangement.arp_pattern
    assert pop.evaluate("(el) => el.matches(':popover-open')")


def test_the_keys_arp_plays_the_pattern_the_server_holds(studio, server):
    s = server.state
    pat = ap.default()
    pat["steps"] = [
        dict(ap.STEP_DEFAULT),
        {**ap.STEP_DEFAULT, "on": False},
        {**ap.STEP_DEFAULT, "rep": 2},
    ]
    pat["rate"] = "1/8"
    s.arrangement = replace(s.arrangement, bpm=120.0, arp_pattern=pat)
    studio.page.context.add_init_script(HOOK)
    page = studio.open("editor")
    page.evaluate(
        "(v) => localStorage.setItem('synthlab.arp', v)",
        json.dumps({"on": False, "mode": "pattern"}),
    )
    page.reload()
    page.wait_for_function("() => !!window.synthlab?.arpPattern?.hits && !!window.htmx")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    studio.start_voice()
    page.evaluate("() => { window.__arp = []; }")
    page.click("#arp-btn")  # on after the voice is up (its first key is a plain one)
    assert page.locator("#arp-face").text_content().startswith("▦")
    # the keys arp's ⚙ opens the same editor
    page.click(".arp-set")
    page.click('#arp-pop [popovertarget="arp-pat"]')
    assert page.locator("#arp-pat").evaluate("(el) => el.matches(':popover-open')")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.locator(".view-head h1").click()
    _triad_down(page)
    page.wait_for_timeout(1700)
    _triad_up(page)
    page.wait_for_timeout(300)
    ons = page.evaluate(NOTES, "noteOn")
    assert len(ons) >= 6, ons
    assert "".join(_pitch(n["freq"]) for n in ons[:6]) == "CEEGCC"
    t0 = ons[0]["at"]
    want = [0.0, 0.5, 0.625, 0.75, 1.25, 1.375]
    assert all(abs(n["at"] - t0 - w) < 1e-6 for n, w in zip(ons, want, strict=False)), (
        ons
    )
    offs = {n["id"]: n["at"] for n in page.evaluate(NOTES, "noteOff")}
    assert abs(offs[ons[0]["id"]] - ons[0]["at"] - 0.9 * 0.25) < 1e-6
    assert abs(offs[ons[1]["id"]] - ons[1]["at"] - 0.45 * 0.25) < 1e-6
