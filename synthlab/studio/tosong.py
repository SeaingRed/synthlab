"""Loops and scenes into the song (studio `docs/song.md`, "Bridges" and
"Drags onto a lane").

What `/loop/song` and Perform's scene routes share, on top of the pure
edits in `song_bridge` (`add_lanes_section`, `add_scene_section`,
`place_lanes(onto=)`):

- **A question instead of a refusal** (`Ask`): the song is left as it was
  and the answer is a 200 carrying `song-ask` in its HX-Trigger — a
  message and its choices, each a link or the same post again with one
  more value (songdrag.js draws it as a popover). Two questions exist:
  - **the 8-track cap** (`cap_ask`, from `song_bridge.TrackCap`): put the
    lane on a track the section does not use yet — replacing that track's
    sound, or keeping it (`onto`, appended to what earlier answers chose);
  - **already in the song** (`already` / `already_ask`): a plain → Song
    whose section would hold nothing another section does not hold
    already — "place it again?" (`again=1`). A drop onto a lane or onto
    the timeline never asks: its place was chosen.
- **A scene's lanes** (`scene_parts`): each slot's loop as a lane the
  song places on a track with its sound, on its own grid, and the beat.
- **A pattern on a lane at a bar** (`place_on_lane`).

Nothing here holds state; each route runs its change inside one
`routes.song._commit` (one song undo step) and turns an `Ask` raised from
the change into `ask_response`.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from fastapi.responses import HTMLResponse

from . import limits, schema, song_bridge
from . import song_model as sm
from .state import Arrangement, StudioState

# the values a question carries over into its choice's post (never the
# page's other fields: htmx.ajax sweeps every named input)
CARRIED = (
    "name",
    "take",
    "slot",
    "as",
    "row",
    "idea",
    "track_id",
    "section_id",
    "at_bar",
    "at_section",
    "again",
    "onto",
)


class Ask(Exception):
    """A question for the person instead of an edit: `message` and its
    `choices` — `{label, title, vals}` (the same post with these values) or
    `{label, title, href}` (a link)."""

    def __init__(self, message: str, choices: list[dict]):
        super().__init__(message)
        self.message, self.choices = message, choices


def carried(form) -> dict:
    return {k: str(form[k]) for k in CARRIED if form.get(k)}


def ask_response(path: str, ask: Ask, trigger: dict | None = None) -> HTMLResponse:
    """The question as an answer: nothing changed, `song-ask` in the
    HX-Trigger with each choice's post resolved to `path`."""
    choices = [
        c if "href" in c else {**c, "path": path, "vals": c["vals"]}
        for c in ask.choices
    ]
    trig = dict(trigger or {})
    trig["song-ask"] = {"message": ask.message, "choices": choices}
    return HTMLResponse("", headers={"HX-Trigger": json.dumps(trig)})


def onto_of(form) -> list[list[str]]:
    """The cap's choices so far (`onto`: JSON, [[track id, how], ...]), or
    a SongError for anything else."""
    raw = str(form.get("onto", "")).strip()
    if not raw:
        return []
    try:
        got = json.loads(raw)
    except (ValueError, RecursionError):
        got = None
    ok = (
        isinstance(got, list)
        and len(got) <= limits.MAX_SYNTH_TRACKS
        and all(
            isinstance(p, list)
            and len(p) == 2
            and all(isinstance(x, str) and len(x) <= 64 for x in p)
            for p in got
        )
    )
    if not ok:
        raise sm.SongError("That choice of track could not be read.")
    return got


def cap_ask(song: dict, err: song_bridge.TrackCap, form) -> Ask:
    """The 8-track cap as a choice: each synth track the section does not
    use yet, two ways — its sound replaced by the lane's (every section it
    plays in hears it), or kept (the lane's notes in the track's sound) —
    and the Song page, to free a track."""
    onto = onto_of(form)
    base = carried(form)
    choices = []
    for tid in err.free:
        t = song["tracks"][sm.track_index(song, tid)]
        own = (t.get("patch") or {}).get("name") or "its sound"
        for how, label, title in (
            (
                "replace",
                f"{t['name']}: becomes {err.sound}",
                f"put it on {t['name']} and give that track {err.sound}: every "
                f"section {t['name']} plays in hears {err.sound} from now on",
            ),
            (
                "keep",
                f"{t['name']}: keeps {own}",
                f"put its notes on {t['name']}, played by the track's own "
                f"sound ({own})",
            ),
        ):
            vals = base | {"onto": json.dumps([*onto, [tid, how]])}
            choices.append({"label": label, "title": title, "vals": vals})
    choices.append(
        {
            "label": "Free a track on Song ▸",
            "title": "the Song page: remove a track, then try again",
            "href": "/view/song",
        }
    )
    n = limits.MAX_SYNTH_TRACKS
    return Ask(
        f"The song's {n} synth tracks are all in use: where should “{err.sound}” go?",
        choices,
    )


def already(song: dict, sid: str) -> dict | None:
    """Another section that already holds everything section `sid` holds
    — every pattern in its cells, every take's clip — or None."""
    secs = song["sections"]
    new = secs[sm.section_index(song, sid)]
    pats = {p for p in new["cells"].values() if p}
    clips = {a["clip"] for a in new.get("audio") or []}
    if not pats and not clips:
        return None
    for sec in secs:
        if sec["id"] == sid:
            continue
        have = {p for p in sec["cells"].values() if p}
        heard = {a["clip"] for a in sec.get("audio") or []}
        if pats <= have and clips <= heard:
            return sec
    return None


def already_ask(song: dict, sec: dict, what: str, form) -> Ask:
    i = sm.section_index(song, sec["id"]) + 1
    return Ask(
        f"“{what}” is already in the song: section {i}, “{sec['name']}”. "
        "Place it again?",
        [
            {
                "label": "Place it again",
                "title": "a new last section playing the same patterns (the pool "
                "keeps one copy of each)",
                "vals": carried(form) | {"again": "1"},
            },
            {
                "label": "Open Song ▸",
                "title": "the Song page, where it already plays",
                "href": "/view/song",
            },
        ],
    )


def loop_arr(loop: dict) -> dict:
    """A saved loop of notes as the song keeps an arrangement (conformed)."""
    grid = Arrangement(meter=loop["meter"], **loop["lane"], **loop["key"])
    return schema.conform(asdict(grid), schema.nodes().ARRANGEMENT)


def slot_lane(s: StudioState, loop: dict, main: bool) -> dict:
    """A slot's loop of notes as one lane: its notes on its own grid
    (`arr`), in its sound — the main layer only, as the launcher plays it —
    or the Synths sound when it kept none."""
    arr = loop_arr(loop)
    sound = loop["sound"]
    patch, extras = sound if sound else (s.patch, None)
    extras = extras or {}
    lane = song_bridge.main_lane(
        patch.to_json(),
        arr,
        {"fx": extras.get("main_fx") or [], "gain": 1.0},
        extras.get("curves") or {},
    )
    if not main:
        lane |= {"role": "layer", "curves": {}}
    return lane | {"arr": arr}


def scene_parts(
    s: StudioState, picks: list, meter: str
) -> tuple[list[dict], dict | None, list[str]]:
    """A scene's slots (loaded loops in launcher order, None for an empty
    one) as (note lanes, the beat's PATTERN or None, the names left out —
    a loop in another meter, or of the wrong kind for its lane)."""
    lanes: list[dict] = []
    beat, out = None, []
    for i, lp in enumerate(picks):
        if lp is None:
            continue
        drum = i == s.DRUM_LANE
        if lp["meter"] != meter or (lp["kind"] == "beat") != drum:
            out.append(lp["name"])
        elif drum:
            beat = lp["beat"]
        elif lp["lane"]["cells"]:
            lanes.append(slot_lane(s, lp, main=not lanes))
    return lanes, beat, out


def place_on_lane(song: dict, sid: str, tid: str, at_bar: int, pid: str) -> int:
    """Pattern `pid` on track `tid` in section `sid` from bar `at_bar` of
    its pass (0: the whole section, as a pool pattern dropped on a cell;
    later: a block from there to the section's end). The bar it landed on."""
    sm.set_cell(song, sid, tid, pid)
    sec = song["sections"][sm.section_index(song, sid)]
    at = max(0, min(int(at_bar), int(sec["bars"]) - 1))
    if at:
        sm.set_span(song, sid, tid, at, int(sec["bars"]) - at)
    else:
        sm.clear_span(song, sid, tid)
    return at


def song_wide(arr: dict) -> dict:
    """What the first loop or scene into an empty song gives it."""
    return {k: arr[k] for k in song_bridge.SONG_WIDE if k in arr}
