"""A take with the keys arp on, in Chromium: it lands on the grid it was
played on, every row of it shows, and "the keys I held" plays back through
the grid arp.

What these journeys prove: ⏺ Rec with nothing playing (count-in, two bars)
and the keys arp at 1/8T over three octaves — the arp counts its steps from
the take's own bar lines (keyrec.js S.keyRecGrid), so the take's loop lands
on 12 steps a bar (`loops.pick_steps`, said in the take's toast) with every
note on its line, and the roll opens on every row the notes use (span 4).
With "the keys I held" (the deck's Rec ▾, [data-key-arp]) the take keeps
the chord held, its loop the grid arp set as the keys arp was, and the
loop's schedule (`arrange.note_schedule`: what its render plays) is the
notes the keys arp sent the live voice — the same pitches, at the same
times within a render quantum (the first of a run within the 30 ms a late
press sounds at once). And the draw cost of a 16-bar grid over five
octaves. The notes are read off the voice's own port; nobody has listened.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

from synthlab.core import SR
from synthlab.sequencer import midi as midi_of
from synthlab.sequencer import name_of
from synthlab.studio import loops
from synthlab.studio.arrange import note_schedule
from synthlab.studio.state import model as state_model
from synthlab.studio.state.model import SPAN_NOTES, Arrangement
from tests.browser.test_key_takes import _take
from tests.browser.test_keys_arp import HOOK, NOTES
from tests.browser.test_take_feel import _grid_ms

BPM = 120.0
BEAT = 60.0 / BPM
QUANTUM = 128 / SR  # s


def _bench(studio, server, keys: str):
    s = server.state
    s.arrangement = replace(s.arrangement, bpm=BPM, root="A1", scale_name="minor")
    studio.page.context.add_init_script(HOOK)
    page = studio.open("perform")
    prefs = {
        "synthlab.keyRecBars": "2",
        "synthlab.keyRecCount": "1",
        "synthlab.keyRecArp": keys,
        "synthlab.arp": json.dumps(
            {
                "on": False,
                "mode": "up",
                "rate": "1/8T",
                "oct": 3,
                "gate": 0.5,
                "swing": 0,
            }
        ),
    }
    page.evaluate(
        "(p) => Object.entries(p).forEach(([k, v]) => localStorage.setItem(k, v))",
        prefs,
    )
    page.reload()
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    studio.start_voice()
    page.click("#arp-btn")
    assert page.get_attribute("#arp-btn", "aria-pressed") == "true"
    return page


def _record(page, hold_ms: int = 3200):
    """⏺ Rec (count-in, two bars from silence): once it records, hold C E G
    for `hold_ms`; the take's answer and the take's bar grid (ref, bar)."""
    page.click("#deck-rec")
    page.wait_for_function(
        "() => document.getElementById('deck-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    grid = page.evaluate("() => window.synthlab.keyRecGrid()")
    page.evaluate("() => { window.__arp = []; }")
    page.locator(".view-head h1").click()
    with page.expect_response("**/take/keys", timeout=30000) as answer:
        for k in ("a", "d", "g"):  # C4 E4 G4
            page.keyboard.down(k)
        page.wait_for_timeout(hold_ms)
        for k in ("a", "d", "g"):
            page.keyboard.up(k)
    return answer.value, grid


def _loop(server, answer):
    from urllib.parse import unquote

    return loops.load(server.state.data_dir, unquote(answer.headers["x-loop-name"]))


def test_an_arp_take_lands_on_its_triplets_with_every_row_shown(studio, server):
    page = _bench(studio, server, "notes")
    answer, grid = _record(page)
    assert answer.ok and grid and abs(grid["bar"] - 4 * BEAT) < 1e-6
    from urllib.parse import unquote

    assert unquote(answer.headers["x-loop-rate"]) == "on 1/8T (12 a bar)"
    meta, _pcm, _rate = _take(server)
    loop = _loop(server, answer)
    lane = loop["lane"]
    assert lane["steps_per_bar"] == 12
    # the arpeggio, not the chord: C E G up three octaves
    assert len(meta["notes"]) >= 12 and len({n[2] for n in meta["notes"]}) == 9
    # every note on its line (the take's stamps to a thousandth of a beat)
    worst = max(abs(f[0]) for f in lane["note_feel"].values())
    print(f"\nARP TAKE worst start off its 1/8T line: {worst:.4f} of a step")
    assert worst <= 0.01, lane["note_feel"]
    # Edit: the roll opens on every row the notes use (three octaves: no
    # 24-row window holds them)
    r = page.request.post(
        f"http://127.0.0.1:{server.port}/loop/edit",
        form={"name": loop["name"]},
        headers={"HX-Request": "true"},
    )
    assert r.ok and server.state.grid_span == SPAN_NOTES
    page.goto(f"http://127.0.0.1:{server.port}/view/perform")
    page.wait_for_selector(".grid.roll")
    rows = page.evaluate(
        "() => [...document.querySelectorAll('.grid.roll .gridrow [data-pitch]')]"
        ".map((x) => x.dataset.pitch)"
    )
    assert {name_of(n[2]) for n in meta["notes"]} <= set(rows), rows
    # the roll scrolls to the lowest row: it is in the pane, not hidden
    page.locator(".grid.roll .gridrow").last.scroll_into_view_if_needed()
    assert page.locator(".grid.roll .gridrow").last.is_visible()
    assert studio.unexplained() == []


def test_the_keys_i_held_play_back_through_the_grid_arp(studio, server):
    page = _bench(studio, server, "held")
    answer, grid = _record(page)
    assert answer.ok and grid
    loop = _loop(server, answer)
    lane = loop["lane"]
    # the chord held, not the arpeggio; the grid arp set as the keys arp
    assert sorted(n[2] for n in loop["played"]) == [60, 64, 67]
    assert lane["arp_mode"] == "pattern"
    pat = lane["arp_pattern"]
    assert pat["held"] == "plain" and pat["rate"] == "1/8T" and pat["oct"] == 3
    # what the keys arp sent the voice, in the take's beats
    sent = [
        ((n["at"] - grid["ref"]) / BEAT, 69 + 12 * math.log2(n["freq"] / 440.0))
        for n in page.evaluate(NOTES, "noteOn")
        if n["freq"]
    ]
    sent = sorted((t, round(m)) for t, m in sent if -0.1 < t < 8.0)
    arr = Arrangement(meter=loop["meter"], **lane, **loop["key"])
    plays = sorted((at, midi_of(p)) for at, _d, p, _v, _s in note_schedule(arr)[0])
    print(f"\nHELD sent {len(sent)} notes, the loop plays {len(plays)}")
    # the run's last step may fall either side of the keys' release (the
    # pump reads 40-60 ms ahead): every other note the same
    assert abs(len(sent) - len(plays)) <= 1, (sent, plays)
    for i, ((t0, m0), (t1, m1)) in enumerate(zip(sent, plays, strict=False)):
        assert m0 == m1, (i, sent, plays)
        tol = 0.03 + QUANTUM if i == 0 else QUANTUM  # a late first press: at once
        assert abs(t0 - t1) * BEAT <= tol, (i, t0, t1)
    gaps = [abs(a[0] - b[0]) * BEAT * 1000 for a, b in zip(sent, plays, strict=False)]
    print(f"HELD first {gaps[0]:.2f} ms apart, the rest at most {max(gaps[1:]):.4f} ms")
    assert studio.unexplained() == []


def test_a_16_bar_grid_over_five_octaves_costs(studio, server, monkeypatch):
    """A 16-bar 1/16 take from A3 to F7, every note felt: the roll's style
    and layout redone whole (test_take_feel's measure) on span 3 (24
    chromatic rows: most notes hidden), span 4 (the key's rows from the
    lowest note to the highest, and any out of key) and — for the choice —
    the full chromatic five octaves. Printed for the report."""
    import random

    rng = random.Random(3)
    notes, t = [], 0.0
    while t < 63.5:
        m = rng.choice([57, 60, 64, 69, 72, 76, 81, 84, 88, 93, 96, 100, 101, 70])
        notes.append([round(t + rng.uniform(0, 0.04), 3), 0.2, m, 0.8])
        t += 0.25
    lane = loops.place(notes, "4/4", 16, 16, 0)
    s = server.state
    fields = {k: v for k, v in lane.items() if k != "meter"}
    s.arrangement = replace(s.arrangement, **fields, root="A1", scale_name="minor")
    out = {}

    def chromatic(arr, cells=None):
        held = sorted(
            {midi_of(p) for p, _st in (arr.cells if cells is None else cells)}
        )
        return [name_of(m) for m in range(held[-1] + 4, held[0] - 1, -1)]

    for label, span, patch in (
        ("span 3 (24 rows)", 3, None),
        ("span 4 (key rows + held)", SPAN_NOTES, None),
        ("chromatic five octaves", SPAN_NOTES, chromatic),
    ):
        if patch:
            monkeypatch.setattr(state_model, "note_rows", patch)
        s.grid_span = span
        r = studio.page.request.get(f"http://127.0.0.1:{server.port}/view/perform")
        page = studio.open("perform")
        page.wait_for_selector(".grid.roll")
        rows = page.locator(".grid.roll .gridrow[aria-label$=' row']").count()
        out[label] = {
            "ms": round(_grid_ms(page), 1),
            "cells": page.locator(".grid.roll .cell").count(),
            "rows": rows,
            "page_kb": round(len(r.body()) / 1024, 1),
        }
    print("\nFIVE OCTAVES", json.dumps(out))
    key, chrom = out["span 4 (key rows + held)"], out["chromatic five octaves"]
    assert key["cells"] < chrom["cells"]
