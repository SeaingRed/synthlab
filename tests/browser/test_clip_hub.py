"""The clip hub (`/view/clips`) in a real browser.

Journeys, driven as a user drives them:

- the list: every clip with its waveform; the search filters it and never
  takes the keyboard out of the box; the kind filter; the URL follows;
- audition: ▶ on a row plays it (the master analyser carries signal);
- the bench: a row opens it; its player plays and seeks; ⟲ loops;
- tune: a knob moved re-asks for the player (its audio URL carries the
  tune); Keep makes a new clip, selected;
- send: a drum row's button posts the drum machine's own route and the
  list says where the clip is used now;
- make a beat: ▶ Preview renders and plays a preview, nothing written;
  Apply writes it as one step; Ctrl+Z (the hub's ↶ Undo) takes it back.

Proves signal flows and state moves; nobody listens here: the clips are
test tones and synthetic drums, and what a make sounds like is unchecked.
"""

from __future__ import annotations

import pytest

from synthlab.studio.clips import list_clips, save_clip
from tests.studio.test_clip_hub import drum_loop, melody


@pytest.fixture
def hub(studio):
    d = studio.server.state.data_dir
    save_clip(d, "loop", drum_loop(), meta={"source": "Bass", "bpm": 120.0, "bars": 2})
    save_clip(d, "tune", melody(), meta={"source": "voice take"})
    return studio


def _names(page) -> list[str]:
    return page.locator("#hub-list .hub-name").all_inner_texts()


def _open_bench(hub, name: str):
    page = hub.page
    page.click(f'#hub-list .hub-row[data-ref="clip:{name}"] .hub-open')
    # (no `settle()`: the bench's <audio preload="metadata"> keeps a media
    # fetch open, so the network is never idle for long)
    page.wait_for_selector(f'#hub-bench[data-ref="clip:{name}"] #hub-make')
    page.wait_for_function("() => !document.querySelector('.htmx-request')")
    return page


def test_the_list_filters_and_keeps_the_keyboard_in_the_box(hub):
    page = hub.open("clips")
    assert _names(page) == ["loop", "tune"]
    assert page.locator("#hub-list svg.hub-wave path").count() == 2
    page.click("#hub-filter [name=find]")
    page.keyboard.type("TU", delay=30)
    page.wait_for_function(
        "() => document.querySelectorAll('#hub-list .hub-row').length === 1"
    )
    assert _names(page) == ["tune"]
    assert page.evaluate("() => document.activeElement.name") == "find"
    assert "find=TU" in page.url
    page.fill("#hub-filter [name=find]", "")
    page.wait_for_function(
        "() => document.querySelectorAll('#hub-list .hub-row').length === 2"
    )
    page.select_option("#hub-filter [name=show]", "bounce")
    page.wait_for_function(
        "() => [...document.querySelectorAll('#hub-list .hub-name')]"
        ".map((e) => e.textContent).join() === 'loop'"
    )
    page.wait_for_function("() => location.search.includes('show=bounce')")
    assert "find=TU" not in page.url


def test_a_rows_play_button_sounds(hub):
    page = hub.open("clips")
    page.click('#hub-list .hub-row[data-ref="clip:loop"] .sc-play')
    hub.wait_sound()


def test_the_bench_plays_seeks_and_loops(hub):
    page = hub.open("clips")
    _open_bench(hub, "loop")
    assert "item=clip%3Aloop" in page.url
    assert page.locator("#hub-player .hub-wavebar svg path").count() == 1
    page.click("#hub-loop")
    assert page.evaluate("() => document.querySelector('#hub-player audio').loop")
    page.click("#hub-player .player-play")
    page.wait_for_function(
        "() => { const a = document.querySelector('#hub-player audio');"
        " return !a.paused && a.currentTime > 0.05; }"
    )
    bar = page.locator("#hub-player .hub-wavebar").bounding_box()
    page.mouse.click(bar["x"] + bar["width"] * 0.75, bar["y"] + bar["height"] / 2)
    page.wait_for_function(
        "() => document.querySelector('#hub-player audio').currentTime > 2.5"
    )
    page.click("#hub-player .player-play")  # pause


def test_tune_reasks_for_the_player_and_keep_makes_a_new_clip(hub):
    page = hub.open("clips")
    _open_bench(hub, "tune")
    page.evaluate(
        "() => window.synthlab.tweak("
        "document.querySelector('#hub-tune [name=semis]'), 5, true)"
    )
    page.wait_for_function(
        "() => document.querySelector('#hub-player audio').src.includes('semis=5')"
    )
    assert page.locator("#hub-player .badge").inner_text() == "tuned"
    page.click("#hub-tune button:has-text('Keep as new clip')")
    page.wait_for_selector('#hub-bench[data-ref="clip:tune tuned"]')
    names = [c["name"] for c in list_clips(hub.server.state.data_dir)]
    assert "tune tuned" in names
    assert "tune tuned" in _names(page)


def test_send_to_a_drum_row_says_where_it_is_used(hub):
    page = hub.open("clips")
    _open_bench(hub, "loop")
    page.click("#hub-send button[aria-label='send to the kick row']")
    page.wait_for_selector(
        '#hub-list .hub-row[data-ref="clip:loop"] .hub-used:has-text("1 drum row")'
    )
    assert hub.server.state.arrangement.drum_samples["kick"] == "loop"


def test_make_a_beat_preview_apply_and_undo(hub):
    s = hub.server.state
    page = hub.open("clips")
    drums0 = list(s.arrangement.drums)
    _open_bench(hub, "loop")
    page.click("#hub-make .hub-target label:has(input[value=beat])")
    page.select_option("#hub-make select[name=kit]", "keep")
    page.click("#hub-make button[type=submit]")
    page.wait_for_selector("#hub-result audio[data-hub-preview]", state="attached")
    page.wait_for_function("() => !document.querySelector('.htmx-request')")
    assert s.arrangement.drums == drums0  # a preview writes nothing
    assert "kick" in page.locator("#hub-result .hub-notation").inner_text()
    page.wait_for_function(
        "() => { const a = document.querySelector('#hub-result audio');"
        " return a && !a.paused; }"
    )
    page.click("#hub-result button:has-text('Apply')")
    page.wait_for_selector("#hub-result .hub-applied")
    page.wait_for_selector("#hub-undo")
    assert ["kick", 0] in s.arrangement.drums and s.arrangement.drum_bars == 2
    page.locator("body").click(position={"x": 5, "y": 5})  # out of any field
    page.keyboard.press("Control+z")
    page.wait_for_function("() => !document.getElementById('hub-undo')")
    assert s.arrangement.drums == drums0


def test_the_hub_is_on_the_rail_and_the_morph_says_it_needs_a_key(hub):
    page = hub.open("editor")
    hub.nav("clips")
    _open_bench(hub, "tune")
    ai = page.locator("#hub-make .btn-ai")
    assert ai.is_disabled()
    assert "GEMINI_API_KEY" in page.locator("#hub-make .hub-ai-note").inner_text()
    assert page.locator("#hub-make select[name=kit]").is_visible()
    page.click("#hub-make .hub-target label:has(input[value=notes])")
    assert page.locator("#hub-make select[name=sound]").is_visible()
    assert not page.locator("#hub-make select[name=kit]").is_visible()
