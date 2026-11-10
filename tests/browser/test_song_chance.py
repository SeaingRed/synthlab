"""A free-played loop with timing inside its steps and a chance lane, into
the Song as a pattern, in Chromium:

- → Song as a pattern from the Loops panel keeps `note_nudge` and
  `note_chance` on the pattern, and the section's render starts its notes
  where the loop's render does (onsets from the audio, ±12 ms);
- chance varies every pass: a section of four repeats is four WAVs
  (`pass_urls`) whose onsets differ, and the same plan rendered again
  (caches emptied) is the same bytes; the working loop plays four passes
  as its cycle (`?passes=4`), its playheads on one (`loopDuration`).

The audio is fetched through the page and its onsets found by an
envelope — nobody has listened.
"""

from __future__ import annotations

import json
import time

import numpy as np

from synthlab.audio import read_pcm16
from synthlab.studio import loops
from synthlab.studio.state.model import Arrangement
from synthlab.synth import Patch

BPM = 120.0
# played free (beats): off the sixteenth lines by a few tens of ms
PLAYED = [
    [0.0, 0.2, 57, 0.9],
    [0.56, 0.2, 60, 0.8],
    [1.03, 0.2, 64, 0.9],
    [1.45, 0.2, 62, 0.8],
    [2.07, 0.2, 57, 0.9],
    [2.52, 0.2, 60, 0.8],
    [3.04, 0.2, 67, 0.9],
    [3.47, 0.2, 64, 0.8],
]
HALF = 0.5


def _wait(page, check, what: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _seed(server) -> str:
    """A loop placed from free playing (`loops.place_free` at strength 0:
    each note's timing kept as its step's nudge), every note at 50 %."""
    lane = loops.place_free(PLAYED, "4/4", 1, 16, 0)
    assert lane["note_nudge"], lane
    starts = {str(c[1]) for c in lane["cells"]}
    arr = Arrangement(
        bpm=BPM,
        bars=1,
        steps_per_bar=16,
        cells=lane["cells"],
        note_nudge=lane["note_nudge"],
        note_chance=dict.fromkeys(sorted(starts), HALF),
    )
    pay = {
        "format": 2,
        "patch": Patch(name="Short pluck", release=0.04, attack=0.001).to_json(),
        "layers": [],
        "main_fx": [],
        "curves": {},
    }
    return loops.save(server.data_dir, "free riff", arr, pay, played=PLAYED)


def _audio(page, url: str) -> np.ndarray:
    r = page.request.get(page.url.split("/view/")[0] + url)
    assert r.ok, (url, r.status)
    audio, _sr = read_pcm16(r.body())
    return audio


def _onsets(audio: np.ndarray, sr: int = 44100) -> list[float]:
    """Note starts (s): the envelope (8 ms) rising past a fifth of its peak
    after 30 ms under a twentieth — the foot of each attack. The same
    reading for the loop and the section, so a bias is shared."""
    env = np.abs(audio.mean(axis=1))
    k = int(0.008 * sr)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    hi, lo = 0.2 * env.max(), 0.05 * env.max()
    out, quiet = [], int(0.03 * sr)
    below, armed = quiet, True
    for i, v in enumerate(env):
        if v < lo:
            below += 1
            armed = armed or below >= quiet
            continue
        below = 0
        if v > hi and armed:
            j = i
            while j > 0 and env[j - 1] > lo:  # back to where it left the floor
                j -= 1
            out.append(j / sr)
            armed = False
    return out


def _close(a: list[float], b: list[float], tol: float = 0.012) -> bool:
    return len(a) == len(b) and all(
        abs(x - y) <= tol for x, y in zip(a, b, strict=True)
    )


def test_a_free_loop_with_chance_plays_its_feel_in_the_song_and_varies_by_pass(
    studio, server
):
    name = _seed(server)
    s = server.state
    with s.lock:  # the notes alone on the Loops page (the default beat off)
        s.perform_parts = ["synth"]
        s.save()
    page = studio.open("perform")
    page.evaluate(
        "() => localStorage.setItem('synthlab.loops',"
        " JSON.stringify({open: true, pin: true}))"
    )
    page.reload()
    page.wait_for_selector("#loops-panel")
    studio.settle()
    row = page.locator(f'#loops-panel .lp-row[data-title="{name}"]').first
    row.locator("summary", has_text="→ Song").click()
    with page.expect_response("**/loop/song"):
        row.locator('button[aria-label$="into the song as a pattern"]').click()
    _wait(page, lambda: len(s.song["sections"]) == 1, "the section")
    (pat,) = [p for p in s.song["patterns"].values() if p["kind"] == "notes"]
    loop = loops.load(server.data_dir, name)["lane"]
    assert pat["note_nudge"] == loop["note_nudge"]
    assert pat["note_chance"] == loop["note_chance"]
    # the loop on the grid, ▶: four passes as its cycle, the playheads on one
    with page.expect_response("**/loop/edit"):
        row.locator(".lp-edit").click()
    page.click("#play")
    studio.wait_sound()
    page.wait_for_function(
        "() => (window.synthlab.lastBufferUrl() || '').includes('passes=4')"
    )
    url = page.evaluate("() => window.synthlab.lastBufferUrl()")
    one = page.evaluate("() => window.synthlab.loopDuration()")
    assert abs(one - 2.0) < 1e-3, one  # one bar at 120 bpm
    looped = _audio(page, url)
    assert abs(len(looped) / 44100 - 8.0) < 0.01
    page.click("#play")
    quarter = len(looped) // 4
    loop_passes = [_onsets(looped[r * quarter : (r + 1) * quarter]) for r in range(4)]
    assert len({tuple(round(x, 2) for x in p) for p in loop_passes}) > 1, loop_passes
    # the song: four repeats of the section, each pass its own WAV
    with s.lock:
        s.song["sections"][0]["repeats"] = 4
        s.save()
    plan = json.loads(
        page.request.get(page.url.split("/view/")[0] + "/song/plan.json").text()
    )
    sec = plan["sections"][0]
    urls = sec["pass_urls"]
    assert len(urls) == 4 and len(set(urls)) == 4
    song_passes = [_onsets(_audio(page, u)) for u in urls]
    # the first pass is the loop's first, onset for onset (the feel kept)
    assert _close(song_passes[0], loop_passes[0]), (song_passes[0], loop_passes[0])
    assert len({tuple(round(x, 2) for x in p) for p in song_passes}) > 1, song_passes
    # rendered again from nothing: the same keys, the same bytes
    first = page.request.get(page.url.split("/view/")[0] + urls[1]).body()
    with s.lock:
        server.app.state.svc.reset_caches()
    plan2 = json.loads(
        page.request.get(page.url.split("/view/")[0] + "/song/plan.json").text()
    )
    assert plan2["sections"][0]["pass_urls"] == urls
    again = page.request.get(page.url.split("/view/")[0] + urls[1]).body()
    assert again == first
    assert studio.unexplained() == []
