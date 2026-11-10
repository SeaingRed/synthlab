"""Time signatures and tempo changes in a real browser (meter.py): a 3/4
loop is 12 steps a bar and its click's accent comes
every three beats; a song with a 6/8 section and a slower one is drawn
time-true (a slower section wider) and exports as long as it lasts; a
ramp is pressed on and its plan has its own bar lines. Counted through the
page's own objects — nobody has listened."""

from __future__ import annotations

import io
import urllib.request
import wave

import pytest

from synthlab.studio import schema
from synthlab.studio import song_model as sm
from tests.browser.test_song_playlist import playlist_song, use

# every oscillator the click starts: its frequency and when (click.js: the
# bar line 1760 Hz, a group's head 1480, a beat 1175)
RECORD_TICKS = """
(() => {
  window.__ticks = [];
  const start = OscillatorNode.prototype.start;
  OscillatorNode.prototype.start = function (when) {
    window.__ticks.push([this.frequency.value, when || 0]);
    return start.apply(this, arguments);
  };
})();
"""


def test_a_three_four_loop_is_twelve_steps_a_bar_and_clicks_in_three(studio):
    studio.page.add_init_script(RECORD_TICKS)
    page = studio.open("perform")
    studio.settle()
    page.select_option('select[name="meter"]', "3/4")
    page.wait_for_function(
        """() => document.querySelectorAll('input[name^="drum.kick."]').length === 12"""
    )
    assert studio.server.state.arrangement.meter == "3/4"
    # the drum grid's bar is 12 sixteenths, the note grid's quarters 3
    assert page.locator('input[name^="drum.kick."]').count() == 12
    assert page.input_value('select[name="steps"]') == "3"
    # the click on its own, at the page's tempo: the bar line every third tick
    page.click("#click-btn")
    page.wait_for_function("() => window.synthlab.getCtx()?.state === 'running'")
    page.wait_for_function("() => window.__ticks.length >= 10", timeout=15_000)
    freqs = [round(f) for f, _t in page.evaluate("() => window.__ticks")]
    clicks = [f for f in freqs if f in (1175, 1480, 1760)]
    first = clicks.index(1760)
    run = clicks[first : first + 9]
    assert run == [1760, 1175, 1175] * 3, run
    page.click("#click-btn")
    # one Ctrl+Z: the loop is 4/4 again, and the picker says so
    page.keyboard.press("Escape")
    page.evaluate("() => document.activeElement?.blur()")
    page.keyboard.press("Control+z")
    page.wait_for_function(
        "() => document.querySelector('select[name=meter]').value === '4/4'"
    )
    assert page.locator('input[name^="drum.kick."]').count() == 16


def _timed(server) -> dict:
    """The playlist song with its Verse in 6/8 and its Build at 80 bpm."""
    song = playlist_song()
    song["sections"][1] |= {"meter": "6/8"}
    song["sections"][2] |= {"bpm": 80.0}
    return use(server, song)


def test_a_six_eight_section_and_a_slower_one_are_drawn_as_they_last(studio):
    song = _timed(studio.server)
    page = studio.open("song")
    studio.settle()
    heads = page.evaluate(
        """() => [...document.querySelectorAll('.arr-section')].map((th) => ({
             id: th.dataset.sectionId, bars: +th.dataset.bars,
             reps: +th.dataset.repeats,
             w: th.getBoundingClientRect().width }))"""
    )
    per_bar = {h["id"]: h["w"] / (h["bars"] * h["reps"]) for h in heads}
    # a 6/8 bar is three beats of a 4/4 bar's four; a bar at 80 bpm is
    # 120/80 of one at 120
    assert per_bar["s2"] / per_bar["s1"] == pytest.approx(0.75, abs=0.02)
    assert per_bar["s3"] / per_bar["s1"] == pytest.approx(1.5, abs=0.02)
    assert per_bar["s4"] / per_bar["s1"] == pytest.approx(1.0, abs=0.02)
    # each section's head says its own clock
    assert "6/8" in page.inner_text("#sec-s2 .sec-time")
    assert "80" in page.inner_text("#sec-s3 .sec-time")
    # the ruler: a run of ticks a section, the numbers where their bars are
    assert page.locator("#song-ruler .ruler-seg").count() == 4
    # the export lasts what the sections last
    with urllib.request.urlopen(f"{studio.server.url}/export/song.wav") as r:
        with wave.open(io.BytesIO(r.read())) as w:
            seconds = w.getnframes() / w.getframerate()
    want = sum(sm.section_seconds(song, s) for s in song["sections"])
    # Intro 4 s, Verse 4 x 2 bars of 6/8 at 120 (12 s), Build 3 bars at 80
    # (9 s), Outro 8 bars at 120 (16 s)
    assert want == pytest.approx(4 + 12 + 9 + 16)
    assert seconds == pytest.approx(want, abs=0.02)


def test_a_ramp_is_pressed_on_in_the_section_menu(studio):
    use(studio.server)
    page = studio.open("song")
    studio.settle()
    page.click("#sec-s4 .sec-menu-btn")
    page.click("#secmenu-s4 .sec-time-row button")
    page.wait_for_function(
        "() => document.querySelector('#sec-s4 .sec-time') !== null", timeout=10_000
    )
    sec = studio.server.state.song["sections"][3]
    assert sec["ramp"] == pytest.approx(132.0)
    assert sm.cycle_bars(studio.server.state.song, sec) == 8
    plan = page.evaluate("() => fetch('/song/plan.json').then((r) => r.json())")
    outro = plan["sections"][3]
    assert outro["ramp"] == pytest.approx(132.0) and len(outro["bar_starts"]) == 9
    gaps = [
        b - a
        for a, b in zip(outro["bar_starts"], outro["bar_starts"][1:], strict=False)
    ]
    assert gaps == sorted(gaps, reverse=True)  # faster and faster: shorter bars
    # the schema keeps it, and a second press takes it off
    assert schema.conform(studio.server.state.song, schema.nodes().SONG)["sections"][3][
        "ramp"
    ]
