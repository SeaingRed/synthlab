"""The arp pattern (arppattern.py; studio docs/loop-and-perform.md, "The arp
pattern"): its editor's posts (fragments/arp_pattern.html, a popover of
every page) — the edited lane's pattern, and the session's 12 slots.

The pattern is the edited lane's `Arrangement.arp_pattern`: the grid arp
plays it in its "pattern" mode and the keys arp in its ▦ mode (arppat.js
reads it off the editor, region `arp-pattern`, which comes back by its
watch). Not a pattern undo step, as the grid arp's mode and octaves are
not: the grid's undo ring holds the notes."""

from __future__ import annotations

import copy
import math
from dataclasses import replace

from fastapi import Request, Response
from fastapi.responses import HTMLResponse

from .. import arppattern as ap
from .. import schema
from ..routing import StrForm, Svc, studio_router
from ..state import StudioState
from .pattern import with_toast

router = studio_router()


def _num(form: StrForm, key: str, default: float) -> float:
    try:
        v = float(form.get(key, default))
    except ValueError:
        return default
    return v if math.isfinite(v) else default


def pattern_from_form(form: StrForm, was: dict) -> dict:
    """The editor's post as a pattern (conformed: `schema.ARP_PATTERN`).
    `n` steps — the ones it draws post every field (a box unticked posts
    nothing: a rest), a step past them keeps what it was or is new; the
    percentages are shares; anything out of range is clamped."""
    old = was["steps"]
    n = int(min(ap.STEPS_MAX, max(1, _num(form, "n", len(old)))))
    steps = []
    for i in range(n):
        st = dict(old[i]) if i < len(old) else dict(ap.STEP_DEFAULT)
        if f"vel.{i}" in form:  # drawn: every field of it posts
            st = {
                "on": f"on.{i}" in form,
                "vel": _num(form, f"vel.{i}", 100.0) / 100.0,
                "tr": int(_num(form, f"tr.{i}", 0)),
                "gate": _num(form, f"gate.{i}", 90.0) / 100.0,
                "chance": _num(form, f"chance.{i}", 100.0) / 100.0,
                "rep": int(_num(form, f"rep.{i}", 1)),
            }
        steps.append(st)
    raw = {
        "steps": steps,
        "rate": form.get("rate", was["rate"]),
        "mode": form.get("mode", was["mode"]),
        "oct": int(_num(form, "oct", was["oct"])),
        "retrig": form.get("retrig", was["retrig"]),
    }
    out = schema.conform(raw, schema.nodes().ARP_PATTERN)
    # a take's "the keys I held" stays one: the pattern edited plays the keys
    return out | {k: was[k] for k in ("held", "swing") if k in was}


@router.post("/fragment/arp/pattern")
def arp_pattern(request: Request, form: StrForm, svc: Svc) -> Response:
    """The editor's change: the edited lane's pattern (`action=clear`: none
    again — the default plays where one is asked for)."""
    s: StudioState = svc.state
    a = s.arrangement
    if form.get("action") == "clear":
        pat = {}
    else:
        pat = pattern_from_form(form, ap.of(a))
    if pat == a.arp_pattern:
        return HTMLResponse("")
    s.arrangement = replace(a, arp_pattern=pat)
    if a.arp_mode != "pattern":  # nothing the loop plays moved
        s.save_soon()
        return HTMLResponse("")
    return svc.do_render(request)


def _slot(form: StrForm) -> int | None:
    try:
        i = int(form.get("slot", ""))
    except ValueError:
        return None
    return i if 0 <= i < ap.SLOTS else None


@router.post("/fragment/arp/slot")
def arp_slot(request: Request, form: StrForm, svc: Svc) -> Response:
    """One of the 12 slots (the session's `arp_slots`): `save` the edited
    lane's pattern into it (the default while the lane has none), `recall`
    it onto the lane, `clear` it."""
    s: StudioState = svc.state
    i, action = _slot(form), form.get("action", "")
    if i is None or action not in ("save", "recall", "clear"):
        return svc.toast("No such arp pattern slot.")
    slots = list(s.arp_slots or [None] * ap.SLOTS)
    if action == "recall":
        if slots[i] is None:
            return svc.toast(f"Arp slot {i + 1} is empty.")
        a = s.arrangement
        s.arrangement = replace(a, arp_pattern=copy.deepcopy(slots[i]))
        if a.arp_mode != "pattern":
            s.save_soon()
            return with_toast(HTMLResponse(""), f"Arp slot {i + 1} recalled.")
        return with_toast(svc.do_render(request), f"Arp slot {i + 1} recalled.")
    slots[i] = copy.deepcopy(ap.of(s.arrangement)) if action == "save" else None
    s.arp_slots = slots if any(slots) else None
    s.save_soon()
    said = (
        f"Saved to arp slot {i + 1}."
        if action == "save"
        else f"Arp slot {i + 1} cleared."
    )
    return with_toast(HTMLResponse(""), said)
