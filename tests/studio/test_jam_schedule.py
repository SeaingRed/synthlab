"""The launcher's quantise maths, run under node from jam.js's own code
("a queue lands on the quantise point; a late buffer on the next").

`window.synthlab.jamSchedule` is the pure half of static/jam.js: where a
press lands (`point`), where a group whose buffers were not decoded in time
goes (`defer`), which bar of the log a start is (`barAt`) and the countdown
the bar shows (`beats`). A context is {t0, bar, q, ref}: the time of the
jam's bar 0 (null while nothing sounds), seconds a bar, the quantise (1, 4
or "end") and, for "end", the loop it waits for.

What this cannot hold: that Web Audio starts a source on the sample it was
given (the browser's), or how a launch sounds (nobody listened).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

JAM = Path("synthlab/studio/static/jam.js")

HARNESS = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: jam.js is a classic script (an IIFE merging onto
// window.synthlab). With no `document` it publishes the pure schedule and
// wires nothing, so the browser's own file runs here.
eval(fs.readFileSync(process.argv[2], "utf8"));
const J = window.synthlab.jamSchedule;
const calls = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(calls.map(([op, ...args]) => J[op](...args))));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run jam.js's own code"
)

BAR = 2.0  # 120 bpm


def run(calls, tmp_path):
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    (tmp_path / "calls.json").write_text(json.dumps(calls), encoding="utf-8")
    out = subprocess.run(
        ["node", str(tmp_path / "harness.js"), str(JAM), str(tmp_path / "calls.json")],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(out.stdout)


def ctx(q=1, t0=10.0, ref=None):
    return {"t0": t0, "bar": BAR, "q": q, "ref": ref}


def test_a_press_lands_on_the_next_bar_line(tmp_path):
    got = run(
        [
            ["point", 10.5, ctx()],
            ["point", 11.999, ctx()],
            ["point", 12.0, ctx()],  # on the line: this one, not the next
            ["point", 12.0000001, ctx()],  # float dust is still the line
            ["point", 12.01, ctx()],
            ["point", 9.0, ctx()],  # before bar 0 (a start still ahead): bar 0
        ],
        tmp_path,
    )
    assert got == pytest.approx([12.0, 12.0, 12.0, 12.0, 14.0, 10.0])


def test_four_bars_and_the_loop_end(tmp_path):
    loop = {"at": 14.0, "dur": 3 * BAR}  # a 3-bar loop launched at bar 2
    got = run(
        [
            ["point", 10.5, ctx(4)],
            ["point", 18.5, ctx(4)],
            ["point", 14.5, ctx("end", ref=loop)],
            ["point", 20.5, ctx("end", ref=loop)],
            # "loop end" with nothing looping on that track: the bar
            ["point", 14.5, ctx("end")],
        ],
        tmp_path,
    )
    assert got == pytest.approx([18.0, 26.0, 20.0, 26.0, 16.0])


def test_from_silence_there_is_no_grid_a_press_starts_now(tmp_path):
    got = run(
        [
            ["point", 3.21, ctx(4, t0=None)],
            ["defer", 3.21, ctx(4, t0=None)],  # and waits for its buffer where it is
        ],
        tmp_path,
    )
    assert got == pytest.approx([3.21, 3.21])


def test_a_late_buffer_lands_on_the_following_point(tmp_path):
    loop = {"at": 14.0, "dur": 3 * BAR}
    got = run(
        [
            ["defer", 12.0, ctx()],
            ["defer", 18.0, ctx(4)],
            ["defer", 20.0, ctx("end", ref=loop)],
        ],
        tmp_path,
    )
    assert got == pytest.approx([14.0, 26.0, 26.0])
    # ... which is a quantise point itself: deferring never leaves the grid
    ctxs = (ctx(), ctx(4), ctx("end", ref=loop))
    again = run([["point", t, c] for t, c in zip(got, ctxs, strict=True)], tmp_path)
    assert again == pytest.approx(got)


def test_every_point_is_a_whole_bar_of_the_log(tmp_path):
    calls = []
    for i in range(40):
        now = 10.0 + i * 0.37
        for q in (1, 4):
            calls.append(["point", now, ctx(q)])
    points = run(calls, tmp_path)
    bars = run([["barAt", at, ctx()] for at in points], tmp_path)
    for at, bar in zip(points, bars, strict=True):
        assert isinstance(bar, int) and at == pytest.approx(10.0 + bar * BAR)
    assert all(b % 4 == 0 for b in bars[1::2])  # the four-bar ones


def test_the_countdown_counts_beats(tmp_path):
    got = run(
        [
            ["beats", 10.0, 12.0, ctx()],
            ["beats", 11.4, 12.0, ctx()],
            ["beats", 11.6, 12.0, ctx()],
            ["beats", 12.0, 12.0, ctx()],
            ["beats", 12.3, 12.0, ctx()],  # already started: never negative
        ],
        tmp_path,
    )
    assert got == [4, 2, 1, 0, 0]
