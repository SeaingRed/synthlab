"""Sound and layer switching on Perform, in a real Chromium: a lane head's
sound ▾ — the searchable menu over every
sound there is — by mouse and by keyboard, what a load replaces when there
are layers, the layers switched from their heads, the keys playing each new
sound (the live voice's patch is the lane's), Ctrl+Z giving the old one
back; and Synths' Load asking the same question."""

from __future__ import annotations

from dataclasses import replace

from synthlab.studio.factory import FACTORY

from .test_input_matrix import seed, wait

PLAIN = FACTORY[3]["name"]


def _layered(s, name: str, cutoff: float) -> None:
    """A saved sound with a layer of its own (Synths' "Save sound…" with its
    layers), then the session's stack back as it was."""
    was = s.patch
    s.patch = replace(s.patch, cutoff=cutoff)
    s.add_layer(replace(s.patch, name=f"{name} layer"), select=False)
    s.save_preset(name, include_layers=True)
    s.clear_layer(1)
    s.patch = was


def _session(server) -> None:
    """Two saved layered sounds, the factory's and the recipes' as shipped,
    and a loop of two lanes with notes of their own."""
    s = server.state
    _layered(s, "Starlight stack", 2400.0)
    _layered(s, "Acid bass", 700.0)
    seed(server, cells=[["A1", 0], ["A1", 8]])
    s.add_layer(replace(s.patch, name="Second"), select=False)
    s.set_follow(1, None)
    s.layers[1]["cells"] = [["E2", 4]]
    s._pattern_undo.clear()


def _heard(page, name: str) -> None:
    """The live voice holds `name` (the keys' patch is the edited lane's), and
    a key played now sounds through it."""
    page.wait_for_function(
        "(n) => window.synthlab.piano.patch()?.name === n", arg=name, timeout=8000
    )
    page.evaluate(
        """() => { const p = window.synthlab.piano;
             p.noteOn("probe", 57, 0.8); }"""
    )


def _release(studio) -> None:
    studio.page.evaluate("() => window.synthlab.piano.noteOff('probe')")
    studio.wait_meter(r"\b0 voices")


def test_pick_a_sound_per_lane_switch_layers_the_keys_play_it_and_undo(studio, server):
    _session(server)
    s = server.state
    page = studio.open("perform")
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    studio.start_voice()
    second = dict(s.layers[1])

    # --- by mouse: lane 1's ▾, a search, a layered sound -> it asks ---------
    page.click("#lh1-sound")
    page.wait_for_selector("#sound-pick:popover-open")
    assert page.evaluate("() => document.activeElement.id") == "spk-q"
    page.fill("#spk-q", "acid")
    shown = page.locator("#spk-list .spk-item:not([hidden])").all_inner_texts()
    assert "Acid bass" in shown and "Starlight stack" not in shown
    page.click('#spk-list .spk-item:not([hidden]) >> text="Acid bass"')
    page.wait_for_selector("#spk-choice:not([hidden])")
    # a sound with layers: the default (focused) is what a load always did
    assert page.evaluate("() => document.activeElement.dataset.as") == "whole"
    with page.expect_response("**/fragment/perform/lane/sound"):
        page.click('#spk-choice [data-as="layer"]')
    wait(page, lambda: s.patch.name == "Acid bass", "lane 1 holds Acid bass")
    assert s.active_layer == 0 and s.layers[1] == second  # this layer only
    page.wait_for_selector('#lh1-sound[data-lane-name="Acid bass"]')
    _heard(page, "Acid bass")
    studio.wait_meter(r"\b1 voice\b")
    _release(studio)

    # --- by keyboard: Alt+S, type, Enter, Enter (the default) ----------------
    page.locator(".view-head h1").click()
    page.keyboard.press("Alt+KeyS")
    page.wait_for_selector("#sound-pick:popover-open")
    page.keyboard.type("starlight")
    page.keyboard.press("Enter")
    page.wait_for_selector("#spk-choice:not([hidden])")
    with page.expect_response("**/fragment/perform/lane/sound"):
        page.keyboard.press("Enter")  # the whole sound: its layer comes too
    wait(page, lambda: s.patch.name == "Starlight stack", "the whole sound")
    wait(
        page,
        lambda: s.layers[1]["patch"]["name"] == "Starlight stack layer",
        "its layer",
    )
    _heard(page, "Starlight stack")
    studio.wait_meter(r"\b2 voices\b")  # the key sounds on both layers
    _release(studio)

    # --- Ctrl+Z: the sound and the lane as they were -------------------------
    page.locator(".view-head h1").click()
    with page.expect_response("**/fragment/pattern/undo"):
        page.keyboard.press("Control+KeyZ")
    wait(page, lambda: s.patch.name == "Acid bass", "Acid bass back")
    wait(page, lambda: s.layers[1] == second, "lane 2 back")
    _heard(page, "Acid bass")
    _release(studio)

    # --- switch layers from the heads: by keyboard, then by mouse -----------
    page.focus("#lh2-name")
    with page.expect_response("**/fragment/layer/select"):
        page.keyboard.press("Enter")
    wait(page, lambda: s.active_layer == 1, "lane 2 edited")
    page.wait_for_selector('#lh2-name[aria-pressed="true"]')
    _heard(page, "Second")
    _release(studio)
    # its own sound from its own ▾: a factory sound, found by its group
    page.click("#lh2-sound")
    page.fill("#spk-q", f"factory {PLAIN.split()[0]}")
    page.click(f'#spk-list .spk-item:not([hidden]) >> text="{PLAIN}"')
    page.wait_for_selector("#spk-choice:not([hidden])")  # (two sounds: it asks)
    with page.expect_response("**/fragment/perform/lane/sound"):
        page.keyboard.press("Enter")  # a plain sound: this layer
    wait(
        page,
        lambda: s.active_layer == 1 and s.patch.name == PLAIN,
        "lane 2's new sound",
    )
    assert s.layers[0]["patch"]["name"] == "Acid bass"
    _heard(page, PLAIN)
    _release(studio)
    with page.expect_response("**/fragment/layer/select"):
        page.click("#lh1-name")
    wait(page, lambda: s.active_layer == 0, "back on lane 1")
    _heard(page, "Acid bass")
    _release(studio)

    # --- mute and solo from a head --------------------------------------------
    with page.expect_response("**/fragment/layer/mute"):
        page.click("#lh2-m")
    wait(page, lambda: s.layers[1].get("muted"), "lane 2 muted")
    page.wait_for_selector('#lh2-m[aria-pressed="true"]')
    assert "unheard" in page.get_attribute('.lane-head[data-lane-index="1"]', "class")

    # --- an empty lane's ▾: a new lane with its own grid ---------------------
    page.click("#lh-new-sound")
    page.fill("#spk-q", "acid")
    with page.expect_response("**/fragment/perform/lane/sound"):
        page.keyboard.press("Enter")  # an empty lane replaces nothing: no question
    wait(page, lambda: s.active_layer == 2 and s.patch.name == "Acid bass", "lane 3")
    assert not s.follows(2) and s.arrangement.cells == []
    page.wait_for_selector('#lh3-name[aria-pressed="true"]')
    page.locator(".view-head h1").click()
    with page.expect_response("**/fragment/pattern/undo"):
        page.keyboard.press("Control+KeyZ")
    wait(
        page,
        lambda: s.active_layer == 0 and not s.layers[2].get("patch"),
        "lane 3 gone",
    )


def test_synths_load_asks_what_it_replaces_when_there_are_layers(studio, server):
    _session(server)
    s = server.state
    page = studio.open("editor")
    second = dict(s.layers[1])
    card = page.locator(f'#preset-list .sound-card[data-name="{PLAIN}"] .load')
    card.scroll_into_view_if_needed()
    card.click()
    page.wait_for_selector("#sound-pick:popover-open #spk-choice:not([hidden])")
    assert page.locator("#spk-list").is_hidden()  # Load: the question, no list
    assert page.evaluate("() => document.activeElement.dataset.as") == "layer"
    with page.expect_response("**/fragment/factory/load"):
        page.click('#spk-choice [data-as="layer"]')
    wait(page, lambda: s.patch.name == PLAIN, "the edited layer's new sound")
    assert s.layers[1] == second
    page.wait_for_selector('#layer-strip[data-stack="2"]')
    # the whole sound: the other layer goes
    card = page.locator('#preset-list .sound-card[data-name="Acid bass"] .load')
    card.scroll_into_view_if_needed()
    card.click()
    page.wait_for_selector("#spk-choice:not([hidden])")
    assert page.evaluate("() => document.activeElement.dataset.as") == "whole"
    with page.expect_response("**/fragment/preset/load"):
        page.keyboard.press("ArrowLeft")  # (the arrows walk the three)
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Enter")
    wait(page, lambda: s.patch.name == "Acid bass", "Acid bass")
    wait(page, lambda: s.layers[1]["patch"]["name"] == "Acid bass layer", "its layer")
