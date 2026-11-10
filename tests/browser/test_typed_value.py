"""A typed value on the views that did not have one.

Typing into a control's read-out was built in the bench's island, so only
Synths, Bass and Voice had it (tests/browser/test_bench.py drives it
there). It is tweaktype.js now — the value layer's — and this drives it
on a Drums strip's knob and a Song track's level (a read-out
in per cent of a 0..1.5 control).

NOT covered: the Lab's stem levels (the same code path: an <output> in
the knob's own label, in the knob's own dB), and that a Song lane's
read-out (`data-untyped`: it is not the slider's own value) takes no
field — that is one line of tweaktype.js, read here, not driven.
"""

from __future__ import annotations

import time

from tests.browser.test_song_playlist import playlist_song, use


def _wait(page, check, what: str, timeout: float = 6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = check()
        if got:
            return got
        page.wait_for_timeout(40)
    raise AssertionError(f"timed out waiting for {what}")


def test_a_drum_knob_and_a_track_level_take_a_typed_value(studio, server):
    page = studio.page
    page.set_viewport_size({"width": 1920, "height": 1080})
    studio.open("drums")
    studio.settle()
    # a kit strip's level: click the read-out, type, Enter
    page.click('output[for="d-kick-level"]')
    box = page.locator('output[for="d-kick-level"] input.val-edit')
    box.fill("0.7")
    box.press("Enter")
    _wait(
        page,
        lambda: (
            abs(server.state.arrangement.drum_mix.get("kick", {}).get("level", 1) - 0.7)
            < 1e-6
        ),
        "the typed kick level",
    )
    assert page.evaluate("document.activeElement.id") == "d-kick-level"
    # Enter on the focused knob opens the same field; Esc leaves the value
    page.keyboard.press("Enter")
    page.locator('output[for="d-kick-level"] input.val-edit').fill("1.4")
    page.keyboard.press("Escape")
    assert page.locator("input.val-edit").count() == 0
    assert server.state.arrangement.drum_mix["kick"]["level"] == 0.7

    # the Song: a track's level reads in per cent (`data-pct`) of a 0..1.5 gain
    use(server, playlist_song())
    studio.open("song")
    studio.settle()
    track = server.state.song["tracks"][0]
    row = f"#trk-{track['id']}"
    with page.expect_response(f"**/song/track/{track['id']}/mix"):
        page.click(f"{row} .track-val")
        page.locator(f"{row} .track-val input.val-edit").fill("80")
        page.keyboard.press("Enter")
    _wait(
        page,
        lambda: abs(server.state.song["tracks"][0]["gain"] - 0.8) < 1e-6,
        "the typed track level",
    )
    # out of range is clamped to the control's travel, as a drag is
    with page.expect_response(f"**/song/track/{track['id']}/mix"):
        page.click(f"{row} .track-val")
        page.locator(f"{row} .track-val input.val-edit").fill("900")
        page.keyboard.press("Enter")
    _wait(
        page,
        lambda: abs(server.state.song["tracks"][0]["gain"] - 1.5) < 1e-6,
        "the clamped track level",
    )
