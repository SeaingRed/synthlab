"""The Mix view's routes (`studio/mixer.py` is the
model, `views/mixer.html` the page).

Every edit is a song edit: it goes through `routes.song._commit`, so it is
schema-clean, one step of the song's undo ring (a control that posts many
times a gesture names a `key`: a fader drag is one step), saved, and
answered with `song-changed` — the player re-fetches the plan and the
change is heard from the next pass. The regions follow by their watches
(`mixer-strips`; the Song page's arranger when that is the page asking).

- `POST /song/mix/strip/{track_id}`: a strip's controls, only those
  posted — `gain`, `pan`, `mute`, `solo`, `send.0` / `send.1` (A / B: the
  first one turned up makes the default returns), `duck`, `eq.low` /
  `eq.mid` / `eq.high` (`mixer.set_strip`). A track's role stays with
  `POST /song/track/{id}/mix`, as on the Song page.
- `POST /song/mix/return/{return_id}`: a return's `gain` and its first
  unit's parameters (`p.<name>`).
- `POST /song/track/{track_id}/fx`: an audio track's rack, unit by unit
  (`mixer.edit_rack`; the Song page's drawer on the track's row draws it).
- `POST /song/mix`: `op=balance | return-add | return-remove`. Balance
  reads the levels the song's render recorded and renders what is cold,
  so the route is `render_outside_lock`: the song is measured from a
  snapshot with the lock released, and the gains are committed back under
  it — unless the song was edited meanwhile, which a toast says.
"""

from __future__ import annotations

from fastapi import Request, Response

from .. import mixer
from ..render import RenderError
from ..routing import OutsideLock, StrForm, Svc, render_outside_lock, studio_router
from .song import _commit

router = studio_router()

STRIP_FIELDS = ("gain", "pan", "mute", "solo", "duck")
STRIP_PREFIXES = ("send.", "eq.")


def _posted(form, names: tuple, prefixes: tuple) -> dict:
    return {
        k: form[k] for k in form if k in names or any(k.startswith(p) for p in prefixes)
    }


@router.post("/song/mix/strip/{track_id}")
def mix_strip(request: Request, track_id: str, form: StrForm, svc: Svc) -> Response:
    """A strip's controls (the module docstring lists them). Unknown
    fields are ignored; a post that names none is refused."""
    values = _posted(form, STRIP_FIELDS, STRIP_PREFIXES)
    if not values:
        return svc.toast("Nothing to change on that strip.")
    said: dict = {}

    def change(song: dict) -> None:
        said["label"], said["key"] = mixer.set_strip(song, track_id, values)

    return _commit(
        svc,
        change,
        label=lambda: said.get("label"),
        key=lambda: said.get("key"),
        soon=True,
    )


@router.post("/song/track/{track_id}/fx")
def track_rack(request: Request, track_id: str, form: StrForm, svc: Svc) -> Response:
    """An audio track's rack (`mixer.edit_rack`): `op` add (`kind`), remove
    (`index`), move (`index`, `dir`) or set (`index`, `name`, `value`).
    One undo step each; a slider's posts share a key, so a drag is one.
    A synth or drums track's rack is its sound's (the dock): refused.
    `play` with `section_id` (an empty rack's "+ Reverb"): the section
    loops once the unit is on, so the first unit is heard at once."""
    op = form.get("op", "")
    if op not in mixer.RACK_OPS:
        return svc.toast("No such rack edit.")
    said: dict = {}
    sid = form.get("section_id", "")
    sections = svc.state.song["sections"]
    heard = form.get("play") and any(sec["id"] == sid for sec in sections)

    def change(song: dict) -> None:
        said["label"], said["key"] = mixer.edit_rack(song, track_id, op, form)

    return _commit(
        svc,
        change,
        label=lambda: said.get("label"),
        key=lambda: said.get("key"),
        soon=True,
        then=lambda: {"song-autoplay": {"section": sid}} if heard else None,
    )


@router.post("/song/mix/return/{return_id}")
def mix_return(request: Request, return_id: str, form: StrForm, svc: Svc) -> Response:
    """A return's level (`gain`) and its first unit's parameters
    (`p.<name>`; never its `mix`: a return is fully wet)."""
    values = _posted(form, ("gain",), ("p.",))
    if not values:
        return svc.toast("Nothing to change on that return.")
    said: dict = {}

    def change(song: dict) -> None:
        said["label"] = mixer.set_return(song, return_id, values)

    return _commit(
        svc,
        change,
        label=lambda: said.get("label"),
        key=f"return:{return_id}:{','.join(sorted(values))}",
        soon=True,
    )


def _pct(gain: float) -> str:
    return f"{round(gain * 100)}"


@router.post("/song/mix")
@render_outside_lock
def song_mix(request: Request, form: StrForm, svc: Svc) -> Response | OutsideLock:
    """`op=return-add`: one more return (the next default: a reverb, then
    a delay). `op=return-remove` (`id`): the return and every send to it.
    `op=balance`: each track that has a role to its role's level under the
    drums, in the loudest section (`mixer.balance`); one undo step, and a
    toast that lists what moved."""
    op = form.get("op", "")
    if op == "return-add":
        return _commit(svc, lambda song: mixer.add_return(song), label="add a return")
    if op == "return-remove":
        rid = form.get("id", "")
        gone: dict = {}

        def remove(song: dict) -> None:
            r = next((r for r in mixer.returns(song) if r["id"] == rid), None)
            sends = sum(1 for t in song["tracks"] if (t.get("sends") or {}).get(rid))
            gone.update(name=r["name"] if r else "", sends=sends)
            mixer.remove_return(song, rid)

        def tell() -> dict:
            n = gone["sends"]
            sent = f" and {n} send{'' if n == 1 else 's'} to it" if n else ""
            return {
                "studio-toast": {
                    "message": f"Removed {gone['name']}{sent} · Ctrl+Z brings it back.",
                    "kind": "done",
                }
            }

        return _commit(svc, remove, label="remove a return", then=tell)
    if op != "balance":
        return svc.toast("No such mix action.")
    st = svc.state
    live = st.song
    if not live["sections"] or not live["tracks"]:
        return svc.toast("Nothing to balance yet: the song has no sections.")
    if not any(t.get("role") in mixer.ROLE_OFFSETS_DB for t in live["tracks"]):
        return svc.toast(
            "Balance goes by each track's role, and no track has one: "
            "set a role in a strip's menu."
        )
    song, master = svc.song_snapshot()

    def run() -> Response:
        try:
            svc.song_gain(song, master)  # renders the sections that are cold
        except RenderError as err:
            return svc.toast(str(err))
        levels = svc.song_stem_levels(song, master)
        with st.lock:
            if st.song is not live:
                return svc.toast(
                    "The song changed while it was measured: press Balance again."
                )
            moved: list = []

            def change(now: dict) -> None:
                moved.extend(mixer.balance(now, levels))

            def tell() -> dict:
                at = mixer.loudest(levels)
                where = next(
                    (s["name"] for s in st.song["sections"] if s["id"] == at), "it"
                )
                if not moved:
                    text = f"Already balanced (read in {where})."
                else:
                    rows = [f"{n} {_pct(a)} → {_pct(b)}%" for n, a, b in moved]
                    text = f"Balanced in {where}: " + ", ".join(rows) + "."
                return {"studio-toast": {"message": text}}

            return _commit(svc, change, label="balance the mix", then=tell)

    return OutsideLock(run)
