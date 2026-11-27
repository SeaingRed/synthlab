# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The Kit page on the acoustic kit, in a real Chromium: the Kit sound
select, the articulation lane cycling a hat to open (its step shows ◐), the
loop playing the kit, and no console error, CSP violation or 5xx.

The kit is a fake: the SFZ fixture of the kit's own layout
(`tests/fixtures/kit_sfz`, its open hat ungated here) over tiny FLACs written
now, in a temp `SYNTHLAB_KIT_DIR`, and `fetch.availability` saying it is
there (the real check stats the 4262 files of the real manifest). Nobody
listened: the analyser carrying signal is all this proves of the sound."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from synthlab.kits import fetch, sfz
from synthlab.studio import arrange

FIXTURE = Path(__file__).parents[1] / "fixtures" / "kit_sfz"


def fake_kit(root: Path) -> Path:
    """The fixture kit with its open hat a plain hit and the other three hat
    articulations played by the same recordings, and a short decaying noise
    burst (FLAC) for every file it names."""
    shutil.copytree(FIXTURE, root)
    base = root / "Wilkinson Audio/Naked Drums"
    keymap = base / "User/Naked Drums GM keymap.txt"
    extra = {"KEY_HH_PDL": 44, "KEY_HH_TGH": 60, "KEY_HH_HLF": 61}
    keymap.write_text(
        keymap.read_text(encoding="utf-8")
        + "".join(f"#define ${k} {n}\n" for k, n in extra.items()),
        encoding="utf-8",
    )
    for group in (base / "Data/group").glob("*.txt"):
        text = group.read_text(encoding="utf-8").replace("locc4=1\nhicc4=40\n", "")
        block = text[text.index("<group>\nkey=$KEY_HH_OPN") :].split("\n\n")[0]
        for k in extra:
            text += "\n\n" + block.replace("KEY_HH_OPN", k)
        group.write_text(text, encoding="utf-8")
    kit = sfz._read(root.joinpath(*sfz._BASE), root)  # uncached: this root's
    rng = np.random.default_rng(7)
    n = 4410
    burst = rng.uniform(-0.5, 0.5, n) * np.exp(-np.arange(n) / 600)
    for art in kit.arts.values():
        for layer in art.layers:
            for take in layer.takes:
                for rel in take.values():
                    path = root / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        sf.write(
                            path, np.column_stack([burst, burst]), 44100, format="FLAC"
                        )
    assert {a for r, a in kit.arts if r == "hat"} == {
        "closed",
        "pedal",
        "tight",
        "half",
        "open",
    }
    return root


@pytest.fixture
def kit_here(tmp_path, monkeypatch):
    root = fake_kit(tmp_path / "kit")
    monkeypatch.setenv(fetch.KIT_ENV, str(root))
    monkeypatch.setattr(
        fetch,
        "availability",
        lambda *a, **k: {"ready": True, "megabytes": 0, "reason": ""},
    )
    sfz._cached.cache_clear()
    arrange.forget_kit()
    yield root
    sfz._cached.cache_clear()
    arrange.forget_kit()


def test_the_acoustic_kit_plays_an_open_hat_from_the_lane(kit_here, studio):
    s = studio.server.state
    s.arrangement = replace(
        s.arrangement, drums=[["hat", 0], ["hat", 4], ["hat", 8]], drum_hits={}
    )
    page = studio.open("drums")
    page.select_option('#kit-source select[name="source"]', "naked")
    page.wait_for_selector(".artrow .art-cell")
    assert s.arrangement.drum_kit == "naked"
    assert page.locator("#drum-panels .mic-strip").count() == 12
    assert "Naked Drums by Wilkinson Audio, CC-BY 4.0" in page.inner_text(
        "#drum-panels"
    )
    assert page.locator("#kit-source .kit-missing").count() == 0  # the kit is here
    # the lane shows the hat (the first row with articulations and a hit;
    # the fake kit is a hat alone: a kick here would be a KitError)
    assert page.inner_text(".artrow .lane-who") == "hat"
    lane = page.locator('.artrow .art-cell[data-step="4"]')
    assert lane.get_attribute("data-art") == "closed"
    for _ in range(4):  # closed -> pedal -> tight -> half -> open
        lane.click()
    assert lane.get_attribute("data-art") == "open"
    cell = page.locator('#drum-grid input[name="drum.hat.4"]')
    assert cell.get_attribute("data-art") == "open"
    glyph = page.evaluate(
        "() => getComputedStyle(document.querySelector('#drum-grid input[name=\"drum.hat.4\"]'), '::after').content"
    )
    assert glyph == '"◐"', glyph
    deadline = 0
    while s.arrangement.drum_hits.get("hat.4", {}).get("a") != "open" and deadline < 80:
        page.wait_for_timeout(50)
        deadline += 1
    assert s.arrangement.drum_hits == {"hat.4": {"a": "open"}}
    # the hit menu offers the hat's articulations, and only those
    page.locator('.artrow .art-cell[data-step="8"]').click(button="right")
    menu = page.locator("#hitmenu-drum-grid")
    menu.wait_for(state="visible")
    shown = [
        b.get_attribute("data-art") for b in menu.locator(".art-item:visible").all()
    ]
    assert shown == ["closed", "pedal", "tight", "half", "open"]
    menu.locator('.art-item[data-art="half"]').click()
    page.wait_for_function(
        "() => document.querySelector('#drum-grid input[name=\"drum.hat.8\"]').dataset.art === 'half'"
    )
    # the loop plays the kit
    page.click("#play")
    studio.wait_sound()
    page.click("#play")
