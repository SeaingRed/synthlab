import io
import wave

import pytest

from synthlab.studio.render import (
    RenderCache,
    RenderError,
    RenderInputs,
    RenderResult,
    patch_key,
    render_loop,
)
from synthlab.studio.state import Arrangement
from synthlab.synth import Patch, preset

# First captured from render_key(...) on the unmodified code; moved on
# purpose when `render.RENDER_REV` joined the key (rev 1) and a drum-less
# render stopped hashing the drum lane, and with each bump since (rev 3:
# the pitch unit's splice). Every
# existing cache identity must survive the RenderInputs refactor, so these
# are pinned literals, not recomputed values.
LEGACY_KEY = "ce54acbfa2e4"  # default Patch/Arrangement, parts=("synth","drums")
LEGACY_PERFORM_KEY = "6d59e1b11bfa"  # layers + main/master/drums fx + clip_root
LEGACY_DRUMS_KEY = "c2a085923a94"  # drums-only


def test_render_returns_valid_wav_of_loop_length():
    arr = Arrangement(bpm=120.0, cells=[["A1", 0]])
    res = render_loop(RenderInputs(patch=preset("bass_reese"), arr=arr))
    with wave.open(io.BytesIO(res.wav)) as w:
        assert w.getnchannels() == 2
        assert w.getsampwidth() == 2
        seconds = w.getnframes() / w.getframerate()
    assert seconds == pytest.approx(2.0, abs=0.05)  # 1 bar @ 120bpm
    assert set(res.analysis) == {"peak_db", "rms_db", "sub_pct", "centroid"}


def test_key_is_stable_and_sensitive():
    arr = Arrangement(cells=[["A1", 0]])
    p = preset("bass_reese")
    assert RenderInputs(patch=p, arr=arr).key() == RenderInputs(patch=p, arr=arr).key()
    assert (
        RenderInputs(patch=p, arr=arr).key()
        != RenderInputs(patch=p.tweak(cutoff=999.0), arr=arr).key()
    )
    assert (
        RenderInputs(patch=p, arr=arr).key()
        != RenderInputs(patch=p, arr=Arrangement(cells=[["C2", 0]])).key()
    )
    assert len(RenderInputs(patch=p, arr=arr).key()) == 12


def test_key_matches_legacy_render_key():
    # pin: the refactor must not invalidate caches or change identities
    inp = RenderInputs(patch=Patch(), arr=Arrangement(), parts=("synth", "drums"))
    assert inp.key() == LEGACY_KEY


def test_key_matches_legacy_render_key_perform_view(tmp_path):
    layer = {
        "patch": {"name": "sub", "osc1": "sine", "cutoff": 300.0},
        "cells": [["A1", 0]],
        "muted": False,
        "gain": 1.0,
    }
    arr = Arrangement(cells=[["A1", 0], ["C2", 2]], drums=[["kick", 0], ["kick", 2]])
    inp = RenderInputs(
        patch=Patch(),
        arr=arr,
        curves={"cutoff": [[0.0, 0.0], [1.0, 1.0]]},
        layers=[layer],
        main_muted=False,
        main_fx=[{"kind": "reverb", "params": {"decay": 2.5, "mix": 0.5}}],
        master_fx=[{"kind": "bitcrush", "params": {"bits": 5.0}}],
        drums_fx=[{"kind": "compressor", "params": {}}],
        parts=("synth", "drums"),
        clip_root="C:/clips",
    )
    assert inp.key() == LEGACY_PERFORM_KEY


def test_key_matches_legacy_render_key_drums_only():
    arr = Arrangement(drums=[["kick", 0], ["hat", 1]])
    inp = RenderInputs(patch=Patch(), arr=arr, parts=("drums",))
    assert inp.key() == LEGACY_DRUMS_KEY


def test_patch_key_is_pinned():
    # Patch.to_json() must serialize identically to the old
    # `{k: v for k, v in asdict(patch).items() if k != "voice_fx"}`
    # comprehension patch_key used to be — this literal was
    # captured from the unmodified code, so any change here means a cache
    # key (and every cached render) silently changed identity.
    assert patch_key(Patch()) == "1a04ddc6b00b"


def test_length_cap_raises_render_error(monkeypatch):
    """Nothing the controls offer reaches the real cap (72 s: 12 bars at
    40 bpm), so the guard is shown at a smaller one."""
    from synthlab.studio import render

    monkeypatch.setattr(render, "LOOP_CAP_SECONDS", 10.0)
    arr = Arrangement(bpm=40.0, bars=4)  # 4 bars @ 40bpm = 24s
    with pytest.raises(RenderError):
        render_loop(RenderInputs(patch=preset("bass_reese"), arr=arr))


def test_nan_guard_raises_render_error(monkeypatch):
    import numpy as np

    import synthlab.studio.render as render_mod

    def bad_song(patch, arr):
        class FakeSong:
            def render(self, **kw):
                return np.full((1000, 2), np.nan)

        return FakeSong()

    monkeypatch.setattr(render_mod, "build_song", bad_song)
    with pytest.raises(RenderError):
        render_loop(RenderInputs(patch=Patch(), arr=Arrangement(cells=[["A1", 0]])))


def test_cache_lru_evicts_beyond_its_entry_cap():
    # 256 entries (not 50, shared with key one-shots); the byte
    # bound is pinned in test_note_cache.py
    cache = RenderCache()
    arr = Arrangement(cells=[["A1", 0]])
    first = render_loop(RenderInputs(patch=preset("bass_reese"), arr=arr))
    cache.put(first)
    assert cache.get(first.key) is first
    # Use synthetic RenderResult objects for filler entries per controller ruling
    for i in range(260):
        fake = RenderResult(key=f"k{i:011d}x", wav=b"", analysis={})
        cache.put(fake)
    assert cache.get(first.key) is None  # evicted
