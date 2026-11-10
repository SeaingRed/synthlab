"""The beat flow: pick the loop's beat on Synths and play the
keys over it, switch Perform's rack between the grids, take the loop to the
Song in one move (`docs/synths.md`, "vs beat"; `docs/loop-and-perform.md`,
"The rack's focus" and "→ Song").

What these prove: a style picked from Beat ▾ becomes the loop's beat (the
server's `arr.drums`), turns "over the beat" on and the phrase's render
carries the drums part; ▶ beat plays the beat alone through its own claim
on the transport (the loop stops, the master analyser carries signal, the
dock names "Beat"); Perform's Notes | Drums | Both shows one grid or both,
is remembered across a reload and stepped by Alt+G; → Song adds a section
and says where it went. Audio is asserted through the page's own objects —
nobody has listened.
"""

from __future__ import annotations

T = "window.synthlab.transport"


def _state(page, player: str) -> str | None:
    return page.evaluate(f"() => {T}.state('{player}')")


def test_pick_a_style_on_synths_then_play_the_beat_alone(studio, server):
    page = studio.open("editor")
    studio.settle()
    assert not server.state.arrangement.drums
    value = page.locator(
        'select[name="beat_pick"] option[value^="style:techno:"]'
    ).get_attribute("value")
    page.select_option('select[name="beat_pick"]', value)
    page.wait_for_function("() => document.querySelector('[name=vs_beat]').checked")
    studio.settle()
    s = server.state
    assert s.arrangement.drums and s.vs_beat and s.beat_style == "techno"
    # the bar came back: the select shows the beat as it is, chosen again
    assert page.input_value('select[name="beat_pick"]') == ""
    # (by its display name: "Techno", never the slug)
    assert (
        "Techno" in page.locator('select[name="beat_pick"] option').first.text_content()
    )
    assert "drums" in server.app.state.svc.render_inputs("editor").parts
    # ▶'s cue names the length the phrase now loops: the beat's bars
    cue = page.locator('[data-cue-for="play"]').text_content()
    assert cue.endswith("bar + the beat") or cue.endswith("bars + the beat"), cue

    # ▶ beat: the beat alone, its own player on the transport
    page.click("#play")
    page.wait_for_function(f"() => {T}.state('loop') === 'playing'")
    page.click("#play-beat")
    page.wait_for_function(f"() => {T}.state('beat') === 'playing'")
    assert _state(page, "loop") is None  # one thing plays: the loop stopped
    assert page.get_attribute("#play-beat", "aria-pressed") == "true"
    studio.wait_sound()
    assert page.locator("#now-playing .np-kind").text_content() == "Beat"
    assert "the beat · Techno" in page.locator("#now-playing .np-label").text_content()
    # the keys play over it (the live voice comes up beside the beat)
    studio.start_voice()
    assert _state(page, "beat") == "playing"
    page.click("#play-beat")
    page.wait_for_function(f"() => !{T}.state('beat')")
    assert page.get_attribute("#play-beat", "aria-pressed") == "false"


def test_none_takes_the_beat_out_of_the_phrase(studio, server):
    page = studio.open("editor")
    studio.settle()
    page.select_option('select[name="beat_pick"]', "bank:0")
    page.wait_for_function("() => document.querySelector('[name=vs_beat]').checked")
    studio.settle()
    beat = server.state.arrangement.drums
    page.select_option('select[name="beat_pick"]', "none")
    page.wait_for_function("() => !document.querySelector('[name=vs_beat]').checked")
    studio.settle()
    assert not server.state.vs_beat and server.state.arrangement.drums == beat


def _shown(page, sel: str) -> bool:
    return page.locator(sel).first.is_visible()


def test_play_beat_asks_again_only_when_the_beat_moved(studio, server):
    """▶ beat reads the beat's key off #play-beat (the sound bar's watch):
    a sound edit — whose preview-ready used to fetch /beat.wav every time
    — asks nothing; a new beat picked while it plays is fetched once."""
    page = studio.open("editor")
    studio.settle()
    page.select_option('select[name="beat_pick"]', "bank:0")
    page.wait_for_function(
        "() => !!document.getElementById('play-beat').dataset.beatKey"
    )
    studio.settle()
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/beat.wav" in r.url else None)
    page.click("#play-beat")
    page.wait_for_function(f"() => {T}.state('beat') === 'playing'")
    assert len(asked) == 1
    key = page.get_attribute("#play-beat", "data-beat-key")
    with page.expect_response("**/fragment/patch"):
        page.evaluate(
            "() => { const el = document.getElementById('p-resonance');"
            " window.synthlab.tweak(el, 0.5); window.synthlab.tweak.later(el); }"
        )
    studio.settle()
    page.wait_for_timeout(300)
    assert len(asked) == 1, asked  # the sound moved, the beat did not
    assert page.get_attribute("#play-beat", "data-beat-key") == key
    value = page.locator(
        'select[name="beat_pick"] option[value^="style:techno:"]'
    ).get_attribute("value")
    page.select_option('select[name="beat_pick"]', value)
    page.wait_for_function(
        f"() => document.getElementById('play-beat').dataset.beatKey !== {key!r}"
    )
    studio.settle()
    _wait_for(lambda: len(asked) == 2, page)
    assert _state(page, "beat") == "playing"
    page.click("#play-beat")


def _wait_for(ok, page, ms: int = 4000) -> None:
    for _ in range(ms // 100):
        if ok():
            return
        page.wait_for_timeout(100)
    assert ok()


def test_perform_shows_notes_drums_or_both(studio, server):
    page = studio.open("perform")
    studio.settle()
    notes, drums = "#transport .grid", "#perform-drum-grid"
    assert page.get_attribute('[data-rack-show="both"]', "aria-pressed") == "true"
    assert _shown(page, notes) and _shown(page, drums)
    page.click('[data-rack-show="notes"]')
    assert _shown(page, notes) and not _shown(page, drums)
    assert page.get_attribute('[data-rack-show="notes"]', "aria-pressed") == "true"
    page.click('[data-rack-show="drums"]')
    assert not _shown(page, notes) and _shown(page, drums)
    assert _shown(page, '[data-rack-show="notes"]')  # the way back stays on screen
    # remembered per browser
    page.reload()
    studio.settle()
    assert not _shown(page, notes) and _shown(page, drums)
    # Alt+G steps it: drums -> both -> notes
    page.locator(".view-head h1").click()  # (off any field: the XY is a tab of Play)
    page.keyboard.press("Alt+g")
    assert _shown(page, notes) and _shown(page, drums)
    page.keyboard.press("Alt+g")
    assert _shown(page, notes) and not _shown(page, drums)
    # the part buttons say they mute
    assert "mute" in page.get_attribute(".part-btn", "title").lower()


def test_to_song_from_perform(studio, server):
    page = studio.open("perform")
    studio.settle()
    page.click("#perform-arrange .to-song")
    page.wait_for_selector("#perform-arrange .to-song-note .tsn-said")
    # the note opens under its button, from its left edge — never squeezed
    # into the 106 px between the window's edge and the button
    note = page.locator("#perform-arrange .to-song-note").bounding_box()
    btn = page.locator("#perform-arrange .to-song").bounding_box()
    below = note["y"] >= btn["y"] + btn["height"] - 1
    above = note["y"] + note["height"] <= btn["y"] + 1  # flipped: no room under
    # (its opening motion may still be settling a few px)
    assert abs(note["x"] - btn["x"]) <= 8 and (below or above), (note, btn)
    assert note["width"] >= 250, note
    # ... and never over the keyboard dock. The button is the top of the
    # right column: it opens right under it
    dock = page.locator("#keys-dock").bounding_box()
    assert note["y"] + note["height"] <= dock["y"] + 1, (note, dock)
    assert below and note["y"] - (btn["y"] + btn["height"]) < 24, (note, btn)
    said = page.locator("#perform-arrange .tsn-said").text_content()
    assert "Loop 1" in said and "section 1" in said
    assert (
        page.locator("#toasts .toast").first.text_content().startswith("Added “Loop 1”")
    )
    assert len(server.state.song["sections"]) == 1
    page.click("#perform-arrange .tsn-go")
    page.wait_for_url("**/view/song")
