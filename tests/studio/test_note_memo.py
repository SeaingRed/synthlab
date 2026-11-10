"""The loop renders' note memo (`arrange.NoteMemo`): a one-cell edit costs
one note, and a memoised render is the render, sample for sample."""

from dataclasses import replace

import numpy as np

from synthlab import core
from synthlab.studio.arrange import NoteMemo, build_song
from synthlab.studio.state import Arrangement
from synthlab.synth import Synth, preset


def _arr(cells):
    return Arrangement(bars=1, steps_per_bar=8, cells=cells)


def test_a_memoised_render_is_the_render():
    p = preset("pluck_glass")
    memo = NoteMemo()
    cells = [["A1", 0], ["C2", 2], ["E2", 4], ["A1", 6]]
    plain = build_song(p, _arr(cells)).render(normalize=0)
    first = build_song(p, _arr(cells), memo=memo).render(normalize=0)
    again = build_song(p, _arr(cells), memo=memo).render(normalize=0)
    assert np.array_equal(plain, first) and np.array_equal(plain, again)
    assert memo.lru.stats()["hits"] >= 3


def test_an_edit_renders_only_the_note_that_changed():
    p = preset("pluck_glass")
    memo = NoteMemo()
    build_song(p, _arr([["A1", 0], ["C2", 2]]), memo=memo).render(normalize=0)
    before = memo.lru.stats()["misses"]
    edited = _arr([["A1", 0], ["C2", 2], ["G2", 5]])
    out = build_song(p, edited, memo=memo).render(normalize=0)
    assert memo.lru.stats()["misses"] - before == 1
    assert np.array_equal(out, build_song(p, edited).render(normalize=0))


def test_a_swapped_engine_misses_the_memo(monkeypatch):
    """A spy on Synth.note or another noise source is a different engine:
    it must render, not be served what the real one made."""
    p = preset("pluck_glass")
    memo = NoteMemo()
    build_song(p, _arr([["A1", 0]]), memo=memo).render(normalize=0)
    calls = []
    real = Synth.note

    def spy(self, *a, **k):
        calls.append(a[0])
        return real(self, *a, **k)

    monkeypatch.setattr(Synth, "note", spy)
    build_song(p, _arr([["A1", 0]]), memo=memo).render(normalize=0)
    assert calls == ["A1"]
    orig = core.noise
    monkeypatch.setattr(core, "noise", lambda *a, **k: orig(*a, **k))
    build_song(p, _arr([["A1", 0]]), memo=memo).render(normalize=0)
    assert calls == ["A1", "A1"]


def test_patch_edits_are_new_notes():
    memo = NoteMemo()
    p = preset("pluck_glass")
    a = build_song(p, _arr([["A1", 0]]), memo=memo).render(normalize=0)
    darker = replace(p, cutoff=p.cutoff / 3)
    b = build_song(darker, _arr([["A1", 0]]), memo=memo).render(normalize=0)
    assert not np.array_equal(a, b)
