"""The Song editor drawer's sound (while a loop is being edited, the synth
sound it uses can be changed on the fly), in a real Chromium at 1920 and
2560, night and dense: the drawer's head
names the sound the pattern plays with — its TRACK's — as a picker over the
one sound menu; while the drawer's ▶ Play loops the cell, a pick from the
menu, ↑ ↓ on the button and the wheel each swap the track's sound and the
playing loop takes the new render where it stands (a source started part
way into its buffer), signal on the master all along; Ctrl+Z gives back the
sound before each browse; Alt+S opens the menu from the drawer.

What it proves: the swaps reach the server, a new render of the cell is
fetched and started mid-pass, the analyser carries signal. Nobody has
listened: not the crossfade, not that each sound is the right one."""

from __future__ import annotations

import pytest

from synthlab.studio.factory import FACTORY

from .test_song import _cell_ids, _with_a_beat

# the playing loop's buffers (a checksum each) and every source's start
# offset: songedit.js calls `synthlab.fetchBuffer` as it cues
_PROBE = """() => {
  const S = window.synthlab, f = S.fetchBuffer;
  window.__bufs = []; window.__offs = [];
  S.fetchBuffer = async (u) => {
    const b = await f(u), d = b.getChannelData(0);
    let s = 0;
    for (let i = 0; i < d.length; i += 7) s += Math.abs(d[i]);
    window.__bufs.push({ u, s });
    return b;
  };
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (when, off) {
    window.__offs.push(off || 0);
    return start.apply(this, arguments);
  };
}"""


def _sound(server, tid: str) -> str:
    t = next(t for t in server.state.song["tracks"] if t["id"] == tid)
    return t["patch"]["name"]


def _wait(page, check, what: str, timeout: float = 8000) -> None:
    for _ in range(int(timeout / 50)):
        if check():
            return
        page.wait_for_timeout(50)
    raise AssertionError(what)


def _heard_anew(studio, bufs: int, offs: int) -> None:
    """A new render of the cell was fetched (not the one before) and started
    part way into it: the playing loop took it where it stood."""
    page = studio.page
    page.wait_for_function(
        """([n, m]) => {
             const b = window.__bufs, o = window.__offs;
             return b.length > n && b.at(-1).u.includes("/song/cell/")
               && b.at(-1).s !== b[n - 1].s && o.slice(m).some((x) => x > 0);
           }""",
        arg=[bufs, offs],
        timeout=10_000,
    )
    assert page.get_attribute(".drawer-solo", "aria-pressed") == "true"
    studio.wait_sound()


def _marks(page) -> tuple[int, int]:
    return page.evaluate("() => [window.__bufs.length, window.__offs.length]")


@pytest.mark.parametrize("size", [(2560, 1400), (1920, 1080)])
def test_swap_the_patterns_sound_while_it_plays_undo_and_alt_s(studio, server, size):
    page = studio.page
    page.context.add_cookies(
        [
            {"name": n, "value": v, "url": server.url}
            for n, v in (("theme", "night"), ("density", "compact"))
        ]
    )
    page.set_viewport_size({"width": size[0], "height": size[1]})
    _with_a_beat(server)
    studio.open("song")
    studio.settle()
    page.click('button[hx-post="/song/from-loop"]')  # "Add loop to song"
    page.wait_for_selector(".arr-cell.filled")
    studio.settle()
    sid, _drums, tid = _cell_ids(server)
    page.locator(f"#cell-{sid}-{tid} .cell-btn").click()
    pick = page.locator("#drawer-sound")
    pick.wait_for()
    studio.settle()
    first = _sound(server, tid)
    assert pick.locator(".ds-name").inner_text() == first
    assert "in every section" in pick.get_attribute("title")
    assert page.locator("#song-drawer .drawer-sound-edit").is_visible()
    page.evaluate(_PROBE)

    page.click("#song-drawer .drawer-solo")  # ▶ Play: the cell, looping
    page.wait_for_selector('.drawer-solo[aria-pressed="true"]')
    page.wait_for_function("() => window.__bufs.length > 0")
    studio.wait_sound()

    # 1: the menu — search, Enter
    name = FACTORY[3]["name"]
    assert name != first
    marks = _marks(page)
    pick.click()
    page.wait_for_selector("#sound-pick:popover-open")
    assert "every section" in page.text_content("#spk-for")
    page.fill("#spk-q", name)
    with page.expect_response("**/song/track/*/sound"):
        page.keyboard.press("Enter")
    _wait(page, lambda: _sound(server, tid) == name, "the menu's pick did not land")
    page.wait_for_selector("#sound-pick:not(:popover-open)", state="attached")
    _heard_anew(studio, *marks)
    page.wait_for_function(
        "(n) => document.querySelector('#drawer-sound .ds-name')?.textContent === n",
        arg=name,
    )

    # 2: ↓ ↓ on the closed button — one browse
    marks = _marks(page)
    pick.focus()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    _wait(page, lambda: _sound(server, tid) != name, "↓ on the button swapped nothing")
    _heard_anew(studio, *marks)
    stepped = _sound(server, tid)
    page.locator("#song-drawer .drawer-pattern").click()  # the browse ends

    # 3: the wheel over the button — another browse
    marks = _marks(page)
    box = pick.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 120)
    _wait(page, lambda: _sound(server, tid) != stepped, "the wheel swapped nothing")
    _heard_anew(studio, *marks)
    page.mouse.move(5, 5)  # (and the pointer leaving ends it)

    # Ctrl+Z: one browse at a time, the playing loop following
    page.locator("#song-drawer .drawer-pattern").click()
    page.keyboard.press("Control+z")
    _wait(page, lambda: _sound(server, tid) == stepped, "Ctrl+Z: not the arrows' sound")
    page.keyboard.press("Control+z")
    _wait(page, lambda: _sound(server, tid) == name, "Ctrl+Z: not the menu's sound")
    page.keyboard.press("Control+z")
    _wait(page, lambda: _sound(server, tid) == first, "Ctrl+Z: not the first sound")
    page.wait_for_function(
        "(n) => document.querySelector('#drawer-sound .ds-name')?.textContent === n",
        arg=first,
    )
    assert page.get_attribute(".drawer-solo", "aria-pressed") == "true"
    studio.wait_sound()

    # Alt+S from inside the drawer: the menu, for this track, the search focused
    page.focus("#song-drawer .drawer-solo")
    page.keyboard.press("Alt+s")
    page.wait_for_selector("#sound-pick:popover-open")
    assert page.evaluate("() => document.activeElement?.id") == "spk-q"
    track = next(t for t in server.state.song["tracks"] if t["id"] == tid)
    assert page.text_content("#spk-for").startswith(track["name"])
    page.fill("#spk-q", "")  # (Esc in a search with text clears it first)
    page.keyboard.press("Escape")
    page.wait_for_selector("#sound-pick:not(:popover-open)", state="attached")
    assert _sound(server, tid) == first  # opened and closed: nothing changed
    page.click("#song-drawer .drawer-solo")
    page.wait_for_selector('.drawer-solo[aria-pressed="false"]')

    # the song playing instead: ↓ on the button swaps, the song plays on
    page.click("#song-play")
    page.wait_for_function("() => window.synthlab.songClock().playing")
    studio.wait_sound()
    marks = _marks(page)
    pick.focus()
    page.keyboard.press("ArrowDown")
    _wait(
        page, lambda: _sound(server, tid) != first, "↓ under the song swapped nothing"
    )
    # song.js's swapNow: the playing pass, again, where it stands
    page.wait_for_function(
        "(n) => window.__offs.slice(n).some((x) => x > 0)", arg=marks[1]
    )
    assert page.evaluate("() => window.synthlab.songClock().playing")
    studio.wait_sound()
    page.click("#song-play")
