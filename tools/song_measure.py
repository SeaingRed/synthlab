"""Measure what a full song costs on this machine: time to the plan (when a
song can start playing), to the export, and the process's peak memory.

    uv run python tools/song_measure.py                    # every starter, cold
    uv run python tools/song_measure.py --style trance     # one starter
    uv run python tools/song_measure.py --worst 480        # the cap's worst case
    uv run python tools/song_measure.py --worst 480 --synths 8 --loudness streaming

Each measurement runs in a **child process** with an empty data dir, so
every cache is cold and the peak memory printed is that run's own
(`PeakWorkingSetSize` on Windows, `ru_maxrss` elsewhere).

`--style`: the starter the Song page's Start button lays down
(`studio/gen/build.py`), for `--seed` (default 1): how long the pure
build takes, how long until the first section's audio exists (when the
page could play), until the whole plan exists (when it does play: the
song's one gain needs every pass), and the export after it.

`--worst SECONDS`: the song the length cap was sized by — `--synths`
synth tracks of the heaviest recipe sounds (supersaw pads into long
rooms, pumped basses, plucks into dotted echoes) and a drums track, every
section its own random patterns, nothing shared, so nothing is reused.
With `--loudness streaming` the export is mastered to -14 LUFS: the run
whose peak memory decides `limits.MAX_SONG_SECONDS` (2.0 GB or under at
480 s, else the cap is 420).

Nothing here listens: these are clocks and byte counts.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def peak_rss_mb() -> float:
    """This process's peak resident memory, MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.PeakWorkingSetSize / 2**20
    import resource

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb / (2**20 if sys.platform == "darwin" else 2**10)


WORST_SOUNDS = (
    "Anthem Supersaw",
    "Long Cinema Pad",
    "Rolling Mid Bass",
    "Dotted Pluck",
    "Stack Pad",
    "Rolling Sub",
    "Evolving Pluck",
    "Gated Pad",
)


def worst_song(seconds: float, synths: int, seed: int = 7) -> dict:
    """`seconds` of song at 128 bpm: a drums track and `synths` synth
    tracks, 16-bar sections, every section its own random 4-bar patterns
    (two tracks share theirs: the pool holds 128)."""
    from synthlab.studio import recipes, schema
    from synthlab.studio import song_model as sm
    from synthlab.studio.state import DRUM_ROWS

    rng = random.Random(seed)
    bpm = 128.0
    song = sm.new_song(bpm, "A1", "minor")
    bar = 240.0 / bpm
    count = max(1, int(seconds // (16 * bar)))
    drums = sm.add_track(song, "drums", "Drums")
    tids = []
    for i in range(synths):
        entry = recipes.get_recipe(WORST_SOUNDS[i % len(WORST_SOUNDS)])
        tids.append(
            sm.add_track(
                song,
                "synth",
                f"{entry['name']} {i + 1}",
                patch=entry["patch"],
                fx=entry["main_fx"],
            )
        )
    ladder = [m for m in sm._ladder(song) if 33 <= m <= 81]
    shared: dict[str, str] = {}
    for n in range(count):
        sid = sm.add_section(song, f"S{n + 1}", 16)
        hits = [
            [rng.choice(DRUM_ROWS[:5]), s] for s in range(64) if rng.random() < 0.45
        ] + [["kick", s] for s in range(0, 64, 4)]
        pid = sm.add_pattern(
            song, "drums", f"beat {n}", {"bars": 4, "steps": 16, "drums": hits}
        )
        sm.set_cell(song, sid, drums, pid)
        for j, tid in enumerate(tids):
            if j >= 6 and tid in shared:
                sm.set_cell(song, sid, tid, shared[tid])
                continue
            cells = []
            for s in range(64):
                if rng.random() < 0.4:
                    cells.append([sm.name_of(rng.choice(ladder)), s])
            pid = sm.add_pattern(
                song,
                "notes",
                f"riff {n}.{j}",
                {"bars": 4, "steps_per_bar": 16, "cells": cells},
            )
            sm.set_cell(song, sid, tid, pid)
            if j >= 6:
                shared[tid] = pid
    return schema.conform(song, schema.nodes().SONG)


def child(args) -> dict:
    """One cold measurement, in this process."""
    from synthlab.audio import encode
    from synthlab.studio import limits, song_play
    from synthlab.studio import song_model as sm
    from synthlab.studio.routes.export import _mastered
    from synthlab.studio.server import create_app

    out: dict = {"idle_mb": round(peak_rss_mb())}
    with tempfile.TemporaryDirectory() as tmp:
        svc = create_app(data_dir=Path(tmp)).state.svc
        t0 = time.perf_counter()
        if args.worst:
            limits.MAX_SONG_SECONDS = max(limits.MAX_SONG_SECONDS, args.worst + 1)
            song = worst_song(args.worst, args.synths, args.seed)
        else:
            from synthlab.studio.gen import build

            song = build.start(args.style, args.seed).song
        out["build_s"] = round(time.perf_counter() - t0, 3)
        svc.state.song = song
        song, master = svc.song_snapshot()
        out |= {
            "sections": len(song["sections"]),
            "tracks": len(song["tracks"]),
            "synths": sum(t["kind"] == "synth" for t in song["tracks"]),
            "bars": sm.song_bars(song),
            "seconds": round(sm.song_seconds(song), 1),
            "bpm": song["bpm"],
        }
        t0 = time.perf_counter()
        svc.section_result(song, 0, gain=1.0, master_fx=master)
        out["first_section_s"] = round(time.perf_counter() - t0, 2)
        plan = svc.song_plan(song, master)
        out["plan_s"] = round(time.perf_counter() - t0, 2)
        out["gain"] = round(plan["gain"], 3)
        out["plan_mb"] = round(peak_rss_mb())
        t1 = time.perf_counter()
        if args.loudness == "peak":
            audio = svc.song_mixdown(song, master)
            report = song_play.loudness_report(audio) if args.report else {}
        else:
            from synthlab.studio.events import QUIET

            audio, report = _mastered(svc, song, master, args.loudness, QUIET)
        wav = encode(audio, "wav")
        out["export_s"] = round(time.perf_counter() - t1, 2)
        out["wav_mb"] = round(len(wav) / 2**20, 1)
        out["peak"] = round(float(max(audio.max(), -audio.min())), 4)
        out["report"] = report
        out["peak_mb"] = round(peak_rss_mb())
    return out


def run_child(extra: list[str]) -> dict:
    cmd = [sys.executable, __file__, "--child", *extra]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if done.returncode:
        raise SystemExit(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--style", help="one starter style (default: all four)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--worst", type=float, default=0.0, help="seconds of worst case")
    ap.add_argument("--synths", type=int, default=8)
    ap.add_argument("--loudness", default="peak", choices=("peak", "streaming", "club"))
    ap.add_argument("--report", action="store_true", help="measure the peak export too")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        print(json.dumps(child(args)))
        return
    common = ["--seed", str(args.seed), "--loudness", args.loudness]
    common += ["--report"] if args.report else []
    if args.worst:
        runs = [
            ("worst case", ["--worst", str(args.worst), "--synths", str(args.synths)])
        ]
    else:
        from synthlab.studio.gen import styles

        names = [args.style] if args.style else list(styles.STYLES)
        runs = [(n, ["--style", n]) for n in names]
    for name, extra in runs:
        r = run_child(extra + common)
        print(
            f"{name}: {r['sections']} sections, {r['bars']} bars, {r['seconds']} s, "
            f"{r['synths']} synths at {r['bpm']:g} bpm"
        )
        print(
            f"  build {r['build_s']} s · first section {r['first_section_s']} s · "
            f"plan {r['plan_s']} s (gain {r['gain']}, {r['plan_mb']} MB)"
        )
        print(
            f"  export [{args.loudness}] {r['export_s']} s, {r['wav_mb']} MB WAV, "
            f"peak {r['peak']} · peak RSS {r['peak_mb']} MB (idle {r['idle_mb']})"
        )
        if r["report"]:
            print(f"  measures {r['report']}")


if __name__ == "__main__":
    main()
