"""Voice lab: a recorded or uploaded take drives the synth through a vocoder.

The take is the *modulator* — its spectral envelope, and so its
articulation, is imposed on a carrier. The carrier supplies pitch and
timbre; the take supplies the words. Four carriers (`CARRIERS`): the
current sound on typed notes or chords, the current sound following the
take's own pitch, the loop Synths plays, or any library clip.

What the page remembers — the settings, where the take came from, the last
audition — is `voice_lab.json` beside the take, never the session: a file
anyone can edit, so everything read from it goes through `clean_settings`
/ `clean_origin`. The last few auditions are held with their clip meta
(`RESULTS`) so "Keep result" saves the samples that were heard.

Only WAV ever reaches this module. The browser owns decoding (it has the
codecs for webm/opus from MediaRecorder, and for whatever a user drags in),
converts to 16-bit PCM and uploads that, so there is no codec surface here
and file upload accepts any format the browser can read for free.
"""

from __future__ import annotations

import io
import json
import math
import os
import threading
import wave
from pathlib import Path

import numpy as np

from .. import effects as fx
from ..analysis import voiced
from ..audio import PCM_DECODE_SCALE, PCM_ENCODE_SCALE, read_pcm16
from ..core import SR
from ..sequencer import SCALES, midi
from ..synth import Patch, Synth
from . import booth, limits, vocal
from .clips import clip_dir, clip_hash, clip_safe
from .lru import LRU

TAKE_NAME = "voice_take.wav"
LAB_NAME = "voice_lab.json"
MAX_TAKE_SECONDS = 30.0

BAND_CHOICES = (8, 16, 24, 32)
# what plays under the voice
CARRIERS = ("sound", "follow", "loop", "clip")
# how long each chord of a row lasts: bars of the session's tempo, or
# "even" — the take shared out once through
STEPS = ("0.5", "1", "2", "even")
SNAPS = ("off", "scale")  # the followed pitch: as sung, or on the session's key
DEFAULT_NOTES = "A2"
DEFAULTS: dict = {
    "bands": 16,
    "carrier": "sound",
    "notes": DEFAULT_NOTES,
    "step": "1",
    "snap": "off",
    "octave": 0,
    "clip": "",
    "shift": 0.0,
    "smooth": 40.0,  # ms: the band followers' release (effects.vocoder's default)
    "sibilance": 0.0,
}
# Named starting points for the settings above, as data a menu can offer
# (no control lists them yet). `settings` are the fields a preset moves —
# `preset_settings` lays them over what is there and cleans the lot, as a
# posted form is; `sound` names the recipe Sound made to be its carrier
# (`recipes.py`), which the menu loads on Synths beside it.
PRESETS: dict[str, dict] = {
    # the take's formants on a bright, gliding mono carrier, lifted two
    # semitones so the words sit forward, with quick band followers
    "talkbox": {
        "label": "Talkbox",
        "sound": "Talkbox Carrier",
        "settings": {
            "carrier": "sound",
            "bands": 24,
            "shift": 2.0,
            "smooth": 30.0,
            "sibilance": 0.2,
        },
    },
}


def preset_settings(name, base: dict | None = None) -> dict | None:
    """`base` (the saved settings) with the preset `name` laid over it,
    cleaned; None for a name that is not a preset."""
    preset = PRESETS.get(name) if isinstance(name, str) else None
    if preset is None:
        return None
    return clean_settings(dict(preset["settings"]), base)


_HELD = {"attack": 0.01, "decay": 0.05, "sustain": 1.0, "release": 0.05}
_MIDI_RANGE = (12, 108)  # a carrier note, whatever was typed or sung
_BEND = 24.0  # semitones either way of its centre a followed carrier can go
_SEAM_FADE = 0.005  # seconds: each end of a repeated clip that is not a loop


class TakeError(ValueError):
    """Bad or unreadable take — surfaced as a 422 toast, never a 500."""


def read_wav(raw: bytes) -> np.ndarray:
    """16/32-bit PCM WAV bytes -> mono float64 in [-1, 1], resampled to SR."""
    if len(raw) > limits.MAX_TAKE_UPLOAD_BYTES:
        raise TakeError("take is too large")
    try:
        with wave.open(io.BytesIO(raw)) as w:
            channels, width, rate, frames = (
                w.getnchannels(),
                w.getsampwidth(),
                w.getframerate(),
                w.getnframes(),
            )
            data = w.readframes(frames)
    except (wave.Error, EOFError) as err:
        raise TakeError("not a readable WAV file") from err
    # A hostile or broken rate (0, 1, absurdly high) must be refused before
    # it ever reaches the resample below — `n` there scales with SR / rate,
    # so a near-zero rate turns a modest take into a multi-gigasample buffer.
    if not limits.MIN_SR <= rate <= limits.MAX_SR:
        raise TakeError(f"unsupported sample rate: {rate} Hz")
    # A file cut mid-frame (its last 1-3 bytes gone) hands back a ragged
    # buffer: `np.frombuffer`/`reshape` raised ValueError on it, a 500.
    # Refused like any unreadable WAV, as /clip/upload and /song/take do.
    if width in (1, 2, 4) and len(data) % (width * max(1, channels)):
        raise TakeError("not a readable WAV file")
    if width == 2:
        arr = np.frombuffer(data, dtype="<i2").astype(np.float64) / PCM_DECODE_SCALE
    elif width == 4:
        arr = np.frombuffer(data, dtype="<i4").astype(np.float64) / 2147483648.0
    elif width == 1:
        arr = (np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128) / 128.0
    else:
        raise TakeError(f"unsupported sample width: {width * 8}-bit")
    if arr.size == 0:
        raise TakeError("take is empty")
    if channels > 1:
        arr = arr.reshape(-1, channels).mean(axis=1)
    # Truncate at the *native* rate before resampling (mirrors clips.py's
    # save_clip), so the resample's cost is bounded by MAX_TAKE_SECONDS
    # regardless of how long the uploaded buffer claimed to be.
    arr = arr[: int(MAX_TAKE_SECONDS * rate)]
    if rate != SR:  # linear resample: takes are speech, not masters
        n = max(1, int(round(len(arr) * SR / rate)))
        arr = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(arr)), arr)
    return arr[: int(MAX_TAKE_SECONDS * SR)]


def take_path(data_dir: Path) -> Path:
    return Path(data_dir) / TAKE_NAME


def save_take(data_dir: Path, samples: np.ndarray) -> Path:
    path = take_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples, -1, 1)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((pcm * PCM_ENCODE_SCALE).astype("<i2").tobytes())
    return path


def load_take(data_dir: Path) -> np.ndarray | None:
    path = take_path(data_dir)
    if not path.exists():
        return None
    try:
        return read_wav(path.read_bytes())
    except TakeError:
        return None


# --- what the page remembers: voice_lab.json ---------------------------------------


def _number(v, lo: float, hi: float, default: float) -> float:
    """`v` as a float inside lo..hi; anything that is not a finite number
    (a bool, NaN, a 400-digit int, text that is not one) is `default`."""
    if isinstance(v, bool):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return default
    return min(hi, max(lo, f)) if math.isfinite(f) else default


def _choice(v, choices: tuple, default):
    return v if isinstance(v, str) and v in choices else default


def clean_settings(raw, base: dict | None = None) -> dict:
    """The vocoder's settings with every field held to its type, choices
    and range — a posted form and the file on disk both come through here.
    A field `raw` does not carry keeps `base`'s value (the saved settings
    under a form that posts some of them), else the default."""
    raw = raw if isinstance(raw, dict) else {}
    was = {**DEFAULTS, **(base or {})}

    def get(key: str):
        return raw[key] if key in raw else was[key]

    bands = _number(get("bands"), 1, 1024, was["bands"])
    notes = get("notes")
    notes = notes if isinstance(notes, str) else DEFAULT_NOTES
    notes = " ".join(notes[: limits.MAX_VOICE_NOTES_CHARS].split())
    clip = get("clip")
    lo, hi = limits.VOICE_OCTAVE_RANGE
    return {
        # the nearest offered band count: the vocoder's cost grows with it
        "bands": min(BAND_CHOICES, key=lambda b: (abs(b - bands), b)),
        "carrier": _choice(get("carrier"), CARRIERS, DEFAULTS["carrier"]),
        "notes": notes or DEFAULT_NOTES,
        "step": _choice(get("step"), STEPS, DEFAULTS["step"]),
        "snap": _choice(get("snap"), SNAPS, DEFAULTS["snap"]),
        "octave": int(round(_number(get("octave"), lo, hi, 0))),
        "clip": clip_safe(clip) if isinstance(clip, str) and clip.strip() else "",
        "shift": round(_number(get("shift"), *limits.VOICE_SHIFT_RANGE, 0.0), 2),
        "smooth": round(_number(get("smooth"), *limits.VOICE_SMOOTH_MS_RANGE, 40.0), 1),
        "sibilance": round(_number(get("sibilance"), 0.0, 1.0, 0.0), 3),
    }


def clean_origin(raw) -> dict:
    """Where the take came from: `{kind: "recording" | "clip", name, hash}`
    (a clip's name and content hash when it was put in the slot)."""
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("kind") != "clip" or not isinstance(raw.get("name"), str):
        return {"kind": "recording", "name": "", "hash": ""}
    digest = raw.get("hash")
    digest = digest[:40] if isinstance(digest, str) and digest.isalnum() else ""
    return {"kind": "clip", "name": clip_safe(raw["name"]), "hash": digest}


def lab_path(data_dir: Path) -> Path:
    return Path(data_dir) / LAB_NAME


def read_lab(data_dir: Path) -> dict:
    """`{take, settings, last, booth, booth_last, live}` from
    voice_lab.json, cleaned; a missing or unreadable file is the defaults."""
    try:
        raw = json.loads(lab_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    last, booth_last = raw.get("last"), raw.get("booth_last")
    return {
        "take": clean_origin(raw.get("take")),
        "settings": clean_settings(raw.get("settings")),
        "last": last[:80] if isinstance(last, str) else "",
        # the vocal booth's: its treatment, chain and moves, its last audition
        "booth": booth.clean_booth(raw.get("booth")),
        "booth_last": booth_last[:80] if isinstance(booth_last, str) else "",
        # the live vocal chain's: its style and its chain (vocal.py)
        "live": vocal.clean_live(raw.get("live")),
    }


# A render writes `last` after it has let go of the studio lock, so two
# renders in flight (a double click) can both be here: one writer at a time.
_LAB_LOCK = threading.Lock()


def write_lab(data_dir: Path, **changes) -> dict:
    """Change `take`, `settings`, `last`, `booth`, `booth_last` and / or
    `live`, leaving the rest. Written
    whole to a temp file and renamed in, so a reader never sees half."""
    with _LAB_LOCK:
        lab = {**read_lab(data_dir), **changes}
        path = lab_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(lab, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return lab


def clip_origin(data_dir: Path, name: str) -> dict:
    return {"kind": "clip", "name": clip_safe(name), "hash": clip_hash(data_dir, name)}


# --- carriers ---------------------------------------------------------------------


def pitch_classes(root: str, scale_name: str) -> list[int]:
    """The session key's pitch classes (0 = C), for a snapped follow."""
    try:
        base = midi(root)
    except (ValueError, KeyError, TypeError):
        base = 0
    return sorted({(base + i) % 12 for i in SCALES.get(scale_name, range(12))})


def _fit_midi(m: float) -> int:
    """A carrier note inside `_MIDI_RANGE`, moved by octaves — never
    clamped to another note."""
    lo, hi = _MIDI_RANGE
    m = int(round(m))
    while m < lo:
        m += 12
    while m > hi:
        m -= 12
    return m


def chord_carrier(
    patch: Patch, chords: list[list[int]], n: int, bpm: float, step: str
) -> np.ndarray:
    """The sound held on each chord of the row in turn, `n` samples. One
    chord lasts the whole take. Several last `step` bars each at `bpm` and
    the row repeats until the take ends ("even": the take shared out once).
    A chord's release rings into the next; an empty chord is a rest."""
    chords = [c[: limits.MAX_VOICE_CHORD_NOTES] for c in chords] or [[45]]
    synth = Synth(patch.tweak(**_HELD))
    if len(chords) == 1:
        span = n
    elif step == "even":
        span = int(math.ceil(n / len(chords)))
    else:
        span = max(1, int(round(float(step) * 4 * 60.0 / bpm * SR)))
    out = np.zeros((n, 2))
    made: dict[tuple, np.ndarray] = {}
    for i, start in enumerate(range(0, n, span)):
        chord = tuple(_fit_midi(m) for m in chords[i % len(chords)])
        length = min(span, n - start)
        if not chord:
            continue
        if (chord, length) not in made:
            seconds = length / SR
            notes = [
                synth.note(m, seconds + _HELD["release"], gate=seconds) for m in chord
            ]
            made[(chord, length)] = sum(notes) / math.sqrt(len(chord))
        seg = made[(chord, length)][: n - start]
        out[start : start + len(seg)] += seg
    return out


def follow_carrier(
    patch: Patch, take: np.ndarray, classes: list[int] | None, octave: int = 0
) -> tuple[np.ndarray, float]:
    """(the sound following the take's own pitch, the share of the take
    that was voiced): one held note at the sung line's median, bent sample
    by sample along `voiced.melody` — as sung, or snapped to `classes`.
    A take with no pitch in it is a `TakeError`."""
    line = voiced.melody(take, pitch_classes=classes)
    if line is None:
        raise TakeError("no clear pitch in this take — type notes instead")
    centre = _fit_midi(line.centre + 12 * octave)
    # the octaves the centre moved by, so the whole line moves with it
    moved = centre - int(round(line.centre))
    bend = np.clip((line.midi + moved - centre) / _BEND, -1.0, 1.0)
    held = patch.tweak(**_HELD, pitch_bend=bend, bend_range=_BEND)
    return Synth(held).note(centre, max(len(take) / SR, 0.05)), line.voiced


def tiled(audio: np.ndarray, n: int, loop: bool = True) -> np.ndarray:
    """`audio` as stereo, repeated or cut to exactly `n` samples. A loop
    tiles as it is; anything else gets `_SEAM_FADE` ramps at its ends
    first, so the seam between two repeats does not click."""
    x = np.asarray(audio, dtype=np.float64)
    x = np.column_stack([x, x]) if x.ndim == 1 else x[:, :2]
    if x.shape[1] == 1:
        x = np.column_stack([x[:, 0], x[:, 0]])
    if not len(x):
        return np.zeros((n, 2))
    if len(x) >= n:
        return x[:n].copy()
    if not loop:
        x = x.copy()
        f = min(int(_SEAM_FADE * SR), len(x) // 2)
        if f:
            ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(f) / f)
            x[:f] *= ramp[:, None]
            x[-f:] *= ramp[::-1, None]
    return np.tile(x, (-(-n // len(x)), 1))[:n]


def load_carrier_clip(data_dir: Path, name: str) -> np.ndarray:
    """A library clip as carrier audio: float at SR, (n, channels). Read as
    a take is — the containment check, a size cap, the sample-rate range —
    and refused the same way (`TakeError`)."""
    root = clip_dir(data_dir).resolve()
    path = (root / f"{clip_safe(name)}.wav").resolve()
    if not name or not path.is_relative_to(root) or not path.is_file():
        raise TakeError("that carrier clip is gone — pick another in the clip library")
    if path.stat().st_size > limits.MAX_UPLOAD_BYTES:
        raise TakeError("that carrier clip is too large")
    try:
        audio, rate = read_pcm16(path.read_bytes())
    except (wave.Error, EOFError, ValueError) as err:
        raise TakeError("that carrier clip is not a readable WAV") from err
    if not limits.MIN_SR <= rate <= limits.MAX_SR:
        raise TakeError(f"unsupported sample rate: {rate} Hz")
    audio = np.asarray(audio, dtype=np.float64)
    audio = audio[:, None] if audio.ndim == 1 else audio
    audio = audio[: int(limits.MAX_CLIP_SECONDS * rate)]
    if rate != SR and len(audio):
        m = max(1, int(round(len(audio) * SR / rate)))
        at, was = np.linspace(0, 1, m), np.linspace(0, 1, len(audio))
        audio = np.column_stack(
            [np.interp(at, was, audio[:, c]) for c in range(audio.shape[1])]
        )
    return audio


def carrier_for(patch: Patch, seconds: float, pitch: str = "A2") -> np.ndarray:
    """A sustained note from the current patch, long enough to cover the take.

    Held rather than plucked: a vocoder can only reveal the modulator's
    articulation where the carrier is actually sounding, so an envelope that
    has already decayed would silence the second half of any phrase.
    """
    return Synth(patch.tweak(**_HELD)).note(pitch, max(seconds, 0.05))


def render_voice(
    patch: Patch,
    take: np.ndarray,
    bands: int = 16,
    pitch: str = "A2",
    chain: list | None = None,
    *,
    carrier: np.ndarray | None = None,
    shift: float = 0.0,
    smooth: float = 40.0,
    sibilance: float = 0.0,
    step=None,
) -> np.ndarray:
    """Vocode the take, then run the FX chain; peak 0.89. The carrier is
    `carrier` (any audio of the take's length, made by one of the carrier
    functions above), else the patch held on `pitch`. `smooth` is the band
    followers' release in ms; `step()` is called once the carrier is there
    and once it is vocoded (a progress line: `events.Task.step`)."""
    if take is None or len(take) == 0:
        raise TakeError("no take recorded yet")
    step = step or (lambda: None)
    seconds = len(take) / SR
    if carrier is None:
        carrier = carrier_for(patch, seconds, pitch)
    if float(np.max(np.abs(carrier), initial=0.0)) < 1e-6:
        raise TakeError("the carrier is silent — nothing for the voice to shape")
    step()
    out = fx.vocoder(
        carrier,
        take,
        bands=int(bands),
        release=float(smooth) / 1000.0,
        shift=float(shift),
        noise=float(sibilance),
    )
    step()
    if chain:
        out = fx.chain(out, chain)
    peak = float(np.max(np.abs(out)))
    return out / peak * 0.89 if peak > 1e-9 else out


# --- the last auditions, and what a kept one says about itself ----------------------

# key -> (the audition's WAV bytes, its clip meta). Four: 30 s of stereo
# 16-bit is 5.3 MB. Module-level like `clips._HASHES`: a key is a hash of
# everything the audio was made from, so two data dirs cannot collide.
RESULTS: LRU[tuple[bytes, dict]] = LRU(4)


def carrier_facts(settings: dict, sound: str, chords: int, voiced_share=None) -> dict:
    """The carrier as a kept result's meta names it."""
    kind = settings["carrier"]
    facts: dict = {"kind": kind}
    if kind in ("sound", "follow"):
        facts["sound"] = sound
    if kind == "sound":
        facts["notes"] = settings["notes"]
        if chords > 1:
            facts["step"] = settings["step"]
    if kind == "follow":
        facts["snap"] = settings["snap"]
        facts["octave"] = settings["octave"]
        facts["voiced"] = round(float(voiced_share or 0.0), 3)
    if kind == "clip":
        facts["clip"] = settings["clip"]
    return facts


def result_meta(
    origin: dict, settings: dict, facts: dict, n: int, tempo: float | None
) -> dict:
    """A kept result's clip meta. `tempo` is the bpm the carrier is locked
    to (None when it is not): the clip then says its `bpm`, and its `bars`
    when its length is whole bars of it within 10 ms."""
    meta: dict = {
        "source": "vocoder",
        "vocoder": {
            "take": dict(origin),
            "carrier": facts,
            "settings": {
                k: settings[k] for k in ("bands", "shift", "smooth", "sibilance")
            },
            "seconds": round(n / SR, 3),
        },
    }
    if tempo:
        meta["bpm"] = float(tempo)
        bar = 4 * 60.0 / float(tempo) * SR
        bars = round(n / bar)
        if bars >= 1 and abs(n - bars * bar) <= 0.010 * SR:
            meta["bars"] = bars
    return meta


# --- the panel ----------------------------------------------------------------------

_CARRIER_LABELS = {
    "sound": "this sound, on notes",
    "follow": "this sound, following the voice",
    "loop": "the Synths loop",
    "clip": "a library clip",
}
_STEP_LABELS = {"0.5": "½ bar", "1": "1 bar", "2": "2 bars", "even": "share the take"}


def carrier_label(kind: str) -> str:
    """A carrier in words ("the Synths loop"): the panel's, and what Now
    playing calls a vocoded take ("Vocoder · the Synths loop")."""
    return _CARRIER_LABELS.get(kind, kind)


def _result_label(meta: dict) -> str:
    """ "“verse 1” over Pad, following the voice": one line for a result."""
    voc = meta.get("vocoder", {})
    take, facts = voc.get("take", {}), voc.get("carrier", {})
    who = f"“{take['name']}”" if take.get("name") else "the recording"
    over = {
        "sound": f"{facts.get('sound', '')} on {facts.get('notes', '')}",
        "follow": f"{facts.get('sound', '')}, following the voice",
        "loop": "the Synths loop",
        "clip": f"“{facts.get('clip', '')}”",
    }.get(facts.get("kind"), "")
    return f"{who} over {over}"


TAKE_COLUMNS = 240  # the take's waveform, as the clip hub's bench draws one


def take_wave(take: np.ndarray | None) -> str:
    """The take's waveform: peak per column, the path the clip
    hub draws (`cliphub.wave_path`, a `0 0 N 2` box), filling the take's
    well. "" without a take."""
    from .cliphub import wave_path

    if take is None or len(take) < TAKE_COLUMNS:
        return ""
    mono = np.abs(take if take.ndim == 1 else take.max(axis=1))
    cols = np.array_split(mono, min(TAKE_COLUMNS, len(mono)))
    pk = np.array([float(c.max()) for c in cols])
    top = float(pk.max()) or 1.0
    return wave_path(pk / top)


def _result_wave(wav: bytes) -> str:
    """The vocoded result's waveform (its well on the page), "" unreadable."""
    try:
        return take_wave(read_wav(wav))
    except TakeError:
        return ""


def band_meter(take: np.ndarray | None, bands: int, shift: float) -> list[dict]:
    """The vocoder's bands as a meter: each band where it sits
    on a log axis (`x`, `w`: 0..100 of the vocoder's range, the edges
    `effects.vocoder` splits at), how loud the take is in it (`level`
    0..1, its share against the loudest band, on a 48 dB scale; 0 without
    a take) and where its carrier band lands after the formant `shift`
    (`cx`, None off the range). A picture of the settings and the take,
    drawn by the panel — nothing it plays reads it."""
    hi = min(fx.VOCODER_HI, SR * 0.45)
    edges = np.geomspace(fx.VOCODER_LO, hi, int(bands) + 1)
    span = math.log(hi / fx.VOCODER_LO)

    def pos(f: float) -> float:
        return 100.0 * math.log(f / fx.VOCODER_LO) / span

    levels = np.zeros(int(bands))
    if take is not None and len(take):
        mono = take if take.ndim == 1 else take.mean(axis=1)
        mono = mono[: SR * 20]  # the first 20 s say it
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
        freqs = np.fft.rfftfreq(len(mono), 1.0 / SR)
        for i in range(int(bands)):
            m = (freqs >= edges[i]) & (freqs < edges[i + 1])
            levels[i] = float(spec[m].mean()) if m.any() else 0.0
        top = float(levels.max())
        if top > 0:
            db = 10 * np.log10(np.maximum(levels / top, 1e-12))
            levels = np.clip(1 + db / 48.0, 0.0, 1.0)
    ratio = 2.0 ** (float(shift) / 12.0)
    out = []
    for i in range(int(bands)):
        lo, hi_ = float(edges[i]), float(edges[i + 1])
        centre = math.sqrt(lo * hi_) * ratio
        inside = fx.VOCODER_LO <= centre <= hi
        x0, w = pos(lo), pos(hi_) - pos(lo)
        mid = x0 + w / 2
        # the band's filter as a bell on the log axis (its skirt one band
        # either side): the bank itself, drawn whether or not there is a take
        pts = [mid + w * (k / 4 - 2) for k in range(17)]
        bell = " ".join(
            f"{x:.2f} {40 - 34 * math.exp(-(((x - mid) / (w * 0.62)) ** 2)):.2f}"
            for x in pts
        )
        out.append(
            {
                "bell": f"M{pts[0]:.2f} 40L{bell}L{pts[-1]:.2f} 40Z",
                "x": round(x0, 2),
                "w": round(w, 2),
                "level": round(float(levels[i]), 3),
                "cx": round(pos(centre), 2) if inside else None,
            }
        )
    return out


def panel_context(data_dir: Path, patch: Patch, arr) -> dict:
    """What `fragments/voice_panel.html` draws: the take (and where it came
    from), the settings, the carrier's facts and the last audition while
    it is still held. Reads the take and voice_lab.json from disk — only
    the Voice page asks."""
    take = load_take(data_dir)
    lab = read_lab(data_dir)
    st, origin = lab["settings"], lab["take"]
    held = RESULTS.get(lab["last"]) if lab["last"] else None
    clip = st["clip"]
    return {
        "has_take": take is not None,
        "take_seconds": round(len(take) / SR, 2) if take is not None else 0.0,
        "take_wave": take_wave(take),
        "vl": {
            "meter": band_meter(take, st["bands"], st["shift"]),
            "take_name": origin["name"] if origin["kind"] == "clip" else "",
            "settings": st,
            "bands": BAND_CHOICES,
            "carriers": [(k, _CARRIER_LABELS[k]) for k in CARRIERS],
            "presets": [
                (k, p["label"], p.get("sound", "")) for k, p in PRESETS.items()
            ],
            "steps": [(k, _STEP_LABELS[k]) for k in STEPS],
            "sound": patch.name,
            "pluck": "pluck" in (patch.osc1, patch.osc2),
            "key": f"{arr.root[:-1]} {arr.scale_name}",
            "bpm": arr.bpm,
            "clip_there": bool(clip) and (clip_dir(data_dir) / f"{clip}.wav").is_file(),
            "result": {
                "key": lab["last"],
                "seconds": held[1]["vocoder"]["seconds"],
                "label": _result_label(held[1]),
                "wave": _result_wave(held[0]),
            }
            if held
            else None,
        },
        "bo": booth.panel_context(data_dir, lab, result_there=held is not None),
    }
