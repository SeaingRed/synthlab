"""Stem separation's own code (`synthlab/stems.py`): the chunked
overlap-add around the model, and the model file's download and
verification. None of it needs the real model or the network — the model is
a function, the server a local file.

The two tests that do run the real model are opt-in:

    SYNTHLAB_STEMS_MODEL_DIR=<folder holding htdemucs-e6b688a9.onnx>
    SYNTHLAB_TEST_AUDIO=<folder holding a song and its instrumental>
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import numpy as np
import pytest

from synthlab import stems
from synthlab.core import SR

REAL_OPEN = stems._open  # before conftest's guard replaces it per test
SEG = 4000  # a toy segment: the logic is the same at any length


def _mix(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n) / SR
    tone = 0.3 * np.sin(2 * np.pi * 220 * t)
    return np.column_stack([tone, tone * 0.5]) + rng.standard_normal((n, 2)) * 0.05


def _identity(chunk: np.ndarray) -> np.ndarray:
    """A "model" whose four stems are the input scaled 1, 1/2, 1/4, 0."""
    return np.stack([chunk, chunk * 0.5, chunk * 0.25, chunk * 0.0])


def _expected(mix: np.ndarray, gain: float) -> np.ndarray:
    """What `separate` returns for a stem that is `gain` x the normalised
    mix: the scale comes back, the mix's DC offset does not."""
    return (mix - mix.mean()) * gain


@pytest.mark.parametrize("n", [2, 37, SEG - 1, SEG, SEG + 1, 3 * SEG + 123, 10 * SEG])
def test_an_identity_model_comes_back_exact_at_any_length(n):
    mix = _mix(n)
    out = stems.separate(mix, _identity, segment=SEG)
    assert out.shape == (4, n, 2) and out.dtype == np.float32
    for i, gain in enumerate((1.0, 0.5, 0.25, 0.0)):
        assert np.allclose(out[i], _expected(mix, gain), atol=2e-5), i


def test_chunk_joins_are_crossfades_not_seams():
    """A model that answers each segment at a different level: the output
    must glide between the levels over the overlap, never step."""
    n = 6 * SEG
    t = np.arange(n) / SR
    mix = np.column_stack([np.sin(2 * np.pi * 50 * t)] * 2) * 0.5
    calls = []

    def uneven(chunk):
        calls.append(1)
        return np.stack([chunk * (1.0 if len(calls) % 2 else 0.6)] * 4)

    out = stems.separate(mix, uneven, segment=SEG)[0, :, 0]
    assert len(calls) == len(stems.chunk_starts(n, SEG)[0])
    biggest_step = float(np.max(np.abs(np.diff(out))))
    own_step = float(np.max(np.abs(np.diff(mix[:, 0]))))
    assert biggest_step < 1.5 * own_step  # a seam would be a step of ~0.2


def test_every_sample_is_covered_by_weight_one():
    for total in (1, SEG, 2 * SEG, 5 * SEG + 17):
        starts, lap = stems.chunk_starts(total, SEG)
        weight = np.zeros(total)
        for i, s in enumerate(starts):
            e = min(s + SEG, total)
            weight[s:e] += stems._window(e - s, lap, i == 0, i == len(starts) - 1)
        assert np.allclose(weight, 1.0, atol=1e-6), total
        assert starts[-1] + SEG >= total


def test_silence_is_silent_stems_without_running_the_model():
    def never(chunk):
        raise AssertionError("ran the model on silence")

    out = stems.separate(np.zeros((5000, 2)), never, segment=SEG)
    assert out.shape == (4, 5000, 2) and not np.any(out)
    assert stems.separate(np.zeros((0, 2)), never, segment=SEG).shape == (4, 0, 2)


def test_mono_is_taken_as_dual_mono_and_other_shapes_refused():
    out = stems.separate(_mix(SEG)[:, 0], _identity, segment=SEG)
    assert out.shape == (4, SEG, 2)
    with pytest.raises(stems.StemsError):
        stems.separate(np.zeros((100, 3)), _identity, segment=SEG)


def test_a_model_returning_the_wrong_shape_is_an_error_not_a_crash():
    with pytest.raises(stems.StemsError):
        stems.separate(_mix(SEG), lambda c: c, segment=SEG)


def test_progress_counts_segments_and_cancel_stops_before_the_next():
    seen = []
    mix = _mix(4 * SEG)
    stems.separate(
        mix, _identity, segment=SEG, progress=lambda d, t: seen.append((d, t))
    )
    total = len(stems.chunk_starts(len(mix), SEG)[0])
    assert seen == [(i + 1, total) for i in range(total)]
    ran = []

    def counting(chunk):
        ran.append(1)
        return _identity(chunk)

    with pytest.raises(stems.Cancelled):
        stems.separate(mix, counting, segment=SEG, cancel=lambda: len(ran) >= 2)
    assert len(ran) == 2


def test_sdr_is_the_plain_ratio():
    ref = np.ones(100)
    assert stems.sdr(ref, ref) > 100
    assert abs(stems.sdr(ref, ref * 0.9) - 20.0) < 1e-6
    assert stems.sdr(np.zeros(10), ref[:10]) == float("-inf")


# --- the model file ----------------------------------------------------------


def _model(data: bytes, **over) -> stems.Model:
    spec = {
        "id": "toy",
        "url": "https://example.invalid/toy.onnx",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "filename": "toy.onnx",
    }
    return stems.Model(**{**spec, **over})


def _serving(data: bytes):
    asked = []

    def opener(url):
        asked.append(url)
        return io.BytesIO(data)

    return opener, asked


def test_a_download_is_installed_only_after_its_hash_matched(tmp_path):
    data = os.urandom(3_500_000)
    model = _model(data)
    opener, asked = _serving(data)
    seen = []
    path = stems.download(
        model, tmp_path, progress=lambda d, t: seen.append((d, t)), opener=opener
    )
    assert path == tmp_path / "toy.onnx" and path.read_bytes() == data
    assert stems.verify(path, model)
    assert seen[-1] == (len(data), len(data)) and len(seen) == 4  # 1 MiB reads
    assert asked == [model.url]
    assert [p.name for p in tmp_path.iterdir()] == ["toy.onnx"]  # no temp left
    # a verified copy is not fetched again
    assert stems.download(model, tmp_path, opener=opener) == path
    assert asked == [model.url]


def test_a_download_with_the_wrong_hash_is_deleted_and_refused(tmp_path):
    data = os.urandom(200_000)
    model = _model(data, sha256="0" * 64)
    with pytest.raises(stems.StemsError, match="SHA-256"):
        stems.download(model, tmp_path, opener=_serving(data)[0])
    assert list(tmp_path.iterdir()) == []
    assert not stems.verify(tmp_path / "toy.onnx", model)


@pytest.mark.parametrize("sent", ["short", "long"])
def test_a_download_of_the_wrong_length_is_refused(tmp_path, sent):
    data = os.urandom(100_000)
    model = _model(data)
    body = data[:50_000] if sent == "short" else data + os.urandom(2_000_000)
    with pytest.raises(stems.StemsError):
        stems.download(model, tmp_path, opener=_serving(body)[0])
    assert list(tmp_path.iterdir()) == []


def test_a_cancelled_or_failed_download_leaves_nothing(tmp_path):
    data = os.urandom(3_000_000)
    model = _model(data)
    with pytest.raises(stems.Cancelled):
        stems.download(model, tmp_path, opener=_serving(data)[0], cancel=lambda: True)

    def dropped(url):
        raise OSError("connection reset")

    with pytest.raises(stems.StemsError, match="download failed"):
        stems.download(model, tmp_path, opener=dropped)
    assert list(tmp_path.iterdir()) == []


def test_a_tampered_file_in_the_cache_is_not_the_model(tmp_path):
    data = os.urandom(50_000)
    model = _model(data)
    path = tmp_path / "toy.onnx"
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))  # same size, one bit off
    assert not stems.verify(path, model)
    assert not stems.availability(model, tmp_path)["model"]
    path.write_bytes(data[:-1])  # wrong size: refused without hashing
    assert not stems.verify(path, model)


def test_downloads_are_https_only_and_the_pinned_model_names_a_commit():
    with pytest.raises(stems.StemsError, match="HTTPS"):
        REAL_OPEN("http://example.invalid/model.onnx")
    with pytest.raises(stems.StemsError, match="HTTPS"):
        REAL_OPEN("file:///etc/passwd")
    m = stems.HTDEMUCS
    assert m.url.startswith("https://huggingface.co/")
    assert "/resolve/e6b688a9a9f977e2efaba79d0f18b9b0cfc606ea/" in m.url  # not main
    assert len(m.sha256) == 64 and m.size <= stems.MAX_MODEL_BYTES


def test_the_cache_is_outside_the_checkout_and_the_env_var_moves_it(monkeypatch):
    monkeypatch.delenv(stems.MODEL_ENV, raising=False)
    repo = Path(__file__).resolve().parents[1]
    assert repo not in stems.cache_dir().resolve().parents
    monkeypatch.setenv(stems.MODEL_ENV, str(repo / "elsewhere"))
    assert stems.model_path() == repo / "elsewhere" / stems.HTDEMUCS.filename


def test_without_onnxruntime_the_button_gets_a_sentence(monkeypatch, tmp_path):
    def missing():
        raise stems.StemsError("Stem separation needs the onnxruntime package.")

    monkeypatch.setattr(stems, "runtime", missing)
    have = stems.availability(stems.HTDEMUCS, tmp_path)
    assert have["runtime"] is False and "onnxruntime" in have["reason"]
    with pytest.raises(stems.StemsError):
        stems.OnnxSeparator(stems.HTDEMUCS, tmp_path)


def test_the_thread_count_leaves_headroom(monkeypatch):
    monkeypatch.delenv(stems.THREADS_ENV, raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    assert stems.threads() == 8
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert stems.threads() == 2
    monkeypatch.setenv(stems.THREADS_ENV, "3")
    assert stems.threads() == 3


# --- opt-in: the real model --------------------------------------------------

MODEL_DIR = os.environ.get("SYNTHLAB_STEMS_MODEL_DIR", "")
AUDIO_DIR = os.environ.get("SYNTHLAB_TEST_AUDIO", "")
needs_model = pytest.mark.skipif(
    not MODEL_DIR, reason="set SYNTHLAB_STEMS_MODEL_DIR to run the real model"
)


@needs_model
def test_the_real_model_finds_the_drums_and_bass_of_a_synthetic_mix():
    """A mix the engine rendered, whose true parts are known: the drum and
    bass stems must each correlate best, and well, with their own part.
    The engine's formant "voice" is a synth and the model files it under
    "other" (measured: 0.87 with the other stem, 0.08 with vocals), so the
    two pitched parts are only held to landing in other + vocals together.
    Not a claim about real records."""
    from tests.songs import build
    from tests.songs import stems as true_stems

    seconds = 8 * 4 * 60.0 / 120
    truth = true_stems(build(120, "A minor", 8, vocal=True), seconds)
    mix = np.sum(list(truth.values()), axis=0)
    scale = float(np.max(np.abs(mix)))
    sep = stems.OnnxSeparator(stems.HTDEMUCS, Path(MODEL_DIR))
    out = dict(zip(sep.sources, stems.separate(mix / scale, sep.run), strict=True))

    def corr(a, b):
        return abs(float(np.corrcoef(a.mean(axis=1), b.mean(axis=1))[0, 1]))

    for name in ("drums", "bass"):
        scores = {k: corr(out[name], v) for k, v in truth.items()}
        assert max(scores, key=scores.get) == name and scores[name] > 0.8, scores
    pitched = out["other"] + out["vocals"]
    assert corr(pitched, truth["other"] + truth["vocals"]) > 0.8
    # the model's stems are four predictions, not a partition: close, not exact
    assert corr(sum(out.values()), mix) > 0.95


@needs_model
@pytest.mark.skipif(not AUDIO_DIR, reason="set SYNTHLAB_TEST_AUDIO to a song folder")
def test_the_real_model_finds_the_vocal_of_a_real_song():
    """`<folder>` holds a song and its instrumental ("NoVox" in its name).
    The pair measured here is the same performance
    but not the same samples — the two files correlate 0.26 at the best
    lag — so "full minus instrumental" is no vocal reference. What does
    hold: the stems sum back to the mix, the vocal stem carries nothing of
    the instrumental, and it is a real part of the song, not silence."""
    import soundfile as sf

    files = sorted(Path(AUDIO_DIR).glob("*.mp3"))
    full = next(p for p in files if "NoVox" not in p.name)
    inst = next(p for p in files if "NoVox" in p.name)
    seconds = 40
    a, sr = sf.read(str(full), start=60 * SR, frames=seconds * SR, dtype="float32")
    b, _ = sf.read(str(inst), start=60 * SR, frames=seconds * SR, dtype="float32")
    assert sr == SR
    sep = stems.OnnxSeparator(stems.HTDEMUCS, Path(MODEL_DIR))
    out = stems.separate(a, sep.run)
    vocals = out[sep.sources.index("vocals")]

    def corr(x, y):
        return float(np.corrcoef(x.mean(axis=1), y.mean(axis=1))[0, 1])

    assert corr(out.sum(axis=0), a) > 0.99
    assert abs(corr(vocals, b)) < 0.1
    level = float(np.sqrt(np.mean(vocals**2)) / np.sqrt(np.mean(a**2)))
    assert 0.1 < level < 0.9, level
