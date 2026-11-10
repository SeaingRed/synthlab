"""The keys arp in Chromium (static/arp.js): a chord held on the
keyboard dock plays as an arpeggio, on the tempo and on the loop's grid.

What these journeys prove: a held triad, arp on at 1/8 and 120 bpm, sends
the live voice its notes one at a time, low to high, each noteOn posted
AHEAD with the context time it is due (`at`) — 0.25 s apart, and with the
loop playing on its grid (a whole number of steps from the loop's start);
the worklet starts a due note at the block nearest that time
(`tests/studio/test_keys_arp.py` runs that half under node), so the notes
land on the grid within half a render quantum whatever the main thread's
timers do. The held keys are ringed (`.arped`), the read-out names the
chord and the mode ("C ↑"); latch + arp keeps running with the keys up and
stops with ■ or Shift+Space; ⏺ Rec takes the arpeggio's notes as they
sound; the switch, its face and its panel never move the toolbar, and at
1920 px the toolbar is one row in both densities. The notes are read off
the voice's own port (every MessagePort's postMessage, wrapped before the
page loads) — nobody has listened: the feel of the grid and the gate are
unchecked.
"""

from __future__ import annotations

from dataclasses import replace

from tests.browser.test_key_takes import _take

# every arp note message the live voice is sent, with the context's time
# it was posted at
HOOK = """(() => {
  const post = MessagePort.prototype.postMessage;
  window.__arp = [];
  MessagePort.prototype.postMessage = function (m, ...rest) {
    // (the arp's notes: the ones with a time, `at`; the id is the voice token)
    if (m && (m.type === "noteOn" || m.type === "noteOff") && m.at)
      window.__arp.push({ type: m.type, id: m.id, at: m.at, freq: m.freq ?? null,
        posted: window.synthlab?.getCtx?.()?.currentTime ?? 0 });
    return post.call(this, m, ...rest);
  };
})();"""

# the notes the voice was sent (one a note: a lane each would repeat it)
NOTES = """(type) => { const seen = new Set();
  return window.__arp.filter((n) => n.type === type && !seen.has(n.id)
    && seen.add(n.id)); }"""

C4, E4, G4 = 261.63, 329.63, 392.0


def _bench(studio, server, bpm: float = 120.0, prefs: dict | None = None):
    s = server.state
    s.arrangement = replace(s.arrangement, bpm=bpm)
    studio.page.context.add_init_script(HOOK)
    page = studio.open("editor")
    if prefs:  # this browser's settings (localStorage), then the page again
        page.evaluate(
            "(p) => Object.entries(p).forEach(([k, v]) => localStorage.setItem(k, v))",
            prefs,
        )
        page.reload()
        page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    studio.start_voice()
    page.evaluate("() => { window.__arp = []; }")
    page.click("#arp-btn")
    assert page.get_attribute("#arp-btn", "aria-pressed") == "true"
    return page


def _triad_down(page):
    for k in ("a", "d", "g"):  # C4 E4 G4 (the classic split)
        page.keyboard.down(k)


def _triad_up(page):
    for k in ("a", "d", "g"):
        page.keyboard.up(k)


def _pitch(freq: float) -> str:
    return min((("C", C4), ("E", E4), ("G", G4)), key=lambda x: abs(x[1] - freq))[0]


def test_a_held_triad_arpeggiates_up_at_an_eighth_in_time(studio, server):
    page = _bench(studio, server)
    _triad_down(page)
    page.wait_for_timeout(1300)
    # the keys held are ringed (the arp holds them), the read-out names it
    assert page.locator("#piano .pkey.arped").count() == 3
    assert page.locator("#kbd-now").text_content() == "C ↑"
    _triad_up(page)
    page.wait_for_timeout(400)
    ons = page.evaluate(NOTES, "noteOn")
    assert len(ons) >= 5, ons
    assert "".join(_pitch(n["freq"]) for n in ons[:6]).startswith("CEGCE")
    ats = [n["at"] for n in ons]
    # the first sounds at once (the press is its step, a block or two on);
    # then an eighth at 120 bpm apart, exactly, on that step's grid
    assert 0.24 < ats[1] - ats[0] <= 0.25 + 1e-6, ats
    for a, b in zip(ats[1:], ats[2:], strict=False):
        assert abs(b - a - 0.25) < 1e-6, ats
    # each posted ahead of its time: the worklet holds it to its block
    assert all(n["at"] - n["posted"] > 0.004 for n in ons[1:]), ons
    offs = page.evaluate(NOTES, "noteOff")
    by = {n["id"]: n["at"] for n in offs}
    for n in ons:  # gate 50 %: each ends half a step on
        assert abs(by[n["id"]] - n["at"] - 0.125) < 1e-6
    # keys up: it stops, nothing else is sent, no voice is left
    assert not page.evaluate("() => window.synthlab.arp.running()")
    count = len(page.evaluate(NOTES, "noteOn"))
    page.wait_for_timeout(500)
    assert len(page.evaluate(NOTES, "noteOn")) == count
    studio.wait_meter(r"0 voices")
    assert page.locator("#piano .pkey.arped, #piano .pkey.down").count() == 0


def test_with_the_loop_playing_it_lands_on_the_loops_grid(studio, server):
    page = _bench(studio, server)
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    page.wait_for_timeout(330)  # somewhere between two steps
    _triad_down(page)
    page.wait_for_timeout(1100)
    _triad_up(page)
    start = page.evaluate("() => window.synthlab.loopStart()")
    ons = page.evaluate(NOTES, "noteOn")
    assert len(ons) >= 4, ons
    for n in ons[1:]:
        steps = (n["at"] - start) / 0.25
        assert abs(steps - round(steps)) < 1e-4, (n, start)  # on the loop's eighths
    # the first: on its step, or — struck up to 30 ms late — at once
    late = (ons[0]["at"] - start) % 0.25
    assert late < 0.03 + 1e-4 or late > 0.25 - 1e-4, (ons[0], start)
    assert all(n["at"] - n["posted"] > 0.004 for n in ons[1:]), ons


def test_latch_and_arp_keep_running_until_stop(studio, server):
    page = _bench(studio, server)
    for stop in ("■", "Shift+Space"):
        page.click("#latch-btn")
        _triad_down(page)
        page.wait_for_timeout(60)
        _triad_up(page)
        page.wait_for_timeout(900)  # the keys are up: it runs on
        assert page.evaluate("() => window.synthlab.arp.running()")
        assert page.evaluate("() => window.synthlab.latch().notes") == 3
        before = len(page.evaluate(NOTES, "noteOn"))
        page.wait_for_timeout(600)
        assert len(page.evaluate(NOTES, "noteOn")) >= before + 2
        assert page.locator("#piano .pkey.arped").count() == 3
        if stop == "■":
            page.click('[data-cmd="Stop everything"]')
        else:
            page.locator(".view-head h1").click()  # (the keyboard, off any button)
            page.keyboard.press("Shift+Space")
        page.wait_for_function("() => !window.synthlab.arp.running()")
        count = len(page.evaluate(NOTES, "noteOn"))
        page.wait_for_timeout(500)
        assert len(page.evaluate(NOTES, "noteOn")) == count, stop
        assert page.evaluate("() => window.synthlab.latch().notes") == 0
        assert page.locator("#piano .pkey.arped").count() == 0
        studio.wait_meter(r"0 voices")


def test_a_note_over_a_latched_arpeggio_plays_over_it(studio, server):
    """latch + arp: the arpeggio runs on its own, and a key played over it
    sounds as a note (it does not join); the arp switched off leaves the
    latched chord sounding, a drone."""
    page = _bench(studio, server)
    page.click("#latch-btn")
    _triad_down(page)
    page.wait_for_timeout(60)
    _triad_up(page)
    page.wait_for_timeout(400)
    page.keyboard.down("j")  # B4, over it
    page.wait_for_timeout(200)
    assert page.locator('#piano .pkey[data-note="11"][data-rel="0"].down').count() == 1
    assert len(page.evaluate("() => window.synthlab.piano.arpHeld()")) == 3
    page.keyboard.up("j")
    page.click("#arp-btn")  # off: the latched chord sounds as it is held
    assert not page.evaluate("() => window.synthlab.arp.running()")
    assert page.locator("#piano .pkey.down.latched").count() == 3
    page.click("#latch-btn")
    studio.wait_meter(r"0 voices")


def test_rec_takes_the_arpeggio_as_it_sounds(studio, server):
    # ⏺ Rec until stopped, no count-in: it starts at once (nothing plays)
    page = _bench(
        studio, server, prefs={"synthlab.keyRecBars": "0", "synthlab.keyRecCount": "0"}
    )
    page.click("#key-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    page.locator(".view-head h1").click()
    _triad_down(page)
    page.wait_for_timeout(1400)
    _triad_up(page)
    page.wait_for_timeout(200)
    with page.expect_response("**/take/keys", timeout=20000) as answer:
        page.click("#key-rec")
    assert answer.value.ok
    meta, _pcm, _rate = _take(server)
    notes = sorted(meta["notes"])
    assert [n[2] for n in notes[:4]] == [60, 64, 67, 60], (
        notes
    )  # the arpeggio, not the chord
    for a, b in zip(notes, notes[1:], strict=False):
        assert abs(b[0] - a[0] - 0.5) < 0.02, notes  # an eighth: half a beat
    assert all(abs(n[1] - 0.25) < 0.02 for n in notes), notes  # half a step long


def test_print_puts_what_the_arp_played_in_a_loop(studio, server):
    """The arp panel's ⤓ print: what the keys arp just played —
    never Rec'd — as a loop of its own notes; a note played with the arp off
    is not the arp's and stays out."""
    from urllib.parse import unquote

    from synthlab.studio import loops

    page = _bench(studio, server)
    page.click("#arp-btn")  # off: B4 is a note of its own
    page.locator(".view-head h1").click()
    page.keyboard.down("j")
    page.wait_for_timeout(200)
    page.keyboard.up("j")
    page.wait_for_timeout(2500)  # a bar of silence: the arp's is a phrase apart
    page.click("#arp-btn")
    page.locator(".view-head h1").click()
    _triad_down(page)
    page.wait_for_timeout(1400)
    _triad_up(page)
    page.wait_for_timeout(200)
    page.click(".arp-set")
    page.wait_for_selector("#arp-pop:popover-open")
    with page.expect_response("**/take/keys/capture") as answer:
        page.click("#arp-print")
    assert answer.value.ok
    loop = loops.load(server.data_dir, unquote(answer.value.headers["x-loop-name"]))
    notes = sorted(loop["played"])
    assert loop["take"] is None and 71 not in [n[2] for n in notes]
    assert [n[2] for n in notes[:4]] == [60, 64, 67, 60], notes
    assert notes[0][0] == 0  # from silence: its first note is the downbeat
    for a, b in zip(notes, notes[1:], strict=False):
        assert abs(b[0] - a[0] - 0.5) < 0.02, notes  # an eighth: half a beat
    assert loop["lane"]["bars"] == 1  # 1.4 s at 120 bpm: a bar
    assert studio.unexplained() == []


def test_the_arp_switch_and_panel_never_move_the_toolbar(studio, server):
    page = _bench(studio, server)
    page.click("#arp-btn")  # off again
    box = """() => [...document.querySelectorAll('.kbd-bar > .kbd-grp')].map((g) => {
      const r = g.getBoundingClientRect();
      return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; })"""
    before = page.evaluate(box)
    page.click(".arp-set")
    page.wait_for_selector("#arp-pop:popover-open")
    for mode in ("down", "updown", "random", "played", "chord", "up"):
        for rate in ("1/4", "1/8T", "1/16T", "1/32", "1/8"):
            page.click(f'#arp-pop [data-arp-mode="{mode}"]')
            page.click(f'#arp-pop [data-arp-rate="{rate}"]')
            assert page.evaluate(box) == before, (mode, rate)
    page.click('#arp-pop [data-arp-oct="3"]')
    assert page.evaluate("() => window.synthlab.arp.get()")["oct"] == 3
    assert page.locator("#arp-face").inner_text() == "↑ 1/8"
    page.click("#arp-btn")
    assert page.evaluate(box) == before


def test_the_toolbar_is_one_row_at_1920(chromium, server):
    from synthlab.studio import loops

    # (on the Loops page a filled slot selected shows #key-how's
    # overdub | replace, its widest)
    s = server.state
    with s.lock:
        loops.save(server.data_dir, "riff", s.arrangement, s.sound_payload())
        s.set_slot(0, 0, "riff")
    for density in ("comfy", "compact"):
        ctx = chromium.new_context(viewport={"width": 1920, "height": 1080})
        ctx.add_cookies([{"name": "density", "value": density, "url": server.url}])
        ctx.add_init_script(
            "sessionStorage.setItem('synthlab.slotSel', JSON.stringify(['0', '0']))"
        )
        page = ctx.new_page()
        for view in ("editor", "perform"):
            page.goto(f"{server.url}/view/{view}")
            page.wait_for_selector(f'#view[data-view="{view}"]')
            if view == "perform":
                page.wait_for_selector("#key-how:not([hidden])")
            tops = page.evaluate(
                """() => [...document.querySelectorAll('.kbd-bar > .kbd-grp')]
                  .map((g) => { const r = g.getBoundingClientRect();
                    return [r.top, r.bottom]; })"""
            )
            # one row: every group overlaps every other vertically
            assert max(t for t, _ in tops) < min(b for _, b in tops), (
                density,
                view,
                tops,
            )
        ctx.close()
