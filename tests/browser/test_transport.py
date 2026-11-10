"""The transport (transport.js): one registry every player
reports to, the top dock's "now playing" read-out, the cue beside each
page's ▶, Space and Shift+Space (`synthlab/studio/docs/frontend.md`, "The
transport is the same on every page").

What these journeys prove: the read-out names the source that sounds
(the loop, the song with its section and bar, the jam, a clip) and its
state (playing, paused, waiting); the cue under ▶ says what ▶ / Space will
start and follows the marker; starting a second player stops the first and
the cue says so before the press; ⏸ holds the place, Space resumes it and
■ takes the marker back to where ▶ started; a jam launch waiting for its
bar shows as waiting, with a count, in the dock and on the pad; one key and
one button stop everything. Audio is asserted through the page's own
objects (`S.isPlaying()`, `songClock()`, `jam.sounding()`, the master
analyser) — nobody has listened.
"""

from __future__ import annotations

import numpy as np

from synthlab.studio.clips import save_clip
from tests.browser.test_input_matrix_jam import fast, pad, sounding, wait_sounding
from tests.browser.test_song_playlist import _click_bar, playlist_song, use

NP = "#now-playing"
T = "window.synthlab.transport"


def _np(page) -> dict:
    return page.evaluate(
        """() => { const b = document.getElementById('now-playing');
          const t = (s) => b.querySelector(s).textContent;
          return { state: b.dataset.state, kind: t('.np-kind'), label: t('.np-label'),
                   pos: t('.np-pos'), next: t('.np-next'), also: t('.np-also'),
                   stop: !document.getElementById('global-stop').disabled,
                   pause: !document.getElementById('np-pause').hidden,
                   resume: !document.getElementById('np-resume').hidden }; }"""
    )


def _wait(page, check, what: str, timeout: float = 10.0):
    waited = 0.0
    while waited < timeout:
        if check():
            return
        page.wait_for_timeout(50)
        waited += 0.05
    raise AssertionError(f"timed out waiting for {what}: {_np(page)}")


def _cue(page, for_id: str) -> str:
    return page.locator(f'[data-cue-for="{for_id}"]').text_content() or ""


def test_the_read_out_names_the_loop_and_its_stop_is_everyones(studio, server):
    page = studio.open("drums")
    studio.settle()
    # stopped: it says what Space would play here, and the ▶ says it too
    _wait(page, lambda: _np(page)["state"] == "idle", "idle")
    assert "the beat" in _np(page)["label"]
    assert _np(page)["stop"] is False
    assert _cue(page, "play").startswith("the beat")
    assert "the beat" in page.get_attribute("#play", "aria-label")
    page.click("#play")
    _wait(page, lambda: _np(page)["state"] == "playing", "the loop playing")
    np_ = _np(page)
    assert (
        np_["kind"] == "Loop" and "Kit" in np_["label"] and "the beat" in np_["label"]
    )
    assert np_["stop"]
    _wait(
        page, lambda: _np(page)["pos"].startswith("bar "), "its bar"
    )  # (a fresh beat is empty: silent)
    # another page with no transport of its own: still named, still stoppable
    studio.nav("breed")
    assert _np(page)["kind"] == "Loop"
    page.click("#global-stop")
    _wait(page, lambda: _np(page)["state"] == "idle", "stopped")
    assert not page.evaluate("() => window.synthlab.isPlaying()")


def test_the_songs_cue_follows_the_marker_and_says_what_it_stops(studio, server):
    use(server, playlist_song())
    page = studio.open("drums")
    page.click("#play")  # the loop plays...
    page.wait_for_function("() => window.synthlab.isPlaying()")
    studio.nav("song")  # ...and carries on here: the dock says so
    studio.settle()
    assert _np(page)["kind"] == "Loop"
    _wait(
        page, lambda: "stops Loop" in _cue(page, "song-play"), "the handoff in the cue"
    )
    assert _cue(page, "song-play").startswith("the song from bar 1")
    _click_bar(page, 8)  # the marker: bar 8, in the Verse
    _wait(page, lambda: "bar 8 · Verse" in _cue(page, "song-play"), "the cue at bar 8")
    assert "bar 8" in page.get_attribute("#song-play", "aria-label")
    # a second player stops the first
    page.click("#song-play")
    page.wait_for_function("() => window.synthlab.songClock().playing")
    assert not page.evaluate("() => window.synthlab.isPlaying()")  # the loop stopped
    _wait(page, lambda: _np(page)["kind"] == "Song", "the song in the dock")
    _wait(page, lambda: _np(page)["pos"].startswith("bar "), "a bar")
    assert "Verse" in _np(page)["label"]
    assert _cue(page, "song-play") == "playing"
    page.click("#song-stop")
    _wait(page, lambda: _np(page)["state"] == "idle", "stopped")


def test_pause_holds_the_place_space_resumes_and_stop_goes_back(studio, server):
    use(server, playlist_song(bpm=200.0))
    page = studio.open("song")
    studio.settle()
    _click_bar(page, 3)  # the Verse's first bar
    page.locator("#song-play").focus()
    page.keyboard.press("Space")  # a focused ▶: the button's own press
    page.wait_for_function("() => window.synthlab.songClock().playing")
    _wait(page, lambda: _np(page)["state"] == "playing", "playing")
    page.wait_for_timeout(2600)  # past a bar or two at 200 bpm
    assert _np(page)["pause"]
    page.locator("#song-ruler").focus()
    page.keyboard.press("Space")  # Space from the playlist: ⏸
    _wait(page, lambda: _np(page)["state"] == "paused", "paused")
    np_ = _np(page)
    assert np_["resume"] and np_["stop"] and not np_["pause"]
    paused_at = page.evaluate("() => +document.getElementById('mark-bar').value")
    assert paused_at > 3, paused_at  # the marker moved to where it paused
    assert not page.evaluate("() => window.synthlab.songClock().playing")
    page.keyboard.press("Space")  # resume
    page.wait_for_function("() => window.synthlab.songClock().playing")
    _wait(page, lambda: _np(page)["state"] == "playing", "playing again")
    page.click("#song-stop")  # ■: back to where ▶ started
    _wait(page, lambda: _np(page)["state"] == "idle", "stopped")
    assert page.evaluate("() => +document.getElementById('mark-bar').value") == 3


def test_a_queued_jam_launch_waits_with_a_count(studio, server):
    use(server, fast() | {"bpm": 80.0})  # a bar is 3 s: the wait is seen
    page = studio.open("song")
    page.wait_for_selector("#jam-toggle")
    studio.settle()
    page.click("#jam-toggle")
    page.select_option("#jam-q", "4")
    page.click(pad("s1", "td"))
    wait_sounding(page, {"td": "s1"}, "the first pad (from silence: at once)")
    _wait(page, lambda: _np(page)["kind"] == "Jam", "the jam in the dock")
    page.click(pad("s2", "t1"))  # waits for the 4-bar line
    _wait(page, lambda: _np(page)["next"].startswith("next:"), "the wait in the dock")
    assert "beat" in _np(page)["next"]
    wait = page.get_attribute(pad("s2", "t1"), "data-wait")
    assert wait and wait.isdigit(), wait  # the pad counts down too
    assert sounding(page) == {"td": "s1"}
    page.keyboard.press("Shift+Space")  # stop everything: the wait goes too
    wait_sounding(page, {}, "everything stopped")
    _wait(page, lambda: _np(page)["state"] == "idle", "idle")
    assert page.get_attribute(pad("s2", "t1"), "data-wait") is None


def test_a_long_audition_layers_over_the_loop_and_stops_with_it(studio, server):
    t = np.arange(44100 * 4) / 44100
    save_clip(
        server.state.data_dir,
        "long-take",
        np.column_stack([np.sin(2 * np.pi * 220 * t) * 0.3] * 2),
    )
    page = studio.open("perform")
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.evaluate("() => window.synthlab.playUrl('/clip/long-take.wav')")
    _wait(
        page,
        lambda: "Clip · long-take" in _np(page)["also"],
        "the clip beside the loop",
    )
    assert _np(page)["kind"] == "Loop"  # a hand's audition layers: the loop plays on
    assert page.evaluate("() => window.synthlab.isPlaying()")
    page.click("#global-stop")
    _wait(page, lambda: _np(page)["state"] == "idle", "idle")
    assert page.evaluate(f"() => {T}.list().length") == 0
    assert not page.evaluate("() => window.synthlab.isPlaying()")


def test_space_is_the_pages_play_and_never_a_fields(studio, server):
    page = studio.open("drums")
    studio.settle()
    page.locator('#drum-transport [name="bpm"]').focus()
    page.keyboard.press("Space")  # a field: typing, never the transport
    page.wait_for_timeout(300)
    assert not page.evaluate("() => window.synthlab.isPlaying()")
    page.locator("h1").first.click()  # the page itself
    page.keyboard.press("Space")
    page.wait_for_function("() => window.synthlab.isPlaying()")
    page.keyboard.press("Space")  # a toggle: Space stops it
    page.wait_for_function("() => !window.synthlab.isPlaying()")
