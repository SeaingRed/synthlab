"""The Synths page's Sounds dock, Save sound… and the mod mixes: a sound
picked from the list plays from the keys
at once; a click on a row's tags loads it (they used to filter the list
away); ☰ makes the list one line a sound; Save sound… opens under its
button and says where the sound went; the mod matrix's ⚄ mix and ⋯ mixes
set the routes in one move; and the scope does not jump when the voice
count changes. Proves what the page does, not how anything sounds."""

from __future__ import annotations


def _box(page, sel):
    return page.locator(sel).bounding_box()


def test_a_picked_sound_plays_from_the_keys_and_the_list_stays(studio, server):
    page = studio.open("editor")
    studio.settle()
    card = page.locator(".sound-card.factory").nth(2)
    name = card.get_attribute("data-name")
    with page.expect_response("**/fragment/factory/load"):
        card.locator(".tagchips i").first.click()  # a tag is part of the row
    studio.settle()
    assert server.state.patch.name == name
    assert page.input_value("#dock-filter") == ""
    assert not page.locator(".sound-cards li[hidden]").count()
    assert page.evaluate("() => document.activeElement === document.body")
    page.keyboard.down("KeyA")
    page.wait_for_function("() => document.querySelectorAll('.pkey.down').length > 0")
    page.keyboard.up("KeyA")
    # ☰: one line a sound, the tags hidden, remembered
    page.click("#sounds-compact")
    assert page.get_attribute("#sounds-compact", "aria-pressed") == "true"
    assert not card.locator(".tagchips").is_visible()
    page.reload()
    page.wait_for_function("() => !!window.htmx")
    assert page.evaluate(
        "() => document.documentElement.hasAttribute('data-sounds-compact')"
    )


def test_save_opens_under_its_button_and_lights_the_saved_row(studio, server):
    page = studio.open("editor")
    studio.settle()
    page.click(".sb-save")
    page.wait_for_selector("#save-dialog:popover-open")
    btn, pop = _box(page, ".sb-save"), _box(page, "#save-dialog")
    # under it (flipped to its left edge when the window's right is short)
    assert pop["x"] < btn["x"] + btn["width"] and pop["x"] + pop["width"] > btn["x"]
    # (a subpixel either way: batched runs land it 0.5 px into the button)
    assert -1 <= pop["y"] - (btn["y"] + btn["height"]) < 40
    # the whole sound by default, as the Recipes are
    assert page.is_checked("#save-dialog input[name=include_layers]")
    page.fill("#save-dialog input[name=name]", "Round Two")
    page.click("#save-dialog button[type=submit]")
    page.wait_for_selector('.sound-card.just-saved[data-name="Round Two"]')
    saved = next(server.state.preset_dir().glob("Round*Two*.json")).read_text("utf-8")
    assert '"format": 2' in saved and '"layers"' in saved
    page.wait_for_function(
        "() => document.getElementById('toasts').textContent.includes('in Yours')"
    )


def test_the_mod_matrix_takes_a_mix_in_one_move(studio, server):
    page = studio.open("editor")
    studio.settle()
    page.locator("#panel-mod-matrix").scroll_into_view_if_needed()
    page.click("#panel-mod-matrix .pp-open")
    page.wait_for_selector("#pp-mod-matrix:popover-open")
    with page.expect_response("**/fragment/mod/mix"):
        page.click("#pp-mod-matrix .menu-item:has-text('Wobble')")
    studio.settle()
    p = server.state.patch
    assert (p.mod1_src, p.mod1_dst, p.lfo_sync) == ("lfo", "cutoff", "1/8")
    assert page.input_value("[name=mod1_dst]") == "cutoff"  # the rows follow
    assert not page.evaluate(
        "() => document.getElementById('pp-mod-matrix').matches(':popover-open')"
    )
    with page.expect_response("**/fragment/mod/mix"):
        page.click("#panel-mod-matrix .mix-dice")
    studio.settle()
    routed = sum(
        getattr(server.state.patch, f"mod{i}_src") != "none" for i in range(1, 5)
    )
    assert routed >= 2


def test_the_scope_keeps_its_width_when_the_voice_count_moves(studio):
    page = studio.open("editor")
    studio.settle()
    widths = (
        "() => ['scope', 'spectrum'].map((id) =>"
        " document.getElementById(id).getBoundingClientRect().width)"
    )
    before = page.evaluate(widths)
    page.evaluate(
        "() => { const c = document.getElementById('cpu-meter'); c.hidden = false;"
        " c.textContent = 'CPU 12% · 16 voices · wasm';"
        " document.querySelector('#now-playing .np-label').textContent ="
        " 'the phrase · a long sound name · riff · 2 bars'; }"
    )
    assert page.evaluate(widths) == before
