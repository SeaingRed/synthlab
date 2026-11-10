"""Every empty state leads to sound, held over the template SOURCES: every
`.empty-state` carries a next step that makes sound in one press.

`tests/browser/test_songcraft.py` presses each one it can bring on screen
and listens to the master meter; this file is what keeps a NEW empty state
from being written without one, and holds the one the browser cannot
reach in a fresh session (Breed's board, which breeds on arrival). The
Sample Lab's two are exempt (`EXEMPT`): their action is the Lab's next
step, not a sound.

A sounding action is one of:

- `fragments/empty_start.html` — "Start a song" (`POST /song/start`);
- `fragments/empty_play.html` — "Play the loop" (`POST /fragment/preview`
  with `play`);
- the rack's "＋ Reverb ▶" (`.empty-fx`: `/fragment/fx/add` with `play`);
- one of the Song page's own (`/song/start`, `/song/gen`, `/song/from-loop`).

What it does not prove: that the press is audible (the browser file does,
for the ones it reaches), or that it is the RIGHT next step — "Play the
loop" beside an empty clip library is a way to hear something, not a way
to make a clip.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import synthlab.studio as studio
from synthlab.studio.server import create_app

TEMPLATES = Path(studio.__file__).parent / "templates"
SOUNDING = (
    "fragments/empty_start.html",
    "fragments/empty_play.html",
    'class="btn btn-s empty-fx"',
    'hx-post="/song/start"',
    'hx-post="/song/gen"',
    'hx-post="/song/from-loop"',
    'hx-post="/song/arrange"',
    "fragments/song_start.html",
)
# where -> why this one has no sounding action
EXEMPT = {
    # the live vocal panel: a loading line while its popover's
    # body is fetched, and the empty rack, whose sound is the mic itself —
    # its next step is a style, beside it
    "base.html: Opening the li": "a loading line, not an empty state",
    "fragments/vocal_panel.html: No effects: th": "the mic is the sound; pick a style",
    "views/library.html: No library yet": (
        "an install fault (presets/library.json is missing), not an empty page"
    ),
    # the Sample Lab's two: their one action is the Lab's next step — choose
    # a song, cut the region into chops — and a "Start a song" or "Play the
    # loop" beside them would be off-topic; the
    # region's own ▶ is the Lab's sound, a step further on
    "fragments/lab_panel.html: Bring in a song": "the next step is an import",
    "fragments/lab_chops.html: No chops yet": "the next step is a cut",
    "fragments/voice_panel.html: No take yet. R": (
        "its three sources (● Record, Open a file, From the clips) are the "
        "buttons right above it"
    ),
    "fragments/voice_panel.html: Nothing vocode": (
        "the result of the form beside it: Vocode it, the page's primary, is "
        "the next step"
    ),
    # a filter that hides every row offers
    # the way back, Clear filter — not a song
    "views/library.html: No sound match": (
        "a filter's no-match: the next step is clearing it (Clear filter, "
        "beside it), and every row plays from its own press"
    ),
    "fragments/presets.html: No sound match": (
        "a filter's no-match: the next step is clearing it (Esc) or the "
        "Library it links to, and every sound in the list plays from its ▶"
    ),
    "fragments/sound_pick.html: No sound match": (
        "the sound menu's search no-match: the next step is a "
        "shorter search or Esc, and every sound in the menu plays as picked"
    ),
}
_OPEN = re.compile(r"<(p|div|li)\b[^>]*class=\"[^\"]*\bempty-state\b[^\"]*\"[^>]*>")


def empty_states() -> list[tuple[str, str]]:
    """(where, the element's source) of every `.empty-state`."""
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        src = path.read_text(encoding="utf-8")
        src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
        for m in _OPEN.finditer(src):
            tag = m.group(1)
            depth, at = 1, m.end()
            for t in re.finditer(rf"<(/?){tag}\b", src[m.end() :]):
                depth += -1 if t.group(1) else 1
                if depth == 0:
                    at = m.end() + t.start()
                    break
            body = src[m.end() : at]
            text = re.sub(r"\{%.*?%\}|<[^>]+>", " ", body, flags=re.S)
            head = " ".join(text.split())[:14].strip()
            name = path.relative_to(TEMPLATES).as_posix()
            out.append((f"{name}: {head}", body))
    return out


def test_the_scan_finds_them():
    found = empty_states()
    assert len(found) >= 24, [w for w, _ in found]


def test_every_empty_state_has_a_sounding_action():
    dead = [
        where
        for where, body in empty_states()
        if not any(mark in body for mark in SOUNDING)
        and not any(where.startswith(e[:-1]) or where == e for e in EXEMPT)
    ]
    assert not dead, "an empty state with no press that makes sound:\n  " + "\n  ".join(
        dead
    )


def test_the_exemptions_are_still_there():
    found = [where for where, _ in empty_states()]
    for e in EXEMPT:
        assert any(w.startswith(e[: len(e) - 3]) for w in found), e


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    with TestClient(create_app(data_dir=tmp_path_factory.mktemp("studio"))) as c:
        yield c


HX = {"HX-Request": "true"}


def test_play_the_loop_cues_the_views_mix_and_starts_it(client):
    for view in ("editor", "perform", "voice", "map", "lab"):
        url = f"http://testserver/view/{view}"
        r = client.post(
            "/fragment/preview",
            data={"play": "1"},
            headers={**HX, "HX-Current-URL": url},
        )
        assert r.status_code == 200, (view, r.text)
        ready = json.loads(r.headers["HX-Trigger"])
        assert ready["preview-ready"]["autoplay"] is True, view
    # without `play` it is audio.js's fallback, as it was: no autoplay
    r = client.post("/fragment/preview", data={"view": "editor"}, headers=HX)
    assert "autoplay" not in json.loads(r.headers["HX-Trigger"])["preview-ready"]


def test_an_empty_racks_reverb_is_added_and_played(client):
    s = client.app.state.studio
    s.fx_master = []
    r = client.post(
        "/fragment/fx/add",
        data={"scope": "master", "kind": "reverb", "play": "1"},
        headers={**HX, "HX-Current-URL": "http://testserver/view/editor"},
    )
    assert r.status_code == 200 and s.fx_master[0]["kind"] == "reverb"
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["autoplay"] is True
    # an ordinary add cues the preview and leaves the transport alone
    r = client.post(
        "/fragment/fx/add",
        data={"scope": "master", "kind": "eq"},
        headers={**HX, "HX-Current-URL": "http://testserver/view/editor"},
    )
    assert "autoplay" not in json.loads(r.headers["HX-Trigger"])["preview-ready"]
    s.fx_master = []
