# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The oscillator panels and the chain browser in a real browser
(tests/studio/test_osc_panels.py holds the markup):

- the FM4 panel: its knobs post their own fields, and what the keys play
  moves with them (the master analyser's spectral centre, a held note
  before and after — nobody listens: it proves the sound changed, not
  that it is good); an algorithm picked from its drawing; a ratio preset;
- the wavetable picker: a table picked with the mouse, the next with the
  arrow keys (a radio group), the position lighting its frame;
- the chain browser: open, search, Enter adds the one found; by genre, the
  arrow keys and Enter add the first; the grouping is kept per browser.
"""

from __future__ import annotations

import time

from synthlab.studio.fxrack import CHAIN_PRESETS

C4 = '.pkey[data-note="0"][data-rel="0"]'


def _wait(page, check, what: str, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = check()
        if got:
            return got
        page.wait_for_timeout(40)
    raise AssertionError(f"never: {what}")


def _shape(studio, server, shape: str):
    page = studio.open("editor")
    studio.settle()
    page.click(f'#row-osc1 .seg label:has(input[value="{shape}"])')
    _wait(page, lambda: server.state.patch.osc1 == shape, f"osc1 = {shape} posted")
    return page


def _var(page, selector: str, name: str) -> str:
    return page.evaluate(
        "([s, n]) => getComputedStyle(document.querySelector(s)).getPropertyValue(n).trim()",
        [selector, name],
    )


def _centre(studio) -> float:
    """The spectral centre (Hz) of a held C4 on the master analyser, the
    mean of a few reads once it sounds."""
    page = studio.page
    studio.pointer_down(C4)
    studio.wait_sound(floor=5e-3)
    page.wait_for_timeout(150)
    reads = []
    for _ in range(6):
        reads.append(
            page.evaluate(
                """() => {
                  const an = window.synthlab.getAnalyser(), ctx = window.synthlab.getCtx();
                  const b = new Float32Array(an.frequencyBinCount);
                  an.getFloatFrequencyData(b);
                  let num = 0, den = 0;
                  for (let i = 1; i < b.length; i++) {
                    const m = 10 ** (b[i] / 20), hz = (i * ctx.sampleRate) / an.fftSize;
                    num += m * hz; den += m;
                  }
                  return den ? num / den : 0;
                }"""
            )
        )
        page.wait_for_timeout(50)
    studio.pointer_up()
    studio.wait_silence()
    return sum(reads) / len(reads)


def test_the_fm4_panels_knobs_post_their_fields_and_the_sound_moves(studio, server):
    page = _shape(studio, server, "fm4")
    panel = page.locator("#fm4-panel")
    assert panel.is_visible()
    assert panel.locator('input[type="range"].knob').count() == 13
    # the default fm4: op 2 bends op 1 a little (stack, ratio 2, level 0.3)
    before = _centre(studio)

    # the keyboard on two knobs: op 2 at its highest ratio and full depth
    for name in ("fm4_ratio2", "fm4_level2"):
        page.focus(f"#p-{name}")
        page.keyboard.press("End")
    _wait(
        page,
        lambda: (
            server.state.patch.fm4_ratio2 == 12.0
            and server.state.patch.fm4_level2 == 1.0
        ),
        "both knobs posted",
    )
    _wait(
        page,
        lambda: page.evaluate("() => window.synthlab.piano.patch().fm4_ratio2") == 12.0,
        "the keys' patch follows",
    )
    after = _centre(studio)
    assert after > before * 1.5, (before, after)
    # the read-outs moved with them, in the matrix's cells
    assert page.locator("#row-fm4_ratio2 output").inner_text().strip() == "12"


def test_an_algorithm_is_picked_from_its_drawing_and_a_ratio_preset_lands(
    studio, server
):
    page = _shape(studio, server, "fm4")
    assert _var(page, '.fm4-algo[data-algo="stack"]', "--on") == "1"
    page.click('.fm4-algo[data-algo="pairs"]')
    _wait(page, lambda: server.state.patch.fm4_algo == "pairs", "the algorithm posted")
    assert page.eval_on_selector("#p-fm4_algo", "s => s.value") == "pairs"
    assert _var(page, '.fm4-algo[data-algo="pairs"]', "--on") == "1"
    assert _var(page, '.fm4-algo[data-algo="stack"]', "--on") == "0"
    # pairs: 2 -> 1 and 4 -> 3, so operators 1 and 3 are heard
    lit = [_var(page, f'.fm4-head[data-op="{k}"]', "--on") for k in (1, 2, 3, 4)]
    assert lit == ["1", "0", "1", "0"]

    # the select is still the control: the keyboard picks there too
    page.select_option("#p-fm4_algo", "three")
    _wait(page, lambda: server.state.patch.fm4_algo == "three", "the select posted")

    page.click(".fm4-ratios-open")
    page.click('[data-cmd="FM4 ratios: E-piano tine"]')
    _wait(
        page,
        lambda: (
            (
                server.state.patch.fm4_ratio1,
                server.state.patch.fm4_ratio2,
                server.state.patch.fm4_ratio3,
                server.state.patch.fm4_ratio4,
            )
            == (1.0, 1.0, 1.0, 14.0)
        ),
        "the four ratios posted",
    )
    # 14 is past the knob's drawn travel (0.5-12): it widened to take it
    assert page.eval_on_selector("#p-fm4_ratio4", "k => [k.max, k.value]") == [
        "14",
        "14",
    ]
    assert page.locator("#fm4-ratios:popover-open").count() == 0


def test_a_wavetable_is_picked_by_click_and_by_the_arrow_keys(studio, server):
    page = _shape(studio, server, "wavetable")
    assert page.locator(".wt-thumb").count() == 12
    page.click('.wt-thumb:has(input[value="vocal"])')
    _wait(page, lambda: server.state.patch.wt_table == "vocal", "a click posted vocal")
    radio = 'input[name="wt_table"][value="vocal"]'
    assert page.eval_on_selector(radio, "r => r.checked") is True
    # a radio group: the arrows move the choice, and each move posts
    page.focus(radio)
    page.keyboard.press("ArrowRight")
    _wait(
        page,
        lambda: server.state.patch.wt_table == "digital",
        "the arrow posted digital",
    )
    assert page.evaluate("() => document.activeElement.value") == "digital"
    assert page.locator('.wt-thumb:has(input[value="digital"]:checked)').count() == 1

    # the position lights its frame: the first at 0, the last at the end
    first, last = (
        ".wt-thumb:has(:checked) .wt-f:first-child",
        ".wt-thumb:has(:checked) .wt-f:last-child",
    )
    assert float(_var(page, first, "opacity")) == 1.0
    page.focus("#p-wt_pos")
    page.keyboard.press("End")
    _wait(page, lambda: server.state.patch.wt_pos == 1.0, "the position posted")
    assert _var(page, ".wt-pick", "--wt-pos") == "1"
    assert float(_var(page, last, "opacity")) == 1.0
    assert float(_var(page, first, "opacity")) < 0.2


def test_the_chain_browser_searches_regroups_and_adds(studio, server):
    page = studio.open("editor")
    studio.settle()
    page.click('button[popovertarget="fx-chains-layer"]')
    pop = page.locator("#fx-chains-layer")
    _wait(
        page,
        lambda: pop.evaluate("p => p.matches(':popover-open')"),
        "the browser opens",
    )
    assert page.evaluate(
        "() => document.activeElement.matches('#fx-chains-layer .fxc-q')"
    )
    rows = pop.locator(".fxc-row:not([hidden])")
    assert rows.count() == len(CHAIN_PRESETS)
    # by purpose: Space first
    assert (
        pop.locator(".fxc-sec:visible .fxc-h").first.inner_text().strip().lower()
        == "space"
    )

    page.keyboard.type("gated")
    _wait(page, lambda: rows.count() == 1, "one chain found")
    assert rows.first.locator(".fxc-name").inner_text() == "Gated Verb"
    page.keyboard.press("Enter")
    _wait(
        page,
        lambda: (
            [u["kind"] for u in server.state.layers[server.state.active_layer]["fx"]]
            == ["reverb", "vgate"]
        ),
        "Gated Verb on the layer",
    )
    page.wait_for_selector("#fx-chains-layer:not(:popover-open)", state="attached")

    # by genre, kept for the next open; the arrow keys and Enter
    page.click('button[popovertarget="fx-chains-master"]')
    master = page.locator("#fx-chains-master")
    master.locator('.fxc-mode[data-by="pack"]').click()
    assert (
        master.locator('.fxc-mode[data-by="pack"]').get_attribute("aria-pressed")
        == "true"
    )
    heads = master.locator(".fxc-sec:visible .fxc-h")
    assert heads.first.inner_text().strip().lower() == "melodic techno"
    packs = {c["pack"] for c in CHAIN_PRESETS.values()}
    assert heads.count() == len(packs)  # the packs with chains in them (no Other)
    master.locator(".fxc-q").focus()
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.matches('.fxc-row')")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    first = page.evaluate(
        "() => document.activeElement.querySelector('.fxc-name').textContent"
    )
    assert first == "Sidechain Pump 1/4"
    page.keyboard.press("Enter")
    _wait(
        page,
        lambda: [u["kind"] for u in server.state.fx_master] == ["pump"],
        "the pump on the master",
    )
    assert page.evaluate("() => localStorage.getItem('synthlab.fxChainsBy')") == "pack"
    page.click('button[popovertarget="fx-chains-master"]')
    _wait(
        page,
        lambda: page.locator("#fx-chains-master[data-by='pack']").count() == 1,
        "genre again",
    )
