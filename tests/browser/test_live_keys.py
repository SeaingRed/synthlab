"""Journeys 2-4: the live keys on Synths — pointer, typing, chords, MIDI.

The probes (conftest): `#cpu-meter` is written from the voice worklet's own
reports, so "1 voice · wasm" means the worklet holds one voice on the
WebAssembly engine; the master analyser's level means samples reached the
output stage. Together: the worklet voice is what is sounding (the WAV
fallback never raises the worklet's voice count). Nothing here judges
timbre, tuning, clicks or latency.

The first press on a fresh page plays the WAV fallback by design (the
worklet modules load on it); the worklet takes the presses after.
"""

from __future__ import annotations

C4 = '.pkey[data-note="0"][data-rel="0"]'
E4 = '.pkey[data-note="4"][data-rel="0"]'
LAYER_FX = 'select.fx-adder[aria-label="add effect to Layer 1 FX"]'
MASTER_FX = 'select.fx-adder[aria-label="add effect to Master FX"]'


def _bench(studio):
    page = studio.open("editor")
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    return page


def test_first_press_sounds_and_brings_the_worklet_voice_up(studio):
    page = _bench(studio)
    assert studio.ctx_state() is None  # nothing runs before the first gesture
    fetched: list[str] = []
    page.on("request", lambda r: fetched.append(r.url.replace(studio.url, "")))

    studio.pointer_down(C4)
    assert page.locator(f"{C4}.down").count() == 1
    studio.wait_sound()  # the WAV fallback, at once
    assert studio.ctx_state() == "running"
    studio.pointer_up()
    assert page.locator(".pkey.down").count() == 0
    studio.wait_silence()

    # the modules loaded: live.start awaits all four addModule calls
    # (voice, sampler, fx, eq), the tables and the wasm compile before the
    # first voice node exists, and only a node writes the meter
    assert "wasm" in studio.wait_live()
    assert any(u.startswith("/wavetables.bin") for u in fetched)
    assert "/static/voice-dsp.wasm" in fetched

    # the second press is the worklet's
    studio.pointer_down(C4)
    studio.wait_meter(r"1 voice · wasm")
    studio.wait_sound()
    studio.pointer_up()
    studio.wait_meter(r"0 voices")
    studio.wait_silence()


def test_two_quick_presses_on_a_fresh_page_leave_a_working_voice(studio):
    """Both presses land while the modules load. Each used to start its own
    graph: the chains wired to the first masterIn never reached the key bus,
    so the worklet held voices that nothing heard."""
    page = _bench(studio)
    for _ in range(2):
        studio.pointer_down(C4)
        page.wait_for_timeout(30)
        studio.pointer_up()
        page.wait_for_timeout(30)
    studio.wait_live()
    studio.wait_silence()

    studio.pointer_down(E4)
    studio.wait_meter(r"1 voice")
    studio.wait_sound(floor=0.01)  # the worklet voice reaches the output
    studio.pointer_up()
    studio.wait_meter(r"0 voices")
    studio.wait_silence()
    assert page.locator(".pkey.down").count() == 0


def test_fx_units_added_and_removed_under_a_held_note(studio):
    """Add the EQ (flat, then boosted), a master reverb, then remove the EQ
    while a worklet note is held: every change rebuilds or updates the live
    chain (the removed worklet node goes through `dispose`) and the note
    keeps sounding. The boost moving the level is the proof the EQ's bands
    (eq-worklet.js, in the FX worklet's scope) process the key."""
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.evaluate("window.synthlab.piano.noteOn('held', 60, 1)")
    studio.wait_meter(r"1 voice")
    dry = studio.level()
    assert dry > 0.01

    page.select_option(LAYER_FX, "eq")
    page.wait_for_selector('.fx-unit button[aria-label="remove eq"]')
    studio.settle()
    flat = studio.level()
    assert abs(flat - dry) < 0.02 * dry  # a flat EQ is a bypass

    low = page.locator(".fx-unit label.fx-param", has_text="low").first
    low.locator("input").fill("12")  # +12 dB low shelf; posts on change
    page.wait_for_function("() => window.synthlab.piano.patch().fx?.[0]?.low === 12")
    page.wait_for_timeout(300)
    boosted = studio.level()
    assert boosted > 1.05 * flat, (flat, boosted)
    assert "1 voice" in studio.meter()  # same chain shape: the note was kept

    page.select_option(MASTER_FX, "reverb")  # a native ConvolverNode stage
    page.wait_for_function("() => document.querySelectorAll('.fx-unit').length === 2")
    studio.settle()
    assert studio.level() > 0.01

    page.click('button[aria-label="remove eq"]')  # the fx node is replaced
    page.wait_for_function("() => document.querySelectorAll('.fx-unit').length === 1")
    studio.settle()
    page.wait_for_timeout(300)
    assert studio.level() > 0.01
    assert "1 voice" in studio.meter()

    page.evaluate("window.synthlab.piano.noteOff('held')")
    studio.wait_meter(r"0 voices")
    # a fresh press through the rebuilt graph
    studio.pointer_down(C4)
    studio.wait_meter(r"1 voice")
    studio.wait_sound()
    studio.pointer_up()
    studio.wait_meter(r"0 voices")


def test_the_fx2_units_play_under_a_held_note(studio):
    """A tape on the layer (fx2-worklet.js, the FX scope's fourth module)
    and a spring on the master (a ConvolverNode on /fx/ir.wav?kind=spring):
    the keys stay on the worklet, the note keeps sounding, and the tape's
    drive turned to 24 dB rounds the level down — the unit is processing
    the key, not passing it."""
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.evaluate("window.synthlab.piano.noteOn('held', 60, 1)")
    studio.wait_meter(r"1 voice")
    page.select_option(LAYER_FX, "tape")
    page.wait_for_selector('.fx-unit button[aria-label="remove tape"]')
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(300)
    warm = studio.level()
    assert warm > 0.01
    drive = page.locator(".fx-unit label.fx-param", has_text="drive").first
    drive.locator("input").fill("24")  # posts on change
    page.wait_for_function("() => window.synthlab.piano.patch().fx?.[0]?.drive === 24")
    page.wait_for_timeout(300)
    hot = studio.level()
    assert 0.0 < hot < 0.8 * warm, (warm, hot)
    page.select_option(MASTER_FX, "spring")
    page.wait_for_function("() => document.querySelectorAll('.fx-unit').length === 2")
    studio.settle()
    page.wait_for_timeout(300)
    assert studio.level() > 0.002
    assert "1 voice" in studio.meter()
    page.evaluate("window.synthlab.piano.noteOff('held')")
    studio.wait_meter(r"0 voices")


def test_the_dynamics_units_play_under_a_held_note(studio):
    """The dynamics units (fx2-worklet.js too): a limiter on
    the layer and an OTT on the master keep the keys on the worklet, and
    the limiter's ceiling pulled to -12 dB brings the held note down — the
    unit is limiting the key, not passing it."""
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.evaluate("window.synthlab.piano.noteOn('held', 60, 1)")
    studio.wait_meter(r"1 voice")
    page.select_option(LAYER_FX, "limiter")
    page.wait_for_selector('.fx-unit button[aria-label="remove limiter"]')
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(300)
    open_ = studio.level()
    assert open_ > 0.01
    ceiling = page.locator(".fx-unit label.fx-param", has_text="ceiling").first
    ceiling.locator("input").fill("-12")  # posts on change
    page.wait_for_function(
        "() => window.synthlab.piano.patch().fx?.[0]?.ceiling === -12"
    )
    page.wait_for_timeout(300)
    held = studio.level()
    assert 0.0 < held < 0.8 * open_, (open_, held)
    page.select_option(MASTER_FX, "ott")
    page.wait_for_function("() => document.querySelectorAll('.fx-unit').length === 2")
    studio.settle()
    assert page.evaluate("window.synthlab.piano.patch().worklet_ok") is True
    page.wait_for_timeout(300)
    assert studio.level() > 0.002
    assert "1 voice" in studio.meter()
    page.evaluate("window.synthlab.piano.noteOff('held')")
    studio.wait_meter(r"0 voices")


def test_keyboard_focus_survives_an_fx_slider_change(studio):
    """A slider change swaps the whole rack. Replaced (outerHTML), that
    destroyed the focused slider: the first arrow press moved it, the rest
    went to <body> (found by this suite). A parameter change now morphs the
    rack, which keeps the node — and its focus."""
    page = _bench(studio)
    page.select_option(LAYER_FX, "eq")
    page.wait_for_selector('.fx-unit button[aria-label="remove eq"]')
    studio.settle()
    low = ".fx-unit label.fx-param input"
    page.locator(low).first.focus()
    for presses in (1, 2, 3):
        page.keyboard.press("ArrowRight")  # one step: 0.24 dB
        page.wait_for_function(
            "(n) => Math.abs(Number(document.querySelector('.fx-unit label.fx-param"
            " input').getAttribute('value')) - 0.24 * n) < 0.01",
            arg=presses,
        )
        studio.settle()
        assert page.evaluate(
            "(sel) => document.activeElement === document.querySelector(sel)", low
        ), presses


def _focused(page) -> str:
    """The focused control, as the rack names it."""
    return page.evaluate(
        "() => { const a = document.activeElement;"
        " const unit = a.closest?.('.fx-unit');"
        " const all = [...document.querySelectorAll('.fx-unit')];"
        " const at = unit ? all.indexOf(unit) : -1;"
        " const name = a.getAttribute('aria-label') || `${a.tagName}:${a.type || ''}`;"
        " return `${name}@${at}`; }"
    )


def _units(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.fx-unit header strong')]"
        ".map((s) => s.textContent.trim())"
    )


def _until(page, check, what) -> None:
    for _ in range(120):
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"{what}: {_focused(page)} in {_units(page)}")


def test_keyboard_focus_follows_fx_add_move_and_remove(studio):
    """Add, move and remove replace the rack, so the control pressed is
    gone. The answer names the control that takes the keyboard (drawn with
    `autofocus`, focused by htmx's own settle): the new unit's first
    control, the moved unit's same button at its new place, the unit after
    a removed one — or the adder. Driven from the keyboard only."""
    page = _bench(studio)
    adder = page.locator(LAYER_FX)

    adder.focus()
    page.select_option(LAYER_FX, "eq")
    _until(page, lambda: _focused(page) == "EQ low@0", "add: the new unit's slider")
    page.keyboard.press("ArrowRight")  # and it is live: the slider moves
    page.wait_for_function(
        "() => Number(document.querySelector('.fx-unit label.fx-param input')"
        ".getAttribute('value')) > 0"
    )
    assert _focused(page) == "EQ low@0"

    page.select_option(LAYER_FX, "reverb")
    _until(page, lambda: len(_units(page)) == 2, "the reverb never arrived")
    _until(page, lambda: _focused(page) == "Reverb decay@1", "add: the second unit")
    page.select_option(LAYER_FX, "delay")
    _until(page, lambda: len(_units(page)) == 3, "the delay never arrived")
    first, second, third = _units(page)

    # move the first unit later, twice, without touching the pointer: the
    # keyboard rides along with the unit
    page.focus('.fx-unit:nth-of-type(1) button[aria-label="move eq later"]')
    page.keyboard.press("Enter")
    _until(page, lambda: _units(page) == [second, first, third], "move 1")
    _until(page, lambda: _focused(page) == "move eq later@1", "move 1: focus")
    page.keyboard.press("Enter")
    _until(page, lambda: _units(page) == [second, third, first], "move 2")
    _until(page, lambda: _focused(page) == "move eq later@2", "move 2: focus")
    page.keyboard.press("Enter")  # at the end: nothing moves, focus stays
    page.wait_for_timeout(400)
    assert _units(page) == [second, third, first]
    assert _focused(page) == "move eq later@2"
    page.keyboard.press("Shift+Tab")  # its neighbour: "move eq earlier"
    assert _focused(page) == "move eq earlier@2"
    page.keyboard.press("Enter")
    _until(page, lambda: _units(page) == [second, first, third], "move back")
    _until(page, lambda: _focused(page) == "move eq earlier@1", "move back: focus")

    # remove the first unit: the one that takes its place has the keyboard
    page.focus('.fx-unit button[aria-label="remove reverb"]')
    page.keyboard.press("Enter")
    _until(page, lambda: _units(page) == [first, third], "remove 1")
    _until(page, lambda: _focused(page) == "remove eq@0", "remove 1: focus")
    page.keyboard.press("Tab")  # its first slider is next in the tab order
    assert _focused(page) == "EQ low@0"
    page.focus('.fx-unit button[aria-label="remove delay"]')
    page.keyboard.press(
        "Enter"
    )  # the last unit: the one before stays, the adder gets focus
    _until(page, lambda: _units(page) == [first], "remove 2")
    _until(
        page, lambda: _focused(page) == "add effect to Layer 1 FX@-1", "remove 2: focus"
    )
    studio.settle()


def test_typing_keys_play_and_release(studio):
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.keyboard.down("a")  # KEYMAP a = C
    studio.wait_meter(r"1 voice")
    assert page.locator(f"{C4}.down").count() == 1
    studio.wait_sound()
    page.keyboard.down("d")  # E: a second voice
    studio.wait_meter(r"2 voices")
    page.keyboard.up("a")
    page.keyboard.up("d")
    studio.wait_meter(r"0 voices")
    assert page.locator(".pkey.down").count() == 0
    studio.wait_silence()


def test_a_modifier_pressed_mid_hold_leaves_no_stuck_note(studio):
    """Hold a letter, press a modifier, release the letter: the keyup's
    `key` may no longer be the letter that started the note (Shift makes it
    "A"; AltGr/Option rename it outright), so the release goes by `code`."""
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    for modifier in ("Shift", "Alt", "AltGraph"):
        page.keyboard.down("KeyA")
        studio.wait_meter(r"1 voice")
        page.keyboard.down(modifier)
        page.keyboard.up("KeyA")
        page.keyboard.up(modifier)
        studio.wait_meter(r"0 voices")
        assert page.locator(".pkey.down").count() == 0, modifier
    studio.wait_silence()


def test_window_blur_releases_every_held_key(studio):
    """A synthetic `blur` on window (headless Chromium never loses focus on
    its own): the handler's release path, not the OS focus change."""
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.keyboard.down("a")
    page.keyboard.down("d")
    studio.wait_meter(r"2 voices")
    page.evaluate("window.dispatchEvent(new Event('blur'))")
    studio.wait_meter(r"0 voices")
    assert page.locator(".pkey.down").count() == 0
    studio.wait_silence()
    page.keyboard.up("a")
    page.keyboard.up("d")


def test_chord_mode_pointer_plays_a_chord(studio):
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.click('[data-plays="triad"]')  # each key plays: a triad (one click)
    studio.pointer_down(C4)
    studio.wait_meter(r"[3-9] voices")  # a triad or more, one voice a note
    studio.wait_sound()
    assert page.locator(".pkey.down").count() >= 3
    studio.pointer_up()
    studio.wait_meter(r"0 voices")
    assert page.locator(".pkey.down").count() == 0


# A Web MIDI stand-in: headless Chromium has no MIDI device, so
# requestMIDIAccess answers with one fake input. midi.js runs unchanged —
# its click handler, attach() and onMessage — the test then calls the
# `onmidimessage` it installed, as the browser would for a real port.
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


def test_midi_note_on_in_chord_mode_does_not_freeze_the_page(studio):
    """A MIDI note-on in chord mode used to recurse forever (the chord's
    notes went back through the bridge as chord roots) and froze the tab."""
    studio.page.add_init_script(_FAKE_MIDI)
    page = _bench(studio)
    studio.start_voice()
    studio.wait_silence()
    page.click("#midi-btn")
    page.wait_for_function(
        "() => document.getElementById('midi-btn').textContent === 'MIDI 1'"
    )
    assert page.evaluate("typeof window.__midiIn.onmidimessage") == "function"

    page.evaluate("window.__midi([0x90, 60, 100])")  # note mode first
    studio.wait_meter(r"1 voice")
    page.evaluate("window.__midi([0x80, 60, 0])")
    studio.wait_meter(r"0 voices")

    page.click('[data-plays="triad"]')  # MIDI follows what each key plays
    page.evaluate("window.__midi([0x90, 60, 100])")
    assert studio.responsive()
    studio.wait_meter(r"[3-9] voices")
    studio.wait_sound()
    # the dock names the chord MIDI is holding, as it does a key's
    page.wait_for_selector('#kbd-now[data-state="held"]')
    name = page.evaluate("() => window.synthlab.plays.chordOf(60).name")
    assert page.text_content("#kbd-now") == name
    page.evaluate("window.__midi([0x80, 60, 0])")
    studio.wait_meter(r"0 voices")
    assert page.locator(".pkey.down").count() == 0
    assert studio.responsive()


def test_shift_is_a_sustain_pedal(studio):
    """Hold Shift (piano.js): a key let go keeps sounding — its key lit,
    `held` — until Shift comes up; struck again it starts over; in a text
    field Shift is a capital, never the pedal. Proves the key state and
    the release, not how a sustained note sounds (nobody has listened)."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    lit = "() => document.querySelectorAll('#piano .pkey.down').length"
    page.keyboard.down("Shift")
    assert page.evaluate("() => window.synthlab.pedal()")
    assert page.evaluate("() => document.documentElement.hasAttribute('data-pedal')")
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    page.keyboard.down("KeyD")
    page.keyboard.up("KeyD")
    page.wait_for_timeout(400)  # past hold-off's short gate too
    assert page.evaluate(lit) == 2
    assert page.locator("#piano .pkey.down.held").count() == 2
    page.keyboard.down("KeyA")  # struck again while held: sounding, not held
    assert page.locator("#piano .pkey.down.held").count() == 1
    page.keyboard.up("KeyA")
    page.keyboard.up("Shift")
    page.wait_for_function(f"() => ({lit})() === 0")
    assert not page.evaluate("() => window.synthlab.pedal()")
    # in a field Shift types a capital: no pedal
    page.click("#dock-filter")
    page.keyboard.down("Shift")
    assert not page.evaluate("() => window.synthlab.pedal()")
    page.keyboard.up("Shift")


def test_right_shift_latches_the_next_note_to_play_over(studio):
    """Tap Right Shift (piano.js `latchTap`): the next note — with the rest
    of its chord — holds after it is let go; other notes play over it as
    usual; its key struck again is a note of its own; Left Shift's pedal
    leaves it; Right Shift again lets it go. Key state only (unheard)."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    latch = "() => window.synthlab.latch()"
    lit = "() => document.querySelectorAll('#piano .pkey.down').length"
    page.keyboard.press("ShiftRight")
    assert page.evaluate(latch) == {"armed": True, "notes": 0}
    assert page.evaluate("() => document.documentElement.dataset.latch") == "armed"
    # a two-note chord, struck together: both latched
    page.keyboard.down("KeyA")
    page.keyboard.down("KeyG")
    page.keyboard.up("KeyA")
    page.keyboard.up("KeyG")
    page.wait_for_timeout(400)
    assert page.evaluate(latch) == {"armed": False, "notes": 2}
    assert page.locator("#piano .pkey.down.latched").count() == 2
    # a note over the drone comes and goes
    page.keyboard.down("KeyE")
    assert page.evaluate(lit) == 3
    page.keyboard.up("KeyE")
    page.wait_for_function(f"() => ({lit})() === 2")
    # the drone's key again: a note of its own, the drone stays
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    page.wait_for_timeout(300)
    assert page.evaluate(latch)["notes"] == 2
    assert page.locator("#piano .pkey.down.latched").count() == 2
    # Left Shift's pedal comes and goes: the latch stays
    page.keyboard.down("ShiftLeft")
    page.keyboard.up("ShiftLeft")
    assert page.evaluate(latch)["notes"] == 2
    page.keyboard.press("ShiftRight")
    page.wait_for_function(f"() => ({lit})() === 0")
    assert page.evaluate(latch) == {"armed": False, "notes": 0}
    assert page.evaluate("() => !('latch' in document.documentElement.dataset)")


def test_a_held_note_struck_again_is_a_voice_of_its_own(studio):
    """A voice token per note-on (piano.js `voices`): the pedal and the
    latch are holds on a voice, so a latched key struck again sounds a
    second voice beside the drone (and lets only that one go), and under
    the pedal the same note struck again starts over — one voice, not two.
    Proves the worklet's voice count, not how it sounds."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()
    studio.start_voice()
    studio.wait_meter(r"0 voices")
    page.keyboard.press("ShiftRight")  # the latch: A holds
    page.keyboard.down("KeyA")
    page.keyboard.up("KeyA")
    page.wait_for_timeout(300)
    studio.wait_meter(r"1 voice\b")
    page.keyboard.down("KeyA")  # struck again: the drone and a new voice
    studio.wait_meter(r"2 voices")
    page.keyboard.up("KeyA")
    studio.wait_meter(r"1 voice\b")
    assert page.evaluate("() => window.synthlab.latch()")["notes"] == 1
    page.keyboard.press("ShiftRight")
    studio.wait_meter(r"0 voices")
    # under the pedal: struck again starts over
    page.keyboard.down("ShiftLeft")
    page.keyboard.down("KeyD")
    page.keyboard.up("KeyD")
    studio.wait_meter(r"1 voice\b")
    page.keyboard.down("KeyD")
    page.wait_for_timeout(200)
    studio.wait_meter(r"1 voice\b")
    page.keyboard.up("KeyD")
    page.keyboard.up("ShiftLeft")
    studio.wait_meter(r"0 voices")
