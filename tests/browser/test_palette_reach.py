"""The command palette reaches every declared command, on every view.

A command is a `data-cmd` on the control that performs it; the palette
harvests them from the live page. Synths declares about 80 and the
unfiltered list used to stop at 80 rows, so "Go to ..." fell off its end.
Here, per view: the unfiltered list holds EVERY command that can run (it
scrolls, it is not cut) with the navigation first; typing a command's own
name finds it; and the Stream Deck's keys, which are bound to command
names (static/deck.js `KEYS`), resolve on the views they should — a dim
key that should be lit is a renamed command.

NOT covered: what a command does when run (each view's own journeys).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.browser.test_browse_views import _close, _open, _page, seeded  # noqa: F401
from tests.browser.test_song_playlist import playlist_song, use

VIEWS = (
    "editor",
    "perform",
    "drums",
    "bass",
    "song",
    "breed",
    "map",
    "library",
    "voice",
    "lab",
)

# every runnable command, then what the palette lists for each one's own name
REACH = """() => {
  const S = window.synthlab, box = document.getElementById("pal-q");
  const rows = () => [...document.querySelectorAll("#pal-list .pal-item")]
    .map((li) => li.firstChild.textContent);
  const type = (text) => { box.value = text;
    box.dispatchEvent(new Event("input", { bubbles: true })); return rows(); };
  const all = S.commands();
  const plain = all.filter((c) => c.value === undefined).map((c) => c.label);
  const listed = type("");
  const groups = [...document.querySelectorAll("#pal-list .pal-group")]
    .map((g) => g.textContent);
  const lost = all.map((c) => c.label).filter((label) => !type(label).includes(label));
  type("");
  return { count: all.length, plain, listed, groups, lost,
    declared: S.commands(true).map((c) => c.label),
    scrolls: getComputedStyle(document.getElementById("pal-list")).overflowY };
}"""

# deck.js KEYS: label -> the views on which one of its names must be declared
# (the Perform page's key is "Loops")
GO = ("Synths", "Loops", "Kit", "Song", "Library", "Lab")
PLAYS = {"editor", "perform", "drums", "song"}
UNDOES = {
    "editor": "Undo the last tweak",
    "perform": "Undo the last pattern edit",
    "drums": "Undo the last pattern edit",
    "song": "Undo the last song edit",
}


def test_every_declared_command_is_reachable_by_its_name(chromium, seeded):  # noqa: F811
    use(seeded, playlist_song())
    ctx, page, errors = _page(chromium, seeded, 1920, 1080)
    try:
        counts = {}
        for view in VIEWS:
            _open(page, seeded, view)
            page.wait_for_load_state("networkidle")
            page.keyboard.press("Control+k")
            page.wait_for_selector("#palette[open]")
            got = page.evaluate(REACH)
            counts[view] = got["count"]
            assert got["count"] >= 20, (view, got["count"])
            # nothing is cut: every command that is not a <select>'s option
            # is in the unfiltered list, which scrolls
            assert sorted(set(got["plain"])) == sorted(set(got["listed"])), view
            assert got["scrolls"] == "auto", view
            # the navigation leads; this view's commands come before the shell's
            assert got["groups"][0] == "Go to", (view, got["groups"])
            assert got["listed"][0].startswith("Go to "), view
            # typing a command's own name finds it
            assert not got["lost"], (view, got["lost"])
            # the Stream Deck's keys: the names deck.js binds are declared here
            declared = set(got["declared"])
            for label in GO:
                assert f"Go to {label}" in declared, (view, label)
            assert "Metronome: on / off" in declared, view
            if view in PLAYS:
                assert any(
                    re.match(r"Play / stop the ", n) or n == "Play the song"
                    for n in declared
                ), view
            if view in UNDOES:
                assert UNDOES[view] in declared, view
            if view == "perform":
                assert "Resample: record what you hear to a clip" in declared
            page.keyboard.press("Escape")
        # the views that had no commands before their package have them now
        assert counts["voice"] >= 30 and counts["lab"] >= 30, counts
        assert not errors, errors
    finally:
        _close(ctx, page)


def test_the_decks_keys_are_bound_by_name_only():
    """deck.js found the loop's undo by a selector while its button had no
    `data-cmd`; it declares one now, and every name in `KEYS` is a string or
    a pattern over command names."""
    js = Path("synthlab/studio/static/deck.js").read_text(encoding="utf-8")
    keys = js[js.index("const KEYS = [[") : js.index("// what the deck knows")]
    assert "{ q:" not in keys and "hx-post" not in keys and "querySelector" not in keys
    templates = Path("synthlab/studio/templates")
    markup = "".join(p.read_text(encoding="utf-8") for p in templates.rglob("*.html"))
    # the deck panel's own buttons: deckui.js's, and the dial sets' (decksets.js)
    ui = "".join(
        Path(f"synthlab/studio/static/{f}").read_text(encoding="utf-8")
        for f in ("deckui.js", "decksets.js")
    )
    # the dock's play modes are named from the server's voicings
    # (piano.html: "Each key plays: {{ PLAYS.get(v, v) }}"): the page as drawn
    import tempfile

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    with tempfile.TemporaryDirectory() as tmp:
        drawn = TestClient(create_app(data_dir=Path(tmp))).get("/view/editor").text
    for name in re.findall(r'"([A-Z][^"]{8,})"', keys):
        cmd = f'data-cmd="{name}"'
        assert cmd in markup or cmd in drawn or name in ui, name
