# Keyboard, touch, pitch bend, WAV keys and MIDI

How keys are played and what they play when the worklet is not the
voice: the keyboard / touch contract, typing rows, the pitch wheel,
keys.js's WAV keys (stand-ins, re-warm, held-key loops) and midi.js.
The AudioWorklet voice is in `live-voice.md`. Index: `README.md`.

## Keyboard/touch contract

**Keyboard/touch contract** (pinned by `tests/studio/test_island.py` and
`test_style.py::test_accessibility_pass_css_markers`): one shortcut gate
`window.synthlab.keysBlocked` (piano.js) is shared by chords.js and
perform.js — typing targets and focused buttons/links outside a play
surface never trigger notes; `#keys-toggle` (the dock's QWERTY group)
switches QWERTY play off.
Every pointer surface has a keyboard path (Enter/Space on keys and pads,
arrows on both XY pads and velocity lanes). One global `:focus-visible`
ring — never use `outline` for decoration. Full-body htmx swaps morph
(`innerMorph`), so the focused node survives; nothing restores focus by
hand (no `lastFocus`, pinned by test_island.py), so a swap that
must keep the keyboard's place morphs too (`frontend.md`, the htmx traps). The `(pointer: coarse)` CSS tier owns touch sizes;
the step grid scrolls inside `.transport` (`--step-min`) rather than
widening the cockpit.

A focused range slider is *not* a typing target —
keys keep playing mid-drag (`isTypingTarget`). A button blocks the keys
only when the keyboard reached it (`:focus-visible`): one a mouse
clicked (a sound in the list) keeps them playing. Chrome turns a clicked
button's `:focus-visible` on at the first key typed, so the dock's typing
gate (piano.js `typing`) remembers the button or link a
pointer pressed (`pointed`) and lets the keys play past it — otherwise
the nav link a page change leaves focused would block every key on the
new page; its Space stays its own (`keysBlocked` alone still gates the
transport's).

**The typing gate** (piano.js `typing(e)`, `synthlab.typing`; the dock
on every page): what the dock's typing — piano.js's keys and
Shift, chords.js's rows and chord mode, letters.js, columns.js — plays
through. Typing on (`#keys-toggle`); not `keysBlocked` (but for a
pointer's button, above); not a surface with keys of its own
(`KEY_OWNERS`: a step grid or the piano roll, the playlist, the jam's
grid, a chord tile, the ruler, the Lab's wave, a chop's or a slice's
edge — focused by a click or the keyboard alike); not a page's own plain
key (`#view[data-own-keys]`: KeyL on Song and Mix, where L loops the
section); not while Perform's typing plays its pads (`padsTyping`), nor
while the Song page's finger drums are armed. The arrow bend's
`ARROW_OWNERS` holds the other pages' arrows the same way (the Library's
cards, the playlist, the jam, a chord tile, the Lab's wave and edges, a
splitter, the drum XY, the chain browser, a tab).

**A voice token per note-on** (piano.js `voices`): a source
— a pointer, `kbd:<code>`, `midi:60`, a chord's `<id>:<i>` — sounds one
voice at a time (`active`: source → token), and every note-on mints a
token (`v<n>`) that the worklet, a WAV key's handle and a take's notes
(`S.keyNote`) know the voice by. A voice's **holds** are what keep it
sounding: `key` (its source is down), `pedal`, `latch`; `noteOff` drops
the source's `key` (adding `pedal` if Shift is down), and the voice ends
only when no hold is left (`drop`). Pedal up and latch off drop their
claim on every voice (`dropAll`); a lost focus ends them all. A key's
light is painted from the voices on it (`paint`: `.down` while one
sounds, `.held` while one sounds with its key up, `.latched` while the
latch holds one). A token is also the press for the WAV keys (no
per-press fetch token of their own). A pointer sliding onto the next key still glides its worklet
voice there (never a latched one: that note is let go and a new one
starts).

**Shift is the sustain pedal** (piano.js): held, a released
note keeps sounding — every source ends in `noteOff` (a typing key, a
row's chord, an on-screen key, MIDI, hold-off's short gate), which gives
its voice the `pedal` hold and marks its key `.held` (lit a shade softer;
`html[data-pedal]` lights the keyboard's edge) instead of ending it.
Shift up lets them all go; a note the pedal alone holds, struck again,
starts over (as under a real pedal: the same pitch, from any source); a
lost focus lets the pedal up. Never from a typing
target (Shift is a capital there), while `#keys-toggle` is off, or in the
letters layout (there Left Shift is "major": "Letters" below); MIDI's
own CC 64 pedal (midi.js) is separate and both may hold a note.
`synthlab.pedal()` reads it (`tests/browser/test_live_keys.py`).

**Right Shift is the latch** (piano.js `latchTap`): a tap
arms it (a toast says so; `html[data-latch="armed"]` lights the keyboard's
edge in the signal colour); the next note played — and every note struck
within 250 ms of it, a chord or a strum — gets the `latch` hold and
sounds after it is let go (`.latched`), a drone to play over. Its key
struck again is a new voice of its own beside it (the drone is never
bent or restarted). A tap again lets it go, or disarms it with nothing
played. Left Shift's pedal never releases it; a lost focus does, and so
do **■ / Shift+Space** (transport.js `stopAll` calls piano.js
`synthlab.keysOff`: every key's note off, latched or not) and **another
sound** (piano.js `loadPatch`: the patch's `name` changed — a tweak keeps
the drone), so a latched note never keeps sounding an old sound under
the new one. `synthlab.latch()` reads it (`notes`: the voices it holds).

## The keyboard dock

**On every page**: the key layout and the sound carry through to every
page where synths are heard. The dock
(`fragments/keys_dock.html`) is the page frame's: base.html draws it once,
after `#view` and outside it, like the top dock and `#vox-pop`, so a view
swap (htmx swaps `#view`) never redraws it — a held note keeps sounding
until its keyup, and the layout (rows / letters / columns), Play, strum,
octave, hold / latch, the typing rows, Rec, the talkbox and the Monitor
are the same on every page. It plays the sound Synths edits: the live
voice reads `/patch.json?view=<page>` (the page's ▶ set: `live-voice.md`),
and an answer that differs from the last only by its page name is left
alone (piano.js `patchText`: no push, no re-warm on a view swap). It
sticks to the window's foot; its height is `--keys-h` on `<html>`
(keysbar.js measures it with a ResizeObserver: wraps, the fold, a Takes
strip), which every window-high view and `.cockpit`'s min-height leave
it (`frontend.md`, "The page frame"). On Synths it stands where its grid
area was: `#keys-slot` holds Synths' `keys` area, and keysbar.js sets the
dock's `--keys-l` / `--keys-r` to the slot's edges (the strip's column to
the cockpit's edge, following the rack's splitter); elsewhere it is the
window's width. In a frame (the Song page's sound dock) it is drawn
`display: none` — the Song page round the frame has its own, which reloads
its patch on the frame's `song-changed` — and typing in the frame still
plays the frame's. Its ids are the page's own: Perform's master knob and
the Mix view's fader became `#pf-volume` / `#mix-volume` with
`data-volume` (audio.js keeps every one in step with the Monitor's
`#volume`), Perform has no `#keys-toggle` of its own (the dock's QWERTY
group is there), and the Loops strip and Perform's Loops panel are two
regions (`key-takes`, every page; `loops-panel`). **Not a workspace
module**: workspace.js lays out `#view` alone, so no preset holds the
dock; its own fold (per page) is what it has. **Perform**: typing → keys | pads (the QWERTY group, below).
`tests/browser/test_keys_everywhere.py` holds it on Drums, Perform,
Song, Mix, the Library and the Lab.

**The dock is laid out by named areas** (style.css 7b; so that keys vs chords,
what is playing and how to record live are all clear at a glance): `bend` (the strip, ±range, ⏺ bend,
↑↓ bend) | `now` (what a key plays, `.kbd-now`) | `keys` | `monitor`
(Room, Volume), and ONE toolbar row under them all, `tools`
(`.kbd-bar`). The container is the dock (`container: keys`): under 940px
of dock the keys take a row of their own above bend | now | monitor;
phones (≤ 900px) have it static, not sticky.

**Its height** (kept small, so more of the page is on screen): the
"⌨ keyboard" head stands at the toolbar's left end (absolute, `--kbd-head-w` of
padding: from 901 px; folded it is still the one row), `bend` puts its
buttons in two rows beside the strip, the Monitor's sliders and the
strum's are their thumb's height, a segmented control in the toolbar the row's, and **off Synths the keys are a slimmer
row** (`#view:not([data-view="editor"]) ~ .keys-dock .pkey`: 52 / 32 px
comfy, 44 / 27 dense; Synths keeps 72 / 44 and 56 / 34). Measured with
`--keys-h`: 1920 wide 110 comfy, 104 dense; 1440 wide 142 comfy, 134
dense (the toolbar is two rows there: its groups are 1570 px). The touch tier keeps its 96 / 60 px keys.

**The toolbar reads in groups** (what the keyboard plays — notes vs
chords, the layout — clear, in few clicks): five
captioned boxes (`.kbd-grp`), always in this order —

| group | holds |
|---|---|
| **Play** | what each key plays (`#kbd-plays`: note · triad · 7th · 9th · power · oct), keys strum, hold, latch, the keys arp (its switch and ⚙: "The keys arp" below), the octave (− C4 +) |
| **QWERTY** | typing (`#keys-toggle`), on Perform typing → keys \| pads (`#kbd-typeto`), the typing rows' strip (`#kr-strip`, it opens their panel) — in the letters layout the letters' modifiers in its place, in the columns layout what its rows play ("A C · Q maj · Z min · 1 7") |
| **Rec** | ⏺ Rec, where it lands on the Loops page (new · overdub \| replace, while a launcher slot is selected), ⟲ (keys capture, Alt+T), count-in, the take's length — "to ■" (until stopped), 1–8 bars, or **free** (from the first note to ■, no bars: free record, below; an option of the select, so the group is no wider) — and + beat. Its caption is read, not drawn (⏺ Rec names the group, which leaves room for ⟲ — at 1920 comfy Synths has 14 px to spare) |
| **Talk** | 🎙 talk ⚙ (the talkbox), 🎙 vocal (the live vocal chain, `vocal.js`: its panel is every page's `#vox-pop`) |
| **Tools** | Play tools, at the right end |

Each group is a section of its own (`data-menu-section`: its right-click
menu lists its controls' `data-cmd` — `frontend.md`, "A section's own
menu"). A group never wraps inside and no state changes its width (⏺
Rec's clock has a width of its own, the read-out beside the keys a fixed
one), so nothing moves under the hand; a narrow dock (1366) wraps the row
between groups, Rec onward on the second line.
`tests/browser/test_keyboard_dock.py` holds the order, the groups that
never move, and the clicks.

**Each key plays** (`#kbd-plays`, a `.seg` of `[data-plays]` buttons,
chords.js; localStorage `synthlab.plays`): *note*, or one of the server's
`voicings` (a triad, a 7th, a 9th, a power chord, an octave) — every
choice in sight and ONE click, both "note ↔ chord" and "which chord"
(a `Note | Chord` switch with the type beside it would make a type
change from a note two). Chord mode is anything but a note: the
on-screen keys, MIDI, typing rows of piano keys and rows of notes play
the chord in key on the key played (`chordAt`). An older stored
`kbd-mode` "chord" comes back as a triad. ONE control, one place: the
typing rows' panel reads it back (*Keys & MIDI*, `#kr-keys`), it does not
offer it again. **`` ` ``** switches note ↔ the last chord the keys played
(`synthlab.playsChord`; a triad the first time), **Alt+`** steps the chord
type (keysbar.js; declared on `#kbd-plays` as `data-keys` /
`data-key-label`, so the shortcut sheet lists it; never from a text
field — `keysBlocked`; while the dock is folded a toast says what it
became). The deck's seventh page of keys has Note, Triad, 7th, Power
(below, "The strip, the keys"). `synthlab.plays` is the state for
keysbar.js: `get` / `set` / `list`, `chordOf(m)` (the chord's notes,
`name` and `roman`), `chords()`.

**What sounds is named**, on the keys and in the read-out, in every
layout: **Plays** names a single note while it sounds — "D4", by piano.js's
`key-note` event (every voice's start and end, whatever played it: a
typed key in any layout, a key, MIDI, a column's home row) — a chord
still by its name, the last struck winning, and Play's choice once
nothing sounds; a note no drawn key holds (above the keys showing) is
named all the same. **A key held down shows its note** in place of its
typed letter (`#piano .pkey.down::after`, its `aria-label`, which piano.js
keeps to the octave; `.qhint` hidden while down) — in every layout. The
`.down` class the press already sets: no `:has`, no transition — a press
adds one event and one write to the read-out (unmeasured on a device). `tests/browser/test_columns.py` and
`test_letters.py` hold it.

**The keys show what they play** (`static/keysbar.js`, a face: it plays
nothing and keeps nothing). The pointer on a key lights the keys of what
that key plays, faintly (`.pkey.tone`): in chord mode the chord in key
built on it, in note mode the key alone. Beside the keys, **Plays**
(`#kbd-now`, an `<output>`, never announced: it follows the pointer)
names it — "Am7", its degree under it, or "A4" — and at rest shows Play's
choice ("7th"); while a chord sounds (`chord-on` / `chord-off`, events
chords.js sends from `chordOn` / `chordOff`: a key, a typing row, MIDI, a
pad) its name is bright in the accent (`data-state="held"`; a chord no
chord in key matches — a pad of the chart — reads as its notes). In chord
mode each key a chord in key is built on carries its degree over its
typed letter ("vi", `data-deg`, a 10 px label the key's own CSS draws —
no extra row, nothing grows). The octave is Play's (Z / X).

**Keys strum** (`#kbd-strum`, ms between a chord's notes) is every
chord's: the keys' while they play one, and the chord pads' whatever a
key plays. It is kept per browser (`synthlab.strum`,
chords.js), so a view swap does not put it back to 0, and the deck's
**Keys** dial set reaches it ("Control surfaces"). **hold**
(`#hold-toggle`) and **latch** (`#latch-btn`: piano.js `latchTap`, a tap
of Right Shift — its light waits in the accent while armed and holds
once a note is latched) are one click each.

**Typing and the typing rows** (the QWERTY group): **typing**
(`#keys-toggle`) is one click in the toolbar, lit while typing plays.
**Typing → keys | pads** (`#kbd-typeto`): on a page with pads
(`#perform-pads`: Perform; piano.js shows it there and hides it
elsewhere), what typing plays — **keys** (the default, per browser:
`synthlab.typeto`), the dock's layout as on every page, or **pads**,
Perform's own keys: Q W E R / A S D F strike the pads and
C V B N · I O P hold the performance FX (perform.js, `padsTyping`),
while the dock's typing stands down whole (z / x included). One click,
or **Alt+P** (a toast says which; `data-keys` on the segment). While
typing plays the keys the pads' and the FX's letters are dimmed
(`html[data-typeto]`). The pads' clicks, MIDI and the deck are unchanged.
The strip beside it (`#kr-strip`, keysbar.js) says what each row plays —
"1 chords · Q A piano · Z —": rows side by side that play the same (and
the same layer) share a chip, "1 Q A Z chords", and a long name is short,
"harm", "ext", "alt" (so the toolbar is one row at 1920 px, and two
between groups at 1366) — struck through while typing is
off, and with **rows → sounds** on each row's layer ("L2"): a row that
plays another layer than the keys says so on the dock, the panel shut.
The strip is the button of the rows' panel (`#kbd-rows`, a native
`popover` anchored to it — `position-anchor` inside `@supports
(position-area: top)`, opening up, flipping where there is no room;
light-dismiss and Esc are the browser's): **rows → sounds**
(`#rows-toggle`) in its head, the **layout** under it (rows | letters |
columns, `#kbd-layout`, also Alt+L: "Letters" and "Columns" below), the
columns layout's **number row** under that (`#kbd-colnum`, dimmed in
the other two), **all rows** under that (every row in
one click: piano · notes ·
chords · off, `[data-rows-all]` — chords.js sets the four rows at once,
keeps them, lights the one every row plays, and the strip redraws; each
is a palette command, "Typing rows: every row plays …"), then a row each, on one set of columns
(subgrid), for *Keys & MIDI* (Play's choice, read back), *1 – =*, *Q –
]*, *A – '* and *Z – /*: the row's name | what it plays | its layer
(rows → sounds only: rowsounds.js disables the column while that is off,
and turning it on from anywhere shows the panel). The ↑↓ bend settings
are the same kind of anchored popover (`#bend-pop`).

**Play tools** is the chord-bar region (`fragments/chord_bar.html`,
included in the Tools group, `display: contents`): its button is the
toolbar's last, and the tools are a manual popover (`#play-tools.pop`)
over the page, above the dock at its right end, never over the keys
(never a line of its own, so the button never moves) — a key played on the dock leaves it open,
only the button shuts it, and chords.js opens it again on the next page
if it was left open (`synthlab.play-tools`). Its pad blocks remember
their folds; one never opened or shut takes its default (`data-open`:
**chords in key** starts open).

**The clicks** (measured by pointer at 1920 x 1080; a select is two,
open and pick):

| change | clicks |
|---|---|
| note → chord | 1 (or `` ` ``) |
| the chord type (triad → 7th) | 1 (or Alt+`) |
| chord → note | 1 (or `` ` ``) |
| typing off / on | 1 |
| octave | 1 |
| hold | 1 |
| latch | 1 (or Right Shift) |
| what a row plays (Z row → notes) | 3 |
| a row's layer | 4 |
| see what the rows play / their layers | 0: the strip |
| see what a key will play | 0: hover |

**A switch is an action with a state light** (`.btn > .led`, lit while
`aria-pressed`: section 15): typing, rows → sounds, hold, count-in,
🎙 talk, Perform's typing and the XY pad's ▶ drone — never an "on" /
"off" word. ⏺ Rec and ⏺ bend, being
records, go red and pulse instead.

**⏺ Rec** (`#key-rec`, `static/keyrec.js`, Alt+R): record
what you play over the loop as a **take** — the notes and the sound that
played them, the audio its preview — to pick the take you like and keep
it. `keytakes.py` and `routes/takes.py` hold the server half.

- **The keys alone.** It records the key bus — `keys.js`'s `keyTap`, a
  gain after the bus limiter and before the live out, so every key,
  chord, pad of the keyboard, MIDI note and live FX lane, but not the
  room — or, while the talkbox speaks for the keys, its vocoder's output
  (`S.talkbox.out()`). Never the master: the beat is not in a take.
  **+ beat** (`#key-beat`, per browser `synthlab.keyRecBeat`, off) takes
  the master bus instead (the meta says `beat`).
- **On the loop's bars.** With the loop playing (`S.isPlaying`) a press
  arms it and the take starts on the loop's next bar line
  (`S.nextBar`, audio.js's launch quantise, at `S.tempo()`; the clock is
  audio.js's for every take: `frontend.md`, "JS islands") — a whole bar off when
  *count-in* is lit (`#key-count`, `synthlab.keyRecCount`, on): the loop
  is the count-in, and the button counts the beats down. Nothing
  playing: four blips to the speakers, one bar, never in the take; with
  count-in off it starts at once.
- **How long** (`#key-bars`, `synthlab.keyRecBars`): 1, 2, 4 or 8 bars,
  then it stops by itself; or **to ■** (the default) — until pressed
  again (Alt+R, ■ in the top dock, Shift+Space), rounded **up** to the
  bar line; free length when nothing plays. Never past
  `limits.MAX_CLIP_SECONDS` (the bars that fit). Stopped before its first
  bar line, nothing is taken. **A page change ends it as ■ does**: kept, stopped on
  the bar line ahead (free length: at the change), holding **only the
  notes played before the change** (a note begun on the next page, before
  that bar line, is not the take's: keyrec.js `cut`); armed and not yet
  started — its count-in, or waiting for the bar line — it is cancelled
  and nothing is taken. A toast says which, and the take's own toast
  begins "The page changed, so ⏺ Rec stopped".
- **The cut**: from its first downbeat *as heard* — the context's time
  plus the output latency, as samplerec.js cuts a mic take — for exactly
  its length (MediaRecorder's start stamped on the context clock;
  silence pads a short one).
- **The notes**: piano.js calls `S.keyNote(token, midi, velocity)` on every
  note and `S.keyNote(token)` where it ends (a pedal or latch hold included;
  the voice's token, so a held key struck again is a note of its own),
  each stamped in heard time on the take's beats: `[start, length, midi,
  velocity]` from its first downbeat, up to `limits.MAX_TAKE_NOTES`.
- **A bar is the loop's meter's**: `S.barBeats()` beats at
  `S.tempo()`, so a 3/4 take's bars,
  its count-in and its "bar n" are 3/4's.
- **Kept, and a loop** (one Rec is a take that is both its audio and an
  editable loop): `POST /take/keys`
  (multipart: the WAV, the notes, bpm, bars, `keys-<time>`, `beat`) —
  checked whole before anything is written — stores the clip with meta
  `{source: "keys", kept: true, bpm, bars, at, root, scale, meter?,
  sound}` and its notes in a sidecar, `studio_data/takenotes/<clip>.json`
  (`{notes, raw?, fixes?}`: the clip store's `clips.SIDECARS`, which
  `delete_clip` / `rename_clip` carry and whose writes never move the clip
  store's listing), **and makes its loop**, named as the clip
  (`X-Loop-Name`; `routes/takes.py` `_loop_from_take`): the notes exactly
  as played, the sound that played them, the tempo, key and meter, placed
  on a grid of the take's bars (rounded up; at most the grid's 4) at the
  meter's sixteenths — `loop-and-perform.md`, "Loops". **Nothing waits in
  an "unkept" limbo**: a take is saved; ✕ deletes it. (Past
  `limits.MAX_LOOPS` the take is kept without a loop and says so: the
  Loops list shows it as a take, and its first action makes the loop.)
  `kept` is always there: a `keys` clip without it is an older plain
  recording of the dock, no take. `sound` names the whole sound that played it
  (`StudioState.sound_payload`, the format-2 preset
  `save_preset(include_layers=True)` writes) kept by content in
  `studio_data/takesounds/`. REC: red and pulsing, "■ m:ss
  · bar n", the top dock's "● Recording" (a transport layer); its toast
  says "its audio and a loop, in Loops: Edit it on Perform, or → Song".
- **Into the selected slot**: on the Loops page a slot of the launcher can be selected — a
  slot's click (a filled one's only selects too: its ▶,
  a double-click or Enter on it selected launches it; an empty one's ＋
  keeps the lane) — drawn `aria-selected`,
  kept per tab (slots.js, sessionStorage `synthlab.slotSel`). With a slot
  of a lane of notes selected, the Rec group shows **#key-how**: "new"
  for an empty slot, else **overdub | replace** (per browser,
  `synthlab.keyRecHow`, overdub first). The take is kept as any (its
  audio and its own loop) and then lands (`slotrec.py`, the post's `lane`,
  `row`, `how`, `bar`): **new / replace** — its loop in the slot, launched
  there (one undo step with the slot's change; the old loop stays in the
  Loops); **overdub** — the slot's loop launched first when its lane is
  not playing it, then the take's notes folded onto that loop's own bars
  from the bar of the loop the take began on (`S.loopBar`, slots.js) and
  merged by `record.merge`'s rules (a note on a held step rewrites its
  velocity; a repeat on its own pitch is a cut; a held note keeps its
  steps) — one whole-loop undo step, saved to the loop by the open loop's
  mirror. A beat's slot, or none: a plain take (Rec is notes
  only). A take a page change ended lands free. keyrec.js posts with
  `fetch`, so it dispatches the answer's `HX-Trigger` itself (the queued
  launch; the server's toast replaces its own).
- **Free** (free record: play, then shape, cut and morph it to the size
  wanted): the length select's
  **free**, or the Loops panel's **⏺ free** (`#lp-free-rec`, which records
  free whatever the select says; its button shows the take as ⏺ Rec's
  does). No count-in, no bar line, no length, the loop's playing or not:
  the take starts on its **first note** (its downbeat — "⏺ free: play"
  until then) and ends where ■ is pressed (⏺ / ■ / Alt+R / Shift+Space),
  never past `limits.MAX_FREE_TAKE_SECONDS` (60 s; every other take stops at the clip cap, 30 s —
  `data-max-free-seconds` on #key-rec, keyrec.js `cap(r)`) from the
  press; its clip is kept that long (`clips.save_clip(cap=)`, at most
  `limits.MAX_STORED_CLIP_SECONDS`, which the hub and a project bundle
  read back); its audio is cut
  from that first note, its notes in beats at the page's tempo, the keys
  arp's flagged (a fifth item 1). Stopped before a note, or a page change
  before one: nothing taken. It lands in no slot (the shaper's Make loop
  does): `POST /take/keys/free` keeps it as a take with no loop, and its
  answer opens the shaper (`loop-and-perform.md`, "Free record and the
  shaper"). keyrec.js and shaper.js post through `synthlab.send`
  (audio.js; frontend.md, "`synthlab.send`").

**⟲ keys capture** (`#key-capture`, **Alt+T** — Alt+C is the jam's
capture on Song, Alt+Shift switches Windows' input): keeps
what was just played on the keys or MIDI, **without** ⏺ Rec, as a loop.
keyrec.js keeps every note piano.js sounds (`S.keyNote`, its fifth
argument `own` flagging the keys arp's own notes) in a buffer — notes
only, the last 120 s or 1000 notes; the dock is outside `#view`, so a
page change keeps it — in loop time: what the ear was at (the context's
time less the output latency), the arp's notes when they are due. ⟲
takes the last #key-bars bars (1 / 2 / 4 / 8: up to the bar line after
the newest note) or, at "to ■", the **latest phrase** — back to a bar of
silence — from the bar line it began on, rounded up to 1 / 2 / 4 / 8 bars
(at most the last 8); a note up to half a 16th early sits on its line
(`captureWindow`, pure, run under node by `tests/studio/test_key_takes.py`).
Bar lines are the playing loop's (`S.nextBar`); from silence the
phrase's first note is the downbeat. The tempo is the page's (`S.tempo`;
asking for one from silence, as Live does in an empty Set, is not built).
`POST /take/keys/capture` makes a loop with no audio (`take` null), named
from what it is, in the sound playing now — and with a slot selected it
lands there as a new loop when the slot is empty. **A capture never
overwrites**: a filled slot selected, it
goes into the lane's next empty slot (the toast says which scene); no
empty slot in the lane, it stays a loop in the Loops panel and the toast
says nothing was replaced (`slotrec.capture_target`). The dock's overdub |
replace is ⏺ Rec's alone. Not the Loops page's **Scene → Song** in the
launcher's head (the scenes launched → sections). **Print** (`#arp-print`, "⤓ print" in the keys
arp's ⚙ panel) is ⟲ of the arp's own notes only, and lands as ⟲ does.

**The Loops strip** (region `key-takes`, `fragments/key_takes.html`, at
the head of the dock, above the keys — the dock is stuck to the window's
foot, so a strip that appears grows it upward, and under the keys it
lifted the toolbar out from under the hand that pressed ⏺; hidden while
this session has made none): the quick view of **this session's** loops
and takes, newest first, the newest six (`looplib.SESSION_MAX`), and
**All loops ▸** (with "N more") — the clip library's Loops group, the
whole list, on every page. A row is `fragments/loop_rows.html`'s (the one
macro every Loops list draws): **▶** in time (loopspanel.js: from the loop's next bar line when it plays, else at once;
one audition at a time, transport.js's `oneShot`; its take's audio, or a
render `/loop/preview` makes — at the tempo playing when that is not the
loop's: `loop-and-perform.md`, "The Loops panel"), its notes as a mini
piano roll, its name (named from what it is: "Acid bass riff"), its facts as
read-out cells (bars, grid, tempo, key: `loop-and-perform.md`, "A row's
facts"), **Edit** (on Perform's grid as this
loop), **→ Song** ▸ pattern | audio, and **✕** (the loop and its audio).
Quantise, ✦ Clean up, ☆ / ★, # tags and ✎ are the full list's
(Perform's Loops panel, the drawer). The strip draws no ★ Keep, → notes,
→ sample, ⟳ loop or "clear takes" (their routes stay: an older unkept
take still answers them). `tests/studio/test_key_takes.py` holds the
take routes, `tests/studio/test_loops.py` the loop each makes,
`tests/browser/test_key_takes.py` the journeys (a take on the loop is
whole bars; the beat is not in it; one Rec -> Loops -> Edit on Perform ->
an edit saves -> → Song -> the song plays it; ✕; loop the lane),
`tests/browser/test_keyboard_dock.py` the dock's own. Nobody has listened
to a take: the feel of the bar-line start and the latency correction on a
real interface are unchecked.


## Typing rows

**Typing rows** (`chords.js` `ROWS`): the number, Q, A and Z rows are
each mapped (the typing rows' panel, its strip in the dock's QWERTY
group; localStorage `synthlab.rows`) to
one surface — piano (both Q+A = classic split layout owned by piano.js;
otherwise a chromatic run from C), notes, chords (key I–VII), major /
minor / diminished (the 36-pad library), harmonics, or off. Matching is
by physical `e.code`; chords.js claims mapped keys in window capture so
piano.js's map (and Z/X octave) never double-fire. Pads show the driving
key in a `.khint`.

**No key is dead** (`tests/browser/test_typing_rows.py`
presses every key of every row in each surface). A row set to any of the
chord library's nine categories plays the chord poster's pads, which are
fetched on its fold's first open, so chords.js `ensureLib` fetches the poster as soon as a row needs
it (a dispatched `toggle` is not an htmx trigger: it fetches the fold's
own `hx-get`). The classic split (piano.js `KEYMAP`) runs past A..J:
K O L P ; ' carry on into the next octave (C..F, as a DAW's typing
keyboard), labelled on that octave's keys (`piano.html` `qwerty2`).
Q R I [ ] stay silent there — the gaps in a piano's black keys.

**The keyboard folds** (`#keys-dock`, layout.js's `data-fold`,
remembered per browser **and per page**: `data-fold-page`
keys it `synthlab.fold.keys-dock@<view>`, a page never pressed taking the
browser-wide key older folds were saved under): "⌨ keyboard" (open, at the toolbar's left end,
not a row of its own: `style.css` 7b; folded, the dock's one row) hides the
keys, the toolbar and Play tools (`.fold-body`) and stays at the bottom to bring
them back, and beside it **what the keys play** (`#kbd-sum`, keysbar.js,
shown only folded: the chord or note sounding, else Play's choice · the
layout · the octave, "note · columns · C4"); typing still plays while it
is folded. Until it is pressed it
**starts folded on a window under 900 px tall or under a finger**
(`data-fold-auto` on the button: a media query layout.js reads for a fold
nobody has chosen; a press stores "1" or "0" and wins from then on) — at
1366 x 768 the open dock takes 150 px of the panels' 430, on a tablet
the whole first screen (`tests/browser/test_keyboard_dock.py`).

**The on-screen keys are labelled by what a typed key plays**: every
`.pkey` of every octave has a `.qhint`, and `applyRows` writes into the
key whose MIDI note is `rowMidi(row, i)` — the same function the keydown
handler plays a piano row from, and `padOn`'s sum for a row of notes. A
key several typed keys play shows ONE letter, the lowest row's (rows by
sound: each row runs from C), and carries them all in `data-typed` and
its `title` ("typed: Z, A") — a "VK" / "Q1" pile reads as noise;
a chord row names no key (a chord has no one key); a typed key whose
note is above the keys showing is named nowhere. Never work a layout out
a second time for the labels: `tests/browser/test_key_labels.py` presses
every typing key in five layouts and holds "the keys that light are
exactly the visible keys that carry its letter" (in `data-typed`; the
key shows one).

### Stacked typing rows lift by an octave

**Stacked typing rows lift by an octave** (`chords.js` `ROW_ORDER`/
`startOf`): two rows set to the same surface would play identical
notes, so rows rank bottom-to-top (Z, A, Q, 1) and each one above the
first transposes +12 semitones — an octave rather than "the next N pads",
because there are only ~11 note pads and 5-7 chord pads to slice into.
The lift must reach all three trigger paths (`padOn`, `padNotes`, the
chromatic piano row).

## Letters

**The letters layout: the key you press is the note** (`static/letters.js`):
the A key plays an A, and Space, Shift or Ctrl held with it make the
chord. The typing layout is **rows** — everything above, untouched — or **letters**:
the rows' panel's *layout* (`#kbd-layout`, a `.seg`, a `data-cmd` each),
or **Alt+L** from anywhere (a toast says which; `data-keys` on the seg, so
the shortcut sheet lists it); per browser, `synthlab.layout`;
`html[data-keylayout]` carries it for the CSS. In letters:

| held as the letter goes down | plays, on the letter's root |
|---|---|
| nothing | what Play says (`synthlab.plays`): the note, or Play's chord in key on it — as every other source of the dock |
| Left Shift | major triad |
| Space | minor triad |
| Ctrl | dominant 7th |
| Shift + Ctrl | maj7 |
| Space + Ctrl | m7 |
| Shift + Space | sus4 (all three: 7sus4) |
| Tab (with any of the above) | the letter's sharp: C → C♯ …, E♯ is F, B♯ the C above |

- **A B C D E F G play that note** in the dock's octave (C..B: A and B sit
  above C, so A is MIDI 69 in the C4 octave). The chord is built **on that
  root, not in key** — the letter names it ("Space + D" is D minor
  whatever the key). The other keys of the typing rows (H–Z, 8 9 0, the
  punctuation) play nothing; they are silenced, not swallowed (Ctrl / Alt
  with them reach the browser and the islands; Shift+/ still opens the
  sheet). Alt+anything is never the layout's: Chrome keeps Alt+F / Alt+E.
- **1–7 jump the octave** (C1..C7, clamped as − / + are: the top octave
  showing stays under MIDI 127); **- / =** still step it; Z / X are
  silent here.
- **Space** is a modifier while it is held *and a letter goes down in the
  hold*; a tap with no letter in it still plays / stops the page's ▶ —
  on its **keyup** (transport.js asks `synthlab.letters.takesSpace` first,
  and letters.js calls its `space` on the keyup; Shift + a tap is still
  stop-all). A focused thing whose own Space it is (a key, a slider, a
  grid: transport.js's `OWN_SPACE`) keeps it. Nothing changes outside
  letters.
- **Left Shift is "major", not the sustain pedal** (letters.js stops it in
  window capture, so piano.js never hears it; the panel's and the strip's
  titles say so). **Right Shift still latches.**
- **Ctrl+A..G never reach the browser** (select all, bookmark, copy, find,
  …: preventDefault on the keydown — the shortcuts Chrome keeps from a
  page are Ctrl+N / T / W, Ctrl+Tab and their Shift forms, as far as we
  know, none of them a letter A–G;
  the journey proves the default prevented, not Chrome's own UI, which a
  headless browser has none of). **Tab** never moves the focus while
  letters plays: Alt+L, typing off, or a click into a field gives it back.
- **A chord ends on its letter's keyup**, whatever the modifiers did
  meanwhile; two letters overlap as two chords; a lost focus ends them all
  (chords.js's blur: the chords are played through
  `synthlab.plays.chordOn` / `chordOff` — the strum, the keys lit, the
  chord's name beside the keys, "Dm7": a `name` on `chord-on`, which
  keysbar.js shows, with its degree when the key has that chord).
- **The keys show it**: the dock's own octave names its letters
  (`piano.html` `data-letter`: a natural its letter, a sharp ⇥ + the
  letter under it, "⇥F"), drawn in place of the typed rows' labels; the
  QWERTY group's strip button shows the modifiers — "⇧ maj · ␣ min · Ctrl
  7 · ⇥ ♯", the full rule in its `title` — in the rows' chips' place (the
  two faces share one cell, so the group never changes width); in the
  panel what only rows reads is dimmed.
- **Typing off turns it off too**, a focused field never plays, a
  keyboard-focused button outside a play surface never plays, nor a
  surface with keys of its own, a page's own key or Perform's typing →
  pads (piano.js `typing`, "The typing gate" above). The dock is on every
  page, so the layout plays on every page. MIDI, the
  on-screen keys and the pads are unchanged.

**Why the layout is in the panel, not a switch of its own beside
"typing"**: the toolbar has 5 px to spare at 1920 px in the comfy
density — a `rows | letters` segment (103 px) would put Tools on a
second row. Two clicks (the strip, then *letters*) or Alt+L instead of one.
`tests/browser/test_letters.py` holds all of it (the notes off
`synthlab.piano.noteOn`, the key-up orders, the group's width in both
layouts); nobody has listened.

**The switch is three-way**: rows | letters | columns,
Alt+L cycling them in that order (from letters it goes on to columns, a
toast each). letters.js keeps it (`synthlab.letters.get()` is what
columns.js reads); `takesSpace` stays the letters layout's alone.

## Columns

**The columns layout: each keyboard column is a root** (`static/columns.js`):
the home row A … ' plays the notes from C, the row above their majors,
the row below their minors, and the number row a choice. The third typing layout: the rows' panel's
*layout* (*columns*), or Alt+L; `html[data-keylayout="columns"]`. By
`KeyboardEvent.code`, so the physical keys whatever the OS layout, column
by column (Q sits over A, Z under it; the stagger ignored):

| row | keys | plays, on the column's note |
|---|---|---|
| home | A S D F G H J K L ; ' | the note: C D E F G A B C D E F from the dock's C (A is C4 at C4, K the C above), a single note whatever Play says |
| above | Q W E R T Y U I O P [ ] | the major triad (] is G: the column past ') |
| below | Z X C V B N M , . / | the minor triad |
| number | 1 2 3 4 5 6 7 8 9 0 | its choice: **7** (the default), **maj7**, **m7**, **sus4**, **♯** (the note a semitone up, single: the black keys — E♯ is F, B♯ the C above), or **oct** (1–7 set the octave, C1..C7 as letters' do; 8 9 0 nothing) |

- **Chords are root position**, the root in the column's note's own octave
  (Q at C4 is C4 E4 G4). Played through `synthlab.plays.chordOn` /
  `chordOff` with a name ("D", "Dm", "G7"; a single note none): the
  strum, the keys lit, the name beside the keys — as letters' are.
- **The number row's choice** is the panel's `#kbd-colnum` (a `.seg`, a
  `data-cmd` each), or **Alt+N** while columns is on (it steps 7 → maj7 →
  m7 → sus4 → ♯ → oct → 7; a toast says what it plays; elsewhere Alt+N is
  not taken); per browser, `synthlab.colnum`; `html[data-colnum]` carries
  it for the CSS.
- **- / = step the octave** (chords.js's octave pair, as in every layout).
- **Shift is as in rows**: Left Shift the sustain pedal, Right Shift the
  latch (piano.js; columns.js never touches Shift, and a key struck under
  Shift plays — so Shift+/ is the pedal and E minor, not the shortcut
  sheet: the sheet's ? button and the palette still open it, as with a
  playing row in rows). **Space** is the transport's on its keydown, as in
  rows: it is no modifier here, so `takesSpace` stays false.
- **Ctrl / Alt / Meta with any key** are the browser's and the islands'
  (Ctrl+C copies, Ctrl+A selects all): only a plain or shifted key plays.
- **A key's chord ends on its keyup**, whatever happened meanwhile — the
  octave moved, the layout switched (columns.js ends what it started,
  whatever the layout is by then; a key held from rows or letters ends as
  that layout plays it); keys held together sound together; a lost focus
  ends them all (chords.js's blur).
- **The keys show it**: each white key the home-row key that plays it
  (`piano.html` `data-col`: A on the dock's C … ' on the F above), and
  while the number row plays ♯ a black key its digit (`data-colsharp`,
  "1" on C♯ … "9" on the next D♯), drawn in place of the typed rows'
  labels. They are static on the first two octaves (`data-rel` 0 / 1),
  which are always the dock's octave and the next, so they follow - / =.
  The strip's button shows "A C · Q maj · Z min · 1 7" (A is C; the last
  chip the choice, `#kbd-colnum-face`, in a room as wide as "maj7" — a
  choice never moves the toolbar), the rule in its `title`; the faces
  share one cell, the columns' the widest by 2 px (153 px comfy), the
  comfy toolbar's spare at 1920 px about 4 px. In the panel what only
  rows reads is dimmed.
- **Typing off, a focused field, a keyboard-focused button**, a surface
  with keys of its own, a page's own key, Perform's typing → pads
  (piano.js `typing`): nothing — as letters. On every page.
- **Not mapped**: the ISO key right of ' (its code is `Backslash`, the
  same as the US board's key above Enter, so it is left alone) and
  Backquote (keysbar.js's note / chord).

**Why its own island**: letters.js is close to its 90-line budget and
the two layouts share nothing but the switch, which stays in letters.js.
`tests/browser/test_columns.py` holds it (the notes off
`synthlab.piano.noteOn`, the number row's choices, the labels, the key-up
orders and layout switches mid-hold, the browser's own keys, the group's
width in all three layouts); nobody has listened.

## The keys arp

**The keys arp** (`static/arp.js`): hold a chord on the keyboard dock and
it plays as an arpeggio. **Not the loop's arp** — Perform's `arp_mode`
(FEEL, **"grid arp"** / "oct": the note grid's pitches a step each, in
the render, `arrange._arp_sequence`). The two are named apart as the
strums are: the grid's reads "grid arp" on Perform, the dock's is "keys arp" in its switch's
title, its panel and the palette ("Keys arp: on / off"), and its switch
keeps its short "arp ↑ 1/8" — "keys arp" there would wrap the toolbar
to a second row at 1920 comfy (`test_the_toolbar_is_one_row_at_1920`).

- **What it takes**: every note the dock's voice is asked for — typed in
  any layout (rows, letters, columns), a clicked key, MIDI, a row's or a
  pad's chord, Play's chord on a key, the latch's — through piano.js's one
  `noteOn`. A note the arp takes is a **voice that does not sound**
  (`voices`' `arp`): its holds are the same (the key, the pedal, the
  latch), so Left Shift's pedal, the latch, ■ / Shift+Space (`keysOff`), a
  lost focus and another sound let go of it as of any note, and its key is
  ringed in the accent (`.pkey.arped`; the note sounding is lit `.down` as
  any). piano.js asks `synthlab.arp.takes(latching)` and tells
  `changed()`; arp.js reads `piano.arpHeld()` (the held notes in the
  order played). Switched off, what it holds sounds as it is held
  (`arpOff`: a latched chord becomes a drone again).
- **What it plays**: each step's notes through `synthlab.piano.noteOn`
  with an `arp:<n>` source — the same voice, lit keys, `key-note` (the
  read-out), `S.keyNote` (⏺ Rec takes the notes **as they sound**, never
  the chord held), `onNoteOn` (the Song page's take). An `arp:` note gets
  no pedal and no latch hold, never takes the armed latch, and ends on its
  gate, not hold-off's 160 ms. **MIDI out**: the studio has no live MIDI
  out (only the `.mid` export, `song-render-export.md`), so there is
  nothing to send them to — whatever is added at the voice's `noteOn` will
  see them.
- **In time**: the tempo is `S.tempo()`, a step `rate` beats (1/4, 1/8,
  1/8T, 1/16, 1/16T, 1/32). With the loop playing the steps are counted
  from its **latest bar line** (`S.nextBar` of a bar back: a meter or a
  tempo map that moves the bar lines moves them, arp.js reads no clock of
  its own; a bar is `S.barBeats()` beats — the loop's meter, off the dock's
  chord bar, which `S.nextBar` counts by too), re-anchored at every bar — a loop started under a running arp
  takes it over at its next step; the first step is the next one on that
  grid, or — struck up to 30 ms after one — at once. Nothing playing: from
  the press. **Swing** (0-60 % of a step) delays the odd steps, as the
  loop's swing does (`arrange.py`). **Each note is posted 60 ms ahead with
  the context time it is due** (`at`): the worklet holds it in `due` to
  the render quantum nearest that time (half a quantum either side —
  voice-worklet.js `process`; `live-voice.md`), so the grid is the audio
  thread's, not the main thread's timers (Chrome's `currentTime` moves in
  10 ms callbacks here: a note sent "now" lands anywhere in one). Its end
  is due `gate` (10-100 %) of a step on. A WAV key (the fallback) waits
  for its `at` on a timer. keyrec.js stamps a note at its `at`, so a take
  is on the grid too; the light and the read-out move up to 60 ms early.
- **Modes**: up, down, up-down (the top and bottom once a cycle), random
  (a seeded shuffle a cycle — every note once a cycle, the same run every
  time it starts), as played (the order struck), chord (the held notes as
  repeated stabs); **octaves** 1-4 carry the run up (chord: a stab an
  octave higher each step). `synthlab.arpSchedule` is the pure half
  (`cycle`, `pick`, `stepAt`), run under node by
  `tests/studio/test_keys_arp.py`.
- **Latch + arp is a running arpeggio you play over**: Right Shift (or
  latch) then a chord: the arp runs on with the keys up. While it runs on
  latched notes alone, a note played **sounds over it** (it does not
  join); the latch again (Right Shift, latch) lets the arpeggio go, and ■
  / Shift+Space stop it — while it runs it is a transport layer ("Keys
  arp" in the top dock: transport.js's `claim`), so ■ is lit. A new chord
  for it: the latch twice (go, arm), then the chord.
- **What sounds is named**: the read-out says the chord it plays and the
  mode, "C ↑" (a `chord-on` with `id: "arp"`, keysbar.js; one note, its
  name), over the notes it plays one at a time.

**The switch** (`#arp-btn`, Alt+A from anywhere but a field; a toast when
the dock is folded) sits in **Play**, beside hold and latch, a section of
its own for the context menu (`.kbd-arp`, `data-menu-section="Arp"`): a
lamp, "arp" and its face — the mode's glyph and the rate, "↑ 1/8", in a
room as wide as the widest ("⚄ 1/16T"), so a choice never moves the
toolbar. **⚙** (`#arp-pop`, a popover anchored to it, opening up) holds
the mode, rate and octaves (`.seg`s, a command each), gate and swing.
Per browser (`localStorage` `synthlab.arp`); nothing posts.

**Why not a captioned group of its own**: an "Arp" group (caption,
switch, face, ⚙) is 154 px, which the toolbar does not have at 1920 px
comfy on Synths. What makes room for the switch (measured at 1920 x 1080
on Synths and Perform, comfy and compact): Rec's length reads "to ■",
Play's choices take their `.seg > button` padding and size, strum's
slider is 36 px, the groups' gap and left padding 6 / 4 px, and the
octaves are in ⚙ (with them the face would be 10 ch). The toolbar is one row in both
densities, 2 px to spare comfy (`tests/browser/test_keys_arp.py`).

`tests/browser/test_keys_arp.py` holds it: a held triad at 1/8, 120 bpm
sends C E G C E… each `at` 0.25 s apart and posted ahead; with the loop
playing every `at` a whole number of eighths from its start; latch + arp
runs with the keys up and stops with ■ and with Shift+Space; a note over
a latched arpeggio sounds, and the arp off leaves the chord latched; ⏺
Rec's notes are the arpeggio, half a beat apart; no choice moves a group;
one row at 1920. **Nobody has listened**: the feel of the grid, the gate
and swing.

## Pitch bend

**Pitch bend** (`tests/studio/test_pitch_bend.py`, the parity corpus's
`pitch_bend*`/`wheel*`/`mod_pitch*` cases): the keyboard has ONE wheel,
piano.js `setWheel(-1..1)`, driven by the bend strip (`#bend-strip`,
beside the keys: role=slider, `aria-valuenow` in semitones, drag and
release springs back to centre, left/right step — shift a quarter — and
Home centres), the **arrow bend** (hold up/down: the wheel glides to +-1
over "rise" and home over "release" once let go, steady or "speeds up" —
keys.js's pure `bendStep`, run under node by test_pitch_bend.py; the
dock's "↑↓ bend" menu sets arrows "bend any time" — anywhere arrows mean
nothing else: not fields, sliders, menus, the XY pad or the song grid —
or "only on the strip", rise/release in ms (300/200), curve; per browser
in localStorage `synthlab.bend.*`, like typing/hold), MIDI pitch bend
(midi.js `piano.wheel`), and the XY pad when
an axis is "Pitch bend" (a performance axis: `/fragment/xy` never writes
it into the patch, and the pad springs it back on release). Semitones =
wheel x the edited patch's `bend_range`, so every lane bends the same
interval: a `{type: "wheel", st}` message to every voice node (livefx.js
forwards any pitchless message to all lanes; the worklet smooths
`wheel_st` like any param and adds it to the patch's own `pitch_bend x
bend_range`, per sample after glide/vibrato/pitch env, plus routes to
"pitch" — in the JS control pass and in voice.ts `control()`, P_SM pair
13), and keys.js `bendKeys(st)`, which glides every sounding WAV key and
pre-rendered lane's `playbackRate` (a buffer can only be sped up: its
formants move with it). The range menu `#bend-range` (±2/7/12/24,
client-owned, synced from /patch.json) posts `/fragment/bend-range`
(sets `bend_range`, its Motion row back OOB). "⏺ bend" (`#bend-rec`)
samples one loop of the wheel against the playing loop's phase and posts
it to `/fragment/curve/pitch_bend/set` (whole curve, schema-conformed) —
the edited lane's curve, like every curve. The per-note `bend` message
is only the legato slide between keys.

## One voice: mono and legato

A patch's `voice_mode` (Character's **Voices**: poly / mono / legato) and
`glide_mode` (**Glide when**: always / legato) decide how its keys share
the voice, in the worklet's noteOn / noteOff (`live-voice.md`, "Phase
warp, custom wavetables, one voice") and in the loop (`sequencer.mono_lines`, the engine's
"One voice") alike — so a phrase played on the keys and the same notes on
the grid are the same samples (`tests/studio/test_live_voicing.py`):

- **mono** — one voice: a key takes it from whatever sounds (that one
  fades over 3 ms), its envelopes start again;
- **legato** — a key pressed while another is held re-aims the held voice
  (it glides there over `glide`, else jumps) without a new attack; letting
  the newest go while older keys are held goes back to the newest of those
  (last-note priority); a key pressed with nothing held starts fresh;
- **glide_mode legato** — a glide only into a key pressed over a held
  one (always: from the last note).

Typing rows, chords and MIDI in reach the worklet through the same
noteOn / noteOff, so they follow it: a chord on a one-voice patch is its
last note. Latch / hold keep a key down, so under legato the next key
glides from it. Per lane: each layer's own patch says.

## The mod wheel and aftertouch

**The mod wheel and aftertouch** (the corpus's
`wheels`, `wheel_live`, `aftertouch_live`, `mod7`, `mod12` cases;
`tests/browser/test_mod_sources.py`): two route sources (`"wheel"`,
`"aftertouch"`) and two Patch fields, `mod_wheel` and `aftertouch`
(0..1), performance controls as `pitch_bend` is (`nogene`, CURVABLE,
`paramui.PERFORMANCE_PARAMS`: a macro refuses them, the XY pad plays
them). Live: piano.js `setMod(param, v)` sends `{type: "modwheel" |
"aftertouch", v}` to every voice node; the worklet smooths `wheel_cc` /
`at_cc` and adds them to the patch's own value, clipped to 0..1, where a
route reads the source. They come from **MIDI** (midi.js: CC 1 is the
wheel **when a route reads "wheel"** — `piano.routes("wheel")`; with none,
CC 1 is the cutoff sweep, so a sound without a wheel route keeps it —
and channel pressure 0xD0 is aftertouch; poly pressure is not read), from
the **XY pad** (an axis on "Mod wheel" / "Aftertouch": no spring back, a
wheel stays where it is left) and from the **deck / MIDI learn** through
their Motion rows (knobs, which write the patch's own value: the loop
hears it too). **⏺ bend records them**: a take samples the bend, the mod
wheel and aftertouch, and posts a curve for each that moved
(`/fragment/curve/mod_wheel/set`...), so a recorded wheel moves the loop
as a recorded bend does. The WAV keys have no wheel (a route reads the
patch's value; the hardware's never reaches a `/note` render).

## A key never waits on a render it can stand in for

**A key never waits on a render it can stand in for** (`test_key_swap.py`): key renders are kept per key (newest wins) and never
flushed; `piano.js` `loadPatch` -> `setKeyPatch` re-warms all 24 visible
keys (QWERTY octave first, four at a time, 300 ms debounce, cancelled by
the next change) on load and every patch change; a press with no current
render plays the key's last render, or the nearest warm key within +-2
semitones repitched by `playbackRate`, and crossfades (40 ms) to the exact
render when it lands (`pickKey`, `swapOffset`); a sounding key re-fetches
on every patch change and crossfades too, so every parameter reaches a
held WAV note. The worklet voice joins the same key bus (`keyBus`,
after the WAV-only live filter): one gain stage for both.

## Held keys loop a separate render

**Held keys loop a separate render** (`/note/{pitch}.wav?hold=1`, cache
key suffix `:hold`, `soundmeta.LIVE_HOLD_WINDOW`): hold mode sustains a
WAV-fallback key by looping its sustain, and the one-shot render holds
only ~0.25 s of it — looped, a unison pad's beating steps 4.5 dB at ~3
wraps a second, heard as clicking. The hold render carries 3 s more sustain; the
one-shot URL and bytes are unchanged. `keys.js` fetches whichever one the
key will play (`piano.js` `holdFor`), warm-up included. **The server owns
the loop** (`holdloop.py`): `loop_points` picks whole fundamental periods
of the sustain, `bake_loop` crossfades the seam into the WAV (250 ms,
linear — the sides are correlated, equal-power measured ~2.5 dB loud),
and the route sends `X-Loop: start,end,gate` in seconds (the browser
decodes at its own rate; keys.js snaps them to the decoded buffer's
sample grid). `tests/studio/test_hold_seams.py` sweeps every hold-looped
factory + library sound at C3/C4 and fails any seam whose level step
exceeds the sound's own 30 ms movement (p95, 1 dB floor) by > 0.6 dB.
A change to loop geometry,
`LIVE_HOLD_WINDOW` or the library must keep that sweep green. Key-up
fades the loop along `core.adsr`'s release curve (`keys.js`
`releaseCurve`, run under node against `core.adsr` by
`test_key_release.py`; `piano.js` passes `env_curve` in the hold spec),
not a straight line. Crossfading into the render's own release tail was
tried and measured rougher (the target sits at an arbitrary phase of any
unison beating) — don't reintroduce it without beating the numbers in
that test's docstring. The filter release is still not reproduced.

## `midi.js`

`midi.js` is
**opt-in**: nothing (no `requestMIDIAccess`, no listeners) runs until
`#midi-btn` is clicked, so the audio path's latency is untouched unless
MIDI is in use; it drives the same `noteOn/noteOff` as QWERTY through
`window.synthlab.piano`, and also handles pitch bend (0xe0), the mod wheel
(CC1: the "wheel" source where a route reads it, else -> cutoff),
channel pressure (0xd0, the "aftertouch" source) and the sustain pedal
(CC64). Every message goes to
`midiLearn.feed` first (below): a learned CC is a dial or a control and
stops there; CC 1, 64 and 120-127 are never learned. A note-on asks
`window.synthlab.drumNote` next: while the Song page's drums lane is
armed, a General MIDI drum note plays (and records) that voice of the
song's kit instead of a synth note (songtake.js, `song.md`, "Finger
drums and ✦ Clean up").

**The finger drums' keys** (songtake.js, the Song page with the drums
lane armed — and only then): A S D F G H J K and Z X C V B N M , are
kick snare hat ohat clap tom rim crash, read on `window` in the capture
phase, so they go before piano.js's typing rows and its Z / X octave
keys, and before the page's shortcuts. Not in a text field, not with
Ctrl / Alt / Meta.

## Control surfaces

**Control surfaces** (`tests/studio/test_deck_hid.py`,
`tests/browser/test_deck.py`): four dials, a touch strip and eight keys as
ONE model, `window.synthlab.deck` (`static/deck.js`), which three things
drive — an Elgato Stream Deck + over WebHID, the virtual deck in the page,
and learned MIDI CCs. **No device was attached when it was built**: the
Stream Deck path is unverified on hardware.

### A dial writes through the one hook, as a hand does

A slot resolves, when it is read, to a control already on the page (a
range input, a select, or a radio group with an id — below). **Every write is tweak.js's** ("Gestures: one owner each" below): `synthlab.tweak(el, value)` sets the
control and dispatches the bubbling `input` a hand on the slider would,
`tweak.later(el)` its `change` 250 ms after the last tick, and
`tweak.reset(el)` its default. So a dial, a learned CC, a pen and a mouse
take one path — the debounced form post, undo, the live voice (piano.js's
`mod`), the read-out chip, a knob's arc (`--fill`), `syncControls`'
"being edited" guard and the OOB refresh are the slider's own — and
**deck.js sets no value, dispatches no event and posts nothing**
(`test_deck_hid.py` pins all three; `test_deck.py::
test_a_dial_turn_is_a_drag_in_posts_read_out_and_undo` proves a turn and
a drag to the same value make the same one post, read-out and one undo
step, on a slider and on a `.knob`).
Ticks are summed and applied once per 16 ms (a timer, not a frame: a
hidden tab still hears the device). One tick = 1% of the range, never
under a step; x2 / x4 / x8 when ticks come under 120 / 60 / 25 ms apart;
**press + turn** = one step a tick; a select or a radio group steps one
option a tick. A
**press let go unturned** = the control's default — **`data-default`,
and audio.js's `dblclick` handler is its one definition**: the press, the
strip's long press, the pen's eraser and the menu's "Reset to default"
all send that `dblclick` through `tweak.reset`, so a log slider lands on
its exact default (2200 Hz, not the snapped position's 2202). A control with no `data-default` is left alone.

**A radio group is a stepped control, as a select is**: a
`role="radiogroup"` **with an id** — the Synths panels' pills rows
(`p-osc1`, `p-filter_type`, `p-osc2_sync` …), the wavetable picker
(`p-wt_table`, twelve drawings, not a select: `sounds-and-breeding.md`,
"Oscillator panels"), a drum voice's on / off pills
(`d-<voice>-<field>`) — has its radios, in page order, as its values.
`tweak.steps(el)` lists a select's options or a group's radios and
`tweak.chosen` finds the current one; `synthlab.tweak(group, n)` checks
radio `n` and the `input` / `change` are **that radio's** (as a click on
it fires them), so the form's own `input` trigger posts it and undo sees
one edit. deck.js, menu.js and decksets.js find a value control by one
selector — `input[type=range], select, [role=radiogroup][id]`
(`test_deck_hid.py` pins it in each); a group is named by its
`aria-labelledby` label and its column reads the chosen radio's label.
The id is what makes it one: a binding needs a name to find it again,
and the groups without one (the phrase pills, the recording mode, the
clip hub's targets, the Lab's snap) stay the hand's alone. A group has
no `data-default`, so a press, the eraser and "Reset to default" leave it
alone. `test_deck.py::test_a_dial_steps_the_wavetable_picker_a_table_a_tick`
and `::test_the_wavetable_picker_has_a_controls_menu_and_learns_a_cc`.

### Which four: macro mode, focus mode, and a pinned dial

- **macro** (default): `#macro-0..3`. They are on Synths (folded or not:
  a hidden slider still posts). An unassigned macro is `disabled` and is
  never written; **no dial is ever dead**: a dial whose macro is
  unassigned, or that has none (every page but Synths), takes the next
  control of the touched panel (focus mode's list), or with nothing
  touched the page's own (`pageList`: the visible, enabled controls in
  `#view`, `.knob`s first, the keyboard's own left out), skipping the
  macros and the pinned controls.
- **focus**: the panel of the last control touched (`focusin`,
  `pointerdown` or `input`, in capture; the deck's own writes are not a
  touch) — its closest `[data-module], [data-menu-section], fieldset,
  .fx-unit, section, form` (a module or a named section first: the
  sections menu.js reads) — whose
  visible range inputs, selects and radio groups, in page order, are
  paged in fours.
  The page that holds the touched control shows first; paging wraps. A
  control that a swap replaced is found again by its id.
- **dial sets** (`static/decksets.js`, a row under the dials, atop
  `#deck-bind`): named fours — Filter, Amp env, Filter env, Oscs,
  Motion, Width (`SETS`, by `to` name; a set shows on a page holding
  three of its four), **Keys** — the keyboard dock's own value controls,
  strum, bend range, Room and Volume, which `pageList` leaves out so no
  other dial reaches them — and **★ Yours**, the four controls this page's
  hand changes most (a `change` each — not the deck's own: a dial's turn
  marks its control, and the `change` tweak.later sends 250 ms on is
  skipped, as is a dial press's reset, `deck.ownChange` — per page and
  per browser: `synthlab.deck.used`, thirty a page). A
  click pins all four at once (`deck.set({pins})`); the lit set again
  unpins them. ▶ / the device's **Sets ▶** key steps through this
  page's sets.
- **a section's dials** ("Put these on the deck" in a
  section's menu — `frontend.md`, "One context menu", "A section's own
  menu"): `deck.hold(to)` pins its visible, addressable value controls
  four at a time (a section of three leaves dial 4 the mode's own); with
  more than four, **Page ▶** (the panel's ▶, the device's key, a swipe)
  turns their fours while the pins are still one of them — a dial set or
  a pin ends it. A toast says what landed where ("Filter: Cutoff,
  Resonance, … on dials 1-4 (page 1 of 2: Page ▶ for the next four)").
  The virtual deck and the Stream Deck + both draw `face()`, so both show
  it. **"Add to ★ Yours"** in the same menu puts them first in this
  page's ★ Yours (`decksets.js` `add`: four a page at most, the last
  added first; the rest of the four are still the most used), shown even
  before four controls were learned from use.
- **pinned** (`deck.pin(i, to)`; "Put on dial N" in a control's menu):
  dial N is that control in either mode, wherever it is on the page; on
  a page without it the slot is the mode's own. `to` is the name MIDI
  learn keeps (`deck.toOf`: `id:<id>`, else `name:<field>`; `deck.find`
  resolves it). The same control again takes it off; a control sits on
  one dial at a time. The strip writes a pinned column's name in the
  accent.

### The strip, the keys

The strip is four 200 px columns (name, value with its unit — the row's
own `<output>` text — and a bar). Tap a column = zoom it across all
800 px; zoomed, a tap right of the left 60 px **sets** the value at that
x and the left 60 px leave; swipe = page; long press = default. The
"Meters" key swaps it for the master peak (audio.js's analyser) and the
`#cpu-meter` line.

**The keys are bound to commands, by name** (`KEYS` in deck.js, seven
pages, key 8 turns them: the views and the transport, the tools, the
beat, the mixer, the Song page's playlist tools — ◀ Prev, Next ▶,
Loop, Split, +4 bars, + Trans, Dup ▶ — the jam's launcher —
Launcher, Scene 1-4, Stop all, Capture — and the keyboard dock's Play —
Note, Triad, 7th, Power, Hold, Latch, Typing; the Song pages are dim on
every other view, the dock's off Synths; the first two are the default
layout). Each entry is a
short label and one or more `data-cmd` names — the declarations the
palette harvests (`frontend.md`, "Commands and shortcuts are declared on
their controls") — resolved through `synthlab.commands(true)` every time
a face is drawn:

| key | commands, first one declared on the page wins |
|---|---|
| Synths / Perform / Drums / Song / Library / Lab | `Go to <view>` |
| Play | `/^Play \/ stop the /` (the phrase, the loop, the beat), `Play the song`, `Stop everything` (the top dock's ■, transport.js) |
| Undo | *the pattern's undo button, by selector (below)*, `Undo the last song edit`, `Undo the last tweak` |
| Macros / Focus | `Deck dials: macros / focus` |
| Meters | `Deck strip: values / meters` |
| Sets ▶ / Page ▶ | `Deck dials: next set` (decksets.js) / `Deck: next four controls` (◀: a swipe, or the panel's ◀) |
| Jam | `Record a jam to a clip` |
| Click | `Metronome: on / off` |
| A / B / C / D (the third page: the beat) | `Beat variation A` … `D` (lit: the one on the grid) |
| Fill / Sparser / Busier | `Add a fill`, `Make it sparser`, `Make it busier` |
| Mix / Balance / Mute / Solo / Return (the fourth page: the mixer) | `Go to Mix`, `Balance the mix`, `Mute the focused strip`, `Solo the focused strip`, `Add a return`; then Play (`Play the song`) and Undo (`Undo the last song edit`) |
| ◀ Prev / Next ▶ | `Previous section` / `Next section` |
| Loop | `Loop the selected range` |
| Split / +4 bars | `Split at the marker` / `Insert 4 bars at the marker` |
| + Trans | `Add a transition before the selected section` |
| Dup ▶ | `Duplicate the selection` |
| Launcher / Scene 1-4 / Stop all / Capture (the sixth page: the jam) | `Jam: on / off`, `Launch scene 1` … `4` (lit while every pad of the scene sounds; dim past the song's last section), `Stop all`, `Capture the jam to the song` (dim while the log is empty). On Perform Scene 1-4 and Stop all are its launcher's — the same command names, a scene lit while all of it plays; Launcher and Capture are dim there |
| Note / Triad / 7th / Power / Hold / Latch / Typing (the seventh page: the keyboard dock's Play) | `Each key plays: a note`, `… a triad`, `… a 7th chord`, `… a power chord` (lit: the choice), `Hold: a note sounds while its key is held, on / off`, `Latch: the next note or chord holds, on / off`, `Typing keys play notes: on / off` |

A key **runs its command as the palette does** (a click on the control
that declares it), is **lit** from that control (`aria-pressed`,
`aria-current`, `data-state="on"`; Play also from the transport itself,
and the deck's own two from its state, so a deck with the panel shut is
right too), and is **dim** when no control on the page declares the
command or the one that does cannot run (`disabled`, `aria-disabled`,
under `[hidden]`). The *first name declared* wins, runnable or not: a
song with nothing to undo dims Undo, it does not fall through to the
patch's. `commands(true)` is the palette's harvest without its two
filters (every declaration, the ones that cannot run marked `off`).
The deck's own actions are `data-cmd` buttons in its panel head
(`#deck-mode`, `#deck-meters`, `#deck-prev`, `#deck-next`,
`#deck-connect`, `#deck-side`) and on the rail (`#deck-btn`), so the
palette lists "Stream Deck: connect / disconnect", "Stream Deck: show /
hide the virtual deck" and "Deck dials: macros / focus" too.

**One selector is left**: `[hx-post="/fragment/pattern/undo"]` — the
loop's own undo (`fragments/transport.html`, `grids.html`) declares no
command yet. When that button carries a
`data-cmd`, put its name in `KEYS` and delete the `{ q }` entry (a test
pins that there is exactly one).

### Decks: virtual and WebHID

A Deck is anything with `show(face)`; `deck.attach()` it and call
`deck.handle(event)` (`turn`, `dialdown` / `dialup`, `keydown`, `tap`,
`long`, `swipe`). `handle` swallows whatever a handler throws.

- **The virtual deck** (`static/deckui.js`, the rail's "deck" button):
  keys, strip and knobs for mouse, pen and keyboard. **A knob is a real
  `input[type=range].knob` that mirrors its slot's control** (min / max /
  step / value, a select's option numbers, the arc) — so every gesture on
  it is tweak.js's, as on any knob in the app, and its `input` goes to
  `deck.put`; Enter / Space or a double-click is a press (the default).
  It has no gestures of its own, and so does not turn in ticks:
  acceleration and press + turn are the
  device's and `deck.turn`'s, driven in the journeys by event.
  **It is docked, never over the page** (`html[data-deck]`, style.css
  section 23 / dense.css D23): `bottom`, a strip along the bottom edge in
  the device's own landscape shape (keys | strip | dials | MIDI), the
  default because it works at every width and keeps the strip legible;
  or `side`, a 300 px column at the right from 1200 px (the head's "dock
  beside" / "dock under"). The page makes room — `body`'s padding, the
  sticky keys dock standing on the strip, beside the pinned clip library
  rather than under it — as it does for that library. It is built from
  the canonical components (`.btn`, `.knob`, `.meter`, `.led`, `.caps`,
  `.panel-head`), no CSSOM layout, and rules of its own only for where
  things sit and the device's look.
  **The look ("Afterhours"; `static/deckart.js`, ONE drawing
  for both decks)**: each key a recessed glass button — a radial
  vignette, a bevel lit from the top edge and shadowed at the bottom, a
  role lamp at its top, the label (or an icon over it: Play, Undo, Loop,
  Page / Prev / Next, Jam / Capture, Stop all, Meters, Macros / Focus,
  Click, More with a dot per key page) glowing in its **role's colour**:
  `--go` play and the jam's launches, `--danger` record and stop,
  `--ai` the generators (Fill, Sparser, Busier, Balance), `--pause`
  Mute, `--signal` the deck's own and the sound's (Macros, Meters, Mix,
  Loop, A-D, Page), `--accent` the rest (the views, Undo). Lit = a rim
  light and a bloom that **pumps on the kick** (motion.js's envelope,
  `synthlab.kick()`, the one `--kick` comes from — not detected twice);
  a lit Play **pulses with the master level** too; a press flashes the
  key for 280 ms and sends a **ring** out across its glass (560 ms);
  dim = faded, no glow. The strip is
  dark glass: per column the name in small caps (a pinned one in the
  accent), the value glowing, and a **luminous trace** with a bright
  head; a value that moves leaves an **afterglow** from where the motion
  began, fading 800 ms after it stops; a fast move (over 0.8 of the
  range a second, a tap that jumps) throws **sparks** off the head; a
  new value **rolls** in over the old one (240 ms) and flares (600 ms).
  While the transport plays: a **mirrored spectrum** (48 log bands, a
  ribbon through the bands' tops and its reflection under a centre line,
  `--signal` to `--ai` to `--accent` low to high, each band's held peak
  a dash of light), a **beat line** sweeping the strip once a bar (the
  loop's start, or the playing song pass, at the page's bpm, less the
  output latency) with the **floor of the beat's column** lit on each
  beat, and light welling up from the strip's bottom edge on the kick.
  The spectrum is the deck's **own tap** off the master analyser
  (1024-point, no smoothing: ~12 ms of window), with envelopes that rise
  at once and fall over 150 ms (peaks held 260 ms): measured against a
  gated 100 Hz tone, half height 32-41 ms after the onset, down to a
  fifth in 272 ms, and a 120 bpm pulse train swings it 95 % a beat (the
  master's 0.8 smoothing read in dB: 61-67 ms, > 4.7 s and 52 %).
  motion.js's master analyser (2048, 0.8) is untouched — a journey pins
  it. The glass is **dark in both themes** (the device is a
  black slab): mixed from the theme's own `--bg-inset` and `--text`
  (`--deck-glass` in style.css section 23, the same recipe in the
  drawing), so the day theme gets warm graphite and the panel round it
  stays light; the role hues are the theme's own tokens, read from
  computed style (`inks`, again only when they changed), lifted a little
  so the day theme's deeper ones glow; a token that will not parse falls
  back to a fixed palette (numbers, never a colour string). The knobs
  are `.knob`s drawn as dark domes with a lit ring in the slot's colour
  (teal; a pinned dial's accent), a bright tick at its end and a glow.
  **What is drawn**: every region and key carries a `sig` (content) and
  an `amb` (its motion); the virtual deck draws only what changed —
  **nothing at rest** (`synthlab.deckArt.draws` counts; journeys hold
  it still for a second, after a move and after the transport stops) —
  **on the next animation frame after a change** (deck.js coalesces
  every event into one face a frame through `requestAnimationFrame`: a
  dial's value to its first draw measured 20-21 ms median, the painted
  frame 26-30) and a frame at a time
  while something moves (`deckArt.busy()`: 60 painted frames a second
  while playing). The
  cost stays small because what a key or a column **shows** (its glass,
  face, name, value, trace: the shadow-blurred part) is drawn once per
  content into a cached layer and a frame of motion composes layers
  with the light over them: playing, the deck open costs ~6 % of the
  main thread in headless Chromium (17.3 % closed, 23.0 % open). **Under calm motion (`frontend.md`,
  "Motion: full or calm") the deck is still**: no spectrum, beat, kick, sparks, ring or roll — a
  press is a plain flash, a move a short dimmer glow — and a playing
  deck draws nothing on its own (a journey). A new page of keys or a
  new mode / page on the strip slides and fades in (GSAP, 0.14 s / 0.16 s,
  never under calm motion); the device just gets the new
  images. A theme or a hue change (`<html>`'s `data-theme` or `style`,
  where the Colours popover sets the `--hue-*`, or `synthlab-colours`:
  `frontend.md`, "Colour: hues and depths") draws the virtual deck again
  and re-sends every image (deckart.js `A.restyled`, deckui.js's next
  frame). The two glass plates
  **lean toward the pointer** (±2.5°) with a soft light where it points
  (`--lean-x` / `--lean-y` / `--glint-x` / `--glint-y` on the panel,
  section 23; not for touch, not under calm motion).
  It is built in script — `base.html` belongs to the shell — and **hangs
  off `<html>`, beside `<body>`**, so a full-body morph does not remove
  it; only its rail button is put back after one. It holds no named field
  (`htmx.ajax` posts every one on the page). Closed, it is `display:
  none`, not `[hidden]`: its commands still run.
- **The Stream Deck +** (`static/deckhid.js`: the one file that knows its
  bytes; vendor `0x0fd9`, product `0x0084`). `parse` reads an input
  report (keys, dial press / turn, strip tap / long / swipe), every read
  length-checked, unknown types ignored, coordinates and ticks clamped;
  `keyPackets` / `lcdPackets` page a JPEG into 1023-byte reports.
  deckui.js draws the same face onto canvases and sends **only the
  images whose content changed — on the frame that changed them, ahead
  of any motion**: encoded with `toDataURL` (synchronous, ~0.1 ms a
  region; `toBlob` and `convertToBlob` answered ~12 ms later, measured)
  on CPU canvases, and queued as **urgent** in deckhid.js's writer. That
  writer is the device's queue: one image on the wire at a time, its
  reports never interleaved; an image still waiting is **replaced** by a
  newer one of the same key, or of a strip region it overlaps; urgent
  images go before waiting motion. So the queue holds at most one image
  a key and a region (`test_deck_hid.py` drives it against a slow fake
  device). An image whose motion alone moved goes at most 20 a second
  for a strip region and 15 for a key, at JPEG quality 0.72 (content
  0.88), **only while nothing waits for the link** — the rate falls to
  what the USB link carries — and a held-back last frame is always sent
  later. Measured against the fake device while a loop plays (a link
  that takes each report at once): a dial's value reaches the device in
  21 ms median, 25 worst, 14 ms at rest; with a fake link of a timer a
  report, 127 / 195; the strip's motion 17 frames a second a region, the
  keys' 9 images a second, ~275 KB/s of reports. A hidden tab keeps the device going on a 50 ms
  timer (`requestAnimationFrame` stops there). Nothing runs until **Connect** is clicked (the chooser
  needs a gesture); a device granted before reconnects on load.
  Chromium / Edge only, and **only in a secure context**: `127.0.0.1`
  is one, a `--share` page over plain HTTP on the LAN is not
  (`navigator.hid` is absent there; the panel says so). The CSP does not
  govern HID and the studio sends no `Permissions-Policy`. **The Elgato
  app holds the device: quit it first** — the panel says so, and an open
  that throws says it again. A report's bytes are taken from two MIT
  projects' reading of the protocol; nothing of theirs is vendored.

### MIDI learn

`static/midilearn.js`; midi.js stays opt-in and asks for nothing new.
"learn" under a knob, "learn: <the last control touched>", or **"Learn
MIDI" in a control's own menu** ("A control's menu" below) arms; the
next CC binds (`{ch, cc, to, name, mode}`, `to` = `slot:N`, `id:<id>` or
`name:<field>`). Modes, cycled by hand on the binding: `abs` (0..127 =
the range), `rel2c` (1 / 127), `relbin` (65 / 63), `relsign` (1 / 65).
**CC 1, 64 and 120-127 are midi.js's**: refused while learning, never
consumed. A CC bound again moves. A binding whose control is not on the
page does nothing there. A bound CC writes through deck.js (`setEl` /
`nudge` / `turn`), so through the one hook; `forget(to)` drops every CC
bound to one target. **"MIDI learn these"** on a section's menu
is a walk: `learn([[to, name], ...])` arms the first, each
CC that binds arms the next ("Learning Resonance (2 of 8)" in the toast
corner), until the last; "Stop learning MIDI" (the same menu, or any
control's) or any `arm` ends it (`stop()`).

A control a binding cannot tell apart from another — a `name` two
controls share, with no id — is never offered for one: its menu keeps
"Reset to default" only, and a section's dials leave it out. The FX
units' knobs all share `name="value"`, so each carries an id,
`fx-<rack>-<index>-<param>`
(`fragments/fx_units.html`).

### A control's menu

A value control's rows in THE context menu (`static/menu.js`,
`#ctx-menu`: `frontend.md`, "One context menu"). A **right click**, the pen's **barrel button tapped** (it arrives
as a right click) or the keyboard's Menu key / Shift+F10 on a slider, a
knob, a select or a radio group with an id (a press on any of its radios)
opens it at the pointer; when the control sits in a
thing with a menu of its own (a layer's level knob, an FX unit's
parameter), that thing's rows follow under a seam, and then the rows of
the section it sits in (its panel's: "Put these on the deck", "Add to ★
Yours", "MIDI learn these", the panel's own commands — `frontend.md`,
"A section's own menu"):

| row | does |
|---|---|
| Learn MIDI / Stop learning MIDI | `midiLearn.arm`: the next CC binds to this control. Said in the toast corner (a plain `.toast`: the deck's panel may be shut), as is the binding when it lands |
| Forget MIDI | only while a CC is bound: `midiLearn.forget` |
| Reset to default | `tweak.reset`; disabled for a control with no `data-default` |
| Put on dial 1-4 / Take off dial N | `deck.pin` |

It is the one `contextmenu` listener for value controls: the browser's
menu never shows on one, and a press that was a drag opens nothing
(`tweak.busy()`: tweak.js's fine drag is a barrel drag). **In the step grids a right
click is still an erase** (grid.js) and opens nothing. Arrow keys move,
Esc and a click outside close. "Type a value" is not a row: no control
takes a typed value yet.

### Where it is kept

`localStorage`: `synthlab.deck` (mode, view, the four pins),
`synthlab.deck.used` (what each page's hand changes most) and
`synthlab.deck.yours` (what a section's menu added to ★ Yours, a page's
four),
`synthlab.deck.open`, `synthlab.deck.dock` (`bottom` / `side`),
`synthlab.deck.hid`, `synthlab.midimap` (cleaned on load). Device
preference, like the HID grant itself: no route, no file in the data
dir, nothing in `state.json`.

## The live talkbox

**The live talkbox** (`static/talkbox.js` — the switch,
`#talk-btn` "🎙 talk" in the keyboard dock's toolbar — and
`static/talk-worklet.js`, the vocoder in the AudioWorklet scope;
`tests/studio/test_talkbox.py` runs its `TalkDSP` under node,
`tests/browser/test_talkbox.py` the switch with Chromium's fake mic).
On, the mic is the voice and the keys the carrier: the key bus is
routed into the vocoder through audio.js's `setLiveOut` (keys.js
reconnects on `live-out-changed`), and the vocoder's output goes where
the keys went (the master, or space.js's room — taken back if space.js
builds its room later); off, the live out is given back and the mic's
tracks stopped. 16 log-spaced bands 120 Hz - 7 kHz, each two RBJ
band-passes in series (4 poles, as `effects.vocoder`'s: a 1 kHz voice
and the 300 Hz keys stay 25 dB apart, where a single pair let it open
them), followers at
3 / 40 ms, `air` 0.3 (the mic's own top over 4.5 kHz: the consonants),
`dry` 0 (no dry keys; the worklet reads it under the name `mix`). ↺ Defaults
(`#talk-reset`) puts every setting back to the Talkbox character's
(`DEFAULTS`); each character names every setting, the robot's note
(C3, 48) included, so the note select never shows one the DSP is not
using. A talk take is kept with source `talkbox` (`/clip/upload`'s
`source`, one of `UPLOAD_SOURCES`, else `jam`). The mic opens with echo cancelling, noise
suppression and gain control off (they smear a voice); the first
switch-on says "wear headphones". **Monitor only**: never in a render,
a bounce or an export — the Voice lab bakes a take. The choice is not
kept: the mic opens when asked, every time. Nobody has talked through
it yet.

**Its panel** (⚙ beside it, `#talk-pop`; this browser's settings,
`localStorage` `synthlab.talk`, posted to a running vocoder as they move —
nothing here posts to the server): a character — *talkbox* (the keys
speak), *robot* (the vocoder's own saw at a note: it talks with no key
held), *whisper* (a noise carrier), *choir* (24 bands, the formants 3
semitones down, a 120 ms release, a little dry) — then the carrier, the
robot's note, **the mic's pick-up** (so a whisper is heard; `TalkDSP` before the bands and the consonants
hear the mic: **mic gain** 0..+36 dB; **auto level** 0..1, a lift toward a
mean |x| of 0.1 from a 10 ms / 250 ms level follower, up to +24 dB and
never below 0 dB, raised to the amount; **gate** -90 (off)..-20 dB on the
same follower, 2 ms open / 80 ms shut, so a boosted room's hiss stays
silent. At 0 / 0 / -90 the pick-up passes the mic sample for sample, and
every character but *whisper* (+12 dB, 0.7, -66 dB) sits there; the
switch's meter reads the voice after them — `tests/studio/test_talkbox.py`),
bands (8-32: re-laid live), formants (-12..+12 st: the
carrier's bands moved off the mic's), consonants (`air`), dry, release
and level. **The mic's level lights the switch** (`--talk-in`, ~15 a
second from the processor). **⏺ record to Clips** (`#talk-rec`): the
processor posts its output blocks while asked; the second press (or the
`MAX_CLIP_SECONDS` cap, or switching off) makes a stereo WAV of them and
`POST /clip/upload`s it as `talk-<time>` — the one way a live talk
leaves the Monitor. **⏺ → booth** (`#talk-rec-booth`)
records the same way, then makes the kept clip the Voice page's take
(`/voice/take/clip`, `booth=1`: the booth treats it next;
`docs/sampler-and-clips.md`, "The vocal booth").

**🎙 vocal**, beside the talkbox's ⚙: opens the live vocal
chain's panel (`#vox-pop`, every page's — the rail's "vocal" opens the
same one on Perform, Song and the rest; its LED lit while the mic is
on). The talkbox makes the keys speak with the mic's shape; the vocal
chain is the voice itself, through a live rack (auto-tune, harmonizer,
compressor, de-esser, gate, the studio tricks), over whatever plays, and
its ⏺ takes it on the loop's bars with the dry voice beside it
(`docs/sampler-and-clips.md`, "The live vocal chain"). The two can run
at once: each opens its own mic stream.

## Gestures: one owner each

**Every gesture on every kind of control has one island that handles
it**. Two islands on one gesture fight: with tweak.js and pen.js both
taking a pen's barrel + drag on a slider, at different factors, both
write the value on every move (a trusted pen dragging `#p-resonance`
80 px: 0.27 / 0.26, 0.30 / 0.29, 0.32 / 0.30 into the live voice), the
last writer wins, and two `change`s follow. Likewise one reset, one
`touch-action` and one pen `contextmenu` cancel, each in one place.

The split: **tweak.js owns everything about a VALUE control** (a slider,
a `.knob`, a select, a radio group with an id: the write, fine, wheel,
reset, the pen on one) and
detecting the pen; **pen.js owns the pen as an instrument on SURFACES**
(grids, lanes, keys) and touches no value control; **menu.js** is THE
context menu — a value control's rows, a thing's own (`data-ctx`), a
panel a thing names (`data-ctx-pop`) — and **tweaktype.js**, tweak.js's
second file, is a value typed into a read-out. This is the one table —
add a row before adding a listener.

| control | gesture | does | owner |
|---|---|---|---|
| any | the first `pointerType: "pen"` contact | marks the browser: `html[data-pen]`, the `pen` cookie, the dense tier whatever the pointer reports | tweak.js (nothing else detects the pen; pen.js only reads each event's `pointerType`) |
| slider | drag | the browser's own | — |
| slider, `.knob` | Shift + drag, or a pen's barrel button held + drag | **fine: a tenth** of the travel, from where it was | tweak.js |
| `.knob` | drag (any pointer) | up / right = more, 160 px the whole travel, pointer captured | tweak.js |
| slider, `.knob` | wheel, **focused** only | 2 % a notch, one step with Shift; `change` 250 ms after the last | tweak.js |
| slider, `.knob` | arrow keys | one step | the native input |
| slider, `.knob` | double-click | `data-default` (a log slider's exact value) | audio.js — **the** definition of "default" |
| slider, `.knob` | pen eraser (button 5 / `buttons & 32`) | that double-click (`tweak.reset`) | tweak.js |
| slider, `.knob` | pen hover | the value as the control's `title` (shell.js shows it) | tweak.js |
| slider, `.knob`, select, radio group with an id | right click, pen barrel **tap**, long press (touch), Menu key / Shift+F10 | the control's rows in the context menu (then its thing's rows); never the browser's; nothing after a drag | menu.js (asks `tweak.busy()`) |
| slider, `.knob` | click its read-out (`<output>`), or Enter on the focused control | a field in the read-out: type, Enter applies (clamped, snapped, one `input` + `change`), Esc cancels | tweaktype.js, through `synthlab.tweak` |
| slider, `.knob` outside `#patch` | double-click | ... and the `change` such a control posts on | tweak.js (after audio.js's reset) |
| a row, a card, a module, a head (`data-ctx`, `data-ctx-pop`) | right click, pen barrel tap (off the pen surfaces), long press, Menu key / Shift+F10 on a control inside it | its menu: its own `data-menu` controls, or the panel it names. Arrows / Home / End move, Enter activates, Esc closes and returns the focus | menu.js — the only island that opens a menu |
| a **section** — a panel, an FX unit or rack, the layer strip, the keyboard dock, a launcher, a mixer strip, a card (`[data-module]`, `[data-menu-section]`, a fieldset, a `<section>`, `.fx-unit`, a folding rack: `frontend.md`, "A section's own menu") — its background, a button in it, or after a value control's / a thing's rows | right click, pen barrel tap (off the pen surfaces), long press, Menu key / Shift+F10 | its own rows: its dials onto the deck ("Put these on the deck", "Add to ★ Yours", "MIDI learn these"), its controls' `data-cmd`, its fold; each section round it a submenu (→ opens, ← or ◂ comes back). **Not** on a text field, a link or selected text (the browser's menu), a step (grid.js's erase) or a pen surface (pen.js) | menu.js |
| slider, `.knob` | any drag | never scrolls or selects: `touch-action: none` | style.css, section 15 (pen.js does not set it on sliders) |
| a module in a dock (`data-ws-m`: its own head — `.module-head`, `legend`, `summary`, `header`, `h2` / `h3`, `[data-ws-head]` — or its bare frame, never a control in it), a group's tab | mouse or pen drag past 6 px (not a finger: its long press is the menu) | moves the module: beside another, into its tabs, to the dock's edge; Esc cancels; the click that ends it is swallowed. A floating module's frame: moves its panel | workspace.js (`frontend.md`, "Workspace: modules you can move") |
| a sash between two modules (`.ws-sash`) | drag, arrows, double-click | the two share their width / the upper one's height; evens them | workspace.js (layout.js keeps the pages' own `.splitter[data-var]`) |
| a module focused (F6) | Alt+Shift+arrows | moves it that way | workspace.js |
| slider, `.knob`, select, radio group with an id | a deck dial, the strip's fader, a learned CC | `synthlab.tweak` + `tweak.later`; press / long press = `tweak.reset` | deck.js, through tweak.js's hook |
| the virtual deck's knob | everything above | it is a `.knob` mirroring its slot's control; its `input` -> `deck.put` | tweak.js; deckui.js forwards |
| the virtual deck's knob | Enter / Space, double-click, eraser | a press: the slot's default | deckui.js -> deck.js -> `tweak.reset` |
| the virtual deck's strip | tap, swipe, hold | zoom / set, page, default | deckui.js -> deck.js |
| step grid cell | click, drag | toggle, paint (the stroke; on the piano roll it fills every step between two pointer samples) | audio.js |
| step grid cell | right click; right-drag (mouse) | erase | grid.js; audio.js's stroke |
| piano roll step | the TOOL (`<html data-roll>`: draw / select / stamp / erase; the toolbar's `.seg`, a command each) | which of the rows below a press is | roll.js (audio.js reads "erase" for its `paintVal`; grid.js leaves a Shift-click alone outside draw) |
| piano roll step, draw tool | a drag that starts on a note's END (its last step's last 6 px; 10 px under a pen; the right half under a finger) | the note longer or shorter: one `resize` post | roll.js (it stops the event before audio.js's stroke arms; a click there is still audio.js's toggle) |
| piano roll step, select tool | drag on empty steps; drag on a selected note (Ctrl = copy); click; Shift-click | marquee; move / copy the selection (one `move` / `copy` post); select the note; add to the selection | roll.js |
| piano roll step, stamp tool | click (any pointer), Space / Enter | the chord on that row: one `stamp` post | roll.js |
| piano roll step, erase tool | click, drag | erase only (a click on an empty step draws nothing) | audio.js's stroke (`paintVal` false); roll.js cancels the click that would draw |
| piano roll step | arrows, Home / End, PageUp / PageDown; Shift+arrows; Alt+arrows; Ctrl+← → / ↑ ↓; Ctrl+C / V / D / A; Delete; Enter, Shift+Enter; Esc | the cursor (one tab stop, roving); select; move the selection or the pattern; a note's length / the step's velocity; copy, paste, duplicate, select all; delete; a click, a cut; no selection | roll.js (`loop-and-perform.md`, "The keyboard"). **On a note step, shift / transpose is Alt+arrows.** grid.js keeps Shift+arrows on a DRUM step |
| piano roll step | a finger held (the browser's `contextmenu`, `pointerType: "touch"`) | selects the note under it and opens the roll's menu (the toolbar's `data-menu` controls) — not an erase | grid.js hears it (the grids' one `contextmenu` listener) and hands it on as `roll-press`; roll.js selects and re-sends a `contextmenu` on the grid; menu.js opens the menu |
| piano roll step | hover (a mouse, a pen above the glass; not a finger) | `title` = its pitch and step | roll.js |
| strum knob (`#t-strum`, the drawer's `#drawer-strum`) | drag, wheel when focused, arrows, a typed value, double-click = 0; a pen and a finger as on any knob | ms between a chord's notes (step 5); posts with the grid's form | tweak.js (a native range) |
| the roll's "⚄ write" menu, ⚄, busier, sparser | click / tap; Tab to the summary, Enter, Tab, Enter; the palette ("Write a lead", "Roll the part again", …) | a generated part over what the grid shows; it plays | native `<details>` and buttons; htmx posts |
| `[data-zoom]` scroller (the piano roll) | Ctrl+wheel; pinch; Ctrl+= / − / 0; the − fit + buttons | the step width, about the pointer; per browser | zoom.js |
| `[data-zoom]` scroller | wheel; Shift+wheel | scroll down; sideways. Never a value | the browser |
| `[data-zoom]` scroller | two fingers moved together; a middle-button drag | pan (a second finger ends the first one's stroke and starts none) | zoom.js |
| a slider or knob with `data-default` (not in a `[data-zoom]` scroller) | a middle click | back to its default, as a double-click (the browser's autoscroll cancelled) | tweak.js (`tweak.reset`) |
| step grid cell | pen tip | draws; **peak pressure over a step it turns on = that step's velocity** (0.25 + pressure, on the lane's 0.05 grid) | audio.js draws, pen.js writes the lane |
| step grid cell | pen eraser | erases: a tap (pen.js), a drag (audio.js's stroke, button 5) | pen.js + audio.js's one line (`paintVal`) |
| step grid cell | pen barrel | **= Shift**: a tap is a Shift-click (a cut, where the grid has cuts; on the piano roll's select tool, "add to the selection": roll.js reads the Shift); a drag paints plainly | pen.js |
| step grid cell, every pen surface | a pen's `contextmenu` (barrel, press-and-hold) | cancelled before grid.js reads it as an erase, and before menu.js hears it: no menu under a pen on a grid, a lane or the keys (a drum row's tools stay on its ⋯) | pen.js (value controls excepted: menu.js) |
| velocity lane | drag, arrows | sets the step's velocity | audio.js |
| velocity lane, piano roll | ← → | the next lane (one tab stop for the row); ↑ ↓ Home End stay the value | roll.js (in capture, before audio.js's arrows) |
| velocity lane | pen hover | `title` = its velocity | pen.js |
| **step sequencer** (the drum grid: `drums.md`): a row's name | press | hears the voice (grid.js) and selects the row: its hits take the lane under the grid; again: every row | grid.js plays, steps.js selects |
| drum step | click, drag; right click; pen tip / eraser / barrel | as any step grid cell (the four rows above): toggle, paint, ERASE, pressure, erase, Shift | audio.js, grid.js, pen.js — unchanged |
| drum step | focus or press | the cursor: the inspector's five knobs are this hit's; with a row selected, the selection follows the hand | steps.js |
| drum step, focused | arrows | move the cursor (↑ ↓ select the row); the grid is ONE Tab stop (roving `tabindex`) | steps.js |
| drum step, focused | Space; Enter | toggle the hit | the checkbox; steps.js |
| drum step, focused | `1`-`4` | the hit's ratchet | steps.js |
| drum step, focused | `+` / `-`, Alt+↑ / Alt+↓ | the hit's value in the lane's field, a notch | steps.js |
| drum step, focused | Alt+← / Alt+→ | nudge the hit earlier / later, 0.05 of a step | steps.js |
| drum step, focused | PageUp / PageDown | the lane: velocity, chance, ratchet, nudge, tune | steps.js |
| drum step, focused | Delete, Backspace | the hit's data cleared (the hit stays) | steps.js |
| drum step, focused | Shift+← / → | shift the beat (the pattern tool) | grid.js — unchanged |
| drum step, **focused** | wheel | the hit's value in the lane's field, a notch a click; nothing with Ctrl or Shift held, nothing on a step that is not focused (the page scrolls) | steps.js |
| drum step with a hit | long press (touch); the Menu key / Shift+F10 | the HIT's menu (ratchet x2 x3 x4, chance 25 / 50 / 75, nudge, clear) — the one place a `contextmenu` is neither grid.js's erase nor menu.js's | steps.js (a mouse's right click stays the erase; a pen's is cancelled by pen.js first) |
| anywhere in the sequencer's pane | Alt+`1`-`4` | the beat's variation A-D | steps.js (clicks the letter) |
| hit lane (a row selected) | drag; pen drag | draws the lane's field across the row's hits, on the field's own levels (`data-q`); one post, one undo step | audio.js draws, steps.js reads it into the table |
| hit lane | arrows, Home / End | one notch of the lane's field; its ends | steps.js (audio.js's are the shared velocity lane's) |
| hit lane | right click, Menu key | the hit's menu for that step (`data-ctx-pop`); a right press never starts a stroke | menu.js opens, steps.js cancels the stroke |
| hit lane | pen pressure on a step of the SELECTED row | that hit's own velocity (0.25 + pressure) — no row selected: the step's | pen.js writes the lane, steps.js reads it |
| the inspector's and a row head's knobs | everything a `.knob` does | the selected row's level and swing, the cursor hit's velocity / chance / ratchet / nudge / tune; double-click a row's swing = follow the kit | tweak.js (the write), steps.js (what it means) |
| **mixer strip** (the Mix view: `song.md`, "The mixer"): a fader, a knob | drag (up or right is more, 160 px the travel); Shift or the pen's barrel = fine; the wheel when focused; ↑ ↓ PageUp PageDown Home End; a double click, the eraser, a dial pressed = the default; a click on its value = type one; a right click = its menu | the track's level, pan, send A / B, duck, EQ lo / mid / hi (a return's level and its unit's parameters); one undo step a gesture | tweak.js (a fader is a `.knob` to it); the post is the control's own (`hx-sync="this:queue last"`) |
| mixer strip: a fader, a knob | a finger's double tap | the default (a touch screen sends no `dblclick`) | mixer.js |
| mixer strip: any control | arrows, Home / End; Tab | a fader's or a knob's own value (← → pan a pan knob); every control is a Tab stop, in the strip's order | the native input, tweak.js (mixer.js marks the strip last touched) |
| mixer strip: M, S | click, Space / Enter, a tap | mute, solo | the button (an htmx post) |
| mixer strip | focus or press anything in it | it is the focused strip (`[data-focus]`): the bar's "M strip" / "S strip" act on it, and in the deck's focus mode its first four controls are the dials | mixer.js marks it; deck.js reads the fieldset |
| Mix view | Space; L; Ctrl+Z / Ctrl+Shift+Z | play the song / pause it (Space again resumes); loop the section shown; the song's undo / redo | transport.js (Space), song.js (L); mixer.js (the undo keys) |
| the keyboard dock (every page): the keys arp | click; **Alt+A** (not a field) | the keys arp on / off ("The keys arp") | arp.js (window capture) |
| the keyboard dock (every page), typing → keys \| pads on Perform | click; **Alt+P** | typing plays the dock's keys (the default) or Perform's pads and FX keys | piano.js (window capture; Alt+P only where `#perform-pads` is) |
| the keyboard dock's typing, any layout | a key a page owns (`#view[data-own-keys]`: L on Song and Mix); a key typed into a focused step grid, the playlist, the jam's grid, a chord tile, the ruler, the Lab's wave or an edge | the page's, never a note | piano.js `typing` — the page's island |
| the keyboard dock, letters layout (not a field, typing on) | A–G; Left Shift / Space / Ctrl / Tab held with them; 1–7; Alt+L (any layout) | that note, or the chord the modifiers name on it; the octave; the next layout — rows → letters → columns → rows ("Letters") | letters.js (window capture, before chords.js and piano.js; Space handed over by transport.js's `takesSpace`) |
| the keyboard dock, columns layout (not a field, typing on; no Ctrl / Alt / Meta held) | A S D … '; Q W E … ]; Z X C … /; 1 … 0; Alt+N | the column's note; its major; its minor; its 7 / maj7 / m7 / sus4 / ♯ — or 1–7 the octave; the number row's next choice ("Columns"). Shift stays the pedal and the latch, Space the transport's, - / = the octave | columns.js (window capture, after letters.js, before chords.js and piano.js) |
| **anywhere** (not a field; for Space also not a focused button, a step grid, a key, a pad, a chord tile, the jam's grid) | **Space**; **Shift+Space** | in the letters layout a tap acts on its keyup, a hold with a letter in it is a modifier; otherwise: this page's ▶ (`#view [data-keys="Space"][data-player]`: Synths, Perform, Drums, Song, Mix) — its ⏸ while that player plays where the page has one, resume while paused, a press otherwise; with no ▶ on the page, pause / stop what plays. Shift+Space: stop everything (the top dock's ■) | transport.js (window capture; never in the Song page's framed dock) — `frontend.md`, "The transport is the same on every page" |
| anywhere | Alt+M | the Mix view | mixer.js (clicks the rail's link) |
| Perform, not a field | **Alt+O** | the Loops panel: open (the search focused) / fold (Alt+L is the keyboard dock's layout). Sliding over the page (below 2200 px, unpinned), Esc inside it or a press outside it folds it | loopspanel.js (window capture; `data-keys` on its ◀) |
| Perform, not a field (or inside the coach) | **Alt+K** | ✦ Coach: open (the message box focused) / close — the column at the right of the grids (`loop-and-perform.md`, "✦ Coach"); Alt+O, Alt+S and Alt+L were taken | coach.js (window capture; `data-keys` on the head's ✦ Coach) |
| Perform (not a field): the edited lane; the Song page: the editor drawer holding the keyboard, or no Perform lane head on the page | **Alt+S** | the sound menu (`#sound-pick`, the search focused) for the edited lane's sound / for the drawer's TRACK's sound (`song.md`, "The drawer's sound: change it while it plays") | soundmenu.js (`data-keys` on the lane head's ▾ and on `#drawer-sound`) |
| the Song drawer's sound button (`#drawer-sound`), closed | ↑ ↓; the wheel | step the track's sound through the menu's order, each heard as it lands (one browse = one Song undo step) | soundmenu.js (`step`; the wheel's `preventDefault` keeps the page from scrolling) |
| ✦ Coach's 🎙 | hold (pointer, Space or Enter) | record a spoken message, sent when let go (at most 10 s) | coach.js |
| a Loops panel row (`data-drag-loop`) | drag onto the note grid / the drum grid | its Edit (notes) / Open (a beat), as a click | loopspanel.js (native drag and drop) |
| a Loops panel row, Perform's launcher slot | a row dragged onto a slot of its kind (a beat: the drums'); a slot dragged onto another (Ctrl or Alt: a copy) | place the loop there; move (copy) the slot | slots.js (native drag and drop; loopspanel.js starts the row's) |
| Perform's launcher: a filled slot | click, tap, pen tap; Space; Enter while not selected | select it — its lane edited, nothing launched | slots.js (`POST /fragment/perform/slot/select`) |
| Perform's launcher: a filled slot's ▶ (`.sl-go`); the slot double-clicked; Enter on the selected slot | click, tap, pen tap; double-click; Enter | launch it at the next quantise point (bar, 4 bars, the loop's end: the head's choice); the slot already playing is only selected | the ▶ button's htmx post; slots.js (`dblclick`, the grid's keydown) clicks it |
| Perform's launcher: a scene's ▶, a lane's ■, ■ all | click, tap, pen tap; Enter / Space on the focused one | launch the scene / stop the lane / stop every lane at the next quantise point; an empty slot's ＋ keeps what its lane plays there; ✕ empties a slot | the buttons (htmx posts); slots.js marks them waiting |
| the keyboard dock (every page), not a field | **Alt+T**; the arp panel's ⤓ print | ⟲ keys capture: what was just played as a loop (into the launcher's selected slot on the Loops page); the keys arp's notes alone | keyrec.js (window capture, with Alt+R) |
| Perform's launcher: an empty slot | click, Enter / Space | select it (where ⏺ Rec, ⟲ and print land; again: let it go); its ＋ keeps what its lane plays there | slots.js (`POST /fragment/perform/slot/select`: its lane edited when it has a sound) |
| Perform's launcher grid (one tab stop, a roving focus over its slots) | arrows; **Shift+Enter**; **Backspace** / **Delete** | move; the focused slot's scene; stop its lane | slots.js (window capture, before piano.js's arrows) |
| Perform, not a field | **Alt+1…8**; **Alt+0** | launch the launcher's scene N; stop every lane (the jam's keys on Song: one meaning). In the drum pane Alt+1…4 stay the beat's variations (steps.js takes them first) | slots.js (window capture) |
| Song page | **Alt+J** | Jam on / off: the launcher in the playlist's place (`#jam-toggle`) | jam.js (window capture: before piano.js hears a J) |
| launcher pad (`.jam-pad`), scene head (`.jam-scene`), a row's ■ (`.jam-off`) | click, tap, pen tap; Enter / Space on the focused one | queue that cell / the whole column / a stop of that track for the next quantise point. No drag, no long press, no wheel, no hover meaning (a pad's title says what it is) | jam.js (one delegated `click`; the buttons are real buttons) |
| launcher grid (one tab stop, a roving focus over its buttons) | arrows / Home / End; **Shift+Enter**; **Backspace** / **Delete**; **Escape** | move (an empty cell is stepped over); the focused pad's scene; stop its track; stop all (while no drawer is open — Escape closes that first, song.js) | jam.js |
| Song page, Jam on, not in a field | **Alt+1…9**; **Alt+0**; **Alt+C** | launch scene N; stop all now; capture the jam to the song | jam.js (it leaves Alt+digits alone while the keyboard is inside the drawer: Alt+1…4 are the beat's variations there, steps.js) |
| launcher's scroller (`.jam-scroll`) | wheel; Shift+wheel; a finger | scroll (many sections scroll sideways under the sticky track heads). Never a value | the browser |
| XY pad (`#xypad`), bend strip | drag, arrows | the pad's two axes; the wheel, springing back | piano.js |
| drum XY (`#drumxy`) | drag | the drum pad's axes | perform.js |
| sampler slice marker, splitter | drag, arrows, double-click | resize | layout.js |
| Lab chop edge | drag, nudge, double-click | move / cut a chop | labchop.js |
| section / block / whole-stem edge | drag | resize on the playlist's time axis | songline.js |
| playlist cell (a block) | drag | MOVES it to another cell (Ctrl or Alt: copies); a click opens its drawer | songdrag.js (native drag and drop) |
| playlist cell | **Shift**+click; **Shift**+drag from a block; a drag that starts on an EMPTY cell | a RANGE of cells: the box from the cursor (or the press) to the cell under the pointer — no drawer opens, no block moves | songsel.js (songdrag.js stands down while Shift is held) |
| playlist cell, range mode (`▭ Select` on: `.song-view.selecting`) | a tap; a drag (mouse, pen, one finger) | a one-cell range; a box. The touch and pen path: cells take `touch-action: none`, so the drag selects and scrolls nothing | songsel.js (songdrag.js stands down) |
| playlist grid (one tab stop) | arrows / Home / End; Enter; Delete; D; N; Ctrl+C / V | the cursor; open; clear the cell; duplicate its section; new pattern; copy a cell's pattern by reference | song.js, songcell.js |
| playlist grid | **Shift**+arrows; **Delete** with a range; **Ctrl+C** then **Ctrl+V** at the cursor; **Ctrl+D**; **Alt**+arrows with a range; Esc or a plain arrow | grow the range from the cursor; clear it; copy it there; duplicate it to the right; move it a cell; end it | songsel.js — song.js and songcell.js ask `synthlab.songKeys` first, and with no range it declines |
| playlist grid | **Alt+← →** on a block NOT in a range (Alt+Shift: its end) | slide the block a bar | songline.js (a range's Alt+arrows are songsel.js's) |
| playlist grid | **Ctrl+← →**; **S**; **I**; **T**; **L** | next / previous section; split at the marker; insert 4 bars at the marker; the transition menu of the selected section's boundary; loop the selected range (else the section) | songsel.js reads the key and clicks the tool bar's command (`#arr-split`, `#arr-insert`, `#arr-trans`); the jump is songline.js's, L song.js's |
| a selected cell, a boundary's `+`, a chord tile (`data-ctx-pop`) | right click, pen barrel tap, long press, Menu key | the range's menu (`#range-menu`), the boundary's (`#bound-menu`), the chord's (`#chord-menu`) | menu.js (songsel.js only names the menu on a selected cell) |
| boundary `+`, chord tile, "＋ transition" | click, Enter / Space, tap | that same menu, opened by the `contextmenu` songline.js sends | songline.js -> menu.js |
| the ruler (a slider) | click; ← → / Home; Enter | the start marker (the bar every "at the marker" edit posts); play from it | songline.js |
| the ruler | **Shift+← →**; **S** / **I** | the loop range's far end, a section at a time; split / insert at the marker | songline.js |
| a section head | click; **Shift**+click | select it (the marker to its first bar); mark the RANGE of sections from the selected one to it (the brace on the ruler: what L loops) | song.js; songline.js |
| energy bar (a native range, one tab stop: the selected section's) | drag along it; arrows; wheel when focused; click its number | the section's energy | the native input, tweak.js, tweaktype.js (`POST /song/energy` on `change`) |
| chord tile (one tab stop) — the Song page's, and Perform's chord lane over the roll (`loop-and-perform.md`, "The chord lane") | ← → / Home / End; 1-7; Delete | the next tile; its degree; remove it — each posting the tile's `data-post` (Perform's: `/fragment/chords/track`) | songline.js |
| chord tile's left edge | drag | the beat its chord starts on (the one before gives way) | songline.js (the edge table) |
| the playlist's scroller (`#arr-scroll`, `[data-zoom-own]`) | Ctrl+wheel; a pinch; Ctrl+= / − / 0 | px a bar, about the pointer | zoom.js hears the gesture and sends `zoom-step`; songline.js sets the playlist's own scale |
| the playlist's scroller | wheel; Shift+wheel; one or two fingers | scroll down; sideways; pan (`touch-action: pan-x pan-y`). Never a value | the browser |
| (the five rows above, and the velocity lane) | a pen | drags as a mouse does — no fine, no eraser, no barrel meaning | the same island; pen.js adds nothing |
| all of those, grids, keys, pads, the deck's panel | a pen hovering | `touch-action: none` while it hovers, handed back when a finger or mouse arrives | pen.js |
| piano key, pad | press | velocity from pressure at the strike (`velOf`; a mouse's 0.5 = full) | piano.js |

So a pen's barrel + drag on a slider is a tenth (written once); a dial's
press, the eraser and a double-click land on the same value; the virtual
deck's knobs behave as every other knob (an arrow key is one step;
Shift + drag is a tenth of 160 px).

## Pen

**Pen** (`static/pen.js`, `tests/browser/test_pen.py`): Pointer Events
with `pointerType === "pen"`. It adds no surface; it translates into
what the other islands understand, does nothing for a mouse or a finger,
and **nothing on a value control** (the table above). Driven by
synthetic events, and one journey by a trusted pen through the DevTools
protocol (tip and barrel: it has no eraser) — never by a device.

- **Velocity is written where a drag on the lane writes it**: the
  `.vel-lane[data-step]` under the same grid — bar height, hidden field,
  `aria-valuenow` — at `pointerup` in capture, so it rides the grid's own
  150 ms post. It is per step (the lane's model), only for steps the
  stroke turns ON, and a mouse's constant 0.5 writes nothing.
- **audio.js's one changed line**: a stroke erases for button 5, and for
  button 2 only when it is not a pen (`paintVal`).
- **No scroll, no menu**: on every pen surface `touch-action` is set to
  `none` while a pen hovers (hover precedes contact; `.grid`'s own
  `pan-y` is a finger's rule) and handed back when another pointer
  arrives; a pen's `contextmenu` there is cancelled in capture, before
  grid.js's "right-click erases" — which is what a pen held still on a
  step would otherwise be (Windows' press-and-hold is a right-click;
  expected, never seen on a device). A slider is not in that list (its `touch-action` is the stylesheet's, its menu menu.js's).
  **Unknown on a device**: a pen held still on a *slider* is that same
  right click, so it would open the control's menu.
- **Hover** sets a lane's `title` to its velocity (a slider's value is
  tweak.js's).
- **Not done, on purpose**: pressure on a drawn curve (a point's height
  is its value), pressure as a third XY axis (nothing real to map it
  to), anything continuous after a key's strike (no aftertouch exists).
