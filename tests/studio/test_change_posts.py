"""Every control that posts on `change` or `input` says what happens to a
post made while another is in flight.

htmx 4's default for one element is "queue first": one request runs, the
FIRST one made meanwhile waits, and every later one is dropped. A value
control is stepped faster than the server answers (a held arrow key, wheel
ticks, a MIDI knob, a Stream Deck dial), so the default leaves the server
on the second value while the page shows the last — the mixer measured a
page at 95 % over a song at 98 %.

The rule this file holds, over the template SOURCES (so a fragment no test
page happens to draw is held too):

- a control or form that sets a **value** carries `hx-sync="…:queue last"`:
  the last post waiting is the value the hand left;
- one whose every post is an **action** (a toggle, an "add…" menu, a file
  chosen) carries `hx-sync="…:queue all"`: dropping any of them is wrong,
  and they are listed in `ACTIONS` with why.

A debounced form (`input delay:150ms`) is held to it as well: the debounce
spaces its posts 150 ms apart, a render takes longer than that, and the
third post of a slow drag was dropped like any other.

`tests/browser/test_rapid_change.py` is the same rule in a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import synthlab.studio as studio

TEMPLATES = Path(studio.__file__).parent / "templates"

_TAG = re.compile(
    r"<(form|input|select|textarea)\b((?:[^<>\"']|\"[^\"]*\"|'[^']*')*)>", re.S
)
_VERB = re.compile(r"\bhx-(?:post|get|put|patch|delete)=\"([^\"]*)\"")
# the rack macro's post (fragments/fx_units.html, `post(r, "set", …)`): the
# url is the rack's own, so its controls are known as "rack:<op>"
_RACK_POST = re.compile(r"\{\{ post\(r, \"(\w+)\"")

# url -> why each of its posts must arrive (never "the last one wins")
ACTIONS = {
    "/fragment/breed/star": "a toggle: an odd number of presses is a change",
    "/fragment/fx/add": "each choice adds a unit",
    # ("/fragment/fx/chain" is not here: the chain browser's rows are
    # buttons, a click a post)
    # an audio track's rack, in the Song page's drawer (its knobs are values)
    "/song/track/{{ d.track.id }}/fx": "each choice adds a unit",
    # the Voice page's booth rack, the same (its knobs are values)
    "/voice/booth/fx": "each choice adds a unit",
    "/voice/live/fx": "each choice adds a unit",
    "/song/gen": "each choice writes a part",
    "/project/import": "a file chosen",
    "/lab/upload": "a file chosen",
    "/fragment/pattern/import": "a file chosen",
}


def posting_controls() -> list[tuple[str, str, str | None]]:
    """(where, url, hx-sync) of every tag that posts on change or input."""
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        src = path.read_text(encoding="utf-8")
        # comments blanked in place, so line numbers hold
        src = re.sub(
            r"\{#.*?#\}", lambda m: re.sub(r"[^\n]", " ", m.group()), src, flags=re.S
        )
        for m in _TAG.finditer(src):
            tag, attrs = m.group(1), m.group(2)
            verb = _VERB.search(attrs) or _RACK_POST.search(attrs)
            if not verb:
                continue
            url = verb.group(1) if verb.re is _VERB else f"rack:{verb.group(1)}"
            trig = re.search(r'hx-trigger="([^"]*)"', attrs)
            # htmx's default trigger: submit on a form, change on a control
            on = trig.group(1) if trig else ("submit" if tag == "form" else "change")
            if not re.search(r"\b(change|input)\b", on):
                continue
            sync = re.search(r'hx-sync="([^"]*)"', attrs)
            line = src[: m.start()].count("\n") + 1
            where = f"{path.relative_to(TEMPLATES).as_posix()}:{line}"
            out.append((where, url, sync.group(1) if sync else None))
    return out


def test_the_scan_sees_the_controls():
    found = posting_controls()
    # a survey by hand counted 66; a scan that finds a handful has
    # stopped parsing, and would pass everything below
    assert len(found) >= 60
    urls = {url for _, url, _ in found}
    assert {"rack:set", "/fragment/patch", "/fragment/layer/gain"} <= urls


def test_every_value_control_keeps_the_last_post_waiting():
    wrong = [
        f"{where} {url}: hx-sync={sync!r}"
        for where, url, sync in posting_controls()
        if url not in ACTIONS and not (sync or "").endswith(":queue last")
    ]
    assert not wrong, (
        "a control that posts on change/input drops posts under htmx's "
        'default (queue first); give it hx-sync="this:queue last":\n  '
        + "\n  ".join(wrong)
    )


def test_every_action_control_keeps_every_post():
    found = posting_controls()
    wrong = [
        f"{where} {url}: hx-sync={sync!r}"
        for where, url, sync in found
        if url in ACTIONS and not (sync or "").endswith(":queue all")
    ]
    assert not wrong, "\n  ".join(wrong)
    # the list holds no url that is gone
    assert set(ACTIONS) <= {url for _, url, _ in found}
