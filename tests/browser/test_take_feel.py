"""A take as played, in Chromium: what the dock reports as each note's END
(the hold / latch, the chord modes, the strum), a real ⏺ Rec whose loop
keeps every press's own feel and the bend and a knob turned during it, the
roll drawing that feel, and what drawing it costs on a 16-bar grid.

What these journeys prove: piano.js tells keyrec.js (`S.keyNote`) a note
ends when its SOUND ends — the key up, the hold's short gate with hold off,
the latch let go, the chord's notes together however strummed — so a take
records what was heard; a take's loop opens as played, its cells carrying
each note's feel, its sound the bend and the cutoff as curves; the roll's
start cell sits off its line. Nobody has listened.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import replace

from synthlab.studio import loops
from tests.browser.test_key_takes import _bench, _loop_playing, _take

LOG = """() => { const S = window.synthlab; window.__ends = [];
  const was = S.keyNote;
  S.keyNote = (id, midi, vel, at, own) => {
    window.__ends.push([S.getCtx().currentTime, String(id), midi ?? null]);
    return was(id, midi, vel, at, own);
  }; }"""


def _log(page) -> list:
    return page.evaluate("() => window.__ends.splice(0)")


def _ons_offs(log):
    ons = {i: t for t, i, m in log if m is not None}
    offs = {i: t for t, i, m in log if m is None}
    return ons, offs


def test_the_dock_reports_a_note_ending_where_its_sound_ends(studio):
    page = _bench(studio)
    page.evaluate(LOG)
    piano = "window.synthlab.piano"
    # a key held 300 ms: its end is its key up
    page.evaluate(f"() => {piano}.noteOn('k1', 60, 0.8)")
    page.wait_for_timeout(300)
    page.evaluate(f"() => {piano}.noteOff('k1')")
    ons, offs = _ons_offs(_log(page))
    (tok,) = ons
    assert 0.25 < offs[tok] - ons[tok] < 0.45
    # the latch: the note holds after its key is up, until the latch goes
    page.click("#latch-btn")
    page.evaluate(f"() => {piano}.noteOn('k2', 62, 0.8)")
    page.wait_for_timeout(100)
    page.evaluate(f"() => {piano}.noteOff('k2')")
    page.wait_for_timeout(300)
    ons, offs = _ons_offs(_log(page))
    assert len(ons) == 1 and not offs  # still sounding: no end yet
    page.click("#latch-btn")
    _ons, offs = _ons_offs(_log(page))
    assert len(offs) == 1  # let go: now it ends
    # a strummed chord: its notes start apart and end together
    page.evaluate(
        """() => { const s = document.getElementById('kbd-strum');
          s.value = '40'; s.dispatchEvent(new Event('input', {bubbles: true})); }"""
    )
    page.evaluate("() => window.synthlab.plays.chordOn('c1', [60, 64, 67], 0.7)")
    page.wait_for_timeout(400)
    page.evaluate("() => window.synthlab.plays.chordOff('c1')")
    ons, offs = _ons_offs(_log(page))
    starts = sorted(ons.values())
    assert len(starts) == 3 and 0.06 < starts[-1] - starts[0] < 0.2
    assert max(offs.values()) - min(offs.values()) < 0.02
    # the chord modes: a key plays the chord (3, 4, 5 notes), all ending at
    # its key up
    for mode, size in (("triad", 3), ("7th", 4), ("9th", 5)):
        if mode not in page.evaluate("() => window.synthlab.plays.list()"):
            continue
        page.evaluate(f"() => window.synthlab.plays.set('{mode}')")
        page.locator(".view-head h1").click()
        page.keyboard.down("KeyA")
        page.wait_for_timeout(250)
        page.keyboard.up("KeyA")
        page.wait_for_timeout(100)
        ons, offs = _ons_offs(_log(page))
        # (as many tones as the key's rows give the voicing: a 7th or a 9th
        # may come out with fewer under a pentatonic key)
        assert 3 <= len(ons) <= size and len(ons) == len(offs), (mode, ons, offs)
        assert max(offs.values()) - min(offs.values()) < 0.02
    page.evaluate("() => window.synthlab.plays.set('note')")
    # hold off: a note is gated short (piano.js GATE_MS) — its end is there
    page.evaluate("() => localStorage.setItem('synthlab.hold', '0')")
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()
    page.evaluate(LOG)
    page.evaluate(f"() => {piano}.noteOn('k3', 64, 0.8)")
    page.wait_for_timeout(600)
    ons, offs = _ons_offs(_log(page))
    (tok,) = ons
    # (never let go by a key: the gate ended it; a fresh page's timers lag)
    assert 0.1 < offs[tok] - ons[tok] < 0.5
    assert studio.unexplained() == []


def test_a_take_keeps_each_press_and_the_bend_and_knob_turned(studio, server):
    page = _bench(studio, bars="1")
    _loop_playing(page)
    page.click("#key-rec")
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    page.keyboard.down("KeyA")
    page.wait_for_timeout(330)
    page.evaluate("() => window.synthlab.piano.wheel(0.6)")  # the bend
    page.wait_for_timeout(120)
    page.evaluate("() => window.synthlab.piano.wheel(0)")
    page.keyboard.up("KeyA")
    # a knob turned through the one write (a deck dial, a MIDI CC, a typed
    # value: synthlab.tweak), if the cutoff is on this page
    turned = page.evaluate(
        """() => { const el = document.getElementById('p-cutoff');
          if (!el) return false;
          window.synthlab.tweak(el, +el.min + (+el.max - +el.min) * 0.8);
          return true; }"""
    )
    page.keyboard.down("KeyD")
    page.wait_for_timeout(180)
    page.keyboard.up("KeyD")
    with page.expect_response("**/take/keys", timeout=25000) as answer:
        pass
    assert answer.value.ok
    meta, _pcm, _rate = _take(server)
    name = loops.of_take(server.data_dir, meta["name"])
    loop = loops.load(server.data_dir, name)
    assert loop["quantise"] == 0  # as played, unasked
    feel = loop["lane"]["note_feel"]
    assert len(feel) == len(loop["played"]) >= 2  # every press its own
    _patch, extras = loop["sound"]
    assert "pitch_bend" in extras["curves"]
    if turned:
        assert "cutoff" in extras["curves"]
    # Edit: the roll draws each note's feel on its cells
    r = page.request.post(
        f"http://127.0.0.1:{server.port}/loop/edit",
        form={"name": name},
        headers={"HX-Request": "true"},
    )
    assert r.ok
    page.goto(f"http://127.0.0.1:{server.port}/view/perform")
    page.wait_for_selector(".grid.roll .cell.felt-s")
    shifted = page.evaluate(
        """() => [...document.querySelectorAll('.grid.roll .cell.felt-s')]
          .map((c) => getComputedStyle(c).translate)"""
    )
    assert shifted and any(t not in ("none", "0px") for t in shifted), shifted
    assert studio.unexplained() == []


def _grid_ms(page) -> float:
    return page.evaluate(
        """() => { const g = document.querySelector('.grid.roll'), out = [];
          for (let i = 0; i < 7; i++) {
            const t0 = performance.now();
            g.style.display = 'none'; g.offsetHeight;
            g.style.display = ''; g.offsetHeight;
            out.push(performance.now() - t0);
          }
          out.sort((a, b) => a - b); return out[3]; }"""
    )


def test_drawing_the_feel_on_a_16_bar_grid_costs_little(studio, server):
    """A 16-bar 1/16 take on the chromatic rows, every note felt: the
    roll's style and layout redone whole, against the same grid placed on
    the steps (no feel). Printed for the report; held to 1.5x + 5 ms."""
    import random

    rng = random.Random(1)
    notes, t = [], 0.0
    while t < 63:
        for m in (57, 60, 64):
            notes.append(
                [
                    round(t + rng.uniform(0, 0.05), 3),
                    round(rng.uniform(0.3, 1.8), 3),
                    m + rng.choice((0, 2)),
                    round(rng.uniform(0.4, 1), 3),
                ]
            )
        t += rng.choice((0.5, 1, 1.5))
    timed = loops.place(notes, "4/4", 16, 16, 0)
    plain = loops.place(notes, "4/4", 16, 16, 100)
    s = server.state
    out = {}
    for label, lane in (("on the steps", plain), ("as played", timed)):
        fields = {k: v for k, v in lane.items() if k != "meter"}
        s.arrangement = replace(s.arrangement, **fields, root="A3", scale_name="minor")
        s.grid_span = 3
        page = studio.open("perform")
        page.wait_for_selector(".grid.roll")
        out[label] = (_grid_ms(page), page.locator(".grid.roll .cell").count())
    print("\nGRID COST", json.dumps(out), "felt notes", len(timed["note_feel"]))
    (plain_ms, cells), (felt_ms, cells2) = out["on the steps"], out["as played"]
    assert cells == cells2
    assert felt_ms < plain_ms * 1.5 + 5, out
    assert statistics.fmean([plain_ms, felt_ms]) > 0
