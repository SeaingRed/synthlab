"""A take plays where it was, in Chromium: its audio on its notes'
stamps.

What these journeys prove: a take recorded with nothing playing is cut so
its notes sound where their stamps say (a note scheduled on the clock, as
the keys arp posts one, and a press as a hand plays one) within one render
quantum — it was ~70 ms late: MediaRecorder's `onstart` comes 60-80 ms
after the recording begins, and the key bus's limiter delays the keys by
its 6 ms look-ahead (keyrec.js `finish`). Onsets are read from
the WAV the server kept — nobody has listened.
"""

from __future__ import annotations

import io
import math
import wave

import numpy as np

from tests.browser.test_key_takes import _bench, _take

QUANTUM = 128  # samples: one render quantum


def _wav(raw: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(raw)) as w:
        rate, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(n), dtype="<i2").reshape(-1, ch)
    return pcm.astype(np.float64) / 32768.0, rate


def _db(x: np.ndarray) -> float:
    return 20 * math.log10(float(np.sqrt(np.mean(x**2))) + 1e-12)


def _record(page, play) -> object:
    """⏺ Rec two bars with nothing playing (count-in off), `play(page)` the
    notes; the take's answer."""
    page.click("#key-rec")
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    with page.expect_response("**/take/keys", timeout=30000) as answer:
        play(page)
    return answer.value


def _note(page, k: int, midi, hold_ms: int, scheduled: bool = False) -> None:
    """A note (or a chord: `midi` a list) held `hold_ms`: struck now, as a
    hand plays it, or `scheduled` 50 ms ahead on the clock, as the keys arp
    posts one."""
    at = "window.synthlab.getCtx().currentTime + 0.05" if scheduled else "undefined"
    for m in [midi] if isinstance(midi, int) else midi:
        page.evaluate(f"() => window.synthlab.piano.noteOn('n{k}-{m}', {m}, 0.8, {at})")
    page.wait_for_timeout(hold_ms)
    for m in [midi] if isinstance(midi, int) else midi:
        page.evaluate(f"() => window.synthlab.piano.noteOff('n{k}-{m}')")


def _onset(env: np.ndarray, near: int, rate: int, frac: float) -> int:
    lo = max(0, near - int(0.15 * rate))
    seg = env[lo : lo + int(0.3 * rate)]
    return lo + int(np.argmax(seg > frac))


def test_a_takes_audio_sits_on_its_notes(studio, server):
    page = _bench(studio, bars="2")
    studio.start_voice()
    # where one note's sound crosses a tenth of its peak: the engine's own
    # render of the key, which the live voice matches (the parity corpus)
    note, _rate = _wav(page.request.get(f"{studio.url}/note/C4.wav").body())
    env = np.abs(note).max(axis=1)
    attack = int(np.argmax(env > 0.1 * env.max()))

    def play(page):
        for k in range(4):
            page.wait_for_timeout(450)
            _note(page, k, 60, 150, scheduled=k % 2 == 0)

    _record(page, play)
    meta, pcm, rate = _take(server)
    beat = 60 / meta["bpm"]
    assert len(meta["notes"]) == 4
    env = np.abs(pcm).max(axis=1)
    off = []
    for n in meta["notes"]:
        stamp = round(n[0] * beat * rate)
        off.append(_onset(env, stamp, rate, 0.1 * env.max()) - attack - stamp)
    # each note sounds where its stamp says, within one render quantum
    # (measured: scheduled 13-24 samples, pressed 50-61; it was ~3,300)
    assert all(abs(o) <= QUANTUM for o in off), off
    assert studio.unexplained() == []
