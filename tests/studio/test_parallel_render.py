"""Notes and stems render on thread pools (`sequencer.render_each`,
`render._each`). Order, errors and the audio itself must be exactly what
the one-at-a-time loops gave."""

import numpy as np
import pytest

from synthlab import sequencer
from synthlab.studio import arrange, render
from synthlab.studio.render import RenderInputs, render_audio
from synthlab.studio.state import Arrangement
from synthlab.synth import preset

LAYER = {
    "patch": {"name": "sub", "osc1": "sine", "cutoff": 300.0},
    "cells": [["A1", 0], ["A1", 2], ["C2", 5]],
    "muted": False,
    "gain": 1.0,
}
ARR = Arrangement(
    cells=[["A1", 0], ["C2", 2], ["E2", 3], ["G2", 6], ["A1", 9], ["C2", 12]],
    drums=[["kick", 0], ["kick", 8], ["hat", 2], ["hat", 6]],
)


def test_render_each_keeps_order():
    thunks = [lambda i=i: i * i for i in range(40)]
    assert sequencer.render_each(thunks) == [i * i for i in range(40)]
    assert render._each(lambda i: -i, list(range(40))) == [-i for i in range(40)]


def test_render_each_propagates_errors():
    def boom():
        raise ValueError("bad note")

    with pytest.raises(ValueError, match="bad note"):
        sequencer.render_each([lambda: 1, boom, lambda: 3])


def _serial(monkeypatch):
    monkeypatch.setattr(render, "_each", lambda fn, items: [fn(x) for x in items])
    thunks = lambda fs: [f() for f in fs]  # noqa: E731
    monkeypatch.setattr(sequencer, "render_each", thunks)
    monkeypatch.setattr(arrange, "render_each", thunks)


@pytest.mark.parametrize(
    "curves",
    [None, {"cutoff": [[0.0, 0.1], [0.5, 0.8], [1.0, 0.2]]}],
    ids=["shared-patch", "curved"],
)
def test_parallel_loop_is_sample_identical_to_serial(monkeypatch, curves):
    inp = RenderInputs(
        patch=preset("bass_reese"), arr=ARR, layers=[LAYER], curves=curves
    )
    parallel = render_audio(inp)
    with monkeypatch.context() as m:
        _serial(m)
        serial = render_audio(inp)
    np.testing.assert_array_equal(parallel, serial)


def test_a_song_warmed_side_by_side_is_the_serial_song(tmp_path, monkeypatch):
    """`SongRender._warm` renders a song's uncached sections on the section
    pool before the gain's loop reads them: the same gain and the same
    mixdown as the loop alone, and every section rendered once."""
    from synthlab.studio import service
    from synthlab.studio.gen import build
    from synthlab.studio.server import create_app
    from synthlab.studio.service.song import SongRender

    song = build.start("trance", 1).song
    master = [{"kind": "reverb", "params": {"decay": 1.0, "mix": 0.2, "size": 0.5}}]
    calls = []
    real = service.section_master_audio

    def counted(parts, *args, **kwargs):
        calls.append(1)
        return real(parts, *args, **kwargs)

    monkeypatch.setattr(service, "section_master_audio", counted)
    svc = create_app(data_dir=tmp_path / "a").state.svc
    gain = svc.song_gain(song, master)
    assert len(calls) == len(song["sections"])
    parallel = svc.song_mixdown(song, master)
    with monkeypatch.context() as m:
        m.setattr(SongRender, "_warm", lambda self, entry, master_fx: None)
        other = create_app(data_dir=tmp_path / "b").state.svc
        assert other.song_gain(song, master) == gain
        np.testing.assert_array_equal(parallel, other.song_mixdown(song, master))
    # sections that hold the same stem (the same pattern over the same
    # bars) render it once between them, not once each: as many stems were
    # put as the serial song put
    assert svc.stems.stats()["entries"] == other.stems.stats()["entries"]
    assert svc.stems.stats()["misses"] <= other.stems.stats()["misses"] + len(
        song["sections"]
    )


def test_one_at_a_time_runs_a_key_once_and_hands_the_rest_the_cache():
    import threading
    import time

    from synthlab.studio.render import StemCache, _one_at_a_time

    cache, made, gate = StemCache(), [], threading.Barrier(6)

    def want():
        gate.wait()
        with _one_at_a_time("k"):
            held = cache.peek("k")
            if held is None:
                time.sleep(0.05)
                made.append(1)
                cache.put("k", np.zeros((4, 2)))

    threads = [threading.Thread(target=want) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1 and cache.stats()["misses"] == 0  # peek counts nothing


def test_warm_stands_aside_when_the_cache_cannot_hold_the_song(tmp_path, monkeypatch):
    from synthlab.studio.gen import build
    from synthlab.studio.server import create_app
    from synthlab.studio.service import song as song_unit

    svc = create_app(data_dir=tmp_path).state.svc
    monkeypatch.setattr(svc.stems, "max_bytes", 8 << 20)
    used = []
    monkeypatch.setattr(song_unit, "section_pool", lambda: used.append(1))
    song = build.start("trance", 1).song
    assert svc.song_gain(song, []) > 0 and not used
