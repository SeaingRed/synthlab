"""Journey: a multi-sample instrument — clips become zones of
one sampler sound, a zone's box is dragged into a velocity layer, and the
live voice plays each key from its own zone.

The clips are uploaded through the app's own route (as test_sampler.py
does: there is no file picker for clips). Everything after is the UI: the
Clips drawer's "＋ zone" on each row, a real pointer drag on the zone
map's box edge, the take stepper on a zone's row, the keys.

Proves: "＋ zone" builds the map clip by clip (placed by detected pitch:
A2 | A3 | A4, a second A3 its round-robin take), the map draws a box and a
row a zone, a dragged edge posts the zone's new velocities, `/patch.json`
admits the zoned sound to the worklet and livefx.js posts its buffers,
and — read from the voice's own pick (`synthlab:zone`, which the map
writes on `#zone-map`) with sound at the master — a low, a middle and a
high key each sound their own zone, a repeated soft A3 alternates between
its two takes, and a hard A3 plays the hard layer. Does not prove the
pitch or the timbre is right (the parity corpus and test_live_sampler.py
hold the voice to the engine under node), and nobody has listened.
"""

from __future__ import annotations

from .conftest import wav_bytes
from .test_sampler import _UPLOAD

# (clip, Hz): A2, A3, A4, A3 again (its second take), A3 hard
# (each its own length: the same tone twice would be one sample, by content)
CLIPS = [
    ("lo", 110.0, 1.5),
    ("mid", 220.0, 1.5),
    ("hi", 440.0, 1.5),
    ("mid2", 220.0, 1.4),
    ("midhard", 220.0, 1.3),
]


def _add(studio, name: str) -> None:
    """The drawer's "＋ zone" on clip `name` (the drawer opened first if a
    whole-page answer closed it)."""
    page = studio.page
    if not page.evaluate(
        "() => document.getElementById('clip-drawer').matches(':popover-open')"
    ):
        page.click("#clip-library .clips-btn")
    page.click(f'#clip-drawer .cd-clip[data-name="{name}"] .cd-zone')
    studio.settle()


def _played(studio, midi: int, vel: float) -> str:
    """Press and let go of `midi` at `vel` through the live voice; the
    zones its voice reported sounding (the map's `data-last`)."""
    page = studio.page
    hits = int(
        page.evaluate(
            "() => Number(document.getElementById('zone-map').dataset.hits || 0)"
        )
    )
    page.evaluate("([m, v]) => window.synthlab.piano.noteOn('z', m, v)", [midi, vel])
    page.wait_for_function(
        "(n) => Number(document.getElementById('zone-map').dataset.hits || 0) > n",
        arg=hits,
    )
    assert studio.wait_sound(floor=1e-3) > 1e-3
    last = page.evaluate("() => document.getElementById('zone-map').dataset.last")
    page.evaluate("() => window.synthlab.piano.noteOff('z')")
    studio.wait_meter(r"0 voices")
    return last


def test_clips_become_zones_and_each_key_plays_its_own(studio):
    page = studio.open("editor")
    studio.settle()
    for name, hz, secs in CLIPS:
        assert (
            page.evaluate(_UPLOAD, [name, list(wav_bytes(seconds=secs, hz=hz))]) == 200
        )
    page.wait_for_selector(
        '#clip-drawer .cd-clip[data-name="midhard"]', state="attached"
    )
    studio.settle()

    # clip by clip: the first makes the saw a sampler of one zone
    for name, *_ in CLIPS:
        _add(studio, name)
    page.wait_for_function(
        "() => document.querySelectorAll('#zone-map .zone').length === 5"
    )
    zones = page.evaluate(
        """() => [...document.querySelectorAll('#zone-map .zone')].map((z) =>
             [Number(z.dataset.lo), Number(z.dataset.hi), Number(z.dataset.rr)])"""
    )
    # A2 | A3 | A4 split half way; the next two A3s are A3's takes 2 and 3
    assert zones == [[0, 51, 0], [52, 63, 0], [64, 127, 0], [52, 63, 1], [52, 63, 2]]
    assert page.locator(".zone-row").count() == 5

    # the hard one is a velocity layer: its box's low edge dragged up to
    # ~100 with a real pointer, then back to take 1 with its row's stepper
    if page.evaluate(
        "() => document.getElementById('clip-drawer').matches(':popover-open')"
    ):
        page.keyboard.press("Escape")
    plane = page.locator("#zone-map .zone-plane")
    plane.scroll_into_view_if_needed()
    box = plane.bounding_box()
    edge = page.locator(
        '#zone-map .zone[data-i="4"] .zone-edge[data-edge="vlo"]'
    ).bounding_box()
    page.mouse.move(edge["x"] + edge["width"] / 2, edge["y"] + edge["height"] / 2)
    page.mouse.down()
    target = box["y"] + box["height"] * (1 - 100.5 / 128)
    page.mouse.move(edge["x"] + edge["width"] / 2, target, steps=6)
    page.mouse.up()
    row = '.zone-row[data-i="4"]'
    # the panel comes back from the server with the zone where it landed
    page.wait_for_function(
        "(sel) => /^v(9[5-9]|10[0-5])–127$/.test("
        "[...document.querySelectorAll(sel + ' .zone-ro')][1]?.textContent || '')",
        arg=row,
    )
    for take in ("take 2", "take 1"):
        page.locator(f'{row} button[aria-label="zone 5: earlier take"]').click()
        page.wait_for_function(
            "([sel, t]) => [...document.querySelectorAll(sel + ' .zone-val')]"
            ".some((x) => x.textContent === t)",
            arg=[row, take],
        )
    studio.settle()

    # the zoned sound plays live: its buffers posted to the voice
    page.wait_for_function(
        "() => { const p = window.synthlab.piano.patch();"
        " return !!p?.sampler_zones && p.worklet_ok && !!p.sampler_live; }"
    )
    patch = page.evaluate("() => window.synthlab.piano.patch()")
    assert "zones=" in patch["sampler_live"]
    studio.start_voice()
    studio.wait_silence()

    # low, middle and high keys: each its own zone
    assert _played(studio, 45, 0.6) == "0"
    assert _played(studio, 69, 0.6) == "2"
    # a repeated soft A3 alternates between its takes; the strike after
    # them is take 1 again, and hard it is the hard layer alone
    assert _played(studio, 57, 0.3) == "1"
    assert _played(studio, 57, 0.3) == "3"
    assert _played(studio, 57, 1.0) == "4"
    assert _played(studio, 57, 0.3) == "3"
    # the zone that sounded is lit on the map as it plays
    page.evaluate("() => window.synthlab.piano.noteOn('z', 45, 0.6)")
    page.wait_for_selector('#zone-map .zone.hit[data-i="0"]', state="attached")
    page.evaluate("() => window.synthlab.piano.noteOff('z')")
    studio.wait_meter(r"0 voices")
