"""A key never waits on a render it can stand in for.

keys.js keeps every key's last render after a patch change and re-warms
all visible keys; a press before the new render lands plays what it has —
the key's own older render, or the nearest warm key within two semitones,
repitched by playbackRate — then crossfades to the exact render at the
same point of the note. A held loop does the same on every patch change.

`pickKey` (which buffer) and `swapOffset` (where the new one picks up) are
pure and run here under node, from the shipped keys.js.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

KEYS = Path("synthlab/studio/static/keys.js")
PIANO = Path("synthlab/studio/static/piano.js")

HARNESS = """
globalThis.window = { synthlab: {} };
globalThis.document = { addEventListener() {} };
const fs = require("fs");
// eval, deliberately: keys.js is a classic script (an IIFE publishing onto
// window.synthlab), not a module. It is the first-party file the browser
// loads, run here against the stub window above rather than reimplemented.
eval(fs.readFileSync(process.argv[2], "utf8"));
const S = window.synthlab;
const cases = JSON.parse(process.argv[3]);
const out = cases.map((c) => {
  if (c.fn === "swap") return S.swapOffset(c.t, c.loop, c.duration);
  const store = new Map(c.entries.map(([k, pk]) => [k, { pk, name: k }]));
  const got = S.pickKey(store, c.midi, c.hold, c.pk, c.lane);
  return got && { name: got.entry.name, rate: got.rate, exact: got.exact };
});
process.stdout.write(JSON.stringify(out));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run keys.js's own code"
)


def run(cases, tmp_path):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(
        ["node", str(harness), str(KEYS), json.dumps(cases)],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    return json.loads(res.stdout)


def pick(tmp_path, entries, midi, hold=False, pk="new"):
    (got,) = run(
        [{"fn": "pick", "entries": entries, "midi": midi, "hold": hold, "pk": pk}],
        tmp_path,
    )
    return got


def test_a_current_render_plays_exactly(tmp_path):
    got = pick(tmp_path, [["60|0", "new"]], 60)
    assert got == {"name": "60|0", "rate": 1, "exact": True}


def test_mid_drag_the_old_render_plays_at_once_then_swaps(tmp_path):
    """A press during a slider drag: the key's own render from before the
    edit plays immediately (rate 1), flagged not exact so the new render is
    fetched and crossfaded in."""
    got = pick(tmp_path, [["60|0", "old"], ["62|0", "new"]], 60)
    assert got == {"name": "60|0", "rate": 1, "exact": False}


def test_a_cold_key_borrows_the_nearest_warm_key(tmp_path):
    got = pick(tmp_path, [["58|0", "new"], ["63|0", "new"]], 60)
    assert got["name"] == "58|0" and not got["exact"]
    assert got["rate"] == pytest.approx(2 ** (2 / 12))
    got = pick(tmp_path, [["61|0", "new"], ["59|0", "new"]], 60)
    assert got["rate"] in (pytest.approx(2 ** (-1 / 12)), pytest.approx(2 ** (1 / 12)))


def test_a_current_neighbour_beats_a_stale_nearer_one(tmp_path):
    got = pick(tmp_path, [["59|0", "old"], ["62|0", "new"]], 60)
    assert got["name"] == "62|0"
    assert got["rate"] == pytest.approx(2 ** (-2 / 12))


def test_no_stand_in_past_two_semitones_or_across_hold_kinds(tmp_path):
    assert pick(tmp_path, [["63|0", "new"], ["57|0", "new"]], 60) is None
    # a held key loops its own hold render: a one-shot can't stand in
    assert pick(tmp_path, [["60|0", "new"]], 60, hold=True) is None
    got = pick(tmp_path, [["61|1", "new"]], 60, hold=True)
    assert got["name"] == "61|1"


@pytest.mark.parametrize(
    ("t", "loop", "duration", "want"),
    [
        (0.05, None, 1.0, 0.05),  # one-shot: same point of the note
        (0.999, None, 1.0, -1),  # ran out: nothing to swap to
        (0.3, [0.5, 2.5], 4.0, 0.3),  # still in the attack/decay
        (1.2, [0.5, 2.5], 4.0, 1.2),  # inside the loop
        (3.1, [0.5, 2.5], 4.0, 1.1),  # wrapped once: back into the loop
        (7.4, [0.5, 2.5], 4.0, 1.4),  # wrapped three times
    ],
)
def test_swap_offset_continues_the_note(tmp_path, t, loop, duration, want):
    (got,) = run([{"fn": "swap", "t": t, "loop": loop, "duration": duration}], tmp_path)
    assert got == pytest.approx(want)


def test_swaps_crossfade_and_patch_changes_rewarm_every_key():
    js = KEYS.read_text(encoding="utf-8")
    xf = float(js.split("const XF = ")[1].split(";")[0])
    assert 0.03 <= xf <= 0.05  # 30-50 ms
    swap = js[js.index("function swap(") :]
    swap = swap[: swap.index("\n  }\n")]
    assert "linearRampToValueAtTime(1, now + XF)" in swap
    assert "linearRampToValueAtTime(0, now + XF)" in swap
    # every sounding key re-fetches on a patch change, so a held WAV loop
    # follows every parameter, not just the cutoff/level bridge
    set_patch = js[js.index("function setKeyPatch(") :]
    assert "ensure(h.midi, h.hold, h.lane)" in set_patch.split("\n  }\n")[0]
    # the warm-up is all visible keys, QWERTY octave (data-rel 0) first,
    # four at a time — the server renders four at once
    piano = PIANO.read_text(encoding="utf-8")
    assert "sort((a, b) => a.dataset.rel - b.dataset.rel)" in piano
    assert "worker(), worker(), worker(), worker()" in js
    # and it is no longer armed by hovering the keyboard
    assert "pointerover" not in piano.split("function warmKeys")[1].split("\n  }\n")[0]
    assert "armWarm" not in piano


def test_lanes_never_stand_in_for_each_other(tmp_path):
    """Pre-rendered lanes share the store under their own prefix: slot 2's
    renders never play for the key's own WAV or for slot 3, and a lane
    borrows only its own neighbours."""
    (own, other, main, near) = run(
        [
            {"fn": "pick", "entries": [["2:60|1", "k2"]], "midi": 60, "hold": True,
             "pk": "k2", "lane": 2},
            {"fn": "pick", "entries": [["2:60|1", "k2"]], "midi": 60, "hold": True,
             "pk": "k3", "lane": 3},
            {"fn": "pick", "entries": [["2:60|0", "k2"]], "midi": 60, "hold": False,
             "pk": "k2"},
            {"fn": "pick", "entries": [["62|0", "k2"], ["2:61|0", "k2"]], "midi": 60,
             "hold": False, "pk": "k2", "lane": 2},
        ],
        tmp_path,
    )  # fmt: skip
    assert own == {"name": "2:60|1", "rate": 1, "exact": True}
    assert other is None and main is None
    assert near["name"] == "2:61|0" and not near["exact"]
