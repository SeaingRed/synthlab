"""Idle controls in a real page (paramui.idle, audio.js `patch-idle`):
unison detune is drawn idle with one voice, and stops being idle the moment
the unison knob gives it voices — no reload."""

from __future__ import annotations

from dataclasses import replace


def test_an_idle_control_wakes_when_its_dependency_moves(studio, server):
    server.state.patch = replace(server.state.patch, unison=1)
    page = studio.open("editor")
    studio.settle()
    row = page.locator("#row-unison_detune")
    assert row.get_attribute("data-idle") is not None
    assert "no effect now: needs 2 or more unison voices" in row.get_attribute("title")
    with page.expect_response("**/fragment/patch"):
        page.evaluate(
            "() => window.synthlab.tweak(document.getElementById('p-unison'), 4, true)"
        )
    page.wait_for_function(
        "() => !document.getElementById('row-unison_detune').hasAttribute('data-idle')"
    )
    assert "no effect now" not in (row.get_attribute("title") or "")
