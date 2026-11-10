"""
demo_track.py — proof that the whole thing works.

Builds a 16-bar piece of electronic music entirely from synthesised sound:
no samples, no external instruments. Run it and you get demo_track.wav.

    python demo_track.py
"""

from synthlab import Song, Synth, audio, preset
from synthlab import effects as fx
from synthlab.sequencer import chord, euclid, midi, scale

BPM = 124
BAR = 4  # beats per bar
song = Song(bpm=BPM)

# --- harmony: Am - F - Cmaj7 - G  (one chord per bar, repeating) ------------
PROG = [("A3", "min9"), ("F3", "maj9"), ("C4", "maj7"), ("G3", "add9")]
ROOTS = ["A2", "F2", "C3", "G2"]

# ---------------------------------------------------------------------------
# instruments
# ---------------------------------------------------------------------------
pad = Synth(preset("pad_warm").tweak(cutoff=520, unison=3, release=3.0))
bass = Synth(
    preset("bass_reese").tweak(cutoff=260, resonance=0.35, drive=2.6, sub_level=0.55)
)
pluck = Synth(preset("pluck_glass").tweak(decay=0.28, release=0.45, resonance=0.4))
lead = Synth(
    preset("lead_supersaw").tweak(
        cutoff=1100, filter_env=5200, unison=3, unison_detune=16
    )
)
stab = Synth(preset("stab_hoover").tweak(release=0.5))

# ---------------------------------------------------------------------------
# tracks (each with its own effects rack)
# ---------------------------------------------------------------------------
KICK_PATTERN = "x...x...x...x..."
kick_times = song.kick_beats(KICK_PATTERN, bars=16)

song.track(
    "pad",
    gain=0.46,
    effects=[
        fx.highpass(160),
        fx.chorus(rate=0.35, depth=4.0, mix=0.5),
        fx.reverb(decay=3.4, mix=0.42, size=1.0, damping=0.5),
        fx.sidechain(kick_times, depth=0.45, release=0.3),
    ],
)
song.track(
    "bass",
    gain=0.85,
    effects=[
        fx.highpass(42),
        fx.drive(1.8, mix=0.6),
        fx.sidechain(kick_times, depth=0.7, release=0.24),
    ],
)
song.track(
    "arp",
    gain=0.40,
    effects=[
        fx.delay(time=60 / BPM * 0.75, feedback=0.42, mix=0.34, ping_pong=True),
        fx.reverb(decay=2.0, mix=0.24),
        fx.width(1.5),
    ],
)
song.track(
    "lead",
    gain=0.58,
    effects=[
        fx.delay(time=60 / BPM * 0.5, feedback=0.3, mix=0.24),
        fx.reverb(decay=2.4, mix=0.22),
        fx.width(1.3),
        fx.sidechain(kick_times, depth=0.3, release=0.22),
    ],
)
song.track(
    "stabs",
    gain=0.3,
    effects=[
        fx.delay(time=60 / BPM * 0.375, feedback=0.35, mix=0.3, ping_pong=True),
        fx.reverb(decay=1.6, mix=0.25),
    ],
)
song.track("drums", gain=0.95, effects=[fx.compress(-16, 3.0), fx.drive(1.3, mix=0.35)])
song.track(
    "perc",
    gain=0.5,
    effects=[
        fx.delay(time=60 / BPM * 0.375, feedback=0.25, mix=0.22, ping_pong=True),
        fx.reverb(decay=1.2, mix=0.2),
    ],
)

# ---------------------------------------------------------------------------
# arrangement
# ---------------------------------------------------------------------------
for bar in range(16):
    b0 = song.bar(bar)
    root_name, quality = PROG[bar % 4]
    notes = chord(root_name, quality)

    # --- pads: whole bar, gently strummed, all the way through
    song.play(
        "pad",
        pad.chord(
            notes,
            BAR * song.beat_seconds * 1.05,
            velocity=0.55,
            spread=0.8,
            strum=0.045,
        ),
        b0,
    )

    # --- bass: from bar 2, offbeat driving pattern
    if bar >= 2:
        root = ROOTS[bar % 4]
        pattern = [
            (root, 0.75),
            (root, 0.25),
            (root, 0.5),
            ("-", 0.5),
            (root, 0.75),
            (root, 0.25),
            (midi(root) + 12, 0.5),
            ("-", 0.5),
        ]
        song.notes("bass", bass, pattern, start_beat=b0, gate=0.85, velocity=0.9)

    # --- drums
    if bar >= 4:
        song.drum(
            "drums",
            "kick",
            KICK_PATTERN,
            start_beat=b0,
            tune=50,
            decay=0.45,
            punch=1.0,
            gain=1.0,
        )
        hat_pattern = "..x...x...x...x." if bar % 4 != 3 else "..x.x.x.x.x.x.xx"
        song.drum(
            "drums",
            "hat",
            hat_pattern,
            start_beat=b0,
            gain=0.9,
            humanize=0.006,
            decay=0.045,
        )
    if bar >= 6:
        song.drum("drums", "clap", "....x.......x...", start_beat=b0, gain=0.95)
    if bar >= 8:
        song.drum(
            "perc",
            "rim",
            euclid(5, 16, rotate=2),
            start_beat=b0,
            gain=0.45,
            humanize=0.008,
        )
        song.drum("drums", "ohat", "......x.......x.", start_beat=b0, gain=0.5)
    if bar in (0, 8):
        song.drum("drums", "crash", "x", start_beat=b0, gain=0.5, decay=2.4)

    # --- arpeggio: bars 4-15
    if bar >= 4:
        song.arp(
            "arp",
            pluck,
            notes,
            start_beat=b0,
            beats=BAR,
            step=0.25,
            mode="updown" if bar % 8 < 4 else "up",
            octaves=2,
            velocity=0.75,
        )

    # --- hoover stabs on the turnaround
    if bar % 8 == 7:
        for off in (2.0, 2.75, 3.5):
            song.play("stabs", stab.chord(notes[:3], 0.5, velocity=0.7), b0 + off)

# --- lead melody over the last 8 bars ---------------------------------------
sc = scale("A4", "minor", octaves=2)
MELODY = [
    (sc[0], 1.0),
    (sc[2], 0.5),
    (sc[4], 1.5),
    (sc[3], 0.5),
    (sc[2], 0.5),
    (sc[0], 2.0),
    ("-", 2.0),
    (sc[4], 1.0),
    (sc[5], 0.5),
    (sc[7], 1.5),
    (sc[6], 0.5),
    (sc[4], 0.5),
    (sc[2], 2.0),
    ("-", 2.0),
    (sc[7], 0.75),
    (sc[6], 0.25),
    (sc[4], 1.0),
    (sc[5], 0.5),
    (sc[4], 0.5),
    (sc[2], 1.0),
    (sc[0], 2.0),
    ("-", 2.0),
    (sc[4], 1.0),
    (sc[7], 1.0),
    (sc[9], 2.0),
    (sc[7], 1.0),
    (sc[4], 3.0),
]
song.notes("lead", lead, MELODY, start_beat=song.bar(8), gate=0.85, velocity=0.85)

# ---------------------------------------------------------------------------
# mixdown
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mix = song.render(
        master_fx=[
            fx.highpass(28),
            fx.compress(threshold=-13, ratio=2.4, attack=0.012, release=0.2),
            fx.air(fc=6500, gain_db=4.5),
            fx.weight(fc=110, gain_db=1.5),
            fx.width(1.18),
            fx.limit(ceiling=0.95, loop=False),  # a song with an ending
        ]
    )
    path = audio.save("demo_track.wav", mix, bit_depth=24, normalize_to=0.92)
    print(f"wrote {path}")
    for k, v in audio.analyse(mix).items():
        print(f"  {k:>20}: {v}")
