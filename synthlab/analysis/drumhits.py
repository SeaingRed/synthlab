"""A drum recording read back into a step pattern (the Sample Lab's "drum
stem to pattern"): where the hits are, which drum each is, and the grid
they sit on.

NumPy only. It answers for four lanes — kick, snare, closed hat, open hat —
and is built to be explained, not to be clever:

- **Hits** (`find_hits`): the energy in four bands (sub 30-100 Hz, body
  150-500, mid 1-3.5 kHz, high 7-16 kHz) per 5.8 ms frame; an onset is a
  peak in the bands' summed rise. Each hit is measured by how much each
  band *rose* over the 50 ms after it (what was already ringing — a kick's
  tail under a hat — is taken off), and by how long its high band takes to
  die away. Hits more than `_GATE_DB` under the region's loud ones are
  dropped: bleed, a stick click, the separator's residue.
- **Which drum** (`classify`): a hit's *shape* is its four bands relative to
  its strongest. The shapes are clustered (k-means, as many clusters as it
  takes for the hits of each to be alike), so what counts as "the same
  drum" is learned from this recording's hits, not from a table of
  frequencies. A cluster is then named by one look at its centre: sub
  within `_SUB_DB` of its strongest band is a kick; otherwise the high band
  on top is a hat; otherwise a snare (a clap and a rimshot land here too —
  the grid's snare lane is "the backbeat thing").
- **Two drums at once**: a hit gets a second lane when that lane's own band
  is as loud in it as in that lane's solo hits (within `_LAYER_DB` of their
  median — learned again): a kick with a hat on top, a kick under a snare.
  A hat under a snare is not looked for: a snare's own noise covers the
  band a hat would be read in.
- **Open or closed**: the hats' decay times split in two when they really
  are two groups (centres a factor of `_OPEN_RATIO` apart); the long group
  is the open hat.
- **The grid** (`to_pattern`): steps of a 16th from the region's start,
  which the caller puts on a bar line. Off-16ths that are consistently
  late are read as swing (the drum machine's own: a fraction of a step the
  odd steps are late by) and taken out before rounding. A step's velocity
  is its loudest hit against the loud hits of the same lane.

What it cannot do, and says nothing about: toms, crashes and rides (they
become whichever of the three they resemble), flams and rolls faster than
a 16th, and a kit so wet that a hit's bands never fall — the hits are
found on rises. `tools/lab_measure.py` measures precision and recall per
lane against patterns the engine rendered; on a real record there is no
ground truth and none is claimed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from ..core import SR

LANES = ("kick", "snare", "hat", "ohat")
BANDS = ((30.0, 100.0), (150.0, 500.0), (1000.0, 3500.0), (7000.0, 16000.0))
SUB, BODY, MID, HIGH = range(4)
_N_FFT, _HOP = 1024, 256
_RISE_FRAMES = 3  # an attack is over in ~17 ms
_LEAD = 6  # frames of silence put before the audio
_SWEEP = 0.2  # seconds a swept kick's sub may trail its body by
_EDGE = 0.03  # seconds before the end of the audio where nothing is a hit
_ONLY_DB = 20.0  # a band this far over the others is the only one that rose
_RINGING_DB = 9.0  # the last kick's sub within this of the new level: still ringing
_ONSET_FLOOR = 0.12  # summed rise (per 60 dB) under which nothing started
_MIN_GAP = 0.06  # seconds between two hits: a 16th at 250 bpm
_AFTER, _BEFORE = 0.05, 0.03  # seconds a hit's level is read over / compared to
_FLOOR_DB = -40.0  # a band this far under a hit's strongest is "not there"
_GATE_DB = 24.0  # under the loud hits by more: not a hit to write down
_SUB_DB = -8.0  # sub within this of the strongest band: a kick
_ALIKE_DB = 9.0  # RMS distance within a cluster for it to be one drum
_MAX_CLUSTERS = 6
_LAYER_DB = 10.0  # a second lane's band within this of that lane's solo hits
_OVER_DB = 7.0  # noise this far over a bare kick's own: something is on top
_OPEN_RATIO = 2.2  # decay centres this far apart: two kinds of hat
_OPEN_SECONDS = 0.12  # ... and the long one at least this long
_OPEN_ALONE = 0.15  # one kind of hat ringing this long: open (nothing to compare)
_DECAY_DB = 15.0
_SWING_LATE = 0.08  # of a step: later than this, off-16ths are swung
MAX_SWING = 0.6  # the drum machine's own limit
STEPS_PER_BAR = 16


@dataclass(frozen=True)
class Hit:
    time: float  # seconds from the start of the audio
    levels: tuple[float, float, float, float]  # dB each band rose by
    decay: float  # seconds the high band took to fall `_DECAY_DB`
    lanes: tuple[str, ...] = ()  # filled by `classify`
    until: float = 0.0  # seconds: the next hit, or the end of the audio

    @property
    def level(self) -> float:
        return max(self.levels)

    def of(self, lane: str) -> float:
        """The hit's level in the band that lane is heard in."""
        if lane == "kick":
            return self.levels[SUB]
        if lane == "snare":
            return max(self.levels[BODY], self.levels[MID])
        return self.levels[HIGH]


@dataclass(frozen=True)
class Pattern:
    """A transcription on the drum grid: `steps` as the Arrangement holds
    them ([lane, step]), `vel` its velocity lane, `swing` its swing."""

    steps: list[list] = field(default_factory=list)
    vel: dict[str, float] = field(default_factory=dict)
    swing: float = 0.0
    bars: int = 1
    steps_per_bar: int = STEPS_PER_BAR
    counts: dict[str, int] = field(default_factory=dict)  # hits per lane

    def row(self, lane: str) -> str:
        """One lane as the notation writes it: "x...x...x...x..."."""
        on = {s for v, s in self.steps if v == lane}
        return "".join(
            "x" if i in on else "." for i in range(self.bars * self.steps_per_bar)
        )


def _band_energy(audio: np.ndarray, sr: int) -> np.ndarray:
    """(frames, 4): the energy of each band in each frame, after `_LEAD`
    frames of silence (a hit on the very first sample needs something to
    rise from). Frame i ends at sample (i - _LEAD + 1) * hop, so a sound
    starting at sample s first shows in frame s // hop + _LEAD."""
    x = np.asarray(audio, dtype=np.float64)
    mono = x.mean(axis=1) if x.ndim == 2 else x.ravel()
    mono = np.clip(np.nan_to_num(mono, nan=0.0, posinf=0.0, neginf=0.0), -4.0, 4.0)
    if len(mono) < _HOP * 4:
        return np.zeros((0, len(BANDS)))
    lead = np.zeros(_N_FFT - _HOP + _LEAD * _HOP)
    mono = np.concatenate([lead, mono, np.zeros(_N_FFT)])
    frames = np.lib.stride_tricks.sliding_window_view(mono, _N_FFT)[::_HOP]
    freqs = np.fft.rfftfreq(_N_FFT, 1.0 / sr)
    masks = [(freqs >= lo) & (freqs < hi) for lo, hi in BANDS]
    win = np.hanning(_N_FFT)
    out = np.empty((len(frames), len(BANDS)))
    for a in range(0, len(frames), 2048):
        power = np.abs(np.fft.rfft(frames[a : a + 2048] * win, axis=1)) ** 2
        for b, mask in enumerate(masks):
            out[a : a + 2048, b] = power[:, mask].sum(axis=1)
    return out


def _db(energy) -> np.ndarray:
    return 10.0 * np.log10(np.maximum(energy, 1e-9))


def find_hits(audio: np.ndarray, sr: int = SR) -> list[Hit]:
    """Every drum hit in `audio`, in time order, measured and not yet
    named (the module's docstring). [] for silence or a scrap."""
    energy = _band_energy(audio, sr)
    if not len(energy):
        return []
    level = _db(energy)
    level = np.maximum(level, level.max(axis=0) - 60.0)  # a floor per band
    k = _RISE_FRAMES
    band_rise = np.zeros_like(level)
    band_rise[k:] = np.maximum(level[k:] - level[:-k], 0.0)
    rise = band_rise.sum(axis=1) / 60.0
    gap = max(1, int(_MIN_GAP * sr / _HOP))
    peaks: list[int] = []
    for i in range(1, len(rise) - 1):
        if rise[i] <= _ONSET_FLOOR or rise[i] < rise[i - 1] or rise[i] <= rise[i + 1]:
            continue
        if peaks and i - peaks[-1] < gap:
            if rise[i] > rise[peaks[-1]]:
                peaks[-1] = i
            continue
        peaks.append(i)
    after, before = int(_AFTER * sr / _HOP), int(_BEFORE * sr / _HOP)

    sweep = int(_SWEEP * sr / _HOP)

    def rose(start: int, end: int) -> np.ndarray:
        """dB each band rose by over [start, end) against just before."""
        was = energy[max(0, start - before) : max(1, start)].mean(axis=0)
        now = energy[start : max(start + 1, end)].mean(axis=0)
        return _db(np.maximum(now - was, 0.0))

    def sub_rose(start: int, end: int, body: float) -> float:
        """The sub band's rise, peak against peak, over a longer look.
        Less than a doubling is no new kick. And while the last kick still
        rings (its sub within `_RINGING_DB` over the last 100 ms), sub alone
        proves nothing — a new kick's sweep beats against the old tail, and
        the sub dips and comes back 60 ms later, under whatever hat is
        playing then: there a kick must also show in its `body`."""
        was = energy[max(0, start - before) : max(1, start), SUB].max()
        now = energy[start : max(start + 1, end), SUB].max()
        if now < 2.0 * was:
            return -90.0
        rise = float(_db(now - was))
        ringing = energy[max(0, start - sweep // 2) : max(1, start), SUB].max()
        if _db(now) - _db(ringing) < _RINGING_DB and body < rise - _ONLY_DB:
            return -90.0
        return rise

    # A synthesized kick is a sine swept down from a few hundred Hz: its
    # body sounds at once and its sub is still arriving 40 ms later —
    # sometimes as a second onset with nothing but sub in it. That onset is
    # the same drum (it is dropped here), and every hit's sub is read over
    # `_SWEEP`, not over the 50 ms the other bands are.
    kept: list[int] = []
    bodies: dict[int, float] = {}
    for i in peaks:
        lv = rose(i - k, i - k + after)
        only_sub = lv[SUB] - lv[BODY:].max() >= _ONLY_DB
        if only_sub and any(
            i - p <= sweep and bodies[p] >= lv[SUB] - _ONLY_DB for p in kept[-4:]
        ):
            continue
        kept.append(i)
        bodies[i] = float(lv[BODY])
    tail = int(0.5 * sr / _HOP)
    total = len(audio) / sr
    hits: list[Hit] = []
    for j, i in enumerate(kept):
        start = max(0, i - k)  # the rise peaks as the attack ends
        nxt = kept[j + 1] - k if j + 1 < len(kept) else len(energy)
        levels = rose(start, min(start + after, nxt))
        levels[SUB] = sub_rose(start, min(start + sweep, nxt), float(levels[BODY]))
        # the high band's own decay: what was ringing before the hit (a
        # snare's tail under a hat) is taken off, or it reads as a long hat
        was = energy[max(0, start - before) : max(1, start), HIGH].mean()
        high = energy[start : max(start + 1, min(nxt, start + tail)), HIGH] - was
        top = int(np.argmax(high[: k + 3]))
        under = np.flatnonzero(high[top:] < high[top] * 10.0 ** (-_DECAY_DB / 10.0))
        decay = (int(under[0]) if len(under) else len(high) - top) * _HOP / sr
        # frame `start` is the last quiet one: the sound enters the next
        time = max(0.0, (start + 1 - _LEAD) * _HOP / sr)
        if time > total - _EDGE:
            continue  # the cut at the end of the audio is a click, not a drum
        hits.append(
            Hit(
                time=time,
                levels=tuple(float(v) for v in levels),
                decay=decay,
                until=min(total, (nxt + 1 - _LEAD) * _HOP / sr),
            )
        )
    if not hits:
        return []
    loud = float(np.percentile([h.level for h in hits], 90))
    return [h for h in hits if h.level >= loud - _GATE_DB]


def _shapes(hits: list[Hit]) -> np.ndarray:
    lv = np.array([h.levels for h in hits])
    return np.maximum(lv - lv.max(axis=1, keepdims=True), _FLOOR_DB)


def _kmeans(x: np.ndarray, k: int) -> np.ndarray:
    """Labels for `x`'s rows in `k` clusters. Seeded by the farthest-point
    rule from the first row, so the same hits always cluster the same."""
    centres = [x[0]]
    for _ in range(1, k):
        d = np.min([np.sum((x - c) ** 2, axis=1) for c in centres], axis=0)
        centres.append(x[int(np.argmax(d))])
    c = np.array(centres, dtype=np.float64)
    labels = np.full(len(x), -1)
    for _round in range(32):
        new = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(axis=2), axis=1)
        if np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            if np.any(labels == j):
                c[j] = x[labels == j].mean(axis=0)
    return labels


def _two_groups(values: np.ndarray) -> tuple[float, float, float] | None:
    """Sorted `values` cut where two groups fit them best (least squared
    spread, in logs): (the edge between them, the low group's median, the
    high group's). None for fewer than two values."""
    v = np.sort(np.log(np.maximum(np.asarray(values, dtype=np.float64), 1e-4)))
    if len(v) < 2:
        return None
    cost = [np.var(v[:i]) * i + np.var(v[i:]) * (len(v) - i) for i in range(1, len(v))]
    i = int(np.argmin(cost)) + 1
    edge = float(np.exp((v[i - 1] + v[i]) / 2.0))
    return edge, float(np.exp(np.median(v[:i]))), float(np.exp(np.median(v[i:])))


def _clusters(shapes: np.ndarray) -> np.ndarray:
    """The hits grouped into as few clusters as leave each one alike
    (`_ALIKE_DB`)."""
    labels = np.zeros(len(shapes), dtype=int)
    for k in range(1, min(_MAX_CLUSTERS, len(shapes)) + 1):
        labels = _kmeans(shapes, k)
        spread = [
            np.sqrt(np.mean(np.sum((shapes[labels == j] - m) ** 2, axis=1)))
            for j in range(k)
            if np.any(labels == j)
            for m in [shapes[labels == j].mean(axis=0)]
        ]
        if max(spread) <= _ALIKE_DB:
            break
    return labels


def _name(centre: np.ndarray) -> str:
    """A cluster's drum, from its centre's shape (the docstring's rule)."""
    if centre[SUB] >= _SUB_DB:
        return "kick"
    if centre[HIGH] >= max(centre[BODY], centre[MID]):
        return "hat"
    return "snare"


def classify(hits: list[Hit]) -> list[Hit]:
    """`hits` with their `lanes` named: the drum each mostly is, then any
    second drum sounding with it, then open or closed for the hats."""
    if not hits:
        return []
    shapes = _shapes(hits)
    labels = _clusters(shapes)
    primary = [""] * len(hits)
    for j in np.unique(labels):
        name = _name(shapes[labels == j].mean(axis=0))
        for i in np.flatnonzero(labels == j):
            primary[i] = name
    named = list(zip(hits, primary, strict=True))
    solo = {
        lane: float(np.median([h.of(lane) for h, p in named if p == lane]))
        for lane in ("kick", "snare", "hat")
        if lane in primary
    }
    # What a kick has of noise when nothing is on top of it, relative to
    # its own sub: the quietest of the kick clusters, or — one cluster —
    # its quietest hits. A kick this recording's kicks never sound like.
    noise = np.array([[h.levels[MID], h.levels[HIGH]] for h in hits])
    noise = np.maximum(noise - np.array([[h.levels[SUB]] for h in hits]), _FLOOR_DB)
    kicks = np.array([p == "kick" for p in primary])
    bare = np.zeros(2)
    if kicks.any():
        groups = [noise[kicks & (labels == j)] for j in np.unique(labels[kicks])]
        if len(groups) > 1:
            bare = np.min([np.median(g, axis=0) for g in groups], axis=0)
        else:
            bare = np.percentile(groups[0], 10, axis=0)
    snares = [
        min(h.levels[MID], h.levels[HIGH])
        for h, p in zip(hits, primary, strict=True)
        if p == "snare"
    ]
    lanes: list[list[str]] = []
    for i, (h, p) in enumerate(zip(hits, primary, strict=True)):
        mine = [p]
        if p != "kick" and "kick" in solo and h.of("kick") >= solo["kick"] - _LAYER_DB:
            mine.insert(0, "kick")
        if p == "kick":
            over_mid, over_high = noise[i] - bare
            # a snare is noise in both bands, well over the kick's own
            # click — and as loud as the snares heard alone, when any were
            as_snare = min(h.levels[MID], h.levels[HIGH])
            if (
                over_mid >= _OVER_DB
                and over_high >= _OVER_DB
                and (not snares or as_snare >= float(np.median(snares)) - _LAYER_DB)
            ):
                mine.append("snare")
            elif "hat" in solo:
                if h.of("hat") >= solo["hat"] - _LAYER_DB:
                    mine.append("hat")
            elif over_high >= _OVER_DB:
                mine.append("hat")
        lanes.append(mine)
    # open or closed: the hats' decay, when it really falls in two groups
    hat_at = [i for i, mine in enumerate(lanes) if "hat" in mine]
    split = _two_groups(np.array([hits[i].decay for i in hat_at]))
    edge = None
    if split is not None and split[2] >= max(_OPEN_RATIO * split[1], _OPEN_SECONDS):
        edge = split[0]
    elif hat_at and np.median([hits[i].decay for i in hat_at]) >= _OPEN_ALONE:
        edge = 0.0  # one kind of hat, and it rings: all open
    if edge is not None:
        for i in hat_at:
            if hits[i].decay > edge:
                lanes[i][lanes[i].index("hat")] = "ohat"
    return [replace(h, lanes=tuple(mine)) for h, mine in zip(hits, lanes, strict=True)]


def swing_of(positions: np.ndarray) -> float:
    """The swing a set of hit positions (in steps) was played with: how
    late the off-16ths run, as the drum machine counts it (a fraction of a
    step every odd step is late by), 0 when they are on the grid or there
    are too few to say."""
    frac = np.mod(positions, 2.0)  # 0 = an eighth, 1 = the 16th between
    off = frac[(frac > 0.85) & (frac < 1.0 + MAX_SWING + 0.05)] - 1.0
    if len(off) < 3:
        return 0.0
    late = float(np.median(off))
    return round(min(MAX_SWING, late), 2) if late > _SWING_LATE else 0.0


def nearest_step(position: float, swing: float = 0.0) -> int:
    """The step a hit at `position` (in steps) belongs to on a grid whose
    odd steps sound `swing` of a step late: the nearer of the eighth and
    the swung 16th either side of it."""
    even = 2 * int(np.floor(position / 2.0 + 0.5))
    odd = 2 * int(np.floor((position - swing - 1.0) / 2.0 + 0.5)) + 1
    return even if abs(position - even) <= abs(position - odd - swing) else odd


def to_pattern(
    hits: list[Hit], bar_seconds: float, bars: int, steps_per_bar: int = STEPS_PER_BAR
) -> Pattern:
    """Classified hits on the grid: `bars` bars of `bar_seconds` from time
    0, `steps_per_bar` steps each (the docstring's "The grid")."""
    bars = max(1, int(bars))
    total = bars * steps_per_bar
    if not hits or bar_seconds <= 0:
        return Pattern(bars=bars, steps_per_bar=steps_per_bar)
    step = bar_seconds / steps_per_bar
    pos = np.array([h.time / step for h in hits])
    swing = swing_of(pos) if steps_per_bar == STEPS_PER_BAR else 0.0
    loud = {
        lane: float(np.percentile([h.of(lane) for h in hits if lane in h.lanes], 90))
        for lane in LANES
        if any(lane in h.lanes for h in hits)
    }
    best: dict[tuple[str, int], float] = {}
    vel: dict[int, float] = {}
    for h, p in zip(hits, pos, strict=True):
        s = nearest_step(float(p), swing)
        if not 0 <= s < total:
            continue
        for lane in h.lanes:
            gain = 10.0 ** ((h.of(lane) - loud[lane]) / 20.0)
            best[(lane, s)] = max(best.get((lane, s), 0.0), gain)
            vel[s] = max(vel.get(s, 0.0), gain)
    steps = sorted([lane, s] for lane, s in best)
    counts = {lane: sum(1 for v, _s in steps if v == lane) for lane in LANES}
    return Pattern(
        steps=steps,
        vel={
            str(s): round(min(1.0, max(0.15, v)) * 20) / 20
            for s, v in sorted(vel.items())
            if v < 0.975
        },
        swing=swing,
        bars=bars,
        steps_per_bar=steps_per_bar,
        counts={k: v for k, v in counts.items() if v},
    )


def transcribe(
    audio: np.ndarray, bar_seconds: float, bars: int, sr: int = SR
) -> tuple[Pattern, list[Hit]]:
    """(the pattern, the classified hits) of `audio`, whose first sample is
    a bar line and whose bars are `bar_seconds` long."""
    hits = classify(find_hits(audio, sr))
    return to_pattern(hits, bar_seconds, bars), hits


_CUT = {"kick": 0.5, "snare": 0.4, "hat": 0.15, "ohat": 0.6}  # seconds kept
_PRE = 0.003  # seconds before the onset: the attack whole


def exemplars(hits: list[Hit]) -> dict[str, tuple[float, float]]:
    """One clean hit per lane to cut out as a one-shot: (start, end) in
    seconds. "Clean" is a hit that is that drum alone, with the most room
    before the next hit, the louder of two as alike; a lane heard only
    under another drum has no exemplar."""
    out: dict[str, tuple[float, float]] = {}
    for lane in LANES:
        alone = [h for h in hits if h.lanes == (lane,)]
        if not alone:
            continue
        loud = max(h.of(lane) for h in alone)
        h = max(
            alone,
            key=lambda h: (
                min(h.until - h.time, _CUT[lane]) / _CUT[lane]
                - (loud - h.of(lane)) / 40.0
            ),
        )
        start = max(0.0, h.time - _PRE)
        out[lane] = (start, min(h.until - 0.004, h.time + _CUT[lane]))
    return out
