"""`StudioService`: the one object every studio router works through.

It holds what used to be the locals of `server.py`'s `create_app()`
closure — the session state, the render/stem caches, perf stats and the
Jinja environment — plus the helpers routes share: the render path
(`render` — the one entry point — `render_inputs`, `do_render`,
`preview_trigger`), browse audition and Use (`audition` / `adopt`), the
toast, which view a request came from, and the page/fragment renderers.
One instance lives on `app.state.svc`; routes get it through
`routing.Svc`.

One object, in several modules — each a mixin over the service's fields,
holding one concern (studio `docs/routing-and-boundary.md`, "service/"):

- `respond`   the toast; which view asked, what it plays, its FX racks
- `loop`      `render_inputs`, `render`, `_ensure`, the preview answers,
              `reset_caches`
- `song`      the song's audio: idents, sections, gain, plan, mixdown, stems
- `browse`    a card's audition, and Use
- `contexts`  the per-area template contexts, and the routes' small helpers
- `songpage`  the Song page's context: drawer, lanes, thumbnails
- `mixpage`   the Mix view's context: strips, returns, the master strip
- `hubpage`   the clip hub's context: the list, the facts, the bench
- `page`      `page_context` (and its lean-context sets), whole-page
              answers, the shared fragments
- `regions`   the region table, its watches and lean contexts (no mixin)
- `refresh`   the out-of-band machinery: `watch_page`, `moved`, `refresh`,
              `announce`, `suppress`

Mixins rather than composed objects: the units call each other through
`self` all the way down (`refresh` -> `oob_regions` -> `page_context` ->
`song_context`; `section_result` -> `_ensure`), and every route and test
reaches methods and fields alike as `svc.<name>`. Tests replace both on
the instance (`svc._song_raw`, `svc._song_idents`, `svc.song_mixdown`;
`svc.cache`, `svc.stems`, `svc.song_memo`), so a composed unit would need
a forwarder per name, and a replaced forwarder or a field swapped on the
service would not be the one its neighbours use. What keeps the units
apart instead is `OWNERS` below.

Everything that was importable from the one-file `service.py` is
importable from here (tests/studio/test_service_split.py pins the list).
`render_loop`, `section_key`, `section_master_audio` and
`library_by_family` are read through this module at call time (`loop.py`,
`song.py`, `page.py`), because tests patch them as
`synthlab.studio.service.<name>`.
"""

# ruff: noqa: F401
# Unused imports are kept on purpose: every name the one-file module had in
# its namespace is still importable (and patchable) from here.

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable, Hashable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ...core import SR
from ...explore import SpaceStore
from ...params import PARAMS, denorm, norm
from ...sequencer import midi, name_of
from ...synth import Patch
from .. import (
    beatgen,
    chords,
    lab,
    labmake,
    limits,
    notation,
    samplerview,
    schema,
    song_play,
    sources,
)
from .. import song_model as sm
from ..agent import AgentService
from ..audition import PHRASES, phrase_arrangement
from ..bassview import phone_speaker
from ..chain import chain_summary, motion_lines
from ..cliphub import HubSession
from ..clips import clip_hash, list_clips
from ..curves import CURVABLE, GAIN, LANE_TARGETS, lane_range
from ..drumfactory import DRUM_KITS
from ..drumlab import DRUM_PARAMS, DRUM_VOICES, VOICE_PRESETS, defaults_of, specs_of
from ..events import ORIGIN_HEADER, QUIET, EventBus, Task
from ..factory import FACTORY
from ..fxrack import CHAIN_PRESETS, FX_UNITS, clean_chain
from ..labjobs import LabJobs
from ..library import by_family as library_by_family
from ..library import tags as library_tags
from ..lru import LRU
from ..paramui import CONTINUOUS_CHOICES, PARAM_GROUPS, SHAPE_DEPS, spec_of
from ..perf import PerfStats
from ..recipes import cards as recipe_cards
from ..recipes import tags as recipe_tags
from ..render import (
    NOTE_MEMO,
    NoteCache,
    RenderCache,
    RenderError,
    RenderInputs,
    RenderResult,
    SectionAudio,
    SectionJob,
    StemCache,
    fold_tail,
    render_loop,
    result_of,
    section_key,
    section_master_audio,
)
from ..soundmeta import envelope_svg, filter_response_svg
from ..state import (
    DRUM_ROWS,
    RESET_SCOPES,
    RESET_VIEWS,
    ROOTS,
    SCALE_CHOICES,
    Arrangement,
    StudioState,
    audible_layers,
    pitch_rows,
)
from ..views import BENCH_VIEWS, VIEW_PARTS
from ..voice import BAND_CHOICES, MAX_TAKE_SECONDS, load_take
from .browse import (
    Browse,
)
from .contexts import (
    DXY_AXES,
    PAD_VOICES,
    Contexts,
    _layer_name,
    _octave,
    dxy_spec,
    group_specs,
    xy_norm,
    xy_value,
)
from .hubpage import (
    HubPage,
)
from .loop import (
    LoopRender,
)
from .mixpage import (
    MixPage,
)
from .page import (
    ALL,
    BANK_NEEDS,
    BAR_NEEDS,
    CLIPLIB_NEEDS,
    DRUM_PANELS_NEEDS,
    SECTIONS,
    SONG_NEEDS,
    Page,
    density_of,
    theme_of,
)
from .refresh import (
    Refresh,
)
from .regions import (
    _EDITOR,
    ALL_VIEWS,
    PAGE_REGIONS,
    REGIONS,
    Region,
    _clips_sig,
    _ctx_breed,
    _ctx_chords,
    _ctx_clip_library,
    _ctx_layers,
    _ctx_mixer,
    _ctx_reset,
    _ctx_sampler,
    _ctx_sound,
    _ctx_viz,
    _median,
    _sig,
    _song_bars,
    _w_amp_viz,
    _w_breed_parent,
    _w_chain,
    _w_chord_bar,
    _w_clip_library,
    _w_clips,
    _w_drum_bank,
    _w_drum_transport,
    _w_filter_env_viz,
    _w_filter_viz,
    _w_gen_strip,
    _w_launcher,
    _w_layer_strip,
    _w_mixer,
    _w_perform_drums,
    _w_reset,
    _w_sampler,
    _w_slots,
    _w_song_arranger,
    _w_song_drawer,
    _w_song_pool,
    _w_song_thumbs,
    _w_song_topbar,
    _w_sound_bar,
    _w_sound_id,
    _w_track_edit,
    _w_transport,
)
from .respond import (
    Respond,
)
from .song import (
    SongRender,
)
from .songpage import (
    SongPage,
    _lane_row,
    _lane_text,
    _thumb,
    lane_label,
)

if TYPE_CHECKING:
    from ..sounds import Sound

log = logging.getLogger("synthlab.studio")

# Which modules of this package may touch which field of the service
# (`self.<name>`, or `svc.<name>` in a watch; the constructor below declares
# them all). One owner where the field is one concern's own; where several
# are listed the reason is beside it — a new use anywhere else fails
# tests/studio/test_service_split.py, which is the place to write down why.
OWNERS: dict[str, tuple[str, ...]] = {
    # the loop cache and the render timings: every render is filed and timed
    # by `LoopRender._ensure`, a song section's included
    "cache": ("loop",),
    "perf": ("loop",),
    # key/pad one-shots are their routes' own; the service only forgets them
    "notes": ("loop",),
    # stems of a loop (`render_loop`) and each section's master-processed
    # cycle (`_master_audio`): one byte-bounded cache for both
    "stems": ("loop", "song"),
    # the song's remembered levels, idents and gain; `reset_caches` (loop)
    # replaces them with the rest
    "song_levels": ("song", "loop"),
    "song_memo": ("song", "loop"),
    # each section's stems' levels, as its render recorded them (the
    # mixer's meters and Balance): written and read with the plan
    "stem_levels": ("song", "loop"),
    # progress of the song's renders; the change notice after a request
    "events": ("song", "refresh"),
    # regions this request must not re-send
    "_suppressed": ("refresh",),
    # the one SpaceStore, behind `space_store()`
    "_space": ("contexts",),
    # the Sample Lab's job and its store's directory (`lab_context`)
    "lab": ("contexts",),
    "data_dir": ("contexts",),
    # the agent drawer's session, drawn by `page_context`
    "agent": ("page",),
    # the clip hub's runtime (its undo steps), drawn by its context
    "hub": ("hubpage",),
    # whatever renders a template
    "templates": ("respond", "loop", "contexts", "page", "refresh"),
    # the session: every unit reads it (a watch, as `svc.state`); `song`
    # only for `data_dir` and the snapshot
    "state": (
        "respond",
        "loop",
        "song",
        "browse",
        "contexts",
        "songpage",
        "mixpage",
        "hubpage",
        "page",
        "regions",
        "refresh",
    ),
}


class StudioService(
    Respond,
    LoopRender,
    SongRender,
    Browse,
    Contexts,
    SongPage,
    MixPage,
    HubPage,
    Page,
    Refresh,
):
    """The service: every router's one object. The fields are declared here
    (and only here); the methods come from the mixins, one concern each."""

    def __init__(self, data_dir: Path, templates: Jinja2Templates):
        self.data_dir = Path(data_dir)
        self.state = StudioState(self.data_dir)
        self.cache = RenderCache()  # /preview/{key}.wav: loops only
        self.notes = NoteCache()  # key/pad one-shots, served by their routes
        self.stems = StemCache()
        # (peak, ref) of each section pass at gain 1, by content: the
        # song-wide gain without rendering every section again
        self.song_levels: LRU[tuple[float, float]] = LRU(1024)
        # a song's section idents and gain, by content (`_song_idents`)
        self.song_memo: LRU[dict] = LRU(16)
        # {track or return id: (peak, RMS)} of each section's stems at gain
        # 1, by the section's key: what its render saw (`_master_audio`)
        self.stem_levels: LRU[dict] = LRU(4096)
        self.perf = PerfStats()
        # server push (`events.py`): progress of the long renders, and a
        # notice of every committed change for the pages open elsewhere
        self.events = EventBus()
        self.templates = templates
        self._space = SpaceStore(self.data_dir / "space.jsonl")
        # regions this request must not re-send (`suppress`); per request,
        # written and taken under the studio lock
        self._suppressed: set[str] = set()
        # the sound agent: its log and budget persist per data dir, its
        # key never leaves it
        self.agent = AgentService(log_path=self.data_dir / "agent_log.json")
        # the Sample Lab's separation job (one at a time, its own thread);
        # opening it sweeps what a crash left in the source store
        self.lab = LabJobs(self.data_dir, self.events)
        # the clip hub's undo steps (an Apply, an Update of a clip): runtime
        self.hub = HubSession()
