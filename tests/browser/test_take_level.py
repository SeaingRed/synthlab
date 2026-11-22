"""A take's loop plays where the take was, in Chromium: at the keys'
level, and its audio on its notes' stamps.

What these journeys prove: a take recorded with nothing playing is cut so
its notes sound where their stamps say (a note scheduled on the clock, as
the keys arp posts one, and a press as a hand plays one) within one render
quantum — it was ~70 ms late: MediaRecorder's `onstart` comes 60-80 ms
after the recording begins, and the key bus's limiter delays the keys by
its 6 ms look-ahead (keyrec.js `finish`); and the take's loop, opened with
Edit and played by Perform's transport, reaches the master at the level
its take does — single notes and a held three-note chord — where it was
~9 dB louder (a loop render is normalised; `keylevel.py`). Levels are the
master analyser's and the WAVs' — nobody has listened.
"""

from __future__ import annotations

import io
import math
import wave
from urllib.parse import unquote

import numpy as np

from synthlab.studio import loops
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


READY = """() => { window.__ready = [];
  document.addEventListener("preview-ready",
    (e) => window.__ready.push(e.detail || {}), true); }"""


def _master_db(page, seconds: float) -> float:
    """The master's level over `seconds`: the analyser's windows' power,
    averaged (a read every 30 ms of its 2048-sample window)."""
    power = page.evaluate(
        """async (s) => {
          const an = window.synthlab.getAnalyser(), b = new Float32Array(an.fftSize);
          let sum = 0, n = 0; const end = performance.now() + s * 1000;
          while (performance.now() < end) { an.getFloatTimeDomainData(b);
            let p = 0; for (const v of b) p += v * v; sum += p / b.length; n++;
            await new Promise((ok) => setTimeout(ok, 30)); }
          return sum / n; }""",
        seconds,
    )
    return 10 * math.log10(power + 1e-15)


def _loop_at_keys_level(studio, server, play):
    """Record `play` as a take with nothing playing; hear the take (its row's
    ▶) and then its loop (Edit, Perform's ▶) at the master: (take dB, loop
    dB at the master; the take's WAV dB, the loop render's dB as normalised
    and as played)."""
    page = _bench(studio, bars="2")
    studio.start_voice()
    answer = _record(page, play)
    name = unquote(answer.headers["x-loop-name"])
    meta, pcm, rate = _take(server)
    secs = len(pcm) / rate
    row = page.locator(f'#key-takes [data-ctx="loop {name}"]')
    row.wait_for()
    studio.wait_silence()
    # nothing plays: its row's ▶ is the take's own audio, at once — heard
    # whole inside the window, its energy over its own length
    row.locator(".lp-play").click()
    window = secs + 0.8
    take_db = _master_db(page, window) + 10 * math.log10(window / secs)
    studio.wait_silence()
    page.evaluate(READY)
    with page.expect_response("**/loop/edit"):
        row.locator(".lp-edit").click()
    page.wait_for_selector('#view[data-view="perform"]')
    studio.settle()
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    page.wait_for_function("() => window.__ready.length > 0", timeout=20000)
    studio.wait_sound()
    page.wait_for_timeout(300)  # (the crossfade in)
    loop_db = _master_db(page, 2 * secs)  # two whole passes
    detail = page.evaluate("() => window.__ready[window.__ready.length - 1]")
    page.click("#play")
    render, _ = _wav(page.request.get(f"{studio.url}{detail['url']}").body())
    assert loops.load(server.data_dir, name)["take"] == meta["name"]
    return take_db, loop_db, _db(pcm), _db(render), detail.get("gain")


def test_a_takes_loop_plays_at_the_keys_level(studio, server):
    def play(page):
        for k, m in enumerate((60, 64, 67, 72, 67, 64)):
            page.wait_for_timeout(180)
            _note(page, k, m, 260)

    take_db, loop_db, wav_db, render_db, gain = _loop_at_keys_level(
        studio, server, play
    )
    assert gain is not None and gain < -3  # the loop is turned down
    # the render as played (its gain) is the take's level, within 1 dB —
    # the render as normalised was ~9 dB over it
    assert render_db - wav_db > 4
    assert abs(render_db + gain - wav_db) < 1.0, (render_db, gain, wav_db)
    # and so is what reaches the master (the analyser after it)
    assert abs(loop_db - take_db) < 1.0, (loop_db, take_db)
    assert studio.unexplained() == []


def test_a_chords_loop_plays_at_the_keys_level(studio, server):
    def play(page):
        for k, chord in enumerate(([60, 64, 67], [57, 60, 64])):
            page.wait_for_timeout(300)
            _note(page, k, chord, 1200)

    take_db, loop_db, wav_db, render_db, gain = _loop_at_keys_level(
        studio, server, play
    )
    assert gain is not None
    assert abs(render_db + gain - wav_db) < 1.0, (render_db, gain, wav_db)
    assert abs(loop_db - take_db) < 1.0, (loop_db, take_db)
    assert studio.unexplained() == []
