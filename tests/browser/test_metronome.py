"""The metronome (click.js, #click-btn in the rail): on is heard. With
nothing playing it ticks on its own at the page's tempo; off, it stops.
Counted through the page's own AudioContext (every tick is one oscillator
to the destination), so it proves ticks are scheduled, at about the tempo
— nobody has listened."""

from __future__ import annotations

COUNT_OSCILLATORS = """
(() => {
  const make = BaseAudioContext.prototype.createOscillator;
  window.__ticks = 0;
  BaseAudioContext.prototype.createOscillator = function () {
    window.__ticks += 1;
    return make.call(this);
  };
})();
"""


def test_the_click_ticks_with_nothing_playing_and_stops_when_off(studio):
    studio.page.add_init_script(COUNT_OSCILLATORS)
    page = studio.open("editor")
    studio.settle()
    assert not page.evaluate("() => window.synthlab.isPlaying()")
    page.click("#click-btn")  # a gesture: the context starts
    assert page.get_attribute("#click-btn", "aria-pressed") == "true"
    page.wait_for_function("() => window.synthlab.getCtx()?.state === 'running'")
    start = page.evaluate("() => window.__ticks")
    page.wait_for_timeout(2000)
    ticks = page.evaluate("() => window.__ticks") - start
    bpm = float(page.input_value('input[name="bpm"]'))
    beats = 2.0 * bpm / 60
    assert beats - 2 <= ticks <= beats + 2, (ticks, beats)
    # nothing else started
    assert not page.evaluate("() => window.synthlab.isPlaying()")
    page.click("#click-btn")
    assert page.get_attribute("#click-btn", "aria-pressed") == "false"
    page.wait_for_timeout(200)  # what was already scheduled
    off = page.evaluate("() => window.__ticks")
    page.wait_for_timeout(1000)
    assert page.evaluate("() => window.__ticks") == off
