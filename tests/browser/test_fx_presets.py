"""Resetting and recalling in a real page: a middle click puts a knob back
to its default (tweak.js), an effect's ★ menu recalls a preset in one move,
and the same presets are rows of the module's right-click menu (menu.js).
Proves the values, not how they sound."""

from __future__ import annotations


def test_middle_click_resets_and_an_effect_recalls_its_presets(studio, server):
    page = studio.open("editor")
    studio.settle()
    # a knob moved, then middle-clicked: its default again
    default = float(page.get_attribute("#p-drive", "data-default"))
    with page.expect_response("**/fragment/patch"):
        page.evaluate(
            "() => window.synthlab.tweak(document.getElementById('p-drive'), 3.5, true)"
        )
    studio.settle()
    assert server.state.patch.drive != default
    page.locator("#p-drive").scroll_into_view_if_needed()
    with page.expect_response("**/fragment/patch"):
        page.locator("#p-drive").click(button="middle")
    studio.settle()
    assert server.state.patch.drive == default
    # an effect: ★ → Hall
    with page.expect_response("**/fragment/fx/add"):
        page.evaluate(
            "() => htmx.ajax('POST', '/fragment/fx/add', {values: {scope: 'layer',"
            " kind: 'reverb'}, target: '#fx-rack', swap: 'outerHTML'})"
        )
    studio.settle()
    unit = page.locator("#fx-rack .fx-unit").first
    unit.scroll_into_view_if_needed()
    unit.locator(".pp-open").click()
    page.wait_for_selector("#fxp-layer-0:popover-open")
    with page.expect_response("**/fragment/fx/set"):
        page.click("#fxp-layer-0 .menu-item:text-is('Hall')")
    studio.settle()
    fx = server.state.layers[server.state.active_layer]["fx"]
    assert fx[0]["params"]["decay"] == 2.8
    # the right click lists the same rows
    unit.locator("header strong").click(button="right")
    page.wait_for_selector("#ctx-menu:popover-open")
    rows = page.locator("#ctx-menu").inner_text()
    assert "Preset: Cathedral" in rows and "Defaults: every setting back" in rows
    page.keyboard.press("Escape")


# --- the same rack on the Song drawer's audio track and in the vocal booth -------
# (fragments/fx_units.html: one macro, each rack posting to its own route)


def _jacks_and_wires(page, rack: str) -> dict:
    return page.evaluate(
        """(sel) => {
          const r = document.querySelector(sel + ' .fx-units');
          return { jacks: r.querySelectorAll(':scope > .fx-io > .jack').length,
                   wired: r.hasAttribute('data-wired'),
                   cores: r.querySelectorAll('.fx-wires .core').length };
        }""",
        rack,
    )


def test_the_song_drawers_rack_recalls_a_preset_and_draws_its_leads(studio, server):
    import numpy as np

    from synthlab.core import SR
    from synthlab.studio import schema
    from synthlab.studio import song_model as sm
    from synthlab.studio.clips import save_clip
    from tests.studio.test_song_routes import song_dict

    from .test_song import _wait

    rng = np.random.default_rng(3)
    save_clip(server.data_dir, "disco-loop", rng.standard_normal((2 * SR, 2)) * 0.2)
    song = schema.conform(song_dict(), schema.nodes().SONG)
    sm.place_take(song, "s1", "ta", "disco-loop", 0)
    server.state.song = schema.conform(song, schema.nodes().SONG)
    server.state.fx_master = []
    page = studio.open("song")
    studio.settle()
    page.click("#trk-ta .sec-menu-btn")
    page.locator("#trkmenu-ta .track-fx").click()
    page.wait_for_selector("#song-drawer .drawer-fx")
    studio.settle()
    page.select_option("#song-drawer .drawer-fx .fx-adder", "reverb")
    page.wait_for_selector('#song-drawer .fx-unit[data-unit="reverb"]')
    studio.settle()
    vox = lambda: next(t for t in server.state.song["tracks"] if t["id"] == "ta")  # noqa: E731
    # jacks at both ends, so fxcables.js wires it: in -> reverb -> out
    page.wait_for_function(
        "() => document.querySelectorAll('#song-drawer .fx-wires .core').length === 2"
    )
    w = _jacks_and_wires(page, "#song-drawer .drawer-fx")
    assert w["jacks"] == 2 and w["wired"]
    # ★ -> Cathedral: one post to the track's route, the song's unit moves
    unit = page.locator('#song-drawer .fx-unit[data-unit="reverb"]')
    unit.scroll_into_view_if_needed()
    unit.locator(".pp-open").click()
    page.wait_for_selector("#fxp-trk-ta-0:popover-open")
    with page.expect_response("**/song/track/ta/fx"):
        page.click("#fxp-trk-ta-0 .menu-item:text-is('Cathedral')")
    _wait(page, lambda: vox()["fx"][0]["params"]["decay"] == 5.5, "Cathedral")
    studio.settle()
    # a middle click on a knob: its default, through the same route (to the
    # knob's step is 0.058 from 0.2, off 2.0: the reset drops the grid)
    knob = page.locator("#song-drawer .knob[aria-label='Reverb decay']")
    knob.scroll_into_view_if_needed()
    with page.expect_response("**/song/track/ta/fx"):
        knob.click(button="middle")
    decay = lambda: vox()["fx"][0]["params"]["decay"]  # noqa: E731
    _wait(page, lambda: decay() == 2.0, "the default")
    studio.settle()


def test_the_booths_rack_recalls_a_preset_and_draws_its_leads(studio, server):
    from synthlab.studio import voice

    from .test_vocal_booth import _take

    page = studio.page
    _take(studio)
    page.get_by_role("button", name="Dreamy plate").click()
    page.wait_for_selector(".vb-rack .fx-unit[data-unit='plate']")
    studio.settle()
    page.wait_for_function(
        "() => document.querySelectorAll('.vb-rack .fx-wires .core').length === 3"
    )
    w = _jacks_and_wires(page, ".vb-rack")
    assert w["jacks"] == 2 and w["wired"]
    unit = page.locator(".vb-rack .fx-unit[data-unit='plate']").first
    unit.scroll_into_view_if_needed()
    unit.locator(".pp-open").click()
    page.wait_for_selector("#fxp-booth-1:popover-open")
    with page.expect_response("**/voice/booth/fx"):
        page.click("#fxp-booth-1 .menu-item:text-is('Long')")
    studio.settle()
    chain = voice.read_lab(server.data_dir)["booth"]["chain"]
    assert chain[1]["kind"] == "plate" and chain[1]["params"]["decay"] == 5.0
    # the right click lists the same rows
    unit.locator("header strong").click(button="right")
    page.wait_for_selector("#ctx-menu:popover-open")
    assert "Preset: Vocal" in page.locator("#ctx-menu").inner_text()
    page.keyboard.press("Escape")
