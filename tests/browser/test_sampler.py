"""Journey 8: the sampler — a clip becomes an instrument, played live.

The clip is uploaded through the app's own route, exactly as audio.js
posts a recorded jam (`POST /clip/upload`, multipart name + file; the clip
library comes back out of band) — there is no file picker for clips.
Everything after is the UI: the Clips drawer, "⤷ sampler" on the clip's
row (which closes the drawer), the keys, the chop button.

Proves: the sampler sound lands on Synths, `/patch.json` admits it to the
worklet, livefx.js fetches `/sampler/live/<id>.bin` and posts it
(`live.ready()`), a key then sounds from the worklet voice (so
sampler-worklet.js is in the voice's scope: its `read` is the oscillator),
and — on a probe tapping the key bus's two channels — a mono sample comes
out identical on both sides while a true-stereo one does not. Chopping
draws one marker a slice. Does not prove the pitch or the sound is right.
"""

from __future__ import annotations

import pytest

from .conftest import wav_bytes

# audio.js's jam upload, minus the recording
_UPLOAD = """async ([name, bytes]) => {
  const form = new FormData();
  form.append("name", name);
  const wav = new Blob([new Uint8Array(bytes)], { type: "audio/wav" });
  form.append("file", wav, "jam.wav");
  const res = await fetch("/clip/upload", { method: "POST", body: form });
  const body = document.body, text = res.ok && await res.text();
  if (res.ok) htmx.swap({ text, swap: "none", target: body, sourceElement: body });
  return res.status;
}"""


def _sampler_from_clip(studio, name: str, stereo: bool) -> dict:
    """Upload a 2 s tone as clip `name`, press its "⤷ sampler"; returns the
    patch the keys then hold."""
    page = studio.open("editor")
    studio.settle()
    wav = wav_bytes(seconds=2.0, hz=220.0, stereo=stereo)
    assert page.evaluate(_UPLOAD, [name, list(wav)]) == 200
    row = f'#clip-drawer .cd-clip[data-name="{name}"]'
    page.wait_for_selector(row, state="attached")  # the upload's OOB library
    page.click("#clip-library .clips-btn")
    page.click(f"{row} .sc-sample")
    page.wait_for_selector("#clip-drawer:not(:popover-open)", state="attached")
    page.wait_for_selector("#sampler-panel:not([hidden]) .sampler-wave")
    page.wait_for_function(
        "() => window.synthlab.piano.patch()?.osc1 === 'sampler'"
        " && !!window.synthlab.piano.patch().sampler_live"
    )
    studio.settle()
    patch = page.evaluate("window.synthlab.piano.patch()")
    assert patch["worklet_ok"] is True, patch["worklet_blockers"]
    assert patch["sampler_mode"] == "resample"
    return patch


@pytest.mark.parametrize("stereo", [False, True], ids=["mono", "true-stereo"])
def test_a_clip_becomes_a_sampler_and_plays_live(studio, stereo):
    patch = _sampler_from_clip(studio, "wide" if stereo else "tone", stereo)
    page = studio.page

    # what the live voice will read: one channel, or the pair
    header = page.evaluate(
        """async (url) => {
          const b = await (await fetch(url)).arrayBuffer();
          const n = new DataView(b).getUint32(0, true);
          return JSON.parse(new TextDecoder().decode(new Uint8Array(b, 4, n)));
        }""",
        patch["sampler_live"],
    )
    assert header["ch"] == (2 if stereo else 1)

    fetched: list[str] = []
    page.on("request", lambda r: fetched.append(r.url.replace(studio.url, "")))
    studio.start_voice()  # the first press: modules, tables, then the buffers
    studio.wait_silence()
    assert patch["sampler_live"] in fetched  # livefx.js asked for them
    assert page.evaluate("window.synthlab.live.ready()") is True

    # the sample's own root key, from the worklet voice
    page.evaluate(
        "(m) => window.synthlab.piano.noteOn('smp', m, 1)", patch["sampler_root"]
    )
    studio.wait_meter(r"1 voice")
    sides = studio.stereo()
    if stereo:  # 220 Hz left, 330 Hz right: two different signals
        assert sides["diff"] > 0.5 * max(sides["l"], sides["r"]), sides
    else:  # the same signal on both sides
        assert sides["diff"] < 0.02 * sides["l"], sides
    page.evaluate("window.synthlab.piano.noteOff('smp')")
    studio.wait_meter(r"0 voices")
    studio.wait_silence()

    # a real pointer press on a key plays it too
    studio.pointer_down('.pkey[data-note="0"][data-rel="0"]')
    studio.wait_sound(floor=0.01)
    studio.wait_meter(r"1 voice")
    studio.pointer_up()
    studio.wait_meter(r"0 voices")


def test_formant_mode_plays_from_the_worklet_and_keeps_its_length(studio):
    """Formant mode is the engine's grain train in the voice
    (grain-worklet.js, a third module in the worklet's scope): the pill
    leaves the sound on the worklet, and a key an octave under the root
    sounds for the clip's own two seconds and no longer — the tape read at
    half speed takes four. (A key over the root would say nothing here:
    the clip is a sine, and grains of a sine laid closer than its period
    cancel — in the engine too.) Does not prove the grains are the
    engine's: the parity corpus and test_live_sampler.py do, under node."""
    page, views = studio.page, []
    page.on("response", lambda r: "/view/editor" in r.url and views.append(r.url))
    patch = _sampler_from_clip(studio, "ah", stereo=False)
    # The helper's upload is a bare fetch, without the page's X-Studio-Page
    # header: events.js takes it for another page's edit and re-fetches this
    # view about a second later. A pill clicked before that lands is
    # morphed back (the form then posts the old mode), so let it land.
    seen = len(views)
    for _ in range(50):
        if len(views) > seen:
            break
        page.wait_for_timeout(100)
    studio.settle()
    # since ● Record sample's row the pills sit low enough for the keyboard
    # dock (fixed, at the foot) to cover them: scroll them clear, as a hand would
    pill = page.locator('input[name="sampler_mode"][value="formant"]')
    pill.evaluate("(el) => el.scrollIntoView({ block: 'center' })")
    pill.check(force=True)
    page.wait_for_function(
        "() => { const p = window.synthlab.piano.patch();"
        " return p.sampler_mode === 'formant' && p.worklet_ok && !!p.sampler_live; }"
    )
    studio.settle()
    studio.start_voice()
    studio.wait_silence()
    assert page.evaluate("window.synthlab.live.ready()") is True
    page.evaluate(
        "(m) => window.synthlab.piano.noteOn('ah', m, 1)", patch["sampler_root"] - 12
    )
    page.wait_for_timeout(900)
    assert studio.level(reads=4) > 0.01
    studio.wait_meter(r"1 voice")
    page.wait_for_timeout(1500)  # ~2.7 s in: past the clip's end, the key still down
    assert studio.level(reads=4) < 1e-3  # a tape read at half speed sounds for 4 s
    page.evaluate("window.synthlab.piano.noteOff('ah')")
    studio.wait_meter(r"0 voices")


def test_chopping_draws_a_marker_for_every_slice(studio):
    _sampler_from_clip(studio, "tone", stereo=False)
    page = studio.page
    assert page.locator(".sampler-wave svg").count() == 1
    assert page.locator(".slice-mark").count() == 0
    page.click('#sampler-panel button[hx-vals*=\'"mode": "8"\']')
    page.wait_for_function(
        "() => document.querySelectorAll('#sampler-panel .slice-mark').length === 8"
    )
    left = page.evaluate(
        "[...document.querySelectorAll('.slice-mark')]"
        ".map((m) => parseFloat(m.style.left))"
    )
    assert left == [0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5]
    assert page.locator(".slice-mark").first.get_attribute("role") == "slider"
    page.wait_for_function(
        "() => window.synthlab.piano.patch().sampler_slices.split(';').length === 8"
    )
    studio.settle()
