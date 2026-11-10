"""The studio's routers, one per area.

Each module builds its own `router = studio_router()`, so every route is
a `routing.StudioRoute`: a plain `def` run in the threadpool under the
studio lock, unless `@lock_free`. Handlers reach the shared state and
helpers through `svc: Svc` (the app's `StudioService`). `app.create_app`
mounts them flat, in this order; no two routers share a path, so the
order only decides which 405 an unmatched method gets.
"""

from __future__ import annotations

from . import (
    agent,
    audio,
    booth,
    breed,
    cliphub,
    clips,
    coach,
    drums,
    events,
    export,
    freerec,
    fx,
    lab,
    library,
    macros,
    misc,
    mixer,
    patch,
    pattern,
    perform,
    presets,
    sampler,
    slots,
    song,
    song_arr,
    song_jam,
    song_long,
    song_record,
    song_start,
    takes,
    views,
    vocal,
    voice,
    wavetable,
)

ROUTERS: list = [
    views.router,
    patch.router,
    drums.router,
    pattern.router,
    perform.router,
    coach.router,
    presets.router,
    library.router,
    breed.router,
    macros.router,
    agent.router,
    fx.router,
    clips.router,
    cliphub.router,
    sampler.router,
    wavetable.router,
    takes.router,
    freerec.router,
    slots.router,
    voice.router,
    booth.router,
    vocal.router,
    lab.router,
    export.router,
    song.router,
    song_record.router,
    song_long.router,
    song_start.router,
    song_arr.router,
    song_jam.router,
    mixer.router,
    audio.router,
    events.router,
    misc.router,
]
