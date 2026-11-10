"""Song creation's journeys, in a real Chromium under the shipped CSP:
what "ease and joy" were defined as, counted.

- **E1**: an empty session to a full, sectioned, editable sketch that is
  PLAYING in at most 3 clicks, 0 keystrokes and no page change after the
  Song page, with sound within 10 s of the click (cold, this machine).
- **E2**: the same for each of the four styles, the song passing that
  style's signature checklist; a generated cell opens in the drawer on
  its own notes.
- **E3**: the Perform loop -> a song of intro / verse / build / drop /
  break / drop / outro in at most 2 clicks.
- **E4**: every empty state the journey met offers a button that makes
  sound within 10 s.
- **E6**: three rolls of the bass are one Ctrl+Z; one more is the song
  before the starter.
- the starter from the keyboard alone, and from the command palette.
- rule 1: under 5 % of the window is bare background on the Song page
  with a starter loaded, at 1440 / 1920 / 2560.

What "sound" means here: audio.js's master analyser carries signal
(`Studio.wait_sound`). Nobody listens: that a starter is *music* is not
something this file can say.

Clicks and keys are counted in the page itself (trusted `click` and
`keydown` events, in capture, reported to Python), page changes as swaps
of `#view` and document loads after the journey's own start.
"""

from __future__ import annotations

import time

import pytest

from synthlab.studio import schema
from synthlab.studio.gen import build, checklist, styles

from .test_page_layouts import SIZES, sized  # noqa: F401 - the fixture

STYLES = list(styles.STYLES)

_COUNTER = """() => {
  if (window.__counting) return;
  window.__counting = true;
  addEventListener("click", (e) => { if (e.isTrusted) window.__tally("click"); }, true);
  addEventListener("keydown", (e) => { if (e.isTrusted) window.__tally("key"); }, true);
  let view = document.getElementById("view")?.dataset.view;
  new MutationObserver(() => {
    const now = document.getElementById("view")?.dataset.view;
    if (now && now !== view) { view = now; window.__tally("view"); }
  }).observe(document.documentElement, { childList: true, subtree: true });
}"""


class Tally:
    """Clicks, keystrokes and view changes of one journey."""

    def __init__(self, page):
        self.n = {"click": 0, "key": 0, "view": 0, "load": 0}
        page.expose_function(
            "__tally", lambda what: self.n.__setitem__(what, self.n[what] + 1)
        )
        page.on("load", lambda _p: self.n.__setitem__("load", self.n["load"] + 1))
        self.page = page

    def start(self) -> None:
        self.page.evaluate(_COUNTER)
        self.n.update(click=0, key=0, view=0, load=0)

    def settle(self) -> dict:
        self.page.wait_for_timeout(50)
        return dict(self.n)


def _song(studio) -> dict:
    return studio.server.state.song


def _click_start(studio, style: str) -> float:
    """Click a style's Start; seconds until the master analyser has signal."""
    t0 = time.perf_counter()
    studio.page.click(f'.start-card[data-style="{style}"] .start-btn')
    studio.wait_sound(timeout=10.0)
    return time.perf_counter() - t0


# --- E1 ------------------------------------------------------------------------


def test_empty_session_to_a_playing_sketch(studio):
    page = studio.open("editor")  # a fresh session opens on Synths
    tally = Tally(page)
    tally.start()
    page.click('nav.views a[href="/view/song"]')  # click 1: the Song page
    page.wait_for_selector(".start-sheet .start-first .start-btn")
    here = tally.settle()
    assert here == {"click": 1, "key": 0, "view": 1, "load": 0}
    t0 = time.perf_counter()
    page.click(".start-sheet .start-first .start-btn")  # click 2: Start
    studio.wait_sound(timeout=10.0)
    seconds = time.perf_counter() - t0
    got = tally.settle()
    assert got["click"] <= 3 and got["click"] == 2
    assert got["key"] == 0
    assert got["view"] == 1 and got["load"] == 0  # no page change after the Song page
    assert seconds <= 10.0, f"sound after {seconds:.1f} s"
    print(f"\nE1: 2 clicks, 0 keys, 0 page changes after Song, sound {seconds:.2f} s")
    # a full, sectioned, editable sketch — and it is playing
    s = _song(studio)
    assert s["gen"]["params"]["style"] == styles.DEFAULT
    assert len(s["sections"]) == len(styles.STYLES[styles.DEFAULT]["shape"])
    page.wait_for_selector("#song-play[aria-pressed=true]")
    page.wait_for_selector(".arr-section.playing")
    assert page.locator(".cell-btn.blk").count() >= 20
    assert (
        page.locator(".arr-chords .chord-slot").count() >= 2
    )  # tiles on the time axis
    # the whole plan takes over from the head plan without stopping
    page.wait_for_function(
        "() => (window.synthlab.songClock().plan?.sections.length || 0) > 1",
        timeout=30_000,
    )
    assert studio.rms() > 1e-4
    page.click("#song-stop")


# --- E2 ------------------------------------------------------------------------


@pytest.mark.parametrize("style", STYLES)
def test_each_style_starts_plays_and_shows_its_signature(studio, style):
    page = studio.open("song")
    tally = Tally(page)
    tally.start()
    seconds = _click_start(studio, style)
    got = tally.settle()
    assert (got["click"], got["key"], got["view"], got["load"]) == (1, 0, 0, 0)
    assert seconds <= 10.0, f"{style}: sound after {seconds:.1f} s"
    print(f"\nE2 {style}: sound after {seconds:.2f} s")
    s = _song(studio)
    st = styles.STYLES[style]
    assert [x["name"] for x in s["sections"]] == [x["name"] for x in st["shape"]]
    sig = checklist.signature(s)
    assert [c["text"] for c in sig if not c["ok"]] == []
    assert checklist.check(s) == []
    assert page.locator(".song-check-btn").get_attribute("data-open") == "0"
    page.click("#song-stop")
    # a generated cell opens in the drawer on its own notes, and edits there
    # the melody's track, or the chords' where a style has none (dnb)
    lead = next(
        t
        for role in (("lead", "vocal", "arp"), ("chords",))
        for t in s["tracks"]
        if t.get("role") in role
    )
    sec = next(x for x in s["sections"] if x["cells"].get(lead["id"]))
    page.click(f"#cell-{sec['id']}-{lead['id']} .cell-btn")
    page.wait_for_selector("#song-drawer:not([hidden]) .grid")
    assert page.locator("#song-drawer .grid input.cell:checked").count() > 0
    assert (
        page.locator("#song-drawer .drawer-hidden").count() == 0
    )  # none outside the rows
    pid = sec["cells"][lead["id"]]
    before = len(s["patterns"][pid]["cells"])
    page.locator(
        "#song-drawer .grid input.cell:not(:checked):not(.slide)"
    ).first.click()
    page.wait_for_function(
        "([pid, n]) => true", arg=[pid, before]
    )  # the grid posts 150 ms after the last input
    page.wait_for_timeout(600)
    assert len(_song(studio)["patterns"][pid]["cells"]) == before + 1


# --- E6 ------------------------------------------------------------------------


def test_three_rolls_of_the_bass_are_one_undo_and_one_more_is_the_empty_song(studio):
    page = studio.open("song")
    _click_start(studio, "trance")
    page.click("#song-stop")
    started = schema.conform(_song(studio), schema.nodes().SONG)
    mid = next(t for t in started["tracks"] if t["name"] == "Mid Bass")
    climax = next(x for x in started["sections"] if x["name"] == "Climax")
    page.click(f"#cell-{climax['id']}-{mid['id']} .cell-btn")
    page.wait_for_selector(
        '#song-drawer:not([hidden]) [data-cmd="Roll this part again"]'
    )
    pid = climax["cells"][mid["id"]]
    seen = [started["patterns"][pid]["cells"]]
    for _ in range(3):
        page.click('#song-drawer [data-cmd="Roll this part again"]')
        page.wait_for_function(
            "(n) => document.querySelectorAll('#toasts .toast').length >= 0", arg=0
        )
        deadline = time.monotonic() + 5
        while (
            _song(studio)["patterns"][pid]["cells"] == seen[-1]
            and time.monotonic() < deadline
        ):
            page.wait_for_timeout(50)
        seen.append(_song(studio)["patterns"][pid]["cells"])
    assert len({str(x) for x in seen}) >= 3  # the rolls differ
    studio.wait_sound(timeout=10.0)  # the roll plays by itself
    # nothing but the bass moved
    now = _song(studio)
    bass = {t["id"] for t in now["tracks"] if t.get("role") == "bass"}
    moved = {p for p, v in now["patterns"].items() if v != started["patterns"][p]}
    owners = {t for x in now["sections"] for t, p in x["cells"].items() if p in moved}
    assert owners and owners <= bass
    page.click("#song-stop")
    # the plan's WAVs are still arriving: an undo to a song without those
    # sections would 404 the ones in flight (the player prefetches ahead)
    page.wait_for_load_state("networkidle")
    page.locator(".arranger-grid").focus()
    page.keyboard.press("Control+z")  # one Ctrl+Z after three rolls: the first bass
    page.wait_for_function("() => true")
    deadline = time.monotonic() + 5
    while (
        _song(studio)["patterns"].get(pid, {}).get("cells") != seen[0]
        and time.monotonic() < deadline
    ):
        page.wait_for_timeout(50)
    assert _song(studio) == started
    page.keyboard.press("Control+z")  # one more: the song before the starter
    deadline = time.monotonic() + 5
    while _song(studio)["sections"] and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert _song(studio)["sections"] == []
    page.wait_for_selector(".start-sheet .start-btn")


# --- E3 ------------------------------------------------------------------------


def test_a_loop_becomes_a_song(studio):
    # a beat in the loop: its fill is what the arrangement adds to it (an
    # empty beat does not come along)
    from dataclasses import replace

    from synthlab.studio import beatgen

    st = studio.server.state
    a = st.arrangement
    st.arrangement = replace(
        a, drums=beatgen.generate("four-floor", 1, a.drum_bars, a.drum_steps)
    )
    page = studio.open("perform")
    tally = Tally(page)
    tally.start()
    page.click('[data-cmd="Make this loop a song"]')  # one click, on Perform
    page.wait_for_selector('#view[data-view="song"]')
    studio.wait_sound(timeout=10.0)
    got = tally.settle()
    assert got["click"] <= 2 and got["key"] == 0 and got["load"] == 0
    s = _song(studio)
    assert [x["name"] for x in s["sections"]] == [
        x["name"] for x in styles.GENERIC_SHAPE
    ]
    assert any(x.get("auto") for x in s["sections"])  # a filter move
    assert any(x["audio"] for x in s["sections"])  # a transition
    assert any(p.get("var") == "fill" for p in s["patterns"].values())  # a fill
    assert studio.server.state.song_undo_state()["depth"] == 1  # one undo step
    print(f"\nE3: {got['click']} click(s), 0 keys")
    page.click("#song-stop")


# --- E4 ------------------------------------------------------------------------

EMPTY = (
    ("song", ".arr-start .start-first .start-btn", "the empty Song page"),
    (
        "song",
        ".song-pool .pool-empty.empty-state .btn >> nth=0",
        "an empty pool: beats",
    ),
    (
        "song",
        ".song-pool .pool-empty.empty-state .btn >> nth=1",
        "an empty pool: note patterns",
    ),
    # (Perform's "no saved sounds", Drums' empty beat library and Voice's
    # empty take and result point at their own next step:
    # tests/studio/test_empty_states.py EXEMPT)
    # ("Make this loop a song" is the Loops page's Song module's ▶ Whole
    # song, never empty: test_a_loop_becomes_a_song presses it)
    ("library", ".lib-dock .empty-state .empty-start", "Library: nothing selected"),
    # (the Lab's empty states are not in this list: their one action
    # is the Lab's own next step, choosing a song — "Start a song" there was
    # off-topic; tests/studio/test_empty_states.py `EXEMPT` says so)
)


@pytest.mark.parametrize("view,button,what", EMPTY, ids=[e[2] for e in EMPTY])
def test_no_empty_state_is_a_dead_end(studio, view, button, what):
    page = studio.open(view)
    target = page.locator(button).first
    target.wait_for(state="visible")
    assert target.evaluate("(el) => !!el.closest('.empty-state')")
    assert target.evaluate("(el) => el.matches('button.btn')")
    target.click()
    level = studio.wait_sound(timeout=10.0)
    assert level > 1e-3, what
    page.wait_for_selector('#view[data-view="song"]')  # where what was made is
    page.click("#song-stop")


# The eleven `.empty-state`s P1 left without a sounding action (songcraft.md,
# "Empty states"), closed by the loose-ends package. (view, what to click
# first to bring the state on screen, its button, what it is.) Two start a
# song (`.empty-start`: the Song drawer's idle line, a sampler with no
# sample — which can only be silent); the rest play the loop the page hears
# (`.empty-play`) or, in an empty FX rack, put a reverb on it and play. The
# Lab's two are no longer here: their one action is the Lab's next step
# (choose a song, cut chops), `tests/studio/test_empty_states.py` `EXEMPT`.
EMPTY_MORE = (
    ("editor", None, ".fx-none .empty-fx >> nth=0", "an empty FX rack"),
    ("editor", None, "#preset-list .empty-state .empty-play", "Synths: no sounds"),
    ("editor", "sampler", "#sampler-panel .empty-state .empty-start", "no sample"),
    ("song", None, ".drawer-idle .empty-start", "the Song drawer, idle"),
    # (the Map has no empty state: the shipped sounds
    # are always plotted, "The Map is never an empty well")
    ("editor", ".sb-agent", "#agent-drawer .empty-state .empty-play", "the agent"),
    (
        "editor",
        "#clip-library .clips-btn",
        "#clip-drawer .empty-state .empty-play",
        "the clip library, empty",
    ),
)


@pytest.mark.parametrize(
    "view,first,button,what", EMPTY_MORE, ids=[e[3] for e in EMPTY_MORE]
)
def test_the_other_empty_states_make_sound_in_one_press(
    studio, server, view, first, button, what
):
    if first == "sampler":  # the panel shows while an oscillator samples
        from dataclasses import replace

        server.state.patch = replace(server.state.patch, osc1="sampler")
        first = None
    page = studio.open(view)
    if first:
        page.click(first)
    target = page.locator(button).first
    target.wait_for(state="visible")
    assert target.evaluate("(el) => !!el.closest('.empty-state')")
    assert target.evaluate("(el) => el.matches('button.btn') && !!el.dataset.cmd")
    target.click()
    level = studio.wait_sound(timeout=10.0)
    assert level > 1e-3, what


def test_an_empty_section_offers_a_generator(studio):
    """A song with sections and tracks but no pattern: the arranger's own
    empty state writes one that plays."""
    page = studio.open("song")
    page.click('.start-hand button[hx-post="/song/section/add"]')
    page.wait_for_selector(".arr-section")
    page.locator(".arr-foot .arr-add-track button").click()
    page.wait_for_selector(".arr-track")
    button = page.locator('.arr-empty button[hx-vals*="riff"]')
    button.wait_for(state="visible")
    button.click()
    studio.wait_sound(timeout=10.0)
    page.click("#song-stop")


# --- keyboard, palette ---------------------------------------------------------


def test_the_starter_from_the_keyboard_alone(studio):
    page = studio.open("song")
    tally = Tally(page)
    tally.start()
    page.locator("body").focus()
    for _ in range(80):
        page.keyboard.press("Tab")
        if page.evaluate(
            "() => document.activeElement?.matches('.start-btn') || false"
        ):
            break
    else:
        raise AssertionError("Tab never reached a Start button")
    # a visible focus ring, and the first Start reached is the default style's
    assert page.evaluate("() => document.activeElement.matches(':focus-visible')")
    assert page.evaluate(
        "() => document.activeElement.closest('.start-first') !== null"
    )
    page.keyboard.press("Enter")
    studio.wait_sound(timeout=10.0)
    got = tally.settle()
    assert got["click"] == 1  # Enter's own click on the button: no pointer was used
    assert _song(studio)["gen"]["params"]["style"] == styles.DEFAULT
    # the button that was pressed is gone with the sheet: the keyboard is on
    # the playlist now (one tab stop, arrows move), not lost to <body>
    page.wait_for_selector(".arr-section")
    page.wait_for_timeout(300)
    where = page.evaluate(
        "() => document.activeElement?.className || document.activeElement?.tagName"
    )
    assert "arranger-grid" in where, where
    page.keyboard.press("ArrowDown")  # and it works from there
    page.wait_for_selector(".arr-cell.cursor")
    page.click("#song-stop")


def test_start_a_trance_song_from_the_command_palette(studio):
    page = studio.open("perform")  # the palette reaches it from any empty state too
    page.goto(f"{studio.url}/view/song")
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette[open]")
    page.keyboard.type("start a song trance")
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")
    studio.wait_sound(timeout=10.0)
    assert _song(studio)["gen"]["params"]["style"] == "trance"
    page.click("#song-stop")


# --- rule 1 --------------------------------------------------------------------


@pytest.mark.parametrize("style", ("trance", "garage"))
def test_rule_1_holds_on_the_song_page_with_a_starter_loaded(sized, server, style):  # noqa: F811
    open_, bare = sized
    server.state.song = schema.conform(build.start(style, 1).song, schema.nodes().SONG)
    got = {}
    for w, h in SIZES:
        page = open_("song", w, h)
        page.wait_for_selector("#chord-strip")
        got[w] = round(bare(page), 1)
        over = page.evaluate(
            "() => [document.documentElement.scrollHeight - innerHeight,"
            " document.documentElement.scrollWidth - innerWidth]"
        )
        assert over[0] <= 1 and over[1] <= 0, (style, w, over)
    print(f"\nrule 1, {style} starter: bare background {got} %")
    assert all(v < 5.0 for v in got.values()), (style, got)


def test_rule_1_holds_on_the_empty_song_page(sized, server):  # noqa: F811
    open_, bare = sized
    got = {}
    for w, h in SIZES:
        page = open_("song", w, h)
        page.wait_for_selector(".start-sheet")
        got[w] = round(bare(page), 1)
    print(f"\nrule 1, the starter sheet: bare background {got} %")
    assert all(v < 5.0 for v in got.values()), got


def test_audio_of_a_song_that_was_replaced_is_not_an_error(studio):
    """The user problem: start a song again while one plays, and the fetches
    still out for the old song's sections come back 404 ("preview fetch
    404", a toast for each). The audio is gone because it was replaced: the
    player says nothing about a fetch it no longer wants. Here the old
    song's fetches are held until the player has stopped, then answered 404."""
    page = studio.open("song")
    page.wait_for_selector(".start-sheet .start-first .start-btn")
    _click_start(studio, styles.DEFAULT)
    page.wait_for_function(
        "() => (window.synthlab.songClock().plan?.sections.length || 0) > 1",
        timeout=30_000,
    )
    held = []
    page.route("**/song/section/*.wav*", lambda route: held.append(route))
    # every section's audio moves (a tempo change mints new URLs); the
    # player re-reads the plan and fetches ahead
    with studio.server.state.lock:
        studio.server.state.song["bpm"] += 3
    page.evaluate("() => document.dispatchEvent(new CustomEvent('song-changed'))")
    for _ in range(300):  # the plan first (it renders the song's gain)
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the player fetched nothing ahead"
    page.click("#song-stop")
    for route in held:
        route.fulfill(status=404)
    page.unroute("**/song/section/*.wav*")
    page.wait_for_timeout(400)
    # no error toast (the start's own info toast may still be up: toasts
    # have kinds, and only an error is a failure here)
    assert page.locator(".toast.error").count() == 0, page.inner_text("#toasts")
    # the browser logs each 404 itself: this test asked for them
    studio.problems[:] = [p for p in studio.problems if "404" not in p]
