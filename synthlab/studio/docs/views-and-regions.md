# Views, regions and form posts

What a view renders (`views.VIEW_PARTS`), the region / out-of-band
refresh contract (`service.REGIONS`, `tests/studio/test_oob_refresh.py`)
and the rules for the forms that post on every input
(`_merge_arrangement`, `forms.patch_from_form`, audio.js's
`syncControls`). Index: `README.md`.

## Views are isolated workshops

**Views are isolated workshops.** Every preview renders only the view's
own `parts` (`views.VIEW_PARTS`): Editor/Bass/Breed/Map = `synth`,
Drums = `drums`, Perform = both. `view_of(request)` reads htmx's
`HX-Current-URL`; `POST /fragment/preview` (audio.js's Play fallback)
takes an explicit `view`. `RenderInputs.key()` only hashes what those parts
render, so synth edits never invalidate a drums preview. Drum-bank
actions save first and report preview failures via an `HX-Trigger:
studio-toast` (handled in `perform.js`) — never a 422. The editor's
transport has no drum rows; `_merge_arrangement` keeps drums/drum_params
when a form doesn't post `drum.*` cells. The drum transport and grid also
sit on Perform, so `routes/drums.py` previews the *request's* view
(`_bank_response` re-cues `bank_response`'s drums-only preview for
another view).

## No stale regions

**No stale regions** (`tests/studio/test_oob_refresh.py`):
one page draws the same state in several places. A route that had to
remember every region its change touched would leave the ones it forgot
stale (an FX edit touches the chain bar and the reset menu; a Perform
parts/sound launch, a kit preset and a macro assignment the reset
menu). **The contract: a region declares what it shows; routes
never name regions.** `service.REGIONS` (`service/regions.py`; the
machinery that sends them is `service/refresh.py`) is the one table, `{id:
Region(template, needs, pages, watch, context=None, inside=None)}`:
`watch(svc, view)` is a cheap signature (`_sig`, a `repr` taken now) of
exactly the state the region draws — never a render or a
`page_context`; `pages` are the views that draw it; `context` an
optional lean context for hot paths (else `page_context(need=needs)`);
`inside` names the region it is drawn inside (`sound-id` in `sound-bar`,
whose watch leaves the inner one's state out). `StudioRoute`'s wrapper
(`routing.py`, the `render_outside_lock` path included) snapshots the
watches of the requesting page (`svc.view_of`, off `HX-Current-URL`:
an island's own `fetch()` post must send it too — `synthlab.pageHeaders`
does — or it reads as Synths' and the page's own regions never come
back) around every non-GET handler and `svc.refresh` appends, OOB, each region whose watch moved —
unless the response already draws it (its main target, e.g. `bank_response`'s
`#drum-bank`, or an outer region carrying it) or the handler called
`svc.suppress(...)`. Only an HTML 200 that swaps part of the page gets
regions (a bodiless 204 whose state moved becomes a 200): a whole page
(`full_view_page` marks `whole_page`), `HX-Location`/redirects, JSON,
toasts and errors get none (htmx 4 processes OOB even on a 4xx).
**Suppress the form that posted** when redrawing it would drop the
gesture: the drum grid (`drum-bank`), a drum tempo/feel edit
(`drum-transport`), a non-structural `/fragment/arrangement`
(`sound-bar`/`transport`), the Song drawer's grid unless the pattern's
shape moved (`song-drawer`), the latency field (`song-topbar`). **Never
OOB a launcher the click just marked "queued"** (perform.js owns that
state until `loop-switched`): a beat launch suppresses `launcher`. The
launcher's region **`slots`** is the exception that is sent: its slots
change lanes, sounds and stops the answer must draw, so it comes back
(morphed) with the launch, already drawing the new slot playing, and
slots.js re-applies its "waiting" mark (`data-q`) over it after every
swap until `loop-switched` (`loop-and-perform.md`, "Slots and scenes").
**A new region** is one `REGIONS` entry
(its template's root carries the page's id and `hx-swap-oob` when
rendered with `oob`); **a new route** needs nothing but a `CASES` row in
the test, which renders every region of the requesting page before and
after each POST and requires exactly the changed ones back (minus the
declared suppressions), each the current render, and — on every page —
that a region's `watch` moved whenever its HTML did
(`test_every_post_route_has_a_case` keeps the table complete). A watch
must be precise both ways: too narrow leaves the region stale, too wide
re-sends it for nothing (the drum bank watches mute/solo, not levels).
Cost: the snapshot adds well under 1 ms (`/fragment/patch` measured).

### Regions outside `#view`: the clip library, the status strip

**`clip-library` is a region every page draws and no view template
includes**: it sits in `base.html`'s top bar (`sampler-and-clips.md`,
"The clip library"). Three things follow that no other region needs
(but the first, the status strip's: below):

- **A view swap must carry it.** `#view` is what the nav swaps, and the
  drawer differs by page (what a clip can do there), so
  `routes/views._view_response` appends it out of band to every htmx
  view answer — the one place a route names a region's template. (That
  answer also starts with the page's `<title>` and says `Vary:
  HX-Request`; Back and Forward swap it into the view root, which is
  `hx-history-elt` — `frontend.md`, "Back, Forward and a link to another
  page".)
- **It morphs, always** (`hx-swap-oob="outerMorph"`, and every control
  that targets it): it is a popover, and a replaced popover is a closed
  one.
- **Its context reads the request** (`StudioService.asked`): the search
  and the Place bar's choices that the drawer's own controls sent with
  the post, so a redraw — out of band included — keeps them.
  `routing._form` leaves the parsed form on `request.state.form` for
  that. A post that carries none (every post from outside the drawer)
  redraws it unfiltered.

Its watch runs on every page, so it is the dearest thing the snapshot
does where clips are many: 0.75 ms with 30 clips and 12 separated Lab
songs (the directory listing of the clip store is 0.6 ms of that, and
Synths, Perform and Drums paid it before). No file is opened
(`test_the_watch_reads_no_clip_and_no_source`).

**The status strip** (`status-bar`, base.html's top dock) is the other
one, and a view swap carries it too, out of band and morphed, in the same
place (`routes/views._view_response`): its tempo and key are the song's
on Song and Mix and the loop's elsewhere (`status_context(view)`;
`frontend.md`, "The shell"), so its watch reads the view.

**A region that holds a popover is morphed**: the reset
menu (`reset-menu`, every page's head) is a popover
(`frontend.md`, "A menu that drops down: a popover"), and its region is
re-sent on nearly every edit to move its "changed" dots — replaced
(`outerHTML`) it would shut the open menu under the hand; it is
`hx-swap-oob="outerMorph"`, like the clip library.

## Form posts

### `_merge_arrangement` preserves per lane

**`_merge_arrangement` preserves per lane**: `arrangement_from_form`
rebuilds from scratch, so every field whose prefix the posting form does
not carry (`cell.`/`vel.`/`slide.`/`drum.`, plus bpm and the grid shape)
is copied from current state. Without that, one slider nudge on Synths
wipes the Perform pattern. **The grid's own post carries `span`**
(transport.html's hidden field), and with it an absent `cell.*` /
`slide.*` means *off*, not "not posted": an unchecked box posts nothing,
so otherwise the last note clicked off would stay on the server while the
page showed an empty grid (`test_the_grids_own_post_can_empty_the_grid`).
The strips are regions (`sound-bar` on
Synths, `transport` on Perform); `/fragment/arrangement` suppresses the
one that posted unless the key, the length or a clamped bpm reshaped it
(a phrase pill's click must reach audio.js). A grid post only
replaces what its checkboxes could show (`_carry_over`: the *current*
key's rows x the current bars·steps): notes, velocities and slides
outside that — or ones the new key/length has no row for — stay, hidden,
so changing root/scale or shrinking bars never deletes a note.

### A patch post applies only what moved

**A patch post applies only what moved** (`forms.patch_from_form`): every
post carries every control, so a control posting back the value it was
drawn with (`_unmoved`, compared as the slider represents it — step-snapped
from its min; a log slider's hidden field is exact) is skipped, and a
moved one clamps to its `params` cap (what `params.clamp` accepts), not
the gene range. `param_row.html` widens a linear slider's min/max to take in a
value outside the spec, because a browser clamps a range input to them
(else Laser Zap's -2600 Hz filter env would become 0 on the first nudge).
The XY pad answers with its two rows OOB, like a macro move.

### Swaps re-assert live values

**Swaps re-assert live values** (`audio.js` `syncControls`,
`tests/studio/test_value_sync.py`): htmx 4's morph copies attributes
only, and a touched control's live value no longer follows its
attribute, so a redraw with the value the page was first drawn with
(Undo, Defaults, Morph) left the thumb where the hand left it and the
next edit posted it back. After every settle each swapped input/select
takes its state from its attributes — except client-owned controls
(`CLIENT_OWNED`: volume, space, search boxes, the chord bar), the one
under the pointer, and the one edited in the last 1.5 s until the hand
moves on (`data-springs` opts the morph slider back in).
