"""Loudness targets on the song export (`song_play.master`,
`/export/song.wav?loudness=`): the mixdown mastered to an integrated
loudness (LUFS, `analysis.loudness`) and limited as a one-shot. The
default target is the export as it always was, byte for byte; playback,
the plan's WAVs and the stems never see any of it.
"""

from __future__ import annotations

import io
import math
import zipfile

import numpy as np
import pytest

from synthlab.analysis import integrated_loudness, peak_db, true_peak_db
from synthlab.core import SR
from synthlab.studio import song_play
from tests.studio.test_song_export import (
    REVERB,
    _pcm,
    _three_section_song,
    _use,
    client,  # noqa: F401  (the fixture)
)

CEILING_DB = 20 * math.log10(song_play.MASTER_CEILING)


def _float(data: bytes) -> np.ndarray:
    return _pcm(data) / 32767.0


def _music(seconds=6.0, level=0.2, seed=1):
    """Something with a body and peaks: a chord under sparse loud clicks."""
    t = np.arange(int(seconds * SR)) / SR
    x = sum(np.sin(2 * np.pi * f * t) for f in (110.0, 277.0, 660.0)) * level / 3
    x = np.column_stack([x, x * 0.8])
    hits = np.random.default_rng(seed).integers(0, len(t), 40)
    x[hits] += 0.7
    return x


def test_the_targets_are_peak_first_then_two_loudnesses():
    """The first is the default and the select's first option."""
    assert list(song_play.LOUDNESS_TARGETS) == ["peak", "streaming", "club"]
    lufs = [t["lufs"] for t in song_play.LOUDNESS_TARGETS.values()]
    assert lufs == [None, -14.0, -9.0]
    assert song_play.MASTER_CEILING == pytest.approx(10 ** (-1 / 20))


def test_master_lands_on_the_target_under_the_ceiling():
    x = _music()
    assert integrated_loudness(x) < -20
    out = song_play.master(x, -14.0)
    assert out.shape == x.shape
    # one gain lands 0.46 LU short (the limiter ducks under every click):
    # the correction passes close that
    assert integrated_loudness(out) == pytest.approx(-14.0, abs=0.15)
    assert true_peak_db(out) <= CEILING_DB + 1e-6
    report = song_play.loudness_report(out)
    assert report["lufs"] == pytest.approx(-14.0, abs=0.3)
    assert report["true_peak_db"] <= -1.0 and report["peak_db"] <= -1.0


def test_master_turns_a_loud_mix_down_too():
    x = _music(level=0.9)
    assert integrated_loudness(x) > -14
    out = song_play.master(x, -14.0)
    assert integrated_loudness(out) == pytest.approx(-14.0, abs=0.3)


def test_pushed_past_what_the_limiter_allows_it_reports_where_it_landed():
    """A full-scale sine can't be -1 dB at its peaks and louder than that
    allows: the ceiling wins, and the report says so."""
    t = np.arange(4 * SR) / SR
    x = np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.5
    out = song_play.master(x, 3.0)
    assert true_peak_db(out) <= CEILING_DB + 1e-6
    assert song_play.loudness_report(out)["lufs"] < 0.0


def test_the_limiter_runs_as_a_one_shot():
    """A loud opening must not turn the quiet ending down: on a loop the
    last block looks ahead to the first one (`fx.limit(loop=True)`)."""
    t = np.arange(4 * SR) / SR
    x = np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.05
    x[:4000] *= 60.0  # an opening far over the ceiling
    out = song_play.master(x, integrated_loudness(x))

    def gain(at):  # what the master did to the level around sample `at`
        sl = slice(at - 200, at)
        return np.max(np.abs(out[sl])) / np.max(np.abs(x[sl]))

    assert gain(4000) < 0.5 * gain(2 * SR)  # the opening was limited
    assert gain(len(x)) == pytest.approx(gain(2 * SR), rel=0.01)  # the end was not


def test_peak_and_silence_come_back_untouched():
    x = _music()
    assert song_play.master(x, None) is x
    silent = np.zeros((SR, 2))
    assert song_play.master(silent, -14.0) is silent
    assert song_play.loudness_report(silent) == {
        "lufs": None,
        "peak_db": None,
        "true_peak_db": None,
    }


def test_the_default_export_is_the_export_it_always_was(client):  # noqa: F811
    _use(client, _three_section_song(), master=REVERB)
    plain = client.get("/export/song.wav")
    named = client.get("/export/song.wav?loudness=peak")
    assert plain.content == named.content
    want = 'attachment; filename="synthlab-song.wav"'
    assert plain.headers["content-disposition"] == want
    assert named.headers["content-disposition"] == want
    assert "x-loudness-lufs" not in plain.headers
    # and it is the mixdown, straight
    svc = client.app.state.svc
    song, master = svc.song_snapshot()
    assert plain.content == _wav(svc.song_mixdown(song, master))


def _wav(audio) -> bytes:
    from synthlab.studio.render import _to_wav_bytes

    return _to_wav_bytes(audio)


@pytest.mark.parametrize("name", ["streaming", "club"])
def test_a_loudness_target_masters_the_export_and_says_what_it_measures(client, name):  # noqa: F811
    _use(client, _three_section_song())
    plain = _float(client.get("/export/song.wav").content)
    r = client.get(f"/export/song.wav?loudness={name}")
    assert r.status_code == 200
    out = _float(r.content)
    assert out.shape == plain.shape  # the same song, sample for sample in time
    target = song_play.LOUDNESS_TARGETS[name]["lufs"]
    got = integrated_loudness(out)
    assert got <= target + 0.1  # never over the target
    assert true_peak_db(out) <= CEILING_DB + 0.05  # 16-bit rounding
    assert peak_db(out) <= CEILING_DB + 0.01
    # the response says what the file measures
    assert float(r.headers["x-loudness-lufs"]) == pytest.approx(got, abs=0.1)
    assert float(r.headers["x-true-peak-dbtp"]) == pytest.approx(
        true_peak_db(out), abs=0.1
    )
    assert float(r.headers["x-peak-dbfs"]) == pytest.approx(peak_db(out), abs=0.1)
    lufs = r.headers["x-loudness-lufs"]
    assert f"synthlab-song ({lufs} LUFS).wav" in r.headers["content-disposition"]


def test_streaming_lands_on_minus_14(client):  # noqa: F811
    """This song has the headroom: the limiter barely works at -14."""
    _use(client, _three_section_song())
    r = client.get("/export/song.wav?loudness=streaming")
    assert float(r.headers["x-loudness-lufs"]) == pytest.approx(-14.0, abs=0.5)


@pytest.mark.parametrize("bad", ["loud", "-14", "PEAK", "", "x" * 40, "peak;rm"])
@pytest.mark.parametrize("url", ["/export/song.wav", "/export/song/loudness"])
def test_an_unknown_target_is_refused(client, url, bad):  # noqa: F811
    _use(client, _three_section_song())
    assert client.get(url, params={"loudness": bad}).status_code == 422


def test_measure_reads_what_the_export_would(client):  # noqa: F811
    _use(client, _three_section_song())
    r = client.get("/export/song/loudness?loudness=streaming")
    wav = client.get("/export/song.wav?loudness=streaming")
    assert r.status_code == 200
    assert r.text.startswith('<output id="song-loudness"')
    lufs, peak = wav.headers["x-loudness-lufs"], wav.headers["x-true-peak-dbtp"]
    assert f">{lufs} LUFS · true peak {peak} dB</output>" in r.text
    # "as played" measures too, and changes nothing
    plain = client.get("/export/song/loudness")
    assert " LUFS · true peak " in plain.text


def test_an_empty_or_silent_song_has_nothing_to_measure(client):  # noqa: F811
    assert client.get("/export/song/loudness").status_code == 404
    assert client.get("/export/song.wav?loudness=club").status_code == 404
    song = _three_section_song()
    for sec in song["sections"]:
        sec["cells"] = {}
    _use(client, song)
    r = client.get("/export/song/loudness?loudness=club")
    assert "too quiet or too short to measure" in r.text
    silent = client.get("/export/song.wav?loudness=club")
    assert silent.status_code == 200 and "x-loudness-lufs" not in silent.headers
    assert 'filename="synthlab-song.wav"' in silent.headers["content-disposition"]
    assert not _pcm(silent.content).any()


def test_playback_and_stems_never_see_the_target(client):  # noqa: F811
    _use(client, _three_section_song())
    plan = client.get("/song/plan.json").json()
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        stems = {n: z.read(n) for n in z.namelist()}
    client.get("/export/song.wav?loudness=club")
    assert client.get("/song/plan.json").json() == plan
    with zipfile.ZipFile(io.BytesIO(client.get("/export/stems.zip").content)) as z:
        assert {n: z.read(n) for n in z.namelist()} == stems


def test_the_song_page_offers_the_targets_beside_the_export(client):  # noqa: F811
    _use(client, _three_section_song())
    html = client.get("/view/song").text
    assert (
        '<form id="song-export-form" class="song-export-form"' in html
    )  # under Export's ▾
    assert 'method="get" action="/export/song.wav"' in html
    for key, target in song_play.LOUDNESS_TARGETS.items():
        assert f'<option value="{key}"' in html and target["label"] in html
    assert '<option value="peak" selected>' in html
    assert 'hx-get="/export/song/loudness"' in html
    assert 'hx-include="#song-loudness-target"' in html
    assert 'id="song-loudness"' in html and 'href="/export/stems.zip"' in html
    form = html.split('<form id="song-export-form"')[1].split("</form>")[0]
    assert "<script" not in form and "onclick" not in form and "onchange" not in form
