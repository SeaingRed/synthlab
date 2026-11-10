"""Stem-cached rendering: same audio as the monolithic path, cached
sections are actually reused."""

import numpy as np

from synthlab.core import SR
from synthlab.effects import to_stereo
from synthlab.studio.arrange import build_song, loop_seconds
from synthlab.studio.render import (
    RenderInputs,
    StemCache,
    _stem_key,
    normalize_master,
    render_audio,
)
from synthlab.studio.state import Arrangement
from synthlab.synth import preset

# Captured from _stem_key(...) on the unmodified pre-Task-7 code — the stem
# cache's own keys are unchanged by the RenderInputs refactor.
LEGACY_STEM_KEY = "drums:51a627172c179fbd"

LAYER = {
    "patch": {"name": "sub", "osc1": "sine", "cutoff": 300.0},
    "cells": [["A1", 0], ["A1", 2]],
    "muted": False,
    "gain": 1.0,
}

ARR = Arrangement(
    cells=[["A1", 0], ["C2", 2]],
    drums=[["kick", 0], ["kick", 2], ["hat", 1], ["hat", 3]],
)


def _monolithic(patch, arr, layers=None):
    """The same loop rendered in one pass, as the reference the stem path
    has to reproduce. It has to fold the tail over the loop point and
    normalize exactly the way the real path does — a plain truncate-and-
    peak-scale stopped matching once note releases got room to ring."""
    audio = build_song(patch, arr, None, layers).render(normalize=0)
    n = int(round(loop_seconds(arr) * SR))
    st = to_stereo(np.asarray(audio, dtype=np.float64))
    if len(st) < n:
        st = np.vstack([st, np.zeros((n - len(st), 2))])
    core = st[:n].copy()
    tail = st[n : 2 * n]
    if len(tail):
        core[: len(tail)] += tail
    return normalize_master(core)


def test_stem_render_matches_monolithic():
    patch = preset("bass_reese")
    got = render_audio(RenderInputs(patch=patch, arr=ARR, layers=[LAYER]))
    want = _monolithic(patch, ARR, layers=[LAYER])
    np.testing.assert_allclose(got, want, atol=1e-9)


def test_stem_cache_reuses_unchanged_sections():
    patch = preset("bass_reese")
    cache = StemCache()
    render_audio(RenderInputs(patch=patch, arr=ARR, layers=[LAYER]), stem_cache=cache)
    keys = list(cache._items)
    assert len(keys) == 3  # main + layer + drums
    # tamper with the cached drum stem: if it's reused, the tampering shows
    drum_key = next(k for k in keys if k.startswith("drums:"))
    cache._items[drum_key] = np.zeros_like(cache._items[drum_key])
    tweaked = patch.tweak(cutoff=333.0)  # only the main lane changes
    out = render_audio(
        RenderInputs(patch=tweaked, arr=ARR, layers=[LAYER]), stem_cache=cache
    )
    ref = render_audio(
        RenderInputs(patch=tweaked, arr=ARR, layers=[LAYER])
    )  # no cache: real drums
    assert not np.allclose(out, ref, atol=1e-9)  # zeroed drums were reused
    # and the changed main lane produced a fresh main stem (4th entry)
    assert sum(k.startswith("main:") for k in cache._items) == 2


def test_stem_keys_are_stable_and_distinct():
    a = _stem_key("drums", {"drums": [["kick", 0]], "bpm": 124.0})
    b = _stem_key("drums", {"drums": [["kick", 0]], "bpm": 124.0})
    c = _stem_key("drums", {"drums": [["kick", 1]], "bpm": 124.0})
    assert a == b != c


def test_stem_key_matches_legacy():
    # pin: the stem cache's own keys must not move under the refactor
    assert _stem_key("drums", {"drums": [["kick", 0]], "bpm": 124.0}) == LEGACY_STEM_KEY


def test_empty_loop_renders_silence():
    arr = Arrangement(cells=[], drums=[])
    out = render_audio(RenderInputs(patch=preset("bass_reese"), arr=arr))
    assert out.shape[1] == 2
    assert float(np.max(np.abs(out))) == 0.0
