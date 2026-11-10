"""Shared by the Sample Lab's tests: a song to import, the import itself,
and a fake separator behind the real one's interface (so no test needs the
model file or the network)."""

from __future__ import annotations

import numpy as np

from synthlab import stems
from synthlab.audio import wav_bytes
from synthlab.core import SR
from tests.songs import loop

LAB = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/lab"}
BPM, KEY, BARS = 120, "A minor", 8


def song_wav(bpm: float = BPM, key: str = KEY, bars: int = BARS) -> bytes:
    return wav_bytes(loop(bpm, key, bars) * 0.8)


def upload(client, data: bytes | None = None, name: str = "my song.wav"):
    data = song_wav() if data is None else data
    return client.post(
        "/lab/upload", files={"file": (name, data, "audio/wav")}, headers=LAB
    )


def source_id(client) -> str:
    from synthlab.studio import sources

    return sources.list_sources(client.app.state.svc.data_dir)[0]["id"]


class FilterBank:
    """Four fixed bands as the four "stems" (bass lowest, then drums,
    other, vocals): a separator with the real one's `run`, `sources` and
    `segment`, whose stems sum back to the mix."""

    sources = stems.SOURCES
    segment = SR  # one second
    provider = "fake"
    EDGES = {"bass": (0, 150), "drums": (150, 800), "other": (800, 3000)}

    def __init__(self):
        self.calls = 0

    def run(self, chunk: np.ndarray) -> np.ndarray:
        self.calls += 1
        spec = np.fft.rfft(chunk, axis=1)
        freqs = np.fft.rfftfreq(chunk.shape[1], 1.0 / SR)
        out = []
        for name in self.sources:
            lo, hi = self.EDGES.get(name, (3000, SR))
            band = np.where((freqs >= lo) & (freqs < hi), spec, 0)
            out.append(np.fft.irfft(band, chunk.shape[1], axis=1))
        return np.stack(out).astype(np.float32)


class SixBands(FilterBank):
    """Six bands as the 6-stem model's six stems: the four, then guitar
    and piano out of what was "vocals"."""

    sources = stems.HTDEMUCS_6S.sources
    EDGES = {
        **FilterBank.EDGES,
        "vocals": (3000, 5000),
        "guitar": (5000, 8000),
        "piano": (8000, SR),
    }


class BassSplit(FilterBank):
    """Everything under 100 Hz is the bass, the rest is "other": a clean
    bass stem for the songs whose bass line is a low sine."""

    EDGES = {"bass": (0, 100), "other": (100, SR), "drums": (0, 0), "vocals": (0, 0)}


class OneStem(FilterBank):
    """The whole mix as one stem, silence as the others: for a test about
    *which* stem an action reads, on a part imported on its own."""

    def __init__(self, stem: str):
        super().__init__()
        self.EDGES = {s: ((0, SR) if s == stem else (0, 0)) for s in self.sources}


def fake_separation(client, monkeypatch, bank: FilterBank | None = None):
    """Give the app's Lab a `FilterBank` and a model that is "there"."""
    jobs = client.app.state.svc.lab
    bank = bank or FilterBank()
    monkeypatch.setattr(jobs, "make_separator", lambda: bank)
    monkeypatch.setattr(
        jobs,
        "availability",
        lambda: {"runtime": True, "model": True, "megabytes": 1, "reason": ""},
    )
    return bank


def separate(client, monkeypatch, sid: str | None = None, bank=None) -> str:
    """Separate the (first) source with the fake and wait for the job."""
    fake_separation(client, monkeypatch, bank)
    sid = sid or source_id(client)
    r = client.post("/lab/separate", data={"source": sid}, headers=LAB)
    assert r.status_code == 200, r.text
    client.app.state.svc.lab.join()
    return sid
