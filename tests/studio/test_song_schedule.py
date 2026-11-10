"""The song player's schedule, run under node from song.js's own code.

song.js turns `/song/plan.json` into buffer starts: each section plays its
`url` once, then its `loop_url` for every later pass, back to back from
`start_sample`; "Loop section" repeats one section's `loop_url`. The pure
half (`window.synthlab.songSchedule`) is what the player steps through, one
pass at a time, so these tests pin the timeline the player queues:
repeats, looping, starting mid-section, the song stopping at its end, and
following a plan that changed under it (`remap`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SONG = Path("synthlab/studio/static/song.js")

HARNESS = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: song.js is a classic script (an IIFE merging onto
// window.synthlab), not a module. With no `document` it publishes the pure
// schedule and wires nothing, so the browser's own file runs here.
eval(fs.readFileSync(process.argv[2], "utf8"));
const S = window.synthlab.songSchedule;
const { op, args } = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
process.stdout.write(JSON.stringify(S[op](...args) ?? null));
"""

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run song.js's own code"
)

SR = 1000


def _section(sid, name, bars, repeats, cycle_bars, start_bar, start_sample, bar=500):
    cycles = bars // cycle_bars
    return {
        "section_id": sid, "name": name, "index": 0, "bars": bars,
        "repeats": repeats, "start_bar": start_bar, "cycle_bars": cycle_bars,
        "cycles": cycles, "passes": cycles * repeats,
        "cycle_samples": cycle_bars * bar, "cycle_seconds": cycle_bars * bar / SR,
        "start_sample": start_sample, "start_seconds": start_sample / SR,
        "url": f"/song/section/{sid}.wav?k=h{sid}",
        "loop_url": f"/song/section/{sid}.wav?loop=1&k=l{sid}",
    }  # fmt: skip


def _plan(*sections):
    at = bar = 0
    out = []
    for i, (sid, name, bars, repeats, cycle_bars) in enumerate(sections):
        s = _section(sid, name, bars, repeats, cycle_bars, bar, at)
        s["index"] = i
        out.append(s)
        at += s["cycle_samples"] * s["passes"]
        bar += bars * repeats
    return {
        "sr": SR, "bpm": 480.0, "gain": 0.8, "song_samples": at,
        "song_seconds": at / SR, "sections": out,
    }  # fmt: skip


# intro: 1 bar once; verse: 2 bars of a 1-bar cycle, twice (4 passes);
# chorus: 2 bars, a 2-bar cycle, once. A bar is 0.5 s.
PLAN = _plan(("a", "Intro", 1, 1, 1), ("b", "Verse", 2, 2, 1), ("c", "Chorus", 2, 1, 2))


def run(op, *args, tmp_path):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"op": op, "args": list(args)}), encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(SONG), str(spec)],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    return json.loads(out.stdout)


def _brief(entries):
    return [(e["sid"], e["pass"], e["url"].split("?")[1][:6], e["at"]) for e in entries]


def test_the_song_plays_each_section_head_then_its_loop_and_stops(tmp_path):
    got = run("schedule", PLAN, {"at": 10}, tmp_path=tmp_path)
    assert [(s, p, u[:6]) for s, p, u, _ in _brief(got)] == [
        ("a", 0, "k=ha"),
        ("b", 0, "k=hb"),  # the head folds the intro's tail
        ("b", 1, "loop=1"),
        ("b", 2, "loop=1"),  # repeats are more loop passes
        ("b", 3, "loop=1"),
        ("c", 0, "k=hc"),
    ]
    # back to back on the plan's own clock: start_sample / sr past `at`
    assert [e["at"] for e in got] == pytest.approx([10, 10.5, 11, 11.5, 12, 12.5])
    assert all(e["offset"] == 0 for e in got)
    assert got[-1]["dur"] == pytest.approx(1.0)  # the chorus's 2-bar cycle
    # and then nothing: the song stops at its end rather than wrapping
    assert got[-1]["at"] + got[-1]["dur"] == pytest.approx(10 + PLAN["song_seconds"])


def test_play_from_the_selected_section_starts_at_its_head(tmp_path):
    got = run("schedule", PLAN, {"sid": "c", "at": 3}, tmp_path=tmp_path)
    assert _brief(got) == [("c", 0, "k=hc", 3)]


def test_loop_section_repeats_its_loop_render_and_never_leaves(tmp_path):
    got = run(
        "schedule", PLAN, {"sid": "b", "loop": True, "at": 0, "horizon": 6},
        tmp_path=tmp_path,
    )  # fmt: skip
    assert {e["sid"] for e in got} == {"b"}
    # every pass is the loop render (it folds its own tail), even the first
    assert {e["url"] for e in got} == {PLAN["sections"][1]["loop_url"]}
    assert [e["at"] for e in got] == pytest.approx([0.5 * k for k in range(12)])
    # passes count round the section's repeats, for the bar readout
    assert [e["pass"] for e in got[:6]] == [0, 1, 2, 3, 0, 1]


def test_a_section_under_a_whole_stem_plays_a_wav_a_pass(tmp_path):
    """Long audio: the plan gives such a section `pass_urls`, and each pass
    plays its own (the stem's next slice) — but a loop still plays the loop
    render, the first pass's slice, every time round."""
    plan = json.loads(json.dumps(PLAN))
    verse = plan["sections"][1]
    verse["pass_urls"] = [verse["url"]] + [
        f"/song/section/b.wav?p={j}&k=p{j}" for j in range(1, 4)
    ]
    got = run("schedule", plan, {"at": 0}, tmp_path=tmp_path)
    assert [(s, p, u[:6]) for s, p, u, _ in _brief(got)] == [
        ("a", 0, "k=ha"),
        ("b", 0, "k=hb"),
        ("b", 1, "p=1&k="),
        ("b", 2, "p=2&k="),
        ("b", 3, "p=3&k="),
        ("c", 0, "k=hc"),
    ]
    # `own` is what keeps the player's "replay the slot's last buffer"
    # fallback per pass: the wrong slice of a vocal is worse than a late one
    assert [e["own"] for e in got] == [False, True, True, True, True, False]
    # started mid-section, the pass it lands in plays its own slice
    got = run("schedule", plan, {"sid": "b", "from": 650, "at": 2}, tmp_path=tmp_path)
    assert "p=1" in got[0]["url"]
    looped = run(
        "schedule", plan, {"sid": "b", "loop": True, "at": 0, "horizon": 3},
        tmp_path=tmp_path,
    )  # fmt: skip
    assert {e["url"] for e in looped} == {verse["loop_url"]}
    assert not any(e["own"] for e in looped)


def test_starting_mid_section_offsets_into_the_right_pass(tmp_path):
    # 1.3 bars into the verse: pass 1, 0.3 bar (0.15 s) into its buffer
    got = run("schedule", PLAN, {"sid": "b", "from": 650, "at": 2}, tmp_path=tmp_path)
    first, second = got[0], got[1]
    assert (first["sid"], first["pass"], first["at"]) == ("b", 1, 2)
    assert first["offset"] == pytest.approx(0.15)
    assert "loop=1" in first["url"]
    # the next pass lands where the first one's remainder ends
    assert (second["pass"], second["at"]) == (2, pytest.approx(2.35))
    assert got[-1]["sid"] == "c"
    # a position past the section runs on into the next one
    got = run("schedule", PLAN, {"sid": "b", "from": 2100, "at": 0}, tmp_path=tmp_path)
    assert (got[0]["sid"], got[0]["offset"]) == ("c", pytest.approx(0.1))
    # and past the end of the song there is nothing to play
    assert run("schedule", PLAN, {"from": 99999}, tmp_path=tmp_path) == []
    # a loop start wraps inside its own section instead
    got = run(
        "schedule", PLAN, {"sid": "b", "from": 2100, "loop": True, "horizon": 1},
        tmp_path=tmp_path,
    )  # fmt: skip
    assert (got[0]["sid"], got[0]["pass"]) == ("b", 0)
    assert got[0]["offset"] == pytest.approx(0.1)


def test_an_empty_song_or_a_missing_section_schedules_safely(tmp_path):
    assert run("schedule", _plan(), {"at": 0}, tmp_path=tmp_path) == []
    # an unknown section id plays from the top rather than failing
    got = run("schedule", PLAN, {"sid": "zz", "at": 0}, tmp_path=tmp_path)
    assert got[0]["sid"] == "a"


def test_locate_reads_bar_and_section_off_the_sounding_pass(tmp_path):
    entries = run("schedule", PLAN, {"at": 0}, tmp_path=tmp_path)
    verse2 = entries[3]  # the verse's third pass: its second repeat, bar 1
    got = run("locate", PLAN, verse2, verse2["at"] + 0.25, tmp_path=tmp_path)
    assert got["name"] == "Verse" and got["sid"] == "b"
    # song bars are 1-based: intro is bar 1, the verse's four bars 2..5
    assert (got["bar"], got["total"]) == (4, 7)
    # the playhead's place along the section's column (one repeat of bars)
    assert got["frac"] == pytest.approx(0.25)
    chorus = entries[5]
    got = run("locate", PLAN, chorus, chorus["at"] + 0.75, tmp_path=tmp_path)
    assert (got["bar"], got["frac"]) == (7, pytest.approx(0.75))
    # a mid-pass start counts its offset
    first = run("schedule", PLAN, {"sid": "b", "from": 650}, tmp_path=tmp_path)[0]
    got = run("locate", PLAN, first, first["at"], tmp_path=tmp_path)
    assert got["bar"] == 3 and got["frac"] == pytest.approx(1.3 / 2)


def test_locate_at_runs_on_through_the_passes_of_a_repeated_section(tmp_path):
    """The playlist draws a section as wide as bars x repeats, so the
    playhead needs bars into the section across ALL its passes: one sweep
    through the echoes, where `frac` starts again every repeat."""
    entries = run("schedule", PLAN, {"at": 0}, tmp_path=tmp_path)
    ats = []
    for e in entries[1:5]:  # the verse: 2 bars twice, a 1-bar cycle (4 passes)
        got = run("locate", PLAN, e, e["at"] + 0.25, tmp_path=tmp_path)
        ats.append((got["at"], got["frac"]))
    assert [a for a, _ in ats] == pytest.approx([0.5, 1.5, 2.5, 3.5])
    assert [f for _, f in ats] == pytest.approx([0.25, 0.75, 0.25, 0.75])
    # the chorus is one 2-bar pass: `at` and `pos` agree there
    got = run("locate", PLAN, entries[5], entries[5]["at"] + 0.75, tmp_path=tmp_path)
    assert got["at"] == pytest.approx(1.5) and got["pos"] == pytest.approx(1.5)
    # a mid-pass start (from a marker) counts its offset
    first = run("schedule", PLAN, {"sid": "b", "from": 1650}, tmp_path=tmp_path)[0]
    got = run("locate", PLAN, first, first["at"], tmp_path=tmp_path)
    assert first["pass"] == 3 and got["at"] == pytest.approx(3.3)
    assert got["bar"] == 5


def test_locate_at_wraps_with_a_looped_section(tmp_path):
    """⟳ Loop walks the section's passes round and round: `at` comes back
    to 0 with them and never runs past the section's width."""
    loop = run(
        "schedule", PLAN, {"sid": "b", "loop": True, "horizon": 3.2}, tmp_path=tmp_path
    )
    assert [e["pass"] for e in loop] == [0, 1, 2, 3, 0, 1, 2]
    got = [run("locate", PLAN, e, e["at"] + 0.1, tmp_path=tmp_path)["at"] for e in loop]
    assert got == pytest.approx([0.2, 1.2, 2.2, 3.2, 0.2, 1.2, 2.2])


def test_stop_at_the_end_advance_returns_nothing(tmp_path):
    last = {"sid": "c", "i": 2, "pass": 0, "at": 5, "offset": 0}
    assert run("advance", PLAN, last, False, tmp_path=tmp_path) is None
    # in loop mode the same cursor wraps to its own section's next pass
    got = run("advance", PLAN, last, True, tmp_path=tmp_path)
    assert (got["sid"], got["pass"], got["at"]) == ("c", 0, 6)


def test_remap_follows_an_edited_plan_by_section_id(tmp_path):
    cur = {"sid": "b", "i": 1, "pass": 3, "at": 7, "offset": 0}
    # the verse lost a repeat: pass 3 no longer exists, play on into the chorus
    fewer = _plan(
        ("a", "Intro", 1, 1, 1), ("b", "Verse", 2, 1, 1), ("c", "Chorus", 2, 1, 2)
    )
    got = run("remap", fewer, cur, False, tmp_path=tmp_path)
    assert (got["sid"], got["pass"], got["at"]) == ("c", 0, 7)
    # ...while a loop wraps inside the verse
    got = run("remap", fewer, cur, True, tmp_path=tmp_path)
    assert (got["sid"], got["pass"]) == ("b", 1)
    # a section moved: found by id, at its new index
    moved = _plan(
        ("b", "Verse", 2, 2, 1), ("a", "Intro", 1, 1, 1), ("c", "Chorus", 2, 1, 2)
    )
    got = run("remap", moved, cur, False, tmp_path=tmp_path)
    assert (got["sid"], got["i"], got["pass"]) == ("b", 0, 3)
    # a deleted section hands over to whatever now sits at its index
    gone = _plan(("a", "Intro", 1, 1, 1), ("c", "Chorus", 2, 1, 2))
    got = run("remap", gone, cur, False, tmp_path=tmp_path)
    assert (got["sid"], got["pass"]) == ("c", 0)
    # and an emptied song ends playback
    assert run("remap", _plan(), cur, False, tmp_path=tmp_path) is None
