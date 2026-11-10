"""The drum grid and the note grid are shared macros (`templates/grids.html`)
so Drums, Perform and the Song page's drawer draw the same markup. Factoring
them out of `drum_bank.html` and `transport.html` must not change a byte of
what Drums and Perform render: both regions are pinned against goldens
rendered by the templates before the refactor
(`tests/fixtures/grid_partials/`).

The session is populated so every branch of both grids renders: hits across
two bars, a muted and a soloed row, a row playing a clip (and the clip
select), velocities, notes, slides — and, since the drum grid is the step
sequencer, a hit with each kind of per-hit data, a row with a swing of its
own and a level off unity.

To regenerate after an intended grid change:
`uv run python -m tests.studio.test_grid_macros` (both goldens), or with
`drums` / `notes` for one — each grid regenerates its own. The drum
golden holds the step sequencer's markup, a row's sample menu with its
Percussion and Clips groups, and (in both) the row's ⋯ and the pattern
tools' menus as popovers (a <details> never closed, and a panel's
overflow clipped it).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.studio.clips import save_clip
from synthlab.studio.server import create_app

GOLDEN = Path(__file__).parents[1] / "fixtures" / "grid_partials"


def _slice(html: str, start: str, end: str) -> str:
    i = html.index(start)
    return html[i : html.index(end, i) + len(end)]


def grid_regions(data_dir: Path) -> dict[str, str]:
    save_clip(data_dir, "hit", np.zeros(4000, dtype=np.float32))
    with TestClient(create_app(data_dir=data_dir)) as client:
        s = client.app.state.studio
        s.arrangement = replace(
            s.arrangement,
            drum_bars=2,
            drum_steps=16,
            drums=[["kick", 0], ["snare", 4], ["hat", 2], ["kick", 16], ["clap", 20]],
            drum_vel={"0": 0.5, "4": 1.0, "20": 0.25},
            drum_mix={
                "snare": {"mute": True},
                "hat": {"solo": True},
                "kick": {"level": 0.8},
            },
            drum_samples={"clap": "hit"},
            drum_hits={
                "kick.0": {"v": 0.6, "n": -0.25},
                "hat.2": {"p": 0.5, "r": 3},
                "kick.16": {"t": 5.0, "n": 0.1, "p": 0.0},
                "clap.20": {"r": 2, "t": -3.0},
            },
            drum_row_swing={"hat": 0.2},
            bars=2,
            steps_per_bar=8,
            cells=[["A1", 0], ["C2", 3], ["E2", 9]],
            slides=[3, 9],
            velocities={"0": 0.7, "9": 0.3},
            arp_mode="up",
        )
        drums = client.get("/view/drums").text
        perform = client.get("/view/perform").text
    return {
        "drum_bank.html": _slice(drums, '<section id="drum-bank"', "</section>"),
        "transport.html": _slice(perform, '<form id="transport"', "</form>"),
    }


def test_drums_and_perform_render_the_grids_byte_for_byte(tmp_path):
    for name, html in grid_regions(tmp_path).items():
        want = (GOLDEN / name).read_text(encoding="utf-8")
        assert html == want, f"{name} changed under the grid macros"


def test_the_goldens_cover_every_branch():
    bank = (GOLDEN / "drum_bank.html").read_text(encoding="utf-8")
    seq = (GOLDEN / "transport.html").read_text(encoding="utf-8")
    assert 'class="gridrow drumrow silent"' in bank  # mute / solo
    assert 'title="plays clip hit"' in bank and 'name="clip.clap"' in bank
    assert 'class="cell beat bar"' in bank  # bar 2
    # the step sequencer: each mark a step LED can carry, a row's own swing,
    # a level in a row head, the per-hit table and the hit menu
    for mark in (
        'data-n="-" style="--hv: 0.6"',
        'data-r="3" data-p="50"',
        'data-p="0" data-n="+" data-t="+"',
        'data-r="2" data-t="-"',
        'class="knob knob-s own" data-row="swing"',
        'data-row="level" min="0" max="1.5" step="0.01" value="0.8"',
        'name="hits" value="{&#34;',
        'name="rswing" value="{&#34;hat&#34;: 0.2}"',
        'id="hitmenu-drum-grid"',
        'data-hit-set="r=3"',
        'data-var="B"',
        'id="euclid-drum-grid-rim"',
    ):
        assert mark in bank, mark
    assert 'name="slide.3"' in seq and "checked" in seq
    assert 'value="0.3"' in seq


if __name__ == "__main__":  # regenerate the goldens (all, or `drums` / `notes`)
    import tempfile

    from synthlab.studio import security

    # the TestClient's host, as tests/conftest.py allows it under pytest
    security.LOCAL_HOSTS = security.LOCAL_HOSTS | {"testserver"}
    only = {"drums": "drum_bank.html", "notes": "transport.html"}.get(
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    GOLDEN.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        for name, html in grid_regions(Path(d)).items():
            if only in (None, name):
                (GOLDEN / name).write_text(html, encoding="utf-8", newline="\n")
                print(f"wrote {GOLDEN / name}", file=sys.stderr)
