"""A take on the Song page: the drums lane armed and played
from the keys (the finger drums), the count-in counted down, the take
recorded onto the drums pattern and cleaned up when it stops; and the
sound dock's ▶ heard in the mix or alone.

Proves: the page's islands do what they say in Chromium — the pads show
for an armed drums lane, the countdown is drawn on the count-in's beats,
the keys reach songrec.js as hits (the take's raw events on the server),
the clean-up runs on stop and its report opens; the dock's Hear radios
post and the sound bar says the song's tempo and key. Does not prove the
kit is heard, nor that the metronome ticks (nobody has listened)."""

from __future__ import annotations

from .test_song import _song_with_a_section, _wait


def _drums(server) -> dict:
    return next(t for t in server.state.song["tracks"] if t["kind"] == "drums")


def test_finger_drums_count_in_record_and_clean_up(studio, server):
    page = _song_with_a_section(studio, server)
    drums = _drums(server)
    page.click(f'.rowbtn.arm[data-arm="{drums["id"]}"]')
    page.wait_for_selector("#finger-drums:not([hidden])")
    assert page.locator(".fd-pad").count() == 8
    page.click("#song-rec")
    page.wait_for_function(
        "() => ['ready', '3', '2', '1'].includes("
        "document.getElementById('rec-countdown')?.textContent)",
        timeout=30_000,
    )
    # past the count-in (a hit in it is dropped, as a note is): the take's bar 1
    page.wait_for_function(
        "() => { const t = window.synthlab.songTake?.();"
        " return !!t && window.synthlab.getCtx().currentTime > t.at0; }",
        timeout=30_000,
    )
    for key in ("a", "d", "s", "d", "a", "d", "s", "d"):
        page.keyboard.press(key)
        page.wait_for_timeout(120)
    page.locator('.fd-pad[data-voice="clap"]').click()
    page.click("#song-rec")  # stop: the last post, then the clean-up
    page.wait_for_selector("#take-report:popover-open", timeout=20_000)
    take = server.state.take_history[-1]
    voices = {e["voice"] for e in take["raw"]}
    assert {"kick", "snare", "hat", "clap"} <= voices
    text = page.locator("#take-report").inner_text()
    assert "Snapped" in text or "Already tight" in text
    assert "GEMINI_API_KEY" in text  # the tests have no key: it says why
    # the keys were the drums', not the synth's octave shortcuts
    assert page.evaluate("() => window.synthlab.piano?.octave?.()") in (None, 3, 4)
    # ↶ Undo clean-up gives the take back as recorded
    after = server.state.song["patterns"][take["pattern_id"]]
    page.click("[data-take-undo]")
    _wait(
        page,
        lambda: server.state.song["patterns"][take["pattern_id"]] != after,
        "the clean-up undone",
    )
    # the section still loops: stop it, and let its fetches land, or the
    # scratch studio waits on them as it shuts down
    page.click("#song-stop")
    studio.settle()


def test_the_dock_hears_the_track_in_the_mix_or_alone(studio, server):
    page = _song_with_a_section(studio, server)
    synth = next(t for t in server.state.song["tracks"] if t["kind"] == "synth")
    page.click(f"#trk-{synth['id']} .track-sound")
    page.wait_for_selector("#song-dock:not([hidden]) iframe")
    frame = page.frame_locator("#song-dock iframe")
    bar = frame.locator("#sound-bar")
    bar.locator('input[name="edit_hear"][value="alone"]').wait_for(state="attached")
    assert bar.locator('input[name="edit_hear"][value="alone"]').is_checked()
    song = server.state.song
    assert f"{round(song['bpm'])} bpm · {song['root']}" in bar.inner_text()
    assert bar.locator('input[name="phrase"]').count() == 0
    assert frame.locator(".ls-track").count() == 1
    assert "the song's" in frame.locator("#sound-id").inner_text()
    with page.expect_response("**/fragment/arrangement"):
        bar.locator('input[name="edit_hear"][value="mix"]').check(force=True)
    assert server.state.song_edit_hear == "mix"
    # a block of the lane opened on the Song page: the frame renders it for ▶
    with page.expect_request("**/fragment/preview"):
        page.locator(f"#trk-{synth['id']} .arr-cell.filled .cell-btn").first.click()
    sec = server.state.song["sections"][0]["id"]
    assert server.state.song_drawer == {"section_id": sec, "track_id": synth["id"]}


def test_a_pool_pattern_plays_through_a_lane_and_keeps_playing_across_lanes(
    studio, server
):
    """The pattern editor's ▶ Play on a pattern no section plays (the pool's
    "+ note pattern"): it loops through a lane's sound; choosing another
    lane ("through") keeps it playing, through that lane. And every view's
    ▶ is the accent-filled `.btn-play`."""
    page = _song_with_a_section(studio, server)
    assert "btn-play" in page.locator("#song-play").get_attribute("class")
    before = set(server.state.song["patterns"])
    page.click('.pool-add[hx-vals*="notes"]')
    _wait(
        page, lambda: len(server.state.song["patterns"]) > len(before), "a new pattern"
    )
    pid = next(iter(set(server.state.song["patterns"]) - before))
    studio.settle()
    page.click(f'.pool-open-pattern[hx-vals*="{pid}"]')
    play = page.locator("#song-drawer .drawer-solo")
    play.wait_for()
    assert play.inner_text().strip() == "▶ Play"
    assert f"/song/pattern/{pid}/play.wav?track=" in play.get_attribute(
        "data-cell-solo"
    )
    with page.expect_response(lambda r: f"/song/pattern/{pid}/play.wav" in r.url):
        play.click()
    page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    synths = [t["id"] for t in server.state.song["tracks"] if t["kind"] == "synth"]
    if len(synths) > 1:
        with page.expect_response(lambda r: f"track={synths[1]}" in r.url):
            page.select_option('#song-drawer select[name="through"]', synths[1])
        assert server.state.song_drawer["through"] == synths[1]
        page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    play.click()
    page.wait_for_selector('.drawer-solo[aria-pressed="false"]')
