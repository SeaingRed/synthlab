# htmx 4 integration checkpoints

Vendored: **htmx 4.0.0 final** (`static/htmx.min.js`), plus the official
`hx-prompt` extension (`static/hx-prompt.min.js`).

Five things to check in a real browser after any htmx upgrade (the test
suite cannot catch most of them).

## Status at 4.0.0

1. **Nav anchor interception** — capture-phase guard in audio.js still wins
   over htmx's own handler; `#view` swaps and `hx-push-url` both fire.
   *Verified.*

2. **Error detail shape** — `htmx:response:error` still exposes
   `ev.detail.ctx.response.status` and `ev.detail.ctx.text`. The 422 toast
   path in audio.js depends on both. *Verified: status 422, text a string.*

3. **OOB swap behaviour** — OOB fragments are still processed
   unconditionally, including on 4xx. The toast design (elements use
   `hx-swap="none"`, the message is parsed out of `ctx.text`) depends on it.
   *Verified: a bad mini-notation surfaced "unexpected '.'" as a toast.*

4. **Container `changed` modifier** — still not used anywhere, and still not
   re-enabled. It was broken on container-level triggers in beta6; nobody
   has re-tested it on 4.0.0. Leave it alone.

5. **noSwap pattern matching** — `["4xx","5xx"]` string patterns still work.
   *Verified via the 422 path above.*

## API surface: beta6 -> 4.0.0

Diffed both minified builds. **No htmx event was added or removed.** The
verb attributes are now generated from a `#verbs` array
(`get post put patch delete query` — `query` is new) rather than written as
literal strings, which is a codegen change, not a behaviour change.

The one real break for this codebase was `hx-prompt`, which 4.0 moved out
of core. The official extension restores it byte-for-byte, including the
`HX-Prompt` request header that `server.py` reads when saving a panel
preset. Override `window.htmxPrompt` to replace the native dialog.

## Morph swaps — adopted

All 13 full-body swaps use `hx-swap="innerMorph"`, and `lastFocus` is gone.
Measured on a 2519-node editor: **73.9ms -> 46.1ms (~38%)** per preset load.
`innerHTML` destroys the focused node (focus fell to `<body>` and had to be
put back by hand); `innerMorph` keeps node identity, so focus survives on
its own.

Two findings worth keeping:

- The docs warn morphing "cannot be used to reset forms". That is about
  *unchanged* markup — a user's typed value survives when nothing changed.
  It does **not** block loads or resets: verified that a slider dragged to
  777 correctly becomes 2200 on reset-to-defaults, and every load path ends
  with the DOM matching `/patch.json`.
- Morph keeps the Library's search box but the cards arrive with the
  *server's* `hidden` state, so the box read "bass" above all 96 sounds.
  Client-side filter state has to be re-asserted after a swap
  (`htmx:after:settle -> applyLibFilter`). This is now better than before,
  where the filter was lost entirely.

## Things 4.0.0 offers that this app does not use yet

- **`<hx-partial>`** — clearer semantics than the 9 `hx-swap-oob` sites.
- **`hx-preload`** — hover-preload for the seven view fragments.

## Migration burden that did NOT apply

Zero uses of `hx-disinherit`, `hx-vars`, `hx-disable`, `htmx.addClass()`,
`htmx:validation:*`, or implicitly-inherited attributes, so the `:inherited`
migration — the headline cost of 4.x — cost nothing here.
