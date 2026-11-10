"""Journey 10: the Sample Lab — a song in, stems out, a region sent places.

The song is two bars of a beat the studio's own drum machine rendered
(`tests/songs.drum_loop`); "separation" is a fake behind the real
separator's interface (`lab_helpers.OneStem`: the mix as the drums stem),
so nothing here needs the model file or the network.

Proves, in a real Chromium under the shipped CSP: a file *dropped* on the
import box is uploaded (lab.js moves it into the file input, whose change
the form posts on); the tempo, the key's confidence and the key override
draw; Separate runs as a job, the status line polls and the stem lanes
arrive on their own; a drag across the waveform sets the region; the
drums go to the drum grid (behind its confirm), to the song's pool and to
a kit, each leaving what it did on the page; the region is placed on the
song; the zoom buttons redraw the lanes; "⤷ voice" lands on Synths with
its toast; a stem's level slider is moved from the keyboard, keeps the
keyboard, and the audition follows it; a running separation is
cancelled from the page; and a chop kept in the Lab goes on through the
clip library (test_clips.py is the drawer alone) to the Voice take, a
section of the song and back to the Lab. Does not
prove any of it sounds right, and a synthetic `drop` is not the OS's own
drag (headless Chromium has none).
"""

from __future__ import annotations

import threading

import pytest

from synthlab.audio import wav_bytes as engine_wav
from tests.browser.conftest import confirm_all
from tests.songs import drum_loop
from tests.studio.lab_helpers import FilterBank, OneStem

ROWS = {
    "kick": "x.....x...x.....x.....x...x.....",
    "snare": "....x.......x.......x.......x...",
    "hat": "..x...x...x...x...x...x...x...x.",
}

# a file dropped on the import box: lab.js's own gesture, with a File the
# page builds from the test's bytes
_DROP = """([bytes, name]) => {
  const box = document.getElementById("lab-drop");
  const dt = new DataTransfer();
  dt.items.add(new File([new Uint8Array(bytes)], name, { type: "audio/wav" }));
  const how = { bubbles: true, cancelable: true, dataTransfer: dt };
  for (const type of ["dragover", "drop"]) box.dispatchEvent(new DragEvent(type, how));
  return document.getElementById("lab-file").files.length;
}"""


@pytest.fixture
def lab(studio):
    """The Lab open with the beat dropped in, on a 120 bpm grid, and a fake
    separator installed (the model is "there")."""
    jobs = studio.server.app.state.svc.lab
    jobs.make_separator = lambda: OneStem("drums")
    jobs.availability = lambda: {
        "runtime": True,
        "model": True,
        "megabytes": 1,
        "reason": "",
    }
    page = studio.open("lab")
    confirm_all(page)  # the hx-confirm prompts
    wav = engine_wav(drum_loop(ROWS, bpm=120)[0])
    assert page.evaluate(_DROP, [list(wav), "two bars.wav"]) == 1
    page.wait_for_selector("#lab-panel .lab-name")
    assert "two bars" in page.inner_text("#lab-panel .lab-name")
    page.fill('#lab-panel form.lab-grid input[name="bpm"]', "120")
    page.click('#lab-panel form.lab-grid button[type="submit"]')
    # two bars of drums give the bar line no harmony to go by: it is found
    # half a beat late, and the quarter-beat nudge is the way to say so
    for _ in range(2):
        if "2 bars" in page.inner_text(".lab-readout"):
            break
        with page.expect_response("**/lab/grid"):
            page.click(
                '#lab-panel button[aria-label="bar lines a quarter beat earlier"]'
            )
    page.wait_for_function(
        "() => document.querySelector('.lab-readout')?.textContent.includes('2 bars')"
    )
    # what a region becomes is a card each, always on the page
    page.wait_for_selector("#lab-make .lab-card")
    return studio


def _said(page, text: str) -> str:
    page.wait_for_function(
        "(t) => document.getElementById('lab-said')?.textContent.includes(t)", arg=text
    )
    return page.inner_text("#lab-said")


def test_a_dropped_song_is_separated_and_its_drums_become_a_pattern(lab):
    page, state = lab.page, lab.server.state
    # the analysis, in words, and the key named by hand
    assert "bpm" in page.inner_text(".lab-readout")
    page.select_option('#lab-panel select[name="key"]', "D minor")
    page.wait_for_function(
        "() => document.getElementById('lab-key-readout')"
        "?.textContent.includes('set by you')"
    )
    # separate: the job's status line polls, the lanes arrive on their own
    page.click("text=Separate into stems")
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-lane').length === 5"
    )
    # the lanes land a swap before the cards under them say what they read
    page.wait_for_function(
        "() => document.getElementById('make-beat')?.innerText"
        ".includes('from drums stem')"
    )
    # a drag across the waveform is the region (bars mode: it snaps)
    box = page.locator("#lab-wave .lab-mix .lab-track").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 4, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 4, y, steps=6)
    page.mouse.up()
    page.wait_for_function(
        "() => document.querySelector('#lab-region input[name=bars]')?.value === '2'"
    )
    # the drums -> the drum grid, behind its confirm
    # the button, by role: the "also sets the loop's tempo" label beside it
    # starts with the same words, and a text selector clicked the label
    page.click('#lab-panel button:has-text("→ drum grid")')
    said = _said(page, "On the drum grid")
    assert "6 kick, 4 snare" in said and ROWS["kick"] in said
    arr = state.arrangement
    assert (arr.drum_bars, arr.drum_steps) == (2, 16)
    assert [s for v, s in arr.drums if v == "kick"] == [0, 6, 10, 16, 22, 26]
    # -> the song's pattern pool, and a kit on the drum rows
    before = len(state.song["patterns"])
    page.click('#make-beat button:has-text("→ Song beat pattern")')
    _said(page, "pattern pool")
    assert len(state.song["patterns"]) == before + 1
    page.click('#make-kit button:has-text("→ drum rows")')
    _said(page, "Kit from the drums")
    assert set(state.arrangement.drum_samples) >= {"kick", "snare"}
    lab.settle()


def _chops(lab) -> list[dict]:
    from synthlab.studio import sources

    return sources.list_sources(lab.server.app.state.svc.data_dir)[0]["chops"]


def _notes(page, cid: str, text: str) -> None:
    """Wait for the chop's row to say what its cleaning did."""
    page.wait_for_function(
        "([id, t]) => document.getElementById('chop-notes-' + id)"
        "?.textContent.includes(t)",
        arg=[cid, text],
    )


def test_a_song_is_chopped_cleaned_and_kept_as_a_clip(lab):
    """Import -> separate (the fake) -> auto-chop by bars -> drag a marker
    -> nudge it from the keyboard -> a double-click cuts another -> clean
    -> Keep -> Update clip. The marker's drag is not a region drag, the
    double-click is not one either, the marker keeps the keyboard across
    its swap, and the clip in the store says which chop it is. (The
    journey on through the library drawer is the next test.)"""
    from synthlab.studio.clips import clip_hash, list_clips

    page, svc = lab.page, lab.server.app.state.svc
    page.click("text=Separate into stems")
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-lane').length === 5"
    )
    with page.expect_response("**/lab/region"):
        page.check('#lab-wave input[name="stem.drums"]')
    region = page.input_value('#lab-region input[name="bars"]')
    # auto by bars: the region's two bars, a chop each, an edge marker each end
    # — and a double-click is one cut: the button is disabled while it posts
    # (hx-disable; htmx 2's hx-disabled-elt, read by nothing, let two through)
    cuts: list[str] = []
    page.on(
        "request",
        lambda r: (
            r.method == "POST" and r.url.endswith("/lab/chop") and cuts.append(r.url)
        ),
    )
    page.dblclick("#chop-auto-bars")
    page.wait_for_function("() => document.querySelectorAll('.chop-row').length === 2")
    lab.settle()
    assert len(cuts) == 1, cuts
    assert "2 chops cut by bars" in page.inner_text("#lab-said")
    first, second = _chops(lab)
    # (the bar line was nudged to a few ms off 2 s: the seam is where it is)
    assert first["end"] == second["start"] and abs(second["start"] - 2.0) < 0.05
    assert second["stems"] == ["drums"]
    assert page.locator("#lab-wave .chop-mark").count() == 4
    # on every lane, and on the overview strip
    assert page.locator("#lab-wave .lw-chop").count() == 2 * 6
    # drag the seam's marker left: the second chop's start, and the first
    # chop's end follows it; nothing here is a region drag
    seam = f"#chop-mark-{second['id']}-start"
    wave = page.locator("#lab-wave .lab-over").bounding_box()
    y = wave["y"] + wave["height"] / 2
    with page.expect_response("**/lab/chop"):
        page.mouse.move(wave["x"] + wave["width"] * 0.5, y)
        page.mouse.down()
        page.mouse.move(wave["x"] + wave["width"] * 0.4, y, steps=6)
        page.mouse.up()
    page.wait_for_function(
        "(s) => Math.abs(Number(document.querySelector(s)?.dataset.at) - 1.6) < 0.05",
        arg=seam,
    )
    first, second = _chops(lab)
    assert abs(second["start"] - 1.6) < 0.05 and first["end"] == second["start"]
    assert page.locator("#lab-wave .lab-drag").count() == 0
    assert page.input_value('#lab-region input[name="bars"]') == region
    # the marker kept the keyboard across the swap: an arrow nudges it 5 ms,
    # Shift 50 ms, a run of presses is one post, and it is still focused
    assert page.evaluate("() => document.activeElement?.id") == seam[1:]
    at = second["start"]
    with page.expect_response("**/lab/chop"):
        page.keyboard.press("ArrowRight")
        page.keyboard.press("Shift+ArrowRight")
    page.wait_for_function(
        "([s, t]) => document.activeElement?.id === s.slice(1)"
        " && Math.abs(Number(document.querySelector(s)?.dataset.at) - t) < 0.001",
        arg=[seam, at + 0.055],
    )
    assert abs(_chops(lab)[1]["start"] - (at + 0.055)) < 0.001
    # a double-click on a lane cuts the chop under it; the region stays
    with page.expect_response("**/lab/chop"):
        page.mouse.dblclick(wave["x"] + wave["width"] * 0.2, y)
    page.wait_for_function("() => document.querySelectorAll('.chop-row').length === 3")
    assert page.input_value('#lab-region input[name="bars"]') == region
    assert page.locator("#lab-wave .lab-drag").count() == 0
    # rename and clean the first chop: the disclosure stays open over its
    # own swap, and the row says what the cleaning did
    cid = _chops(lab)[0]["id"]
    page.fill(f"#chop-name-{cid}", "first bar")
    with page.expect_response("**/lab/chop"):
        page.keyboard.press("Enter")
    page.click(f"#chop-clean-{cid} > summary")
    page.fill(f"#chop-trim-{cid}", "-30")
    with page.expect_response("**/lab/chop"):
        page.keyboard.press("Tab")
    _notes(page, cid, "trimmed")
    assert page.locator(f"#chop-clean-{cid}[open]").count() == 1
    assert _chops(lab)[0]["clean"]["trim_db"] == -30.0
    # its audition is a WAV of its own mix
    url = page.get_attribute(f"#chop-{cid} .audition", "data-note")
    assert page.evaluate("(u) => fetch(u).then((r) => r.status)", url) == 200
    # Keep: a clip in the store that says which chop of which song it is
    page.click(f"#chop-keep-{cid}")
    page.wait_for_selector(f"#chop-update-{cid}")
    (clip,) = list_clips(svc.data_dir)
    chop = _chops(lab)[0]
    assert clip["name"] == "first bar" == chop["clip"]
    assert clip["source"] == "lab: two bars" and clip["bpm"] == 120.0
    assert clip["lab"]["chop"] == cid and clip["lab"]["stems"] == ["drums"]
    assert clip["lab"]["source"] == page.input_value('#lab-region input[name="source"]')
    assert clip["lab"]["clean"]["trim_db"] == -30.0 and not clip["lab"]["fitted"]
    # Update clip (behind its confirm): the same clip, different audio
    before = clip_hash(svc.data_dir, "first bar")
    page.select_option(f"#chop-level-{cid}", "normalise")
    _notes(page, cid, "normalised")
    with page.expect_response("**/lab/chop/keep"):
        page.click(f"#chop-update-{cid}")
    page.wait_for_function(
        "() => document.getElementById('toasts')?.textContent.includes('Updated clip')"
    )
    assert [c["name"] for c in list_clips(svc.data_dir)] == ["first bar"]
    assert clip_hash(svc.data_dir, "first bar") != before
    # a whole stem is one link away
    href = page.get_attribute('#lab-wave a[href$="/stem/drums.flac"]', "href")
    status = (
        "(u) => fetch(u).then((r) => [r.status, r.headers.get('content-disposition')])"
    )
    assert page.evaluate(status, href) == [
        200,
        'attachment; filename="two bars - drums.flac"',
    ]
    lab.settle()


def test_a_chop_goes_from_the_lab_through_the_library_to_voice_and_song(lab):
    """The whole journey, both halves' real code, one page: import ->
    separate (the fake) -> auto-chop -> drag a marker -> clean -> Keep ->
    the drawer on Voice lists the clip under its song and stems -> "Use as
    take" fills the take -> the drawer on Song -> "Place" lands it in a
    section, Ctrl+Z takes it back -> "Open in Lab" on the clip lands on the
    Lab with that song open and that chop selected -> the Stems group's
    links download a stem and the zip. Does not prove what any of it
    sounds like."""
    import numpy as np

    from synthlab.studio import lab as lab_logic
    from synthlab.studio import schema, sources
    from synthlab.studio.clips import list_clips, load_clip
    from synthlab.studio.voice import load_take
    from tests.browser.test_clips import DRAWER, OPEN, _open, _row, _rows
    from tests.studio.test_song_routes import song_dict

    page, state = lab.page, lab.server.state
    svc = lab.server.app.state.svc
    # the song the clip will be placed into: Intro s1, Verse s2, Vox "ta"
    state.song = schema.conform(song_dict(), schema.nodes().SONG)
    closed = f"() => !({OPEN[6:]})"

    # --- the Lab: separate, chop, drag, clean, keep ---------------------------
    page.click("text=Separate into stems")
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-lane').length === 5"
    )
    with page.expect_response("**/lab/region"):
        page.check('#lab-wave input[name="stem.drums"]')
    page.click("#chop-auto-bars")
    page.wait_for_function("() => document.querySelectorAll('.chop-row').length === 2")
    first, second = _chops(lab)
    sid = page.input_value('#lab-region input[name="source"]')
    wave = page.locator("#lab-wave .lab-over").bounding_box()
    y = wave["y"] + wave["height"] / 2
    with page.expect_response("**/lab/chop"):
        page.mouse.move(wave["x"] + wave["width"] * 0.5, y)
        page.mouse.down()
        page.mouse.move(wave["x"] + wave["width"] * 0.4, y, steps=6)
        page.mouse.up()
    page.wait_for_function(
        "(s) => Math.abs(Number(document.querySelector(s)?.dataset.at) - 1.6) < 0.05",
        arg=f"#chop-mark-{second['id']}-start",
    )
    first, second = _chops(lab)
    cid = first["id"]
    assert abs(first["end"] - 1.6) < 0.05 and first["end"] == second["start"]
    page.fill(f"#chop-name-{cid}", "first bar")
    with page.expect_response("**/lab/chop"):
        page.keyboard.press("Enter")
    page.click(f"#chop-clean-{cid} > summary")
    page.fill(f"#chop-trim-{cid}", "-30")
    with page.expect_response("**/lab/chop"):
        page.keyboard.press("Tab")
    _notes(page, cid, "trimmed")
    assert page.locator("#clip-library .clip-count").inner_text() == "0"
    page.click(f"#chop-keep-{cid}")
    page.wait_for_selector(f"#chop-update-{cid}")
    (clip,) = list_clips(svc.data_dir)
    name = clip["name"]
    assert name == _chops(lab)[0]["clip"] == "first bar"
    assert clip["lab"]["source"] == sid and clip["lab"]["chop"] == cid
    kept, _sr = load_clip(svc.data_dir, name)
    assert 1.0 < len(kept) / 44100 <= first["end"] and np.abs(kept).max() > 0.05
    # the Lab page's own drawer heard about it (the rail button's count)
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '1'"
    )
    # leave the OTHER chop selected, so "Open in Lab" has something to change
    page.click(f"#chop-pick-{second['id']}")
    page.wait_for_selector(f'#chop-{second["id"]}[aria-current="true"]')
    assert sources.read_meta(svc.data_dir, sid)["chop"] == second["id"]
    lab.settle()

    # --- Voice: the drawer lists it under its song and stems; Use as take ----
    lab.nav("voice")
    assert load_take(svc.data_dir) is None
    _open(lab)
    group = page.locator(f'{DRAWER} .cd-group[data-group="lab"]')
    assert group.count() == 1
    assert group.get_attribute("aria-label") == "two bars · drums"
    assert _rows(page) == [name]
    assert group.locator(f'.cd-clip[data-name="{name}"]').count() == 1
    page.click(f"{_row(name)} .cd-take")
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    take = load_take(svc.data_dir)
    assert take is not None and take.ndim == 1  # summed to mono
    assert abs(len(take) - len(kept)) <= 2 and np.abs(take).max() > 0.05
    np.testing.assert_allclose(take, np.asarray(kept).mean(axis=1), atol=1e-3)
    assert page.locator("#voice-render button[type=submit]").is_enabled()
    page.keyboard.press("Escape")
    page.wait_for_function(closed)

    # --- Song: Place into a section on the audio track; Ctrl+Z ----------------
    lab.nav("song")
    _open(lab)
    page.select_option(f"{DRAWER} [name=place_section]", "s2")
    page.wait_for_selector(
        f'{_row(name)} .cd-place-btn[hx-post="/song/section/s2/clip"]'
    )
    page.select_option(f"{DRAWER} [name=at_bar]", "1")
    page.select_option(f"{DRAWER} [name=track_id]", "ta")
    page.click(f"{_row(name)} .cd-place-btn")
    page.wait_for_selector(
        f'#song-arranger .take-chip[data-clip="{name}"]', state="attached"
    )
    placed = {"track_id": "ta", "clip": name, "at_bar": 1, "gain": 1.0}
    assert state.song["sections"][1]["audio"] == [placed]
    assert state.clip_usage()[name]["song"] == 1
    lab.settle()
    page.keyboard.press("Escape")
    page.wait_for_function(closed)
    page.keyboard.press("Control+z")
    page.wait_for_function("() => !document.querySelector('#song-arranger .take-chip')")
    assert state.song["sections"][1]["audio"] == []
    lab.settle()

    # --- back to the Lab from the clip: that song, that chop ------------------
    _open(lab)
    link = page.locator(f"{_row(name)} a.cd-lab")
    assert link.get_attribute("href") == f"/view/lab?source={sid}&chop={cid}"
    link.click()
    page.wait_for_selector(f'#view[data-view="lab"] #chop-{cid}[aria-current="true"]')
    page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
    assert "two bars" in page.inner_text("#lab-panel .lab-name")
    assert page.input_value('#lab-region input[name="source"]') == sid
    assert page.locator('.chop-row[aria-current="true"]').count() == 1
    assert page.locator("#lab-notice").count() == 0
    meta = sources.read_meta(svc.data_dir, sid)
    assert meta["chop"] == cid and meta["picked"] == ["drums"]
    start, length = lab_logic.span(meta)  # the region is the chop
    assert abs(start - first["start"]) < 1e-3 and abs(length - first["end"]) < 1e-3
    # its row still offers Update for that clip, which nothing holds any
    # more but the song's redo step — said in the library's words
    confirm = page.get_attribute(f"#chop-update-{cid}", "hx-confirm")
    assert f"“{name}”" in confirm and "1 undo step of the song" in confirm

    # --- the Stems group: a stem and the zip are one link away ----------------
    _open(lab)
    stems_of = f'{DRAWER} .cd-stems .cd-source[data-source="{sid}"]'
    hrefs = page.locator(f"{stems_of} a.cd-dl").evaluate_all(
        "(els) => els.map((e) => e.getAttribute('href'))"
    )
    model = meta["stems"]["model"]
    assert f"/lab/{sid}/stem/drums.flac?model={model}" in hrefs
    assert hrefs[-1] == f"/lab/{sid}/stems.zip?model={model}" and len(hrefs) == 5
    fetch = (
        "(u) => fetch(u).then(async (r) => [r.status,"
        " r.headers.get('content-disposition'), (await r.arrayBuffer()).byteLength])"
    )
    drums = next(h for h in hrefs if "/stem/drums.flac" in h)
    status, disposition, size = page.evaluate(fetch, drums)
    assert (status, disposition) == (
        200,
        'attachment; filename="two bars - drums.flac"',
    )
    stored = sources.stem_path(svc.data_dir, sid, "drums", model)
    assert size == stored.stat().st_size > 1000
    status, disposition, size = page.evaluate(fetch, hrefs[-1])
    assert (status, disposition) == (
        200,
        'attachment; filename="two bars - stems.zip"',
    )
    assert size > stored.stat().st_size
    page.keyboard.press("Escape")
    lab.settle()


def test_the_lab_pages_drawer_and_its_chop_list_follow_each_other(lab):
    """The drawer is on the Lab page too. A chop kept there shows up in it;
    a clip renamed from the drawer is still the chop's clip, and its row
    says the new name at once; deleted from the drawer, the row offers
    Keep again — with the drawer open through all of it."""
    from synthlab.studio.clips import list_clips
    from tests.browser.test_clips import DRAWER, OPEN, _open, _row

    page, svc = lab.page, lab.server.app.state.svc
    page.click("#chop-auto-bars")
    page.wait_for_function("() => document.querySelectorAll('.chop-row').length === 2")
    cid = _chops(lab)[0]["id"]
    page.click(f"#chop-keep-{cid}")
    page.wait_for_selector(f"#chop-update-{cid}")
    name = _chops(lab)[0]["clip"]
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '1'"
    )
    _open(lab)
    assert page.locator(f"{_row(name)} a.cd-lab").count() == 1
    # rename it in the drawer: the chop goes with its clip
    page.click(f"{_row(name)} .ren summary")
    page.fill(f"{_row(name)} .ren input[name=new]", "snare bit")
    page.keyboard.press("Enter")
    page.wait_for_selector(_row("snare bit"))
    page.wait_for_function(
        "(id) => document.getElementById('chop-update-' + id)"
        "?.getAttribute('hx-confirm').includes('“snare bit”')",
        arg=cid,
    )
    assert _chops(lab)[0]["clip"] == "snare bit"
    assert page.evaluate(OPEN)
    # delete it in the drawer: the row is back to Keep, nothing to update
    page.click(f"{_row('snare bit')} .del summary")
    page.click(f"{_row('snare bit')} .del button")
    page.wait_for_function(
        "(id) => !document.getElementById('chop-update-' + id)"
        " && document.getElementById('chop-keep-' + id)?.textContent.trim() === 'Keep'",
        arg=cid,
    )
    assert list_clips(svc.data_dir) == []
    assert page.locator(f"{DRAWER} .cd-clip").count() == 0 and page.evaluate(OPEN)
    assert page.locator(".chop-row").count() == 2
    # and Keep keeps it again, under the chop's own name
    page.keyboard.press("Escape")
    page.click(f"#chop-keep-{cid}")
    page.wait_for_selector(f"#chop-update-{cid}")
    assert [c["name"] for c in list_clips(svc.data_dir)] == [name]
    lab.settle()


def test_a_stem_level_is_set_from_the_keyboard_and_changes_the_audition(lab):
    """The level slider of a stem lane: arrow keys move it, each change is
    posted and the body morphed, so the slider keeps the keyboard; the
    audition's URL follows the level (another URL, so the <audio> loads the
    new mix) and what it plays is that much quieter. Solo ticks one stem
    alone and keeps the keyboard too."""
    from synthlab.audio import read_pcm16
    from synthlab.studio import sources

    page, svc = lab.page, lab.server.app.state.svc
    page.click("text=Separate into stems")
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-lane').length === 5"
    )
    with page.expect_response("**/lab/region"):
        page.check('#lab-wave input[name="stem.drums"]')
    page.wait_for_function(
        "() => document.querySelector('#lab-panel audio')?.src.endsWith('stems=drums')"
    )
    wav = "() => fetch(document.querySelector('#lab-panel audio').src)" + (
        ".then((r) => r.arrayBuffer()).then((b) => Array.from(new Uint8Array(b)))"
    )
    plain = read_pcm16(bytes(page.evaluate(wav)))[0]
    slider = page.locator("#lab-level-drums")
    slider.focus()
    for presses in (1, 2, 3):  # 0.5 dB a key; the third works like the first
        with page.expect_response("**/lab/region"):
            page.keyboard.press("ArrowLeft")
        page.wait_for_function(
            "(t) => document.querySelector('.lab-level output')?.textContent === t",
            arg=f"−{presses * 0.5:g} dB",
        )
        assert page.evaluate("() => document.activeElement?.id") == "lab-level-drums"
    assert sources.levels(sources.list_sources(svc.data_dir)[0])["drums"] == -1.5
    assert "levels=-1.5&stems=drums" in page.get_attribute("#lab-panel audio", "src")
    quiet = read_pcm16(bytes(page.evaluate(wav)))[0]
    assert abs(abs(quiet).max() / abs(plain).max() - 10 ** (-1.5 / 20)) < 0.01
    # a drag of the level is not a drag across the waveform (it is a knob,
    # whose 160 px is its whole travel, left or down = less)
    region = page.input_value('#lab-region input[name="bars"]')
    box = slider.bounding_box()
    with page.expect_response("**/lab/region"):
        page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] - 220, box["y"] + box["height"] / 2, steps=8)
        page.mouse.up()
    page.wait_for_function(
        "() => document.querySelector('.lab-level output')?.textContent === '−24 dB'"
    )
    assert page.input_value('#lab-region input[name="bars"]') == region
    assert page.locator("#lab-wave .lab-drag").count() == 0
    # solo: one stem alone, the button pressed, and still under the keyboard
    page.focus("#lab-solo-bass")
    with page.expect_response("**/lab/region"):
        page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('lab-solo-bass')?.getAttribute('aria-pressed')"
        " === 'true'"
    )
    assert page.is_checked('#lab-wave input[name="stem.bass"]')
    assert not page.is_checked('#lab-wave input[name="stem.drums"]')
    page.wait_for_function("() => document.activeElement?.id === 'lab-solo-bass'")
    lab.settle()


def test_a_region_is_placed_on_the_song_zoomed_and_played_as_a_voice(lab):
    page, state = lab.page, lab.server.state
    status = page.evaluate(
        """() => fetch("/song/section/add", {
             method: "POST",
             headers: { "Content-Type": "application/x-www-form-urlencoded" },
             body: "name=Verse&bars=4",
           }).then((r) => r.status)"""
    )
    assert status == 200
    lab.nav("editor")
    lab.nav("lab")  # the panel lists the song's sections when it is drawn
    page.wait_for_selector('#lab-panel select[name="section_id"]')
    page.click('#make-take button:text-is("→ Song")')
    said = _said(page, "Placed")
    assert "Verse, bar 1" in said and "bpm" in said
    assert len(state.song["sections"][-1]["audio"]) == 1
    # zoom: the lanes are redrawn over a shorter span, and back
    page.click('#lab-panel button[title^="zoom in"]')
    page.wait_for_function(
        "() => document.querySelector('.lab-zoom-readout')?.textContent.includes('×2')"
    )
    page.click('#lab-panel button[title^="show the whole song"]')
    page.wait_for_function(
        "() => !document.querySelector('.lab-zoom-readout')?.textContent.includes('×')"
    )
    # the region as a voice: Synths, a sampler sound, and what was found
    page.click('#make-voice button:has-text("Make voice")')
    page.wait_for_selector('#view[data-view="editor"]')
    page.wait_for_selector("#sampler-panel:not([hidden]) .sampler-wave")
    assert state.patch.osc1 == "sampler"
    lab.settle()


def test_a_running_separation_is_cancelled_from_the_page(lab):
    page = lab.page
    gate = threading.Event()

    class Slow(FilterBank):
        def run(self, chunk):
            gate.wait(10)  # held until the page has asked to cancel
            return super().run(chunk)

    lab.server.app.state.svc.lab.make_separator = Slow
    page.click("text=Separate into stems")
    page.wait_for_selector("#lab-job progress")
    assert page.locator("#lab-job button").inner_text() == "cancel"
    page.click("#lab-job button")
    gate.set()
    page.wait_for_function(
        "() => document.querySelector('#lab-job')?.textContent.includes('Cancelled')"
    )
    # the mix, and the call-out where the stems will be: no stem lanes
    assert page.locator("#lab-wave .lab-lane:not(.lab-cta)").count() == 1
    assert page.locator("text=Separate into stems").is_enabled()
    lab.settle()


def test_a_whole_stem_goes_onto_the_song_and_plays_through_a_repeat(lab, tmp_path):
    """Long audio: Lab -> separate (the fake) -> tick the drums -> "→ Song
    track, whole" -> the Song page draws it across the sections it crosses
    -> it plays -> the export holds the stem.

    Proves: the Lab's button reaches the song with the lanes' mix and the
    Lab's tempo, the region is drawn in every section's audio cell, the
    plan gives the repeated section a WAV a pass, the player plays them to
    the output, and the export is not silent. Does not prove a seam between
    two passes is inaudible, or how the stem sits against a beat."""
    page, state = lab.page, lab.server.state
    for name in ("A", "B"):  # two 1-bar sections under a 2-bar stem
        status = page.evaluate(
            """(name) => fetch("/song/section/add", {
                 method: "POST",
                 headers: { "Content-Type": "application/x-www-form-urlencoded" },
                 body: `name=${name}&bars=1`,
               }).then((r) => r.status)""",
            name,
        )
        assert status == 200
    page.click("text=Separate into stems")
    page.wait_for_selector('#lab-panel input[name="stem.drums"]')  # not a skeleton
    with page.expect_response("**/lab/region"):
        page.check('#lab-panel input[name="stem.drums"]')
    page.wait_for_selector("#lab-wave .lab-lane.feeds input[name='stem.drums']")
    with page.expect_response("**/song/long") as posted:
        page.click("#lab-whole")
    assert posted.value.status == 200
    (item,) = state.song["long"]
    assert item["stems"] == ["drums"] and item["at_bar"] == 0
    assert state.song["bpm"] == 120  # the song took the Lab's tempo
    assert [t["kind"] for t in state.song["tracks"]].count("audio") == 1

    # the Song page: one strip in each section's audio cell, its row below
    lab.nav("song")
    page.wait_for_selector(".long-row")
    assert page.locator(".arr-takes .long-part").count() == 2
    assert page.locator(".long-part .long-name").count() == 1  # named where it starts
    assert "bars 1–2" in page.inner_text(".long-says")
    # a repeat does not replay it: the first section twice pushes the rest on
    first = state.song["sections"][0]["id"]
    with page.expect_response(f"**/song/section/{first}/repeats"):
        page.evaluate(
            """(id) => htmx.ajax("POST", `/song/section/${id}/repeats`,
                 { values: { repeats: "2" }, swap: "none" })""",
            first,
        )
    lab.settle()
    plan = page.evaluate("() => fetch('/song/plan.json').then((r) => r.json())")
    assert len(plan["sections"][0]["pass_urls"]) == 2
    assert len(set(plan["sections"][0]["pass_urls"])) == 2  # two slices, two WAVs

    # it plays: samples reach the page's output
    page.click('[data-song-action="play"]')
    page.wait_for_function(
        "() => /bar [0-9]/.test(document.getElementById('song-position')?.textContent)"
    )
    deadline, heard = 40, 0.0
    while deadline and heard < 1e-3:
        page.wait_for_timeout(100)
        heard, deadline = max(heard, lab.rms()), deadline - 1
    assert heard > 1e-3
    page.click('[data-song-action="stop"]')

    with page.expect_download() as dl:
        page.click(".song-export button[type=submit]")
    wav = tmp_path / "song.wav"
    dl.value.save_as(wav)
    data = wav.read_bytes()
    assert data[:4] == b"RIFF" and len(set(data[4096:8192])) > 8  # not silence

    # its row: each control posts itself (its ids ride in hx-vals)
    with page.expect_response("**/song/long"):
        page.fill('.long-row input[name="bar"]', "2")
        page.keyboard.press("Tab")
    page.wait_for_function(
        "() => document.querySelector('.long-row input[name=bar]')?.value === '2'"
    )
    assert state.song["long"][0]["at_bar"] == 1
    with page.expect_response("**/song/long"):
        page.click(".long-row .rowbtn.mute")
    page.wait_for_selector('.long-row .rowbtn.mute[aria-pressed="true"]')
    assert state.song["long"][0]["mute"] is True
    page.wait_for_selector(".long-part.muted")

    # Ctrl+Z territory: taking it off is one step, and it comes back
    page.click(".long-row .long-x")
    page.wait_for_function("() => !document.querySelector('.long-row')")
    assert "long" not in state.song
    page.click('#song-topbar [hx-post="/song/undo"]')
    page.wait_for_selector(".long-row")
    assert len(state.song["long"]) == 1
    lab.settle()


# --- the Lab: where from, what it becomes, where it went ---


def _drag(
    page, a: float, b: float, selector: str = "#lab-wave .lab-mix .lab-track"
) -> None:
    """A drag across a lane from `a` to `b` (fractions of its width)."""
    box = page.locator(selector).bounding_box()
    y = box["y"] + box["height"] * 0.6
    page.mouse.move(box["x"] + box["width"] * a, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * b, y, steps=6)
    page.mouse.up()


def test_a_dragged_region_is_heard_looped_and_becomes_a_sampler_sound(lab):
    """Import (the fixture's drop) -> drag the first bar -> the selection
    bar says "bar 1" and the mix feeds it -> ▶ plays it, looped -> ⤷ Sampler,
    sliced -> it opens on Synths as the edited sound."""
    page, state = lab.page, lab.server.state
    # before a separation the lanes say stems exist, where they will be
    assert "drums · bass · vocals · other" in page.inner_text("#lab-wave .lab-cta")
    with page.expect_response("**/lab/region"):
        _drag(page, 0.02, 0.4)
    page.wait_for_function(
        "() => document.querySelector('.lab-pick-where')?.textContent"
        ".includes('bar 1 · 1 bar')"
    )
    page.wait_for_selector("#lab-wave .lab-mix.feeds", state="attached")
    assert page.inner_text(".lab-pick-what .lab-what") == "mix"
    # the one-press lengths: 2 bars, and the selection follows
    with page.expect_response("**/lab/region"):
        page.click('.lab-lengths button:text-is("2")')
    page.wait_for_function(
        "() => document.querySelector('.lab-pick-where')?.textContent"
        ".includes('bars 1–2')"
    )
    # the audition plays the region, round and round until told otherwise
    page.click(".lab-player .player-play")
    page.wait_for_function(
        "() => { const a = document.querySelector('.lab-player audio');"
        " return a && !a.paused && a.currentTime > 0 && a.loop; }"
    )
    page.click(".lab-player .player-loop")
    assert (
        page.evaluate("() => document.querySelector('.lab-player audio').loop") is False
    )
    assert page.get_attribute(".lab-player .player-loop", "aria-pressed") == "false"
    page.click(".lab-player .player-play")  # pause before leaving
    # the selection bar makes the sound, sliced, and lands on Synths with it
    page.select_option('#lab-region select[name="chop"]', "beats")
    page.click("#lab-sampler")
    page.wait_for_selector('#view[data-view="editor"]')
    page.wait_for_selector("#sampler-panel:not([hidden]) .sampler-wave")
    assert state.patch.osc1 == "sampler" and state.patch.sampler_slices
    lab.settle()


def test_a_drum_stems_region_becomes_a_pattern_that_lands_on_drums(lab):
    """Separate (the fake: the beat as the drums stem) -> the Drum pattern
    card reads the drums stem -> → drum grid -> the said line: Undo takes it
    back, and again -> "Open on Kit" -> the Kit page, with the beat."""
    page, state = lab.page, lab.server.state
    page.click("text=Separate into stems")
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-lane:not(.lab-mix)')"
        ".length === 4 && !document.querySelector('.lab-lane-skel')"
    )
    page.wait_for_function(
        "() => document.querySelector('#make-beat .lab-from')?.textContent"
        " === 'from drums stem'"
    )
    # solo the drums: the lane says it feeds, the mix lane offers itself back
    with page.expect_response("**/lab/region"):
        page.click("#lab-solo-drums")
    page.wait_for_selector("#lab-wave .lab-lane.lane-drums.feeds", state="attached")
    # ... and gives it back: every box unticked, the recording feeds again
    with page.expect_response("**/lab/region"):
        page.click("#lab-use-mix")
    page.wait_for_selector("#lab-wave .lab-mix.feeds", state="attached")
    assert page.locator("#lab-wave input[name^='stem.']:checked").count() == 0
    # the drum card reads the drums stem whatever the lanes feed
    assert page.inner_text("#make-beat .lab-from") == "from drums stem"
    with page.expect_response("**/lab/region"):
        _drag(page, 0.02, 0.98)
    page.wait_for_function(
        "() => document.querySelector('#lab-region input[name=bars]')?.value === '2'"
    )
    before = state.pattern_snapshot()["arr"]
    page.click('#make-beat button:has-text("→ drum grid")')
    said = _said(page, "On the drum grid")
    assert "Open on Kit" in said and "Undo" in said
    assert [s for v, s in state.arrangement.drums if v == "kick"][:2] == [0, 6]
    page.click("#lab-undo")
    _said(page, "Undone")
    assert state.pattern_snapshot()["arr"] == before
    page.click('#make-beat button:has-text("→ drum grid")')
    _said(page, "On the drum grid")
    page.click('#lab-said a:has-text("Open on Kit")')
    page.wait_for_selector('#view[data-view="drums"]')
    assert [s for v, s in state.arrangement.drums if v == "kick"][:2] == [0, 6]
    lab.settle()


# --- the Lab: pick a sample, zoom into the stems ------


@pytest.fixture
def lanes(studio):
    """The Lab open on 16 bars at 120 bpm (32 s, bar 1 at 0), separated by
    the fake filter bank into the four stems — stored before the page opens,
    so the lanes are there at once."""
    from synthlab import stems as stem_models
    from synthlab.studio import lab as labmod
    from synthlab.studio import sources
    from tests.studio.lab_helpers import song_wav

    data = studio.server.app.state.svc.data_dir
    meta = sources.add(
        data, song_wav(bars=16), "sixteen bars.wav", analyse=labmod.analyse
    )
    sources.update_meta(
        data, meta["id"], lambda m: m["user"].update(bpm=120.0, offset=0.0)
    )
    audio = sources.read(data, sources.read_meta(data, meta["id"]))
    split = FilterBank().run(audio.T.astype("float32"))
    model = next(iter(stem_models.MODELS))
    parts = {n: split[i].T for i, n in enumerate(FilterBank.sources)}
    sources.save_stems(data, meta["id"], model, parts, meta["seconds"])
    page = studio.open("lab")
    confirm_all(page)
    page.wait_for_function(
        "() => document.querySelectorAll('#lab-wave .lab-track').length === 5"
        " && !!window.synthlab?.lab"
    )
    studio.sid = meta["id"]
    return studio


def _lab_meta(lanes) -> dict:
    from synthlab.studio import sources

    return sources.read_meta(lanes.server.app.state.svc.data_dir, lanes.sid)


def _numbers(page) -> list[int]:
    return [int(t) for t in page.locator("#lab-wave .lab-bar-n").all_inner_texts()]


def _x_of(page, t: float) -> float:
    """The page x of a time of the song in what the lanes show now."""
    return page.evaluate(
        "(t) => { const r = document.querySelector('#lab-wave .lab-over')"
        ".getBoundingClientRect();"
        " return r.left + window.synthlab.lab.pct(t) / 100 * r.width; }",
        t,
    )


def _where(page, text: str) -> None:
    """Wait for the selection bar to say where the selection is."""
    page.wait_for_function(
        "(t) => document.querySelector('.lab-pick-where')?.textContent.includes(t)",
        arg=text,
    )


def _lane(page, name: str) -> dict:
    return page.locator(
        f'#lab-wave .lab-lane[data-lane="{name}"] .lab-track'
    ).bounding_box()


def test_ctrl_wheel_zooms_round_the_pointer_and_the_ruler_numbers_the_bars(lanes):
    """Ctrl + wheel over the lanes zooms at once, the moment under the
    pointer staying under it; the span is posted when the wheel rests and
    the lanes come back drawn over it. The ruler numbers the bars at both
    zooms — the phrases' bars when they crowd, every bar zoomed in."""
    page = lanes.page
    whole = _numbers(page)
    assert {1, 5, 9, 13} <= set(whole) and whole == sorted(whole)
    track = _lane(page, "vocals")
    x, y = track["x"] + track["width"] * 0.6, track["y"] + track["height"] / 2
    page.mouse.move(x, y)
    t0 = page.evaluate("(x) => window.synthlab.lab.at({clientX: x})", x)
    page.keyboard.down("Control")
    for _ in range(5):
        page.mouse.wheel(0, -120)
    page.keyboard.up("Control")
    view = page.evaluate("() => window.synthlab.lab.view()")
    assert view["seconds"] < 32 / 2.5
    t1 = page.evaluate("(x) => window.synthlab.lab.at({clientX: x})", x)
    assert abs(t1 - t0) < 0.05  # the time under the pointer stayed under it
    # the page's span goes to the server, which draws the lanes over it
    page.wait_for_function(
        "() => { const w = document.getElementById('lab-wave');"
        " const v = window.synthlab.lab.view();"
        " return Math.abs(+w.dataset.viewSeconds - v.seconds) < 0.01; }"
    )
    shown = _lab_meta(lanes)["view"]
    assert abs(shown["length"] - view["seconds"]) < 0.01
    assert abs(shown["start"] - view["start"]) < 0.01
    zoomed = _numbers(page)
    assert zoomed == list(range(zoomed[0], zoomed[0] + len(zoomed)))  # every bar
    assert len(zoomed) < len(whole) and int(t0 // 2) + 1 in zoomed
    # the bars and, zoomed in this far, the beats are drawn over the lanes
    assert page.locator("#lab-wave .lab-gridlines .lg-beat").count() == 1
    lanes.settle()


def test_the_overview_box_scrolls_the_lanes(lanes):
    page = lanes.page
    page.click('#lab-wave .lab-corner button[aria-label="zoom in on the region"]')
    page.click('#lab-wave .lab-corner button[aria-label="zoom in on the region"]')
    page.wait_for_function(
        "() => +document.getElementById('lab-wave').dataset.viewSeconds < 9"
    )
    before = page.evaluate("() => window.synthlab.lab.view()")
    box = page.locator("#lab-wave .lab-ov-win").bounding_box()
    strip = page.locator("#lab-wave .lab-strip").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + strip["width"] * 0.25, y, steps=5)
    page.mouse.up()
    after = page.evaluate("() => window.synthlab.lab.view()")
    # a quarter of the strip is a quarter of the song (32 s)
    assert after["start"] - before["start"] == pytest.approx(8.0, abs=0.6)
    assert after["seconds"] == pytest.approx(before["seconds"])
    page.wait_for_function(
        "(s) => Math.abs(+document.getElementById('lab-wave').dataset.viewStart"
        " - s) < 0.01",
        arg=after["start"],
    )
    assert abs(_lab_meta(lanes)["view"]["start"] - after["start"]) < 0.01
    lanes.settle()


def test_a_drag_on_a_stem_lane_selects_it_snapped_and_enter_keeps_it(lanes):
    """The main flow: drag on the vocals lane -> the vocals alone, snapped
    to the bars it touched -> Shift+→ moves it a bar, ] makes it a bar
    longer -> Enter keeps it as a clip named for what and where -> a click
    on another lane plays that lane from there."""
    page, data = lanes.page, lanes.server.app.state.svc.data_dir
    box = _lane(page, "vocals")
    y = box["y"] + box["height"] / 2
    page.mouse.move(_x_of(page, 2 * 2.3), y)  # bar 3, a third in
    page.mouse.down()
    page.mouse.move(_x_of(page, 2 * 4.6), y, steps=8)  # into bar 5
    page.mouse.up()
    _where(page, "bars 3–5")
    meta = _lab_meta(lanes)
    assert meta["picked"] == ["vocals"]
    assert meta["region"] == {"mode": "bars", "start": 2.0, "length": 3.0}
    assert page.inner_text(".lab-pick-what .lab-what") == "vocals"
    # the keys on the lanes (the drag left them the keyboard)
    assert page.evaluate("() => document.activeElement?.id") == "lab-wave"
    with page.expect_response("**/lab/region"):
        page.keyboard.press("Shift+ArrowRight")
    _where(page, "bars 4–6")
    with page.expect_response("**/lab/region"):
        page.keyboard.press("]")
    _where(page, "bars 4–7")
    page.keyboard.press("Enter")
    said = _said(page, "Kept as clip")
    assert "“vocals bars 4-7”" in said
    from synthlab.studio.clips import list_clips

    clip = next(c for c in list_clips(data) if c["name"] == "vocals bars 4-7")
    assert clip["lab"]["stems"] == ["vocals"] and clip["bars"] == 4
    # a click (no drag) on the bass lane plays the bass from there
    bass = _lane(page, "bass")
    page.mouse.click(_x_of(page, 10.0), bass["y"] + bass["height"] / 2)
    page.wait_for_function(
        "() => { const a = document.getElementById('lab-scrub');"
        " return a && !a.paused && a.src.includes('stems=bass')"
        " && a.src.includes('start=10'); }"
    )
    page.keyboard.press("Escape")
    page.wait_for_function(
        "() => { const a = document.getElementById('lab-scrub');"
        " return a.paused && !a.getAttribute('src'); }"
    )
    lanes.settle()


def test_the_selections_edge_drags_and_its_chop_is_cut_named_for_it(lanes):
    page = lanes.page
    _drag(page, 0.0, 0.2, '#lab-wave .lab-lane[data-lane="drums"] .lab-track')
    page.wait_for_function(
        "() => document.querySelector('.lab-pick-what .lab-what')?.textContent"
        " === 'drums'"
    )
    region = _lab_meta(lanes)["region"]
    end = page.locator('#lab-wave .lab-sel-edge[data-edge="end"]').bounding_box()
    y = end["y"] + end["height"] / 2
    page.mouse.move(end["x"] + end["width"] / 2, y)
    page.mouse.down()
    to = 2 * (region["start"] + region["length"] + 2) + 0.2  # two bars on
    page.mouse.move(_x_of(page, to), y, steps=6)
    with page.expect_response("**/lab/region"):
        page.mouse.up()
    page.wait_for_function(
        "(n) => document.querySelector('#lab-region input[name=bars]')?.value"
        " === String(n)",
        arg=int(region["length"]) + 2,
    )
    page.click("#lab-chop-add")
    _said(page, "Chop “drums bars")
    page.wait_for_selector('#lab-chops .chop-row[aria-current="true"]')
    lanes.settle()
