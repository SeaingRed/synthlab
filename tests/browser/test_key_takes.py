"""Keys takes in Chromium: ⏺ Rec in the keyboard dock records
the key bus — not the beat — on the loop's bar lines, with the notes; one
Rec is a take that is both its audio and an editable loop,
in the one Loops list.

What these journeys prove: with the loop playing a take starts on its bar
lines and is the whole bars chosen (its WAV's length is bars x 240 / bpm);
a take with no key pressed over a sounding loop is near silence — the beat
is NOT in it — while "+ beat" puts it in; the notes played are kept with
it, as played, in its loop; the loop is in the dock's strip and the clip
library's Loops with its audio and its notes; Edit opens it on Perform's
grid at the right steps and an edit there saves to it; → Song puts it in
the pool as a pattern on a track with its sound, and the song plays it; ✕
deletes it and its audio; "loop the lane" saves the grid as a loop it then
edits. Levels are read from the WAV the server kept and the page's
analyser — nobody has listened: the timing feel, the latency correction on
a real interface and the sound are unchecked.
"""

from __future__ import annotations

import io
import json
import time
import wave
from urllib.parse import unquote

import numpy as np

from synthlab.studio import loops
from tests.browser.conftest import confirm_it, name_it
from tests.browser.inputs import clear_of_dock


def _wait(page, check, what: str, timeout: float = 8.0):
    """Poll server-side state the page's request is about to change."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {what}")


def _bench(studio, bars: str = "1", count: bool = False, beat: bool = False):
    page = studio.open("editor")
    page.evaluate(
        """([b, c, beat]) => { localStorage.setItem('synthlab.keyRecBars', b);
          localStorage.setItem('synthlab.keyRecCount', c);
          localStorage.setItem('synthlab.keyRecBeat', beat);
          localStorage.setItem('synthlab.loops', '{"open": true, "pin": true}'); }""",
        [bars, "1" if count else "0", "1" if beat else "0"],
    )
    page.reload()
    page.wait_for_selector("#key-rec")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    return page


def _loop_playing(page) -> float:
    """▶ the phrase; its tempo once it sounds."""
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    return float(page.input_value("#t-bpm"))


def _take(server, timeout: float = 20.0):
    """The newest keys take the store holds: (meta with its notes — the
    sidecar's, `keytakes.read_notes` — samples, rate)."""
    import time

    from synthlab.studio import keytakes

    clips = server.data_dir / "clips"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = sorted(clips.glob("keys-*.wav")) if clips.exists() else []
        if found:
            path = found[-1]
            meta = json.loads(path.with_suffix(".meta.json").read_text("utf-8"))
            assert "notes" not in meta  # they are the sidecar's
            meta["notes"] = keytakes.read_notes(server.data_dir, path.stem)["notes"]
            with wave.open(io.BytesIO(path.read_bytes())) as w:
                rate, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
                pcm = np.frombuffer(w.readframes(n), dtype="<i2").reshape(-1, ch)
            return meta, pcm.astype(np.float64) / 32768.0, rate
        time.sleep(0.2)
    raise AssertionError("no keys take reached the clip store")


def test_a_take_on_the_loop_is_whole_bars_of_the_keys_with_their_notes(studio, server):
    page = _bench(studio, bars="1")
    bpm = _loop_playing(page)
    page.click("#key-rec")
    page.wait_for_selector('#key-rec[aria-pressed="true"]')
    # armed: it waits for the loop's next bar line, then records
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    page.keyboard.down("KeyA")  # C4 while it records
    page.wait_for_timeout(500)
    page.keyboard.up("KeyA")
    with page.expect_response("**/take/keys", timeout=20000) as answer:
        pass  # one bar: it stops by itself
    assert answer.value.ok
    page.wait_for_selector('#key-rec[aria-pressed="false"]')
    meta, pcm, rate = _take(server)
    assert meta["source"] == "keys" and meta["kept"] is True and meta["bars"] == 1
    assert abs(meta["bpm"] - bpm) < 0.01
    assert abs(len(pcm) / rate - 240 / bpm) < 0.02  # exactly one bar
    pitches = [n[2] for n in meta["notes"]]
    assert 60 in pitches, meta["notes"]
    (c4,) = [n for n in meta["notes"] if n[2] == 60]
    assert 0 <= c4[0] < 4 and 0 < c4[1] <= 4 - c4[0]  # inside the bar, in beats
    assert np.sqrt(np.mean(pcm**2)) > 1e-3  # the key is in it
    assert isinstance(meta["sound"], str)  # and the sound that played it
    # the strip lists it: its loop, made with it — named from what it is
    # (the clock names the take)
    loop = loops.of_take(server.data_dir, meta["name"])
    assert (
        loop and loop != meta["name"] and loop.endswith((" riff", " chords"))
    )  # plainly
    page.wait_for_selector(f'#key-takes:not([hidden]) [data-ctx="loop {loop}"]')
    assert studio.unexplained() == []


def test_the_beat_is_not_in_a_take_unless_asked(studio, server):
    page = _bench(studio, bars="1")
    _loop_playing(page)
    studio.wait_sound()  # the loop sounds on the master
    page.click("#key-rec")  # no key pressed: the take hears nothing
    with page.expect_response("**/take/keys", timeout=20000):
        pass
    meta, pcm, _rate = _take(server)
    assert np.sqrt(np.mean(pcm**2)) < 1e-3, "the beat leaked into a keys take"
    assert meta["notes"] == []
    (server.data_dir / "clips" / f"{meta['name']}.wav").unlink()
    # "+ beat": the whole mix, as heard
    page.click("#key-beat")
    page.wait_for_selector('#key-beat[aria-pressed="true"]')
    page.click("#key-rec")
    with page.expect_response("**/take/keys", timeout=20000):
        pass
    meta, pcm, _rate = _take(server)
    assert meta.get("beat") is True
    assert np.sqrt(np.mean(pcm**2)) > 1e-3
    assert studio.unexplained() == []


def _record(page, keys=("KeyD",), gap: int = 600):
    """⏺ Rec over the playing loop, a key at a time once it records: the
    answer to its post (the take and its loop)."""
    page.click("#key-rec")
    page.wait_for_function(
        "() => document.getElementById('key-rec').textContent.startsWith('■')",
        timeout=10000,
    )
    for key in keys:
        page.keyboard.down(key)
        page.wait_for_timeout(250)
        page.keyboard.up(key)
        page.wait_for_timeout(gap)
    with page.expect_response("**/take/keys", timeout=25000) as answer:
        pass  # it stops by itself at its bars
    assert answer.value.ok, answer.value.text()[:400]
    page.wait_for_selector('#key-rec[aria-pressed="false"]')
    return answer.value


def test_one_rec_is_a_loop_to_edit_on_perform_and_put_in_the_song(studio, server):
    """The whole journey: record 2 bars of keys over the loop -> the take
    is in Loops with its audio and its notes -> Edit opens it on Perform's
    grid with the notes at their steps (and the notes as played kept in the
    loop) -> an edit there saves to it -> → Song puts it in the pool as a
    pattern on a track with its sound -> the song plays it."""
    page = _bench(studio, bars="2")
    _loop_playing(page)
    answer = _record(page, keys=("KeyD", "KeyG"))
    name = unquote(answer.headers["x-loop-name"])
    page.click("#play")  # the loop stops: what is heard next is the song
    meta, _pcm, _rate = _take(server)
    loop = loops.load(server.data_dir, name)
    assert loop["take"] == meta["name"]
    assert loop["played"] == meta["notes"]  # exactly as played, kept
    assert {n[2] for n in loop["played"]} == {64, 67}  # E4, G4
    assert (loop["lane"]["bars"], loop["lane"]["steps_per_bar"]) == (2, 16)
    # in Loops: the dock's strip, its audio and its notes
    row = page.locator(f'#key-takes [data-ctx="loop {name}"]')
    row.wait_for()
    assert row.locator(".kt-play").count() == 1
    assert row.locator(".lp-roll path").get_attribute("d")
    # and the clip library's Loops, every page's ("All loops")
    page.locator("#key-takes .lp-all").click()
    page.wait_for_selector(f'#clip-drawer .cd-loops [data-ctx="loop {name}"]')
    page.keyboard.press("Escape")
    page.wait_for_selector("#clip-drawer:not(:popover-open)", state="attached")
    # Edit: Perform's grid, the notes at their steps, as this loop
    with page.expect_response("**/loop/edit"):
        row.locator(".lp-edit").click()
    page.wait_for_selector('#view[data-view="perform"]')
    page.wait_for_selector("#transport .lp-edit-note")
    # the address bar follows (a reload stays on Perform)
    page.wait_for_url("**/view/perform")
    studio.settle()
    (e4,) = [n for n in loop["played"] if n[2] == 64]
    step = round(e4[0] * 4)
    cell = f'#transport input.cell[name="cell.E4.{step}"]'
    assert page.locator(cell).is_checked()
    # an edit on the grid saves to the loop; the notes as played stay
    clear_of_dock(page, cell)
    page.locator(cell).click()
    _wait(
        page,
        lambda: loops.load(server.data_dir, name)["edited"],
        "the edit saved into the loop",
    )
    edited = loops.load(server.data_dir, name)
    assert ["E4", step] not in edited["lane"]["cells"]
    assert edited["played"] == loop["played"]
    # → Song as a pattern, from Perform's Loops panel
    studio.settle()
    bin_row = page.locator(f'#loops-panel [data-ctx="loop {name}"]')
    bin_row.locator(".lp-pick > summary").first.click()
    with page.expect_response("**/loop/song"):
        bin_row.locator('.lp-pick button:has-text("pattern")').click()
    song = server.state.song
    sec = song["sections"][-1]
    (tid, pid), *_ = sec["cells"].items()
    track = next(t for t in song["tracks"] if t["id"] == tid)
    assert track["patch"]["name"] == loop["sound"][0].name
    pattern = song["patterns"][pid]
    assert sorted(map(list, pattern["cells"])) == sorted(edited["lane"]["cells"])
    # the song plays it
    studio.nav("song")
    studio.settle()
    page.click('[data-song-action="play"]')
    studio.wait_sound(timeout=20.0)
    assert studio.unexplained() == []


def test_a_rec_on_perform_lands_in_its_bin_and_the_rail_counts_loops(studio, server):
    """⏺ Rec on Perform lands the take in Perform's Loops bin, not only the
    dock's strip, without a reload: keyrec.js's fetch() sends
    HX-Current-URL, or the server reads the post as Synths' and never
    re-sends the Perform-only bin. And the rail's count is the loops', not
    the clip store's ("Loops 6" with 2 loops)."""
    page = _bench(studio, bars="1")
    studio.nav("perform")
    studio.settle()
    assert page.locator("#loops-panel .lp-row[data-kind=loop]").count() == 0
    rail = "#clip-library .loop-count"
    assert page.locator(rail).text_content() == "0"
    page.click("#play")  # Perform's ▶: the loop plays
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)
    first = unquote(_record(page).headers["x-loop-name"])
    page.wait_for_selector(f'#loops-panel [data-ctx="loop {first}"]')  # no reload
    second = unquote(_record(page).headers["x-loop-name"])
    page.wait_for_selector(f'#loops-panel [data-ctx="loop {second}"]')
    assert page.locator("#loops-panel .lp-row[data-kind=loop]").count() == 2
    assert page.locator("#key-takes .lp-row").count() == 2
    assert page.locator(rail).text_content() == "2"
    # the clips beside it leave a loop's audio out (it is one of the Loops)
    assert page.locator("#clip-library .clip-count").text_content() == "0"
    page.click("#play")
    assert studio.unexplained() == []


def test_from_the_strip_delete_and_the_bin_loops_the_lane_it_then_edits(studio, server):
    page = _bench(studio, bars="1")
    _loop_playing(page)
    first = unquote(_record(page).headers["x-loop-name"])
    took = _record(page).headers
    second, clip = unquote(took["x-loop-name"]), took["x-clip-name"]
    # named from what it is (the clock named its audio): two alike, apart
    assert (
        first.endswith((" riff", " chords"))
        and second == f"{first} 2"
        and clip.startswith("keys-")
    )
    rows = page.locator("#key-takes .lp-row")
    rows.nth(1).wait_for()
    assert rows.nth(0).get_attribute("data-ctx") == f"loop {second}"  # newest first
    # ✕ deletes the loop and its audio
    with page.expect_response("**/loop/delete"):
        page.locator(f'#key-takes [data-ctx="loop {second}"] .kt-drop').click()
        confirm_it(page)
    page.wait_for_selector(f'#key-takes [data-ctx="loop {second}"]', state="detached")
    assert not loops.exists(server.data_dir, second)
    assert not (server.data_dir / "clips" / f"{clip}.wav").exists()
    assert loops.exists(server.data_dir, first)
    page.click("#play")
    # Perform's Loops panel: "loop the lane" names a loop of the grid, which the
    # grid then edits
    studio.nav("perform")
    studio.settle()
    bars = server.state.arrangement.bars  # the grid's own: the loop is the grid
    page.locator(f'#loops-panel .kt-lane .barchip:text-is("{bars}")').click()
    with page.expect_response("**/loop/save"):
        name_it(page, "hook")
    page.wait_for_selector('#loops-panel [data-ctx="loop hook"]')
    assert server.state.loop_edit_of()["name"] == "hook"
    page.wait_for_selector("#transport .lp-edit-note")
    assert studio.unexplained() == []


# --- ⏺ Rec into the launcher's selected slot, ⟲ Capture ------

SWITCHED = "() => !document.querySelector('#slots .sl-cell[data-q]')"


def _slot(lane: int, row: int) -> str:
    return f'#slots .sl-cell[data-lane="{lane}"][data-row="{row}"]'


def _launcher(studio, server):
    """The Loops page, a 1-bar loop of notes ("Acid line") in lane 1's first
    slot; takes of 1 bar, no count-in."""
    from synthlab.studio.state.model import Arrangement
    from synthlab.synth import Patch

    s = server.state
    arr = Arrangement(bpm=124.0, bars=1, steps_per_bar=16, cells=[["A2", 0], ["E3", 8]])
    pay = {"format": 2, "patch": Patch(name="Acid bass").to_json(), "layers": []}
    loops.save(server.data_dir, "Acid line", arr, pay | {"main_fx": [], "curves": {}})
    with s.lock:
        s.arrangement = Arrangement(bpm=124.0, bars=1, steps_per_bar=16)
        s.set_slot(0, 0, "Acid line")
        s.save()
    page = studio.open("perform")
    page.evaluate(
        """() => { localStorage.setItem('synthlab.keyRecBars', '1');
          localStorage.setItem('synthlab.keyRecCount', '0');
          localStorage.setItem('synthlab.keyRecHow', 'overdub'); }"""
    )
    page.reload()
    page.wait_for_selector("#slots")
    studio.settle()
    return page


def _played(page) -> None:
    page.click("#play")
    page.wait_for_function("() => window.synthlab.isPlaying()", timeout=20000)


def test_rec_into_a_slot_overdub_replace_and_new_while_the_loop_plays(studio, server):
    """⏺ Rec lands in the selected slot: overdubbing the loop it holds (its
    notes merged, saved to it), replacing it (the take's loop plays from
    the slot, queued for the bar), or — an empty slot — a new loop there."""
    page = _launcher(studio, server)
    s = server.state
    assert page.locator("#key-how").is_hidden()  # no slot selected: a free take
    _played(page)
    page.locator(f"{_slot(0, 0)} .sl-go").click()  # selected and launched
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(
        f'{_slot(0, 0)}[data-state="playing"][aria-selected="true"]'
    ).wait_for()
    overdub = page.locator('#key-how [data-key-how="overdub"]')
    assert overdub.get_attribute("aria-pressed") == "true"
    assert page.locator('#key-how [data-key-how="new"]').is_hidden()
    # overdub: C4 played into the playing slot's loop, which saves it
    page.locator(".view-head h1").click()
    take = unquote(_record(page, keys=("KeyA",)).headers["x-loop-name"])

    def held() -> list:
        return loops.load(server.data_dir, "Acid line")["lane"]["cells"]

    _wait(page, lambda: any(c[0] == "C4" for c in held()), "the overdub saved")
    assert ["A2", 0] in held() and ["E3", 8] in held()  # what it held stays
    assert s.slot_at(0, 0) == "Acid line" and loops.exists(server.data_dir, take)
    assert any(c[0] == "C4" for c in s.arrangement.cells)  # the lane plays it
    # replace: the take's own loop takes the slot and plays from it
    page.click('#key-how [data-key-how="replace"]')
    how = page.evaluate("() => localStorage.getItem('synthlab.keyRecHow')")
    assert how == "replace"
    page.locator(".view-head h1").click()
    took = unquote(_record(page, keys=("KeyD",)).headers["x-loop-name"])
    _wait(page, lambda: s.slot_at(0, 0) == took, "the take in the slot")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(0, 0)}[data-state="playing"]').wait_for()
    assert s.loop_edit_of(0)["name"] == took
    assert {c[0] for c in s.arrangement.cells} == {"E4"}  # only what was played
    assert loops.exists(server.data_dir, "Acid line")  # the old loop stays
    # new: an empty slot of lane 2, selected; the take is a new loop there
    page.locator(f"{_slot(1, 0)} .sl-empty").click()
    page.locator(f'{_slot(1, 0)}[aria-selected="true"]').wait_for()
    assert page.locator(f'{_slot(0, 0)}[aria-selected="true"]').count() == 0
    new_how = page.locator('#key-how [data-key-how="new"]')
    assert new_how.get_attribute("aria-pressed") == "true"
    page.locator(".view-head h1").click()
    new = unquote(_record(page, keys=("KeyG",)).headers["x-loop-name"])
    _wait(page, lambda: s.slot_at(1, 0) == new, "the take in the empty slot")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(1, 0)}[data-state="playing"]').wait_for()
    assert {c[0] for c in s.layers[1]["cells"]} == {"G4"}
    studio.wait_sound()
    # the selection is the tab's: a reload keeps it
    page.click("#play")
    page.reload()
    page.locator(f'{_slot(1, 0)}[aria-selected="true"]').wait_for()
    assert studio.unexplained() == []


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


def _midi_note(page, midi: int, ms: int = 250) -> None:
    page.evaluate(f"window.__midi([0x90, {midi}, 100])")
    page.wait_for_timeout(ms)
    page.evaluate(f"window.__midi([0x80, {midi}, 0])")


def test_capture_keeps_what_was_played_without_rec_across_a_page_change(studio, server):
    """⟲ Capture: notes played on a MIDI port over the loop, never recorded,
    kept as a loop of the bar they fall in (Alt+T) — from Synths after the
    page changed (the buffer is the dock's); then into a selected empty slot
    on the Loops page, where it plays from."""
    studio.page.add_init_script(_FAKE_MIDI)
    page = _launcher(studio, server)
    s = server.state
    page.locator(".view-head h1").click()
    page.click("#midi-btn")
    page.wait_for_function(
        "() => typeof window.__midiIn.onmidimessage === 'function'", timeout=8000
    )
    _played(page)
    bar = 240 / 124
    # three notes inside one bar of the loop (from just after a bar line)
    page.wait_for_function(
        """(bar) => { const S = window.synthlab, t = S.heardTime();
          return t - S.nextBar(t - bar + 1e-4, 0) < 0.15; }""",
        arg=bar,
        timeout=8000,
        polling=10,
    )
    for midi in (60, 64, 67):
        _midi_note(page, midi)
        page.wait_for_timeout(100)
    studio.nav("editor")
    studio.settle()
    with page.expect_response("**/take/keys/capture") as answer:
        page.keyboard.press("Alt+T")
    assert answer.value.ok
    name = unquote(answer.value.headers["x-loop-name"])
    loop = loops.load(server.data_dir, name)
    assert loop["take"] is None  # notes only: no audio
    assert [n[2] for n in loop["played"]] == [60, 64, 67]
    assert loop["lane"]["bars"] == 1 and loop["key"]["bpm"] == 124.0
    assert all(0 <= n[0] < 4 for n in loop["played"]), loop["played"]
    assert sorted({c[0] for c in loop["lane"]["cells"]}) == ["C4", "E4", "G4"]
    # (no slot on Synths: a free loop)
    assert [r["slots"] for r in s.slot_rows()] == [["Acid line", *[None] * 4]]
    # on the Loops page: into the selected empty slot, played from it
    studio.nav("perform")
    studio.settle()
    page.locator(f"{_slot(2, 1)} .sl-empty").click()
    page.locator(f'{_slot(2, 1)}[aria-selected="true"]').wait_for()
    _midi_note(page, 69, 300)
    page.locator(".view-head h1").click()
    with page.expect_response("**/take/keys/capture") as answer:
        page.click("#key-capture")
    assert answer.value.ok
    got = unquote(answer.value.headers["x-loop-name"])
    _wait(page, lambda: s.slot_at(2, 1) == got, "the capture in the slot")
    page.wait_for_function(SWITCHED, timeout=8000)
    page.locator(f'{_slot(2, 1)}[data-state="playing"]').wait_for()
    assert any(c[0] == "A4" for c in s.layers[2]["cells"])
    # a capture never overwrites: with that filled slot still
    # selected, the next goes into lane 3's next empty slot, the toast says so
    page.locator(f'{_slot(2, 1)}[aria-selected="true"]').wait_for()
    _midi_note(page, 72, 300)
    page.locator(".view-head h1").click()
    with page.expect_response("**/take/keys/capture") as answer:
        page.click("#key-capture")
    assert answer.value.ok
    more = unquote(answer.value.headers["x-loop-name"])
    _wait(page, lambda: s.slot_at(2, 2) == more, "the next capture in the next slot")
    assert s.slot_at(2, 1) == got  # the filled slot keeps its loop
    page.locator(".toast", has_text="this went into scene 3").first.wait_for()
    page.click("#play")
    assert studio.unexplained() == []


def test_the_loops_page_master_recording_reads_resample(studio, server):
    """ "⏺ jam" (the master bus to a clip) is "⏺ resample", apart from ⏺ Rec."""
    page = studio.open("perform")
    rec = page.locator("#jam-rec")
    assert rec.inner_text() == "⏺ resample"
    assert rec.get_attribute("data-cmd") == "Resample: record what you hear to a clip"
    assert studio.unexplained() == []
