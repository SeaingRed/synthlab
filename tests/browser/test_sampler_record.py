"""● Record sample: the sampler panel's timed mic take, on Chromium's fake
microphone (the harness launches with `--use-fake-device-for-media-stream`:
a device that beeps).

Everything is the UI: the osc1 pill turns the sampler on, the panel's
"1 bar" and ● Record sample are clicked. Proves: the mic opens through
voice.js's capture, the count-in shows over the page ("ready", then 3, 2,
1), the button reads as recording while it runs, the take stops by itself
after one bar and is posted (`/fragment/sampler/record`, multipart from
#smp-countdown), the clip store keeps it as `rec-…`, the sampler holds it
(the patch's `sampler_sample`, the panel's waveform), and a key then
sounds from the worklet voice. Does not prove the cut lands on the beat:
the fake device's latency is nobody's (the route tests hold the length;
nobody has listened)."""

from __future__ import annotations

from synthlab import sampler


def test_record_a_sample_from_the_mic_and_play_it(studio):
    page = studio.open("editor")
    page.context.grant_permissions(["microphone"], origin=studio.url)
    studio.settle()
    page.check('input[name="osc1"][value="sampler"]', force=True)
    page.wait_for_selector("#sampler-panel:not([hidden]) #smp-rec")
    studio.settle()
    assert page.locator("#sampler-panel .empty-state").count() == 1

    page.click('[data-smp-bars="1"]')
    assert page.get_attribute('[data-smp-bars="1"]', "aria-pressed") == "true"
    assert page.get_attribute("#smp-click", "aria-pressed") == "true"  # on by default

    page.click("#smp-rec")
    # the count-in, big over the page, while the button reads as recording
    page.wait_for_selector("#smp-countdown:not([hidden])")
    said = page.text_content("#smp-countdown")
    assert said in ("ready", "3", "2", "1"), said
    assert page.get_attribute("#smp-rec", "aria-pressed") == "true"
    page.wait_for_function(
        "() => document.getElementById('smp-countdown').textContent === '1'"
    )

    # one bar later (124 bpm: ~2 s) it stops by itself and the sample lands
    page.wait_for_function(
        "() => !!window.synthlab.piano.patch()?.sampler_sample"
        " && !!window.synthlab.piano.patch().sampler_live",
        timeout=20_000,
    )
    page.wait_for_selector("#sampler-panel:not([hidden]) .sampler-wave")
    assert page.get_attribute("#smp-rec", "aria-pressed") == "false"
    assert page.is_hidden("#smp-countdown")
    studio.settle()

    s = studio.server.state
    patch = page.evaluate("window.synthlab.piano.patch()")
    assert patch["osc1"] == "sampler" and s.patch.sampler_sample
    assert s.patch.name.startswith("rec-")
    bar = 240 / s.arrangement.bpm
    assert abs(sampler.seconds(s.patch.sampler_sample) - bar) < 0.01  # exactly a bar
    assert patch["worklet_ok"] is True, patch["worklet_blockers"]

    # a key plays it, from the worklet voice
    studio.start_voice()
    studio.wait_silence()
    page.evaluate(
        "(m) => window.synthlab.piano.noteOn('rec', m, 1)", patch["sampler_root"]
    )
    studio.wait_meter(r"1 voice")
    studio.wait_sound(floor=1e-3)
    page.evaluate("window.synthlab.piano.noteOff('rec')")
    studio.wait_meter(r"0 voices")


def test_a_second_press_cancels_the_take(studio):
    page = studio.open("editor")
    page.context.grant_permissions(["microphone"], origin=studio.url)
    studio.settle()
    page.check('input[name="osc1"][value="sampler"]', force=True)
    page.wait_for_selector("#sampler-panel:not([hidden]) #smp-rec")
    studio.settle()
    posted: list[str] = []
    page.on(
        "request",
        lambda r: "/fragment/sampler/record" in r.url and posted.append(r.url),
    )

    page.click("#smp-rec")
    page.wait_for_selector("#smp-countdown:not([hidden])")
    page.click("#smp-rec")  # during the count-in
    assert page.get_attribute("#smp-rec", "aria-pressed") == "false"
    assert page.is_hidden("#smp-countdown")
    page.wait_for_timeout(5000)  # past where a 1-bar take would have ended
    assert posted == [] and not studio.server.state.patch.sampler_sample
