"""Perform's ✦ Coach in a real Chromium, on a typical hand-made
loop (`tests/studio/test_loop_coach.seed`: a 4-bar riff in A major with
notes off the key, every velocity the same, a note off the roll's rows, a
bass lane, a beat): open it from its button and Alt+K; Assess; Fix one and
see it on the grid; ▶ hear the proposal; Accept, and Ctrl+Z; Clean up all;
an option on the beat; Discard; a chat message whose reply (a stand-in for
Gemini, in the studio's own process) proposes edits; ↶ Reverse, both ways;
the column beside the grids at both densities.

Proves the panel, the overlay, the posts and the undo steps work as a
browser drives them, and that ▶ proposed plays (the master analyser carries
signal). Nobody has listened: whether a fix sounds better is not checked."""

from __future__ import annotations

import json
import types

import pytest

from synthlab.studio import loopcoach as lc
from synthlab.studio import takecoach
from synthlab.studio.agent.config import AgentConfig
from tests.studio.test_loop_coach import seed

PANEL = "#coach-panel"


def _seed(server) -> None:
    s = server.state
    with s.lock:
        seed(s)
        s._pattern_undo.clear()


def _doc(server) -> str:
    with server.state.lock:
        return lc.fingerprint(lc.read(server.state))


def _wait(page, check, what: str, timeout: float = 6.0) -> None:
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}")


def _open(studio):
    page = studio.open("perform")
    studio.settle()
    page.click("button.coach-open")
    page.wait_for_selector("#coach-dock:not([hidden])")
    return page


def _blur(page) -> None:
    page.evaluate("() => document.activeElement?.blur()")


def test_assess_fix_one_hear_it_accept_and_ctrl_z(studio, server):
    _seed(server)
    page = _open(studio)
    assert page.locator(".coach-open").get_attribute("aria-expanded") == "true"
    # what it found, each with a why and a Fix
    off = page.locator('.coach-find[data-rule="off_key"]')
    assert "off the key of A major" in off.inner_text()
    assert (
        page.locator(
            '.coach-find[data-rule="hidden"] button:has-text("Show them")'
        ).count()
        == 1
    )
    before = _doc(server)
    off.locator("button", has_text="Fix").click()
    page.wait_for_selector(".coach-prop")
    assert _doc(server) == before  # a proposal changes nothing
    # on the grid: added notes outlined, removed ones struck
    page.wait_for_selector("#transport .grid.roll input.coach-add")
    assert page.locator("#transport .grid.roll input.coach-del").count() >= 1
    # ▶ proposed: the loop as it would be, playing
    page.click(".coach-prop button:has-text('▶ proposed')")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=15000)
    studio.wait_sound()
    page.click(".coach-prop button:has-text('Accept')")
    _wait(page, lambda: _doc(server) != before, "the accept")
    page.wait_for_selector(".coach-done")
    assert page.locator(".coach-prop").count() == 0
    assert page.locator("#transport .grid.roll input.coach-add").count() == 0
    assert "off_key:0" not in {f["id"] for f in lc.context(server.state)["findings"]}
    # Ctrl+Z on Perform takes the accepted change back, whole
    _blur(page)
    page.keyboard.press("Control+z")
    _wait(page, lambda: _doc(server) == before, "the undo")
    # (the undo's answer brings the panel back by its watch)
    page.wait_for_selector('.coach-find[data-rule="off_key"]')


def test_clean_up_all_discard_an_option_on_the_beat_and_reverse(studio, server):
    _seed(server)
    page = _open(studio)
    before = _doc(server)
    page.click("button.coach-all")
    page.wait_for_selector(".coach-prop")
    assert "Clean up all" in page.locator(".coach-label").inner_text()
    page.click(".coach-prop button:has-text('Discard')")
    page.wait_for_selector(".coach-prop", state="detached")
    assert _doc(server) == before
    # an option on the beat: shown on the drum grid
    page.click(".coach-opts button:has-text('Beat: fill into the top')")
    page.wait_for_selector("#perform-drum-grid input.coach-add")
    page.click(".coach-prop button:has-text('Accept')")
    _wait(page, lambda: _doc(server) != before, "the accept")
    rev = page.locator(".coach-done button:has-text('Reverse')")
    assert "undoes its step" in page.locator(".coach-done").inner_text()
    rev.click()
    _wait(page, lambda: _doc(server) == before, "the reverse")
    page.wait_for_selector(".coach-done.reversed")
    assert "it was undone" in page.locator(".coach-done").inner_text()


@pytest.fixture
def gemini(monkeypatch):
    """A stand-in for Gemini in the scratch studio's own process."""
    asked: dict = {}
    reply = {
        "reply": [
            "Bar 1's F is off the key: it is an E now, and the bass lands on the kick."
        ],
        "heard": "make the bass follow the kick",
        "edits": [
            {"lane": "1", "op": "move", "pitch": "F4", "step": 6, "to_pitch": "E4"},
            {"lane": "2", "op": "add", "pitch": "E2", "step": 4, "length": 2},
        ],
    }

    def generate_content(**kw):
        asked.update(kw)
        usage = types.SimpleNamespace(prompt_token_count=900, candidates_token_count=80)
        return types.SimpleNamespace(
            text=json.dumps(reply),
            usage_metadata=usage,
            model_version="gemini-2.5-flash",
        )

    fake = types.SimpleNamespace(
        models=types.SimpleNamespace(generate_content=generate_content)
    )
    monkeypatch.setattr(takecoach, "client_for", lambda config: fake)
    monkeypatch.setattr(takecoach, "sdk_available", lambda: True)
    monkeypatch.setattr(
        takecoach, "load_config", lambda: AgentConfig(api_key="k-test", source="env")
    )
    return asked


def test_a_chat_reply_proposes_edits_accept_then_reverse_as_a_new_step(
    studio, server, gemini
):
    _seed(server)
    page = _open(studio)
    assert page.locator("[data-coach-mic]").count() == 1  # a key: 🎙 is offered
    before = _doc(server)
    box = page.locator('.coach-say [name="coach_say"]')
    box.fill("why does bar 1 sound off?")
    box.press("Enter")
    page.wait_for_selector(".coach-reply")
    assert "off the key" in page.locator(".coach-reply").inner_text()
    assert "why does bar 1 sound off?" in gemini["contents"]
    assert box.input_value() == ""  # emptied once answered
    page.wait_for_selector(".coach-prop")
    assert "lane 2" in page.locator(".coach-prop").inner_text()
    page.click(".coach-prop button:has-text('Accept')")
    _wait(page, lambda: _doc(server) != before, "the accept")
    with server.state.lock:
        doc = lc.read(server.state)
    assert ["E4", 6] in doc["lanes"]["0"]["cells"] and ["E2", 4] in doc["lanes"]["1"][
        "cells"
    ]
    # the grid edited after it: Reverse restores the loop before it as a new step
    page.locator("#transport .grid .gridrow:not(.sliderow)").nth(0).locator(
        "input.cell"
    ).nth(15).click()
    _wait(
        page,
        lambda: (
            _doc(server) not in (before,)
            and "restores" in page.locator(".coach-done").inner_text()
        ),
        "the edit",
    )
    page.click(".coach-done button:has-text('Reverse')")
    _wait(page, lambda: _doc(server) == before, "the reverse")
    page.wait_for_selector(".coach-done.reversed")
    assert "as a new step" in page.locator(".coach-done").inner_text()


def test_hold_the_mic_to_talk_and_the_thread_says_what_was_heard(
    studio, server, gemini
):
    """🎙 held over Chromium's fake microphone (a tone): a WAV reaches the
    model as audio, and the thread shows what it says it heard."""
    _seed(server)
    page = _open(studio)
    studio.pointer_down("[data-coach-mic]")
    page.wait_for_selector('[data-coach-mic][aria-pressed="true"]')
    page.wait_for_timeout(1200)
    studio.pointer_up()
    page.wait_for_selector(".coach-you:has-text('make the bass follow the kick')")
    parts = gemini["contents"]
    assert isinstance(parts, list) and parts[1].inline_data.mime_type == "audio/wav"
    assert "The player's message is the attached audio" in parts[0].text
    page.wait_for_selector(".coach-prop")


def test_alt_k_opens_and_closes_it_and_the_choice_is_kept(studio, server):
    page = studio.open("perform")
    studio.settle()
    assert page.locator("#coach-dock").get_attribute("hidden") is not None
    _blur(page)
    page.keyboard.press("Alt+k")
    page.wait_for_selector("#coach-dock:not([hidden])")
    # focus lands in the message box; Alt+K from there closes it
    page.wait_for_function("() => document.activeElement?.name === 'coach_say'")
    page.keyboard.press("Alt+k")
    page.wait_for_selector("#coach-dock[hidden]", state="attached")
    page.keyboard.press("Alt+k")
    studio.open("perform")
    page.wait_for_selector("#coach-dock:not([hidden])")


@pytest.mark.parametrize("density", ["compact", "comfy"])
def test_the_coach_is_a_column_beside_the_grids(studio, server, density):
    _seed(server)
    page = studio.page
    if density == "comfy":
        page.context.add_cookies(
            [{"name": "density", "value": "comfy", "url": studio.url}]
        )
    studio.open("perform")  # (the studio's origin: its localStorage)
    page.evaluate(
        "() => { try { localStorage.setItem('synthlab.coach', 'open'); } catch {} }"
    )
    for w, h in ((1920, 1080), (2560, 1400)):
        page.set_viewport_size({"width": w, "height": h})
        studio.open("perform")
        studio.settle()
        page.wait_for_selector("#coach-dock:not([hidden])")
        coach = page.locator("#coach-dock").bounding_box()
        roll = page.locator("#transport").bounding_box()
        assert coach["x"] >= roll["x"] + roll["width"] - 2, (w, density, coach, roll)
        assert coach["width"] >= 280 and coach["height"] >= 300, (w, density, coach)
        assert roll["width"] >= 500, (w, density, roll)
        # nothing scrolls sideways
        assert page.evaluate(
            "() => document.documentElement.scrollWidth <= innerWidth + 1"
        )
