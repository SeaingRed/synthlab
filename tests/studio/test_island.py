import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app

ISLAND = Path("synthlab/studio/static/audio.js")
KEYS = Path("synthlab/studio/static/keys.js")


def test_every_island_states_a_budget_and_keeps_it():
    """Each island declares `Budget: <= N code lines` in its header. Nothing
    used to check them, so piano.js sat 82 lines over its stated 380 without
    anyone noticing. Reading N from the header means the comment can never
    lie: shipping more lines forces you to raise the number deliberately.
    """
    static = Path("synthlab/studio/static")
    # vendored third-party files are not ours to budget: htmx itself, the
    # official hx-prompt extension htmx 4 moved out of core, and GSAP
    vendored = {"htmx.min.js", "hx-prompt.min.js", "gsap.min.js"}
    islands = sorted(p for p in static.glob("*.js") if p.name not in vendored)
    # 9 islands + the worklet: voice.js was added for the Voice lab, whose
    # mic capture and codec work belong in neither audio.js nor a view island;
    # keys.js (the key voice) moved out of audio.js when it hit 745/745;
    # song.js is the Song page's player and arranger, and songrec.js its
    # recording half (one island, two files, each with its own budget);
    # motion.js took the scope/spectrum/meter out of audio.js and drives the
    # GSAP UI motion; the live keys' FX added the key engine's
    # graph (livefx.js: a voice node per layer, FX chains, master) and a
    # second worklet (fx-worklet.js: the FX units) — the voice worklet had
    # no room for a rack, and piano.js none for a graph; grid.js is the loop
    # editor's (step LEDs + per-grid playheads, playable row labels,
    # right-click erase, pattern undo, the pattern tools' motion) and
    # layout.js the workbench's (density, splitters, folds, the sampler's slice
    # markers); eq-worklet.js is the FX rack's parametric EQ for
    # the live keys, a module in fx-worklet.js's scope, which had no room;
    # sampler-worklet.js is the live voice's sampler oscillator (its buffers'
    # split and the mono / stereo read), a module in voice-worklet.js's scope
    # for the same reason, and grain-worklet.js beside it is the sampler's
    # formant mode (the engine's grain train: sampler-worklet.js is full);
    # songcell.js is the Song page's pattern-making
    # half (shared-pattern highlight, cell copy / paste / new keys, the
    # drawer's focus and scroll across swaps) — song.js sat at 310/310;
    # events.js is the server-push island (GET /events: render progress and
    # edits made on another page), which belongs to no view;
    # lab.js is the Sample Lab's (the selection dragged on any lane, its
    # edges, its keys, a click to hear a lane; a dropped file);
    # songedit.js is the Song island's fourth file (the song's undo / redo
    # keys, the drawer's "solo this cell" audition): song.js is full;
    # songdrag.js is its fifth (anything dropped on a lane of the playlist:
    # a pattern, a block — moved, or copied with Ctrl / Alt — a clip, a
    # take, a whole stem): song.js and songcell.js are both full;
    # labchop.js is the Sample Lab's second (a chop's edge dragged or nudged,
    # a double-click that cuts one, the Lab's open <details> across swaps):
    # lab.js sat at its 50;
    # click.js is the metronome (a tick on every beat — of the loop, the
    # song, a take, or its own at the tempo: audio.js, which owns the
    # loop, is full);
    # songline.js is the Song island's sixth file (the playlist's time axis:
    # zoom, the ruler's start marker, following the playhead, the section's
    # and a block's edges): song.js is the player and sheds lines to it;
    # hardware control is five: deck.js (the control surface's
    # model: dial slots, strip gestures, the key table), deckhid.js (the
    # Stream Deck + over WebHID: its bytes and nothing else, run under node
    # by test_deck_hid.py), deckui.js (the virtual deck's panel and the
    # canvas drawing both decks share), midilearn.js (a CC onto a slot or a
    # control: midi.js had the room for one line, not for a map) and pen.js
    # (pressure, eraser and barrel on the surfaces the other islands own);
    # the shell's three (they belong to no view):
    # palette.js is the command palette and the shortcut sheet's generated
    # half, shell.js the tooltips and the status strip's progress line,
    # tweak.js the pen and what every slider and knob shares (fine mode, the
    # wheel, the eraser) — audio.js and layout.js, the natural homes, are full;
    # menu.js is THE context menu: it replaces four separate ones —
    # ctlmenu.js (a value control's: Learn MIDI, Reset, Put on a dial), a
    # `data-ctx` opener in grid.js, the views' menu.js and one in bench.js —
    # as this one file: a thing's own `data-menu`
    # controls, a value control's generated rows, one #ctx-menu;
    # tweaktype.js is tweak.js's second file (a value typed into a control's
    # read-out, on every view: it was bench.js's, and tweak.js has six lines
    # left) — so the count stays 38: ctlmenu.js out, tweaktype.js in;
    # bench.js is the bench views': the patch bay's
    # leads and the shared audio player;
    # song creation is five more:
    # roll.js (the piano roll's tools, <= 260; 290 since it was filled: its
    # header says what the browser journeys added), zoom.js (any `[data-zoom]`
    # scroller, <= 70), steps.js (the step sequencer: <= 270 since the beat
    # workflow filled it; 295 for Kit's stacked hit lanes),
    # songsel.js (the playlist's range, <= 120) and jam.js (the launcher,
    # <= 240) — 43;
    # mixer.js is the Mix view's (the strips' meters lit
    # from the plan, the focused strip, one tab stop a strip): it belongs
    # to no file of the Song island, whose six are the Song page's — 44;
    # rowsounds.js is the typing rows' "rows → sounds" toggle (each row one
    # layer of the stack): piano.js, chords.js and livefx.js, the three it
    # sits between, were each within a line of their budgets — 45;
    # songdock.js is the Song island's seventh file (the sound dock: the
    # framed Synths page's requests, told to the player) — 46;
    # songtake.js is its eighth (a take: the count-in drawn, the metronome,
    # the finger drums, ✦ Clean up and its report — songrec.js keeps the
    # take itself and had 48 lines left) — 47;
    # the live talkbox is two: talkbox.js, its switch (the mic,
    # the key bus through setLiveOut) and talk-worklet.js, the vocoder in
    # the AudioWorklet scope (never a <script>, like voice-worklet.js) — 49;
    # samplerec.js is the sampler panel's ● Record sample (a mic take timed
    # to the session: the count-in, the click, the cut on the downbeat):
    # voice.js, whose capture it uses, has no room, and songrec.js and
    # songtake.js are the Song page's — 50;
    # deckart.js is hardware control's sixth (the look both decks share: the
    # keys' glass, the strip's traces, the afterglow, the spectrum — deckui.js
    # sat at 251/255 and keeps the panel and the device's pump) — 51;
    # transport.js is the transport: the registry every player
    # reports to, the top dock's "now playing", the cue beside each page's
    # ▶, Space and Shift+Space — it belongs to no player, and audio.js,
    # song.js and jam.js (the players) each grew a hook, not a UI — 52;
    # cliphub.js is the clip hub's (/view/clips: the bench's loop toggle,
    # the row just opened, a preview that plays, Ctrl+Z as the hub's Undo):
    # a page of its own, which no island's file is about — 53;
    # decksets.js is the deck's dial sets (named fours and your
    # own top four under the dials, one click pins all four): deckui.js sits
    # at its 300 and deck.js is the surface, not its preferences — 54;
    # keyrec.js is the keyboard dock's ⏺ Rec (what you play, the
    # master bus, into Clips live — a count-in, the REC state, the take's
    # toast): audio.js's jam is Perform's, samplerec.js a mic take, and
    # chords.js / piano.js had no room — 55
    # fxcables.js draws the FX racks' leads from where the modules are laid
    # out (real cables and plugs, joined beside or a row below):
    # every rack (Synths, the Song drawer, the booth) has it, and bench.js
    # is the patch bay's — 56
    # the Visuals page is two: visuals.js, the registry of
    # every way the dock's scope and spectrum can draw (motion.js shed its
    # drawing to it and keeps the measuring), and vispage.js, the page's
    # gallery, demo signal and swap — a page of its own; colours.js is the
    # palette's hues (the Colours popover's sliders, kept per browser), the
    # one island loaded without `defer`, in <head>, so a chosen hue is on
    # <html> before the first paint — 59
    # the live vocal chain is two: vocal.js, the mic through a
    # live rack, the monitor and ⏺ (talkbox.js is the keys' vocoder and full,
    # keyrec.js the keys' take), and vox-worklet.js, its units and the pitch
    # shifter in fx-worklet.js's scope (which had no room) — 61;
    # keysbar.js is the keyboard dock's toolbar as a face (the
    # note / chord shortcuts, the keys lit and labelled with what they play,
    # the chord's name, the typing rows' strip) — chords.js and piano.js
    # hold the state and had no room;
    # fx2-worklet.js is the second module of FX units (the
    # auto-filter, tremolo, tape, ping-pong, formant and frequency shifters,
    # exciter, vocoder), a fourth module in that scope: fx-worklet.js and
    # vox-worklet.js are both full;
    # visuals2.js is the visualiser registry's second file (sixteen more
    # variations, the pitch / chroma / stereo reads they share):
    # visuals.js sits at its budget with the infrastructure and the first
    # thirteen — 64;
    # workspace.js is the workspace (every module collapsed,
    # popped out, focused, and in a dock moved, tabbed and resized; the
    # page's layouts per browser, its presets and its added modules) — it
    # belongs to no view, and layout.js (the splitters, the folds, the
    # density) sat at its budget; loaded in <head> like colours.js, so a
    # saved layout is drawn before the first paint;
    # labview.js is the Sample Lab's third (Ctrl + wheel zoom,
    # the overview strip, the ruler and the bar / beat lines drawn for any
    # span, the playhead, the lanes' height): lab.js took the selection on
    # any lane, its edges and keys, and had no room;
    # library.js is the Library page's (its filter, the chips'
    # counts, the arrow keys across a grid of cards, cards or rows, the
    # playing lamp): the filter sat in audio.js, at 761 of its 762 — 67;
    # letters.js is the keyboard dock's letters layout (A-G play
    # that note, the modifiers held make the chord on it, 1-7 the octave):
    # chords.js, piano.js and keysbar.js each sat within a few lines of
    # their budgets — 68;
    # panelfaces.js is the drawn controls on the Synths panels (the
    # FM4 algorithms picked from their drawings, the ratio presets, the
    # wavetables' position) and the FX racks' chain browser (its search,
    # purpose / genre, its keys): bench.js, audio.js and
    # fxcables.js, the nearest homes, each sat at or near their budgets — 69;
    # wtdraw.js is the wavetable editor (four frames
    # drawn with the pointer or the pen into a hidden field): panelfaces.js,
    # its nearest home, sat at its budget — 70;
    # columns.js is the keyboard dock's third typing layout (each
    # keyboard column a root — the home row its note, the row above major,
    # the row below minor, the number row a choice): letters.js keeps the
    # switch and sat at its budget with it — 71;
    # zonemap.js is the sampler's zone map (a multi-sample
    # instrument: its boxes dragged and stepped, what a key sounded lit):
    # layout.js, the slice markers' home, sat near its budget — 72;
    # arp.js is the keyboard dock's keys arp (a held chord played
    # as an arpeggio on the tempo and the loop's grid, each note posted
    # ahead with its time): piano.js sat at 712 of its 715 and chords.js at
    # 366 of 370 — 73;
    # soundmenu.js is the one sound menu (a lane head's searchable sound ▾,
    # what a load replaces — on
    # Perform and Synths' Load —, a Song lane's list): songdock.js, where
    # the list's copy lived, is the Song page's dock wire, and the menu is
    # every page's — 74;
    # loopspanel.js is Perform's Loops panel (open / folded /
    # pinned, search, kind, ★, a tag, sort, a row dragged onto a grid) and
    # ▶ in time on every Loops list, which it took from keyrec.js (at its
    # budget with the take): a module of a page, no island's — 75;
    # coach.js is Perform's ✦ Coach (open / closed and Alt+K,
    # its proposal drawn on the two grids, 🎙 hold to talk): the panel is
    # the server's, and no island near it had the room — loopspanel.js sat
    # at its budget, grid.js and roll.js own the grids' gestures — 76;
    # slots.js is Perform's launcher (lanes x scenes of
    # slots — the quantise audio.js asks, queued slots until the switch,
    # drags onto a slot, the grid's roving keys and Alt+1…8 / Alt+0):
    # perform.js sat at 147 of its 150 — 77;
    # shaper.js is free record's shaper (a free take made into a
    # loop in a time-true roll — its trim, cuts, fit / crop, quantise, the
    # tempo guess, ▶ and Make loop): a popover of every page, and keyrec.js
    # — the take — had 20 lines of room, roll.js owns the step grid — 78;
    # kitpads.js is Kit's pads drawn (each voice's one-shot,
    # its envelope and numbers on its pad, redrawn after a knob's post):
    # steps.js owns the grid and sat 13 lines under its budget — 79;
    # pagefill.js is the filled pages' live parts (the
    # Song editor's rows fitted to its pane and its ghost repeats, the Mix
    # master's read-outs, a Breed litter's entrance): mixer.js, song.js,
    # roll.js and motion.js had no room for them — 80;
    # pagemotion.js is the page transition (the old page
    # leaving in htmx's view transition, the new one's modules arriving on
    # GSAP): motion.js, whose entrance it replaced, sat at 264 of 265 — 81
    assert len(islands) == 81, [p.name for p in islands]
    stubs = {
        "roll.js": 290,
        "zoom.js": 70,
        "steps.js": 295,  # 270 + Kit's stacked hit lanes
        "songsel.js": 120,
        "jam.js": 250,  # 240 + the transport's hooks
    }
    for path in islands:
        if path.name in stubs:
            text = path.read_text(encoding="utf-8")
            assert f"Budget: <= {stubs[path.name]} code lines" in text, path.name
    over = []
    for path in islands:
        text = path.read_text(encoding="utf-8")
        m = re.search(r"Budget:\s*<=\s*(\d+)\s*code lines", text)
        assert m, f"{path.name} states no line budget in its header"
        stated = int(m.group(1))
        actual = len(
            [
                ln
                for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith(("//", "*", "/*"))
            ]
        )
        if actual > stated:
            over.append(f"{path.name}: {actual} lines vs stated budget {stated}")
    assert not over, "islands over their stated budget: " + "; ".join(over)


def test_island_served_and_within_budget(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/audio.js")
    assert r.status_code == 200
    lines = [
        ln
        for ln in ISLAND.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith(("//", "*", "/*"))
    ]
    # 340 -> 390 (a11y pass): vel-lane keyboard, pointercancel commits,
    # focus restoration after full-body swaps, context-state handling.
    # 465 -> 480 (static pass): polyphony limiter + seamless-loop builder.
    # 480 -> 575 (UI pass): peak/clip meter, Library filtering, shortcut
    # sheet opener, at-rest loop waveform.
    # 575 -> 580 (transport truth): resync #play after a view swap, and the
    # dock's global stop for views that ship no transport of their own.
    # 625 -> 700: bench fixes (error LED, same-URL guard, phase-continuous
    # crossfade, log sliders, dock filter, curve scroll).
    # 580 -> 625 (performance FX): loopFx over the sounding buffer — rate
    # ramps, loop-window stutter/freeze, reversed-buffer swap.
    assert len(lines) <= 762, f"audio.js is {len(lines)} code lines (budget 762)"


def test_xy_pad_reaches_sounding_voices():
    """A held chord follows the XY pad: worklet voices via "mod" (incl.
    level), WAV-fallback keys via the live key filter/gain in keys.js."""
    piano = PIANO.read_text(encoding="utf-8")
    assert 'typeof patch[param] !== "number"' in piano  # any numeric axis
    assert "window.synthlab.liveMod(param, value)" in piano
    audio = KEYS.read_text(encoding="utf-8")
    assert "BiquadFilterNode" in audio and "function liveMod" in audio
    # (a pre-rendered lane plays into livefx.js's master chain instead)
    assert "connect(g).connect(o.out || keyPath())" in audio
    # the worklet's voices read the shared block a "mod" writes into
    worklet = WORKLET.read_text(encoding="utf-8")
    assert 'this.T[m.param === "level" ? "osc1_level" : m.param] = m.value' in worklet


def test_loop_playhead_is_compositor_only(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    assert "tickPlayhead" in js and '"--phase"' in js
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    head = css.split(".grid .playhead::before {")[1].split("}")[0]
    assert "translate: calc(var(--phase) * 100cqw)" in head  # no left/width per frame
    # the note grid (and its playhead) moved to Perform; Synths is a bench
    html = TestClient(create_app(data_dir=tmp_path)).get("/view/perform").text
    assert '<i class="playhead" aria-hidden="true"' in html  # (over its rows: grid-row)


def test_strummed_chord_release_cancels_pending_notes():
    js = Path("synthlab/studio/static/chords.js").read_text(encoding="utf-8")
    assert "h.timers.forEach(clearTimeout)" in js
    assert 'id.startsWith("ppad:")' in js  # release by held id, not by .down class


def test_no_other_scripts_in_page(tmp_path):
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    import re

    srcs = re.findall(r'<script[^>]*src="([^"]+)"', html)
    srcs = [src.split("?")[0] for src in srcs]
    # the islands + htmx + hx-prompt (htmx 4 moved hx-prompt out of core;
    # the official 356-byte extension restores it for the panel preset save)
    # + GSAP, which motion.js drives: fetched on first launch (vendor.py), so
    # the page carries it only once the file is there
    gsap = Path("synthlab/studio/static/gsap.min.js").exists()
    assert ("/static/gsap.min.js" in srcs) == gsap
    if not gsap:
        srcs.append("/static/gsap.min.js")
    assert sorted(srcs) == [
        "/static/arp.js",
        "/static/audio.js",
        "/static/bench.js",
        "/static/chords.js",
        "/static/click.js",
        "/static/cliphub.js",
        "/static/coach.js",
        "/static/colours.js",
        "/static/columns.js",
        "/static/deck.js",
        "/static/deckart.js",
        "/static/deckhid.js",
        "/static/decksets.js",
        "/static/deckui.js",
        "/static/events.js",
        "/static/fxcables.js",
        "/static/grid.js",
        "/static/gsap.min.js",
        "/static/htmx.min.js",
        "/static/hx-prompt.min.js",
        "/static/jam.js",
        "/static/keyrec.js",
        "/static/keys.js",
        "/static/keysbar.js",
        "/static/kitpads.js",
        "/static/lab.js",
        "/static/labchop.js",
        "/static/labview.js",
        "/static/layout.js",
        "/static/letters.js",
        "/static/library.js",
        "/static/livefx.js",
        "/static/loopspanel.js",
        "/static/menu.js",
        "/static/midi.js",
        "/static/midilearn.js",
        "/static/mixer.js",
        "/static/motion.js",
        "/static/pagefill.js",
        "/static/pagemotion.js",
        "/static/palette.js",
        "/static/panelfaces.js",
        "/static/pen.js",
        "/static/perform.js",
        "/static/piano.js",
        "/static/roll.js",
        "/static/rowsounds.js",
        "/static/samplerec.js",
        "/static/shaper.js",
        "/static/shell.js",
        "/static/slots.js",
        "/static/song.js",
        "/static/songcell.js",
        "/static/songdock.js",
        "/static/songdrag.js",
        "/static/songedit.js",
        "/static/songline.js",
        "/static/songrec.js",
        "/static/songsel.js",
        "/static/songtake.js",
        "/static/soundmenu.js",
        "/static/space.js",
        "/static/steps.js",
        "/static/talkbox.js",
        "/static/transport.js",
        "/static/tweak.js",
        "/static/tweaktype.js",
        "/static/vispage.js",
        "/static/visuals.js",
        "/static/visuals2.js",
        "/static/vocal.js",
        "/static/voice.js",
        "/static/workspace.js",
        "/static/wtdraw.js",
        "/static/zonemap.js",
        "/static/zoom.js",
    ]
    assert "<script>" not in html  # no inline scripts
    # every island waits for the document but colours.js, which must run
    # before the first paint (a chosen hue on <html>; #view held hidden
    # while a saved layout waits for workspace.js): in <head>, after the
    # stylesheets, not deferred. workspace.js (45 KB) is not blocking:
    # its prelude, the wait, is in colours.js
    head = html[: html.index("<body")]
    blocking = re.findall(r'<script src="([^"?]+)[^"]*"></script>', html)
    assert blocking == ["/static/colours.js"]
    for js in blocking:
        assert head.index(js) > head.index("/static/dense.css")
    assert re.search(r'<script src="/static/workspace\.js\?v=\w+" defer>', head)
    colours = Path("synthlab/studio/static/colours.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("synthlab.ws")' in colours
    assert "root.dataset.wsWait" in colours


def test_canvases_track_device_pixel_ratio(tmp_path):
    # the scope/spectrum moved to motion.js, sized by a ResizeObserver rather
    # than by reading clientWidth every frame
    js = Path("synthlab/studio/static/motion.js").read_text(encoding="utf-8")
    assert "devicePixelRatio" in js and "ResizeObserver" in js
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    assert 'id="scope" width=' not in html  # sizing is JS-owned now


PIANO = Path("synthlab/studio/static/piano.js")


def _code_lines(path):
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith(("//", "*", "/*"))
    ]


def test_piano_island_served_and_within_budget(tmp_path):
    # Budget 360->380 (ledger-sanctioned): the arm-desync fix (syncArms +
    # htmx:after:swap re-apply, task-8/9 review round) needed a few more
    # lines than the 360 squeeze allowed without unreadable one-liners.
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/piano.js")
    assert r.status_code == 200
    # 396 -> 440 (a11y pass): shared key gate + keys toggle, Enter/Space on
    # focused keys, XY pad arrow keys, live aria-labels on octave change.
    # 490 -> 540: the pitch-bend wheel (strip, MIDI, XY axis, record bend)
    # 540 -> 590: the arrow bend (hold up/down to glide, its settings menu)
    # 590 -> 592: the classic split carries on past J (K O L P ; ')
    # 592 -> 615: Shift is the sustain pedal; 615 -> 645: Right Shift latches
    # 645 -> 660: a voice token per note-on, the pedal and latch its holds
    # 660 -> 680: the mod wheel and aftertouch (setMod, the XY axes,
    # MIDI's bridge, the take recording them)
    # 680 -> 690: keysOff (stop all ends the keys' notes, a latched one's
    # too) and a latch let go on another sound (no sticking notes);
    # 690 -> 715: the keyboard dock on every page (the typing gate, typing →
    # keys | pads, the key-note event, the same sound left alone)
    # 715 -> 730: the keys arp (a note it holds is a voice that does not
    # sound; a note it plays is due at a context time; `arpOff`)
    assert len(_code_lines(PIANO)) <= 730  # +Ctrl+Z undo, slider live-mod bridge


def test_piano_glide_markers(tmp_path):
    js = PIANO.read_text(encoding="utf-8")
    assert '"bend"' in js and "lastFreq" in js and "voiceKind" in js


def test_qwerty_moved_out_of_audio_island(tmp_path):
    assert "KEYMAP" not in ISLAND.read_text(encoding="utf-8")
    assert "KEYMAP" in PIANO.read_text(encoding="utf-8")
    assert "window.synthlab" in ISLAND.read_text(encoding="utf-8")


def test_play_url_bridged_for_sound_card_audition(tmp_path):
    assert "playUrl" in ISLAND.read_text(encoding="utf-8")


def test_latency_tuning_markers(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    piano = PIANO.read_text(encoding="utf-8")
    assert 'latencyHint: "interactive"' in js
    assert "> 80" in piano and "> 150" not in piano  # xy throttle tightened
    assert "warmKey" in piano


WORKLET = Path("synthlab/studio/static/voice-worklet.js")
LIVEFX = Path("synthlab/studio/static/livefx.js")


def test_voice_worklet_served_within_budget_and_not_a_script_tag(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/static/voice-worklet.js").status_code == 200
    # 200 -> 300: the engine's own chain (test_live_parity_corpus)
    # 300 -> 570: noise, pluck, wavetable, formant, the mod matrix, every LFO
    # shape and the block-structured render
    # 570 -> 585: the per-layer voice cap and stealing
    # 585 -> 600: silence culling and the CPU meter's per-quantum timer
    # 600 -> 740: the WebAssembly engine's glue (slots, the module's control
    # pass / stack / post calls, voice pairing) beside the JS DSP it falls
    # back to. The AssemblyScript sources (synthlab/studio/wasm/assembly/)
    # are not islands: they compile to static/voice-dsp.wasm, which isn't JS
    # 740 -> 770: the live sampler (its buffers, the read, the stereo pan law)
    # 770 -> 750: the read moved to sampler-worklet.js (<= 35, a module in
    # the same scope); the stereo sample's sum into the voice stays here
    # 750 -> 890: hard sync (syncBlock: every step
    # polyBLEP'd, a sample held back), the fm4 shape and analog drift (the
    # note's seed, phases, wander, cutoff), and their glue to voice.ts. Not
    # a module of their own: the oscillator loop and the control pass they
    # sit in are this file's, and every node harness evals it
    # 890 -> 975: the phase warp (warpBlock and the
    # warp's mode functions: WOS sub-samples, the FIR), your own wavetable's
    # read and one voice (mono / legato in noteOn / noteOff); the same reason
    # 975 -> 1170: the general filter chain
    # (xpost / xf / xcoef: the new types and filter 2, the comb's ring, glue
    # to voice.ts's xpost) and the modulation (twelve slots, LFO 2 / 3 and
    # their shapes in lfoX / lfoG, envelopes 3 / 4, the wheels' messages,
    # key and random, the new targets): the control pass and post() they
    # extend are this file's
    # 1170 -> 1176: a multi-sample instrument — the voice's
    # strike per key (the round robin) and a zone map's re-hit as a voice
    # of its own; the zone pick is sampler-worklet.js's (35 -> 64)
    # 1176 -> 1180: a note due later (the keys arp's `at`)
    # waits for its quantum (`due`)
    assert len(_code_lines(WORKLET)) <= 1180
    sampler_js = WORKLET.with_name("sampler-worklet.js")
    assert client.get("/static/sampler-worklet.js").status_code == 200
    assert len(_code_lines(sampler_js)) <= 64  # + the zone map
    assert "globalThis.synthlabSampler = { set, read, pick }" in sampler_js.read_text(
        encoding="utf-8"
    )
    assert 'addModule("/static/sampler-worklet.js")' in LIVEFX.read_text(
        encoding="utf-8"
    )
    # formant mode's grain train: a third module in the voice's scope
    # (<= 60), which sampler-worklet.js's read hands a copy to
    grain_js = WORKLET.with_name("grain-worklet.js")
    assert client.get("/static/grain-worklet.js").status_code == 200
    assert len(_code_lines(grain_js)) <= 60
    assert "globalThis.synthlabGrains = { on, read }" in grain_js.read_text(
        encoding="utf-8"
    )
    assert "globalThis.synthlabGrains" in sampler_js.read_text(encoding="utf-8")
    assert 'addModule("/static/grain-worklet.js")' in LIVEFX.read_text(encoding="utf-8")
    # the module is fetched and compiled by livefx.js, never a script tag
    assert "voice-dsp" not in client.get("/").text
    assert "registerProcessor" in WORKLET.read_text(encoding="utf-8")
    html = client.get("/").text
    assert "voice-worklet" not in html  # loaded via addModule, never a script tag
    # loaded by the key engine's graph (livefx.js), which piano.js starts
    assert "audioWorklet" in LIVEFX.read_text(encoding="utf-8")
    assert "window.synthlab.live.start(" in PIANO.read_text(encoding="utf-8")


def test_worklet_v2_markers(tmp_path):
    js = WORKLET.read_text(encoding="utf-8")
    for marker in ['"bend"', '"mod"', "fromFreq", "glideTime"]:
        assert marker in js, marker


def test_piano_panic_stops_notes_on_blur(tmp_path):
    piano = PIANO.read_text(encoding="utf-8")
    blur = piano[piano.index('"blur"') :]
    assert "noteOff" in blur and "allOff" in blur


def test_worklet_gating_is_honest(tmp_path):
    """The gate is a server-side whitelist now (studio/worklet.py); piano.js
    only reads its verdict off /patch.json, never re-derives it. No literal
    field/shape list survives client-side to drift from voice-worklet.js."""
    js = PIANO.read_text(encoding="utf-8")
    assert "workletCanPlay" in js
    assert "SUPPORTED" not in js
    assert "mod${i}_src" not in js
    assert "worklet_ok" in js


def test_synthlab_object_is_never_replaced_wholesale(tmp_path):
    """audio.js used to overwrite window.synthlab with a fresh object literal
    partway through its own file, silently dropping whatever it had already
    hung off it earlier in the same IIFE (logVal, set a few hundred lines
    above) — the exact bug behind piano.js:174's "logVal is not a function"
    on the first slider drag. Every creation/update must merge in place."""
    js = ISLAND.read_text(encoding="utf-8")
    assert js.count("window.synthlab =") == 0
    assert "window.synthlab ??= {}" in js
    assert "window.synthlab.logVal = logVal" in js


def test_live_mod_push(tmp_path):
    js = PIANO.read_text(encoding="utf-8")
    # the whole patch, not five LIVE_PARAMS: every control reaches held notes
    assert "LIVE_PARAMS" not in js and "function pushParams" in js
    assert "window.synthlab.live.setStack(patch)" in js
    assert '{ type: "params", p: lp }' in LIVEFX.read_text(encoding="utf-8")
    assert "select[name=xy_x]" in js or 'select[name="xy_x"]' in js


def test_reactive_light(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    assert "--live" in js and "setProperty" in js


SPACE = Path("synthlab/studio/static/space.js")


def test_space_island_served_and_within_budget(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/space.js")
    assert r.status_code == 200
    assert len(_code_lines(SPACE)) <= 150


def test_space_markers(tmp_path):
    js = SPACE.read_text(encoding="utf-8")
    assert "createConvolver" in js
    assert "synthlab.space" in js
    audio = ISLAND.read_text(encoding="utf-8")
    assert "setLiveOut" in audio


def test_loop_path_bypasses_space_chain(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    loop = js[js.index("function startLoop") : js.index("function stop")]
    assert "connect(master)" in loop and "liveOutNode" not in loop


def test_space_chain_has_safety_limiter(tmp_path):
    js = SPACE.read_text(encoding="utf-8")
    assert "createDynamicsCompressor" in js
    assert "dry.connect(comp)" in js or "connect(dry).connect(comp)" in js
    assert "wetDelay.connect(comp)" in js
    assert "wetRev).connect(comp)" in js
    assert "comp.connect(master)" in js


def test_factory_audition_buffers_survive_flush(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    flush = js[js.index('"preview-ready"') :]
    assert "/factory/" in flush
    assert "keyBuffers.clear()" not in js


def test_worklet_honors_osc1_level(tmp_path):
    piano = PIANO.read_text(encoding="utf-8")
    worklet = WORKLET.read_text(encoding="utf-8")
    # the whole patch goes over; the worklet scales by osc1_level as the
    # engine does, with no constant of its own (the parity corpus measures it)
    assert "p: patch" in piano
    assert "sl1 = S.osc1_level" in worklet and "0.3 *" not in worklet


def test_hold_suppress_scoped_to_held_card(tmp_path):
    js = PIANO.read_text(encoding="utf-8")
    assert "holdCard" in js
    assert 'e.target.closest(".sound-card") !== holdCard' in js


def test_vel_lane_paint_markers(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    assert "vel-lane" in js
    assert "vel-bar" in js
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    assert ".vel-lane {" in css
    assert ".vel-lane .vel-bar {" in css


def test_dock_toggle_aria_expanded(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    assert 'querySelectorAll(".dock-toggle")' in js
    assert "aria-expanded" in js


def test_nav_sync_in_island(tmp_path):
    js = ISLAND.read_text(encoding="utf-8")
    assert "dataset.view" in js and "active" in js
    # the nav-sync listener reads the INCOMING #view from the swap's main
    # task, not e.target — htmx 4 dispatches its swap events on the element
    # that carried hx-get (the clicked nav link), never on #view itself —
    # and runs as the swap begins: after the swap, it would lag a
    # page transition (pagemotion.js) by the transition's length
    marker = 'document.addEventListener("htmx:before:swap", (e) => {'
    assert marker in js
    handler = js[js.index(marker) :]
    handler = handler[: handler.index("});")]
    assert 'k.target?.id === "view"' in handler
    assert 'fragment?.querySelector?.("#view")?.dataset.view' in handler
    assert "e.target.matches" not in handler


def test_master_bus_is_limited():
    js = ISLAND.read_text(encoding="utf-8")
    assert "DynamicsCompressorNode" in js
    assert "master.connect(limiter)" in js


def test_sliders_do_not_swallow_live_keys():
    # A focused <input type=range> is not a typing target: QWERTY keys keep
    # playing while a slider is being dragged (both keymaps).
    gate = "range|checkbox|radio|button|submit|color"
    assert gate in PIANO.read_text(encoding="utf-8")
    # perform.js shares piano.js's gate rather than duplicating it
    perform = Path("synthlab/studio/static/perform.js").read_text(encoding="utf-8")
    assert "keysBlocked" in perform


MIDI = Path("synthlab/studio/static/midi.js")


def test_midi_island_is_opt_in_and_within_budget(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/midi.js")
    assert r.status_code == 200
    assert len(_code_lines(MIDI)) <= 120
    js = MIDI.read_text(encoding="utf-8")
    # requestMIDIAccess must only ever run from the click handler: no
    # listeners, no access request, no audio-path change unless used.
    assert js.count("requestMIDIAccess(") == 1
    assert (
        js.index('addEventListener("click"') < js.index("requestMIDIAccess(")
        or "async function enable" in js
    )
    assert "DOMContentLoaded" in js and "onmidimessage" in js
    assert "window.synthlab.piano" in PIANO.read_text(encoding="utf-8")
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    assert 'id="midi-btn"' in html


def test_chords_island_within_budget(tmp_path):
    p = Path("synthlab/studio/static/chords.js")
    assert (
        TestClient(create_app(data_dir=tmp_path)).get("/static/chords.js").status_code
        == 200
    )
    # 150 -> 270: note/harmonic pads, chord library, four-row key map
    # 270 -> 292: per-row range offset + collapsible pad blocks
    # 292 -> 320: pad-list tiling with octave wrap + the - / = octave pair
    # 320 -> 330: a row set to a library category loads the chord poster
    # 330 -> 340: "each key plays" (one choice, two selects) and one typed
    # letter per on-screen key
    # 340 -> 345: strum is every chord's (the pads' too), kept per browser
    # 345 -> 355: Play tools a popover reopened across pages, and a pad
    # block's default fold (chords in key starts open)
    # 355 -> 360: "each key plays" as one row of segments, synthlab.plays
    # and the chord-on / chord-off events (keysbar.js shows them)
    # 360 -> 370: "all rows" (every typing row at once), a chord
    # row's letter on its root key, a chord's first note in the press
    # 370 -> 390: "chords in key" follow the loop's chords (the
    # chord under the playhead, its pad lit), the bridge passes the arp's `at`
    assert len(_code_lines(p)) <= 390
    js = p.read_text(encoding="utf-8")
    assert "stopImmediatePropagation" in js and "chordAt" in js
    assert "midiOf" in PIANO.read_text(encoding="utf-8")


def test_shared_key_gate_and_keys_toggle(tmp_path):
    """One typing/shortcut gate lives in piano.js; chords.js and perform.js
    consume it instead of carrying their own drifting regex copies."""
    piano = PIANO.read_text(encoding="utf-8")
    assert "window.synthlab.keysBlocked = keysBlocked" in piano
    assert "isContentEditable" in piano
    # (chords.js through piano.js's whole typing gate, which holds keysBlocked)
    assert "keysOn && (!keysBlocked(e.target)" in piano
    for island, gate in (("chords.js", "typing("), ("perform.js", "keysBlocked(")):
        js = Path("synthlab/studio/static", island).read_text(encoding="utf-8")
        assert f"window.synthlab.{gate}" in js, island
        assert "select|textarea" not in js, island
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    assert 'id="keys-toggle"' in html and 'aria-pressed="true"' in html


def test_keyboard_paths_for_pointer_surfaces(tmp_path):
    piano = PIANO.read_text(encoding="utf-8")
    assert 'e.target.id === "xypad"' in piano  # XY pad arrow keys
    assert 'closest?.(".pkey")' in piano  # Enter/Space plays a focused key
    audio = ISLAND.read_text(encoding="utf-8")
    assert 'closest?.(".vel-lane")' in audio and "aria-valuenow" in audio
    perform = Path("synthlab/studio/static/perform.js").read_text(encoding="utf-8")
    assert 'e.target.id === "drumxy"' in perform
    client = TestClient(create_app(data_dir=tmp_path))
    assert 'id="xypad" tabindex="0" role="application"' in client.get("/").text
    # velocity lanes ride with the note grid, which Perform now owns
    assert (
        'class="vel-lane" data-step="0" role="slider" tabindex="0"'
        in client.get("/view/perform").text
    )


def test_focus_survives_body_swaps_by_morphing_not_by_restoring():
    """Full-body swaps morph, so the focused element is patched in place and
    focus is never lost. Measured: innerHTML destroys the node (focus falls
    to <body> and had to be put back by hand), innerMorph keeps node identity
    and focus with it, and is ~39% faster on a 2500-node page.

    The save/restore pair this replaced is gone — keeping it would be dead
    code that also fights morph by moving focus after it already survived.
    """
    js = ISLAND.read_text(encoding="utf-8")
    code = "\n".join(
        ln for ln in js.splitlines() if not ln.strip().startswith(("//", "*", "/*"))
    )
    assert "lastFocus" not in code, "the focus restore should be gone"
    assert "htmx:before:request" not in code or "activeElement" not in code

    # every full-body swap must actually morph, or focus really is destroyed
    templates = Path("synthlab/studio/templates")
    stale = []
    for path in templates.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'hx-target="body"\s+hx-swap="(\w+)"', text):
            if m.group(1) != "innerMorph":
                stale.append(f"{path.name}: {m.group(1)}")
    assert not stale, "body swaps that do not morph: " + ", ".join(stale)


def test_save_dialog_still_moves_focus_deliberately():
    """The one focus move that is not a restore: saving closes the popover
    and hands focus back to its opener. Morph must not take that away."""
    js = ISLAND.read_text(encoding="utf-8")
    assert "#save-dialog" in js and "hidePopover" in js
    assert "querySelector('[popovertarget=\"save-dialog\"]')?.focus()" in js


def test_audio_context_interruption_stops_transport():
    js = ISLAND.read_text(encoding="utf-8")
    assert "ctx.onstatechange" in js
    space = SPACE.read_text(encoding="utf-8")
    assert "window.synthlab.ensureCtx()" in space


def test_live_voice_is_band_limited_and_never_steals_a_held_voice():
    """The live worklet must not alias against the rendered PolyBLEP engine
    (measured 2.6% -> 0.11% non-harmonic energy at the top of the keyboard),
    and retriggering a key must let the old voice finish its release rather
    than overwrite it — but only if it was released, or it would hang."""
    wl = Path("synthlab/studio/static/voice-worklet.js").read_text(encoding="utf-8")
    assert "blep(t, dt)" in wl
    assert "2 * t - 1 - this.blep(t, dt)" in wl  # saw
    assert "this.blep(u - Math.floor(u), dt)" in wl  # square (any PWM width)
    assert "old?.rel" in wl and 'this.voices.set(m.id + "#" + this.steal++' in wl
    # and a big chord must not clip: the worklet shares the key bus (its
    # polyphony gain and limiter) with the WAV keys
    assert "window.synthlab.live.start(" in PIANO.read_text(encoding="utf-8")
    assert "S.keyBus()" in LIVEFX.read_text(encoding="utf-8")
    assert "limiter," in KEYS.read_text(encoding="utf-8")


def test_key_warm_is_debounced_and_worklet_aware():
    js = PIANO.read_text(encoding="utf-8")
    keys = KEYS.read_text(encoding="utf-8")
    assert "warmTimer = setTimeout(() => warm(jobs), 300)" in keys
    assert "token === warmToken" in keys
    # no key WAVs are fetched for a patch the worklet plays — only its
    # pre-rendered lanes'
    assert "holdMode, !workletCanPlay(patch))" in js
    assert '...(needWav ? [[m, holdOf(patch, holdMode), ""]] : [])' in keys


def test_hold_sustains_wav_fallback_voices():
    """Hold must work on every sound: worklet voices gate naturally, WAV-
    fallback notes loop their sustain segment until releaseKey fades them."""
    audio = KEYS.read_text(encoding="utf-8")
    assert "src.loop = true; src.loopStart = loop[0];" in audio
    # the seam is crossfaded server-side (holdloop.py, pinned by
    # test_hold_seams.py) and the points arrive as X-Loop; they must land on
    # the decoded buffer's sample grid, or the resampled seam ticks
    assert 'res.headers.get("X-Loop")' in audio
    assert "Math.round(t * buffer.sampleRate) / buffer.sampleRate" in audio
    # released along the engine's release curve (test_key_release.py)
    assert "function releaseKey" in audio and "setValueCurveAtTime(" in audio
    piano = PIANO.read_text(encoding="utf-8")
    assert "wavHandles" in piano and "window.synthlab.releaseKey(h" in piano
    assert 'id="hold-toggle"' in Path(
        "synthlab/studio/templates/fragments/piano.html"
    ).read_text(encoding="utf-8")


def test_queued_loops_land_on_the_next_bar_not_the_clip_end():
    """A 4-bar beat must not make you wait a whole clip to switch away:
    launch quantise is one bar, derived from the page's bpm."""
    js = ISLAND.read_text(encoding="utf-8")
    assert "function nextBar" in js
    bar = js[js.index("function nextBar") : js.index("let swapToken")]
    # one bar, not the clip's length — of the loop's meter (S.barBeats, off
    # the dock's chord bar: 4 for 4/4, 3 for 3/4; it was 4 always)
    assert "barBeats() * (60 / bpm)" in bar
    assert 'getElementById("chord-bar")?.dataset.barBeats) || 4' in js
    assert "Math.ceil((now + lead - s0) / grid) * grid" in bar
    # (Perform's launcher may name its own point: slots.js `launchAt`, else the bar)
    assert "(queue && (S().launchAt?.() ?? nextBar()))" in js
    # one bar-line helper: keyrec.js's take lands on it, and every Loops
    # list's ▶ (loopspanel.js's, not keyrec.js's)
    keyrec = Path("synthlab/studio/static/keyrec.js").read_text(encoding="utf-8")
    assert keyrec.count("S.nextBar(") == 1 and "S.loopStart" not in keyrec
    panel = Path("synthlab/studio/static/loopspanel.js").read_text(encoding="utf-8")
    assert panel.count("S.nextBar(") == 1 and "S.loopStart" not in panel
    assert "nextBar," in js[js.index("Object.assign(window.synthlab, {") :]
    perform = Path("synthlab/studio/static/perform.js").read_text(encoding="utf-8")
    # (no Drums bank, so no queued slot of it: the page is Kit)
    assert "bankQueued" not in perform


def test_slider_output_chips_update_while_dragging(tmp_path):
    """A server-rendered <output> must not read the old value for the whole
    drag — the drum transport never re-renders, so it would lie forever."""
    js = ISLAND.read_text(encoding="utf-8")
    assert "function syncOutput" in js and "syncOutput(t);" in js
    assert 'hasAttribute("data-pct")' in js
    html = TestClient(create_app(data_dir=tmp_path)).get("/view/drums").text
    assert html.count("<output data-pct>") == 2  # swing + humanise


def test_play_button_resyncs_after_a_view_swap():
    """Every view ships its own #play and the server always renders it in the
    stopped state, so switching views mid-loop used to leave a playing
    transport showing "▶" — the next press tried to start what was already
    running. audio.js already resynced #audio-led in its htmx:after:swap
    handler for exactly this reason; the play button has to ride along."""
    text = ISLAND.read_text(encoding="utf-8")
    bodies = re.findall(
        r'addEventListener\("htmx:after:swap",\s*\(\)\s*=>\s*\{(.*?)\n  \}\);',
        text,
        re.S,
    )
    assert bodies, "no htmx:after:swap handler found in audio.js"
    assert any("setPlayButton(playing)" in b for b in bodies), (
        "audio.js must call setPlayButton(playing) after an htmx swap, or the "
        "transport lies about whether the loop is running"
    )


def test_step_click_is_not_retargeted_by_pointer_capture():
    """Capturing the pointer on pointerdown would retarget pointerup to
    the .grid, so a mouse click would fire `click` on the grid and the step
    cell never toggle. Capture starts only once a drag leaves the origin
    cell (Playwright evidence in the task-15 report)."""
    audio = ISLAND.read_text(encoding="utf-8")
    m = re.search(
        r'addEventListener\("pointerdown", \(e\) => \{\s*const cell = '
        r'e\.target\.closest\("\.cell"\);(.*?)\n  \}\);',
        audio,
        re.S,
    )
    assert m, "cell pointerdown handler not found"
    assert "setPointerCapture" not in m.group(1)
    move = audio[m.end() : audio.index("function endPaint")]
    assert "setPointerCapture" in move  # captured once the drag leaves


def test_jam_recording_is_visible_and_never_prompts():
    """The jam button shows its recording state, and it never stops into
    a window.prompt whose Cancel would discard the take."""
    audio = ISLAND.read_text(encoding="utf-8")
    jam = audio[
        audio.index("// ---- jam capture") : audio.index("// ---- performance FX")
    ]
    assert "prompt(" not in jam
    assert "jam-${" in jam  # the default, timestamped name
    assert "REC " in jam and "bar ${" in jam  # the elapsed · bar n/N label
    # IMPORTANT #2: a jam encoded with maxSeconds=Infinity could exceed
    # BodySizeLimit's /clip/upload cap (~131s of 48kHz stereo) with no
    # client-side copy kept, silently losing the take. It is now capped at
    # limits.MAX_CLIP_SECONDS, read off the button's own data attribute.
    assert "Infinity" not in jam
    assert "dataset.maxClipSeconds" in jam
    # recording is the transport's ⏺: its red pilot lamp lit
    # and pulsing while it records, still under calm motion
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    perform = Path("synthlab/studio/templates/views/perform.html").read_text(
        encoding="utf-8"
    )
    assert 'id="jam-rec" class="btn btn-s btn-rec"' in perform
    assert '.btn.btn-rec[aria-pressed="true"]::before { animation: rec-pulse' in css
    assert (
        'html[data-motion="calm"] .btn.btn-rec[aria-pressed="true"]::before'
        " { animation: none; }" in css
    )
    voice = Path("synthlab/studio/static/voice.js").read_text(encoding="utf-8")
    assert "window.synthlab.wavEncode" in voice  # one encoder, in the codec island


SONG = Path("synthlab/studio/static/song.js")


def test_song_island_is_served_budgeted_and_loads_after_the_key_voice(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/static/song.js").status_code == 200
    # +cached playhead geometry (310); +swapNow, the sound dock's swap (328)
    # +want: a stale fetch's failure is not said (329); +the transport's
    # claim, wait and held ⏸, and ■ back to where ▶ started (340); a ramped
    # section's bars by its own bar lines (352)
    assert len(_code_lines(SONG)) <= 352
    js = SONG.read_text(encoding="utf-8")
    assert "Budget: <= 352 code lines" in js
    # it reads audio.js's context and master bus, and piano.js's key gate
    html = client.get("/").text
    order = [html.index(f"/static/{n}.js") for n in ("audio", "keys", "piano", "song")]
    assert order == sorted(order)


def test_song_island_merges_onto_synthlab_and_never_replaces_it():
    js = SONG.read_text(encoding="utf-8")
    assert js.count("window.synthlab =") == 0
    assert "window.synthlab ??= {}" in js and "Object.assign(S, {" in js
    assert "songSchedule" in js
    # no eval / Function / inline handlers: the CSP is script-src 'self'
    assert "eval(" not in js and "new Function" not in js and "innerHTML" not in js


def test_song_player_rides_the_master_bus_and_stops_the_view_preview():
    js = SONG.read_text(encoding="utf-8")
    # through audio.js's master gain -> limiter -> analyser, like previews
    assert "S.getMaster()" in js and "ctx.destination" not in js
    # the view's own loop preview stops first: through the transport, which
    # stops every other player (audio.js's loop claims there with its stop)
    assert 'S.transport?.take("song"' in js
    assert 'transport?.claim("loop"' in ISLAND.read_text(encoding="utf-8")
    audio = ISLAND.read_text(encoding="utf-8")
    exports = audio[audio.index("Object.assign(window.synthlab, {") :]
    assert "stop," in exports
    # the gain is baked into every WAV: never applied again
    assert "plan.gain" not in js


def test_song_player_refetches_the_plan_on_every_song_changed():
    js = SONG.read_text(encoding="utf-8")
    handler = js[js.index('"song-changed"') :]
    handler = handler[: handler.index("});")]
    assert "refresh(" in handler
    # a best-effort hint: never trusts the listed ids alone
    assert "sections" not in handler


def test_song_playhead_reads_the_audio_clock_not_wall_time():
    js = SONG.read_text(encoding="utf-8")
    # heard time: the context clock less the output latency (audio.js)
    assert "requestAnimationFrame" in js and "S.heardTime()" in js
    assert "performance.now" not in js and "Date.now" not in js
    # the live region speaks on a section change, never per beat
    assert '"aria-live"' in js and '"polite"' in js


def test_song_arranger_keyboard_contract():
    """One tab stop with a roving aria-activedescendant; the shortcuts go
    through piano.js's shared gate, and D (piano.js's E key) is claimed in
    window capture only while the arranger grid holds focus."""
    js = SONG.read_text(encoding="utf-8")
    assert "aria-activedescendant" in js
    assert "S.keysBlocked(" in js
    assert (
        'window.addEventListener("keydown"' in js and "stopImmediatePropagation" in js
    )
    for key in ('"Enter"', '"Delete"', '"Escape"', '"ArrowLeft"', '"ArrowDown"'):
        assert key in js, key
    # every arranger action posts through htmx so the OOB regions land
    assert js.count("htmx.ajax(") >= 1 and "/song/cell" in js and "/song/drawer" in js
    # drags onto a lane moved out to songdrag.js (the pool's with them)
    assert "dragstart" not in js


SONGCELL = Path("synthlab/studio/static/songcell.js")


def test_songcell_island_is_served_budgeted_and_loads_after_song_js(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/static/songcell.js").status_code == 200
    js = SONGCELL.read_text(encoding="utf-8")
    # 100 -> 110: a section's menu that stays open
    # across its own ± posts, and the line that lets a range's keys
    # (songsel.js) go before the cell's
    assert len(_code_lines(SONGCELL)) <= 110
    assert "Budget: <= 110 code lines" in js
    html = client.get("/").text
    # after song.js: its observer runs first, so this one's focus restore
    # lands before song.js's queued sync looks for a lost focus
    assert html.index("/static/song.js") < html.index("/static/songcell.js")


def test_songcell_keyboard_and_swap_contract():
    """N / Ctrl+C / Ctrl+V act only on the focused arranger grid, in window
    capture (N must not reach a piano key, Ctrl+C not the browser's copy),
    and post through htmx so the OOB regions land. Every edit is a route:
    the island holds no song state. Focus and scroll are put back when the
    drawer or the arranger is redrawn."""
    js = SONGCELL.read_text(encoding="utf-8")
    handler = js[js.index('window.addEventListener("keydown"') :]
    handler = handler[: handler.index("}, true);")]
    assert "e.target !== g" in handler and "stopImmediatePropagation" in handler
    for key in ('"n"', '"c"', '"v"'):
        assert key in handler, key
    assert "/song/cell/new" in js and '"/song/cell"' in js
    assert js.count("htmx.ajax(") == 1 and "fetch(" not in js
    assert "localStorage" not in js
    # the regions arrive OOB: watched in the DOM, as song.js does
    assert "MutationObserver" in js and '"song-drawer"' in js
    assert "scrollLeft" in js and "preventScroll" in js
    # smooth scrolling is motion too: calm (the Motion setting) scrolls at once
    assert 'window.synthlab.calm() ? "auto"' in js
    # htmx's settle resets an id'd element's class to the response's, which
    # dropped song.js's cursor mark: the next Delete then cleared the
    # top-left cell (browser-found). Re-marked from aria-activedescendant,
    # which lives on the table (no id, so the settle leaves it alone)
    settle = js[js.index('"htmx:after:settle"') :].split("});")[0]
    assert "aria-activedescendant" in settle and '"cursor"' in settle
    assert '"open"' in settle and '"selected"' in settle
    # the shared-pattern highlight never follows a touch (no hover there)
    assert 'pointerType !== "touch"' in js


SONGEDIT = Path("synthlab/studio/static/songedit.js")


def test_songedit_island_is_served_budgeted_and_loads_after_song_js(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/static/songedit.js").status_code == 200
    js = SONGEDIT.read_text(encoding="utf-8")
    # 110 -> 116: a sound swap (`now`) crossfades the solo
    # where it stands instead of waiting for the loop's next wrap
    assert len(_code_lines(SONGEDIT)) <= 116
    assert "Budget: <= 116 code lines" in js
    html = client.get("/").text
    # after song.js and songcell.js: its refocus of the arranger runs once
    # song.js's queued sync has made the redrawn grid a tab stop
    assert html.index("/static/songcell.js") < html.index("/static/songedit.js")


def test_songedit_undo_keys_and_solo_contract():
    """Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y click the topbar's buttons (the posts
    are the buttons' own htmx: nothing else on the page rides along), in
    window capture so the key is neither the patch undo nor the browser's.
    The solo audition holds no song state: it loops a route's WAV on the
    master bus and follows `song-changed`."""
    js = SONGEDIT.read_text(encoding="utf-8")
    handler = js[js.index('window.addEventListener("keydown"') :]
    handler = handler[: handler.index("}, true);")]
    assert "stopImmediatePropagation" in handler and "preventDefault" in handler
    assert "b.click()" in handler and "onSong()" in handler
    assert '"song-undo"' in js and '"song-redo"' in js
    # every edit is a route, and no post is made by hand
    assert "htmx.ajax(" not in js and "localStorage" not in js
    assert js.count("fetchBuffer(") == 1 and "fetch(" not in js.replace(
        "fetchBuffer(", ""
    )
    # a text field keeps its own undo; a pointer held down or a drag blocks the key
    assert "isTypingTarget" in js and '"pointerdown"' in js and '"dragstart"' in js
    # the topbar morphs (the pressed button keeps focus): its transport
    # marks are put back from the player after the settle
    settle = js[js.index('"htmx:after:settle"') :].split("});")[0]
    assert '"song-play"' in settle and '"song-loop"' in settle
    # solo: through the master bus, stopped when the song starts or the
    # drawer moves on, re-cued by an edit
    assert "getMaster()" in js and "data-cell-solo" in js
    # its playhead counts a trimmed block's pattern from the block's start
    # and hides outside it (the drawer's data-at / data-len)
    assert "d.dataset.at" in js and "d.dataset.len" in js and "ph.hidden" in js
    assert '"song-changed"' in js and "songClock?.().playing" in js


SONGLINE = Path("synthlab/studio/static/songline.js")


def test_songline_island_is_served_budgeted_and_loads_after_song_js(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    assert client.get("/static/songline.js").status_code == 200
    js = SONGLINE.read_text(encoding="utf-8")
    # 150 (the first estimate) -> 230: the section's edge, a stem's two,
    # a block's two and their keys
    # 230 -> 300: the section jump, the range brace and
    # its heads, the chord tiles' keys and edge, the two click-to-open
    # menus, the audition, a marker that keeps its bar across a split —
    # the time axis is this file, and songsel.js (120, the range of cells)
    # has no room for them
    # 300 -> 306: the follow tells a scroll from a click, and looks again
    # 306 -> 312: stopped, the ledger reads the marker; fit fills the lanes
    assert len(_code_lines(SONGLINE)) <= 326
    assert "Budget: <= 326 code lines" in js
    html = client.get("/").text
    # after song.js: it reads the player's clock (songClock) and the
    # playhead song.js draws
    assert html.index("/static/song.js") < html.index("/static/songline.js")


def test_songline_contract():
    """The playlist's time axis: zoom is a CSS variable on .song-view (which
    no region swap replaces), kept per browser; the start marker is a place
    on the ruler, never song state; following the playhead costs no
    per-frame work. It merges onto window.synthlab and posts nothing by
    hand."""
    js = SONGLINE.read_text(encoding="utf-8")
    assert js.count("window.synthlab =") == 0 and "window.synthlab ??= {}" in js
    assert "Object.assign(S, { songLine:" in js and "S.songFrom = " in js
    # (`data-bar-w`, never `data-zoom`: zoom.js takes every `[data-zoom]` as a
    # scroller of its own, and wrote an unused `--zoom-24` for the whole view)
    assert '"--bar-w"' in js and "dataset.barW" in js and "dataset.zoom =" not in js
    assert "localStorage" in js and "try {" in js  # a blocked store is no error
    assert "IntersectionObserver" in js and "requestAnimationFrame" not in js
    # the zoom GESTURES are zoom.js's (Ctrl+wheel about the pointer, a
    # pinch, Ctrl+= / − / 0 — the piano roll's own hands): #arr-scroll is a
    # `[data-zoom-own]` scroller and takes them as `zoom-step`. Ctrl+wheel
    # is the page zoom unless the listener may cancel it
    assert '"zoom-step"' in js and 'addEventListener("wheel"' not in js
    zoom = Path("synthlab/studio/static/zoom.js").read_text(encoding="utf-8")
    wheel = zoom[zoom.index('addEventListener("wheel"') :].split("});")[0]
    assert "e.ctrlKey" in wheel and "preventDefault" in wheel
    assert "passive: false" in zoom and "data-zoom-own" in zoom
    assert 'new CustomEvent("zoom-step"' in zoom
    arranger = Path("synthlab/studio/templates/fragments/song_arranger.html").read_text(
        encoding="utf-8"
    )
    assert 'id="arr-scroll" data-zoom="song" data-zoom-own' in arranger
    # the ruler is a slider: arrows, Home, Enter
    for key in ("ArrowLeft", "ArrowRight", "Home", '"Enter"'):
        assert key in js, key
    assert "eval(" not in js and "innerHTML" not in js and "fetch(" not in js
    # an edge drag moves CSS only and posts once, on release, through htmx
    # (so the regions land); the pointer is captured on the first move, so a
    # plain click on a handle is still a click
    assert js.count("S.post(") == 1  # htmx.ajax through audio.js's synthlab.post
    move = js[js.index('addEventListener("pointermove"') :].split("});")[0]
    assert "setPointerCapture" in move
    down = js[js.index('addEventListener("pointerdown"') :].split("});")[0]
    assert "setPointerCapture" not in down and "preventDefault" in down
    assert '"pointercancel"' in js and '"Escape"' in js
    # three kinds of edge, one table: a section's, a whole stem's, a block's
    for selector in ('".sec-resize"(h)', '".long-edge"(h)', '".blk-edge"(h)'):
        assert selector in js, selector
    assert '"/song/cell/span"' in js and "to_section" in js  # with spill
    assert '"dblclick"' in js  # an edge double-clicked: fill the section
    # the block's keys, on the focused grid only, in window capture: Alt+←
    # is the browser's Back
    keys = js[js.rindex('window.addEventListener("keydown"') :].split("}, true);")[0]
    assert "e.altKey" in keys and "e.target === g" in keys
    assert "preventDefault" in keys and "stopImmediatePropagation" in keys
    assert "e.shiftKey" in keys and ".arr-cell.cursor.filled" in keys
    # every post leaves from an element with no field under it
    assert "S.post(path, values)" in js  # synthlab.post: from the empty #post-from
    assert "`/song/section/${encodeURIComponent(sid)}/${to[0]}`" in js
    # song.js starts from the marker
    song = SONG.read_text(encoding="utf-8")
    assert "S.songFrom?.()" in song and "barSamples(s0, bars)" in song
    assert (
        "bars * s.cycle_samples / s.cycle_bars" in song
    )  # a pass is even bars, but a ramp


def test_chord_bar_blocks_survive_its_region_morph():
    """#chord-bar is morphed on every root/scale change. The morph used to
    strip `open` off every block (Play tools, notes in key, ...) and the
    resync hung off htmx:after:swap, which fires on the posting element —
    detached by that same swap. Browser-verified; pinned here."""
    js = Path("synthlab/studio/static/chords.js").read_text(encoding="utf-8")
    guard = js.split('"htmx:before:morph:attr"')[1].split("});")[0]
    assert 'attrName === "open"' in guard and "preventDefault" in guard
    assert '"htmx:after:swap"' not in js.replace("`htmx:after:swap`", "")
    for fn in ("syncMode", "applyAll", "syncBlocks"):
        assert f'addEventListener("htmx:after:settle", {fn})' in js, fn


def test_voice_panel_is_swapped_through_htmx():
    """A bare outerHTML left the new panel unprocessed: "Vocode it" did a
    native GET and "keep as clip" did nothing."""
    js = Path("synthlab/studio/static/voice.js").read_text(encoding="utf-8")
    assert "outerHTML = html" not in js
    # (through synthlab.send, whose answer is htmx.swap'd)
    assert 'send("/voice/take", body, {' in js and 'swap: p ? "outerHTML"' in js
    audio = ISLAND.read_text(encoding="utf-8")
    assert "await htmx.swap({ text, swap, target: target || document.body" in audio


def test_one_send_for_the_posts_islands_build_by_hand():
    """`synthlab.send` (audio.js) is the one hand-built post: the islands
    that post a body htmx cannot build (a take, a file, the coach's mic)
    hand it over, and none fetches, swaps and reads HX-Trigger itself."""
    audio = ISLAND.read_text(encoding="utf-8")
    send = audio[audio.index("async function send(") :]
    send = send[: send.index("\n  }\n")]
    for part in (
        "pageHeaders",
        "htmx.swap(",
        "HX-Trigger",
        "busy",
        "AbortController",
        "toastOf(",
        "unreachable",
    ):
        assert part in send, part
    static = Path("synthlab/studio/static")
    for name in (
        "coach",
        "keyrec",
        "loopspanel",
        "shaper",
        "songrec",
        "songtake",
        "talkbox",
        "vocal",
        "voice",
        "chords",
    ):
        js = "\n".join(_code_lines(static / f"{name}.js"))
        assert "S.send(" in js or "synthlab.send(" in js, name
        assert 'method: "POST"' not in js and "HX-Trigger" not in js, name
    assert "takeSend" not in (static / "keyrec.js").read_text(encoding="utf-8")
