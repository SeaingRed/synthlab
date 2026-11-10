"""The page: `page_context`, whole-page answers and the lean fragments.

**Lean fragment contexts**: `page_context` builds
only the expensive sections a template asks for (`need=`). A full page
takes `ALL`; the fragments that re-render one strip (`bank_response`,
the regions' `needs`, `drum_panels_fragment`) name just what their
templates reference, so a click on a drum-bank slot no longer re-reads
the voice take, the clip store and every preset from disk.

`full_view_page` answers with the requesting view's whole page (and marks
it `whole_page`, which `Refresh.refresh` reads), `full_page_with_preview`
adds its preview; `bank_response`, `drum_panels_fragment` and
`clips_fragment` are the fragments several routes share. The cookie
readers `theme_of` / `density_of` are here because the page is what they
dress.

A mixin of `StudioService` (the package docstring says why mixins).
"""

from __future__ import annotations

import json

from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import (
    beatgen,
    beatpick,
    chords,
    cliplib,
    limits,
    loopchat,
    loopcoach,
    looplib,
    loops,
    notation,
    samplerview,
    sounds,
    takecoach,
)
from ..agent import AgentService
from ..audition import PHRASES, has_slides
from ..clips import list_clips
from ..curves import CURVABLE
from ..drumfactory import DRUM_KITS
from ..factory import FACTORY
from ..fxrack import CHAIN_PRESETS, FX_UNITS, clean_chain
from ..library import tags as library_tags
from ..paramui import PARAM_GROUPS, SHAPE_DEPS, idle
from ..recipes import cards as recipe_cards
from ..recipes import core_cards
from ..recipes import tags as recipe_tags
from ..render import RenderError
from ..soundmeta import filter_response_svg
from ..state import (
    DRUM_ROWS,
    ROOTS,
    SCALE_CHOICES,
    StudioState,
)
from ..state.model import BASS_ROOTS, grid_rows
from ..views import view_title
from ..visuals import FAMILIES, VARIATIONS
from ..voice import BAND_CHOICES, MAX_TAKE_SECONDS


def library_by_family(*args, **kwargs):
    """`library.by_family`, read off the package on every call: it has
    always been patchable as `synthlab.studio.service.library_by_family`
    (tests/studio/test_routes.py), and stays so."""
    from . import library_by_family as real

    return real(*args, **kwargs)


# -- page_context sections ---------------------------------------------------
# The expensive parts of a page's context, each built only when asked for:
#   voice   — the take on disk (read + decoded)
#   clips   — the clip store listing
#   cliplib — the clip library drawer (base.html, every page): the listing
#             grouped by where each clip came from, where the session uses
#             each, the Lab's separated songs
#   presets — saved presets' metadata, the factory list, panel presets
#   sounds  — the one sound menu (base.html's `#lane-sounds`, every page:
#             a Song lane's <select>, Perform's lane heads): `sounds.menu`
#   library — the 96-sound shelf and its tags
#   chords  — chord/note/harmonic pad sets for the key
#   drums   — drum-lab values + hashes, pad hashes and the drum XY, the beat
#             library listing, the notation line
#   resets  — the reset menu's rows and "changed" dot (computed once)
#   song    — the Song page's regions and Synths' track-edit banner: the
#             song, pattern usage, its length, the drawer, pool thumbnails
#   takes   — the Loops list (`looplib.context`): the
#             keyboard dock's Loops strip on every page, and Perform's
#             Loops panel (`PANEL_PAGES`)
SECTIONS = frozenset(
    {
        "voice",
        "clips",
        "cliplib",
        "presets",
        "sounds",
        "library",
        "chords",
        "drums",
        "resets",
        "song",
        "takes",
    }
)
SONG_NEEDS = frozenset({"song"})
ALL = SECTIONS
# what each lean fragment's template references (read from the templates;
# tests/studio/test_routes.py renders each lean and with ALL and compares):
#   drum_bank.html   — the clip select per row, notation (the beat library
#                      is the Loops panel's: no list here)
#   drum_panels.html — the drum-lab values/hashes/presets
#   sound_bar.html (+ ab_slots.html) and transport.html — nothing expensive
BANK_NEEDS = frozenset({"clips", "drums"})


def song_sections(song: dict) -> tuple[tuple[str, str], ...]:
    """The first four sections of the song, (id, name): what the Drums
    page's empty grid offers to open into the loop (`/song/section/{id}/to-loop`)
    — after a starter the song's beats are its patterns, not the loop's."""
    return tuple((sec["id"], sec["name"]) for sec in song["sections"][:4])


DRUM_PANELS_NEEDS = frozenset({"drums"})
BAR_NEEDS: frozenset[str] = frozenset()
#   clip_library.html — the drawer's own section
CLIPLIB_NEEDS = frozenset({"cliplib"})
#   key_takes.html — the keys takes: the dock's strip (region `key-takes`,
#   every page); loops_panel.html, Perform's Loops panel (region `loops-panel`)
TAKES_NEEDS = frozenset({"takes"})
PANEL_PAGES = frozenset({"perform"})
# the pages that draw the patch's parameter rows (param_row.html: Synths'
# panels), which read `idle` — the controls that change nothing now
PARAM_PAGES = frozenset({"editor"})


def theme_of(request: Request) -> str:
    cookie = request.cookies.get("theme")
    if cookie in ("day", "night"):
        return cookie
    return "system"


def density_of(request: Request) -> str:
    """The workbench density (layout.js's `density` cookie): compact unless
    the cookie asks for the roomier layout."""
    return "comfy" if request.cookies.get("density") == "comfy" else "compact"


def pen_of(request: Request) -> bool:
    """A pen has been used in this browser (tweak.js's `pen` cookie): the
    page then links the dense tier whatever the primary pointer reports,
    and the touch tier stands down (`html[data-pen]`)."""
    return request.cookies.get("pen") == "1"


class Page:
    """`page_context`, the whole-page answers and the shared fragments."""

    # the service's fields this unit reads (all set in
    # StudioService.__init__; test_service_split.py holds this list to the code)
    state: StudioState
    templates: Jinja2Templates
    agent: AgentService

    # -- the page ----------------------------------------------------------
    def page_context(
        self,
        request: Request,
        view: str = "editor",
        *,
        need: frozenset[str] = ALL,
    ) -> dict:
        """The template context for `view`. The cheap values are always
        there; each expensive section (see SECTIONS) only when `need`
        names it."""
        s = self.state
        patch_values = {
            spec.name: getattr(s.patch, spec.name)
            for _, specs in PARAM_GROUPS
            for spec in specs
        }
        # the wavetable picker's own tables are radios of wt_table's group
        # (param_row.html), checked by this field, which has no row
        patch_values["wt_custom"] = s.patch.wt_custom
        ctx = {
            # a song to replace? (the empty states' "Start a song" asks first)
            "song_live": bool(s.song["sections"]),
            "request": request,
            "theme": theme_of(request),
            "density": density_of(request),
            "pen": pen_of(request),
            # a page loaded into a frame is the Song page's sound dock: no
            # rail, no top dock. The dock asks (`?embed=1`) and the browser
            # says so too — but only to a trustworthy origin, so not over
            # a LAN share. Nothing to forge: it only hides chrome
            "embed": request.headers.get("sec-fetch-dest") == "iframe"
            or (
                "query_string" in request.scope  # (a hand-built test request)
                and request.query_params.get("embed") == "1"
            ),
            # the clip library pinned beside the page (shell.js's cookie)
            "clips_pinned": request.cookies.get("clips") == "pinned",
            "current_view": view,
            "view_title": view_title(view),
            "groups": PARAM_GROUPS,
            "shape_deps": SHAPE_DEPS,
            "filter_response": filter_response_svg(s.patch),
            "env_amp": self.env_amp_svg(s.patch),
            "env_filter": self.env_filter_svg(s.patch),
            "sampler": samplerview.context(s.patch),
            "curvable": set(CURVABLE),
            "has_curve": {name for name, pts in s.curves.items() if len(pts) >= 2},
            "patch": s.patch,
            "patch_values": patch_values,
            # controls that change nothing now, and why (paramui.idle): read
            # by the parameter rows alone
            "idle": idle(s.patch, has_slides(s.audition))
            if view in PARAM_PAGES
            else {},
            "arrangement": s.arrangement,
            # the status strip, every page
            **self.status_context(view),
            "pitch_rows": grid_rows(s.arrangement, s.grid_span, s.grid_oct),
            **self.grid_context(),
            "drum_rows": DRUM_ROWS,
            "drum_bank": s.drum_bank,
            "active_slot": s.active_slot,
            # the bank's old "Save to slot" defaults (the Kit page's ✚ Save
            # beat shows `save_name` as its placeholder; the slot is unused)
            "save_slot": s.default_save_slot(),
            "save_name": s.default_pattern_name(),
            "beat_styles": beatgen.STYLES,
            "beat_style_groups": beatgen.style_groups(),
            "drum_kits": [(k, v["style"]) for k, v in DRUM_KITS.items()],
            "beat_style": s.beat_style,
            "beat_seed": s.beat_seed,
            # the beat's variations A-D: (letter, on the grid, holds a beat)
            "beat_variants": s.variation_names(),
            "roots": ROOTS,
            "bass_roots": BASS_ROOTS,
            "bass_root": s.bass_root,  # the Bass page's quick key in force
            "band_choices": BAND_CHOICES,
            "max_seconds": MAX_TAKE_SECONDS,
            "scales": SCALE_CHOICES,
            "phrases": PHRASES,
            "audition": s.audition,
            "vs_beat": s.vs_beat,
            # Synths' Beat ▾, its kit and ▶ beat (beatpick.py)
            # (+ the beat's render key: ▶ beat refetches only when it moves)
            "beat_pick": {**beatpick.context(s), "key": self.beat_key()},
            "voicings": list(chords.VOICINGS),
            "total_steps": s.arrangement.bars * s.arrangement.steps_per_bar,
            "velocities": s.arrangement.velocities,
            "breed_wildness": getattr(s, "breed_wildness", "normal"),
            "breed_depth": getattr(s, "breed_depth", "single"),
            # the lanes a layered card's "Use with its layers" would replace
            "busy_lanes": s.lanes_with_notes(),
            "slot_a": s.slot_a,
            "slot_b": s.slot_b,
            "ab_live": s.ab_live,
            "layers": s.layers,
            "active_layer": s.active_layer,
            "fx_units": FX_UNITS,
            "chain_presets": CHAIN_PRESETS,
            "fx_layer": clean_chain(s.layers[s.active_layer].get("fx")),
            "fx_master": clean_chain(s.fx_master),
            "fx_drums": clean_chain(s.fx_drums),
            "fx_scopes": self.fx_scopes_for(view),
            "perform_parts": list(s.perform_parts),
            # Perform's Beats launcher: the pool's drum patterns (the bank is
            # the pool now) with the one the working loop is playing
            **self.pool_beats(),
            "tiles": s.breed_tiles,
            "starred": set(s.breed_starred),
            "active": None,
            "history": s.breed_history,
            **self.xy_context(),
            **self.macro_context(),
            **self.sound_context(),
            **self.layer_context(),
            "lane_follow": self.lane_follow(),
            # the saved loop the note grid is editing (its banner)
            "loop_edit": self.loop_editing(),
            # the Sound agent drawer (runtime session: log, budget)
            "agent": self.agent.view(),
        }
        if view == "perform":
            # the launcher's lanes x scenes (loops.py's listing: cached)
            ctx["slots"] = self.slots_context()
            # the lane inspector: the selected lane, the heads
            ctx["insp"] = self.lane_context()
            # ▶ Whole song asks before it replaces a song (#perform-arrange)
            ctx["song_filled"] = bool(s.song.get("sections"))
        if view == "lab":
            # the Sample Lab reads its own store; no other page draws it
            ctx["lab"] = self.lab_context()
        if view == "mixer":
            ctx.update(self.mixer_context())
        if view == "clips":
            # the clip hub reads the stores and its own URL (`hub_params`)
            ctx["hub"] = self.hub_context(request)
        if view == "visuals":
            # the gallery's cards: what static/visuals.js draws, by name,
            # grouped by family
            ctx["visuals"] = VARIATIONS
            ctx["visual_families"] = FAMILIES
        if view == "perform":
            # the loop coach: its body (region `coach-panel`) and, for the
            # dock's message box, whether it can talk (a key, the SDK)
            ctx["coach"] = loopcoach.context(s)
            ctx["coach_why"] = loopchat.available()
            ctx["coach_audio"] = takecoach.load_config().audio
        if "takes" in need:
            # the Loops list (looplib.py): the keyboard dock's strip (every
            # page: this session's), and Perform's Loops panel (every one)
            ctx["key_takes"] = self.loops_context(view, session=True)
            if view in PANEL_PAGES:
                ctx["loops_panel"] = self.loops_context(view, bank=True)
        if "voice" in need:
            # voice lab: the take lives on disk, so the page reads its state
            # from there rather than from anything cached in the session
            ctx.update(self.voice_context())
        if "clips" in need:
            ctx["clips"] = list_clips(s.data_dir)
        if "cliplib" in need:
            ctx.update(self.clip_library_context(request, view, ctx.get("clips")))
        if "presets" in need:
            # (with `layered`: Load asks what a layered one replaces)
            ctx["presets"] = sounds.yours(s)
            ctx["factory"] = [
                {k: e[k] for k in ("name", "tags", "spectrum")} for e in FACTORY
            ]
            ctx["panel_presets"] = {
                name: s.list_panel_presets(name) for name, _ in PARAM_GROUPS
            }
        if "sounds" in need:
            ctx["sound_menu"] = sounds.menu(s, ctx.get("presets"))
        if "library" in need:
            ctx["library"] = library_by_family()
            # the genre recipes' shelf; the core recipes are on the Core
            # shelf (`core_cards`: by category, with what it lists besides)
            ctx["recipes"] = [c for c in recipe_cards() if "core" not in c["tags"]]
            ctx["core"] = core_cards()
            ctx["library_tags"] = library_tags() + [
                t
                for t in [cat for cat, _ in ctx["core"]] + recipe_tags()
                if t not in library_tags()
            ]
        if "chords" in need:
            ctx.update(self.chords_context())
        if "drums" in need:
            ctx["song_sections"] = song_sections(s.song)
            ctx["notation"] = notation.to_notation(
                s.arrangement.drums, s.arrangement.drum_steps, s.arrangement.drum_bars
            )
            ctx.update(self.drums_context())
            ctx.update(self.dxy_context())
        if "song" in need:
            ctx.update(self.song_context())
            # the starter sheet's first card is the style last started in
            # this browser (a cookie)
            ctx["song_style_last"] = request.cookies.get("synthlab_style")
        if "resets" in need:
            ctx.update(self.reset_context(view))
        return ctx

    def full_view_page(
        self,
        request: Request,
        extra_headers: dict | None = None,
        view: str | None = None,
    ) -> Response:
        """Render `view_page.html` for whichever view the request came
        from — a reset on Drums must not swap you onto the editor. `view` overrides
        that for the handful of actions that deliberately move you somewhere
        else (picking a Library sound lands on Synths); pair it with an
        HX-Push-Url so the URL agrees with the screen."""
        view = view or self.view_of(request)
        ctx = self.page_context(request, view)
        ctx["view_template"] = f"views/{view}.html"
        resp = self.templates.TemplateResponse(request, "view_page.html", ctx)
        for k, v in (extra_headers or {}).items():
            resp.headers[k] = v
        resp.whole_page = True  # every region is in it: `refresh` adds none
        return resp

    def full_page_with_preview(
        self, request: Request, view: str | None = None, push_url: str | None = None
    ) -> Response:
        """full_view_page() plus a preview render; RenderError becomes a
        toast, not a 500.

        The preview must be *this view's* mix. Hardcoding "editor" here meant
        adopting a tile from Breed answered with the Synths reference-phrase
        render, so the sound you auditioned and the sound you got after "Use
        this sound" were two different renders.

        The body it swaps in must be this view's too: this used to always
        render editor.html, so pressing "Use" on the Library replaced the
        page with the editor while the URL still said /view/library — Back
        and reload then disagreed with the screen, and the Library's filter
        went with it. `view` + `push_url` is the sanctioned way to land
        somewhere else: it moves the body AND the URL together, so the two
        never drift apart again."""
        v = view or self.view_of(request)
        try:
            headers = self.preview_trigger(view=v)
        except RenderError as err:
            return self.toast(str(err))
        if push_url:
            headers = {**headers, "HX-Push-Url": push_url}
        return self.full_view_page(request, headers, view=v)

    def land_on(
        self, request: Request, view: str, say: str = "", *, kind: str = ""
    ) -> Response:
        """Answer with `view`'s whole page and its preview — the URL moved
        there unless the request came from it — saying `say` (a toast, of
        `kind` when given: "done") when it renders: what a move that lands
        the player on another page answers (a keys take or a loop →
        Perform, a sample → Synths)."""
        here = self.view_of(request) == view
        resp = self.full_page_with_preview(
            request, view=view, push_url=None if here else f"/view/{view}"
        )
        if say and resp.status_code == 200:
            trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
            said = {"message": say} | ({"kind": kind} if kind else {})
            trig["studio-toast"] = said
            resp.headers["HX-Trigger"] = json.dumps(trig)
        return resp

    # -- fragments ---------------------------------------------------------
    def bank_response(
        self,
        request: Request,
        extra: dict | None = None,
        *,
        need=BANK_NEEDS,
    ) -> Response:
        """Re-render #drum-bank (the bank actions' hx-target) after any
        bank action; RenderError -> toast. The drum transport and the reset
        menu show bank state too: the route wrapper sends them when it
        moved."""
        s = self.state
        s.save()  # the action is done regardless of whether it previews
        try:
            headers = self.preview_trigger(view="drums")
            trig = json.loads(headers["HX-Trigger"])
            if extra:
                trig["preview-ready"].update(extra)
        except RenderError as err:
            # the bank still updates; the preview problem is a toast
            # (studio-toast is handled in perform.js -> synthlab.toast; its
            # `kind` is the toast's: "error" here, info when absent)
            trig = {"studio-toast": {"message": str(err), "kind": "error"}}
        headers = {"HX-Trigger": json.dumps(trig)}
        html = self.templates.get_template("fragments/drum_bank.html").render(
            **self.page_context(request, need=need)
        )
        return HTMLResponse(html, headers=headers)

    def drum_panels_fragment(self, request: Request, *, need=DRUM_PANELS_NEEDS) -> str:
        return self.templates.get_template("fragments/drum_panels.html").render(
            **self.page_context(request, need=need)
        )

    def loops_context(
        self, view: str, *, session: bool = False, q: str = "", bank: bool = False
    ) -> dict:
        """A Loops list on `view`'s page (`looplib.context`): every loop
        and take — or this session's (the dock's strip), or those a search
        names (the drawer) — the one open on the note grid marked; with
        `bank` (Perform's Loops panel), the Drums bank's slots no beat loop
        holds, as beats (a view of the session's bank: the drawer on every
        page lists what is kept, not 8 slots more) — and the song's
        sections, to open as a scene of the launcher. The old beat
        library's beats ride with the bank's slots; and on the Kit page
        (`view` "drums": it has no bank or library lists of its own) the
        drawer lists both too, so its Beats ▸ reaches every beat."""
        s = self.state
        editing = tuple(sorted({e["name"] for e in s.loop_edit.values()}))
        beats = bank or (view == "drums" and not session)
        slots = s.drum_bank if beats else ()
        lib = s.drum_lib_patterns() if beats else ()
        sections = s.song["sections"] if bank else None
        return looplib.context(
            s.data_dir,
            view,
            q=q,
            session=session,
            editing=editing,
            bank=slots,
            sections=sections,
            lib=lib,
        )

    def loop_editing(self) -> dict | None:
        """The saved loop the note grid edits (`StudioState.loop_edit`: the
        edited lane's, else one an Edit opened on another lane), as
        Perform's banner says it: its name, its lane (1-based), and whether
        that lane is the one on screen (another lane's edits are not the
        loop's) — and its row of the Loops list (`looplib.rows`), whose
        Quantise the banner holds while the loop is open."""
        s = self.state
        e = s.loop_edit_of() or next(
            (
                x
                for x in s.loop_edit.values()
                if x["row"] is None and x["layer"] < s.DRUM_LANE
            ),
            None,
        )
        if e is None:
            return None
        here = e["layer"] == self.state.active_layer
        rows = looplib.rows(self.state.data_dir)
        row = next(
            (r for r in rows if r["kind"] == "loop" and r["name"] == e["name"]), None
        )
        # (named as the Loops lists name it, never by a file's clock name)
        title = row["title"] if row else e["name"]
        lane = e["layer"] + 1
        return {
            "name": e["name"],
            "title": title,
            "lane": lane,
            "here": here,
            "row": row,
        }

    def slots_context(self) -> dict:
        """What Perform's launcher draws (`StudioState`'s slots, studio
        loop-and-perform.md "Slots and scenes"): its lanes — the layer
        slots, then the drums — each with its sound, whether ▶ hears it and
        the row it plays; and its scenes (at least `SLOT_ROWS_SHOWN`, one
        empty past the last that holds anything), each slot a loop's name,
        its title, its length, and whether it plays or is gone from the
        store. `on`: every lane of the scene plays it (a Stream Deck key's
        light, as the jam's)."""
        s = self.state
        known = {r["name"]: r for r in loops.listing(s.data_dir)}
        titles = looplib.titles(s.data_dir)  # the Loops lists' names for them
        lanes = []
        for i in range(s.SLOT_LANES):
            drums = i == s.DRUM_LANE
            if drums:
                sound = ""
            elif i == s.active_layer:
                sound = s.patch.name
            else:
                sound = (s.layers[i].get("patch") or {}).get("name") or ""
            lanes.append(
                {
                    "i": i,
                    "label": "Drums" if drums else str(i + 1),
                    "kind": "beat" if drums else "notes",
                    "sound": sound,
                    "stopped": s.lane_stopped(i),
                    # nothing to hear there: stopped, or a slot with no sound
                    "quiet": s.lane_stopped(i) or not (drums or sound),
                    "edited": i == s.active_layer,
                    "row": s.slot_playing(i),
                }
            )
        stored = s.slot_rows()
        shown = min(limits.MAX_SLOT_ROWS, max(limits.SLOT_ROWS_SHOWN, len(stored) + 1))
        rows = []
        for r in range(shown):
            row = (
                stored[r]
                if r < len(stored)
                else {"slots": [None] * s.SLOT_LANES, "name": ""}
            )
            cells = []
            for ln, name in zip(lanes, row["slots"], strict=True):
                info = known.get(name) if name else None
                cells.append(
                    {
                        "lane": ln["i"],
                        "name": name,
                        "title": titles.get(name, name) if name else None,
                        "bars": info["bars"] if info else 0,
                        "gone": bool(name) and info is None,
                        "playing": bool(name) and ln["row"] == r and not ln["stopped"],
                    }
                )
            filled = any(c["name"] for c in cells)
            on = filled and all(
                c["playing"] if c["name"] else lanes[c["lane"]]["quiet"] for c in cells
            )
            rows.append(
                {
                    "i": r,
                    "name": row["name"],
                    "cells": cells,
                    "filled": filled,
                    "on": on,
                }
            )
        return {"lanes": lanes, "rows": rows}

    def lane_context(self) -> dict:
        """What the Loops page's lane inspector draws
        (fragments/lane_inspector.html, region `lane-inspector`): the lane
        heads (`lane_heads`, the slot a ＋ fills next), and the selected
        lane — the edited layer slot: its name, whether it is the main lane
        or follows it (and at which octave), its level, its FX by name,
        whether ▶ hears it — and what the launcher plays on it and on the
        drums (the playing slot's scene, its loop as the Loops lists name
        it). Cheap: the loop store's titles are read only while a slot
        plays."""
        s = self.state
        act, main = s.active_layer, s.main_index()
        lay = s.layers[act]
        follows = s.follows(act)
        rows = s.slot_rows()

        def playing(lane: int) -> dict | None:
            row = s.slot_playing(lane)
            name = s.slot_at(lane, row) if row is not None else None
            if name is None:
                return None
            return {"row": row, "name": name, "scene": rows[row]["name"]}

        plays, drums = playing(act), playing(s.DRUM_LANE)
        if plays or drums:
            titles = looplib.titles(s.data_dir)
            for p in (plays, drums):
                if p is not None:
                    p["title"] = titles.get(p["name"], p["name"])
        heads = self.lane_heads()
        # (the grid's own notes are counted only where the panel says so:
        # no slot plays the lane and it does not follow — a stroke on a
        # slot's lane redraws nothing here)
        own = not plays and not follows
        return {
            "lanes": heads,
            "lane_next": s.free_slot(),
            "n": act + 1,
            "name": s.patch.name,
            "main": act == main,
            "main_n": main + 1,
            "octave": lay["follow"]["octave"] if follows else None,
            "gain": float(lay.get("gain", 1.0)),
            "fx": [
                FX_UNITS.get(u["kind"], {}).get("label", u["kind"])
                for u in clean_chain(lay.get("fx"))
            ],
            "heard": s.audible(act),
            "notes": len(s.arrangement.cells) if own else 0,
            "plays": plays,
            "drums": drums,
            "drums_stopped": s.lane_stopped(s.DRUM_LANE),
        }

    def clip_library_context(
        self, request: Request, view: str, clips: list[dict] | None = None
    ) -> dict:
        """What the clip library drawer draws on `view`'s page
        (`cliplib.library`): the Loops list at its top (`loops_context`),
        the store by group, where the session uses each clip, the Lab's
        separated songs and — on Song — the "Place into" bar. The search
        (`q`) and the bar's choices are the request's own when its controls
        sent them (`asked`); the bar and track only beside the section,
        which no other form posts."""
        s = self.state
        section = self.asked(request, "place_section")
        if clips is None:
            clips = list_clips(s.data_dir)
        return {
            "loop_lib": self.loops_context(view, q=self.asked(request, "q")),
            # on Song a clip drags onto a lane: how many song bars it is
            # long, for the drop's preview (seconds x bpm / 240)
            "clip_bars": {
                c["name"]: round(c["seconds"] * s.song["bpm"] / 240.0, 2)
                for c in (clips if view == "song" else ())
            },
            "clip_lib": cliplib.library(
                s.data_dir,
                view,
                s.song,
                s.clip_usage(),
                q=self.asked(request, "q"),
                section=section,
                at_bar=self.asked(request, "at_bar") if section else "",
                track=self.asked(request, "track_id") if section else "",
                clips=clips,
            ),
        }

    def clip_library_html(
        self,
        request: Request,
        view: str | None = None,
        *,
        part: str = "fragments/clip_library.html",
        oob: bool = False,
    ) -> str:
        """The drawer (or one `part` of it: its list) for `view`, default
        the page the request came from."""
        ctx = self.clip_library_context(request, view or self.view_of(request))
        return self.templates.get_template(part).render({**ctx, "oob": oob})

    def clips_fragment(self, request: Request) -> HTMLResponse:
        """The clip library after a clip-store change, for the controls
        that target it (hx-target="#clip-library")."""
        return HTMLResponse(self.clip_library_html(request))
