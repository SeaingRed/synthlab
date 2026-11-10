"""FX-parameter automation: a song section's lanes on a unit of a track's
FX chain (`curves.FX_LANES`, target "fx:<unit id>:<kind>:<param>").

A chain is otherwise built once per render as `partial`s with scalar
parameters (`fxrack.build_chain`). Here a whitelisted parameter is a
per-sample array instead, and each one is the unit itself with its slider
moving — never a crossfade between two renders, never the chain re-run in
pieces:

- `mix` (drive, clip, phaser, chorus, flanger, delay, reverb, fold,
  ringmod, comb, granular, shimmer, and the studio tricks below): every
  one of these ends in `dry * (1 - mix) + wet * mix`, so the unit runs
  once fully wet and the blend is
  applied per sample. Exact: the same arithmetic as the scalar unit, and a
  lane that stands still is that unit bit for bit.
- drive `amount`: `effects.drive_shape` takes a per-sample drive in every
  mode (soft is `core.soft_clip`, as the Patch's own `drive` curve runs).
  Memoryless, exact. Its lane spans 1-4, the slider's range before it grew
  to 12 (`curves.FX_LANE_RANGES`): a drawn lane keeps its values.
- clip `drive`: the dB of gain per sample. Memoryless, exact.
- delay `feedback`: y[i] = x[i] + feedback[i] * y[i - d]
  (`effects._delay_line_numpy`). Exact, per sample.
- the studio tricks' `mix` (doubler, rotary, tape_echo, telephone, pitch,
  plate) as every other mix; tape_echo `feedback` and rotary `speed` are
  arrays the unit takes as it comes (the loop's gain per sample; the
  rotors' target, slewed by `effects.rotor_rate` — the chorale / tremolo
  switch). One code path for a scalar and an array: exact.
- the FX2 units': every `mix` as above; the
  auto-filter's `cutoff` and `depth` (its cutoff curve takes arrays, then
  the swept filter's blocks as a lowpass lane's — but not `BLOCKED`: a
  lead-in would move its LFO, so a restart may land up to 63 samples
  late), the tremolo's `depth`, the tape's `drive` (dB a sample), the
  ping-pong's `feedback` (a sample's loop gain, as the delay's) and the
  exciter's `amount` (its blend): one code path for a scalar and an
  array, standing still bit for bit the scalar unit.
- the dynamics': the OTT's `depth` (each band's blend with
  its own gain, per sample), the limiter's `gain` (dB in, per sample —
  the held gain follows it, so a lane pushing a build louder is held at
  the ceiling as the slider would be), the transient shaper's `attack`
  and `sustain` (each its detector's dB times the value, per sample):
  memoryless in the parameter, exact, standing still bit for bit.
- lowpass / highpass `cutoff` and `q`: `core.biquad`'s swept state-variable
  filter, one coefficient set per 64-sample block (1.45 ms) — the engine's
  own swept filter, the one a Patch's cutoff curve runs.
- eq `low` / `mid_gain` / `high` / `mid_freq`: the same filter with the
  band's gain mixed from its outputs (`effects._eq_swept`), per block.
- gate / sidechain / pump `depth`: each of these is a gain on the clock,
  `1 - depth * (1 - g)` for an envelope `g` that does not depend on the
  depth (the gate's smoothed steps; the duck's shape under its triggers).
  The envelope is computed once and the depth scales it per sample.
  Exact, memoryless.
- beat_repeat / stop `mix`: the unit's own blend inside its regions
  (`dry * (1 - ramp * mix) + wet * mix`, `OWN_MIX`) with the mix read at
  each output sample. Which steps repeat, and where the tape stops, are
  decided by the pattern, the chance rolls and the period — never by the
  mix — so this is the unit with its slider moving and nothing else. It
  is how a stutter or a stop is placed on one bar of a section: the rack
  rests at 0 and the lane lifts it. No time parameter is on the list.

Measured (`tests/studio/test_song_fx_lanes.py`, which holds the table): a
lane that stands still is the scalar unit bit for bit (the EQ's gains to
1e-13: its swept form is another realisation of the same filter). Against
the same sweep with a coefficient set per sample, the 64-sample blocks sit
39 dB (the EQ's mid frequency, 100 Hz - 8 kHz in two seconds) to 78 dB
below the signal on a full-range lane; a filter's cutoff, 46 dB. The
depths and the two time remaps' mixes have no block and no state: a moving
lane is the hand-computed per-sample formula to rounding (1e-15), and each
sample is the scalar unit's at that sample's value.

**Tails.** A stem's chain runs once over its cycle and what rings past it
(`render._cycle_stems`); that ring is folded into whatever plays next. Two
things can play next, and a moving parameter makes them differ:

- the section's own next pass (a repeat, a looped section): the lane has
  started again, so past the cycle's end the parameter *replays the lane*
  (`loop`). For a linear unit that is the two passes rendered in one
  piece: a linear system's response to (ring + next pass) is the sum of
  its responses, whatever its parameters do, as long as they do the same
  thing in both renders. Measured: rounding (-250 dB) for the reverb, the
  delay and the comb; the swept filters' own block error and nothing more
  (`_unit` puts a block boundary on the cycle's end);
- another section (`out`): there the unit sits at the rack's own value, so
  past the cycle's end the parameter *rests at it*. As exact, when the
  section the ring enters does not automate that parameter. When it does
  — or when a section without the lane rings into one that has it — the
  ring alone (the section's own notes follow its lane) plays at the rack's
  value instead of that lane's: -11.5 dB of the pass on about the worst
  case, a reverb's mix at 1 against a rack at 0.25.

The fold itself is as good as the unit's with its sliders still, which for
some units was never exact and is not made so here: a drive is not linear,
a phaser's, chorus's or flanger's LFO and a ring mod's carrier restart
with the stem, granular and shimmer read the whole buffer. A ring longer
than one cycle is cut, as every stem's is.

The clocked units (gate, sidechain, pump, beat repeat, stop) are linear
and periodic on the clock, and a section's cycle is a whole number of
their periods, so both tails are exact for them (measured: rounding) —
with the one drift the gate always had: its 16th is a whole number of
samples, so at a tempo whose 16th is not (120 bpm: 5512.5) its pattern
slips 8 samples a bar against the clock, lane or no lane.
"""

from __future__ import annotations

import numpy as np

from .. import effects as fx
from ..core import SR
from .curves import fx_lane_range, parse_fx
from .fxrack import build_chain, unit_args
from .state import Arrangement


def resolve(units: list[dict], lanes) -> dict[int, dict[str, list]]:
    """{unit index: {param: points}} for the lanes (`[[target, points]]`)
    whose unit is in the clean chain `units`: the unit with the lane's id,
    still of the lane's kind. Any other lane is missing, and moves nothing."""
    out: dict[int, dict[str, list]] = {}
    for target, points in lanes or ():
        named = parse_fx(target)
        if named is None or len(points) < 2:
            continue
        uid, kind, param = named
        for i, u in enumerate(units):
            if u.get("id") == uid and u["kind"] == kind:
                out.setdefault(i, {})[param] = points
                break
    return out


def values(
    points, lo: float, hi: float, n: int, length: int, rest: float | None = None
) -> np.ndarray:
    """A lane over `length` samples of a stem whose cycle is `n`: sample i
    of the cycle sits at i / (n - 1) (`curves.lane_array`'s reading), and
    past the cycle the lane starts again — or, with `rest`, sits there."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    pos = (np.arange(length) % n) / max(1, n - 1)
    v = lo + np.interp(pos, xs, ys) * (hi - lo)
    if rest is not None:
        v[n:] = rest
    return v


# the units whose lanes run `core.biquad`'s swept filter, and its block
BLOCKED = frozenset({"lowpass", "highpass", "eq"})
BLOCK = 64
# the units that blend inside their own regions (a repeat's, a stop's
# window) rather than over the whole buffer: they take the mix array
# themselves, where every other `mix` is `dry * (1 - mix) + wet * mix`
OWN_MIX = frozenset({"beat_repeat", "stop"})


def _unit(x: np.ndarray, u: dict, arr: Arrangement, arrays: dict, n: int) -> np.ndarray:
    """One unit with some of its parameters per-sample arrays. A swept
    filter reads one value per 64-sample block, on a grid that starts with
    its input: the input is led in by silence (which a filter at rest
    passes as silence) so that a block starts on the cycle's end `n` — the
    lane's restart, or its return to rest, lands on that sample rather
    than up to 63 samples late."""
    lead = -n % BLOCK if u["kind"] in BLOCKED else 0
    if lead:
        x = np.concatenate([np.zeros((lead, *x.shape[1:])), x])
        arrays = {
            k: np.concatenate([np.full(lead, v[0]), v]) for k, v in arrays.items()
        }
        return _unit(x, u, arr, arrays, n + lead)[lead:]
    # the clocked triggers (a sidechain's, a pump's) reach the buffer's end
    name, kw = unit_args(u, arr, len(x) / SR)
    mix = None if u["kind"] in OWN_MIX else arrays.get("mix")
    kw |= {k: v for k, v in arrays.items() if k != "mix" or mix is None}
    if mix is None:
        return getattr(fx, name)(**kw)(x)
    st = fx.to_stereo(x)
    wet = fx.to_stereo(getattr(fx, name)(**{**kw, "mix": 1.0})(st))
    m = mix[:, None]
    return st * (1 - m) + wet * m


def render(
    dry: np.ndarray, units: list[dict], arr: Arrangement, lanes, n: int, span: float
) -> tuple[np.ndarray, np.ndarray | None]:
    """A stem's clean chain over `dry` (stereo: its cycle of `n` samples
    and what rings past it) under the section's FX lanes: `(loop, out)`,
    each as long as `dry` — past the cycle `loop` replays the lanes and
    `out` rests at the rack's values (the module docstring's two tails).
    No lane finds its unit: the plain chain, and `out` None. The units
    before the first automated one run once for both."""
    found = resolve(units, lanes)
    if not found:
        return fx.chain(dry, build_chain(units, arr, span=span)), None
    first = min(found)
    head = fx.chain(dry, build_chain(units[:first], arr, span=span))

    def rest_of(resting: bool) -> np.ndarray:
        x = head
        for i in range(first, len(units)):
            u = units[i]
            if i not in found:
                x = fx.chain(x, build_chain([u], arr, span=span))
                continue
            arrays = {
                param: values(
                    points,
                    *fx_lane_range(u["kind"], param),
                    n,
                    len(dry),
                    u["params"][param] if resting else None,
                )
                for param, points in found[i].items()
            }
            x = _unit(x, u, arr, arrays, n)
        return np.asarray(x)

    loop = rest_of(False)
    return loop, (rest_of(True) if len(dry) > n else None)
