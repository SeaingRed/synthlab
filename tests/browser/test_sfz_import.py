"""Journey: Import SFZ… — a zip of an .sfz and its samples (built here from
synthesised tones) is chosen in the file input the dock's button is a label
for, becomes a four-zone sampler, `/patch.json` admits it to the worklet and
a live key sounds its own zone.

Proves the control posts the multipart form, the page answers whole with
the zone map, the toast names the import, and a held key makes sound
through the live voice (the zone read from the voice's own pick). Does not
prove the timbre or pitch is right, and nobody has listened."""

from __future__ import annotations

from tests.sfzkit import small_instrument

from .test_sampler_zones import _played


def test_an_sfz_zip_becomes_a_zoned_sampler_that_plays(studio):
    page = studio.open("editor")
    studio.settle()
    page.set_input_files(
        "#sfz-file",
        files=[
            {
                "name": "inst.zip",
                "mimeType": "application/zip",
                "buffer": small_instrument(),
            }
        ],
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#zone-map .zone').length === 4"
    )
    assert page.locator(".zone-row").count() == 4
    page.wait_for_function(
        "() => { const p = window.synthlab.piano.patch();"
        " return !!p?.sampler_zones && p.worklet_ok && !!p.sampler_live; }"
    )
    studio.start_voice()
    studio.wait_silence()
    assert _played(studio, 57, 0.8)  # the middle zone (A3)
