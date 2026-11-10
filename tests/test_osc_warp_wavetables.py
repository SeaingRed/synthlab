"""The phase warp, your own wavetables, one voice (mono / legato). Each is
opt-in — at its default a patch without it renders bit for bit as if it
did not exist (also measured: 294 shipped patches x two pitches hash equal
with and without them, native and NumPy) — and each is
held to what it claims: the warp band-limited against a naive one, a made
table built like the built-ins, a missing one the basic table, a legato
line one voice."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from synthlab import core, wavetables
from synthlab import synth as synth_mod
from synthlab.core import SR
from synthlab.sequencer import MONO_FADE, Song, cut_line, mono_lines
from synthlab.studio.factory import FACTORY, get_factory
from synthlab.synth import Patch, Synth

ROUND6 = ("PD Resonant Sine", "Bent Sine Bass", "Mirror Lead", "Legato Glide Lead")


def _alias_db(y: np.ndarray, f0: float) -> float:
    """Energy away from the multiples of f0 over the energy on them, dB."""
    p = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    f = np.fft.rfftfreq(len(y), 1 / SR)
    near = np.abs(f / f0 - np.round(f / f0)) * f0 < 25
    return float(10 * np.log10(p[~near].sum() / p[near].sum()))


# --- the defaults: the old engine, untouched ---------------------------------------


def test_older_patches_never_reach_the_newer_code(monkeypatch):
    """Every factory sound but those built on them, every PRESET and the
    default patch render without the warp, a made table or a line."""

    def boom(*a, **k):
        raise AssertionError("a round-six path ran on an older patch")

    monkeypatch.setattr(core, "warped", boom)
    monkeypatch.setattr(wavetables, "stack", boom)
    olds = [get_factory(e["name"]) for e in FACTORY if e["name"] not in ROUND6]
    for p in [*olds, *synth_mod.PRESETS.values(), Patch()]:
        Synth(p).note("C3", 0.3, gate=0.2)
    song = Song(120)
    song.notes("a", Synth(Patch()), [("C4", 1), ("E4", 1)])  # poly: no lines


def test_the_warp_sounds_use_their_features():
    got = {e["name"]: get_factory(e["name"]) for e in FACTORY if e["name"] in ROUND6}
    assert set(got) == set(ROUND6)
    assert got["PD Resonant Sine"].osc1_warp == "pd"
    assert got["Mirror Lead"].osc1_warp == "mirror"
    assert got["Legato Glide Lead"].voice_mode == "legato"


# --- the warp ----------------------------------------------------------------------


@pytest.mark.parametrize("mode", core.WARP_MODES[1:])
def test_every_warp_keeps_the_phase_in_a_cycle_and_starts_at_zero(mode):
    t = np.linspace(0, 1, 4001, endpoint=False)
    for a in (0.0, 0.3, 1.0):
        u = core.warp_phase(mode, t, np.full_like(t, a))
        assert u[0] == 0.0
        assert np.all((u >= 0) & (u <= 1)), (mode, a)
    # amount 0 is the plain phase (quantize: 256 steps of it)
    u0 = core.warp_phase(mode, t, np.zeros_like(t))
    assert np.max(np.abs(u0 - t)) <= (1 / 256 if mode == "quantize" else 1e-12)


@pytest.mark.parametrize("shape", ["saw", "sine", "triangle", "square", "wavetable"])
@pytest.mark.parametrize("mode", ["bend_pos", "pd", "mirror", "quantize", "pwm"])
def test_the_warp_is_band_limited_against_a_naive_one(shape, mode):
    """Oversampled 4x, its wrap polyBLEP'd, down through the minimum-phase
    FIR: 10-25 dB less alias than the same warp at the rate with no
    band-limiting (measured at 880 Hz: saw bend -45.5 / pd -42.0 against
    -20.7 / -17.1; sine bend -53.1 against -34.1; a wavetable's mip taken
    as much darker as the warp's steepest slope; the square's mid edge,
    unwrapped, sits at the plain PolyBLEP square's own -34)."""
    f0 = 880.0
    a = core.warped(shape, f0, SR, SR, mode, 0.7, pos=0.6)[2000:]
    b = core.warped(shape, f0, SR, SR, mode, 0.7, pos=0.6, naive=True)[2000:]
    got, naive = _alias_db(a, f0), _alias_db(b, f0)
    assert got < naive - 9.0, (got, naive)
    assert got < -28.0, got


def test_a_warp_at_zero_is_the_plain_oscillator():
    """The oversampled path with the phase unbent: the plain saw's
    harmonics within 0.5 dB to 4.4 kHz, and above that to 15 kHz never
    duller (the 4x polyBLEP droops less than the plain one's; the FIR is
    flat to 10 kHz, -0.3 dB at 18)."""
    plain = core.saw(220.0, SR)
    bent = core.warped("saw", 220.0, SR, SR, "bend_pos", 0.0)
    sp = np.abs(np.fft.rfft(plain[4000:] * np.hanning(SR - 4000)))
    sb = np.abs(np.fft.rfft(bent[4000:] * np.hanning(SR - 4000)))
    f = np.fft.rfftfreq(SR - 4000, 1 / SR)
    for h in range(1, 69):
        i = int(np.argmin(np.abs(f - 220.0 * h)))
        d = 20 * np.log10(sb[i - 2 : i + 3].max() / sp[i - 2 : i + 3].max())
        assert (abs(d) < 0.5) if h <= 20 else (d > -0.3), (h, d)
    assert len(core.WARP_TAPS) == 48 and abs(core.WARP_TAPS.sum() - 1) < 1e-12


def test_the_warp_amount_is_array_safe_and_routable():
    n = SR // 2
    sweep = Patch(osc1_warp="bend_pos", osc1_warp_amt=np.linspace(0, 1, n))
    y = Synth(sweep).note("C3", 0.5, gate=0.4)
    assert np.all(np.isfinite(y)) and np.abs(y).max() > 0.05
    routed = Patch(
        osc1_warp="pd", mod1_src="lfo", mod1_dst="osc1_warp_amt", mod1_amt=0.5
    )
    still = replace(routed, mod1_amt=0.0)
    a = Synth(routed).note("C3", 0.5, gate=0.4)
    b = Synth(still).note("C3", 0.5, gate=0.4)
    assert np.max(np.abs(a - b)) > 1e-3


def test_what_a_warp_ignores():
    """fm, supersaw and a synced osc2 are not bent: the same samples as
    with the warp off."""
    for p in (
        Patch(osc1="fm", osc1_warp="mirror", osc1_warp_amt=0.5),
        Patch(osc1="supersaw", osc1_warp="pd", osc1_warp_amt=0.5),
        Patch(osc2="saw", osc2_sync="on", osc2_warp="pd", osc2_warp_amt=0.5),
    ):
        off = replace(p, osc1_warp="off", osc2_warp="off")
        assert np.array_equal(Synth(p).note("C3", 0.2), Synth(off).note("C3", 0.2))


# --- your own wavetables -------------------------------------------------------------


def test_a_made_table_is_built_like_the_built_ins():
    spec = core._wt_spectra()["basic"]
    st = core.wavetable_stack(np.array([np.asarray(f, np.complex128) for f in spec]))
    assert np.max(np.abs(st - core._WT_STACK["basic"])) < 1e-12


def test_a_clip_becomes_frames_and_a_table():
    t = np.arange(SR) / SR
    tone = np.sin(2 * np.pi * 220 * t) * np.linspace(0, 1, SR) + np.sin(
        2 * np.pi * 440 * t
    ) * np.linspace(1, 0, SR)
    for mode in wavetables.MODES:
        cyc = wavetables.from_audio(tone, 8, mode)
        assert cyc.shape == (8, core._WT_LEN)
    spec = wavetables.spectra(wavetables.from_audio(tone, 8, "pitch"))
    assert spec.shape == (8, wavetables.HARMONICS) and spec.dtype == np.complex64
    # a period of 220 Hz: the first frame's energy on harmonic 1 and 2
    mags = np.abs(spec[0])
    assert mags[:2].sum() > 0.9 * mags.sum()
    tid = wavetables.register(spec)
    assert tid == wavetables.table_id(spec) and wavetables.valid_id(tid)
    y = Synth(Patch(osc1="wavetable", wt_custom=tid, wt_pos=0.5)).note("C3", 0.3)
    assert np.abs(y).max() > 0.05


def test_a_missing_table_plays_the_basic_table():
    missing = Patch(osc1="wavetable", wt_custom="0123456789abcdef", wt_table="vocal")
    basic = Patch(osc1="wavetable", wt_table="basic")
    assert np.array_equal(Synth(missing).note("C3", 0.2), Synth(basic).note("C3", 0.2))
    assert wavetables.clean_id("not a hash") == ""
    assert wavetables.live_url(missing) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1,2",  # too few points
        ";".join(["0," * 63 + "0"] * 5),  # five frames
        "0," * 63 + "nan",
        "0," * 200 + "0",  # too many points
        "a," * 63 + "0",
        "0" * 20000,
    ],
)
def test_a_drawing_is_refused_unless_it_is_one(text):
    assert wavetables.parse_drawing(text) is None


def test_a_drawing_is_its_frames():
    pts = ",".join(f"{np.sin(2 * np.pi * i / 64):.3f}" for i in range(64))
    cyc = wavetables.parse_drawing(pts + ";" + pts)
    assert cyc.shape == (2, core._WT_LEN)
    spec = wavetables.spectra(cyc)
    power = np.abs(spec[0]) ** 2
    assert power[0] > 0.99 * power.sum()  # a sine: harmonic 1


def test_the_live_payload_is_the_stack(tmp_path):
    wavetables.add_dir(tmp_path)
    spec = wavetables.spectra(np.sin(2 * np.pi * np.arange(2048) / 2048)[None, :])
    tid = wavetables.save(tmp_path, spec, "one")
    assert (tmp_path / f"{tid}.npy").is_file() and wavetables.info(tid)["name"] == "one"
    body = wavetables.live_payload(tid)
    n = int.from_bytes(body[:4], "little")
    data = np.frombuffer(body[4 + n :], dtype="<f4")
    assert data.size == len(core._WT_MIPS) * 1 * core._WT_LEN
    # a hostile file in the store is no table
    (tmp_path / "00000000000000ff.npy").write_bytes(b"\x93NUMPY junk")
    assert wavetables.stack("00000000000000ff") is None


# --- one voice -----------------------------------------------------------------------


def _ev(*rows):
    return [(a, d, g, p, s) for a, d, g, p, s in rows]


def test_legato_joins_held_notes_and_goes_back_to_the_last_held():
    p = Patch(voice_mode="legato")
    lines = mono_lines(
        _ev((0.0, 1.0, 1.0, "C4", False), (0.3, 0.3, 0.3, "E4", False)), p
    )
    assert len(lines) == 1
    assert lines[0].path == [(0.3, "E4"), (pytest.approx(0.6), "C4")]
    assert lines[0].gate == 1.0


def test_a_slide_joins_a_legato_line_and_holds_the_gate_into_it():
    p = Patch(voice_mode="legato")
    lines = mono_lines(
        _ev((0.0, 0.5, 0.45, "C4", False), (0.5, 0.5, 0.45, "D4", True)), p
    )
    assert len(lines) == 1 and lines[0].path == [(0.5, "D4")]
    assert lines[0].gate == pytest.approx(0.95)


def test_mono_retriggers_and_glide_mode_decides_the_glide():
    ev = _ev(
        (0.0, 0.5, 0.5, "C4", False),
        (0.4, 0.5, 0.5, "D4", False),  # over a held key
        (1.2, 0.5, 0.5, "F4", False),  # after the keys are up
    )
    always = mono_lines(ev, Patch(voice_mode="mono", glide=0.05))
    assert [ln.from_pitch for ln in always] == [None, "C4", "D4"]
    assert always[0].cut == pytest.approx(0.4) and always[2].cut is None
    legato_only = mono_lines(
        ev, Patch(voice_mode="mono", glide=0.05, glide_mode="legato")
    )
    assert [ln.from_pitch for ln in legato_only] == [None, "C4", None]
    no_glide = mono_lines(ev, Patch(voice_mode="mono"))
    assert [ln.from_pitch for ln in no_glide] == [None, None, None]


def test_a_line_is_cut_with_the_live_voices_fade():
    y = np.ones((SR, 2))
    out = cut_line(y, 0.5)
    f = round(MONO_FADE * SR)
    i = int(0.5 * SR)
    assert len(out) == i + f
    assert out[i, 0] == 1.0 and out[-1, 0] == pytest.approx(1 / f)
    assert cut_line(y, None) is y


def test_a_legato_note_glides_without_a_new_attack():
    """A legato line's second note: the pitch moves, the envelope runs on
    (no dip where the mono line's second note starts again from 0)."""
    p = Patch(voice_mode="legato", attack=0.05, decay=0.1, sustain=0.8, cutoff=8000.0)
    y = Synth(p).note("C4", 1.0, gate=0.9, path=[(0.5, "G4")])[:, 0]
    at = int(0.5 * SR)

    def rms(a, b):
        return float(np.sqrt(np.mean(y[a:b] ** 2)))

    assert rms(at, at + 400) > 0.8 * rms(at - 2000, at)
    song = Song(120)
    song.notes("m", Synth(replace(p, voice_mode="mono")), [("C4", 1), ("G4", 1)])
    song.notes("l", Synth(p), [("C4", 1.2), ("G4", 1)])
    assert song.render(normalize=0).shape[0] > SR
