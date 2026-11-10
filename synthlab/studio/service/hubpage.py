"""The clip hub's context (`/view/clips`, `cliphub.py` the model).

`hub_context` is what `views/clips.html` and its region draw: the list
(`hub-list`: every clip, sample and Lab stem, filtered and sorted, each
with where the session uses it, and the hub's ↶ Undo) and — for the page
and the bench fragment — the selected item's bench (`hub_bench_context`:
its facts, the waveform, the player, tune, send to, make from it).

**The hub keeps its choices in its URL**, never in the session:
`/view/clips?item=clip:<name>&find=&show=&order=` (names no other form on
a page posts: the drawer's search is `q`). Every request the page
makes carries that URL (`HX-Current-URL`), so a region redrawn after any
post — a send from the bench, a delete in the drawer — keeps the filter
and the selection (`hub_params`); the hub's own GETs name what moved and
answer with `HX-Replace-Url`.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from fastapi import Request

from .. import cliphub, cliplib, clipmake, clipmorph
from ..cliphub import HubSession, Ref
from ..clips import clip_safe
from ..state import DRUM_ROWS, StudioState

PARAMS = ("item", "find", "show", "order")
# the last item this browser heard or opened on the hub (cliphub.js writes it)
HUB_COOKIE = "hub_item"


class HubPage:
    """`hub_params`, `hub_url`, `hub_context`, `hub_bench_context`,
    `hub_undo_label`."""

    # the service's fields this unit reads (set in StudioService.__init__;
    # test_service_split.py holds this list to the code)
    state: StudioState
    hub: HubSession

    def hub_params(self, request: Request) -> dict:
        """The hub's choices: the page's URL (`HX-Current-URL`), with what
        this request's own QUERY names over it (a GET of the hub: the page
        itself, the bench, the list) and, last, what a whole-page answer
        was asked to draw (`request.state.hub`: a clip just made). Never a
        posted form's: a post's `item` is the thing it acts on, and every
        region it redraws must draw the page as the URL has it. Every
        value is cut and checked by its reader."""
        out = dict.fromkeys(PARAMS, "")
        url = request.headers.get("HX-Current-URL") or ""
        if "/view/clips" in url:
            q = parse_qs(urlsplit(url).query)
            for k in PARAMS:
                if q.get(k):
                    out[k] = q[k][0][:240]
        has_query = "query_string" in request.scope
        override = getattr(request.state, "hub", None) or {}
        for k in PARAMS:
            # a key the query carries wins even when empty: a search box
            # emptied is "no search", not "the page's last one"
            got = request.query_params.get(k) if has_query else None
            got = override.get(k, got)
            if isinstance(got, str):
                out[k] = got[:240]
        out["find"] = cliphub.clean_query(out["find"])
        out["show"] = out["show"] if out["show"] in cliphub.KIND_LABELS else "all"
        out["order"] = out["order"] if out["order"] in dict(cliphub.SORTS) else "name"
        return out

    @staticmethod
    def hub_url(params: dict) -> str:
        """The page's URL for these choices (only what is not the default)."""
        keep = {
            k: v
            for k, v in params.items()
            if k in PARAMS and v and (k, v) not in (("show", "all"), ("order", "name"))
        }
        return "/view/clips" + (f"?{urlencode(keep)}" if keep else "")

    def hub_context(self, request: Request, *, bench: bool = True) -> dict:
        """The list and the selected item's facts (and its bench, unless
        `bench` is False: a region redraw needs neither the bench nor the
        measuring it does)."""
        s = self.state
        p = self.hub_params(request)
        found = cliphub.items(s.data_dir, s.clip_usage(), cliphub.sample_usage(s))
        ref = cliphub.parse_ref(p["item"])
        item = next((it for it in found if ref and it.ref == ref.text), None)
        shown = cliphub.view(found, p["find"], p["show"], p["order"])
        if item is None and not p["item"]:
            # nothing asked for: the bench is never empty — the
            # last item this browser heard or opened, else the first the list
            # shows that plays (a stem streams minutes). One asked for that
            # is gone stays so: the bench's route toasts it.
            item = self.hub_default(request, found, shown)
            ref = cliphub.parse_ref(item.ref) if item is not None else None
        if (
            bench
            and item is not None
            and ref is not None
            and ref.kind in ("clip", "sample")
            and cliphub.measured(s.data_dir, ref) is None
        ):
            # the bench measures its item (hub_bench_context): measured
            # first, so its row says the tempo / key the bench does in this
            # same answer — the list drawn before it was missing them until
            # some later redraw, which the hub-list watch (it counts what is
            # measured) never asked for
            cliphub.measure(s.data_dir, ref)
            found = cliphub.items(s.data_dir, s.clip_usage(), cliphub.sample_usage(s))
            item = next((it for it in found if it.ref == ref.text), item)
            shown = cliphub.view(found, p["find"], p["show"], p["order"])
        ctx = {
            "params": p,
            "items": shown,
            "counts": cliphub.counts(found),
            "kinds": cliphub.KINDS,
            "sorts": cliphub.SORTS,
            "item": item,
            "selected": item.ref if item else "",
            "bench": None,
        }
        if bench and item is not None and ref is not None:
            ctx["bench"] = self.hub_bench_context(request, item, ref, p)
        ctx["undo"] = self.hub_undo_label()
        return ctx

    @staticmethod
    def hub_default(
        request: Request, found: list[cliphub.Item], shown: list[cliphub.Item]
    ) -> cliphub.Item | None:
        """The item the bench opens when the URL names none: the browser's
        last heard or opened (the `hub_item` cookie, cliphub.js: a per-browser
        convenience like the theme, never session state) while it is there,
        else the first row shown that plays, else the first row."""
        last = cliphub.parse_ref(unquote(request.cookies.get(HUB_COOKIE, ""))[:240])
        hit = next((it for it in found if last and it.ref == last.text), None)
        if hit is not None:
            return hit
        return next(
            (it for it in shown if it.kind != "stem"), shown[0] if shown else None
        )

    def hub_undo_label(self) -> str:
        """What the hub's ↶ Undo would take back ("" when nothing)."""
        return self.hub.last.label if self.hub.last else ""

    def hub_bench_context(
        self, request: Request, item: cliphub.Item, ref: Ref, params: dict
    ) -> dict:
        """The selected item's bench. A clip or a sample is measured here
        (its tempo and key, once per version of its file); a stem is not
        (minutes long: the Lab reads it)."""
        s = self.state
        # the tune a GET asks to hear (the player's); a post's is not drawn
        query = request.query_params if "query_string" in request.scope else {}
        tune = cliphub.clean_tune(query)
        playable = ref.kind in ("clip", "sample")
        if playable:
            cliphub.measure(s.data_dir, ref)
        query = cliphub.tune_query(tune)
        audio = (
            f"/hub/audio.wav?{urlencode({'item': ref.text})}"
            + (f"&{query}" if query else "")
            if playable
            else item.audio
        )
        section = self.asked(request, "place_section")
        usage = s.clip_usage().get(ref.name) if ref.kind == "clip" else None
        used = cliplib.update_note(usage)
        return {
            "ref": ref.text,
            "kind": ref.kind,
            "clip": ref.name if ref.kind == "clip" else "",
            "playable": playable,
            "wave": cliphub.wave_path(cliphub.peaks(s.data_dir, ref))
            if playable
            else "",
            "audio": audio,
            "tune": tune,
            "tuned": not cliphub.is_identity(tune),
            "tune_ranges": cliphub.TUNE_RANGES,
            "update_confirm": (
                f"Write the tuned audio over “{clip_safe(ref.name)}”? It is used "
                f"in: {used}. The hub's Undo puts the old audio back."
                if used
                else f"Write the tuned audio over “{clip_safe(ref.name)}”? The "
                "hub's Undo puts the old audio back."
            ),
            "place": cliplib.place_context(
                s.song,
                section,
                self.asked(request, "at_bar") if section else "",
                self.asked(request, "track_id") if section else "",
            ),
            "drum_rows": DRUM_ROWS,
            "pick": clipmake.labels(),
            "sounds": clipmake.sound_catalogue(),
            "ai": clipmorph.available(),
            "morph_seconds": clipmorph.MORPH_SECONDS,
            "max_describe": clipmorph.MAX_DESCRIBE,
            "facts": cliphub.measured(s.data_dir, ref) if playable else None,
            "used": item.used,
            "loop": {
                "bpm": s.arrangement.bpm,
                "key": f"{s.arrangement.root[:-1]} {s.arrangement.scale_name}".replace(
                    "_", " "
                ),
            },
        }
