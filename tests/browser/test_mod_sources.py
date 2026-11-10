# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""Filter 2, drawn LFOs and the mod routes in a real browser
(tests/studio/test_filter2_mod_ui.py holds the markup):

- filter 2: switched on from its block, serial then parallel — the rows
  appear, the drawing follows, and what a held key plays moves (a low-pass
  into a high-pass is quieter than the two side by side);
- a drawn LFO: LFO 1 drawn (a shape from the menu, then a point dragged),
  the route it feeds moves a held note's spectral centre;
- routes 5-12: "+ route" opens them one by one, a route set there posts;
- a mod-wheel route from a stand-in MIDI port: CC 1 opens the cutoff of a
  held note (and the Modulators panel is on the page).

Nobody listens: each proves the sound changed, not that it is good.
"""

from __future__ import annotations

import time
from dataclasses import replace

from synthlab.synth import Patch

C4 = '.pkey[data-note="0"][data-rel="0"]'

_FAKE_MIDI = """
window.__midiIn = {
  id: "fake-1", name: "Fake Keys", type: "input", state: "connected",
  onmidimessage: null,
};
navigator.requestMIDIAccess = async () => ({
  inputs: new Map([[window.__midiIn.id, window.__midiIn]]), onstatechange: null,
});
window.__midi = (bytes) => window.__midiIn.onmidimessage(
  { data: new Uint8Array(bytes), target: window.__midiIn });
"""

_CENTRE = """() => {
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


def _wait(page, check, what: str, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = check()
        if got:
            return got
        page.wait_for_timeout(40)
    raise AssertionError(f"never: {what}")


def _open(studio, server, **patch):
    server.state.patch = replace(Patch(), **patch)
    page = studio.open("editor")
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    return page


def _reads(page, js: str, n: int = 6) -> float:
    vals = []
    for _ in range(n):
        vals.append(page.evaluate(js))
        page.wait_for_timeout(50)
    return sum(vals) / len(vals)


def _held(studio, js: str = _CENTRE) -> float:
    """A held C4's reading (the spectral centre by default) once it sounds."""
    page = studio.page
    studio.pointer_down(C4)
    studio.wait_sound(floor=1e-3)
    page.wait_for_timeout(200)
    got = _reads(page, js)
    studio.pointer_up()
    studio.wait_silence()
    return got


def _level(studio) -> float:
    studio.pointer_down(C4)
    studio.wait_sound(floor=1e-4)
    studio.page.wait_for_timeout(200)
    got = studio.level()
    studio.pointer_up()
    studio.wait_silence()
    return got


def _patch_follows(page, field: str, value) -> None:
    _wait(
        page,
        lambda: page.evaluate(f"() => window.synthlab.piano.patch().{field}") == value,
        f"the keys' patch has {field} = {value}",
    )


def test_filter_2_serial_then_parallel(studio, server):
    page = _open(studio, server, filter_type="lowpass", cutoff=400.0, filter_env=0.0)
    block = page.locator(".f2-block")
    assert block.is_visible()
    assert not page.locator("#row-filter2_cutoff").is_visible()  # off: only its switch
    drawn = page.get_attribute("#filter-response polyline", "points")

    page.click('#row-filter2_route .seg label:has(input[value="serial"])')
    _wait(page, lambda: server.state.patch.filter2_route == "serial", "serial posted")
    assert page.locator("#row-filter2_cutoff").is_visible()
    page.select_option("#p-filter2_type", "highpass")
    _wait(
        page, lambda: server.state.patch.filter2_type == "highpass", "the type posted"
    )
    _patch_follows(page, "filter2_type", "highpass")
    _wait(
        page,
        lambda: page.get_attribute("#filter-response polyline", "points") != drawn,
        "the filter drawing follows filter 2",
    )
    serial = _level(studio)

    page.click('#row-filter2_route .seg label:has(input[value="parallel"])')
    _wait(
        page, lambda: server.state.patch.filter2_route == "parallel", "parallel posted"
    )
    _patch_follows(page, "filter2_route", "parallel")
    parallel = _level(studio)
    # the low-pass into the high-pass leaves the gap between them; side by
    # side, each one's band is there
    assert parallel > serial * 1.5, (serial, parallel)
    assert "Low 12" in page.inner_text("#signal-chain")


def test_a_drawn_lfo_moves_its_route(studio, server):
    # LFO 1 so slow that a held note hears where its drawing starts
    page = _open(
        studio,
        server,
        filter_type="lowpass",
        cutoff=900.0,
        filter_env=0.0,
        mod_lfo_rate=0.05,
        mod1_src="lfo",
        mod1_dst="cutoff",
        mod1_amt=0.6,
    )
    assert not page.locator("#draw-lfo").is_visible()
    page.select_option("#p-lfo_shape", "drawn")
    _wait(page, lambda: server.state.patch.lfo_shape == "drawn", "drawn posted")
    assert page.locator("#draw-lfo").is_visible()

    page.click(
        '#draw-lfo [data-draw="0:-1,0.99:1"]'
    )  # a ramp up: it starts at the bottom
    _wait(page, lambda: server.state.patch.lfo_draw == "0:-1,0.99:1", "the ramp posted")
    _patch_follows(page, "lfo_draw", "0:-1,0.99:1")
    low = _held(studio)

    # drag the first point to the top: the drawing starts there now
    box = page.locator("#draw-lfo .lfo-draw-svg").bounding_box()
    page.mouse.move(box["x"] + 1, box["y"] + box["height"] - 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 2, box["y"] + 3, steps=4)
    page.mouse.up()
    _wait(
        page,
        lambda: float(server.state.patch.lfo_draw.split(",")[0].split(":")[1]) > 0.8,
        "the drag posted",
    )
    _patch_follows(page, "lfo_draw", server.state.patch.lfo_draw)
    high = _held(studio)
    assert high > low * 1.3, (low, high)
    # two points, each with its handle
    assert page.locator("#draw-lfo .lfo-draw-pt").count() == 2


def test_routes_five_to_twelve_open_one_by_one(studio, server):
    page = _open(studio, server)
    extra = page.locator("#mod-matrix .route[data-extra]")
    assert extra.count() == 8
    assert all(not extra.nth(i).is_visible() for i in range(8))
    for k in range(5, 13):
        page.click("#mod-matrix .route-add")
        row = page.locator(f'#mod-matrix .route[data-slot="mod{k}"]')
        assert row.is_visible(), k
    page.click("#mod-matrix .route-add")  # none left to open
    page.wait_for_selector("#toasts .toast.error")

    # route 7: LFO 2 to the wavetable position, posted like any route
    page.select_option("#p-mod7_src", "lfo2")
    page.select_option("#p-mod7_dst", "resonance")
    page.focus("#p-mod7_amt")
    page.keyboard.press("End")
    _wait(
        page,
        lambda: (
            (
                server.state.patch.mod7_src,
                server.state.patch.mod7_dst,
                server.state.patch.mod7_amt,
            )
            == ("lfo2", "resonance", 1.0)
        ),
        "route 7 posted",
    )
    # its lead is in the bay (bench.js draws every active route)
    page.wait_for_selector('#patchbay .cable[data-slot="mod7"]')
    # the Modulators panel holds LFO 2's controls
    assert page.locator("#panel-modulators #row-lfo2_rate").is_visible()


def test_a_mod_wheel_route_from_a_midi_port(studio, server):
    studio.page.add_init_script(_FAKE_MIDI)
    page = _open(
        studio,
        server,
        filter_type="lowpass",
        cutoff=250.0,
        filter_env=0.0,
        mod1_src="wheel",
        mod1_dst="cutoff",
        mod1_amt=0.9,
    )
    studio.start_voice()
    studio.wait_silence()
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').textContent.includes('MIDI 1')"
    )
    page.evaluate("window.__midi([0x90, 60, 110])")
    studio.wait_sound(floor=1e-3)
    page.wait_for_timeout(200)
    shut = _reads(page, _CENTRE)
    page.evaluate("window.__midi([0xb0, 1, 127])")  # the wheel all the way up
    page.wait_for_timeout(250)
    open_ = _reads(page, _CENTRE)
    page.evaluate("window.__midi([0x80, 60, 0])")
    studio.wait_silence()
    assert open_ > shut * 1.3, (shut, open_)
    # a route reads the wheel: CC 1 is its source, not the old cutoff sweep
    assert page.evaluate("() => window.synthlab.piano.patch().cutoff") == 250.0
