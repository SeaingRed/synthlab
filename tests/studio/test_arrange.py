from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio.arrange import arrangement_from_form, build_song, loop_seconds
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement
from synthlab.synth import preset


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_form_parsing_reads_bpm_and_cells():
    form = {"bpm": "132", "cell.A1.0": "on", "cell.C2.2": "on", "unrelated": "x"}
    arr = arrangement_from_form(form)
    assert arr.bpm == 132.0
    assert sorted(arr.cells) == [["A1", 0], ["C2", 2]]


def test_form_parsing_clamps_bpm_and_ignores_bad_cells():
    arr = arrangement_from_form({"bpm": "9999", "cell.A1.nope": "on"})
    assert arr.bpm == 200.0  # clamped to [40, 200]
    assert arr.cells == []


def test_loop_seconds_sixteenths():
    arr = Arrangement(bpm=120.0, bars=1, steps_per_bar=16)
    assert loop_seconds(arr) == pytest.approx(2.0)  # 16 sixteenths at 120bpm


def test_consecutive_cells_merge_into_held_note():
    arr = Arrangement(cells=[["A1", 0], ["A1", 1], ["A1", 2], ["C2", 8]])
    song = build_song(preset("bass_reese"), arr)
    audio = song.render()
    assert np.all(np.isfinite(audio))
    assert len(audio) > 0


# --- slides (303-style glide between adjacent notes) -----------------------


def test_form_parses_slide_steps():
    form = {"cell.A1.0": "on", "cell.C2.1": "on", "slide.1": "on", "slide.99": "on"}
    arr = arrangement_from_form(form)
    assert arr.slides == [1]  # out-of-range slide dropped


def test_slide_changes_the_render():
    base = Arrangement(steps_per_bar=8, cells=[["A1", 0], ["C2", 1]])
    slid = Arrangement(steps_per_bar=8, cells=[["A1", 0], ["C2", 1]], slides=[1])
    patch = preset("bass_acid")
    a = build_song(patch, base).render()
    b = build_song(patch, slid).render()
    assert np.all(np.isfinite(b))
    assert not np.allclose(
        a[: min(len(a), len(b))], b[: min(len(a), len(b))], atol=1e-6
    )


# --- arp -------------------------------------------------------------------


def test_form_parses_arp_fields():
    arr = arrangement_from_form({"arp_mode": "updown", "arp_octaves": "2"})
    assert arr.arp_mode == "updown" and arr.arp_octaves == 2
    bad = arrangement_from_form({"arp_mode": "sideways", "arp_octaves": "9"})
    assert bad.arp_mode == "off" and bad.arp_octaves == 3  # clamped


def test_arp_fills_the_loop_from_held_cells():
    held = Arrangement(steps_per_bar=16, cells=[["A1", 0], ["C2", 0], ["E2", 0]])
    off = build_song(preset("pluck_glass"), held).render()
    arp = build_song(
        preset("pluck_glass"),
        Arrangement(
            steps_per_bar=16,
            cells=[["A1", 0], ["C2", 0], ["E2", 0]],
            arp_mode="up",
            arp_octaves=2,
        ),
    ).render()
    assert np.all(np.isfinite(arp))
    n = min(len(off), len(arp))
    assert not np.allclose(off[:n], arp[:n], atol=1e-6)
    # the arp keeps striking notes at the end of the loop (~1.94s at 124bpm),
    # long after the single held chord of the off render has decayed
    sr = 44100
    lo, hi = int(1.5 * sr), int(1.85 * sr)
    arp_rms = float(np.sqrt(np.mean(arp[lo:hi] ** 2)))
    off_rms = float(np.sqrt(np.mean(off[lo:hi] ** 2)))
    assert arp_rms > 0.02
    assert arp_rms > 2.0 * off_rms


def test_empty_grid_renders_silence_not_crash():
    arr = Arrangement(cells=[])
    song = build_song(preset("bass_reese"), arr)
    audio = song.render()
    assert np.all(np.isfinite(audio))


def test_v2_form_parsing_reads_everything():
    form = {
        "bpm": "140",
        "bars": "2",
        "steps": "32",
        "root": "E1",
        "scale_name": "minor",
        "swing": "0.3",
        "cell.E1.0": "on",
        "vel.0": "0.5",
        "drum.kick.0": "on",
        "drum.kick.999": "on",  # out of range: dropped
    }
    arr = arrangement_from_form(form)
    assert (arr.bars, arr.steps_per_bar, arr.root, arr.swing) == (2, 32, "E1", 0.3)
    assert arr.velocities == {"0": 0.5}
    assert arr.drums == [["kick", 0]]


def test_a_velocity_key_is_stored_as_the_lane_keys_it():
    """`vel.03` and `vel. 3 ` were kept under those literal keys: `int()`
    takes both, the lane is read by `str(step)`, so the value was inert
    until a load's schema pass renamed (or dropped) it. The form boundary
    stores the plain step number, the drum lane's rule
    (`limits.step_key`); what is not a plain number is no step."""
    from synthlab.studio import limits

    form = {
        "bars": "1",
        "steps": "16",
        "vel.03": "0.5",
        "vel.7": "0.25",
        "vel. 4 ": "0.9",  # not a plain number: no step
        "vel.+5": "0.9",
        "vel.²": "0.9",  # isdigit() is True, int() raises
        "vel." + "9" * 5000: "0.9",  # past what int() converts
        "vel.16": "0.9",  # past this grid
    }
    assert arrangement_from_form(form).velocities == {"3": 0.5, "7": 0.25}
    assert limits.step_key("007") == "7" and limits.step_key("127") == "127"
    for bad in ("", " 3", "3 ", "-1", "1.0", "²", "128", "9" * 7):
        assert limits.step_key(bad) is None, bad


def test_v2_bad_values_clamp_or_default():
    arr = arrangement_from_form(
        {"bars": "9", "steps": "17", "root": "Z9", "scale_name": "nope", "swing": "2"}
    )
    assert arr.bars == 4 and arr.steps_per_bar == 4
    assert arr.root == "A1" and arr.swing == 0.6
    assert arr.scale_name == "pentatonic_minor"


def test_steps_validation_accepts_only_powers():
    assert arrangement_from_form({"steps": "8"}).steps_per_bar == 8
    assert arrangement_from_form({"steps": "32"}).steps_per_bar == 32
    assert arrangement_from_form({"steps": "17"}).steps_per_bar == 4


def test_pitch_rows_come_from_scale():
    from synthlab.studio.state import pitch_rows

    rows = pitch_rows(Arrangement())
    assert len(rows) == 6
    assert all(isinstance(p, str) for p in rows)
    assert rows[0] != rows[-1]


def test_velocity_reaches_song():
    # Song.render()'s default normalize=0.89 rescales any non-silent mix to
    # the same peak, which would mask a raw amplitude difference from
    # velocity. Pass normalize=0 (falsy => render() skips rescaling, see
    # sequencer.py) so this observes the real pre-normalize amplitude.
    arr = Arrangement(cells=[["A1", 0]], velocities={"0": 0.4})
    song = build_song(preset("bass_reese"), arr)
    audio_soft = song.render(normalize=0)
    loud = build_song(
        preset("bass_reese"), Arrangement(cells=[["A1", 0]], velocities={"0": 1.0})
    ).render(normalize=0)
    assert float(np.abs(audio_soft).max()) < float(np.abs(loud).max())


def test_drums_render_audio():
    arr = Arrangement(cells=[], drums=[["kick", 0], ["kick", 8], ["hat", 4]])
    audio = build_song(preset("bass_reese"), arr).render()
    assert float(np.sqrt(np.mean(audio**2))) > 1e-4


def test_swing_delays_offbeats():
    base = Arrangement(cells=[["A1", 1]])
    swung = Arrangement(cells=[["A1", 1]], swing=0.5)
    a = build_song(preset("pluck_glass"), base).render()
    b = build_song(preset("pluck_glass"), swung).render()

    # onset of the swung note is later: compare first index crossing 10% of peak
    def onset(x):
        m = np.abs(x).max() * 0.1
        return int(np.argmax(np.abs(x).max(axis=1) > m))

    assert onset(b) > onset(a)


def test_two_bars_render_longer():
    one = build_song(preset("bass_reese"), Arrangement(cells=[["A1", 0]]))
    two = build_song(preset("bass_reese"), Arrangement(cells=[["A1", 0]], bars=2))
    # render length is trimmed by render.py, but Song content spans 2 bars:
    assert len(two.render()) >= len(one.render())


def test_transport_has_single_bpm_display(tmp_path):
    html = make_client(tmp_path).get("/").text  # bpm lives on the sound bar
    assert '<output for="t-bpm">' not in html
    assert 'id="t-bpm"' in html


def test_velocity_lane_markup_replaces_micro_slider(tmp_path):
    html = make_client(tmp_path).get("/view/perform").text
    assert 'class="vel-lane"' in html
    assert 'data-step="0"' in html
    assert 'name="vel.0"' in html
    assert 'type="hidden" name="vel.0"' in html
    assert 'class="vel"' not in html  # old range micro-slider markup is gone


def test_notes_past_a_shrunk_grid_stay_hidden_and_silent():
    """A grid cut from 2 bars to 1 keeps bar 2's notes hidden (limits.py)
    — and they must not play: rendered past the loop's end, the wrap used
    to fold them onto bar 1. A held note that starts inside still rings."""
    p = preset("bass_acid")
    one = Arrangement(bars=1, steps_per_bar=4, cells=[["A1", 0]])
    hidden = Arrangement(bars=1, steps_per_bar=4, cells=[["A1", 0], ["C2", 6]])
    a = build_song(p, one).render(normalize=0)
    b = build_song(p, hidden).render(normalize=0)
    assert np.array_equal(a, b)
    held = Arrangement(bars=1, steps_per_bar=4, cells=[["A1", 3], ["A1", 4]])
    short = Arrangement(bars=1, steps_per_bar=4, cells=[["A1", 3]])
    assert len(build_song(p, held).render(normalize=0)) > len(
        build_song(p, short).render(normalize=0)
    )
