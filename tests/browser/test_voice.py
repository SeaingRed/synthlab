"""Journey 13: the vocoder — a take from the library, a carrier, a kept result.

One pass through the Voice lab as a user makes it:

- "from the clip library…" on the page opens the drawer; "Use as take" on
  a clip fills the take slot and the page names the clip;
- chords typed into the carrier, Vocode it: audio reaches the output, a
  result row appears, and the panel still holds what was typed (it used
  to come back with 16 bands and A2);
- the carrier's own rows follow its kind, with no script;
- "as carrier" on another clip in the drawer makes that clip the carrier,
  and what was typed is still there;
- "Keep result" (its prompt names the clip) lands the result in the
  drawer's Vocoder group, and keeping it again never writes over it;
- on the Song page that clip is placed like any other.

Does not prove how any of it sounds: the take is gated noise, the
carriers are test tones. The level probe says samples reached the page's
output, nothing about words.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from synthlab.studio import schema, voice
from synthlab.studio.clips import list_clips, save_clip
from tests.browser.conftest import name_it
from tests.studio.test_song_routes import song_dict

DRAWER = "#clip-drawer"
OPEN = "() => document.getElementById('clip-drawer').matches(':popover-open')"
SR = 44100


def _phrase(seconds: float = 2.0) -> np.ndarray:
    """Noise in four bursts a second apart: syllables, no pitch."""
    n = int(SR * seconds)
    gate = np.sin(2 * np.pi * 2 * np.arange(n) / SR) > 0
    return np.random.default_rng(0).standard_normal(n) * 0.4 * gate


def _pad(seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (
        np.column_stack([np.sin(2 * np.pi * 110 * t), np.sin(2 * np.pi * 165 * t)])
        * 0.5
    )


@pytest.fixture
def lab(studio):
    voice.RESULTS = voice.LRU(4)
    d = studio.server.state.data_dir
    save_clip(d, "verse 1", _phrase(), meta={"source": "voice take"})
    save_clip(d, "pad", _pad(), meta={"source": "Warm Pad", "bpm": 120.0, "bars": 1})
    studio.server.state.song = schema.conform(song_dict(), schema.nodes().SONG)
    return studio


def _row(name: str) -> str:
    return f'{DRAWER} .cd-clip[data-name="{name}"]'


def _vocode(studio) -> None:
    page = studio.page
    with page.expect_response(lambda r: "/preview/voice" in r.url, timeout=20_000):
        page.click("#voice-render button[type=submit]")
    page.wait_for_selector("#voice-panel .voice-result")


def _shown_rows(page) -> list[str]:
    return page.locator(".vc-row").evaluate_all(
        "(els) => els.filter((e) => e.offsetParent).map((e) => e.className)"
    )


def test_a_library_clip_is_vocoded_over_chords_then_a_clip_and_kept(lab):
    page = lab.open("voice")
    d = lab.server.state.data_dir
    assert page.locator("#voice-render button[type=submit]").is_disabled()

    # the take, from the library, through the page's own button
    page.click(".voice-from-lib")
    page.wait_for_selector(f"{DRAWER}:popover-open")
    lab.settle()
    with page.expect_response(lambda r: "/voice/take/clip" in r.url):
        page.click(f"{_row('verse 1')} .cd-take")
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    status = page.locator("#voice-panel .voice-take .voice-status").inner_text()
    assert "Take ready — 2.0s" in status and "clip “verse 1”" in status
    page.keyboard.press("Escape")

    # the carrier: this sound on a row of chords
    assert page.input_value("#vc-kind") == "sound"
    assert all("vc-sound" in c for c in _shown_rows(page))
    page.fill("#voice-render [name=notes]", "Am F C G")
    page.select_option("#voice-render [name=step]", "0.5")
    page.select_option("#voice-render [name=bands]", "24")
    page.fill("#voice-render [name=sibilance]", "0.5")
    _vocode(lab)
    assert lab.wait_sound() > 1e-3
    # the panel came back holding what was typed
    assert page.input_value("#voice-render [name=notes]") == "Am F C G"
    assert page.input_value("#voice-render [name=step]") == "0.5"
    assert page.input_value("#voice-render [name=bands]") == "24"
    assert page.input_value("#voice-render [name=sibilance]") == "0.5"
    said = page.locator(".voice-result .voice-status").inner_text()
    assert "Result — 2.0s" in said and "“verse 1” over" in said and "Am F C G" in said

    # each kind shows its own rows, by CSS alone
    for kind, cls in (
        ("follow", "vc-follow"),
        ("loop", "vc-loop"),
        ("clip", "vc-clip"),
    ):
        page.select_option("#vc-kind", kind)
        shown = _shown_rows(page)
        assert shown and all(cls in c for c in shown), (kind, shown)
    assert "No clip picked yet" in page.locator(".vc-clip").inner_text()
    page.select_option("#vc-kind", "sound")
    page.fill("#voice-render [name=notes]", "Dm7 G7")  # typed, not yet rendered

    # the carrier clip, picked in the drawer; what was typed rides along
    page.select_option("#vc-kind", "clip")
    page.click(".vc-clip button")
    page.wait_for_selector(f"{DRAWER}:popover-open")
    lab.settle()
    with page.expect_response(lambda r: "/voice/carrier/clip" in r.url):
        page.click(f"{_row('pad')} .cd-carrier")
    page.wait_for_function(
        "() => document.querySelector('.vc-clip')?.textContent.includes('“pad”')"
    )
    assert page.input_value("#vc-kind") == "clip"
    assert page.input_value("#voice-render [name=notes]") == "Dm7 G7"
    assert page.evaluate(OPEN)
    page.keyboard.press("Escape")
    _vocode(lab)
    assert "over “pad”" in page.locator(".voice-result .voice-status").inner_text()

    # the audition loops until it is stopped; ▶ on the result row then
    # plays it once more
    page.click("#global-stop")
    lab.wait_silence(timeout=15.0)
    page.click(".voice-result .sc-play")
    assert lab.wait_sound() > 1e-3

    # keep it: the prompt names the clip; a second keep never writes over it
    page.click(".voice-result .vc-keep")
    with page.expect_response(lambda r: "/voice/result/keep" in r.url):
        name_it(page, "robot verse")
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '3'"
    )
    assert "Kept as “robot verse”" in page.locator("#toasts").inner_text()
    page.click(".voice-result .vc-keep")
    with page.expect_response(lambda r: "/voice/result/keep" in r.url):
        name_it(page, "robot verse")
    page.wait_for_function(
        "() => document.querySelector('#clip-library .clip-count').textContent === '4'"
    )
    names = {c["name"] for c in list_clips(d)}
    assert {"robot verse", "robot verse 2"} <= names
    meta = json.loads((d / "clips" / "robot verse.meta.json").read_text("utf-8"))
    assert meta["source"] == "vocoder" and meta["bpm"] == 120.0
    assert meta["vocoder"]["take"]["name"] == "verse 1"
    assert meta["vocoder"]["carrier"] == {"kind": "clip", "clip": "pad"}

    # it is in the drawer's Vocoder group, with this page's two actions
    page.click("#clip-library .clips-btn")
    page.wait_for_selector(f"{DRAWER}:popover-open")
    lab.settle()
    group = f'{DRAWER} .cd-group[data-group="vocoder"]'
    assert page.locator(f"{group} .cd-clip").evaluate_all(
        "(els) => els.map((e) => e.dataset.name)"
    ) == ["robot verse", "robot verse 2"]
    assert page.locator(f"{group} .cd-take").count() == 2
    assert page.locator(f"{group} .cd-carrier").count() == 2
    page.keyboard.press("Escape")

    # a reload: the take, its origin, the settings and the result are there
    page = lab.open("voice")
    assert "clip “verse 1”" in page.locator(".voice-take .voice-status").inner_text()
    assert page.input_value("#vc-kind") == "clip"
    assert page.locator(".voice-result").count() == 1

    # and on the Song page it is placed like any other clip
    lab.nav("song")
    page.click("#clip-library .clips-btn")
    page.wait_for_selector(f"{DRAWER}:popover-open")
    lab.settle()
    page.click(f"{_row('robot verse')} .cd-place-btn")
    page.wait_for_selector(
        '#song-arranger .take-chip[data-clip="robot verse"]', state="attached"
    )
    takes = lab.server.state.song["sections"][0]["audio"]
    assert [t["clip"] for t in takes] == ["robot verse"]


def test_following_a_take_with_no_pitch_says_so_and_keeps_the_panel(lab):
    """A refusal is a toast: the panel, and what was typed in it, stay."""
    page = lab.open("voice")
    page.click(".voice-from-lib")
    page.wait_for_selector(f"{DRAWER}:popover-open")
    lab.settle()
    page.click(f"{_row('verse 1')} .cd-take")
    page.wait_for_selector('#voice-panel .voice-take[data-has-take="true"]')
    page.keyboard.press("Escape")
    page.select_option("#vc-kind", "follow")
    page.select_option("#voice-render [name=snap]", "scale")
    with page.expect_response(lambda r: "/voice/render" in r.url) as got:
        page.click("#voice-render button[type=submit]")
    assert got.value.status == 422
    page.wait_for_function(
        "() => document.getElementById('toasts').textContent.includes('No clear pitch')"
    )
    assert page.input_value("#vc-kind") == "follow"
    assert page.locator(".voice-result").count() == 0
    # Chromium logs every 4xx answer as a console error; this one is the
    # refusal the test asked for, and the only thing the page reported
    refused = [p for p in lab.problems if "422" in p and "/voice/render" in p]
    assert len(refused) == 1 and lab.problems == refused
    lab.problems.clear()


def test_the_voice_form_fits_a_phone(lab):
    """The carrier rows wrap; nothing runs off a 390 px screen."""
    lab.page.set_viewport_size({"width": 390, "height": 800})
    page = lab.open("voice")
    for sel in ("#voice-panel", ".vc-carrier", "#voice-render [name=notes]"):
        box = page.locator(sel).bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390 + 1, (sel, box)
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= window.innerWidth + 1"
    )
