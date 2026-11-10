# Frontend: htmx, JS islands, layout

The browser side that is not one feature's own: the vendored htmx 4
stack and its traps, the JS islands and their line budgets, the
workbench (density, splitters, folds), page weight, the CSS grid
gotchas, and the **design system** (tokens, the canonical components,
density and the pen, tweakable controls, commands and shortcuts: the
last section — read it before adding any control). Keyboard and touch rules are in `keys-and-midi.md`; Perform's
own grid is in `loop-and-perform.md`. Index: `README.md`.

## The stack: vendored htmx 4 and its traps

FastAPI + Jinja fragments + **vendored htmx 4.0.0** (`static/htmx.min.js`)
plus the official `hx-prompt` extension (`static/hx-prompt.min.js`, which
htmx 4 moved out of core; panel-preset save, preset rename, bounce, a
take's ★ Keep, a loop's name and voice "keep" use it). It sends
`encodeURI(value)` in `HX-Prompt`: read it only via
`routing.prompt_value(request)`, which decodes it (read raw, "my bass"
would be saved as `my_20bass`). **Every "name it" asks in one popover, never the
browser's `prompt()`** (which opens mid-window with an empty
default): tweaktype.js answers the extension's `window.htmxPrompt` with
`#name-pop.dialog` under the control that asked — the prompt as its
label, the field prefilled with the control's `data-name` and selected,
Enter saves, Esc or a click away cancels. The extension wants the answer
at once, so the first ask opens the popover and answers null (no
request); Save clicks the control again, and that ask answers with the
name — the same request, `HX-Prompt` and all. A new "name it" is an
`hx-prompt` (+ `data-name` for the default): nothing else to write.
**Every "are you sure" asks in one popover too, never the browser's
`confirm()`**: `confirm()` stops every script on the page
while it is open, so the song (song.js schedules 1.5 s ahead) would go silent
into the question, the jam and the click with it. tweaktype.js answers
htmx 4's `htmx:confirm` — whose detail carries `issueRequest` /
`dropRequest` — with `#confirm-pop.dialog` under the control that asked
(in the window's centre when that control is out of sight: a row of a
closed menu), the question as its text, OK focused: Enter sends the
request, Cancel, Esc or a click away drop it. It arrives in the studio's
motion (GSAP: a drop and a bloom, the answers after; none when calm). A
new "are you sure" is an `hx-confirm`: nothing else to write. A browser
test answers it with `conftest.confirm_it(page)` (or `confirm_all(page)`
where the question is not the subject); `tests/browser/test_frontend_contracts.py`
plays the song through an open one.
**`htmx:error` says what went wrong** (audio.js): a request
htmx stopped at its timeout "took too long … and was stopped"; one a newer
one replaced (`hx-sync`) is no error and says nothing; a fetch that never
reached the server says "server unreachable" and turns `#audio-led` red —
the LED is red for that alone; anything else thrown on the page is "that
went wrong in the page: …".
**A control that posts on `change` or `input` says what happens to a
post made while another is in flight.** htmx 4's default for one element
is "queue first": one request runs, the FIRST made meanwhile waits, and
every later one is dropped — so a knob stepped faster than the server
answers (a held arrow key, wheel ticks, a MIDI CC, a deck dial) leaves
the server on the second value while the page shows the last (measured:
a level at 0.75 on the page and 0.90 in the session after five presses).
The rule, held over the template sources by
`tests/studio/test_change_posts.py` and in a browser by
`tests/browser/test_rapid_change.py`:

- a control or form that sets a VALUE: `hx-sync="this:queue last"` — the
  last post waiting is the value the hand left. Debounced forms too
  (`input delay:150ms`): the debounce spaces posts 150 ms apart and a
  render takes longer;
- one whose every post is an ACTION (a toggle, an "add…" menu, a file
  chosen): `hx-sync="this:queue all"`, and a row in that test's
  `ACTIONS` saying why;
- radios of one group share a queue (`closest .seg:queue last`): each
  input is its own element otherwise.

Known htmx4 traps are documented in `docs/htmx4-upgrade-checkpoints.md` —
notably the `changed` trigger modifier is broken on container triggers; never
use it. Two htmx 2 names htmx 4 no longer reads: `hx-disabled-elt` is
`hx-disable` (the element is disabled while its request runs: the Lab's
auto chop, a double-click is one cut), and `hx-boost` is gone. `tests/studio/test_frontend_contracts.py` refuses both. Vendored files are excluded from the island line budgets by name in
`tests/studio/test_island.py`.
Two more, found by the browser smoke suite (`tests/browser/`, root
`CONTRIBUTING.md`, "Browser smoke tests"):

### `htmx.ajax` posts the whole page

**`htmx.ajax` posts the whole page.** With no `source` it posts from
`<body>`, and a non-form source sweeps in every named input under it; only
the names in `values` are replaced. Extra fields are harmless where the
route ignores them, but a post that means "no ids" must send them empty:
song.js's Esc posted nothing to `/song/drawer`, the open drawer's own
hidden `section_id`/`track_id` rode along, and Esc reopened the drawer
(`NONE` in song.js). A detached `source` is no way out: htmx drops the
request (`isConnected`). What is a way out, when the route *reads* a
name some other form on the page uses: **post from a connected element
with no field under it**. songdrag.js and songline.js post from the Song
page's `<h1>` — `/song/long` takes `name`, `bar`, `bars` and `gain`, all
of which the section menus and the whole-stem rows also name, and a
dragged stem came back renamed after whatever `name` input came first in
the page.

**`synthlab.post(path, values, opts)`** (audio.js) is the way out for a
post that means only its `values`: it posts from `#post-from`, an empty
element in `base.html`, with `swap: "none"` (the XY pads, the bend curve
and range, the sampler slice move, the Play fallback, the jam's capture,
songdrag.js and songline.js). A post that wants one form's fields gives
that form as `source` (the Lab's region and chop posts).

### `synthlab.send`: a post whose body htmx cannot build

**`synthlab.send(path, body, { target, swap, refused, done, timeout })`**
(audio.js) is the one post
an island makes by hand — a body htmx cannot build: a recorded take, a
WAV, the coach's mic, a typed chord line. It is answered as htmx answers
its own requests, once: the busy LED (`#audio-led.busy`) while it runs;
the answer swapped through `htmx.swap` — its OOB regions (`swap: "none"`,
the default) or into `target` (the Voice lab's panel, `outerHTML`; the
live vocal panel, `outerMorph`); a JSON answer is not swapped; its
`HX-Trigger` dispatched on `<body>` (a `studio-toast` toasts through
perform.js's listener, `song-changed` reaches song.js); a refusal in the
server's own words (a 422's toast fragment, else `refused (status)`); a
dead server ("server unreachable", the LED red) or the timeout (htmx's
`defaultTimeout`) said once. `done(headers)` is the toast when the answer
brings none (keyrec.js names the loop it made from `X-Loop-Name`). It
resolves to `{ headers, text, trigger }`, or null when refused or failed.
Its callers: audio.js (the jam's resample), coach.js, keyrec.js,
loopspanel.js, shaper.js, songrec.js, songtake.js, talkbox.js, vocal.js,
voice.js, chords.js — none fetches, swaps or reads `HX-Trigger` itself
(`tests/studio/test_island.py::test_one_send_for_the_posts_islands_build_by_hand`).
**A GET an island wants swapped is `htmx.ajax`**, from `#post-from` (so
no field rides along): the chord poster's first load (chords.js), a free
take's shaper (shaper.js), the sound menu again (soundmenu.js, swapped
only when it differs — an `htmx:before:swap` that cancels an identical
answer) — never a bare `fetch` into `innerHTML`, which has neither the
page's headers nor an error's toast.

### A swap that replaces the control under the keyboard loses focus

**A swap that replaces the control under the keyboard loses focus.** An
`outerHTML` swap (targeted or OOB) destroys the focused slider and focus
falls to `<body>`: an arrow-key nudge works once. Morph instead, as the
body swaps do — the FX rack's parameter posts (`hx-swap="outerMorph"`),
the Song drawer's and the Song topbar's OOB redraws
(`hx-swap-oob="outerMorph"`), like the layer strip and the chord bar.
audio.js's `syncControls` then re-asserts every live value from the
attributes (`views-and-regions.md`, "Swaps re-assert live values").
Two things a morph does not save you from: a button that comes back
`disabled` while it has the keyboard loses focus anyway (the Song
topbar's undo / redo are `aria-disabled` and their click is swallowed
in songedit.js), and state a script wrote onto a morphed element is
reset to the response's (the transport's `aria-pressed`: put back
after the settle).

### When the swap must replace: the server names the next focus

**A morph cannot say "focus the thing that was just made".** The FX
rack's add / move / remove change the rack's shape and replace it
(`outerHTML`), which destroys the control that was pressed; and units
have no identity to match a moved one by (a chain is a list of `{kind,
params}`, and an id on each would change what a session saves — only a
unit a song's automation lane targets carries one: `song.md`,
"FX-parameter lanes"). So the
route, which knows what it did, names the control that gets the
keyboard next — `fx_focus = (scope, index, which)` in `routes/fx.py` —
the template draws that one control with `autofocus`, and **htmx's own
settle focuses it** (htmx 4 focuses the first `[autofocus]` in
swapped-in content; it also re-focuses a focused element *with an id*
across an `outerHTML` / `innerHTML` swap). After add (and "+ add
chain…"): the new unit's first parameter (its remove button if it has
none). After move: the same button on the unit that moved, at its new
index — pressing it again carries the unit on; at the end of the chain
nothing moves and the button keeps focus. After remove: the remove
button of the unit that took its place, else the scope's adder. (What
is replaced, or morphed, is the scope's own fieldset,
`#fx-scope-<scope>`, picked from the same answer by `hx-select`: a rack
moved anywhere on the page is redrawn where it stands — "Workspace".) No
script restores anything (`test_fx_rack.py` pins which control, per
operation; `tests/browser/test_live_keys.py` drives it from the
keyboard). Use this, not a JS save-and-restore, for the next swap that
has to replace.

### Back, Forward and a link to another page: the shell stays

**A link to another page swaps it, never loads it**: a full
load stops the audio, the held keys and the song. Every in-page `<a>` to
`/view/...` is `<a class="…" {{ view_link("/view/song") }}>` — the
template global `views.view_link` writes `href` (a new tab, Ctrl+click, a
cold start still work) and `hx-get` / `hx-target="#view"` /
`hx-swap="outerHTML"` / `hx-push-url="true"`; the rail's buttons use it
too. Not a page-wide `hx-boost`. A link that only looks like one (the clip
hub's `/view/clips?item=` rows, whose `hx-get` is their bench fragment)
keeps its own `hx-get`. **Back and Forward bring the page back inside the
shell**: htmx 4's history restore GETs the URL and swaps it into
`[hx-history-elt]` — else the whole `<body>` — and `/view/{name}` answers
an htmx GET with the view alone, so without it Back would leave the bare
view with no rail, no docks and no toasts. Every view's `<main id="view">` carries
`hx-history-elt`; the answer to one URL differs by `HX-Request`, so both
carry `Vary: HX-Request` (a cache must never hand the fragment to a cold
load). **The tab says the page**: `<title>` is `views.view_title(view)`
("Kit · synthlab studio") on a cold load, and a view swap carries the
same `<title>` first, which htmx applies (it skips an `<svg>`'s titles).
`tests/studio/test_frontend_contracts.py` holds the sources;
`tests/browser/test_frontend_contracts.py` goes Synths → Kit → Back → Forward
and finds the rail, both docks and the running AudioContext each time.
**The rail lights the page as its swap begins** (audio.js, on
`htmx:before:swap`, from the incoming `#view`'s `data-view` in the swap's
main task): lit after the swap, it would lag a page transition by the
transition's length. It stays a toggle in audio.js rather than the rail
riding the view's answer out of band: the rail is
base.html's, outside every region, and an OOB copy would re-send twelve
links with every page for what six lines do at the same moment.

### The page transition: the old page leaves, the new one arrives

**A move to another page — the rail, a
`view_link`, Back / Forward — is a motion moment, not a cut**
(`static/pagemotion.js`, style.css 48). The top dock and the keyboard
dock are the instrument's frame and stand still, live (a held note
sounds and stays lit through it); the page between them changes:

1. **The old page leaves on the compositor.** pagemotion.js marks every
   task of a swap into `#view` that brings another page `transition`
   (htmx 4's view transition; no link asks for it, so the rail, the
   links and the history restore all get it), the OOB regions too —
   swapped before it, their settles ran each island's work against the
   OLD page (37 ms of style on Synths before the capture). While it runs
   it names three elements inline: `#view` `page-down` or `page-up`
   (further down the rail is "down"), `.topdock` and `.keys-dock`. The
   old page's snapshot slides 34 px against the way you travel, dims a
   step brighter and fades over 240 ms; the docks' snapshots are hidden
   and their live selves shown, unanimated. No name stands at rest (a
   name on `#view` is a stacking context: a floating module would sit
   under the keyboard dock), and none hangs on `<html>` (an attribute
   there restyled the whole page each way, 12 ms on Kit).
2. **The new page arrives under it, at once.** htmx 4 settles a
   transition's swap inside the transition, so the islands (the
   workspace's layout, folds, tabs, the dock filter) have laid the page
   out before its first frame. GSAP brings its outermost modules in sight
   (up to 40) in the order they stand from the direction of travel — 30
   px up (or down), 12 px from the rail, 3 % small, over 0.3 s each,
   spread over 0.13 s — each igniting as it lands (a brightness flare, a
   filament by night and a glint by day; a panel's glow layer lit,
   `--ignite`), a band of the theme's glow sweeping the page the same way
   (`.pm-sweep`, under both docks, screened), and the knobs, faders,
   meters and velocity bars in sight (up to 96: `--fill`, `--level`,
   `--v`) sweeping up from nothing to their values a beat behind their
   module. The cascade waits for the transition's first frame (`ready`):
   the swap's own long task ate its first 150 ms otherwise.
3. **Fast to use.** About 420 ms from the first frame. Input is never
   blocked: `::view-transition` lets the pointer through to the live
   page, and nothing waits on the motion; a press or a value moved in the
   page lands the motion at once; a second move mid-sequence lands this
   one (the cascade at its end, every inline style it wrote taken back)
   and skips its transition, so the next page comes in clean. A skipped
   transition's `ready` rejection is caught. htmx awaits a transition's
   `finished` before the swap's `htmx:after:swap` and its next swap —
   the keys typed on the new page would play nothing for the leave's 240 ms —
   so pagemotion.js wraps `document.startViewTransition` and hands htmx
   the swap done (`updateCallbackDone`) as `finished`; the leave runs on
   by itself.
4. **Calm motion keeps a plain swap** (no transition is asked for, no
   cascade), as does a hidden tab. The cold load has no entrance.

**GSAP tweens plain proxies; `onUpdate` writes the styles** — opacity,
`translate`, `scale`, `filter` and `--ignite` on the module, `will-change`
while it moves — and nothing computed is read per module: the boxes are
read once, before any write (one layout, the one the capture needs
anyway). `gsap.from` read each module's transform first, a forced layout
a module, which is why the entrance is not a `gsap.from`; a Web Animations
entrance cost 35 ms a section and is not used either (motion.js, "view
entrances").
**Measured** (Playwright Chromium on the RTX 3080 through ANGLE/D3D11,
1920 x 1080 dense, Kit ⇄ Synths three times under a trace): one long
task a move — the swap itself (htmx's swap, every settle and its
autofocus inside the transition's callback: 77-78 ms to Synths, 98-133 ms to Kit; calm splits the
same work over two or three tasks of 60-89 ms; 568 ms of long tasks over six moves against 478) — and none during the
transition: the cascade's frames restyle ~5 ms each. Three costs it
avoids: the OOB settles on the old page
(above), the `<html>` attribute (above), and two islands' writes of what
had not moved (audio.js `setPlayButton` and the dock filter's counts: a
text node replaced restyled `#view` through a `:has()` rule, 12 ms style
+ 13 ms layout; kitpads.js asking a drawn pad's visibility, a forced
layout each settle — it asks once a frame, drawn pads first).
`tests/browser/test_page_motion.py` holds it: the cascade runs and lands
clean, a second move mid-sequence, a held note through it, a click
through it, calm keeps a plain swap.

### A menu that drops down: a popover

**A dropdown menu is a popover, never a `<details>`**: the
pattern tools' menus (`.pt-menu`: vel, copy ▸, fill ▾, ⚄ write, vary ▸),
a drum row's ⋯ (`.row-menu`), the reset menu and a macro's assign. A
`<details>` never closes — no Esc, no click away — and a
panel's `overflow: clip` cuts it off. So: the button carries
`popovertarget="<id>"` (which makes it the panel's implicit anchor:
`position-area` places the panel under it, flipped where there is no
room — no inline `anchor-name` / `position-anchor` pair, and no
`@supports` fallback: Chromium is the one browser), the panel is
`popover` (auto: Esc and a click away close it; the top layer: nothing
clips it), and a row that runs an action closes it as it posts
(`popovertarget="<id>" popovertargetaction="hide"` beside its
`hx-post`) — a drum row's tools stay open, they are pressed again and
again. A popover's open state is the element's, so a region holding one
is morphed, never replaced (the reset menu's region is
`hx-swap-oob="outerMorph"`: an edit elsewhere redraws its dots with the
menu open). menu.js's arrows walk a `.menu` popover's `.menu-item`
rows, ↓ / ↑ from its button too, and Esc gives the button the focus
back. Ids are unique per page: a macro rendered twice names its menus by
its `target` (`ptm-dvel-drum-bank`, `ptm-dvel-loop`). The rule's CSS is
one block (style.css section 15, "a menu that is a popover"). The
`<details>` that stay are disclosures that open in place (a rack, a
loop row's Quantise and → Song picks, the Lab's chop clean).

### A drawer that must stay open: a popover, morphed

**The clip library is the pattern for a panel that lives outside
`#view`** (`fragments/clip_library.html`, in `base.html`'s top bar):
`popover` + `popovertarget`, so no script opens or closes it and Esc is
the browser's; every swap of it is `outerMorph` (targeted and
`hx-swap-oob`), because the open state belongs to the element and a
replaced element is a closed drawer; a button that should close it as
it posts carries `popovertargetaction="hide"` beside its `hx-post`
("⤷ sampler"); and a control that should open it *and* refresh it
carries `hx-get` beside `popovertarget` (the rail button — htmx does not
cancel a plain button's click). It is an auto popover: a click outside
closes it, the nav included. A GET from an element that is not an input
sends none of the page's fields (only `hx-include`d ones), unlike a POST
from `htmx.ajax` above. Checked in Chromium under the CSP, in the
compact side bar, the comfy top bar and a touch context
(`tests/browser/test_clips.py`).

## JS islands and their line budgets

Eighty-one budgeted JS files (`tests/studio/test_island.py` pins the
count; `pagemotion.js`, <= 120, is the page transition — "The page
transition" above — loaded after motion.js; `pagefill.js`, <= 200, is the filled pages' live parts — the Song editor's rows fitted to its pane and its ghost
repeats, the Mix master's read-outs, a Breed litter's entrance: `song.md`,
"Song view" and "The mixer"; `sounds-and-breeding.md`, "What a card
changed"; `soundmenu.js`, <= 155, is the one sound menu — a Song lane's
list, Perform's lane heads' searchable sound ▾ and what a load
replaces, Synths' Load's question: `loop-and-perform.md`, "The lane
heads"; `letters.js`, <= 91, is the keyboard dock's letters layout — A–G
play that note, the modifiers held make the chord on it: `keys-and-midi.md`,
"Letters" — and the typing layouts' switch, loaded after piano.js and
before chords.js, whose window capture it must come before; `columns.js`,
<= 65, is the third layout — each keyboard column a root, its note, major
and minor, the number row a choice: "Columns" there — loaded after
letters.js (it reads the switch) and before chords.js; `workspace.js`, <= 645, is the workspace — modules moved, tabbed,
folded, floating, focused, a page's layouts and added modules: "Workspace:
modules you can move" below — deferred (in `<head>` without `defer`, its
45 KB would hold the parser): colours.js, which
blocks, hides `#view` (`data-ws-wait`) on a page with a saved layout
until workspace.js has drawn it, so the first paint is never the
server's order; the Sample Lab's three are `lab.js`, <= 130 (picking a sample:
the selection on any lane, its edges, its keys, a click to hear),
`labview.js`, <= 200 (the lanes as a window on the song: Ctrl + wheel,
the overview, the ruler and the lines) and `labchop.js`, <= 90 —
`sample-lab.md`, "The Lab's islands"; `visuals.js`, <= 350, `visuals2.js`, <= 450 (more
variations), and `vispage.js`, <= 205, are the Visuals page's — the
registry of the top dock's scope / spectrum variations and the page that
shows them: "The visualiser registry" below; `colours.js`, <= 101, is the palette's theme and hues — the Colours popover — and the workspace's first-paint wait, loaded without `defer`, in `<head>` (the one script that blocks): "Colour: hues and depths"; `fxcables.js`, <= 86, draws the FX racks' patch leads from where
the modules are laid out — a sleeve, a core and a sheen, a plug each end,
beside or down to the row below; `decksets.js`, <= 75, is the deck's dial sets — `keys-and-midi.md`,
"Which four"; `samplerec.js`, <= 110, is the sampler panel's ● Record sample —
a mic take timed to the session: `sampler-and-clips.md`, "Record a
sample"; `keyrec.js`, <= 110, is the keyboard dock's ⏺ Rec — what you
play, the master bus, into Clips live: `keys-and-midi.md`, "The keyboard
dock"; `transport.js`, <= 190, is the transport's — the registry every
player reports to, the top dock's "now playing", the cue beside each ▶,
Space and Shift+Space: "The transport is the same on every page" under
"Design system"; `mixer.js`, <= 145, is the Mix view's — the strips' meters lit
from the plan's `levels`, the focused strip (every control its own tab
stop and its own arrows), a finger's double tap: `song.md`, "The mixer"; five are song creation's — `roll.js` <= 290 (the piano
roll's tools, selection and keyboard), `zoom.js` <= 70 (any `[data-zoom]`
scroller — both in `loop-and-perform.md`, "The piano
roll"), `steps.js`
<= 270 (the step sequencer, 257 used — the
per-hit table, the selected row and the lane switch, the cursor and its
keys, the wheel, the inspector's and the rows' knobs, the hit menu and
the step LEDs' marks: `drums.md`,
"The step sequencer"), `songsel.js` <= 120 (the
playlist's range of cells and the range loop's pure half `songRange`,
120 used — `song.md`, "Playlist tools"), `jam.js` <= 240
(the jam's launcher: its groups, quantise, per-track sources and
meters, the log and the grid's roving focus; its pure half
`jamSchedule` runs under node; 238 used — `song.md`,
"The jam"); song.js holds
`song-autoplay` — what a starter or a generator made plays by itself,
a new song on its head plan: `songcraft.md`; one is the bench views' (`bench.js`, <= 135 — the
mod matrix's patch-bay leads and its jack-to-jack drag, the shared
`.player`, the docked agent drawer: `synths.md`, "The bench tiles the
window"); `menu.js`, <= 90, is THE context menu, the only one — "One context menu"
under "Design system" below; four are the shell's (`palette.js`,
`shell.js`, `tweak.js` and `tweaktype.js`,
<= 60: a value typed into a control's read-out, on every view; "Design
system" below), six are hardware control — `deck.js` <= 230,
`deckhid.js` <= 125, `deckui.js` <= 300, `deckart.js` <= 420 (both
decks' look and motion), `midilearn.js` <= 80, `pen.js`
<= 110: `keys-and-midi.md`, "Control surfaces", "Pen" and "Gestures: one
owner each", which says which island owns which gesture. They talk to
the other islands through the DOM — a button's click, a command's
declaration — `window.synthlab.deck` / `midiLearn`, and **one write**,
`window.synthlab.tweak`; their two
touch points elsewhere are one line in midi.js and one in audio.js's
stroke). The first ten are the islands proper (`audio.js`, `keys.js`, `piano.js`, `space.js`,
`perform.js`, `midi.js`, `chords.js`, `voice.js`, `song.js`, `motion.js`;
also `grid.js` and `layout.js`, see "Loop
editor" in `loop-and-perform.md`; `livefx.js`, the key engine's graph, beside the
two AudioWorklet files `voice-worklet.js` and `fx-worklet.js`;
`eq-worklet.js`, `sampler-worklet.js`, `grain-worklet.js`,
`songrec.js`, `songcell.js`, `events.js`, `lab.js`, `songedit.js`,
`songdrag.js`, `songline.js`, `labchop.js` — the Sample Lab's chop markers, <= 90:
`sample-lab.md`, "Chops" — and `click.js` — the metronome: `#click-btn` in the rail,
a tick on every beat — of a Song take running (unless its own
`#rec-click` ticks it), the Song playing (`songClock`'s pass and the
plan's bpm), the loop (audio.js's `loopStart()` and the page's
`input[name=bpm]`), and with nothing playing **on its own** at the
page's tempo, bar 1 where it was switched on (on is heard) — straight to the context's destination so a
jam never records it; it does not follow Perform's tape-stop / half /
double; and
the shell's, which belong to no view — `palette.js` (<= 155: the
command palette and the shortcut sheet's generated half), `shell.js`
(<= 95: tooltips, the clip library's pin, the status strip's progress
line), `tweak.js` (<= 95: the pen, and what every slider and knob
shares) and `tweaktype.js` (<= 60: its second file, the typed value) —
see "Design system" below),
each with a stated line budget in its header. `motion.js` (<= 265:
"The energy" and "Motion: full or calm" under "Design
system") owns
everything that moves without sounding: the scope/spectrum (live whenever
the output carries signal — keys included, loop or not — else the current
preview's waveform overview and log-band spectrum, decoded on an
OfflineAudioContext so it draws before any gesture; ResizeObserver-sized,
no per-frame layout reads, paused while hidden — it
measures and `visuals.js` draws: "The visualiser registry" below), the
peak meter, `--live`, and the UI motion on **GSAP** (the page's entrance
is pagemotion.js's) (`static/gsap.min.js`, served from `static/` — CSP is
`script-src 'self' 'wasm-unsafe-eval'`; excluded from budgets by name). GSAP
is not in the repository (its licence is not open source): the first
`synthlab-studio` launch fetches the pinned npm package and checks it
(`studio/vendor.py`); until it is there `base.html` leaves the script out and
every island runs without the motion. Every tween is
skipped under calm motion ("Motion: full or calm", below); flashes animate `--flash`, a
box-shadow ring, never layout. audio.js exposes `getAnalyser`/`lastBuffer`
for it. The ninth, the Song island, is six
files (seven with `songsel.js`, above): `song.js` (<= 310, the player
and the playlist's keyboard grid; 289 used — the range loop and the
section jump are edits of lines it had, plus two), `songrec.js`
(<= 200, recording), `songcell.js` (<= 110 since a section's menu stays
open across its own posts — pattern making: see
"Pattern making" under Song view, `song.md`), `songedit.js` (<= 110,
the song's undo / redo keys and the drawer's "solo this cell" audition:
`song.md`, "Undo and redo" and "Solo this cell"; its pure key rule
`songEdit.key` runs under node in `test_song_undo.py`), `songdrag.js`
(<= 140, every drop on a lane of the playlist — a pattern, a block, a
clip, a take, a whole stem: `song.md`, "Drags onto a lane"; it talks to
the page only through the DOM and htmx posts) and `songline.js` (<= 300,
the playlist's time axis: zoom, the ruler's start marker and
`songFrom()`, following the playhead, the edges of a section, a block, a
whole stem and a chord tile, and — the playlist tools — the section
jump, the range brace, the chord tiles' keys, the click that opens a
boundary's or a tile's menu and the audition of a placed transition —
`song.md`, "The playlist" and "Playlist tools"; its pure half `songLine`
runs under node in `test_song_playlist.py`; its budget is raised with
the code, never the code squeezed), which talk only
through `window.synthlab`
(`songClock()` — with `jump(sid)` —, `songFrom()`, `songMark(bar)`,
`songSend(path, values)`, `songJump(±1)`, `songHeads()`,
`songKeys(e, grid)`, `songRange`, `songCountIn`, `onNoteOn` — piano.js's
one-line capture hook — and `voice.capture`). `song.js` is the Song
page's player: it plays `/song/plan.json` pass by pass
(`url` once, then `loop_url`) on audio.js's context through `getMaster()`,
re-fetches the plan on every `song-changed` and swaps an edited pass at
its next start; its pure schedule (`window.synthlab.songSchedule`) is run
under node by `test_song_schedule.py`. On the focused arranger grid it
claims D (piano.js's E) in window capture; L goes through
`keysBlocked` (Space is transport.js's: the page's ▶, its ⏸ while the
song plays). `voice.js` is its own island on purpose: mic capture
and codec work belong in neither audio.js nor a view island. `keys.js`
is one too (moved out of audio.js, which sat at its budget):
the key voice — key-buffer cache, the WAV-fallback key bus (live
filter/gain, a voices^-0.25 polyphony gain under ONE level, the loop playing or
not — the keys must not fall in level when the loop starts —, per-bus limiter), held-note
loops and release, one-shot pads. audio.js keeps the AudioContext, master
chain and transport and exposes them (`getCtx`, `getMaster`, `liveOut`,
`isPlaying`, `fetchBuffer`) — and the one clock every
take and launch reads: `tempo()` (the sound bar's `#t-bpm`, else the
page's bpm field; 0 none), `outLatency()` and `heardTime()` (the
context's time as the speakers are at it), `nextBar(now, lead)` (the
playing loop's next bar line, launch quantise's; null with no loop) and
`toastOf(html, fallback)` (a 422's own toast fragment as text). keyrec.js,
samplerec.js, songrec.js, songtake.js, song.js, jam.js, deckart.js and
chords.js read these rather than re-derive them; keys.js reads them live and publishes
`playKey`/`releaseKey`/`warmKey`/`playUrl`/`liveMod`/`flushKeys`/
`limiter`/`setPoly`/`setKeyPatch`/`keyBus`/`countVoice` onto
`window.synthlab` with `Object.assign`, never a
reassignment. It loads after audio.js and before piano.js in `base.html`.

`cliphub.js` (<= 60) is the clip hub's (`clip-hub.md`): the bench
player's ⟲ (the `<audio>`'s loop, kept across a tune's redraw, and a
player that was playing plays on), the row just opened marked selected, a
make's preview played when it lands, one hub player at a time, and Ctrl+Z
as the hub's ↶ Undo (window capture, ahead of piano.js's patch undo). The
players are bench.js's `.player` and audio.js's `.audition[data-note]` —
the two helpers a transport read-out can hook; it owns no state the
server needs.

`library.js` (<= 72) is the Library page's
(`sounds-and-breeding.md`, "The page"): the filter (the words and the tag
chips, each chip's `data-n` — how many it would show — a family it empties
marked `.lib-nomatch`, each family's "6 of 53", re-applied after every
swap), the arrow keys across the grid of cards, cards or rows
(`html[data-lib-view]`, localStorage `synthlab.libView`) and the playing
lamp (`.playing`, from the `audition-start` event audio.js sends a card
with keys.js's buffer source).

`wtdraw.js` (<= 80) is the custom wavetable editor
(`sounds-and-breeding.md`, "Phase warp, custom wavetables, voice and glide modes"): the "draw…"
popover's four frames of 64 points, set by a pointer or pen dragged over
its canvas (the points between two moves joined) or a fill button, the
frame tabs, and after every change the hidden `points` field holding the
first `count` frames as the route reads them — so the form posts as it
is, no script on submit. The stroke's colour is the canvas's own `color`
(a token, `--signal-text`). Its home would have been panelfaces.js, at
its budget.

`zonemap.js` (<= 112) is the sampler's zone map
(`sampler-and-clips.md`, "Zones: a multi-sample instrument"): a box's
edges or the box itself dragged over the keys x velocity plane and
posted once a gesture, the arrows on a focused box, a box and its row lit
together, the zones a played key sounded flashed (`synthlab:zone`, the
live voice's own pick). Its home would have been layout.js (the slice
markers'), near its budget.

`panelfaces.js` (<= 120) is the drawn controls on
the Synths panels (the oscillator panels; the drawn-LFO editor: `synths.md`,
"The Modulators panel") and the FX racks' chain browser
(`sounds-and-breeding.md`, "Oscillator panels" and "Chain presets"): a
click on an FM4 algorithm's drawing and a ratio preset, both through
`synthlab.tweak` (it loads after tweak.js); the wavetable drawings'
`--wt-pos` from the position slider and after every settle; the chain
browser's search, its purpose / genre regrouping (rows moved under the
other headings, localStorage `synthlab.fxChainsBy`), focus to the search
on open (in a `toggle` listener: an `autofocus` there would be focused by
every htmx settle of the rack) and ↓ / ↑ / Home / End / Enter. What can be
CSS is: the lit algorithm and its carriers' lamps are a `:has(option:checked)`
rule editor.html generates, a heading with nothing under it hides by
`:has()`, the wavetable frames' fade is CSS math on `--wt-pos`. bench.js,
audio.js and fxcables.js, the nearest homes, sat at or near their budgets.

### The visualiser registry (`visuals.js`) and the Visuals page

The top dock's two canvases are **slots**: the scope slot (`#scope`) and
the spectrum slot (`#spectrum`) each draw a **variation** chosen on the
Visuals page (`/view/visuals`, the rail's last entry, no jump key;
`views/visuals.html`, style.css 36), per browser (`localStorage`
`synthlab.visuals`, guarded), on every page at once and after a reload;
another tab's choice follows (the `storage` event). The Classic scope and
Classic spectrum are the defaults.

- **One analyser, read once.** motion.js reads the master analyser (the
  one audio.js feeds) once a frame — the time domain, the bins, the kick,
  the 72 log bands — into one frame object `F`, then calls
  `synthlab.visuals.render(F, scope, spectrum)`. visuals.js draws the two
  slots and hands the same frame to its hooks (`visuals.each`: the
  gallery). Nothing else reads the audio per frame (deckart.js has its
  own tap) but **the goniometer's two sides** (visuals2.js `sides`): an up-mix to two channels (a mono source reaches both, as
  the speakers would play it), a splitter and two 1024-point analysers
  off the master analyser, made the first time a stereo variation draws
  and read once a frame after that. The other per-frame reads several
  variations share are worked out once a frame too (`once`): the
  dominant pitch (an autocorrelation decimated to 11 kHz; with no clear
  period — a chord — the loudest partial; held over a 250 ms gap) and
  the chroma (the bins 200 Hz..4 kHz folded onto 12 pitch classes).
- **A variation** is `add({ id, name, kind: "scope" | "spectrum" |
  "both", draw(g, f) })` in visuals.js, or in **visuals2.js** (visuals.js
  sits at its budget), which registers
  through `synthlab.visuals.add` and draws with visuals.js's shared
  strokes (`visuals.kit`: `trace`, `curve`, `fade`, `trail`, `glowLine`,
  `graticule`, `pair`); a stored choice of one of its entries is taken
  as it registers. `f` carries `wave` (Uint8, 128 =
  silence), `freq` (live only), `bands` (0..1, falling slowly), `peaks`
  (the preview's overview, or null), `isLive`, `live`, `kick`, `onset` (a
  kick began this frame), `rms`, `peak`, `beat` (0..1 through the beat —
  the loop's at the page's tempo, or the demo's — or -1), `dt`, `rate`,
  `w` / `h` / `dpr`, `c` (the theme's role colours) and `s`, this
  canvas's own state (trails, caps, particles), new — `f.fresh` — when the
  variation or the canvas size changes. `kind` says what it is drawn from;
  any variation may fill either slot.
- **The rules motion.js kept**: at rest nothing draws per frame;
  no layout reads; arrays and offscreen layers reused (a trail, the
  needles' faces); colours through **one helper**, `ink()` — a hidden
  probe resolves each role token (`--signal`, `--accent`, `--text`,
  `--text-dim`, `--bg-inset`, `--bg`, `--line`, `--danger`) as the page
  does and a 1 px canvas turns it into rgb, so a literal, a `var(--hue-…)`
  or a `color-mix()` all work — again only after a theme change. No raw
  colour in the file: alphas are built from the roles (`rgba(c, a)`).
  **No `ctx.font` on a canvas in the document**: Chromium resolves the
  page's style to set it — a style recalc whenever anything moved since
  (motion.js writes `--live` every few frames), ~1.5 ms, paid by
  whichever draw sets a font first. visuals2.js stamps its text from
  sprites drawn once on an `OffscreenCanvas` (`text`); the needles paint
  their faces once a theme.
- **At rest: never a dead display** (a blank canvas at rest would leave
  the Visuals page a wall of black tiles). `visuals.js` `restSound`
  fetches one note of the sound on Synths (`/note/C3.wav`, on load and
  700 ms after each `preview-ready` — an edit, a sound loaded), runs it on
  an `OfflineAudioContext` through an `AnalyserNode` like the master's
  (2048, no smoothing — the one analyser of the registry's own, offline:
  `tests/studio/test_visuals.py`) and reads its time domain and bins
  0.3 s in: a frame as the live analyser would give it, the bands by the
  same 72-band mapping (`edgesOf`). `still` draws each variation from that
  frame as if live — ten frames at 50 ms, so springs, trails and stacks
  settle — into an offscreen canvas, kept per (variation, size, theme,
  note); before the note is read, the variation's own at-rest branch over
  motion.js's rest picture (the preview's overview and bands). `rest`
  puts it on the canvas once: **the last live frame stays and fades**
  into the still (a GSAP tween, 1.6 s; a still replacing a still 0.7 s),
  drawn at 66 % (`REST_A`) — plainly a still. **Sound starting**
  (`wake`) flares the canvas (GSAP: brightness 2.4 to 1 over 0.9 s) and
  it runs live again. Under calm motion the still lands at once and
  nothing flares. Nothing runs per frame at rest: a still is drawn when it
  changes (a theme, a size, a new note) and the fade's tween is the only
  timer. A card's cost before any sound is its still's last frame.
- **Cost**: every draw is timed (`performance.now()` around it, a moving
  average per canvas: `visuals.cost(canvas)`, `visuals.costs()`), and
  each card shows its number, red over 1 ms. It is the time to *record*
  the frame's commands; the GPU's raster is not in it. Measured headless
  on an i9-14900K at a card's size (≈ the dock's), the demo playing: the
  two trails dearest (Phosphor ~0.64 ms, Lissajous ~0.3: a fade and a
  layer composited each frame), every other under 0.12 — the needles
  paint their faces once a theme, not every frame (0.7 ms otherwise).
  A canvas this small is rastered on the CPU, so the raster is in the
  number after all (the goniometer's dots as a path cost ~1.5 ms where a
  near-mono mix stacks them, so they are pixels in an `ImageData` of
  their own). A state's first frame (layers built, a tap made) is not
  timed. The browser journey holds every variation under 1 ms.
- **Adding one**: an `add({...})` in visuals2.js (or a third file the
  same way, in `base.html` after visuals2.js) and a row in
  `studio/visuals.py` `VARIATIONS` (the card: name, kind, a line, its
  family) — `tests/studio/test_visuals.py` holds the lists to each other
  — and the browser journey (`tests/browser/test_visuals.py`) checks that
  it inks its card, under 1 ms, and holds a still at rest.
- **The twenty-nine**, by family (the gallery's groups, `visuals.py`
  `FAMILIES`, each a head and a grid):
  - *Scopes*: Classic scope, Phosphor, Lissajous, Triggered scope (held
    on a rising zero cross after a dip, four cycles of the pitch: it
    stands still), Goniometer (L / R as mid up and side across, a pixel
    phosphor; the correlation, red below 0), Polar scope (six cycles
    wound round a ring, turning on the kick), Afterimage (a Lissajous
    whose last seven figures cool from the signal colour to the accent),
    Horizon (wave slices flying in from a horizon).
  - *Spectrums*: Classic spectrum, Mirror bars, Breath, Radial,
    Waterfall, Ridgeline, Spectrum surface (the last second of spectra in
    pseudo-3D, turning slowly), Reflection bars, Bloom (additive halos:
    `lighter`), 1/3-octave (the 28 ISO centres 31.5 Hz..16 kHz, peak
    hold; tags from 40 px), Brightness ribbon (the spectral centroid over
    time, as wide as it is loud), Chroma wheel (the circle of fifths
    beside 12 named bars, the strongest in the accent).
  - *Meters*: VU needles, LED ladder, LCD panel (seven segments with
    their unlit ghosts: the note — D and B lower case, a flat a small b —
    the peak in dB, two block bars), Beat grid (sixteen steps on the
    beat clock, lit where the kicks and the hats land, fading over a few
    bars), Pitch trace (note lanes, the note and its cents).
  - *Generative*: Particles, Tunnel, Plasma (a 64 x 16 field scaled up:
    the lows ring it, the mids stir it, the highs grain it), Ripples (a
    kick drops a stone).
  Under calm motion the surface stops turning, the plasma drifts at a
  fifth of its pace and the ripples fade where they land.
- **The page** (vispage.js): every card draws live (only while on screen:
  an IntersectionObserver), a big preview (⤢, or a click on a card's
  canvas) that goes full screen (the Fullscreen API), "Use for scope" /
  "Use for spectrum" (each a `data-cmd`: "Dock scope: Phosphor"), the
  page's ▶ (the working loop, `/fragment/preview`: the view's parts are
  Perform's) and **Demo signal** — a two-bar test pattern made in the page
  (a kick, an off-beat hat a little left, a chord a quarter-cycle apart
  on the right: wide, for the goniometer), looped quietly through the
  master; it is a player (`transport.claim("demo")`): the dock's ■ stops
  it, a ▶ takes over from it. From 901 px the page is
  window-high and the big preview takes the height the gallery leaves
  (else, at 2560, the gallery ends 200 px down over bare room;
  the view scrolls inside itself when the gallery is taller), and under
  2200 px the cards are 212 px wide at least, so a family is a row or
  two. Kept per browser beside the choice: the big preview's pick (`synthlab.visuals.big`) and the
  **♥ favourites** (`synthlab.visuals.fav`), moved up into a Favourites
  group of their own, first, hidden while empty (`arrange`: each group in
  the registry's order). **⤨ Shuffle** (`data-cmd` "Visuals: shuffle the
  dock") deals the dock a random pair — each slot one of its own kind if
  it can (not a "spectrum" in the scope slot), never what it drew, and
  from the favourites once there are two.
- **GSAP, disciplined**: the families' heads and their cards rise in
  staggered, down the page; a ♥ card pops into its new place; cards lift on hover, their
  costs count to each new value, a swap flies a still of the card up into
  its slot while the slot fades up (FLIP: `.vis-ghost`, transforms only),
  the dock's name ticker (`.viz-name`, none for a classic) slides in, the
  particles' bursts are tweens, and the lit ▶ buttons pop on each kick's
  onset. All through `G()`, null under calm motion; nothing
  per frame goes through GSAP that the canvas draw does.

## Layout gotcha

The editor's `.cockpit` is a strict CSS grid with named `grid-template-areas`
(`strip chain panels fx perform bass keys` beside the dock; section 17
re-arranges the same areas at 1200 and 2200 px: `perform` beside `bass`,
then `fx` / `perform` / `bass` beside `panels`; `keys` holds `#keys-slot`,
the empty place the page frame's keyboard dock stands under: "The page
frame" below)
(defined twice in `static/style.css`: desktop and `max-width: 900px`). Any new
top-level editor section needs its own named area in **both** definitions, or
it lands in broken implicit rows below the layout. Verify template/CSS changes
in a real browser (Playwright) — the test suite does not catch layout breakage.

### The sticky topdock

The nav + scope/spectrum strip live in a sticky `.topdock` in `base.html`,
so the trace keeps running on every view however far you scroll. Any new
top-level section in a `.cockpit`/`.perform-view` still needs its own
named grid area in **both** breakpoints.

### The page frame: what base.html draws outside `#view`

A view swap replaces `#view` (htmx, `hx-target="#view"`, the nav's links)
and nothing else, so what must survive a page change lives in base.html
around it: the top dock (nav, scopes, status strip), the clip library
drawer, `#vox-pop` (the live vocal chain), the sound menu
(`#lane-sounds`, every sound there is, and `#sound-pick`, the popover
that searches it), and **the
keyboard dock** (`fragments/keys_dock.html`, after `#view`: held notes,
the layout, Play, the octave, the Loops strip, the talkbox — every page;
`keys-and-midi.md`, "The keyboard dock"). It is sent once per full page
load, never per swap (measured below, "Page weight"). Its regions
(`chord-bar`, `key-takes`) are every page's (`ALL_VIEWS`), and a region
of a page that shares one of its ids needs its own (Perform's Loops panel
is `loops-panel`, the strip `key-takes`; its master knob and the Mix fader `#pf-volume` /
`#mix-volume`, `data-volume`).

**`--keys-h`**: the keyboard dock is sticky at the window's foot, after
`#view` in the page's flow, and its height (keysbar.js measures it with a
ResizeObserver, on `<html>`; 0 in a frame, where it is not drawn) is what a
window-high view leaves it: every `height: calc(100dvh - var(--top-h))`
of a view is `calc(100dvh - var(--top-h) - var(--keys-h))` (the Library,
Breed, Map, Lab, Clips, Bass, Voice, Song, Mix, Perform, Drums' floor),
and `.cockpit`'s `min-height` subtracts it too — so a view's bottom row
ends above the dock and nothing scrolls (`tests/browser/
test_keys_everywhere.py`). A new window-high view subtracts it as well.
On Synths the dock stands where its grid area was: `#keys-slot` (empty,
grid area `keys`) gives its edges (`--keys-l` / `--keys-r` on the dock).

## Workbench (`static/layout.js`)

**Workbench** (`static/layout.js`, island 16; style.css section 20 and
`static/dense.css`):
- **Density**: dense ("compact" in the cookie and the code) is the
  default and the desktop density (a `density` cookie layout.js writes;
  `service.density_of` -> `<html data-density>`, so first paint is right;
  the rail's density button toggles). Every dense rule is inside one
  `html:not([data-density="comfy"]) { … }` block in `static/dense.css`
  (CSS nesting: the scope is spelt once), which
  base.html links with `media="(pointer: fine)"` — or `media="all"` once
  a pen has been seen ("Density and the pen" below) — so comfy is
  style.css untouched and touch keeps the coarse tier. In dense, >900px,
  the nav is a side activity bar (`.topbar` fixed left, `--nav-w`, icon +
  label per view: base.html's `icons` table).
- **Splitters**: `.splitter[data-var]` drags `--dock-w` (Synths dock,
  beside the dock in its grid area because the dock scrolls:
  `data-pane`), `--pool-w` (Song) and `--lane-w` (the Loops page's right
  column — the lane inspector and Play — its handle on the column's left
  seam, `data-edge="left"`),
  read by the grids' own `grid-template-columns`; per browser
  (localStorage), arrows nudge, double-click resets. The Song page's
  editor pane has two (`views/song.html`, outside every region):
  `--drawer-w` from 2200 px, where it stands beside the playlist once
  its ◨ is pressed (under it is the default: `song.md`,
  "Song view"), and `--drawer-h` under it (held to 55 % of the view) (`data-axis="y"`: the handle drags a height;
  `data-edge` left / top: it sits on the pane's near edge). dense.css
  reads them only while set (`.song-view[style*="--drawer-w"]`) and
  keeps the playlist its room with `min()`. **Folds**:
  `[data-fold=<id>]` hides `#id .fold-body` (the drum rack, the keys dock, the Loops page's lane inspector and Play); `data-fold-auto="<media query>"` folds it where the query holds until it is first pressed; `data-fold-page` keeps it per page (the keys dock, every page's: `synthlab.fold.<id>@<view>`, falling back to the one key).
- **Tabs**: `[data-tab-of=<id>][data-tab-pick=<name>]`
  shows one part of the box `#id` at a time — `data-tab` on the box, which
  its CSS reads (the Loops page's Play module: pads | XY | FX | beats,
  style.css 42) — per box per browser (`synthlab.tab.<id>`), re-applied
  after every swap as a fold is (a morph puts the server's default back);
  the buttons' `aria-selected` follow.
- **A geometry change is `synthlab-geometry`**: what moves
  the page's boxes without moving the window — a splitter or a sash let
  go, a fold, a module moved or popped out, the clip library pinned, the
  deck docked, the playlist zoomed, the bench's dock — dispatches
  `synthlab-geometry` on window, not a synthetic `resize`, which would run
  every window listener (and any library's) for what is not a resize.
  What caches geometry hears both (`for (const ev of ["resize",
  "synthlab-geometry"])`): song.js (the playhead's column), labview.js,
  steps.js, kitpads.js, shaper.js, deckui.js, pagefill.js; the workspace's floats
  follow the window alone.
- **Slice markers**: the sampler panel's `.slice-mark`s (see "Sampler
  instrument" in `sampler-and-clips.md`) — a drag-to-resize gesture like the splitters, so here
  rather than a new island.

## Workspace: modules you can move

Every section of a page is a module of its own — an effect block, the
playing block, the layers — that can be moved, tabbed, folded, floated
and added, as panes are in VS Code. `static/workspace.js` (island 65, deferred; its
first-paint wait is colours.js's),
style.css 38, `studio/workspace.py`, `fragments/ws_menu.html`,
`GET /fragment/module/{key}`. Per page and per browser: localStorage
`synthlab.ws` (guarded), `{view: {docks, fold, float, add}}`. Nothing is
session state; **the server's markup is always the default order**, and
⊞ Layout → ↺ Reset layout (the palette's "Reset the layout", a module's
"Reset this page's layout") puts it back.

- **What moves.** Every module with an id (`data-module`, "The module"
  above) folds, floats and focuses, on any page (the Lab's included). A
  **dock** is an element a page marks `data-ws-dock="<key>"`: `#view` on
  Synths (`synths`), Perform, Drums and Clips, and Synths' `#patch` form
  (`synths.panels`, itself the module "Panels" of the page). Its
  **members** are the modules in it with nothing between that a swap
  names — an element with an id, a form — or that is a module: a member
  moves anywhere in its dock and **every region still finds it by id**
  (a region's OOB morph lands where the module now stands: the Filter
  panel's response curve in a tab, the layer strip beside the panels —
  `tests/browser/test_workspace.py`). **A wrapper marked `data-ws-pass`**
  is only its modules' place in the server's grid, not a wall: Synths'
  `#fx-rack`, `#perform-tools` and the Bass check `<details>` (each has no
  module of its own; once a layout takes every module out of one it goes,
  `display: none !important`). A move
  is a DOM move (`moveBefore` where the browser has it: the focus, a
  canvas, a playing `<audio>` survive), **never a clone**.
- **Every block of Synths is a module**, each right-hand block its own
  (a wrapper module holding several — both racks, macros + XY — would
  leave a press on a head inside it, MACROS, XY PAD, LAYER 3 FX, finding
  a module that is no member, and nothing would move): `fx.layer`, `fx.master` (`#fx-scope-<scope>`),
  `synths.macros` (`#macros`), `synths.xy` (`#xypad-panel`, its head
  `data-ws-head`), `synths.bass` (`#bass-module`: the mono and phone
  cards, Low roots) and `synths.sub` (`#bass-sub`). Each is a `.panel` —
  the bench's face, its lit edge (`--bevel`) and the live glow
  (`.cockpit .panel`'s inset signal light) the panels have. In the server's grid each is a tile as a
  panel is (a hairline between, style.css 17.3), **as tall as what it
  holds** — the wrapper fills its cell, its last tile does not (below,
  "No stretched last module"). How their regions follow them:
  - **an FX rack answers for itself**: every post of a rack (add, chain,
    a knob, move, remove, the empty rack's ＋ Reverb) targets its own
    fieldset and takes only it from the answer (`hx-target="#fx-scope-…"
    hx-select="#fx-scope-…"`, fragments/fx_rack.html — on every page, the
    answer unchanged); `fx_split` (Synths) drops `fx.rack` from the
    wrapper. Elsewhere `#fx-rack` is the module `fx.rack` (Drums,
    Perform's "+ FX rack");
  - **the sub spectrum rides the Bass check's answer**: `GET
    /fragment/bass` asked from Synths (`split`) draws the mono and phone
    cards in `#bass-panels` and `#bass-sub` out of band (`outerMorph`, by
    id wherever it stands; the plot is fragments/sub_plot.html, which the
    Bass page's wide card draws too). The Bass check measures when it
    comes into view (`intersect once`, which covers the fold's toggle) or
    after a ▶;
  - Macros' assign form (`hx-target="#macros"`) and the XY pad's own
    answers (`#xypad-panel`, OOB) replace them by id where they stand; a
    replaced member takes its tab's state again (`paint`).
- **A panel goes anywhere on the page**. A nested dock's member may stand in its own dock or
  any dock round it (`reach`): its drop zones are its own panels' first,
  then the page's (never the module it is in), and the dock it lands in
  holds it in its tree — the dock's tree that names a module draws it,
  any other lets it go (`moveTo`, `unplace`), a module no tree names goes
  home to its anchor (`back`), and a body morph's server order is laid out
  again. What keeps it working out of `#patch`:
  - its fields still ride every patch post: workspace.js gives its
    controls `form="patch"` (`owned`, after every draw and swap; a field
    that names another form — a curve's point — is left), FormData of the
    form takes them, and the sound bar's post includes `[form=patch]`;
  - what is typed there still posts: an `input` from a control whose form
    is a dock it is outside reaches that form as `ws-input` (`#patch`'s
    `hx-trigger="input delay:150ms, ws-input delay:150ms, submit"`);
  - its styling: every panel is a `.patch-panel`, and style.css /
    dense.css spell `#patch X` as `:is(#patch, .patch-panel) X` (`#patch
    .panel` as `:is(#patch .panel, #view .patch-panel)`) — the same
    specificity, so nothing at home moved; the shape rows' rules
    (`views/editor.html`) read `#view:has(…)`; bench.js's fields, audio.js's
    idle rows and tweak.js's reset ask the same.
  The one thing that may not happen is a page module going *into*
  the Panels form (its controls would join every patch post): the Panels
  module is one drop zone for them.
- **The keyboard dock** is the page frame's (outside `#view`), no module
  of a page; Song, Mix, Voice and the Lab have no dock yet (below).
- **The sound list is a member**: `#sound-dock` (`synths.sounds`), a
  `popover` that is an ordinary box on desktop, is taken by its title row
  (`.dock-title`, `data-ws-head`; never the filter or its buttons) and
  goes anywhere in the `synths` dock — beside, into the tabs of, under
  any module, folded, popped out, focused — and Reset puts it back in the
  left column. In a group it spans the group (style.css 38, desktop only:
  under 900 px it is the bottom sheet wherever it stands) and keeps its
  own scroll under its sticky head and filter. Its ‹ (audio.js,
  `.dock-collapsed`) **folds it where it stands**: alone in a row's
  column the column goes, as at home (`ws-group[data-ws-ids=
  "synths.sounds"]` and its sash hide); stacked in a column or tabbed it
  keeps its head, the ‹ turned ›. **Sounds ›** in the view head opens it
  in place. `#sound-dock` and `#preset-list` are found by id wherever it
  stands (a save lands in the moved list).
- **The layout is a tree**, VS Code's grid: a split `{d: "row" | "col",
  k: [nodes], s: [sizes]}` — a row's sizes are weights (`--g`), a
  column's a height in px (`--h`) or none (its own) — or a group `{t:
  [ids], a: the tab shown}`. Until a member first moves, the page is its
  own CSS grid; the first move **carves** the tree from where the members
  stand (guillotine cuts of their boxes; a `display: contents` module —
  the sound bar — by its children's; a sticky one — the sound list, the
  keys dock — where it flows, not where it is stuck: stuck, it would
  straddle every cut and the whole page would fall into one column), so nothing else
  jumps. Drawn as
  `<ws-dock>` › `<ws-split>` › `<ws-group data-ws-ids>` (+ `<ws-tabs>`, and a
  `.splitter.ws-sash` between two: a row's two neighbours share their
  width, a column's upper one takes a height; arrows nudge 16 / 64 px,
  double-click evens it). A laid-out dock (`data-ws-on`) is a flex column:
  its head, readouts and popovers flow round the `<ws-dock>`; the page
  grid's own splitters hide. In a column, a group holding something that
  takes the height it is given (a step grid, the drum XY pad, a big
  visualiser) shares what is left; the rest keep their own height. On
  the tiled pages (Perform, Drums) the sash is the 1 px seam. A comment
  `<!-- ws <id> -->` marks each member's place in the server's markup,
  for Reset.
- **A drag** starts on a member's head — a `.module-head`, `legend`,
  `summary`, `header`, `h2` / `h3` of its own (not a nested section's),
  a `[data-ws-head]` (Perform's XY head) — or on its bare frame (a gap
  between its controls), or on a tab; never on a control in it, and only
  past 6 px (a click is a click; the click that ends a drag is
  swallowed). Measured once at its start; the drop zone — a side of a
  module (a new row or column there), its middle (its tabs), within
  24 px of the dock's own edge, or **a column's foot** — is previewed by
  a fixed `.ws-drop` moved by `transform` only, the module's name at the
  pointer. A new row's column is the dragged module's own width (up to
  half its target's; the preview says so), not half the page. **The
  foot**: the empty room under a column's last
  module, at least 48 px (`ROOM`) — in the server's grid the room from a
  module nothing of its dock stands under down to the foot of its place
  (`under`: the wrapper or column holding it — `#fx-rack` under Master
  FX, a column of panels under its last panel — else the page's grid, to
  whatever is drawn below), laid out a column shorter than
  its row, and the room left in a module stretched past its own content
  (one that takes the height it is given, measured from its children's
  boxes: `filled`). A drop there puts
  the module at the bottom of that column (`place(…, "foot")`: under the
  smallest node holding the column's modules — a column takes it last,
  one module becomes a column with it underneath). The room under Master
  FX becomes the FX column's next place: the racks, then the module. **Esc
  cancels.** A floating module's frame drags its panel. A sash's grip
  reaches 3 px into its neighbours, so a press at a module's very edge
  is the sash's.
- **No stretched last module** (the last module in a column keeps its
  natural height, so the empty space under it is the column-bottom drop
  zone). The rule on every page, in the server's grid and laid out:
  - **a module is as tall as what it holds**: Synths' columns of panels
    end with their last panel (its hairline under it), `#fx-rack` with
    Master FX, the Bass check with the sub spectrum, Perform tools with
    the XY pad; Voice's FX racks. (Kit from 2200 px fills to the foot
    instead: its steps and pads take the height — `drums.md`, "The Kit
    page, laid out".) Laid out, `ws-group > [data-module]`
    is `flex: 0 1 auto` (style.css 38);
  - **except one that takes the height it is given** and fills its place:
    a step grid, the drum XY, a big visualiser (`:has(.grid, #drumxy,
    canvas.ws-vis)`, as for a column's share above), a pane that scrolls
    inside itself — the sound list, the Loops panel, the lane inspector
    (its body: the lane, ✦ Coach's chat), the clip list and bench — and
    the Panels form (`#patch`: its columns). Kit's beat is one at
    every width (its steps share the window's height; from 2200 px up to
    96 px, its five lanes stacked under them);
  - **the pages**: Synths and Kit as above; Voice's racks end with
    their content (the bench under them); Loops — the Loops list and the
    editor's grids fill, and the right column's last module, Play, keeps
    its height under the inspector, which fills (its body scrolls);
    Song — the pool and the playlist scroll inside themselves, the master
    meters run the playlist's height, the editor pane is the editor;
    Mix — the strips' faders and the returns take the height, the master
    strip scrolls inside itself (`overflow: auto`, its rack can be long);
    the Lab — the songs list, the lanes (the editor), the chops' list and
    "make" all scroll or take the height; Clips — the list and the bench
    each scroll inside themselves. None of these is stretched past what
    it needs: they fill because they scroll or are the editor.
  - **the room is the bench**: on Synths and Voice the bench's own face;
    never the bare page. Laid out on a tiled
    page (Kit, Loops) the room under a column is the
    seam colour (`--line`, the grid's background).
- **Fold, float, focus** (a module's right-click menu: menu.js asks
  `synthlab.workspace.rows(module)` — "A section's own menu"): **Collapse
  to its head** (`data-ws-fold`: clipped to one head row; a module's own
  `[data-fold]` button — the keys dock's "⌨ keyboard" — pressed while it is
  collapsed expands it, so the button that shows it always does); **Pop out** (a
  `<ws-float>`: fixed, resizable by its corner, kept in view, its place
  and size kept; appended to its dock, so a panel stays in its form);
  **Focus** (`data-ws-max`: the module alone over the whole window, Esc
  returns) and **Focus in full screen** (the same inside the Fullscreen
  API; leaving full screen leaves focus too; the rail's `#fullscreen-btn`
  is the whole studio's, and a layout follows it as any window size).
  These are module attributes that **ride the module's own morph**
  (`htmx:before:morph:node` copies them onto the incoming node) and are
  put back after every swap. (A state outranks any page's own placement
  of the module: those few properties are `!important`.)
- **After every swap** (`htmx:after:swap` and `:settle`) the page's
  layout is drawn again where it is not: a body `innerMorph` (a panel's
  Defaults, an undo) or a view swap puts the server's order back, and the
  island re-lays it in the same task. A saved page holds `#view` hidden
  (`html[data-ws-wait]`, a CSS fallback shows it after 0.8 s) from
  `<head>` until DOMContentLoaded has drawn it: **no flash**.
- **Presets and Reset**: ⊞ Layout (`fragments/ws_menu.html`, in the page
  head's tools) lists the page's presets — Synths: Sound design /
  Performance / Compact; Perform (the Loops page): Grids / Launch /
  Compact (`workspace.PRESETS`, data the
  button carries) — "Reset the layout",
  "+ a module" and the module keys. Each row is a `data-cmd` (group
  View): the palette's "Layout: Performance", "Add module: Scope, big".
- **A page's record key**: `synthlab.ws` keeps a page's
  record under its view's name — or `<view>@<n>` for a page in
  workspace.js's `REV`, bumped when the page's markup moved too far for an
  old layout to fit (a carved tree names modules and places that are
  gone). On load a record under an older key of that page is deleted:
  Perform is `perform@2`, so a layout saved under `perform` (an older
  markup) is dropped, not mis-applied. Synths is `editor@2` (its
  right-hand blocks each a module), and a record saved under `editor`
  is **carried over**, not dropped
  (workspace.js `SPLIT`): `fx.rack` becomes `fx.layer` over `fx.master`,
  `synths.perform` `synths.macros` over `synths.xy`, `synths.bass`
  `synths.bass` over `synths.sub` — a column where the old module stood
  alone, its tabs where it was a tab (the tab shown follows); a fold
  folds the new ones, a float of an old one is dropped.
- **Bring things in**: "+ Scope, big" / "+ Spectrum, big" (the top dock's
  chosen variation drawn from the shared frame at the module's size:
  `visuals.each`), "+ FX rack" on Perform (`GET /fragment/module/fx-rack`:
  `fx_rack_fragment` for the asking page — the loop's layer and master
  racks, whose own posts re-cue Perform's preview), "+ Clip library,
  pinned" (the drawer's pin). An added module lands at its dock's foot;
  its menu has "Remove from this page". **An added server fragment is no
  region**: it re-fetches itself (`outerMorph`, 400 ms after the last
  non-GET request of the page, not while it holds the focus) — a region
  is sent only to the pages its `pages` name. Not added (yet): the
  current track's mixer strip. (The keyboard is on every page, the page
  frame's — and with it the Loops strip — so it is no module here: in no
  dock and no preset.)
- **A slide-out module** (Perform's Loops panel, `perform.loops`:
  `loop-and-perform.md`, "The Loops panel"): in the page's own grid it
  is a column, a tab, or — open and not pinned — a panel standing over
  the page from its tab column (`html[data-loops="over"]`, only while
  `#view:not([data-ws-on])`); once the dock is laid out it is a module
  like any other, its tab or its body in its group. Sliding over, a press
  outside it folds it — a drag of another module starts with that press,
  so the carving measures the tab, never the panel over its neighbours.
- **Keys**: **F6 / Shift+F6** focus the next / previous module (Ctrl+1…9
  are the browser's tabs; F6 is VS Code's "next part"); on a focused
  module, **Alt+Shift+arrows** move it beside the nearest module that way
  (or to that edge of its dock). Alt+Shift+digits stay the views'.
- **Not docked (yet)**: Song, Mix, Library, Breed, Map, Voice, Bass,
  Visuals and the Lab carry no `data-ws-dock` — their modules fold, float
  and focus; moving them waits for a look at each page's own grid (the
  Song page's drawer splitters, the Lab's own). The docked pages have
  none of the gaps Synths' split closes (a block no member because a
  module or an id'd wrapper holds it; a column with a bare foot): the
  Loops page's Play module holds pads / XY / FX / beats as its tabs and
  the lane inspector holds ✦ Coach — both by design (one module, its
  tabs); Drums' `fx.rack` holds one rack (the drum bus), so the
  module is the rack; Clips' three are all members. Mix (its master strip
  holds the master rack) and Voice (its two racks in `#fx-rack`) have
  that shape, but no dock at all yet.

## Page weight

**Static files: a hash each** (`app.py`
`static_hashes`, `asset`). base.html names every script and stylesheet
as `{{ asset('/static/x.js') }}` — the URL with that file's own hash
(`?v=` the first 8 hex of a sha1 of its bytes, read once at startup:
restart to pick an edit up, as for everything else) — and
`RevvedStaticFiles` serves a URL carrying the file's current hash as
`immutable`; any other `v` (none, a stale one, another file's)
revalidates. An edit re-downloads that file alone (one rev for the
whole folder, the newest mtime's, would send all ~82 files again). The page carries 77 distinct revs where one repeated
compressed to nothing: ~0.3 KB gzipped (`test_browse_views.py`'s Library
budget).

**Library shelves out of sight skip their rendering**
(`content-visibility: auto; contain-intrinsic-size: auto 480px`, style.css
"the browser: a section a family"): ~50 shelves, ~900 cards — but never
the shelf holding the active card, whose detail is placed in the view's
grid (containment would make the shelf its containing block).

**Page weight**: the chord poster (~500 buttons) is fetched from
`/fragment/chordlib` on first open, not rendered into every page; all four
pad blocks are `<details>` that start collapsed (`chords.js` remembers each
by id). Editor HTML is 167 KB raw / 16 KB on the wire, 2322 nodes (266 KB
and 2828 nodes with the poster rendered in). `fragments/param_row.html` exists so single controls can be sent OOB.

**The keyboard dock on every page** (measured on a fresh
studio): the dock is 50.7 KB raw / 8.5 KB gzipped, sent once per full
page load (base.html) and never on a view swap. Full pages, without ->
with it (raw KB / gz KB): Synths 966 / 94 -> 967 / 94 (it held the keys already),
Breed 71 / 14 -> 123 / 22, Drums 242 / 29 -> 294 / 37, Perform
186 / 26 -> 238 / 34, Map 39 / 9 -> 91 / 18, Bass 42 / 10 -> 94 / 18,
Library 908 / 83 -> 960 / 91, Voice 132 / 24 -> 184 / 32, Lab
42 / 10 -> 94 / 19, Clips 44 / 11 -> 96 / 19, Song 137 / 25 -> 189 / 33,
Mix 82 / 17 -> 134 / 25, Visuals 89 / 13 -> 141 / 21. A swap to Synths
is 883 / 78 (934 / 86 with the dock in it); no swap carries the dock.

**The sound menu on every page** (measured on a fresh
studio): `#lane-sounds` is 370 options, 24 KB raw / 3 KB gzipped, sent
once per full page load and never on a
view swap; a menu opening asks `GET /fragment/sound-menu` for it again
(~4 ms, the same 24 KB) and swaps it in only when it differs.

## Design system

The look is a digital sound lab: dense, dark, small type, signal flow
drawn as cables, a meter wherever there is a level, no wasted space.
One system, in two files: `static/style.css` (tokens in section 1, the
canonical components in section 15, the shell in 16) and
`static/dense.css` (the desktop density). `tests/studio/
test_design_system.py` holds it: contrast of the token pairs in both
themes, every component and its states, the shell built from them.

### Tokens: never a raw value in a component

| scale | names | comfy -> dense |
|---|---|---|
| space | `--s0 --s1 --s15 --s2 --s3 --s4 --s5 --s6 --s7 --s8` | 2 4 6 8 12 16 20 24 32 48 -> `--s3..--s6` become 8 10 12 16 |
| type | `--fs-2xs --fs-xs --fs-s --fs-m --fs-l --fs-xl` | 10 11 13 15 19 24 -> 10 10 11 12 13 15 (labels 10, values 11, body 12) |
| radius | `--r-xs --r-s --r-m --r-l --r-pill --r-round` | 3 6 10 16 999 50% -> 2 3 4 6 **3** 50% |
| control height | `--h-s --h-m --h-l` | 24 30 38 -> 22 24 30 (the touch tier: 44) |
| motion | `--t-fast --t-med --t-slow`, `--ease` | 100 / 160 / 240 ms |
| elevation | `--e-mod --e1 --e2 --e3`, `--scrim` | a module / a raised control / popovers, menus and tooltips / drawers and dialogs (dense: `--e1` none) |
| depth | `--bevel --bevel-in --well --sheen --glow-r --cap --cap-lip` | a module's or a key's lit edge / a key held in / a display's inner shade / a control's face / an LED's glow ("The module") / a transport key's machined cap and the lip it stands on ("The transport keys") |
| light | `--e-ambient --e-lift --spec --knob-spec --glass --brushed --halo-edge --filament`, `--a-ambient --a-spec --a-glass --a-halo`; the glow `--glow` (`--hue-glow`) | a module's soft shadow on the chassis / a control under the hand / a cap's and a knob's specular spot / a well's reflection / the chassis' brushed metal / a played module's lit edge / a struck pad ("The light model") |
| stacking | `--z-sticky --z-rail --z-toast` | 30 / 40 / 60 |

Colour roles (each resolves to a night and a day value: "Colour: hues
and depths" below says how):

- surfaces `--bg` (the page), `--bg-raised` (panels, the rail),
  `--bg-inset` (wells: fields, canvases), `--bg-over` (menus, tooltips,
  the palette);
- text `--text`, `--text-dim`, `--text-faint` (a dimmed hint —
  a pad's key while typing plays the keys, the mod matrix's ± ✕ on an
  unplugged route, an empty cell's or slot's "+" — at >= 3:1 on every
  surface, a `PAIRS` row; **never `opacity` on text**: a hint dimmed so
  renders at 1.7-2.3:1 and no token test can see it.
  `test_a_dimmed_hint_is_a_faint_tone_never_an_opacity`);
- lines `--line` (seams and panel edges), `--line-ctl` (a button's edge),
  `--line-strong` (a field's edge: >= 3:1 on every surface, WCAG 1.4.11);
- `--accent` (what is on, the one primary action, selection) and
  `--signal` (what is sounding: traces, meters, levels, audio cables),
  `--danger`;
- **a hue used as text is not the fill**: `--accent-text`,
  `--signal-text`, `--danger-text` (the fills fail AA as text by day);
  text ON a fill is `--on-accent` / `--on-signal` / `--on-danger`. Never
  `color: var(--accent)`: a test refuses it.
- `--focus`: the one ring. `.btn` adds a page-coloured halo so it shows on
  an accent fill. Never `outline` for decoration.
- `--ai` (violet: an AI feature), `--go` / `--pause` (the transport's ▶
  and ⏸), each with its `-text` and `on-`; `--white` / `--black` (no hue:
  a mix toward them keeps its own); `--shade` (what a shadow and
  `--scrim` are made of: black by night, the ink's warm shade by day).

### Colour: hues and depths

Every colour is `oklch(lightness chroma hue)`, and
style.css section 1 takes the three from three places:

- **Hues** — one variable a role, in degrees, and one for all:
  `--hue-neutral` (the surfaces, lines and dim text: obsidian 260 by
  night, brushed aluminium 245 by day),
  `--hue-ink` (the text: warm 90 by night, the faceplate's cool 250 by
  day), `--hue-accent`,
  `--hue-signal`, `--hue-danger`, `--hue-ai`, `--hue-go`, `--hue-pause`,
  `--hue-map-dark` / `--hue-map-bright` (the Map's dots, a sound's
  brightness between the two: mapview.py writes `--b`, 0..1, on each), and
  **`--hue-shift`**, added to every one — one line turns the whole palette.
  A theme may give a role its own default (the neutral, the ink, the
  accent's ochre by day, the signal's deeper teal), and a tone may lean a
  few degrees off its role (`--tilt-*`: the day's accent text, the pause
  text). A hue set on `<html>` holds in both themes.
- **Depths** — `--l-<tone>` (lightness), `--c-<tone>` (chroma), `--a-*`
  (an alpha: the shadows', the scrim's), each written **once as a night |
  day pair**: `--l-bg: var(--night, 0.16) var(--day, 0.97)`. A theme is two
  switches: `:root` has `--night: initial; --day: ;` (the guaranteed-invalid
  `initial` lets that column's fallback through, a space hides the other),
  `html[data-theme="day"]` and "system" under a light OS's media query flip
  them — two one-line rules, never a second copy of the palette
  (`color-scheme` follows the same switch). `light-dark()` would do it for
  colours only; the switch carries numbers.
- **The colours** — written once after the ladders, from the two:
  `--accent: oklch(var(--l-accent) var(--c-accent) calc(var(--hue-accent)
  + var(--hue-shift)))`. A role is its fill, its `-text` (the same hue at a
  text depth) and its `on-` (text on the fill: by night the page's own
  colour, by day a deep ink on the light fills and near-white on the deep
  ones). The night's glass (`--ah-*`) is mixed from the roles too
  (`oklch(from var(--ai) 0.32 0.13 h / 0.22)`), so it turns with them.
  The colours a script reads (`--bg`, `--bg-inset`, `--text`, `--text-dim`
  and the six role fills) are `@property`-typed `<color>`, so
  `getComputedStyle` hands a canvas the resolved colour, not the formula.

**Nothing after section 1 writes a colour.** A rule derives one: a token,
relative colour syntax (`oklch(from var(--black) l c h / 0.4)` for a
shadow, `oklch(from var(--accent) l c h / 0.3)`), `color-mix(in oklch,
var(--accent), var(--white) 30%)`, `transparent`, `currentColor`.
`test_no_literal_colour_outside_the_tokens` refuses a hex, a colour
function not relative to a token, or a named colour in style.css after
section 1, dense.css, every template's inline `style` and the server's
Python (the one exception: `song_model.PALETTE`, a section's or a
pattern's own colour, which is the song's data, saved with it; and the
data-URL images — the select's arrow, a mask — which no variable reaches).
`test_a_hue_moves_every_colour_of_its_role_and_the_shift_moves_all`
holds a hue to its role, the contrast test resolves the tokens itself (the
WCAG pairs above, both themes).

- **A new colour**: a role's new tone is a ladder line (`--l-x`, `--c-x`:
  night | day) and one formula in section 1, on the role's hue; a one-off
  is a mix or a relative colour of a token where it is used. A new role is
  a `--hue-*`, its ladder, its three tones, a row in the Colours popover
  and in `ROLES` (test_design_system.py).
- **Retheming**: the hues are the brand (change a `--hue-*` default, or
  `--hue-shift`); the depths are the theme (change a column). A third
  theme is a third switch and a third column.
- **The Colours popover** (the rail, beside the theme toggle;
  `fragments/colours.html`, `static/colours.js`): the themes' swatches
  ("Themes" below), then a hue slider a role and
  "all" (the shift), each track the role's own colour turned round the
  wheel. It sets the hues on `<html>` and keeps them per browser
  (localStorage `synthlab.colours`); colours.js is the one island loaded
  without `defer`, in `<head>`, so a stored hue is in place before the
  first paint. A slider's default is the theme's own hue (double click,
  the eraser, the menu's Reset to default); Reset lifts them all (the
  theme stays). A change
  fires `synthlab-colours`: motion.js (the scope, the spectrum) and
  deckart.js (both decks: the virtual one and the Stream Deck +'s keys and
  strip, `keys-and-midi.md`) read their cached colours again — deckart.js
  also on any change to `<html>`'s `style`, `data-theme` or `data-look`.
- **Contrast and black level**: the popover's
  last two sliders, in percent, set two variables on `<html>` that the
  lightness ladder is derived from — no colour is overridden. Every
  surface, line and text depth is written `--l-x: clamp(0, var(--ladder-0)
  + var(--ladder-k) * (var(--night, N) var(--day, D)), 1)`, and section 1
  works the two coefficients out of **`--contrast`** (1; the slider
  80-140 %) and **`--black-level`** (0..1; 0-100 %):
  - contrast scales every depth's distance from the page's own
    (`--ladder-p`, `--l-bg`'s pair), both themes: the panels, the lines
    and the text move apart (more) or together (less);
  - black level, the night only, stretches the dark half about L 0.6
    (`--ladder-f`): at 100 % `--bg` is L 0.04 and `--bg-inset` 0 (an OLED
    black; measured on the page, panels from rgb(19, 22, 27) to (2, 4, 6)),
    each surface above dropping a little less than the one under it, so
    the steps between them grow, and the text goes to white;
  - the role fills (`--l-accent` .. `--l-pause`) and the text ON them
    (`--l-on*`) are written plain and never move, so text on a fill reads
    the same at every setting (moved with the ladder, the day's
    signal fill would meet its own text at 110 %);
  - **at the defaults nothing changes**: `--ladder-0` is 0 and
    `--ladder-k` 1, so every depth is the number written (measured:
    every root token and every element's computed colour
    on 12 views x 4 theme modes, ΔE 0.000;
    `test_the_ladder_dials_leave_every_depth_as_written_at_their_defaults`);
  - **the WCAG notes**: from 100 % up, at any black level, every pair in
    `PAIRS` holds AA in both themes (`test_contrast_up_and_any_black_level_
    keep_wcag_aa`). Below 100 % they no longer do — the dim text and a
    field's edge go first: at 95 % a field's edge on a raised panel is
    3.00:1, at 90 % the night's dim text on a menu is 4.2:1 and the day's
    `-text` tones on an inset 4.0-4.5:1, at 80 % most text tokens fail
    (`test_contrast_down_trades_aa_away_and_says_so`; the slider's title
    says so).
  Kept with the hues (`synthlab.colours`: `contrast`, `black`, the
  slider's numbers), applied before the first paint, lifted by Reset; a
  change fires the same `synthlab-colours`, and the decks and the
  visualisers read their colours again.
- **A mix of a role and a neutral is `in oklab`**: a
  neutral token has a hue (the night's slate, 260) at a tiny chroma, and
  `color-mix(in oklch, …)` turns the hue the short way round, so a pressed
  toggle's amber tint, 82 % toward `--bg-raised`, comes out violet with a
  salmon edge. A mix of two roles (or a role and `transparent`, `--white`,
  `--black`: no hue) may stay `in oklch`.

#### The day: brushed aluminium

The day column of the ladder is a cool grey-silver faceplate, and white
keys read as keys: (`--hue-neutral` 245,
`--hue-ink` 250, `--c-bg` 0.007) — the chassis L 0.90 (`--ladder-p` with
it), a panel 0.94, a well 0.87 (pressed in, darker than its panel), a
menu 0.97, the seams 0.82, a button's edge 0.70, a field's edge 0.56, the
text 0.17 and the dim text 0.42 (no warm tilt: `--tilt-text-dim`
0), the faint 0.56; **the white keys 0.99** with almost no chroma, apart
from every surface (not the panel's own white drawn by hairlines alone).
The day's `-text` tier is L 0.44 (and the accent's and the signal's chroma under the gamut there) so
each holds AA on the darker well — `TEXT_TIERS` in
test_design_system.py. Every pair in `PAIRS` holds, in every theme, at
every contrast from 100 % and every black level. The themes keep their
own day hues (Warm Tube's, Analog Moog's cream…) on these depths; a
neutral or ink hue set in the Colours popover holds over it.

#### Themes

A **theme** is a named set of the dials above —
the hues, contrast, black level and three chroma dials — **never a colour
and never a second stylesheet**: one rule each in style.css section 1,
`html[data-look="<key>"], .look-sw[data-look="<key>"] { … }`, numbers (or a
night | day pair of numbers) only. The ladder's depths, the tiers and every
formula stay as written, so a theme keeps the palette's rules. The three
chroma dials are the only tokens the themes added: **`--tint`** scales the
chroma of the surfaces, lines and keys (0.2 a graphite, 3.2 a warm
brown-black), **`--tint-ink`** the text's (a cream, a phosphor green),
**`--chroma`** every role's but the accent's (the hand stays full; Mono Lab
mutes the rest to half).

| theme (`data-look`) | scheme | the idea |
|---|---|---|
| Studio Teal (none: the defaults) | split complementary | amber on slate, teal and violet its splits ("The palette") |
| Warm Tube (`warm-tube`) | analogous + a complement | a filament's red 26 · orange 50 · amber 78 on warm brown-black, cream text; what sounds is the magic eye's green (150); play's lamp orange, record red |
| Analog Moog (`analog-moog`) | complementary | orange 58 and blue 238 on walnut and cream |
| Neon Night (`neon-night`) | split complementary | magenta 345, its splits cyan 205 and lime 125, on deep indigo |
| Mono Lab (`mono-lab`) | monochrome + accent | graphite with no tint, every role muted to half under one safety orange |
| Vintage Console (`vintage-console`) | triadic (red · yellow · blue) | a console's caps on grey-blue steel, cream legends |
| Phosphor (`phosphor`) | analogous | a green CRT (trace 150, text, black) with the amber terminal for the hand |

- **Applied before the first paint**: colours.js keeps it with the hues
  (`synthlab.colours.theme`) and sets `data-look` on `<html>` in `<head>`;
  Studio Teal is no attribute at all.
- **Picked in the Colours popover**: a swatch each (`.look-sw`), the page in
  miniature — section 1's dial-and-colour rule is `:root, .look-sw`, so a
  swatch recomputes every colour from its own theme's dials and draws its
  page, text and five role colours in tonight's or today's form. One click
  applies it and lifts every moved dial; the sliders then show the theme's
  values (their defaults are the theme's own), a slider moved from them
  reads "· custom" beside the name, and Reset goes back to the theme's own
  and keeps it. Each swatch is a command, `Theme: Warm Tube`.
- **The decks follow**: deckart.js (both decks) and visuals.js read their
  colours again on a change of `data-look` and on `synthlab-colours`.
- **Held** (test_design_system.py `THEMES`): each theme only dials; every
  pair in `PAIRS` AA in its night and day, and with contrast up and any
  black level; the six role hues at least 20 degrees apart, the hand 25 from
  delete, the danger a red (`test_every_theme_keeps_one_meaning_per_hue`).
  The browser journey is `tests/browser/test_themes.py`.
- **A new theme**: a rule in section 1 (a comment naming its scheme; its glow, `--hue-glow`: "The light model"), its
  row in colours.html's list, this table. The tests pick it up.

### The palette: one scheme, tiers, one meaning per hue

The six role hues are one
scheme, not six picks:

- **A split complement round the amber.** The accent is an amber LED (70).
  Its complement (250) is the night's slate — `--hue-neutral` 260, so the
  amber stands out on a cool case — and its two splits are the signal's
  teal (195) and the AI's violet (302). The transport's green (148) sits
  between amber and teal; the warm side is stepped about 21 degrees: red
  29, orange 50, amber 70 (a stop, a pause and the hand never read as each
  other). Both themes share every hue (only the neutral, the ink and two
  `-text` tilts differ by day).
- **Tiers, not tunings.** Every role fill sits at one lightness a theme —
  by night L 0.73; by day two, the light fills under a deep ink (accent,
  go, pause: L 0.72) and the deep fills under white (signal, danger, ai:
  L 0.54) — each within ±0.02. Chroma is held under a ceiling (0.18 by
  night; 0.15 / 0.17 by day) or the sRGB gamut's edge, whichever is lower,
  and close to it: teal cannot be as saturated as red at the same depth,
  so no role is allowed to shout over it. Every `-text` sits at one depth
  a theme (L 0.78 | 0.48, chroma ≤ 0.14 | 0.13), and every `on-` is the
  page's colour by night, the deep ink on a light fill and white on a deep
  one by day — each AA on its fill (`PAIRS`).
  `test_every_role_sits_on_its_tier` and `test_the_hues_are_one_scheme`
  hold it.

| role | hue | night fill L / C | day fill L / C | means — and only this |
|---|---|---|---|---|
| `--accent` amber | 70 | 0.74 / 0.16 | 0.72 / 0.15 | chosen, on, the hand: a selection, a lit step, a pressed toggle, the primary, a segment's tick — and **everything that waits** (a queued launch, a count-in, a request in flight) |
| `--signal` teal | 195 | 0.75 / 0.125 | 0.54 / 0.092 | what sounds: traces, meters, levels, audio leads (a sidechain lead dashed), a playhead, a launcher or row that plays, a knob's arc |
| `--danger` red | 29 | 0.71 / 0.18 | 0.54 / 0.17 | record, clip, mute, stop, delete, an error or a limit |
| `--ai` violet | 302 | 0.72 / 0.16 | 0.53 / 0.17 | an AI feature (the night's nebula is the glow's, not violet) |
| `--go` green | 148 | 0.73 / 0.16 | 0.71 / 0.15 | ▶, what plays now in the status strip, a toast for something done |
| `--pause` orange | 50 | 0.74 / 0.16 | 0.73 / 0.15 | ⏸ and what is paused |

So, for instance: a Perform launcher is teal while it plays and a dashed
amber while queued, a pending button is amber, a track's kind is its
shape, in the dim ink (not a colour), and the count-in glows red (it is
a take). The exceptions, and why: the meter's ladder (teal, amber
past 70 %, red past 90 % — the hardware convention), the song's own
section and pattern colours (data, `song_model.PALETTE`), the roll's
ghost lanes (mixed from the roles as data colours), the night's nebula (the glow and the signal, one palette).

### The module: one frame for every section

Every section of the instrument is a **module** — `.module` (style.css 15): a
panel, an FX unit, the layer strip, the sound bar, the chain bar, a
transport, the keys dock, a drum grid, a launcher, a mixer strip, a shelf,
a card. One frame, defined once:

- a 1 px hairline edge (`--line`) and the small radius tier (`--r-s`: 3 px
  dense) — both zero-weight (`:where`), so a view's own placement wins and
  a tile in a seamed grid (dense D2) keeps the seam as its edge;
- **`--bevel`**: lit on its top edge, shaded on its bottom (inset
  hairlines: crisp at 100 % and 125 %), over `--e-mod`, a contact line. By
  night it reads as dark anodised metal (the face is its own surface:
  the glass, the bench), by day as brushed aluminium;
- **`.module-head`**: its head strip — a caps label, its tools at the right
  (`.module-tools` / `.panel-tools`); on a panel it is the `legend`, on an
  FX unit the `header`;
- **`.module-well`**: a display pressed into it (`--well`, an inner shade):
  the scope and spectrum, the XY pads, the patch bay, the map's plot. The
  fields, selects, number boxes, segmented controls and slider slots are
  wells too.

**`data-module="<page>.<id>"`** is a module's stable id — `synths.sounds`,
`synths.sound`, `synths.layers`, `synths.chain`, `synths.<panel>`,
`synths.macros` / `.xy` / `.bass` / `.sub`,
`perform.transport` / `.fx` / `.pads` / `.xy` / `.launch` /
`.notes` / `.drums` / `.song`, `drums.transport` / `.bank`,
`song.transport` / `.pool` / `.playlist` / `.master` / `.editor` / `.dock`,
`mix.returns` / `.master`, `library.filter` / `.recipes` / `.<family>` /
`.selected`, `breed.rack`, `map.legend`, `clips.filter` / `.list` /
`.bench`, `lab.import` / `.song` / `.lanes` / `.chops` / `.make`, `voice.source`, `bass.keys` / `.read`;
a rack every page draws is `fx.<scope>` (`fx.layer`, `fx.master`,
`fx.drums`). menu.js takes it as a section (named from its legend or
label; an unnamed one stays a wrapper), deck.js's focus mode finds it
first, and its markup is self-contained: the workspace moves it whole
("Workspace: modules you can move"). A repeated module (an FX unit, a mixer strip, a card) takes
the class without an id.

**Depth with restraint**: the elevation ladder is `--e-mod` (a module) <
`--e1` (a raised control) < `--e2` (a popover, a menu, a tooltip) < `--e3`
(a drawer, a dialog). The `--live` / `--kick` light on a module is a
faint inner glow. Every new depth is a token in section 1 (an
alpha a theme: `--a-bevel-hi`, `--a-bevel-lo`, `--a-well`, `--a-sheen`).

**The controls, one family** (section 15): a button is a hardware key —
its face catches the light (`--sheen`) over the bevel; held, or latched on
(`aria-pressed`), it **sinks** (`--bevel-in`: the light moves to its lower
lip) and its tint comes up — never a shrink. The tiers: default ·
`.btn-ghost` (a head's tool: no face until the hand is on it) ·
`.btn-primary` · `.btn-danger` · `.btn-ai` · a toggle on; the transport's
▶ ⏸ ■ ⏺ are machined caps with a pilot lamp ("The transport keys" under
Components). One height ladder (`--h-s`
22 / `--h-m` 24 / `--h-l` 30 dense), one focus ring. A pad (`.hit-pad`,
`.note-pad`, `.chord-pad`, `.launch`, `.bank-slot`) is a key of the same
family. A knob is a machined cap (lit from above, a darker skirt, a rim)
with an indicator line at its value, in a ring lit in its role colour
(`--knob-arc`, teal by default); a slider is a slot pressed into the
module, lit to the value, with a fader cap riding it. The segmented
control is a row of keys in a well, the chosen one standing up with an
amber tick. One LED (`.led`): a sunk lens while off, its colour (`--led`)
with a glow tier (`--glow-r`, full by night, a hint by day) while lit. A
switch's light (`.btn > .led`) is the accent — on is amber, as the key's
pressed tint (not the LED's default teal: that would be two meanings on
one key); a latched drone, which sounds, holds in the signal's.

**The patch leads** (fxcables.js, style.css 17.2): a sheathed lead — the
rubber sleeve, the coloured core, a specular line along its top (brighter
by night) — sagging by its length, whether it runs beside or to the row
below; each plug a machined barrel with a bright metal band and a collar.
Drawn on change only, one SVG a rack: on a settle, a resize of the rack
**or of any module in it**, and the end of an animation or transition
round it (a popover's scale-in). **`[data-wired]` is set before the
boxes are measured**: it lays the rack out (its gaps, the
inline leads' room) and a morph drops it, so measured first every lead of
a redrawn rack — the live vocal chain after a knob moved — would sit where
the modules stand unwired, 28-49 px inside them, with nothing to redraw it.

### The light model

One light, above and a little in front, on every surface, written once in
style.css section 1 so every theme, night and day, has its own version:

- **A module** is a machined panel: lit on its top edge (`--bevel`),
  resting on the chassis with a soft ambient shadow under it
  (`--e-ambient`, part of `--e-mod`).
- **A well** (a field, a segmented control, a slider's slot, the scope) is
  pressed in: shaded at its near lip, its far lip catching the light (the
  second line of `--well`); a display well (`.module-well`, the top dock's
  scope and spectrum) holds a faint reflection (`--glass`); the status
  strip's read-out is such a window.
- **A cap** catches the light where it is: a knob's specular spot above its
  centre (`--knob-spec`, the fifth gradient of `.knob`), the transport keys'
  `--cap`; **a lens is a jewel** — an `.led` off is dark glass in its own
  hue with a glint, lit a hot core falling to its colour under a tight glow
  and a wide bloom (`--glow-r` × `--a-halo`).
- **The chassis** — the dense rail and the top dock — is brushed metal
  (`--brushed`: an SVG fractal noise stretched along the grain, ~0.5 KB, no
  image file), the rail's right edge lit.
- **The glow** (`--glow`, its own dial `--hue-glow`): the light the
  instrument gives off while it is played. Not a role — it means nothing a
  role does not say — and never text (no `-text` tone, no AA pair:
  `test_the_glow_is_each_themes_own_light_and_never_text`). Each theme sets
  it: Studio Teal the signal's teal (195), Warm Tube an amber filament (62),
  Analog Moog orange (58), Neon Night hot pink (330), Mono Lab its safety
  orange (50), Vintage Console valve amber (80), Phosphor green (150). The
  Colours popover has a slider for it (colours.js keeps it with the hues).
  `--a-halo` is how strongly it shows: full by night, 0.6 by day — softer,
  never absent.
- **The hand** (section 15): a `.btn` under the pointer rises a hair
  (`translate: 0 -1px` over `--e-lift`) with its edge warming toward the
  accent; pressed it sinks with a quick light flash (an accent tint at
  `transition-duration: 0s`, decaying over `--t-slow` as it comes up); a
  latched key stays sunk, a disabled one never moves. **A struck thing is a
  filament**: a pad (`--filament`: a bloom and an inner glow in the glow),
  a piano key (the accent with the glow round it), a lit step the playhead
  strikes (`.cell.now:checked`) flare at once and cool over ~0.5 s — the
  decay is the transition back, so nothing runs while nothing is struck.
  **A value that moved** (a knob, a fader, a slider, a step — by hand, a
  dial, a CC: tweak.js sends the same `input`) leaves an afterglow:
  motion.js adds `.afterglow` and tweens `--after` 1 → 0 over 0.7 s (a
  `drop-shadow` in the glow, style.css 39; not the Colours popover's own
  sliders, whose sync is no edit).
- **Glamour where it counts**: the brand — comfy's "studio" in machined
  metal falling to the glow, the dense rail's "sl" an engraved badge —
  in a still halo (a breathing text-shadow would lay the rail out at every
  write; the audio LED under it breathes); the page you are on in the
  rail lit (dense: its amber lamp edge washing into the item and round the
  icon; comfy: a key standing out of the well with an amber tick), with
  **one** slow sweep of light across it when you arrive (an animation that
  runs as the item becomes `.active`, never a loop); the now-playing
  read-out a recessed glass window whose rim pulses in the go colour on
  the beat while something plays, its LED a jewel.

### Motion: full or calm

**The OS's
`prefers-reduced-motion` is deliberately not followed** — with Windows'
"Animation effects" off (work laptops often have it off) it would still the
whole studio. The studio is a desktop app, so motion is a
setting of its own, per browser, **full by default whatever the system
says**:

- **Motion: full | calm** — the Colours popover's last row (the rail's
  Colours button; a `.seg`, each half a command: "Motion: full", "Motion:
  calm"). Kept in `localStorage` (`synthlab.motion` = `"calm"`; full is no
  key), and not a colour: the popover's Reset leaves it alone.
- **Calm is the reduced-motion mode**: colours.js sets
  `html[data-motion="calm"]` in `<head>` before the first paint (full is no
  attribute), and every reduced-motion rule is an `html[data-motion="calm"] …`
  rule, never a `@media (prefers-reduced-motion: reduce)` block (section 10's kill switch — every animation and transition —
  and the light's `--live-eff` / `--kick-eff` at 0, the playheads hidden,
  the blinks stilled, the deck's lean gone…).
- **One JS source of truth**: `synthlab.calm()` (colours.js), which every
  island that moves asks instead of `matchMedia` — motion.js (`G()`, the
  energy, the afterglow), pagemotion.js (the page transition), grid.js, layout.js (a fold's
  opening), transport.js (the now-playing LED's pulse), songcell.js (smooth
  scrolling), deckart.js / deckui.js, visuals.js / visuals2.js / vispage.js.
  A toggle dispatches `synthlab-motion` on document; motion.js takes the
  light down at once on it (what it wrote would otherwise stay put), the
  rest read `calm()` on their next frame.
- **Held**: `test_design_system.py`
  (`test_motion_is_full_whatever_the_os_says_and_calm_on_request`: no media
  block, no island `matchMedia` for it); `tests/browser/test_alive.py` —
  with Chromium emulating `reduce` and the default setting the entrance
  animates, the energy is written and the LED pulses; with calm, none of it.

### The energy

**One signal, measured once, published once.** motion.js measures
the master analyser each frame for the scope (`smooth`, the level; `kick`,
the low end's onsets). The same two numbers are written, in one tick, at
most ~20 times a second and only while they move:

- on **`<body>`** as `--energy` and `--kick` (registered `<number>`,
  non-inherited: a write restyles `<body>` alone — not on `<html>`, whose
  `style` deckart.js and visuals.js watch for the hues: there, every write
  would make them read the palette again, 20 times a second), with `html[data-alive]`
  while the output carries signal, and `synthlab.energy()` beside
  `synthlab.kick()` — the published reading, for a script, a test, a deck;
- on **the consumers** that draw with them as `--live` and `--kick`
  (`LIT` in motion.js: the panels, the transports, the keys dock, the drum
  bank, the play keys, the playheads, `#view`…), each reading
  the calm-safe `--live-eff` / `--kick-eff` (0 under calm motion,
  section 10: "Motion: full or calm").

**At rest it settles**: when the output goes quiet the level and the kick
snap to exactly 0, one last write is made and nothing more —
`tests/browser/test_alive.py` plays the loop, sees `--energy` move and
`data-alive` set, stops, and sees both back to 0. **Under calm motion
nothing is written** (motion.js checks before it writes), no afterglow is
added and the rail's sweep is gone; the static depth stays.

What draws with it (style.css 39):
- **the module being played** lights at its edge in the glow
  (`--halo-edge`): a step grid's module while it runs (grid.js sets `data-running` on the
  grid once when it starts and removes it at rest — never per frame). The
  light is a `::after` whose **opacity alone** follows `--live-eff` and
  `--kick-eff`: a composited layer (`will-change` only while lit), no
  repaint of the module. **Never on a key or pad press**: the
  keys dock lit by a `:has` on a pressed key, or a key's / a pad's glow
  decaying through a `box-shadow` transition, makes a press block the main
  thread 40 ms instead of 17 — the keys and pads decay by colour only, and
  a chord's first note sounds in the press's own handler (chords.js), not
  a 0 ms timer. Desktop only (≥ 901 px, where the hosts are
  positioned); under calm motion it holds one still light. A transport
  whose grid is not the running one still breathes on the same layer
  (opacity `--live-eff` × 0.4, not a border-color);
- **every panel's live glow is a layer too** (style.css 11): each `.panel` holds one `::before` (the panel is
  positioned and isolated, the layer at `z-index: -1`, under its content)
  drawn once at full strength — a lit inner edge in the signal, a glow lip
  at its top, a wash in from the edges (the stage's: none by day) — and
  the level moves its **opacity alone** (promoted while
  `html[data-alive]`) — not the panel's `border-color` and two
  `box-shadow`s moving with `--live-eff`, where every write (~20 a second)
  repaints every panel on the page. No outer halo past the panel's edge
  (`overflow: clip` clips a layer inside it); the inner edge is brighter
  instead. Measured against the `box-shadow` form (headless Chromium,
  1920 × 1080, 5 s of the loop playing, `devtools.timeline` tracing):
  Synths' paint time 115 ms against 264, Loops' raster 207 ms against 544;
- the patch leads (`.cable.live`): their glow is a still `drop-shadow`,
  set once while the output sounds (`html[data-alive]`), and the level
  moves the lead's opacity alone;
- the transport's lit pilot lamps pump on the kick and breathe with the
  level; the playheads carry a static bloom (they move by `translate`
  only); the stage light (`#view::after`) and the scope's halo.

**Rules for a new consumer**: read `--live-eff` / `--kick-eff` (add the
element to motion.js's `LIT` and to both lists in style.css — section 1's
and section 10's); animate `opacity` or `transform` on a layer of its own,
never a `box-shadow` or a `filter` on a large or repeated element; nothing
may run at rest.

**What it costs**: the page's frame cost while playing is measured
(headless Chromium, 1920 × 1080, CDP `Performance.getMetrics` per frame
over 5 s, and a trace with invalidation tracking). Two storms to avoid: a
selector such as `.roll[style*="--mx"] .cell.sel` (the piano roll's drag)
makes **every** inline-style write on any element — a playhead's `--phase`,
motion.js's `--live` — restyle every step cell under it (an ancestor's
`[style]` in a selector puts an invalidation set on every element's style
attribute): ~1800 cells restyled a second on Perform. The drag is
`.carrying` (roll.js), and `test_no_selector_reads_an_ancestors_inline_style`
refuses the pattern. And a progress line as a `width` is a layout of the
page every frame: the now-playing's is a `scale`; transport.js writes
the read-out's `hidden` / `disabled` / `data-state` only when they change.

### Components: the one form of each

A new control uses these classes. It does not get a family of its own.
**Each is defined once, in section 15**
(`test_a_promoted_component_is_defined_once_and_in_the_shared_section`):
a view's section may place or size a component, never restate it.

| component | class | notes |
|---|---|---|
| module | `.module` (`data-module="<page>.<id>"`), `.module-head`, `.module-well` | every section's frame: "The module" above |
| button | `.btn` + `.btn-primary` / `.btn-ghost` / `.btn-danger` / `.btn-ai` (iridescent edge: "Colour by state" below) / `.btn-icon` / the transport keys `.btn-play` (a view's ▶) `.btn-pause` `.btn-stop` `.btn-rec` (a machined cap with a pilot lamp: "The transport keys" below), `.btn-s` / `.btn-l` | a toggle is `aria-pressed` (an accent tint, sunk in, not a solid block); `data-state="on" / "pending"` for a script-owned one (pending: a dashed amber edge); one `.btn-primary` per page |
| pad | `.hit-pad`, `.note-pad`, `.chord-pad`, `.launch`, `.bank-slot` | a struck key of the button family: bevelled, sunk while struck (section 15) |
| field | `.field` (`.field-s`) on `input`, `select`, `textarea` | `aria-invalid` / `:user-invalid` |
| checkbox / radio | `.check` | |
| segmented control | `.seg` > `button[aria-pressed]`, `a[aria-current]` or `label > input` | |
| caption | `.caps` | the one uppercase micro-label: `--fs-xs`, `--track-caps` |
| panel header | `.panel-head` (in a `.panel`, section 5) | |
| menu | `.menu` > `.menu-item` (`.danger`, `kbd` / `.menu-hint` at the right), `.menu-sep` | |
| popover | `.pop` on a `[popover]` or a `<details>` panel | |
| drawer | `.drawer` on a `[popover]` | see "A drawer that must stay open" |
| tooltip | `title="…"` | shell.js: nothing else to write |
| toast | `.toast` (`.done`, `.error`, `.progress`) | `synthlab.toast(message, kind)`: a plain `.toast` is information (neutral edge, `role=status`) and the default, `done` something kept or made (green), `error` a failure or a refusal (red, `role=alert`: a 422's fragment, a `catch`); a server's `studio-toast` HX-Trigger carries `{message, kind}` (perform.js hands it on). It lets the pointer through to what it covers (only its own buttons take one) and goes faint under the pointer (`.see-through`) |
| badge | `.badge` (`-accent`, `-signal`, `-danger`) | a fact, never a control |
| list row | `.row` (`aria-selected`, `.row-on`) | |
| empty state | `.empty-state` (a `p`, optionally an `h3` and a `.btn`) | (`.empty` is the layer strip's own) |
| loading | `.skel` | |
| key chip | `kbd` | |
| meter | `button.meter` / `span.meter` + `<i></i>` (`<b></b>` for a clip segment), `.meter-v` | THE meter, on every view: segmented LEDs, teal to 70 %, amber to 90 %, red above; set `--level` (0%..100%), `--peak` for the held tick, class `clipped` for the last segment. `#meter` in the top dock is the master (motion.js) and the only one that is a button (it clears the clip latch); every other is a reading (`cursor: default`, from `.meter:not(button)`). Drive one only from data something already computes: no new per-frame audio analysis without measuring it |
| LED | `.led` (`data-on`, `="armed"`, `="clip"`; `--led` its colour) | a sunk lens off, its colour with the `--glow-r` glow on |
| jack, cable | `.jack`; `svg.cable` > `path` + two `circle`s | the path sags (`M2 4C7 15 17 15 22 4`: control points hang below the ends); `data-signal="audio"` (teal, default) / `mod` (amber) / `clock` / `side` (teal, dashed); `.off` is an unplugged lead; `.live` glows with the output level (motion.js writes `--live`). The chain bar's stage-to-stage leads are the first use |
| knob | `input[type="range"].knob` (`.knob-s`, `.knob-l`) | a range drawn as a machined cap with an indicator line and a value arc (`--knob-arc`, teal by default) from the inline `--fill` the page already writes; tweak.js drags it. One size a tier: 28 px comfy, **24 px dense** (one rule, D1), 40 px under a finger (one rule, section 9f); `.knob-s` 22 and `.knob-l` 40 in every tier |
| fader | `input[type="range"].knob.fader` | a `.knob` to the input layer (tweak.js's captured drag — up is more, 160 px the travel — fine, wheel, eraser, `data-default`, a typed value) drawn as a channel fader: a slot lit to `--fill`, a cap riding it. 22 px wide, **44 px under a finger**; as tall as its cell. Defined in section 28 (the Mix view is its one user: promote it to 15 when a second view wants it) |
| knob cell | a `.param` holding a `.knob`: `#patch .param-knob`, `.drum-panel > .param` | three lines, in this order: the dial, its name (`--fs-2xs`, dim, mixed case), its value (the read-out, a typeable chip) |
| typed value | an `<output>` beside (or `for=`) a range | on every view: click it, or Enter on the control — tweaktype.js swaps in `input.val-edit`, the number goes through `synthlab.tweak` (a log slider's hidden twin takes it exactly). `data-pct`: the read-out shows value x 100; `data-untyped`: it is not the control's own value (a Song lane's point) and takes no field |
| mixer toggle | `.btn` + `.mute` / `.solo` / `.arm` (the layer strip's `.l-m` / `.l-s`, the Lab's `.lab-solo`) | lit = the pressed TINT of any toggle in a mixer's colours: mute and arm red, solo teal. Never a solid block (the Song's record button, a transport, is the one solid red) |
| file picker | `label.filepick` (+ `.btn`, or a drop target's own box) > `input[type=file]` | the input covers the label, invisible: click, drop and keyboard reach it; dimmed while the input is `disabled` |
| audio player | `.player` > `.player-play`, `.player-bar` (`--at`), `output.player-time`, `audio`; optional `.player-loop` (aria-pressed: the `<audio>`'s `loop`; the Lab's starts on) | bench.js (the listeners are the document's: any view); no `<audio controls>` anywhere — a test refuses one |
| context menu | `data-ctx` + `data-menu`, or `data-ctx-pop` | `#ctx-menu.pop.menu.ctx-menu`: "One context menu" below |
| module name | `.panel-name` in a panel's `legend` | the engraved caption; a `title` on it is the panel's note |
| rack | `.rack-sec` (a docked column's captioned section, seamed from the next); `details.rack` (a fold that is always open from 1200 px via `::details-content`) | |
| ledger of facts | `.t-facts` > `.caps`, `.meter`, `output` | caption, meter, number |
| signal graphic | `svg.sig` + one `path` from the `sig_path` / `smooth_path` filters (`studio/sigdraw.py`), `viewBox` from `sig_box` | THE way a spark, a spectrum or an energy shape is drawn: one smooth path whose outline leaves its box below and beside it, filled with base.html's shared `#sig-fade` and stroked — so the stroke shows only along the top; a glow where it is big enough to read (section 32). Teal: it sounds. A canvas draws the same (visuals.js `curve` + `fade`, the top dock's Classic spectrum). Never bars, never a flat slab — in a page's own graphics; the Visuals page's variations are the dock's alternatives, chosen by hand |
| cables in a row / over rows | `svg.cable` between flex items; `g.cable` paths in one stretched `<svg preserveAspectRatio="none">` | the layer strip's bus and the FX pedalboard; `.pb-cables` is the script-drawn kind (pixel coordinates, no viewBox). Every lead is 2 px with 1.5 px ring jacks, teal for audio, amber for control, dashed grey unplugged |

Layout tokens the shell owns (style.css `:root`, dense D1): `--top-h`, the
sticky top dock's height (95 px comfy, 59 px dense) — a window-high view
is `calc(100dvh - var(--top-h))`, and the sticky docks hang from it (the
Synths sounds dock, the Song's pool, the panels' scroll margin). **The top
dock is two rows**: the scope and the spectrum
across the whole width, half and half (`.viz-cell { flex: 1 1 0 }`), and
under them the status strip in small type — now-playing and the facts at
the left, the meter and the CPU pushed right — a row of its own, so the
voice count changing never resizes the visualisers. `tests/browser/test_shell.py` holds
the halves, the row and the dock's height to `--top-h`; `--rack-w` (240 px), a view's left rack
(Synths' sounds, the Song's pool, Breed's controls, the Lab's songs: a
splitter may override it per browser); `--side-w`
(`clamp(240px, 14vw, 320px)`), its right dock (the Library's selected sound, the Map's
legend).

**Night tiles are glass under one nebula** (style.css 1, 20 and 32,
dense.css D32): a
tile's background is `--ah-tile` — a lit top edge over `--bg-raised`,
opaque — and a sticky part (a track head, `.arr-corner`, `.jam-head`, a
drum row's head, the velocity row) takes `--ah-solid`, the plain face, so
nothing that scrolls under it shows; the bench (Synths, Bass, Voice) is
`--ah-bench`, a shade over the page; a plot's well is `--ah-well`. **The
nebula is one layer** (`body::before`, `--ah-nebula`): fixed to the
window, composited (`will-change`), painted once, screened over the room
(`mix-blend-mode: screen` — it lifts the dark surfaces into its colour and
leaves the light ones, the text, a lit key, as they are), under the sticky
top dock, the rail, the popovers and the toasts — never painted by each
tile (`background-attachment: fixed` is three window-sized gradients a
tile, repainted with the tile, and on Chromium drawn per tile: each tile
its own corner). Its palette is the theme's glow and the signal (one
palette: top right the glow, bottom left the signal's teal, a deeper glow
at the foot) — not the AI's violet, which would wash plain controls
(Kit's voice strips, Mix's master, the Map's legend).
A view's stage light (`#view::after`, motion.js lights `#view`) breathes
with `--live` and pumps on `--kick` (`--ah-stage`; its corner is the glow,
not the AI's violet). By day each is the plain surface or 0, and the
nebula is not drawn (`--ah-nebula-on`). A new tile takes `--ah-tile`,
never `--bg-raised`, and never a `fixed` background of its own.

**Type ramp**: a value's read-out is `--fs-val` (11.5 px dense),
tabular and semibold, in `--text`; a label `--fs-2xs` / `--fs-xs`, dim; a
page head `--fs-xl` in the display face (section 33).

**The typeface** (Bahnschrift, no web font): the
panel lettering — legends (`legend`, `.caps`, `.module-head`,
`.panel-name`, a chain stage's head, an FX unit's name, the jacks'
words, a shelf title), control labels (`.param > label`,
`.param-knob > label`, `.fx-param`, the hit inspector's, the mixer's) and
read-outs (`output`, number fields, `.readouts`, `.mix-val`, the status
strip's window) — is **`--font-panel`**: Bahnschrift, Windows' own DIN 1451
(the lettering of German panels and signs), SemiCondensed, tabular
figures, on every Windows 10 / 11, so no font file, no CSP change and
Windows' hinting at 10 px. One zero-weight rule (style.css 33, after the
type ramp) sets it, so a component with its own face (a mono field, the
display heads) keeps it; it changes no size. Body text, names, menus and
tooltips stay `--font` (Segoe UI Variable): a name reads better in it.
How Chromium resolves it: the family name `"Bahnschrift SemiCondensed"`
is the semi-condensed face (Windows names each width as a family); the
font's `wdth` / `wght` axes are not reachable on a local font there, so
`font-stretch` and `font-variation-settings: "wdth" var(--panel-wdth)`
(87.5) only matter where a browser exposes them, and every weight renders
the face's regular (a DIN's regular is sturdy at 10 px: the labels read
firmer than Segoe's). The measured gain: "Unison voices", "Pulse width
rate", "Osc 1 warp amount" fit on one line under a dense knob (in Segoe
they wrap). Off Windows the stack falls to `--font`.

**Tiles meet at 1 px seams.** A tiled view is a grid whose background is
the seam (`--line`), every tile a surface, no outer margin, no border or
radius on a tile, the page head a tile: dense.css D2's "seamed grid" rule,
which the seven dense-tiled views (Perform, Drums, Song from 901 px;
Library, Breed, Map, Lab from 1100 px) opt into by class. The bench
(Synths, Bass, Voice: style.css 17.3) draws the same seams as borders
inside one surface, in both densities, because its panels flow in CSS
columns.

Colour by state, everywhere: **amber = chosen or on** (a selected row, a
lit step — a drum step is a dimmer amber, never red —, the primary
action), **teal = sounding or audio** (traces, meters, levels, audio
leads, a card that is playing), **red = clip, record, mute, delete**,
**violet = an AI feature** (`--ai` / `--ai-text` / `--on-ai`,
`.btn-ai` — a tint, or filled when it is also the page's `.btn-primary`
— and `.is-ai` for a label or heading): the sound agent (✦ Agent, its
drawer and Send), a take's ✦ Clean up and "✦ after" with its report, and
the Lab's ✦ Separate, the clip hub's ✦ morph. Each also declares
`data-cmd-group="AI"`, so the palette lists them under AI and "ai" finds
them. **The AI button**: a `.btn` in shape and density, with an **iridescent edge**
— a conic ring (`--ai-ring`) from the AI's hue through `--ai-2` / `--ai-3`
(section 1: its hue turned 80 degrees one way and 45 the other, at its own
depth, so a theme turns it with the AI) — over its violet glass, and a
soft glow on its glyph; under the hand the ring turns slowly (`@property
--ai-a`, 3.6 s a turn; still under calm motion) and the glass lights.
Filled (the page's primary, or pressed) it is a violet gradient toward
`--ai-2` under the same ring.
One `.btn-primary` a view: the transport's play where the page is a
transport (Perform, Drums, Song), else the page's one outcome (Save
sound…, Vocode it, Breed next generation, Use on Synths). A dialog's
submit (`.dialog.pop`: Save sound…'s Save, the name popover's) is that
dialog's primary, seen only while it is open: it is not the page's.

**The transport is the same on every page**: **▶ first, at
the left of the view's own bar, then ⏸ and ■ beside it** where the page
has them, coloured by what they do — **▶** (`.btn-play`, `--go`: green
in Studio Teal), **⏸** (`.btn-pause`, `--pause`), **■** (`.btn-stop`,
`--danger`), and **⏺** where it records (`.btn-rec`, `--danger`); not a
`.btn-primary`'s fill (still one a view), so a page's play never goes
dark.

**The transport keys**: each is a hardware key, like an old amp's — a machined cap
(`--cap`: lit from above, falling to a darker skirt, on `--knob-face`)
standing on a lip (`--cap-lip`) that a press sinks into (`translate` 1 px,
`--bevel-in`) — with a **pilot lamp** in its corner (`::before`, 6 px; 4 px
on a `.btn-s`): a jewel lens in its role's colour (`--lamp`), **dark glass
in that hue while off — never absent — and lit with a bloom while on**
(`aria-pressed="true"` or `data-state="on"`). ▶'s lamp is the theme's go
hue (green in Studio Teal, orange in Warm Tube), ⏸'s the pause hue, ⏺'s red
— it pulses while it records —, ■'s lights only under the press. A playing
▶'s bloom pumps on the kick (`--kick-eff`, 0 under calm motion; the
pulse stops too). The glyph is the role's `-text` colour; a latched key
stays sunk. Same shape everywhere: the views' ▶, the top dock's ▶ / ⏸ / ■,
▶ beat, the Song's and the Mixer's transport, the pattern editor's ▶, every
⏺ (the Song's record, the dock's ⏺ Rec, Perform's jam, the sampler's, the
Voice lab's, the vocal chain's, the talkbox's, the bend's), and the deck's
Play, Stop all, Jam and Capture keys (deckart.js draws the same jewel). The
dense footprint is the `.btn`'s. A ▶ that is its own stop
(`.btn-toggle`: Synths, Perform, Drums, the pattern editor's ▶ Play, whose
two faces are `.when-off` / `.when-on`) shows the red ■ while it plays, its
lamp lit; one with a ■ beside it (the Song page, the Mixer) keeps its ▶ and
lights its lamp. The top dock's stop and the jam's Stop all are
`.btn-stop`; the small auditions (`.sc-play`) and the shared player's ▶
are green glyphs, the player orange (⏸) while it plays. The Song page
and the Mixer have ⏸ (song.js `pause`): the song stops where it is
heard and the start marker moves to that bar (`S.songMark`), so ▶ carries
on from there.

**What plays, what will, and what waits — one registry** (`static/
transport.js`). Every player reports to `window.synthlab.transport` and draws
no transport UI of its own: the working loop (audio.js: a claim on ▶ and
on every swap, so its label follows the view whose mix it is; a wait for
Perform's queued launcher), Synths' ▶ beat (audio.js: the loop's beat
alone, a claim of its own, `synths.md`, "Beat ▾"), the song and its section / range loops
(song.js: a wait while the first pass decodes, a claim with its section
and bar and its count-in's `at`, a held ⏸), the jam (jam.js: a claim
while a track sounds, a wait while a launch waits for its point), a cell
or pool pattern played alone (songedit.js), the shared `<audio>` player
(heard from its media events in capture: bench.js has no hook), and an
audition longer than 1.5 s (keys.js `playUrl` → `transport.oneShot`: a
clip, a sound). The API is `claim(id, spec)` / `hold` / `release` /
`queue` / `take` / `stop` / `stopAll` (the file's header). It draws:
- **`#now-playing`** (base.html, first in the status strip, the client's
  — outside the `status-bar` region; over 900px the strip's column is
  `clamp(30rem, 44vw, 50rem)` — the window's width, never its text's, so
  a voice count or a longer name never resizes the scope and spectrum
  beside it, and `#cpu-meter` keeps its widest reading's 26ch): an LED, the source in caps (Loop,
  Song, Jam, Cell, Clip, Audition, the page of an `<audio>`), its name
  ("Drums · the beat · 2 bars", "loop · Chorus"), where it is ("bar 21 /
  64", "0:12 / 0:40"), a progress line, what waits ("next: Verse · 3
  beats"), a layer that sounds beside it ("+ Clip · talk-…"), ⏸ / ▶
  (resume) when the player has them, and **THE stop for everything**
  (`#global-stop`, `data-cmd="Stop everything"`, Shift+Space; disabled
  while nothing sounds). Colour by state: green playing (the LED glows
  on the beat: `--np-pulse`, written by the frame, 0 under calm
  motion), orange paused, amber waiting — a count-in, a launch for the
  bar, a song still loading — with a ring that fills (`--q`). Stopped,
  it says what Space would play on this page, or who just took over.
- **The cue beside each page's ▶**: a `#view [data-player]` button
  (`data-player="loop"` + `data-plays` on Synths, Perform, Drums;
  `"song"` on the Song page and the Mixer, whose cue is computed — the
  song from the marker's bar and section) gets `output.play-cue[data-cue-
  for=<its id>]` (section 15), its `aria-label` and its `title`: what
  ▶ / Space will start, and " · stops Loop" when it would stop another
  player — written on every swap and every 400 ms (the marker, the
  selection, a loop toggled). A track's sound edited on Synths
  (`[data-plays-now]` on the banner) is what its ▶ names. Synths' phrase
  ▶ names the phrase exactly (`playsOf`: "the phrase · riff · 1 bar +
  the beat · this layer", read off the sound bar's own pills, which post
  with the bar's redraw suppressed), and the Loop's read-out label is the
  same words; Perform's names the parts its switches leave on and the
  loop's length (`partsOf`: "the loop · notes + drums · 2 bars"); a sound's ▶ in the list is "Audition · <name> · the
  phrase, alone"; the XY pad's switch is "▶ drone" (one note, the key's
  root, of the edited sound).
- **What waits, on the thing**: a wait's `sel` (the jam's `.queued`
  pads, scenes and stops; Perform's queued `.launch`) gets `data-wait`
  (beats left) and `--q`; `[data-wait]::after` is the count in a ring
  that fills (section 15).

The rules, one behaviour everywhere:
1. **One thing plays.** Starting a player (a ▶, ⟳ Loop, a jam launch, a
   cell's ▶ Play, an `<audio>` player, a loop preview that starts by
   itself) stops every other player; the cue said so before the press,
   and the read-out says who took over. **Layered**, never stopped by a
   start: what a hand plays as an instrument — the live keys and their
   held-key loops, pads, one-shot auditions (a long one is named beside
   the player and a second replaces the first) — and the metronome.
2. **⏸ holds the place**, wherever it exists (the song, the Mixer, an
   `<audio>` player); ▶ — or Space — goes on from there.
3. **■ stops and goes back**: the song's marker returns to the bar ▶
   started from (after a ⏸ too: song.js `back`), a player to 0:00, the
   loop to its top. A ▶ with no ■ beside it is its own stop.
4. **Space is this page's ▶** — the `#view [data-keys="Space"]
   [data-player]` button: its ⏸ while that player plays (where the page
   has one), resume while it is paused, else a press. With no ▶ on the
   page, Space pauses (or stops) whatever plays. Never from a field, a
   focused button, a step grid, a key, a pad, a chord tile or the jam's
   grid: each keeps its own Space. **Shift+Space stops everything**
   (from anywhere but a field). The Song page's sound dock (a frame,
   `html[data-embed]`) takes neither.

Icons are 24-unit line glyphs (`svg.vi`, `stroke: currentColor`,
base.html's `icons` table and its `icon()` macro). An icon-only control
is a `.btn-icon` with an `aria-label` and a `title`.

### Density and the pen

- **Dense is the desktop density** and the one that is designed for.
  `dense.css` is linked with `media="(pointer: fine)"`; every rule in it
  is nested in one `html:not([data-density="comfy"])` block (write a new
  rule as it would read after that prefix; `&[data-…]` for a state of
  `<html>` itself), and it mostly restates token values. Comfy (`data-density="comfy"`, the rail's toggle, a cookie) is
  style.css alone: kept as an option, not designed for.
- **A pen gets the dense tier, whatever the pointer reports.** On a
  pen tablet the primary pointer can be coarse while the hand holds a pen.
  tweak.js marks the browser on the first `pointerType === "pen"` event
  (`html[data-pen]`, the `pen` cookie, `#dense-css`'s media to `all`);
  the server draws both from the cookie (`service.page.pen_of`), so the
  first paint is right from then on. The touch tier — every
  `@media (pointer: coarse)` block — is nested under
  `html:not([data-pen])`: write a new touch size inside one of those
  blocks, never as a bare media rule. "forget the pen" (`#pen-off`, in
  the shortcut sheet) undoes it.
- **Nothing is sized for a giant screen**. A wider or taller window gets
  more columns, more bars, more rows — never bigger cells, cards or
  gutters: a step cell tops out at 30 px (dense.css D18 / D25), a song
  lane at 96, a Breed spectrum at 120, a Library card starts at 176 px,
  the Visuals stage at 200, a mixer strip stops at 128 with the returns
  beside the last and the master taking the slack. A pane with little in
  it does not take the window: the Song page's idle editor is a 220-320 px
  column from 2200 px, Drums' grid is as tall as its rows with
  its tools under it and the bus FX takes the column's foot, Breed's
  litter is one row of eight from 2200 px. **Comfy is tight too**
  (style.css): the top dock 95 px (the nav's padding, 28 px
  visualisers), every page but Synths has the one-line head, 8 px
  gutters and a floor of the window less `--top-h` (so none scrolls by
  the dock's height; `.cockpit:not([data-view="editor"])`),
  Breed and the Map put
  their rack beside the board / plot from 1100 px, Perform's FX and Show
  share a line. The keyboard dock is slim on every page
  (`keys-and-midi.md`, "The keyboard dock"). Synths and the touch tier
  keep their own sizes.
- A pen is precise and it hovers: dense targets, hover states and
  tooltips are right for it. What it must never do is scroll or select
  while it drags a control: a draggable carries `touch-action: none` and
  captures the pointer (`setPointerCapture`).

### Tweakable controls (what a pen layer or a hardware controller drives)

**A tweakable value is a native `input[type="range"]`** — a slider or a
`.knob`. Nothing else marks it and nothing needs to be added:

| what | where it is |
|---|---|
| the parameter | `name` (a log slider posts through a hidden twin: it has no `name`, its id is `p-<name>` and it carries `data-log`, `data-min`, `data-max`; its own value is the 0..1 position) |
| travel | `min`, `max`, `step` |
| default | `data-default` |
| the drawn value | the inline `--fill` (audio.js keeps it) and the row's `<output>` |

**Drive one through the hook, never by hand**: `window.synthlab.tweak(el,
value, done)` sets the control (a range clamped and snapped to its step;
a select, or a radio group with an id, to option number `value` — a
group's events are the checked radio's: `tweak.steps`) and dispatches
the bubbling `input`
— only when the value moved — and, with `done`, the `change`. The page's
listeners then fill the track or a knob's arc, write the read-out, feed
the live voice and post the form as for a hand, and undo sees one edit.
Beside it: `tweak.later(el)` (the `change` of a tweak with no "let go",
250 ms after the last move: the wheel, a dial, a CC), `tweak.reset(el)`
(THE default: it sends the `dblclick` audio.js resets a slider on),
`tweak.lim(el)` and `tweak.busy()`. The gestures that reset: a
double-click, a **middle click** (pointerdown button 1 on a control with
`data-default`: `tweak.reset`, its autoscroll cancelled), the pen's
eraser, and the right-click menu's Reset to default (menu.js). The control surface (deck.js), MIDI
learn and the virtual deck's knobs all write this way; a test refuses a
`dispatchEvent(new Event(` or a `.value =` in them.
`window.synthlab.commands()` lists every command on the page (below) —
a key on a hardware surface runs one with `el.click()`;
`commands(true)` adds the declared ones that cannot run now, marked
`off` (how a key knows to dim).

What a mouse, the wheel, the keyboard, a pen, a dial and a CC each do to
a slider, a knob, a select, a grid step, a lane, a pad, an edge and a
key — **and which one island handles it** — is one table:
`keys-and-midi.md`, "Gestures: one owner each". In short: tweak.js owns
every value control (fine = Shift or the pen's barrel = a tenth; wheel
when focused; eraser = default; the knob's drag; with tweaktype.js, a
typed value), menu.js its menu (right click = the barrel tapped), pen.js
the pen on surfaces.

The mapping for anything new: **barrel = fine on a value while it drags
and the context action when tapped** (it arrives as a right click: the
step grids' erase already takes it there); **eraser = reset or erase**.
Never rely on the right button alone for something a finger must reach.

### Commands and shortcuts are declared on their controls

The command palette (Ctrl/Cmd+K, palette.js) and the shortcut sheet's
"Commands on this page" have no list of their own. **The element that
performs a command declares it**, and the palette harvests the live page
each time it opens:

```html
<button data-cmd="Add a layer" hx-post="/fragment/layer/add" …>+ Add layer</button>
<select data-cmd="Add effect to Master FX" data-cmd-group="Effects" …>   <!-- one command per option -->
<button data-cmd="Undo the last song edit" data-keys="Ctrl+Z" …>
```

- `data-cmd` — the name (verb first). On a `button`, `a`, `summary` or
  `select` only (a test checks). The same name on two controls is one
  command.
- `data-cmd-group` — its heading (else the nearest ancestor's, else
  "This page"). In use: `Go to`, `Transport`, `Effects`, `Export`,
  `Reset`, `View`, `Help`, `Stream Deck` (the virtual deck's own
  buttons: `keys-and-midi.md`, "The strip, the keys" — the deck's keys
  are bound to command names, so renaming a `data-cmd` a key uses means
  renaming it in deck.js's `KEYS`).
- `data-keys="Ctrl+Z"` — a shortcut an island already handles for it.
  This is how a shortcut gets into the sheet; do not add it to
  `fragments/shortcuts.html` by hand (that file keeps only the keys and
  gestures that belong to no one control). Without a `data-cmd`, name it
  with `data-key-label`.
- `data-cmd-hint`, `data-cmd-fill` — a dim line; a field that takes the
  command's name before it runs.

The palette's list is never cut: it scrolls. With nothing typed its
order is fixed — "Go to" first, then this view's own commands (the group
of its primary action leading), then the shell's; typed, a group is
ranked by its best hit. `tests/browser/test_palette_reach.py` harvests
every view and asserts each command is found by its own name.

A command runs by activating its element (`click()`, or the option's
`change`), so it goes through the same htmx attributes, route, lock,
confirm, OOB refresh and toast as a hand; it is skipped while its
control is `disabled`, `aria-disabled` or under `[hidden]`; and it
exists exactly as long as the control does. What is not on the page —
the sounds of every shelf, the clips — is the server's to find:
`GET /fragment/palette?q=` (`studio/palette.py`) answers with rows that
are themselves `[data-cmd]` buttons carrying the app's own posts.
**Adding an action to the app: put `data-cmd` on its control.** Nothing
else registers it.

Keys: no bare letter is free (every one is a note somewhere). Ctrl/Cmd+K
is the palette, `?` the sheet; neither fires from a text field. The
views jump with **Alt+Shift+1…0**, in the rail's order (shell.js; each
nav link's `data-keys`): plain Alt+1…4 are a beat's variations (steps.js)
and Alt+1…0 the jam's scenes (jam.js), which let a Shift through. The
rail runs in the order of the job: make (Synths, Kit, Loops, Song,
Mix), find (Library, Breed, Map), audio (Voice, Lab, Clips — the eleventh,
no key). Two rail labels are not their routes: **Loops** is `/view/perform`
and **Kit** is `/view/drums` (the drum page makes kits, its beats
are the Loops panel's); the view keys, module ids, templates and code keep
`perform` / `drums`.

### The shell

`base.html` draws, on every page: the rail (nav, the clip library's
button, `.rail-tools`), the top dock (scope, spectrum, and the **status
strip**: the `status-bar` region — sound, edited, tempo, key — beside
`#meter`, `#cpu-meter` and `#st-task`, which are the client's and must
stay outside the region's root), `#toasts`, the shortcut sheet, the
palette and `#tip`. A page head is one line in dense: `h1`, `.view-sub`
(what the page is for) and `.head-tools`; how the page works is in
`#head-how`, a popover behind `fragments/head_help.html`'s button. **No
always-visible instruction paragraphs**: a hint is a `title`.

**Full screen**: the rail's `#fullscreen-btn`
(`data-cmd="Full screen: on / off"`) puts the whole document in the
Fullscreen API's full screen — the browser's own bars gone, the studio's
layout unchanged (a window-high view takes the extra height); it is
pressed while full screen holds, however it was left (Esc). shell.js, 7.

**The keyboard's way through** (without it, 501 Tabs to Synths' ▶ and
96 to Perform's first note cell): the page's first Tab stop
is **"Skip to the page"** (`.skip-link`, base.html, shown only on focus:
style.css 16), a jump to `#view` past the rail and the top dock; a view
may add its own at the top of `#view` — Perform's "Skip to the note grid"
and "Skip to the drum grid" — whose `data-focus` names the control to land
on (shell.js: a grid's one Tab stop). **A long list is one Tab stop**, as
the step grids are: Synths' sounds list (audio.js `rove`) lets only the
cursor's card's load button take Tab; ↑ / ↓ / Home / End move it, Enter
loads, Space hears, the Menu key (Shift+F10) is the card's menu for the
rest (＋ layer, ★, rename, delete). `tests/browser/test_shell.py` counts
the Tabs.

Tooltips (shell.js): a `title` shows after 500 ms of hover, at once on
keyboard focus, never for a finger, in the top layer. **Placed by CSS
anchor positioning**:
`showPopover({ source })` makes the control the tooltip's anchor, and
style.css's `.tip` puts it under the control (`position-area: bottom`),
above it or run to one side where the window has no room
(`position-try-fallbacks`), beside the side rail's icons (`data-at-rail`,
`position-area: right`), and hides it while the control is scrolled out
of sight (`position-visibility: anchors-visible`) — nothing measured, no
scroll listener. While a hover's
shows the attribute is lent (`data-tip`) and it is handed back on hide — a script
that reads a control's `title` on click still finds it (a press hides
the tooltip first); one the keyboard's focus opened leaves the `title` on
the control (else a glyph-only button would lose its name while it has
the focus).

**The strip's tempo and key are what the page plays**
(`service.status_context(view)`): the SONG's on Song and Mix
(`contexts.SONG_PAGES`: they play the song, never the loop), the loop's
everywhere else, each `title`d with whose it is; the key is a name ("E
minor"), never a register ("E1"): never the song at 116 bpm under a
strip saying 124, two tempos on one screen. So the region differs by page,
and a view swap carries it out of band as it carries the clip library
(`views-and-regions.md`). The same rule names an owner wherever two
tempos can meet: the Lab's Make cards say "the loop's 124 bpm", "the Song
page's 116 bpm", "this song's 120 bpm", as the booth's "synced to auto
(the loop's, 124 bpm)".

### One context menu, declared on what it is for

`static/menu.js` is the one opener. A right click — which is also the pen's barrel button tapped, a
finger's long press and the keyboard's Menu key / Shift+F10 on the
focused control — opens ONE element, `#ctx-menu` (`.pop.menu.ctx-menu`,
section 15). Arrows / Home / End move, Enter activates, Esc closes and
hands the focus back. As with commands, there is no list of menus:

```html
<li class="cd-clip" data-ctx="{{ c.name }}">           <!-- the thing: its name is the caption -->
  <button data-menu="Hear" class="btn …">▶</button>     <!-- a row, in document order -->
  <summary data-menu="Delete…" class="btn btn-danger …">✕</summary>
</li>
<th class="arr-section" data-ctx-pop="secmenu-{{ sec.id }}">   <!-- it already HAS a menu panel -->
```

- **`data-ctx="name"` + `data-menu="Label"`**: the thing and the controls
  of it the menu lists. A row activates its control (`click()`), so it is
  the same route, confirm and refresh as a hand; it is disabled while the
  control is, ticked while the control is pressed or checked, red when
  the control is a `.btn-danger`. `data-menu` with no value takes the
  control's `aria-label`.
- **`data-ctx-pop="id"`**: the thing already owns a menu on the page — a
  popover or a `<details>` its ⋯ button opens (a Song section's and
  track's, a drum row's tools), or the Song grid's one shared
  `#cell-menu`. That panel opens instead, anchored to the thing; its
  `input[data-from="trackId"]` fields are filled from the thing's
  `data-*`, and `data-ctx-state` is copied onto it.
- **A value control** (a slider, a knob, a select) declares nothing: its
  rows are generated — Learn / Forget MIDI, Reset to default, Put on /
  Take off dial 1-4 — and the rows of the thing it sits in follow under
  a seam.
- **A section** declares nothing either (below).

#### A section's own menu

A right click anywhere in a section — its
background, a button in it, a dial — adds, after the control's and the
thing's rows, the **section's** own, under its name:

- **its dials** (its visible, enabled value controls that a binding can
  name: `deck.toOf` finds them again): **Put these on the deck** (deck.js
  `hold`: four pinned, paged in fours by Page ▶), **Add to ★ Yours**
  (decksets.js `add`), **MIDI learn these** (midilearn.js `learn`: a walk,
  one CC each; "Stop learning MIDI" while it walks) — `keys-and-midi.md`,
  "Which four";
- **its commands**: every control in it with a `data-cmd` (or a
  `data-menu`) that no nested thing (`data-ctx` / `data-ctx-pop`: a
  layer's mute is the layer's menu) or nested section owns, in document
  order, one row each however many controls declare it, the section's
  name off the front ("Mod matrix: a random mix" -> "A random mix").
  Declared once, on the control: the menu is generated from the same
  attributes the palette reads, so a panel's ⋯ Defaults / Randomise, a
  fold, a launcher's pads, a card's buttons are all there with nothing
  listed twice. Past twelve, the rest under **More ▸**;
- **its fold**: a folding rack's Fold / Unfold, a `[data-fold]` button
  that is no command already;
- **its place on the page**, a module's (`data-module`) last, under a
  seam: Collapse to its head / Expand, Pop out / Dock it back, Focus,
  Focus in full screen, Move with the keyboard, Remove from this page (an
  added one), Reset this page's layout — rows workspace.js hands over
  (`synthlab.workspace.rows`; "Workspace: modules you can move"), for a
  press on the module itself — its background, its head, a button — not
  on a thing (`data-ctx`) or a value control in it, whose menus stay
  theirs. They come after the listing of the sections in it, which still shows for a
  module with no rows of its own (Perform's Launchers);
- a section with none of these lists **the sections in it** as submenus
  (Perform's Launchers: each launcher; the Visuals gallery: each card).

**What a section is** (`SEC` in menu.js): `[data-module]` — a movable
module's stable id — `[data-menu-section="Name"]` (where the markup has no panel of
its own: the chain bar, the FX rack, a Visuals card), a `fieldset`, a
`<section>`, an `.fx-unit` or a `details.rack`; named by
`data-menu-section`, `data-ctx`, `aria-label` / `aria-labelledby`, else
its legend's `.panel-name`, legend, summary, header or heading. **One
without a name is a wrapper**, not a section, and is skipped. deck.js's
focus mode takes its panel by the same attributes first.

**Nested**: the innermost section with rows is in place; each section
round it is one row, "Name ▸" (`aria-haspopup`), which shows its rows in
the same menu with "◂ <inner>" on top — → opens one, ← or ◂ comes back,
Esc closes. A thing that is itself a section (an FX unit, a mixer strip)
gets one caption: its `data-menu` rows, then the section's.

**The browser's own menu** stays on a text field, on a link (open in a
new tab) and on selected text (copy) — unless a thing claims them — and
on what is in no section at all (a page's title). The gestures table's
owners hold: a value control's rows come first, a step's right
click is grid.js's erase, a pen's on a pen surface is pen.js's.
`tests/browser/test_context_menu.py::test_a_sections_own_menu_on_every_kind_of_panel`.

`#ctx-menu` is moved inside the open popover or dialog its target is in,
so it nests: it does not close the clip drawer, and Esc closes the menu
alone. Text fields keep the browser's menu. **Two rules stand
exactly**: on a step grid a right click ERASES (grid.js), and a pen's
`contextmenu` on a pen surface — the grids, the lanes, the keys — is
cancelled by pen.js before menu.js hears it. In use on: a layer, an FX
unit, a mod route, a sound, a slice (Synths); a section, a track, a
block (Song); a drum row (Drums, Perform); a library row, a breed card,
a map row, a clip row, a chop, a Lab song; every slider, knob and select.
A long list's per-row actions belong here rather than in `data-cmd` (a
hundred rows would flood the palette).
`tests/browser/test_context_menu.py` opens one a view by mouse, keyboard
and pen, and pins that no other island opens a menu.
