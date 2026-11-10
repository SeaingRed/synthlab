"""Perform's chord lane in Chromium: the working loop's chord
track over the roll, `POST /fragment/chords/track`.

What these journeys prove: "⚄ chords" on an empty lane rolls a
progression in and its tiles appear over the roll with the chord tones
under them; a tile's click opens its menu (the Song page's tiles' way:
songline.js -> menu.js) and a degree there changes it; a focused tile's
1-7 sets its degree; "⚄ write" writes a part over the lane's chords and,
after a lane edit, over the new ones (the first bar's notes are the new
chord's tones); with "follow the loop's chords" on and the loop playing,
the keys' chord mode plays the chord under the playhead and its pad is
lit. Read from the session and the page's own objects — nobody has
listened.
"""

from __future__ import annotations

from dataclasses import replace

from synthlab.sequencer import midi
from synthlab.studio.gen import chordtrack
from synthlab.studio.gen import loop as gl


def _perform(studio, server, fold: bool = True):
    s = server.state
    s.arrangement = replace(
        s.arrangement,
        bpm=120.0,
        root="A1",
        scale_name="minor",
        bars=4,
        steps_per_bar=16,
        cells=[],
    )
    s.grid_span = 2
    page = studio.open("perform")
    studio.settle()
    fold, want = page.locator("#keys-dock .keys-fold"), fold
    if (fold.get_attribute("aria-expanded") == "true") == want:
        fold.click()  # (folded: the roll, not the keys, under the hand)
    return page


def _first_bar_fits(server) -> bool:
    """Every note of the loop's first bar is a tone of its lane's first chord."""
    a = server.state.arrangement
    c = chordtrack.chord_at(
        chordtrack.resolve(gl.song_of(a), None, a.bars, a.steps_per_bar), 0
    )
    pcs = {(midi(a.root) + t) % 12 for t in c["tones"]}
    bar = [p for p, st in a.cells if int(st) < a.steps_per_bar]
    return bool(bar) and all(midi(p) % 12 in pcs for p in bar)


def test_a_chord_lane_edit_changes_what_write_writes(studio, server):
    page = _perform(studio, server)
    page.click(".chordrow .lchord-empty")  # ⚄ chords
    page.wait_for_selector(".chordrow .lctile")
    assert server.state.arrangement.chords["slots"]
    assert page.locator(".roll .cell.ct").count() > 0  # the roll's chord tones
    # write chords over them
    # (⚄ write opens a popover)
    page.click('.roll-gen .pt[popovertarget="ptm-write"]')
    page.wait_for_selector("#ptm-write:popover-open")
    with page.expect_response("**/fragment/gen"):
        page.click('#ptm-write [hx-vals*=\'"kind": "chords"\']')
    assert _first_bar_fits(server)
    first = server.state.arrangement.cells
    # the first tile another chord, from its menu
    now = sorted(server.state.arrangement.chords["slots"], key=lambda x: x["at"])[0][
        "deg"
    ]
    to = 4 if now != 4 else 5
    page.locator(".chordrow .lctile").first.click()
    page.wait_for_selector("#loop-chord-menu:popover-open")
    assert (
        page.get_attribute(f'#loop-chord-menu [data-is="deg-{now}"]', "aria-pressed")
        == "true"
    )
    with page.expect_response("**/fragment/chords/track"):
        page.click(f'#loop-chord-menu [data-is="deg-{to}"]')
    page.wait_for_function(
        "(to) => document.querySelector('.chordrow .lctile')"
        "?.dataset.ctxState.startsWith(`deg-${to}`)",
        arg=to,
    )
    # ⚄ again: the part rolled over the new chords
    with page.expect_response("**/fragment/gen"):
        page.click('[data-cmd="Roll the part again"]')
    assert server.state.arrangement.cells != first
    assert _first_bar_fits(server)
    # a focused tile's 1-7 sets its degree (songline.js, posting the tile's own route)
    page.locator(".chordrow .lctile").first.focus()
    with page.expect_response("**/fragment/chords/track"):
        page.keyboard.press("2")
    first_slot = sorted(
        server.state.arrangement.chords["slots"], key=lambda x: x["at"]
    )[0]
    assert first_slot["deg"] == 2
    # one Ctrl+Z step: the degree back
    with page.expect_response("**/fragment/pattern/undo"):
        page.click("#pattern-undo")
    first_slot = sorted(
        server.state.arrangement.chords["slots"], key=lambda x: x["at"]
    )[0]
    assert first_slot["deg"] == to


def test_the_keys_can_follow_the_chord_under_the_playhead(studio, server):
    page = _perform(studio, server, fold=False)
    page.click(".chordrow .lchord-empty")
    page.wait_for_selector(".chordrow .lctile")
    names = page.locator(".chordrow .lctile").evaluate_all(
        "(ts) => ts.map((t) => t.querySelector('small').textContent)"
    )
    page.evaluate("() => window.synthlab.plays.set('triad')")
    off = page.evaluate("() => window.synthlab.plays.chordOf(60).name")
    page.click('[popovertarget="play-tools"]')
    page.click("#chords-follow")
    assert page.get_attribute("#chords-follow", "aria-pressed") == "true"
    # stopped: the keys play the chords in key, as before
    assert page.evaluate("() => window.synthlab.plays.chordOf(60).name") == off
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    # (from its first downbeat as heard: before it the keys play the chords in key)
    page.wait_for_function(
        "() => window.synthlab.heardTime() > window.synthlab.loopStart() + 0.05"
    )
    seen = []
    for _ in range(20):  # 4 bars at 120 bpm: 8 s; a bar a chord
        seen.append(
            page.evaluate(
                """() => { const S = window.synthlab;
                  return [S.plays.chordOf(60).name, S.heardTime() - S.loopStart()]; }"""
            )
        )
        page.wait_for_timeout(250)
    # (a sample before the loop's downbeat is heard — ▶ can cue it twice,
    # moving its start — is the chords in key: nothing is under the playhead)
    heard = {n for n, t in seen if t > 0}
    assert heard <= set(names) and len(heard) >= 2, (seen, names)
    assert page.locator("#chord-bar .chord-pad.now").count() == 1
    page.click("#chords-follow")
    page.wait_for_function("() => !document.querySelector('#chord-bar .chord-pad.now')")
    assert page.evaluate("() => window.synthlab.plays.chordOf(60).name") == off
