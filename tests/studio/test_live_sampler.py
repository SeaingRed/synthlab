"""The live sampler's wire: /patch.json names the buffers (`sampler_live`),
GET /sampler/live/<id>.bin serves them (`sampler.live_payload`), and the
gate admits only what the worklet reproduces (tests/studio/
test_live_parity_corpus.py proves the sound itself). Formant mode's grain
train (static/grain-worklet.js) is also held here to `sampler._psola`
directly, sample for sample, and to what made baking it impossible: the
played pitch changes the grain spacing, never the note's length."""

import json
import shutil
import struct
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.studio.app import create_app
from synthlab.studio.samplerview import sampler_patch
from synthlab.studio.worklet import blockers
from synthlab.synth import Patch

SR = sampler.SR
_T = np.arange(int(0.5 * SR)) / SR
MONO = np.sin(2 * np.pi * 220.0 * _T) * 0.5
STEREO = np.column_stack([MONO, np.sin(2 * np.pi * 330.0 * _T) * 0.5])


def _split(body: bytes):
    n = struct.unpack("<I", body[:4])[0]
    assert (4 + n) % 4 == 0  # the samples start aligned for a Float32Array
    return json.loads(body[4 : 4 + n]), np.frombuffer(body[4 + n :], dtype="<f4")


def _client(tmp_path: Path, audio) -> tuple[TestClient, str]:
    client = TestClient(create_app(data_dir=tmp_path))
    state = client.app.state.svc.state
    sid = sampler.save(state.sample_dir, audio, SR)
    state.load_sound(sampler_patch(sid, "take"), label="take")
    return client, sid


def test_patch_json_names_the_buffers_and_plays_live(tmp_path):
    client, sid = _client(tmp_path, MONO)
    pj = client.get("/patch.json").json()
    assert pj["worklet_ok"], pj["worklet_blockers"]
    url = pj["sampler_live"]
    assert url.startswith(f"/sampler/live/{sid}.bin?")
    r = client.get(url)
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]
    meta, data = _split(r.content)
    assert meta["sr"] == SR and not meta["slices"]
    (src,) = meta["sources"]
    assert len(data) == src["n"]


def test_payload_is_the_engines_own_source(tmp_path):
    sid = sampler.register(MONO)
    p = Patch(
        osc1="sampler",
        sampler_sample=sid,
        sampler_start=0.1,
        sampler_loop="on",
        sampler_loop_start=0.3,
        sampler_loop_end=0.8,
        sampler_xfade=0.02,
    )
    query = dict(parse_qsl(sampler.live_url(p).split("?", 1)[1]))
    st = sampler.live_settings(query)
    meta, data = _split(sampler.live_payload(sid, st))
    src, s0 = sampler._whole_source(sampler.get(sid), sampler.Settings.of(p))
    (info,) = meta["sources"]
    assert (info["s0"], info["lo"], info["hi"], info["looping"]) == (
        s0,
        src.lo,
        src.hi,
        True,
    )
    np.testing.assert_allclose(data, src.buf[:, 0], rtol=0, atol=1e-7)  # float32


def test_chops_send_every_slice(tmp_path):
    sid = sampler.register(MONO)
    st = sampler.Settings(slices="0,0.25,0,0;0.25,0.6,5,1;0.6,1,-3,0")
    meta, data = _split(sampler.live_payload(sid, st))
    assert meta["slices"] and [s["pitch"] for s in meta["sources"]] == [0, 5, -3]
    assert len(data) == sum(s["n"] for s in meta["sources"])


def test_what_the_worklet_cant_play_names_its_field(tmp_path):
    """Exactly the refusing field, per sample kind. Formant mode plays live
    (its grain train, at any scalar stretch); the WSOLA stretch ("resample"
    mode off 1) and a drawn stretch stay offline for a mono and a stereo
    sample alike; and a formant sampler whose pitch the voice cannot
    foresee a root period ahead (a route onto the pitch, cross-FM) names
    the route or the FM, not the sampler."""
    for sid in (sampler.register(MONO), sampler.register(STEREO)):
        base = Patch(osc1="sampler", sampler_sample=sid)
        assert blockers(base) == []
        formant = Patch(**{**_kw(base), "sampler_mode": "formant"})
        slow = Patch(**{**_kw(formant), "sampler_stretch": 2.0})
        for p in (formant, slow):
            assert blockers(p) == [] and sampler.live_url(p) is not None
        stretched = Patch(**{**_kw(base), "sampler_stretch": 1.5})
        # a drawn stretch is offline in both modes
        curve = np.linspace(0.5, 2.0, 100)
        drawn = Patch(**{**_kw(base), "sampler_stretch": curve})
        drawn_f = Patch(**{**_kw(formant), "sampler_stretch": curve})
        for p in (stretched, drawn, drawn_f):
            assert blockers(p) == ["osc1", "sampler_stretch"]
            assert sampler.live_url(p) is None  # no buffers for what plays offline
        # the pitch the grain schedule reads ahead: only what the voice foresees
        for dst in ("pitch", "osc1_detune", "vibrato_depth"):
            route = {"mod2_src": "lfo", "mod2_dst": dst, "mod2_amt": 0.4}
            assert blockers(Patch(**{**_kw(formant), **route})) == ["mod2_src"]
            assert blockers(Patch(**{**_kw(base), **route})) == []  # the tape read
        cross = {"osc2": "sine", "fm_amount": 0.3}
        assert blockers(Patch(**{**_kw(formant), **cross})) == ["fm_amount"]
        assert blockers(Patch(**{**_kw(base), **cross})) == []
        # every other route, and osc2 without FM, play
        fine = {"mod1_src": "lfo", "mod1_dst": "cutoff", "mod1_amt": 0.5, "osc2": "saw"}
        assert blockers(Patch(**{**_kw(formant), **fine})) == []


def _kw(p: Patch) -> dict:
    return {k: v for k, v in p.to_json().items()}


def test_a_stereo_sample_plays_live_on_both_channels(tmp_path):
    client, sid = _client(tmp_path, STEREO)
    pj = client.get("/patch.json").json()
    assert pj["worklet_ok"], pj["worklet_blockers"]
    r = client.get(pj["sampler_live"])
    assert r.status_code == 200
    meta, data = _split(r.content)
    (info,) = meta["sources"]
    assert meta["ch"] == 2 and len(data) == 2 * info["n"]
    # each source a channel at a time, each the engine's own (float32)
    p = client.app.state.svc.state.patch
    src, _s0 = sampler._whole_source(sampler.get(sid), sampler.Settings.of(p))
    n = info["n"]
    np.testing.assert_allclose(data[:n], src.buf[:, 0], rtol=0, atol=1e-7)
    np.testing.assert_allclose(data[n:], src.buf[:, 1], rtol=0, atol=1e-7)
    assert float(np.max(np.abs(data[:n] - data[n:]))) > 0.1  # two channels


def test_stereo_chops_send_both_channels_of_every_slice():
    sid = sampler.register(STEREO)
    st = sampler.Settings(slices="0,0.25,0,0;0.25,0.6,5,1")
    meta, data = _split(sampler.live_payload(sid, st))
    assert meta["ch"] == 2 and len(data) == 2 * sum(s["n"] for s in meta["sources"])
    x, o = sampler.get(sid), 0
    for info, sl in zip(meta["sources"], sampler.parse_slices(st.slices), strict=True):
        buf, n = sampler._slice_source(x, sl).buf, info["n"]
        np.testing.assert_allclose(data[o : o + n], buf[:, 0], rtol=0, atol=1e-7)
        np.testing.assert_allclose(
            data[o + n : o + 2 * n], buf[:, 1], rtol=0, atol=1e-7
        )
        o += 2 * n


def test_a_mono_sample_still_sends_one_channel():
    sid = sampler.register(MONO)
    meta, data = _split(sampler.live_payload(sid, sampler.Settings()))
    assert meta["ch"] == 1 and len(data) == meta["sources"][0]["n"]
    assert sampler.live_samples(sid, "") == 2 * len(MONO) + 1


def test_the_cap_counts_both_channels(tmp_path):
    """A stereo sample's payload is twice a mono one's, and the cap, the
    gate and the route all count it that way: past it the route answers
    413 and the patch keeps its WAV keys, named by `sampler_sample`."""
    client, sid = _client(tmp_path, STEREO)
    frames = 2 * len(STEREO) + 1  # the whole-sample bound, per channel
    assert sampler.live_samples(sid, "") == 2 * frames
    p = Patch(osc1="sampler", sampler_sample=sid)
    old = sampler.LIVE_MAX_SAMPLES
    try:
        sampler.LIVE_MAX_SAMPLES = 2 * frames  # exactly both channels: live
        assert blockers(p) == [] and client.get(sampler.live_url(p)).status_code == 200
        sampler.LIVE_MAX_SAMPLES = 2 * frames - 1  # one channel would still fit
        assert blockers(p) == ["osc1", "sampler_sample"]
        assert sampler.live_url(p) is None
        assert client.get(f"/sampler/live/{sid}.bin").status_code == 413
    finally:
        sampler.LIVE_MAX_SAMPLES = old


def test_a_sample_this_machine_lacks_is_a_404_until_it_arrives(tmp_path):
    """A project.json imported without its audio names a sample the store
    doesn't hold. The route answered 200 with an empty payload, cached
    `immutable`: the browser kept "nothing to play" for that URL after the
    sample arrived (the bundle, imported next), and the live keys stayed
    silent while the loop played the sound."""
    client = TestClient(create_app(data_dir=tmp_path))
    audio = np.sin(2 * np.pi * 777.7 * _T[:9001]) * 0.31  # in no other test
    sid = sampler.sample_id(sampler.prepare(audio, SR))
    url = sampler.live_url(Patch(osc1="sampler", sampler_sample=sid))
    assert url is not None  # the gate can't know: a missing sample is silence
    missing = client.get(url)
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("cache-control", "")
    assert sampler.save(client.app.state.svc.state.sample_dir, audio, SR) == sid
    r = client.get(url)
    assert r.status_code == 200 and len(_split(r.content)[0]["sources"]) == 1


def test_bad_ids_and_oversized_chops_are_refused(tmp_path):
    client, sid = _client(tmp_path, MONO)
    assert client.get("/sampler/live/nothex.bin").status_code == 404
    # overlapping full-length chops past LIVE_MAX_SAMPLES: refused, and the
    # gate keeps such a patch off the worklet
    huge = ";".join(["0,1,0,0"] * sampler.MAX_SLICES)
    old = sampler.LIVE_MAX_SAMPLES
    try:
        sampler.LIVE_MAX_SAMPLES = len(MONO) * 4
        r = client.get(f"/sampler/live/{sid}.bin", params={"slices": huge})
        assert r.status_code == 413
        p = Patch(osc1="sampler", sampler_sample=sid, sampler_slices=huge)
        assert "sampler_sample" in blockers(p)
    finally:
        sampler.LIVE_MAX_SAMPLES = old


# --- formant mode's grain train, against sampler._psola itself ---------------

GRAINS = """
const fs = require("fs");
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
globalThis.sampleRate = job.sr;
// the shipped modules, as the worklet scope holds them (eval: classic scripts)
for (const f of ["sampler-worklet.js", "grain-worklet.js"]) {
  eval(fs.readFileSync(process.argv[2] + "/" + f, "utf8"));
}
const sb = fs.readFileSync(job.payload), sn = sb.readUInt32LE(0);
const meta = JSON.parse(sb.subarray(4, 4 + sn).toString("utf8"));
const data = new Float32Array(
  sb.buffer.slice(sb.byteOffset + 4 + sn, sb.byteOffset + sb.length));
const m = globalThis.synthlabSampler.set("u", meta, data, job.sr);
const n = job.n, hz = job.hz, L = new Float32Array(n), R = new Float32Array(n);
// one unison copy of a voice holding `hz`: what voice-worklet.js hands over
const F = new Float64Array(64).fill(hz);
const o = new Float64Array(64), oR = new Float64Array(64);
const v = { sp: [], target: job.key, n: 0, freq: hz, glideN: 0, glideR: 1, pv: 0,
  vel: job.vel, rr: job.rr };
// a zone map's pick, as the voice posts it to the page
const P = { T: job.T, F,
  cur: { vibrato_depth: 0, pitch_bend: 0, bend_range: 2, wheel_st: 0 },
  port: { postMessage: (x) =>
    fs.writeFileSync(job.out + ".zone", JSON.stringify(x)) } };
for (let i = 0; i < n; i += 64) {
  const c = Math.min(64, n - i);
  v.n = i + c;
  globalThis.synthlabSampler.read(m, job.T, v, 0, F, o, oR, c, P);
  L.set(o.subarray(0, c), i); R.set(oR.subarray(0, c), i);
}
fs.writeFileSync(
  job.out, Buffer.concat([Buffer.from(L.buffer), Buffer.from(R.buffer)]));
"""
STATIC = Path("synthlab/studio/static").resolve()
C4 = sampler.midi_hz(60)
_T2 = np.arange(SR) / SR  # one second
_SAW = sum(np.sin(2 * np.pi * C4 * k * _T2) / k for k in range(1, 9)) * 0.3
TONE = _SAW * np.exp(-_T2 / 1.5)
TONE_ST = np.column_stack(
    [TONE, np.sin(2 * np.pi * 1.5 * C4 * _T2) * 0.3 * np.exp(-_T2 / 0.9)]
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs it")


def _grains(
    tmp_path, p: Patch, hz: float, seconds: float, sr: int = SR, key=C4, vel=1.0, rr=0
):
    """`seconds` of the live sampler oscillator alone (sampler-worklet.js
    handing to grain-worklet.js) holding `hz`, in a context at `sr`:
    stereo (n, 2). `vel` and `rr`: the voice's velocity and strike (a
    zone map picks by them)."""
    query = dict(parse_qsl(sampler.live_url(p).split("?", 1)[1]))
    payload = sampler.live_payload(p.sampler_sample, sampler.live_settings(query))
    (tmp_path / "p.bin").write_bytes(payload)
    job = {
        "sr": sr,
        "n": int(seconds * sr),
        "hz": hz,
        "key": key,
        "vel": vel,
        "rr": rr,
        "payload": str(tmp_path / "p.bin"),
        "T": {k: v for k, v in p.to_json().items() if k.startswith("sampler")},
        "out": str(tmp_path / "o.f32"),
    }
    (tmp_path / "job.json").write_text(json.dumps(job), encoding="utf-8")
    (tmp_path / "grains.cjs").write_text(GRAINS, encoding="utf-8")
    cmd = ["node", str(tmp_path / "grains.cjs"), str(STATIC)]
    subprocess.run([*cmd, str(tmp_path / "job.json")], check=True, timeout=120)
    raw = np.fromfile(tmp_path / "o.f32", dtype=np.float32).astype(np.float64)
    return raw.reshape(2, -1).T


def _engine(p: Patch, hz: float, seconds: float, key=C4, vel=1.0, rr=0) -> np.ndarray:
    n = int(seconds * SR)
    return sampler.render(np.full(n, hz), n, SR, sampler.Settings.of(p, vel, rr), key)


def _level_db(x: np.ndarray, sr: int) -> np.ndarray:
    """The 10 ms level of (n, channels) audio, dB."""
    fr = sr // 100
    m = len(x) // fr
    power = np.mean(np.square(x[: m * fr]).reshape(m, fr, -1), axis=(1, 2))
    return 10 * np.log10(power + 1e-12)


def _sounds_for(x: np.ndarray, sr: int = SR) -> float:
    """Seconds until the 10 ms level last sits within 40 dB of its peak."""
    level = _level_db(x, sr)
    return float((np.flatnonzero(level > level.max() - 40)[-1] + 1) / 100)


_FORMANT = {"osc1": "sampler", "sampler_mode": "formant"}
_LOOP = {
    "sampler_loop": "on",
    "sampler_loop_start": 0.3,
    "sampler_loop_end": 0.7,
    "sampler_xfade": 0.02,
}
_INSIDE = {**_LOOP, "sampler_start": 0.4}


@needs_node
@pytest.mark.parametrize(
    "kw, st",
    [
        ({}, 0),
        ({}, 12),
        ({}, -7),
        ({}, 24),
        ({"sampler_start": 0.2, "sampler_end": 0.9}, 5),
        (_LOOP, 12),
        (_INSIDE, -12),
        ({"sampler_stretch": 2.0}, 12),
        ({"sampler_stretch": 0.5}, -5),
        ({"sampler_slices": "0,0.5,0,0;0.5,1,-12,1", "sampler_root": 59}, 0),
        ({"sampler_keytrack": "off"}, 7),
        ({"sampler_root": 36}, 0),
    ],
)
@pytest.mark.parametrize("audio", [TONE, TONE_ST], ids=["mono", "stereo"])
def test_the_grain_train_is_psolas_own_samples(tmp_path, audio, kw, st):
    """At a held pitch the worklet's grain train is `_psola`'s, grain for
    grain: the same centres (snapped to whole root periods), the same
    fractional offsets, gains and window — float32 apart. The last grain
    length of the engine's buffer is left out: `_psola` stops laying grains
    at the buffer's end (`while c < n`), so its last half-grain fades with
    nothing overlapping it, and a live voice has no buffer end to know
    (in a note that is the last few ms of the release)."""
    p = Patch(**_FORMANT, sampler_sample=sampler.register(audio), **kw)
    hz, seconds = C4 * 2 ** (st / 12), 1.3
    eng, live = _engine(p, hz, seconds), _grains(tmp_path, p, hz, seconds)
    assert eng.shape == live.shape and float(np.max(np.abs(eng))) > 0.1
    g = int(min(max(2 * SR / sampler.midi_hz(p.sampler_root), 32), 8192))
    err = np.max(np.abs(eng - live)[: len(eng) - g])
    assert err < 1e-6 * np.max(np.abs(eng)), err


@needs_node
def test_formant_keeps_the_length_whatever_the_key(tmp_path):
    """What a baked buffer could not do (read at twice the rate it lasts
    half as long): an octave up, an octave down and the root all sound for
    the sample's own second, and stretch 2 for two — the engine's lengths."""
    sid = sampler.register(TONE)
    for stretch in (1.0, 2.0, 0.5):
        p = Patch(**_FORMANT, sampler_sample=sid, sampler_stretch=stretch)
        for st in (-12, 0, 12):
            hz = C4 * 2 ** (st / 12)
            live = _sounds_for(_grains(tmp_path, p, hz, 2.6))
            eng = _sounds_for(_engine(p, hz, 2.6))
            assert abs(live - eng) <= 0.011, (stretch, st, live, eng)
            assert abs(live - stretch) < 0.03, (stretch, st, live)
    # the tape read, for contrast: an octave up is half as long
    tape = Patch(osc1="sampler", sampler_sample=sid)
    assert abs(_sounds_for(_grains(tmp_path, tape, 2 * C4, 2.6)) - 0.5) < 0.03


@needs_node
@pytest.mark.parametrize("st", [0, 12, -7])
def test_a_48k_context_plays_the_same_grains_in_time(tmp_path, st):
    """The engine runs at 44.1 kHz; a browser's context usually at 48. The
    grains are the engine's in seconds there (g / ratio samples long, read
    `ratio` source samples apart), so the note keeps its length, level and
    pitch: against the engine's render, the 10 ms level within 0.5 dB
    while it sounds, the same length, and the same loudest harmonic."""
    p = Patch(**_FORMANT, sampler_sample=sampler.register(TONE))
    hz = C4 * 2 ** (st / 12)
    live, eng = _grains(tmp_path, p, hz, 1.2, sr=48000), _engine(p, hz, 1.2)
    a, b = _level_db(eng, SR), _level_db(live, 48000)
    body = slice(2, 95)  # past the first grains' fade-in, before the end
    assert np.max(np.abs(a[body] - b[body])) < 0.5
    assert abs(_sounds_for(live, 48000) - _sounds_for(eng)) <= 0.011

    def peak_hz(x, sr):
        seg = x[int(0.2 * sr) : int(0.7 * sr), 0]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        return float(np.fft.rfftfreq(len(seg), 1 / sr)[np.argmax(spec)])

    assert abs(peak_hz(live, 48000) - peak_hz(eng, SR)) <= 2.0


# --- a zone map: a multi-sample instrument -----------------------------------

_LOW = sampler.register(TONE * 0.6)
_HIGH = sampler.register(np.sin(2 * np.pi * 2 * C4 * _T2) * 0.4 * np.exp(-_T2))
_ALT = sampler.register(TONE_ST)  # a true-stereo take
_HARD = sampler.register(np.sign(np.sin(2 * np.pi * C4 * _T2)) * 0.2)
# low keys, high keys; on the high keys two takes and a hard layer from 90
MAP = (
    f"{_LOW},0,59,48,0,127,0,-3;{_HIGH},60,127,72;{_ALT},60,127,72,0,127,1,0,30;"
    f"{_HARD},60,127,72,90,127,0,2,-20,0.1,0.9,0.3,0.6"
)


def test_a_zone_map_names_every_sample_and_sends_each_zones_source():
    p = Patch(osc1="sampler", sampler_zones=MAP)
    assert blockers(p) == []
    url = sampler.live_url(p)
    assert url.startswith(f"/sampler/live/{_LOW}.bin?") and "zones=" in url
    query = dict(parse_qsl(url.split("?", 1)[1]))
    st = sampler.live_settings(query)
    assert st.zones == sampler.clean_zones(MAP)
    meta, data = _split(sampler.live_payload("", st))
    assert meta["ch"] == 2 and not meta["slices"]  # one zone is true stereo
    zones = sampler.parse_zones(MAP)
    assert [z["src"] for z in meta["zones"]] == [0, 1, 2, 3]
    assert [
        (z["lo"], z["hi"], z["root"], z["rr"], z["vlo"]) for z in meta["zones"]
    ] == [(z.lo, z.hi, z.root, z.rr, z.vlo) for z in zones]
    o = 0
    for info, z in zip(meta["sources"], zones, strict=True):
        src, s0 = sampler._whole_source(
            sampler.get(z.sample), sampler.zone_settings(st, z)
        )
        assert (info["s0"], info["looping"]) == (s0, src.looping)
        n = info["n"]
        np.testing.assert_allclose(data[o : o + n], src.buf[:, 0], rtol=0, atol=1e-7)
        o += 2 * n
    assert len(data) == o
    # the cap counts every zone; past it the keys keep the WAV path
    assert sampler.live_size(p) == sampler.live_zone_samples(MAP) > 0
    old = sampler.LIVE_MAX_SAMPLES
    sampler.LIVE_MAX_SAMPLES = sampler.live_size(p) - 1
    try:
        assert blockers(p) == ["osc1", "sampler_zones"]
        assert sampler.live_url(p) is None
    finally:
        sampler.LIVE_MAX_SAMPLES = old


def test_a_zone_map_whose_sample_is_missing_is_a_404(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    gone = "0123456789abcdef"
    p = Patch(osc1="sampler", sampler_zones=f"{_LOW},0,59;{gone},60,127")
    url = sampler.live_url(p)
    assert client.get(url).status_code == 404
    ok = Patch(osc1="sampler", sampler_zones=f"{_LOW},0,59;{_HIGH},60,127")
    assert client.get(sampler.live_url(ok)).status_code == 200


@needs_node
@pytest.mark.parametrize("mode", ["resample", "formant"])
@pytest.mark.parametrize(
    "key, vel, rr, want",
    [
        (48, 1.0, 0, [0]),  # a low key: the low zone, -3 dB
        (55, 0.5, 3, [0]),  # one take there: every strike the same
        (72, 0.5, 0, [1]),  # a high key, soft: take 0
        (74, 0.5, 1, [2]),  # its next strike: take 1 (stereo, +30 cents)
        (72, 0.8, 0, [1, 3]),  # 102: crossfading into the hard layer
        (76, 1.0, 2, [3]),  # the top: the hard layer alone, its own span + loop
    ],
)
def test_the_live_zone_pick_and_read_are_the_engines(
    tmp_path, mode, key, vel, rr, want
):
    """The voice's zone pick (sampler-worklet.js `pick`) is
    sampler.pick_zones, and its read of each zone the engine's: the same
    samples as `sampler.render` for the same key, velocity and strike,
    float32 apart — the pick reported as the voice reports it."""
    p = Patch(osc1="sampler", sampler_mode=mode, sampler_zones=MAP)
    hz = sampler.midi_hz(key)
    eng = _engine(p, hz, 1.3, key=hz, vel=vel, rr=rr)
    live = _grains(tmp_path, p, hz, 1.3, key=hz, vel=vel, rr=rr)
    assert float(np.max(np.abs(eng))) > 0.05
    g = 8192 if mode == "formant" else 0  # _psola's buffer end (above)
    err = np.max(np.abs(eng - live)[: len(eng) - g])
    assert err < 1e-6 * np.max(np.abs(eng)), err
    zones = sampler.parse_zones(MAP)
    picked = sampler.pick_zones(zones, key, sampler.velocity_step(vel), rr)
    assert [i for i, _ in picked] == want
    report = json.loads((tmp_path / "o.f32.zone").read_text(encoding="utf-8"))
    assert report["zones"] == want and report["key"] == key and report["rr"] == rr
