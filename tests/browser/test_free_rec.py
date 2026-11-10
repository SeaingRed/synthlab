"""Free record in Chromium: ⏺ free on the Loops page records
every key from the first note to ■ — no count-in, no bars — and the shaper
makes a loop of it that keeps its timing on the note grid.

The journey: a phrase played on a stand-in MIDI port at irregular timing,
of uneven length; ■; the shaper opens on it; the end handle dragged in
(the last note trimmed off); 2 bars, fit; quantise 50 %; ▶ hear (the
master carries the shaped loop, looped); Make loop into the selected empty
slot; the loop is in the Loops panel with ✂ Shape; Edit puts it on the
grid with notes drawn off their step lines; its render holds the timing
(the onsets of the audition's WAV are where the placement says, not on the
steps); the MIDI file writes it; Ctrl+Z takes the grid back. Also: the
dock's length "free" records free from ⏺ Rec itself, and a free take with
nothing played takes nothing. Levels and onsets are read from the page's
analyser and the server's WAVs — nobody has listened: the feel of the
shaped loop, its sound and the latency are unchecked.
"""

from __future__ import annotations

import io
import time
import wave
from urllib.parse import unquote

import numpy as np

from synthlab import midifile as mf
from synthlab.studio import arrange, keytakes, loops
from synthlab.synth import Patch

_FAKE_MIDI = """
window.__midiIn = {
  id: "fake-1", name: "Fake Keys", type: "input", state: "connected",
  onmidimessage: null,
};
navigator.requestMIDIAccess = async () => ({
  inputs: new Map([[window.__midiIn.id, window.__midiIn]]), onstatechange: null,
});
window.__midi = (bytes) => window.__midiIn.onmidimessage(
  { data: new Uint8Array(bytes), target: window.__midiIn });
"""

# (gap before it in ms, midi, held ms): an uneven phrase — no tempo in it
PHRASE = [
    (0, 57, 220),
    (180, 60, 160),
    (330, 64, 260),
    (90, 62, 140),
    (410, 67, 300),
    (260, 64, 200),
]


def _slot(lane: int, row: int) -> str:
    return f'#slots .sl-cell[data-lane="{lane}"][data-row="{row}"]'


def _wait(page, check, what: str, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _loops_page(studio):
    studio.page.add_init_script(_FAKE_MIDI)
    page = studio.open("perform")
    page.evaluate(
        """() => {
          localStorage.setItem('synthlab.loops', '{"open": true, "pin": true}');
          localStorage.setItem('synthlab.keyRecBars', '0'); }"""
    )
    page.reload()
    page.wait_for_selector("#lp-free-rec")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.click("#midi-btn")
    page.wait_for_function(
        "() => typeof window.__midiIn.onmidimessage === 'function'", timeout=8000
    )
    return page


def _play(page, phrase=PHRASE) -> None:
    for gap, midi, held in phrase:
        page.wait_for_timeout(gap)
        page.evaluate(f"window.__midi([0x90, {midi}, 100])")
        page.wait_for_timeout(held)
        page.evaluate(f"window.__midi([0x80, {midi}, 0])")


def _onsets(wav: bytes, floor: float = 0.04) -> list[float]:
    """Where the WAV's notes start (seconds): a 2 ms envelope rising past a
    jump over the 15 ms before it."""
    with wave.open(io.BytesIO(wav)) as w:
        sr, ch = w.getframerate(), w.getnchannels()
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float)
    mono = np.abs(pcm.reshape(-1, ch).mean(axis=1)) / 32768
    hop = int(sr * 0.002)
    env = np.array([mono[i : i + hop].max() for i in range(0, len(mono) - hop, hop)])
    peak = env.max()
    out, last = [], -1.0
    for k in range(8, len(env)):
        before = env[k - 8 : k].max()
        if env[k] > floor * peak and env[k] > 2.5 * before + 1e-4:
            t = k * hop / sr
            if t - last > 0.05:
                out.append(t)
            last = t
    return out


def test_free_rec_a_phrase_shape_it_and_make_a_loop_that_keeps_its_timing(
    studio, server
):
    s = server.state
    with s.lock:  # a pluck: its onsets read sharp in the render
        s.patch = Patch(
            name="pluck", attack=0.001, decay=0.12, sustain=0.0, release=0.04
        )
        s.save()
    page = _loops_page(studio)
    # an empty slot selected: where Make loop lands
    page.locator(f"{_slot(1, 0)} .sl-empty").click()
    page.locator(f'{_slot(1, 0)}[aria-selected="true"]').wait_for()
    page.click("#lp-free-rec")
    page.wait_for_function(
        "() => document.getElementById('lp-free-rec')"
        ".textContent.includes('free: play')"
    )
    page.wait_for_timeout(600)  # silence before the first note is not the take's
    _play(page)
    page.wait_for_timeout(450)
    with page.expect_response("**/take/keys/free") as answer:
        page.click("#lp-free-rec")
    assert answer.value.ok, answer.value.text()
    take = answer.value.headers["x-clip-name"]
    meta = keytakes.read_meta(server.data_dir, take)
    notes = keytakes.read_notes(server.data_dir, take)["notes"]
    assert meta["free"] is True and [n[2] for n in notes] == [m for _g, m, _h in PHRASE]
    assert notes[0][0] == 0  # from its first note
    secs = meta["bars"] * 4 * 60 / meta["bpm"]
    assert 2.6 < secs < 4.2, secs  # the phrase and the hand's ■, no bar rounding
    # the shaper opens on it
    page.wait_for_selector("#free-shaper:popover-open .shp-roll svg")
    assert page.locator("#free-shaper .shp-note").count() == len(PHRASE)
    # trim the end: the end handle dragged to just before the last note
    last = page.locator('#free-shaper .shp-note[data-i="5"]').bounding_box()
    handle = page.locator('#free-shaper .shp-handle[data-h="b"]').bounding_box()
    page.mouse.move(handle["x"] + 3, handle["y"] + 40)
    page.mouse.down()
    page.mouse.move(last["x"] - 4, handle["y"] + 40, steps=6)
    page.mouse.up()
    assert page.locator("#free-shaper .shp-note.out").count() == 1
    page.click('#free-shaper [data-shp-bars="2"]')
    page.click('#free-shaper [data-shp-mode="fit"]')
    page.locator('#free-shaper [data-shp="q"]').fill("50")
    assert page.locator('#free-shaper [data-shp-out="q"]').text_content() == "50%"
    assert (
        "5 notes in 2 bars"
        in page.locator('#free-shaper [data-shp-out="notes"]').text_content()
    )
    # ▶ hear: the shaped loop, looped, on the master
    with page.expect_response("**/take/keys/free/hear") as heard:
        page.click("#free-shaper [data-shp-hear]")
    assert heard.value.ok
    preview = heard.value.headers["x-preview-url"]
    page.wait_for_function(
        "() => document.querySelector('#free-shaper [data-shp-hear]')"
        ".getAttribute('aria-pressed') === 'true'"
    )
    assert studio.level() > 1e-3
    page.click("#free-shaper [data-shp-hear]")  # again: stop
    # Make loop: into the selected slot
    with page.expect_response("**/take/keys/free/loop") as made:
        page.click("#free-shaper [data-shp-make]")
    assert made.value.ok, made.value.text()
    name = unquote(made.value.headers["x-loop-name"])
    page.wait_for_selector("#free-shaper:not(:popover-open)", state="attached")
    _wait(page, lambda: s.slot_at(1, 0) == name, "the slot to hold the loop")
    loop = loops.load(server.data_dir, name)
    assert loop["free"]["take"] == take and len(loop["played"]) == 5
    lane = loop["lane"]
    assert lane["bars"] == 2 and lane["note_nudge"], lane  # off its steps
    row = page.locator(f'#loops-panel .lp-row[data-ctx="loop {name}"]')
    row.wait_for()
    assert row.locator(f'[data-free-take="{take}"]').count() == 1
    # the timing is in the render: the audition's onsets are where the
    # placement puts the notes (bpm, steps a bar, nudges), not on the steps
    wav = page.request.get(f"{server.url}{preview}").body()
    step = 4 / lane["steps_per_bar"] * 60 / loop["key"]["bpm"]
    starts = sorted(
        (int(k) + lane["note_nudge"].get(str(k), 0.0)) * step
        for k in {c[1] for c in lane["cells"]}
        if [p for p, st in lane["cells"] if st == k]
        and all([p, k - 1] not in lane["cells"] for p, st in lane["cells"] if st == k)
    )
    heard_at = _onsets(wav)
    off_step = [t for t in starts if abs(t / step - round(t / step)) > 0.05]
    assert off_step, starts
    for t in off_step:
        near = min(heard_at, key=lambda x: abs(x - t))
        assert abs(near - t) < 0.012, (t, heard_at)
        assert abs(near - round(t / step) * step) > 0.004  # not on the step
    # Edit: on the grid, notes drawn off their step lines
    row.locator(".lp-edit").click()
    page.wait_for_selector(".grid.roll .cell.nudged")
    studio.settle()
    assert page.locator(".grid.roll .cell.nudged").count() == len(
        [k for k, v in lane["note_nudge"].items() if v]
    )
    assert s.arrangement.note_nudge == lane["note_nudge"]
    # the MIDI file writes the timing
    mid = mf.read(page.request.get(f"{server.url}/export/loop.mid").body())
    (sched,) = arrange.note_schedule(s.arrangement)[:1]
    got = sorted(round(n.start, 2) for n in mid.tracks[0].notes)
    assert got == sorted(round(e[0], 2) for e in sched)
    # Ctrl+Z past Edit's opening: the grid (lane 1's) as it was, no timing
    # on it, the edit ended — the loop keeps its own
    page.locator(".view-head h1").click()
    page.keyboard.press("Control+z")
    _wait(page, lambda: not s.arrangement.note_nudge, "Ctrl+Z to take the grid back")
    assert s.loop_edit_of() is None
    page.wait_for_function("() => !document.querySelector('.grid.roll .cell.nudged')")
    assert loops.load(server.data_dir, name)["lane"]["note_nudge"] == lane["note_nudge"]
    assert studio.unexplained() == []


def test_the_docks_free_length_records_free_and_nothing_played_takes_nothing(
    studio, server
):
    page = _loops_page(studio)
    page.select_option("#key-bars", "free")
    page.click("#key-rec")  # nothing played: ■ takes nothing
    page.wait_for_timeout(300)
    page.click("#key-rec")
    page.wait_for_timeout(300)
    assert not list((server.data_dir / "clips").glob("*.wav"))
    assert page.locator("#free-shaper:popover-open").count() == 0
    page.click("#key-rec")
    _play(page, PHRASE[:3])
    with page.expect_response("**/take/keys/free") as answer:
        page.keyboard.press("Alt+R")
    assert answer.value.ok
    page.wait_for_selector("#free-shaper:popover-open .shp-roll svg")
    assert page.locator("#free-shaper .shp-note").count() == 3
    page.keyboard.press("Escape")
    page.wait_for_selector("#free-shaper:not(:popover-open)", state="attached")
    # the take waits in the Loops panel: ✂ Shape opens it again
    take = answer.value.headers["x-clip-name"]
    page.locator(f'#loops-panel [data-free-take="{take}"]').click()
    page.wait_for_selector("#free-shaper:popover-open .shp-roll svg")
    assert studio.unexplained() == []
