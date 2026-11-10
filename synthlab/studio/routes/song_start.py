"""Song creation's routes (studio docs/songcraft.md): a style's starter,
the working loop arranged into a song, one part rolled
again, the song's own title / tempo / key, its chord track, and the
checklist.

Each is a `_commit` (`routes/song.py`): one undo step, the regions by
their watches, a refusal a 422 toast and nothing changed. What makes them
different from a hand edit:

- **Generating is committing, and committing plays.** There is no preview:
  the answer carries `song-autoplay` beside `song-changed`, and song.js
  starts what was made — a new song on its head plan (its first section,
  ready in a second or two, while the rest renders outside the studio
  lock), one part by looping the first section it sounds in.
- **Rolling again is the same undo step.** A starter is `key="start"`, a
  part `key="gen:<track>"`, with no time limit (the take rule): ten rolls
  are one Ctrl+Z, and the step before them is the song before the first.
- **Seeds** are taken modulo `limits.SEED_MODULO`; none posted draws one.
  The pure half (`studio/gen/`) is deterministic for a seed.

The loop's own generators and chord track (`POST /fragment/gen`,
`POST /fragment/chords/track`) are routes/pattern.py's; a chord edit is
`songops.edit_chords` for both, its fields read by `chord_args` here.
"""

from __future__ import annotations

import copy
import math
import random

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .. import limits, songops
from .. import meter as _meter
from .. import song_model as sm
from ..clips import delete_clip
from ..gen import build, chordtrack, styles
from ..gen import notes as nt
from ..routing import StrForm, Svc, studio_router
from ..service import StudioService
from ..state import ROOTS, SCALE_CHOICES
from .song import _commit, _section, _title, _track
from .song_record import _transition_clip

router = studio_router()

STYLE_COOKIE = "synthlab_style"
FOREVER = math.inf  # a roll joins the roll before it however long ago


def _seed(form: dict) -> int:
    """The posted seed modulo `SEED_MODULO`, or a drawn one."""
    raw = str(form.get("seed", "")).strip()
    if raw.lstrip("-").isascii() and raw.lstrip("-").isdigit() and len(raw) <= 12:
        return int(raw) % limits.SEED_MODULO
    return random.randrange(limits.SEED_MODULO)


def _number(form: dict, key: str, what: str) -> float | None:
    raw = str(form.get(key, "")).strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        v = math.nan
    if not math.isfinite(v):
        raise sm.SongError(f"{what} is a number.")
    return v


def _choice(form: dict, key: str, choices, what: str) -> str | None:
    raw = str(form.get(key, "")).strip()
    if not raw:
        return None
    if raw not in choices:
        raise sm.SongError(f"No such {what}.")
    return raw


def chord_args(form: dict, op: str) -> dict:
    """A chord-track edit's fields (`songops.edit_chords`), read from a post
    — the song's chord strip and tiles, and the loop's chord lane alike: a
    number that is not one, or a quality there is no such, is a SongError.
    The seed is drawn only for a roll."""
    return {
        "prog": _number(form, "prog", "The progression"),
        "seed": _seed(form) if op == "roll" else 0,
        "slot": _number(form, "slot", "The slot"),
        "deg": _number(form, "deg", "The degree"),
        "q": _choice(form, "q", sm.CHORD_QUALITIES, "chord quality"),
        "inv": _number(form, "inv", "The inversion"),
        "at": _number(form, "at", "The beat"),
        "bars": _number(form, "bars", "Bars"),
    }


def last_style(request: Request, song: dict) -> str:
    """The style a Start offers first: the song's own, else the last one
    used in this browser (a cookie), else melodic techno."""
    own = ((song.get("gen") or {}).get("params") or {}).get("style")
    seen = request.cookies.get(STYLE_COOKIE)
    for style in (own, seen):
        if style in styles.STYLES:
            return style
    return styles.DEFAULT


def _clips(svc: StudioService, built: build.Built) -> list[str]:
    """Render the transition clips a build places into the clip store (a
    clip already saved from the same settings is reused); the names this
    call wrote, to take back if the commit is refused."""
    wrote = []
    for name, spec in built.clips.items():
        real, new = _transition_clip(svc.state.data_dir, spec)
        if new:
            wrote.append(real)
        if real != name:  # the store held another clip under that name
            for sec in built.song["sections"]:
                for take in sec["audio"]:
                    if take["clip"] == name:
                        take["clip"] = real
    return wrote


def _replace(svc: StudioService, built: build.Built, said: str, **commit) -> Response:
    """Commit a built song in place of the session's: its clips rendered
    first and deleted again if the song is refused, the drawer closed, a
    track-sound edit ended, the takes' own undo emptied (it restores
    patterns by id), and the page told to play it from bar 1."""
    s = svc.state
    wrote = _clips(svc, built)

    def change(song):
        song.clear()
        song.update(copy.deepcopy(built.song))

    def then():
        s.song_drawer = None
        s.take_history = []
        s.grid_span = 2  # the drawer shows a generated part's two octaves
        if s.song_edit_track is not None:
            s.end_track_edit()
        # a run of starters is one step (key, FOREVER): named for this one
        s.song_undo_relabel(commit["label"])
        note = " ".join([said, *built.notes])
        return {"song-autoplay": {"head": True}, "studio-toast": {"message": note}}

    resp = _commit(svc, change, window=FOREVER, then=then, **commit)
    if resp.status_code >= 400:
        for name in wrote:
            delete_clip(s.data_dir, name)
    return resp


@router.post("/song/start")
def song_start(request: Request, form: StrForm, svc: Svc) -> Response:
    """Lay down a whole song in a style (`gen/styles.py`): tracks named
    for their roles with their sounds, patterns, sections with repeats,
    spans, lanes and transitions, and the chord track — replacing the song,
    as ONE undo step, then playing from bar 1. `style` (default: the last
    used), `seed`, and optionally `bpm`, `root`, `scale_name`, `variant`
    ("long"). `again=1` rolls the song's own style again with a new seed.
    Starting again straight after a start is the same undo step."""
    s = svc.state
    gen = (s.song.get("gen") or {}).get("params") or {}
    try:
        style = _choice(form, "style", styles.STYLES, "style")
        if style is None:
            style = gen.get("style") if form.get("again") else None
            style = style if style in styles.STYLES else last_style(request, s.song)
        variant = _choice(form, "variant", styles.variants(style), "length")
        if variant is None:
            variant = gen.get("variant") if form.get("again") else None
        if variant not in styles.variants(style):
            variant = "standard"
        built = build.start(
            style,
            _seed(form),
            bpm=_number(form, "bpm", "The tempo"),
            root=_choice(form, "root", ROOTS, "key"),
            scale=_choice(form, "scale_name", SCALE_CHOICES, "scale"),
            variant=variant,
        )
    except sm.SongError as err:
        return svc.toast(str(err))
    label = styles.STYLES[style]["label"]
    song = built.song
    said = (
        f"{label} in {song['root'][:-1]} {song['scale_name'].replace('_', ' ')}, "
        f"{sm.song_bars(song)} bars · Ctrl+Z takes the whole song back."
    )
    resp = _replace(svc, built, said, label=f"start: {label.lower()}", key="start")
    if resp.status_code < 400:
        resp.set_cookie(STYLE_COOKIE, style, max_age=31_536_000, samesite="lax")
    return resp


@router.post("/song/arrange")
def song_arrange(request: Request, form: StrForm, svc: Svc) -> Response:
    """ "Make this loop a song": `source` "loop" (the default) lays the
    working loop down as intro / verse / build / drop / break / drop /
    outro — by muting, thinning and filtering its lanes, with a fill and
    transitions (`build.arrange`) — replacing the song, one undo step, and
    plays it. `source` a section id shapes that section in place, among
    the song's others. `add` "intro" / "outro" adds one thinned section
    at that end (the checklist's fix)."""
    s = svc.state
    seed = _seed(form)
    add = str(form.get("add", ""))
    source = str(form.get("source", "") or "loop")
    if add:
        if add not in ("intro", "outro"):
            return svc.toast("An intro or an outro.")
        made: list[str] = []

        def edge(song):
            made.append(build.add_edge(song, add, seed))

        return _commit(svc, edge, label=f"add {add}", key=f"arrange:{add}")
    if source == "loop":
        try:
            song, sid = s.loop_as_song()
            built = build.arrange(song, sid, seed)
        except sm.SongError as err:
            return svc.toast(str(err))
        bars = sm.song_bars(built.song)
        said = f"The loop as a song of {bars} bars · Ctrl+Z takes it back."
        return _replace(svc, built, said, label="loop → song", key="arrange")
    done: dict = {}

    def change(song):
        _section(song, source)
        built = build.arrange(song, source, seed)
        done["wrote"] = _clips(svc, built)

    resp = _commit(svc, change, label="arrange section", key=f"arrange:{source}")
    if resp.status_code >= 400:
        for name in done.get("wrote", ()):
            delete_clip(s.data_dir, name)
    return resp


# a generator's own settings, as a form may post them
_PARAMS = {
    "figure": tuple(nt.FIGURES),
    "mode": nt.ARP_MODES,
    "rhythm": nt.RHYTHMS,
    "voicing": nt.VOICINGS,
    "contour": ("arch", "rise", "fall"),
}
_NUMBERS = {"rate": (1, 2), "gate": (1, 2), "octaves": (1, 2), "density": (0.0, 1.0)}
_TAKES = {
    "bass_rolling": ("figure",),
    "chords": ("rhythm", "voicing"),
    "pad": ("voicing",),
    "arp": ("mode", "rate", "gate", "octaves"),
    "lead": ("contour", "density"),
}


def _gen_params(form: dict, kind: str) -> dict:
    out: dict = {}
    for name in _TAKES.get(kind, ()):
        if name in _PARAMS:
            v = _choice(form, name, _PARAMS[name], name)
        else:
            lo, hi = _NUMBERS[name]
            v = _number(form, name, name.capitalize())
            if v is not None:
                v = min(hi, max(lo, v))
                v = v if name == "density" else int(v)
        if v is not None:
            out[name] = v
    return out


@router.post("/song/gen")
def song_gen(request: Request, form: StrForm, svc: Svc) -> Response:
    """A generator on one track (`track_id`): `kind` "again" (the default:
    every generated pattern the track plays, rolled with a new `seed` — a
    rolling bass's other half with it), "busier" / "sparser" (`amount`),
    a note generator's name (its patterns rewritten by it, with its own
    settings), "fill" / "build" on the drums (`section_id` for a build),
    or "duck". `add` "bass" adds a bass that follows the chords. Nothing
    else in the song moves; rolling again is the same undo step; and the
    result plays (the first section the track sounds in, looped)."""
    seed = _seed(form)
    kind = str(form.get("kind", "") or "again")
    tid = str(form.get("track_id", ""))
    done: dict = {}

    def change(song):
        if form.get("add"):
            if form["add"] in ("beat", "riff"):
                done["section"] = build.add_part(song, form["add"], seed)
                done["said"] = f"A {form['add']} · seed {seed}."
                return
            if form["add"] != "bass":
                raise sm.SongError("No such part to add.")
            done["track"] = build.add_bass(song, seed)
            done["said"] = "Added a bass that follows the chords."
            return
        track = _track(song, tid)
        done["track"] = tid
        if kind == "again":
            names = build.reroll(song, tid, seed)
            done["said"] = f"Another {' + '.join(names)} · seed {seed}."
        elif kind in ("busier", "sparser"):
            amount = _number(form, "amount", "The amount")
            amount = 0.25 if amount is None else min(1.0, max(0.05, amount))
            build.vary(song, tid, kind, amount, seed)
            done["said"] = f"{track['name']} is {kind}."
        elif kind == "duck":
            done["said"] = f"{build.duck(song, tid)} ducks under every beat."
        elif kind in nt.GENERATORS or kind in ("fill", "build"):
            section = str(form.get("section_id", "")) or None
            build.write(song, tid, kind, seed, _gen_params(form, kind), section)
            done["said"] = f"{track['name']}: {kind.replace('_', ' ')} · seed {seed}."
        else:
            raise sm.SongError("No such generator.")

    def then():
        song = svc.state.song
        first = done.get("section") or next(
            (x["id"] for x in song["sections"] if x["cells"].get(done.get("track"))),
            None,
        )
        out: dict = {"studio-toast": {"message": done.get("said", "")}}
        if first and kind != "duck":
            out["song-autoplay"] = {"section": first}
        return out

    key = f"gen:{form['add']}" if form.get("add") else f"gen:{tid}"
    label = f"add {form['add']}"[:40] if form.get("add") else f"generate: {kind}"[:40]
    return _commit(svc, change, label=label, key=key, window=FOREVER, then=then)


@router.post("/song/meta")
def song_meta(request: Request, form: StrForm, svc: Svc) -> Response:
    """The song's own title, tempo, key and groove (the Song topbar's
    fields): any of `title`, `bpm`, `root`, `scale_name`, `swing`,
    `drum_swing`, `meter` (the song's bar, `meter.METERS`: a section
    without one of its own plays in it); only what is posted changes. A
    key change moves nothing
    by itself — the chord track is in degrees and follows — unless `move`
    is on: then every note pattern is carried to the new key (each note to
    the same step of the new scale, nearest octave)."""
    did: list[str] = []

    def change(song):
        if "title" in form:
            song["title"] = _title(form["title"])
            did.append("title")
        bpm = _number(form, "bpm", "The tempo")
        if bpm is not None:
            song["bpm"] = min(limits.BPM_RANGE[1], max(limits.BPM_RANGE[0], bpm))
            did.append("tempo")
        root = _choice(form, "root", ROOTS, "key")
        scale = _choice(form, "scale_name", SCALE_CHOICES, "scale")
        if root or scale:
            old = {"root": song["root"], "scale_name": song["scale_name"]}
            song["root"], song["scale_name"] = (
                root or song["root"],
                scale or song["scale_name"],
            )
            if form.get("move") and (old["root"], old["scale_name"]) != (
                song["root"],
                song["scale_name"],
            ):
                move_key(song, old)
            did.append("key")
        for name in ("swing", "drum_swing"):
            v = _number(form, name, "Swing")
            if v is not None:
                song[name] = min(0.6, max(0.0, v))
                did.append("swing")
        if "meter" in form:
            if form["meter"] not in _meter.METERS:
                raise sm.SongError("That is not a meter the studio has.")
            song["meter"] = form["meter"]  # 4/4 is written down as none
            did.append("meter")

    return _commit(
        svc,
        change,
        label=lambda: f"song {did[0]}" if did else "song settings",
        key=lambda: f"meta:{did[0]}" if did else None,
        soon=True,
    )


def move_key(song: dict, old: dict) -> None:
    """Carry every note pattern from the key `old` to the song's: a note
    on step k of the old scale goes to step k of the new one (a note
    outside the old scale moves with the root alone), by the smaller way
    round, so a line keeps its shape and its register."""
    was = chordtrack.scale_of(old)
    now = chordtrack.scale_of(song)
    shift = (chordtrack.key_pc(song) - chordtrack.key_pc(old)) % 12
    shift = shift - 12 if shift > 6 else shift
    was_root = chordtrack.key_pc(old)

    def moved(pitch: str) -> str:
        m = sm.midi(pitch)
        rel = (m - was_root) % 12
        if rel in was and len(now) == len(was):
            m += now[was.index(rel)] - rel
        return sm.name_of(min(127, max(0, m + shift)))

    for p in song["patterns"].values():
        if p["kind"] == "notes":
            p["cells"] = [[moved(c[0]), c[1]] for c in p["cells"]]
            p["cuts"] = [[moved(c[0]), c[1]] for c in p.get("cuts") or []]


@router.post("/song/chords")
def song_chords(request: Request, form: StrForm, svc: Svc) -> Response:
    """The chord track: `scope` "song" (the default) or a section id (its
    own, overriding the song's), and `op` —

    - `preset`: progression number `prog` of the strip's menu
      (`build.progressions`); `roll`: one of them, drawn by `seed`;
    - `set`: slot `slot`'s `deg` (1-7), `q` (a quality), `inv` (0-2);
      `edge`: the beat `at` slot `slot` starts on (a tile's edge dragged);
      `split`: slot `slot` as two of the same chord, each half as long;
    - `add` / `remove`: a slot at the end / slot `slot`;
    - `bars`: the length that repeats (`bars`), the slots spread again;
    - `own`: give a section the song's progression to change; `clear`:
      take a section's own away (or the song's whole track);
    - `from-pattern`: read `pattern_id`'s chords into slots.

    It renders nothing: patterns already written keep their notes until
    they are rolled again."""
    scope = str(form.get("scope", "") or "song")
    op = str(form.get("op", ""))

    def change(song):
        holder = song if scope == "song" else _section(song, scope)
        track = copy.deepcopy(
            chordtrack.track_of(song, None if scope == "song" else holder)
        )
        if op in songops.CHORD_EDITS:  # (the loop's chord lane takes the same)
            track = songops.edit_chords(song, track, op, chord_args(form, op))
        elif op == "own":
            if scope == "song":
                raise sm.SongError("Pick the section that gets its own chords.")
        elif op == "clear":
            holder.pop("chords", None)
            return
        elif op == "from-pattern":
            p = song["patterns"].get(form.get("pattern_id", ""))
            got = chordtrack.from_cells(song, p) if p and p["kind"] == "notes" else None
            if got is None:
                raise sm.SongError("That pattern holds no chords this key can name.")
            track = got
        else:
            raise sm.SongError("No such chord edit.")
        holder["chords"] = track

    return _commit(svc, change, label=f"chords: {op}"[:40], key=f"chords:{scope}:{op}")


@router.get("/fragment/song/check")
def song_check(request: Request, svc: Svc) -> Response:
    """The checklist: what the song is missing, each finding with the
    button that fixes it, and — for a song started in a style — that
    style's signature. Read-only: it reads the model and renders nothing."""
    ctx = svc.page_context(request, "song", need=frozenset({"song"}))
    html = svc.templates.get_template("fragments/song_check.html").render(**ctx)
    return HTMLResponse(html)
