"""Measure the Sample Lab on this machine: analysis accuracy, separation
time and memory, and a sanity check of what the stems hold.

    uv run python tools/lab_measure.py                 # a synthetic song
    uv run python tools/lab_measure.py --audio <dir>   # + a real one
    uv run python tools/lab_measure.py --model 6       # the 6-stem model
    uv run python tools/lab_measure.py --drums         # drum reading only

The synthetic song is rendered by the engine (drums, bass, keys and a
formant "voice", in sections), so the true stems are known — which makes
the numbers a check that the pipeline is wired right, **not** a measure of
quality on real records: a synth voice is not a singer and nothing in a
rendered mix is as entangled as a recording.

`--audio <dir>` takes a folder holding a song and, optionally, the same
song's instrumental ("NoVox" in its name, sample-aligned): full minus
instrumental is then a rough vocal reference (two lossy files: the
difference carries codec error). It also pushes regions through the
studio's own routes: a clip, sampler sounds, and what the stems become — the key read
again with the bass line, the vocal as an instrument and as chops, the
drums as a pattern, a kit and a take on the song. On a real record none of
that has a ground truth: what is printed is what was found, to be read and
listened to, not a score.

`--drums` (no model needed) prints the drum reading's precision and recall
per lane against patterns the studio's own drum machine rendered: the one
place it can be scored.

The model comes from `SYNTHLAB_MODEL_DIR` / the user cache and is
downloaded if missing (316 MB). Separation runs in a child process through
the Lab's own job (`studio/labjobs.py`), so the peak memory printed is the
job's, not this script's rendering.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from synthlab import Song, Synth, preset, sampler, stems
from synthlab.analysis import detect_key, drumhits, tempo
from synthlab.audio import wav_bytes
from synthlab.core import SR
from synthlab.sequencer import chord
from synthlab.studio import lab, sources
from synthlab.studio.labjobs import LabJobs

BPM, KEY = 124, "A minor"
PROG = [("A3", "min"), ("D3", "min"), ("E3", "min"), ("A3", "min")]
ROOTS = ["A2", "D3", "E3", "A2"]
# (bars, parts playing): an intro, verses with the voice, a break, an outro
SECTIONS = [
    (8, {"drums", "bass"}),
    (16, {"drums", "bass", "other"}),
    (16, {"drums", "bass", "other", "vocals"}),
    (8, {"other", "vocals"}),
    (16, {"drums", "bass", "other", "vocals"}),
    (16, {"drums", "bass", "other"}),
    (16, {"drums", "bass", "other", "vocals"}),
    (8, {"drums", "other"}),
]


def synthetic() -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(mix, true stems), peak-normalised together."""
    song = Song(bpm=BPM)
    bass, keys = Synth(preset("bass_reese")), Synth(preset("keys_ep"))
    voice = Synth(
        preset("lead_supersaw").tweak(
            unison=1,
            formant_amount=0.9,
            formant_vowel=0.1,
            vibrato_depth=0.25,
            attack=0.06,
            release=0.2,
        )
    )
    bar = 0
    for bars, parts in SECTIONS:
        for b in range(bar, bar + bars):
            at, root, (name, quality) = b * 4, ROOTS[b % 4], PROG[b % 4]
            if "drums" in parts:
                song.drum("drums", "kick", "x...x...x...x...", start_beat=at)
                song.drum("drums", "snare", "....x.......x...", start_beat=at, gain=0.7)
                song.drum("drums", "hat", "..x.x.x...x.x.x.", start_beat=at, gain=0.4)
            if "bass" in parts:
                line = [(root, 1.0), (root, 0.5), (root, 0.5), (root, 1.0), (root, 1.0)]
                song.notes("bass", bass, line, start_beat=at, gain=0.8)
            if "other" in parts:
                stab = chord(name, quality, octave_shift=1)
                song.notes("other", keys, [(stab, 2.0)] * 2, start_beat=at, gain=0.6)
            if "vocals" in parts:
                t = chord(name, quality, octave_shift=2)
                line = [(t[b % 3], 1.5), (t[(b + 1) % 3], 0.5), (None, 0.5)]
                line += [(t[(b + 2) % 3], 1.5)]
                song.notes("vocals", voice, line, start_beat=at, gain=0.5)
        bar += bars
    n = int(bar * 4 * 60.0 / BPM * SR)
    truth = {}
    for name, track in song.tracks.items():
        buf = track.render(n, SR)[:n]
        truth[name] = np.concatenate([buf, np.zeros((n - len(buf), 2))])
    mix = np.sum(list(truth.values()), axis=0)
    peak = float(np.max(np.abs(mix))) / 0.9
    return mix / peak, {k: v / peak for k, v in truth.items()}


def peak_rss_mb() -> float:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (n, ctypes.c_size_t)
                for n in ("peak", "now", "qpp", "qp", "qpn", "qn", "page", "peakpage")
            ]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
        return c.peak / 1e6
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1e6 if sys.platform == "darwin" else 1e3)


def child(data_dir: str, sid: str, model_id: str) -> None:
    """Run the Lab's own job on one source; print its cost as JSON."""
    jobs = LabJobs(Path(data_dir))
    base = peak_rss_mb()
    start = time.perf_counter()
    assert jobs.start(sid, model_id) == "", "the job did not start"
    jobs.join(3600)
    out = {
        "seconds": round(time.perf_counter() - start, 1),
        "peak_rss_mb": round(peak_rss_mb()),
        "rss_before_mb": round(base),
        "error": jobs.last["error"] if jobs.last else "never ended",
        "threads": stems.threads(),
        "provider": "CPUExecutionProvider",
        "available": stems.runtime().get_available_providers(),
    }
    print("RESULT " + json.dumps(out))


MODEL = stems.HTDEMUCS  # --model picks


def separate(data_dir: Path, sid: str) -> dict:
    run = subprocess.run(
        [sys.executable, __file__, "--child", str(data_dir), sid, MODEL.id],
        capture_output=True,
        text=True,
        check=False,
    )
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), "")
    if not line:
        raise SystemExit(f"separation failed:\n{run.stdout}\n{run.stderr}")
    return json.loads(line[7:])


def compare(name: str, ref: np.ndarray, est: np.ndarray) -> str:
    n = min(len(ref), len(est))
    r, e = ref[:n].mean(axis=1), est[:n].mean(axis=1)
    corr = float(np.corrcoef(r, e)[0, 1])
    # the 10 ms level envelopes: what survives when two files are the same
    # performance but not the same samples (another mixdown or master)
    m = n // 441 * 441
    env = float(
        np.corrcoef(
            np.abs(r[:m]).reshape(-1, 441).mean(axis=1),
            np.abs(e[:m]).reshape(-1, 441).mean(axis=1),
        )[0, 1]
    )
    return (
        f"  {name:<34} SDR {stems.sdr(ref[:n], est[:n]):6.2f} dB   corr {corr:.3f}"
        f"   envelope corr {env:.3f}"
    )


def report_synthetic(data_dir: Path) -> None:
    print(f"\n== synthetic song: {BPM} bpm, {KEY} ==")
    t = time.perf_counter()
    mix, truth = synthetic()
    seconds = len(mix) / SR
    print(f"rendered {seconds:.0f} s in {time.perf_counter() - t:.1f} s")
    meta = sources.add(data_dir, wav_bytes(mix), "synthetic.wav", analyse=lab.analyse)
    an = meta["analysis"]
    print(
        f"tempo {an['bpm']} (conf {an['confidence']}), key {an['key']}"
        f" (conf {an['key_confidence']}), downbeat at {an['downbeat_offset']} s"
    )
    cost = separate(data_dir, meta["id"])
    print(
        f"separation: {cost['seconds']} s wall ({seconds / cost['seconds']:.1f}x real"
        f" time), peak RSS {cost['peak_rss_mb']} MB, {cost['threads']} threads,"
        f" {cost['provider']} (available: {cost['available']})"
    )
    meta = sources.read_meta(data_dir, meta["id"])
    print("each stem against every true part (correlation; SDR on the diagonal):")
    for stem in MODEL.sources:
        est = sources.read(data_dir, meta, stem)
        if not np.any(est):
            print(f"  {stem:<7} silent")
            continue
        row = {
            k: abs(float(np.corrcoef(est.mean(axis=1), v.mean(axis=1))[0, 1]))
            for k, v in truth.items()
        }
        best = max(row, key=row.get)
        cells = "  ".join(f"{k} {v:.2f}" for k, v in row.items())
        sdr = f"{stems.sdr(truth[stem], est):6.2f} dB" if stem in truth else "   n/a   "
        print(f"  {stem:<7} best={best:<7} SDR {sdr}   {cells}")


def offset_of(a: np.ndarray, b: np.ndarray, reach: float = 2.0) -> int:
    """How many samples `b` lags `a` (negative: it leads), by the peak of
    their cross-correlation over 30 s from the middle, within `reach` s.
    Two encodes of one song rarely start on the same sample."""
    n, mid = int(30 * SR), min(len(a), len(b)) // 2
    x, y = a[mid : mid + n].mean(axis=1), b[mid : mid + n].mean(axis=1)
    size = 1 << int(np.ceil(np.log2(2 * n)))
    cc = np.fft.irfft(np.fft.rfft(y, size) * np.conj(np.fft.rfft(x, size)))
    r = int(reach * SR)
    lags = np.concatenate([np.arange(0, r), np.arange(-r, 0)])
    return int(lags[np.argmax(np.concatenate([cc[:r], cc[-r:]]))])


def report_real(data_dir: Path, folder: Path) -> None:
    import soundfile as sf

    files = sorted(
        p
        for p in folder.iterdir()
        if p.suffix.lower() in (".mp3", ".flac", ".wav", ".ogg")
    )
    full = next(p for p in files if "novox" not in p.name.lower())
    inst = next((p for p in files if "novox" in p.name.lower()), None)
    print(f"\n== real song: {full.name} ==")
    t = time.perf_counter()
    meta = sources.add(data_dir, full.read_bytes(), full.name, analyse=lab.analyse)
    an = meta["analysis"]
    print(f"imported {meta['seconds']:.1f} s in {time.perf_counter() - t:.1f} s")
    print(
        f"tempo {an['bpm']} (conf {an['confidence']}); key {an['key']}"
        f" (conf {an['key_confidence']}); candidates {an['key_scores']}"
    )
    cost = separate(data_dir, meta["id"])
    print(
        f"separation: {cost['seconds']} s wall"
        f" ({meta['seconds'] / cost['seconds']:.1f}x real time),"
        f" peak RSS {cost['peak_rss_mb']} MB, {cost['threads']} threads"
    )
    meta = sources.read_meta(data_dir, meta["id"])
    now = meta["analysis"]
    print(
        f"key from the mix: {an['key']} (conf {an['key_confidence']}, lead over"
        f" its relative {an['key_margin']:+.3f}); after the stems: {now['key']}"
        f" (conf {now['key_confidence']}, lead {now['key_margin']:+.3f}, read"
        f" from {now['key_from']}, tonic by {now['key_tonic'] or 'the chroma'});"
        f" candidates {now['key_scores']}"
    )
    got = {s: sources.read(data_dir, meta, s) for s in MODEL.sources}
    level = float(np.sqrt(np.mean(sources.read(data_dir, meta) ** 2)))
    print(
        "stem levels against the mix (dB): "
        + ", ".join(
            f"{k} {20 * np.log10(max(np.sqrt(np.mean(v**2)), 1e-9) / level):+.1f}"
            for k, v in got.items()
        )
    )
    if inst is not None:
        a, _ = sf.read(str(full), dtype="float32", always_2d=True)
        b, _ = sf.read(str(inst), dtype="float32", always_2d=True)
        lag = offset_of(a, b)
        print(
            f"the instrumental file sits {lag} samples ({lag / SR * 1000:.1f} ms)"
            " after the full one; aligned before comparing"
        )
        b = b[lag:] if lag >= 0 else np.concatenate([np.zeros((-lag, 2), "f4"), b])
        n = min(len(a), len(b))
        scale = max(1.0, float(np.max(np.abs(a))))  # the store's peak rule
        a, b = a[:n] / scale, b[:n] / scale
        got = {k: v[:n] for k, v in got.items()}
        print("against the instrumental file (both MP3: a rough reference):")
        print(compare("vocals vs (full - instrumental)", a - b, got["vocals"]))
        backing = got["drums"] + got["bass"] + got["other"]
        print(compare("drums+bass+other vs instrumental", b, backing))
        print(compare("(the mix itself vs instrumental)", b, a))
        print(compare("(vocals stem vs instrumental)", b, got["vocals"]))
        tk = tempo(b)
        print(f"instrumental file alone: tempo {tk.bpm}, key {detect_key(b).name}")
    flow(data_dir, meta["id"])


def flow(data_dir: Path, sid: str) -> None:
    """Three regions through the studio's own routes."""
    from fastapi.testclient import TestClient

    from synthlab.studio import security
    from synthlab.studio.clips import list_clips
    from synthlab.studio.server import create_app

    security.LOCAL_HOSTS = security.LOCAL_HOSTS | {"testserver"}
    client = TestClient(create_app(data_dir=data_dir))
    state = client.app.state.svc.state
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/lab"}

    def post(path, **data):
        r = client.post(path, data={"source": sid, **data}, headers=hx)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        return r

    print(f"flow (session at {state.arrangement.bpm:g} bpm):")
    post("/lab/region", mode="bars", bar="17", bars="4")
    r = post("/lab/send", target="clip", fit="on", name="measure mix")
    clip = next(
        c for c in list_clips(data_dir) if c["name"] == r.headers["X-Clip-Name"]
    )
    print(
        f"  4 bars of the mix -> clip: {clip['seconds']} s, bpm {clip.get('bpm')},"
        f" bars {clip.get('bars')}, fitted {clip['lab']['fitted']}"
        f" (4 bars at the session tempo = {16 * 60 / state.arrangement.bpm:.2f} s)"
    )
    post("/lab/region", stems="1", **{"stem.vocals": "on"})
    post("/lab/region", mode="bars", bar="21", bars="2")
    post("/lab/send", target="sampler", name="measure vocal")
    p = state.patch
    midi, conf = sampler.sample_pitch(p.sampler_sample)
    secs = sampler.seconds(p.sampler_sample)
    print(
        f"  2 bars of the vocal stem -> sampler: root {p.sampler_root},"
        f" detected {midi:.2f} (conf {conf:.2f}), {secs:.2f} s"
    )
    post("/lab/region", stems="1", **{"stem.drums": "on"})
    for mode in ("transients", "beats"):
        post("/lab/send", target="sampler", name=f"measure drums {mode}", chop=mode)
        n = len(sampler.parse_slices(state.patch.sampler_slices))
        print(f"  2 bars of the drum stem -> sampler, chop {mode}: {n} slices")
    made(client, post, state, data_dir)


def said(r) -> str:
    """What a Lab action reported: its toast, or the line it left on the page."""
    import html
    import re

    trig = json.loads(r.headers.get("HX-Trigger") or "{}")
    if "studio-toast" in trig:
        return trig["studio-toast"]["message"]
    m = re.search(r'<div class="lab-said"[^>]*>(.*?)</div>', r.text, re.S)
    text = re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""
    return " ".join(html.unescape(text).split())


def made(client, post, state, data_dir: Path) -> None:
    """What the stems become, through the routes: the vocal, the drums, the song. No
    ground truth on a record — what is printed is what was found."""
    from synthlab.sequencer import name_of
    from synthlab.studio.clips import list_clips

    print("the vocal stem as an instrument (regions of 4 and 8 bars):")
    for bar, bars in ((1, 4), (17, 4), (33, 8), (49, 4)):
        post("/lab/region", mode="bars", bar=str(bar), bars=str(bars))
        r = post("/lab/vocal", what="instrument")
        p = state.patch
        total = sampler.seconds(p.sampler_sample)
        loop = (p.sampler_loop_end - p.sampler_loop_start) * total
        print(
            f"  bars {bar}-{bar + bars - 1}: root {name_of(p.sampler_root)},"
            f" {p.sampler_mode}, loop {loop * 1000:.0f} ms, detune"
            f" {p.osc1_detune:+.1f} c | {said(r)}"
        )
        r = post("/lab/vocal", what="chops")
        print(f"     chops: {len(sampler.parse_slices(state.patch.sampler_slices))}")
    print("the drum stem as a pattern (4-bar regions; hits a bar, by lane):")
    for bar in (9, 25, 33, 41, 57):
        post("/lab/region", mode="bars", bar=str(bar), bars="4")
        post("/lab/drums", to="grid")
        arr = state.arrangement
        rows = {}
        for lane in drumhits.LANES:
            on = {s for v, s in arr.drums if v == lane}
            if on:
                rows[lane] = "".join("x" if i in on else "." for i in range(64))
        per_bar = [sum(1 for _v, s in arr.drums if s // 16 == b) for b in range(4)]
        print(
            f"  bars {bar}-{bar + 3}: {per_bar} hits a bar, swing"
            f" {arr.drum_swing:g}, {len(arr.drum_vel)} steps with a velocity"
        )
        for lane, row in rows.items():
            print(
                f"     {lane:<5} " + " ".join(row[i : i + 16] for i in (0, 16, 32, 48))
            )
        snare = {s % 16 for v, s in arr.drums if v == "snare"}
        print(
            "     snare on beats 2 and 4 (steps 4, 12) in every bar: "
            + str(
                all(
                    {4, 12}
                    <= {s % 16 for v, s in arr.drums if v == "snare" and s // 16 == b}
                    for b in range(4)
                )
            )
            + f"; snare steps used: {sorted(snare)}"
        )
    r = post("/lab/drums", to="pool")
    print(f"  pool: {said(r)[:110]}")
    r = post("/lab/drums", to="kit")
    kit = state.arrangement.drum_samples
    lengths = {c["name"]: c["seconds"] for c in list_clips(data_dir)}
    print(
        "  kit: " + ", ".join(f"{k} {lengths.get(v, 0):.2f} s" for k, v in kit.items())
    )
    song = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/song"}
    client.post("/song/section/add", data={"name": "Verse", "bars": "8"}, headers=song)
    section = state.song["sections"][-1]
    post("/lab/region", mode="bars", bar="33", bars="4")
    r = post("/lab/place", section_id=section["id"], at_bar="1", name="measure take")
    print(f"  onto the song ({state.song['bpm']:g} bpm): {said(r)}")


# --- the drum reading against a known pattern ------------------------------------

DRUM_PATTERNS = {
    "backbeat": {
        "kick": "x.....x...x.....",
        "snare": "....x.......x...",
        "hat": "x.x.x.x.x.x.x.x.",
    },
    "four-floor": {
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "hat": "..x...x...x...x.",
    },
    "breaks": {
        "kick": "x.........x..x..",
        "snare": "....x..x.x..x...",
        "hat": "x.x.x.x.x.x.x.x.",
    },
    "kick-only": {"kick": "x...x...x...x..."},
    "hats-only": {"hat": "x.x.x.x.x.x.x...", "ohat": "..............x."},
    "open-off": {
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "ohat": "..x...x...x...x.",
    },
    "two-bar": {
        "kick": "x.....x...x.....x..x..x...x.....",
        "snare": "....x.......x.......x.......x..x",
        "hat": "x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.x.",
    },
    "16ths, choked open hats": {
        "kick": "x...x...x...x...",
        "snare": "....x.......x...",
        "hat": ".x.x.x.x.x.x.x.x",
        "ohat": "..x...x...x...x.",
    },
    "swung (0.16)": {
        "kick": "x.........x.....",
        "snare": "....x.......x...",
        "hat": ".x...x...x...x.x",
    },
}


def drum_loop(rows: dict, bpm: float, swing: float, params: dict) -> np.ndarray:
    """A pattern rendered by the studio's drum machine, exactly its bars."""
    from synthlab.studio.arrange import _schedule_drums
    from synthlab.studio.state import Arrangement

    bars = max(len(r) for r in rows.values()) // 16
    steps = sorted(
        [v, i] for v, r in rows.items() for i, ch in enumerate(r) if ch == "x"
    )
    arr = Arrangement(
        bpm=bpm,
        cells=[],
        drums=steps,
        drum_bars=bars,
        drum_steps=16,
        drum_swing=swing,
        drum_params={v: dict(p) for v, p in params.items()},
    )
    song = Song(bpm=bpm)
    _schedule_drums(song, arr)
    x = song.render(tail_beats=0)
    n = int(round(bars * 240.0 / bpm * SR))
    if len(x) < n:
        x = np.concatenate([x, np.zeros((n - len(x), 2))])
    return x[:n]


def report_drums() -> None:
    from synthlab.studio.drumfactory import DRUM_KITS

    kits = {"default": {}, **{k: v["params"] for k, v in DRUM_KITS.items()}}
    tempi = (93, 124, 140)
    lanes = ("kick", "snare", "hat", "ohat", "any hat")
    print(
        f"\n== drum reading vs the drum machine's own patterns:"
        f" {len(DRUM_PATTERNS)} patterns x {len(tempi)} tempi x {len(kits)} kits =="
    )
    total = {lane: [0, 0, 0] for lane in lanes}
    swing_err = []
    for name, rows in DRUM_PATTERNS.items():
        mine = {lane: [0, 0, 0] for lane in lanes}
        swing = 0.16 if "swung" in name else 0.0
        for params in kits.values():
            for bpm in tempi:
                audio = drum_loop(rows, bpm, swing, params)
                bars = max(len(r) for r in rows.values()) // 16
                pat, _hits = drumhits.transcribe(audio, 240.0 / bpm, bars)
                swing_err.append(abs(pat.swing - swing))
                for lane in lanes:
                    names = ("hat", "ohat") if lane == "any hat" else (lane,)
                    truth = {
                        i
                        for v in names
                        for i, c in enumerate(rows.get(v, ""))
                        if c == "x"
                    }
                    got = {s for v, s in pat.steps if v in names}
                    for k, n in enumerate(
                        (len(truth & got), len(got - truth), len(truth - got))
                    ):
                        mine[lane][k] += n
                        total[lane][k] += n
        cells = "  ".join(
            f"{lane} +{fp}/-{fn}" for lane, (_tp, fp, fn) in mine.items() if fp or fn
        )
        print(f"  {name:<24} {cells or 'exact'}")
    print("  lane       precision  recall   (true +, false +, missed)")
    for lane, (tp, fp, fn) in total.items():
        print(
            f"  {lane:<10} {tp / max(1, tp + fp):9.3f}  {tp / max(1, tp + fn):6.3f}"
            f"   ({tp}, {fp}, {fn})"
        )
    print(f"  swing read within {max(swing_err):.2f} of the pattern's at worst")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--audio", type=Path, help="a folder with a real song")
    ap.add_argument("--skip-synthetic", action="store_true")
    ap.add_argument("--model", choices=("4", "6"), default="4", help="stems")
    ap.add_argument("--drums", action="store_true", help="the drum reading only")
    ap.add_argument("--child", nargs=3, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(*args.child)
        return
    if args.drums:
        report_drums()
        return
    global MODEL
    MODEL = stems.HTDEMUCS_6S if args.model == "6" else stems.HTDEMUCS
    have = stems.availability(MODEL)
    if have["reason"]:
        raise SystemExit(have["reason"])
    if not have["model"]:
        print(f"downloading the model ({have['megabytes']} MB) to {stems.cache_dir()}")
        t = time.perf_counter()
        stems.download(MODEL)
        print(f"downloaded and verified in {time.perf_counter() - t:.0f} s")
    print(f"model: {MODEL.id} ({MODEL.label})")
    report_drums()
    with tempfile.TemporaryDirectory() as tmp:
        if not args.skip_synthetic:
            report_synthetic(Path(tmp))
        if args.audio:
            report_real(Path(tmp), args.audio)


if __name__ == "__main__":
    main()
