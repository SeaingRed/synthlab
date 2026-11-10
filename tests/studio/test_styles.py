"""The styles' starters: each style's **signature checklist** as machine
checks over the song model (`gen/checklist.py`), for five seeds; the
"what is missing" checklist empty; and the plan rendering with every pass
under the 0.97 ceiling.

These prove the grammar is PRESENT — the rolling bass never on the kick's
step, a gate on a pad with a lane on its depth, a breakdown of sixteen
bars without drums. They do not prove it is music: nobody has listened.

Every check is held: the two that waited on the per-hit drum table
(trance's roll reaching 32nds with the snare's tune rising, melodic
techno's hits with a chance) were strict `xfail`s until the beat workflow's
package gave a hit its own ratchet, tune and chance, and pass now."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from synthlab.studio import song_model as sm
from synthlab.studio import song_play
from synthlab.studio.gen import build, checklist, styles
from synthlab.studio.gen import notes as nt
from synthlab.studio.server import create_app

STYLES = list(styles.STYLES)
SEEDS = (1, 2, 3, 4, 5)


def _cases():
    for style in STYLES:
        for seed in SEEDS:
            for i, c in enumerate(checklist.signature(build.start(style, seed).song)):
                yield pytest.param(
                    style, seed, i, id=f"{style}-{seed}-{c['text'][:44]}"
                )


@pytest.mark.parametrize("style,seed,i", list(_cases()))
def test_a_starter_shows_its_styles_signature(style, seed, i):
    c = checklist.signature(build.start(style, seed).song)[i]
    assert set(c) == {"text", "ok"}  # a pass or a fail: nothing waits
    assert c["ok"], c["text"]


@pytest.mark.parametrize("style", STYLES)
def test_the_signature_lists_have_each_styles_length(style):
    sig = checklist.signature(build.start(style, 1).song)
    assert (
        len(sig)
        == {
            # with the drop FX: trance's sub drop and punched drums,
            # techno's sub drop, house's envelope filter, garage's OTT pad,
            # electro's punched drums
            "trance": 15,
            "melodic_techno": 11,
            "filter_house": 9,
            "garage": 11,
            "electro": 9,
            "industrial": 7,
            "psych": 8,
            "sixties": 10,
            "hiphop": 9,
            "dnb": 9,
            "dubstep": 9,
            "synthwave": 8,
            "future_bass": 10,
            "lofi": 10,
            "ambient": 10,
            "chiptune": 10,
            "hardstyle": 10,
            "phonk": 10,
        }[style]
    )
    assert all(c["ok"] for c in sig)
    assert checklist.signature(sm.new_song(120, "A1", "minor")) == []
    assert checklist.signature(build.start(style, 1).song, "polka") == []


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("seed", SEEDS)
def test_a_starter_opens_with_nothing_missing(style, seed):
    """The "what is missing" checklist has no item open."""
    s = build.start(style, seed).song
    assert checklist.open_items(checklist.check(s)) == []
    assert checklist.check(s) == []


# --- the checks are not vacuous: break the song, and they say so ---------------


def _sig(song: dict) -> dict:
    return {c["text"]: c["ok"] for c in checklist.signature(song)}


def _track(song: dict, name: str) -> dict:
    return next(t for t in song["tracks"] if t["name"] == name)


def _pattern(song: dict, section: str, track: str) -> dict:
    sec = next(x for x in song["sections"] if x["name"] == section)
    return song["patterns"][sec["cells"][_track(song, track)["id"]]]


def test_the_trance_checks_fail_when_the_grammar_is_broken():
    good = build.start("trance", 2).song
    s = copy.deepcopy(good)
    p = _pattern(s, "Climax", "Mid Bass")
    p["cells"].append([p["cells"][0][0], 0])  # a bass note on the kick's step
    assert not _sig(s)["in every climax bar no bass note starts on a kick's step"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Climax", "Sub")
    p["cells"] = [c for c in p["cells"] if c[1] % 4 != 3]  # the third sixteenth gone
    assert not _sig(s)[
        "a bass note on each of the three sixteenths after every climax kick"
    ]
    s = copy.deepcopy(good)
    _pattern(s, "Climax", "Mid Bass")["cuts"] = []  # the roll becomes held notes
    assert not _sig(s)["a cut between the bass's notes (each is one step long)"]
    s = copy.deepcopy(good)
    _track(s, "Sub")["fx"] = []
    assert not _sig(s)["both bass tracks ducked"]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec.pop("auto", None)
    assert not _sig(s)["a gate with a pattern on a pad track, and a lane on its depth"]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec["audio"] = []
    assert not _sig(s)["a riser or sweep ending on the climax's bar line"]
    s = copy.deepcopy(good)
    _pattern(s, "Build", "Drums")["drums"] = [["snare", i] for i in range(0, 128, 4)]
    assert not _sig(s)["a build whose roll never thins and takes three rates or more"]
    s = copy.deepcopy(good)
    drums = _track(s, "Drums")["id"]
    for sec in s["sections"]:  # the beat plays through the breakdown
        sec["cells"][drums] = next(x for x in s["sections"] if x["name"] == "Mid")[
            "cells"
        ][drums]
        sec.pop("spans", None)
    assert not _sig(s)["a breakdown of 16 bars or more with no drums"]
    s = copy.deepcopy(good)
    _track(s, "Lead")["patch"]["unison"] = 2
    assert not _sig(s)["a supersaw lead (unison of 5 or more) in the climax"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Climax", "Drums")
    p["drums"] = [h for h in p["drums"] if h != ["ohat", 2]]
    assert not _sig(s)["an open hat on every offbeat of the climax"]
    s = copy.deepcopy(good)
    _track(s, "Pluck")["fx"] = []
    assert not _sig(s)["a synced ping-pong delay on the pluck"]
    s = copy.deepcopy(good)
    s["bpm"] = 124.0
    assert not _sig(s)["tempo 132-140"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Build", "Drums")
    for h in p["hits"].values():  # the roll stops at sixteenths
        h.pop("r", None)
    assert not _sig(s)["the roll reaches 32nds with the snare's tune rising"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Build", "Drums")
    for h in p["hits"].values():  # ... or the snare stays at one pitch
        h.pop("t", None)
    assert not _sig(s)["the roll reaches 32nds with the snare's tune rising"]
    assert all(_sig(good).values())


def test_the_other_styles_checks_fail_when_the_grammar_is_broken():
    good = build.start("melodic_techno", 2).song
    s = copy.deepcopy(good)
    p = _pattern(s, "Drop A", "Sub")
    p["cells"].append([p["cells"][0][0], 4])
    p["cuts"].append([p["cells"][0][0], 4])  # a note of its own, not a held one
    assert not _sig(s)["the bass never on a kick's step"]
    s = copy.deepcopy(good)
    _track(s, "Pad")["fx"] = []
    assert not _sig(s)["a pad with a reverb of 3 s or more"]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec["audio"] = [a for a in sec["audio"] if not a["clip"].startswith("impact")]
    _track(s, "Impact")["mute"] = True  # (the drops' own track)
    assert not _sig(s)["an impact starting on a drop's first bar line"]
    s = copy.deepcopy(good)
    for p in s["patterns"].values():  # every hit certain again
        for h in (p.get("hits") or {}).values():
            h.pop("p", None)
    assert not _sig(s)["four hits or more with a chance under 1"]
    assert all(_sig(good).values())

    good = build.start("filter_house", 2).song
    s = copy.deepcopy(good)
    p = _pattern(s, "Drop A", "Drums")
    p["drums"] = [h for h in p["drums"] if h != ["kick", 4]]
    assert not _sig(s)["a kick on every beat of each drop"]
    s = copy.deepcopy(good)
    s["sections"][0].pop("auto")
    assert not _sig(s)[
        "the same rising low-pass lane on every sounding synth track of the intro"
    ]
    s = copy.deepcopy(good)
    _pattern(s, "Drop A", "Bass")["slides"] = []
    assert not _sig(s)["a slide in the bass"]
    s = copy.deepcopy(good)
    _track(s, "Bass")["fx"] = []
    assert not _sig(s)["a duck of depth 0.8 or more on the bass and on the loop"]
    s = copy.deepcopy(good)
    _track(s, "Drums")["fx"] = []
    assert not _sig(s)["a bitcrush or a compressor on a track"]

    good = build.start("garage", 2).song
    s = copy.deepcopy(good)
    s["drum_swing"] = 0.0
    assert not _sig(s)[
        "the hats swung by 0.12 of a sixteenth or more, the kick straight"
    ]
    s = copy.deepcopy(good)
    _pattern(s, "Verse", "Drums")["drums"].append(["kick", 4])
    assert not _sig(s)["in the verses no kick on beat 2 or 4, and one on an offbeat"]
    s = copy.deepcopy(good)
    _pattern(s, "Verse", "Drums")["drum_vel"] = {}
    assert not _sig(s)[
        "two hits a bar or more at a velocity of 0.4 or less (the verses)"
    ]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec["audio"] = [a for a in sec["audio"] if not a["clip"].startswith("bed")]
    assert not _sig(s)["a room-tone bed"]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        for lanes in (sec.get("auto") or {}).values():
            lanes[:] = [ln for ln in lanes if ln["target"] != "gain"]
    assert not _sig(s)["a cut before a drop"]
    s = copy.deepcopy(good)
    for t in s["tracks"]:
        t["fx"] = [u for u in t.get("fx", []) if u["kind"] != "vinyl"]
    assert not _sig(s)["a vinyl unit"]


def test_the_rolling_bass_of_a_trance_starter_against_its_own_kick():
    """Section 6.4, spelled out on the song itself rather than through the
    checklist: in every climax bar no bass note-on shares a step with a
    kick, each kick is followed by three bass sixteenths, each one step
    long, on both bass tracks."""
    for seed in SEEDS:
        s = build.start("trance", seed).song
        climax = next(x for x in s["sections"] if x["name"] == "Climax")
        kicks = checklist.kick_set(s, climax)
        assert kicks == {b * 16 + k for b in range(8) for k in (0, 4, 8, 12)}
        for name in ("Sub", "Mid Bass"):
            t = _track(s, name)
            ons = checklist.note_ons(s, climax, t)
            assert not ons & kicks
            assert ons == {k + i for k in kicks for i in (1, 2, 3)}
            p = s["patterns"][climax["cells"][t["id"]]]
            assert all(n == 1 for _p, _s, n in nt.runs(p))
            assert any(u["kind"] == "pump" for u in t["fx"])


# --- the plan renders, and nothing passes the ceiling --------------------------


@pytest.mark.parametrize("style", STYLES)
def test_a_starters_plan_renders_with_every_pass_under_the_ceiling(style, tmp_path):
    """One seed a style here (a plan is 3-13 s cold; all five seeds are
    run by `tools/song_measure.py`): the song plays, its gain puts the
    loudest pass at or under 0.97, and the checklist stays empty with the
    levels in hand."""
    with TestClient(create_app(data_dir=tmp_path)) as client:
        client.post("/song/start", data={"style": style, "seed": "1"})
        svc = client.app.state.svc
        song, master = svc.song_snapshot()
        plan = svc.song_plan(song, master)
        assert len(plan["sections"]) == len(song["sections"])
        entry = svc._song_idents(song, master)
        levels = [
            svc.song_levels.get(svc._section_cache_key(ident, 1.0))
            for head_loop in entry["passes"]
            for ident, _parts, _prev in head_loop
        ]
        assert all(lv is not None for lv in levels)
        peak = max(p for p, _r in levels) * plan["gain"]
        assert 0.05 < peak <= song_play.CEILING + 1e-9
        ref = max(r for _p, r in levels)
        found = checklist.check(song, {"peak": peak / plan["gain"], "ref": ref})
        assert checklist.open_items(found) == []
        # the first section's WAV, as the page fetches it
        wav = client.get(plan["sections"][0]["url"])
        assert wav.status_code == 200 and len(wav.content) > 100_000


def test_the_house_and_techno_checks_fail_when_their_piece_is_gone():
    """The drop FX as tracks, the punched drums, the envelope filter and the
    OTT pad: take each away and its check says so."""
    good = build.start("melodic_techno", 3).song
    s = copy.deepcopy(good)
    for t in s["tracks"]:
        if t["name"] == "Impact":
            t["patch"]["pitch_env"] = 0.0  # no longer a hit's fall
    assert not _sig(s)["an impact starting on a drop's first bar line"]
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec["audio"] = [a for a in sec["audio"] if not a["clip"].startswith("subdrop")]
    assert not _sig(s)["a sub drop under a drop's first beat"]
    # ... and a track of the sub drop's grammar on a drop's first beat is one
    sub = build.start("melodic_techno", 3).song
    imp = _track(sub, "Impact")
    imp["patch"].update(osc1="sine", cutoff=400.0, pitch_env=12.0, pitch_env_decay=1.0)
    for sec in sub["sections"]:
        sec["audio"] = [a for a in sec["audio"] if not a["clip"].startswith("subdrop")]
    assert _sig(sub)["a sub drop under a drop's first beat"]
    trance = build.start("trance", 3).song
    s = copy.deepcopy(trance)
    _track(s, "Drums")["fx"] = [
        u for u in _track(s, "Drums")["fx"] if u["kind"] != "transient"
    ]
    assert not _sig(s)["the drums punched (a transient shaper on their bus)"]
    s = copy.deepcopy(trance)
    for sec in s["sections"]:
        for a in sec["audio"]:
            if a["clip"].startswith("subdrop"):
                a["at_bar"] = 1  # late: not on the climax's first beat
    assert not _sig(s)["a sub drop under the climax's first beat"]
    house = build.start("filter_house", 3).song
    s = copy.deepcopy(house)
    for u in _track(s, "Bass")["fx"]:
        if u["kind"] == "autofilter":
            u["params"]["env"] = 0.0  # an LFO sweep, not an envelope filter
    assert not _sig(s)[
        "the bass through an envelope filter (an auto-filter its notes open)"
    ]
    garage = build.start("garage", 3).song
    s = copy.deepcopy(garage)
    for u in _track(s, "Vocal Pad")["fx"]:
        if u["kind"] == "ott":
            u["params"]["depth"] = 0.1
    assert not _sig(s)["a pad squashed over the top (an OTT at a depth of 0.3 or more)"]


def _without_hits(song: dict, voice: str, key: str) -> None:
    for p in song["patterns"].values():
        for k, h in (p.get("hits") or {}).items():
            if k.startswith(f"{voice}."):
                h.pop(key, None)


def test_the_hiphop_to_future_bass_checks_fail_when_their_piece_is_gone():
    """Hip-hop / trap, drum & bass, dubstep, synthwave, future bass: take
    each style's grammar away and its check says so."""
    good = build.start("hiphop", 2).song
    s = copy.deepcopy(good)
    _without_hits(s, "hat", "r")
    assert not _sig(s)[
        "hat rolls in the hooks (a hat struck twice or more in its step)"
    ]
    s = copy.deepcopy(good)
    _pattern(s, "Hook", "Drums")["drums"].append(["snare", 4])
    assert not _sig(s)["half time in every hook: the snare on 3, none on 2 or 4"]
    s = copy.deepcopy(good)
    _track(s, "808")["patch"]["glide"] = 0.0
    _track(s, "808 Dist")["patch"]["glide"] = 0.0
    assert not _sig(s)["an 808: a sine bass falling onto its note, gliding"]
    s = copy.deepcopy(good)
    for name in ("808", "808 Dist"):
        for sec in s["sections"]:
            if (pid := sec["cells"].get(_track(s, name)["id"])) is not None:
                s["patterns"][pid]["slides"] = []
    assert not _sig(s)["a slide in the 808's line"]
    s = copy.deepcopy(good)
    _track(s, "808 Dist")["fx"] = []
    assert not _sig(s)["a distorted 808 (a drive or a clipper on a bass)"]
    assert all(_sig(good).values())

    good = build.start("dnb", 2).song
    s = copy.deepcopy(good)
    _pattern(s, "Drop", "Drums")["drums"].append(["kick", 4])
    assert not _sig(s)["the two-step in every drop: kick on 1 and the 'and' of 3"]
    s = copy.deepcopy(good)
    s["drum_swing"] = 0.0
    assert not _sig(s)["the hats shuffled (0.05 of a sixteenth or more)"]
    s = copy.deepcopy(good)
    _track(s, "Reese")["patch"]["osc2_detune"] = 3.0
    assert not _sig(s)["a Reese: two saws detuned 10 cents or more on a bass"]
    s = copy.deepcopy(good)
    _track(s, "Sub")["fx"] = []
    assert not _sig(s)["a sine sub under it, ducked"]
    s = copy.deepcopy(good)
    for p in s["patterns"].values():
        p.pop("drum_vel", None)
        for h in (p.get("hits") or {}).values():
            h.pop("v", None)
    assert not _sig(s)["a busier drop: two ghost snares a pass or more"]
    assert all(_sig(good).values())

    good = build.start("dubstep", 2).song
    s = copy.deepcopy(good)
    _track(s, "Triplet Wobble")["patch"]["filter_lfo_sync"] = "1/8"
    _track(s, "Growl")["patch"]["filter_lfo_sync"] = "1/8"
    assert not _sig(s)["wobbles at two clock rates or more"]
    s = copy.deepcopy(good)
    for t in s["tracks"]:
        if t.get("role") == "bass":
            t["patch"]["filter_lfo_sync"] = "off"
    assert not _sig(s)["a wobble: a bass whose filter LFO is synced to the clock"]
    s = copy.deepcopy(good)
    _track(s, "Growl")["fx"] = []
    assert not _sig(s)["a growl: FM or the vowel filter on a bass, driven or folded"]
    s = copy.deepcopy(good)
    _track(s, "Impact")["mute"] = True
    assert not _sig(s)["an impact starting on a drop's first bar line"]
    assert all(_sig(good).values())

    good = build.start("synthwave", 2).song
    s = copy.deepcopy(good)
    _track(s, "Drums")["fx"] = [
        u for u in _track(s, "Drums")["fx"] if u["kind"] != "vgate"
    ]
    assert not _sig(s)["a gated reverb on the drums (a room, then a gate)"]
    s = copy.deepcopy(good)
    _track(s, "Brass")["patch"]["filter_attack"] = 0.005
    assert not _sig(s)["a brass stab whose filter swells open after the note"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Last Chorus", "Bass")
    p["cells"] = [c for c in p["cells"] if int(c[1]) % 4 == 0]
    assert not _sig(s)["a sequenced bass: eight notes a bar or more in the choruses"]
    s = copy.deepcopy(good)
    s["drum_swing"] = 0.1
    assert not _sig(s)["the drums straight (no swing)"]
    assert all(_sig(good).values())

    good = build.start("future_bass", 2).song
    s = copy.deepcopy(good)
    _track(s, "Chords")["fx"] = [
        u for u in _track(s, "Chords")["fx"] if u["kind"] != "pump"
    ]
    assert not _sig(s)["the chords pumped hard (a duck of 0.8 or more)"]
    s = copy.deepcopy(good)
    _track(s, "Chops")["patch"]["formant_amount"] = 0.0
    assert not _sig(s)["vocal chops through the vowel filter"]
    s = copy.deepcopy(good)
    _track(s, "Pluck")["patch"].update(pitch_env=0.0, mod1_src="none")
    assert not _sig(s)["a pitch-bent pluck or lead"]
    s = copy.deepcopy(good)
    _without_hits(s, "snare", "t")
    assert not _sig(s)["the roll reaches 32nds with the snare's tune rising"]
    assert all(_sig(good).values())


def test_the_lofi_to_phonk_checks_fail_when_their_piece_is_gone():
    """Lo-fi hip-hop, ambient / cinematic, chiptune, hardstyle, phonk: take
    each style's grammar away and its check says so."""
    good = build.start("lofi", 2).song
    s = copy.deepcopy(good)
    s["drum_swing"] = 0.05
    assert not _sig(s)["the drums swung hard (0.18 of a sixteenth or more)"]
    s = copy.deepcopy(good)
    _pattern(s, "Hook", "Drums")["swing"] = {}
    assert not _sig(s)["the kick and the snare held straight under the swung hats"]
    s = copy.deepcopy(good)
    _track(s, "Keys")["patch"]["osc1"] = "saw"
    assert not _sig(s)["e-piano keys from 4-operator FM"]
    s = copy.deepcopy(good)
    _track(s, "Drums")["fx"] = [
        u for u in _track(s, "Drums")["fx"] if u["kind"] != "tape"
    ]
    assert not _sig(s)["the drums on tape too"]
    s = copy.deepcopy(good)
    _track(s, "Pad")["patch"]["drift"] = 0.0
    assert not _sig(s)["a drifting pad"]
    assert all(_sig(good).values())

    good = build.start("ambient", 2).song
    s = copy.deepcopy(good)
    for sec in s["sections"]:
        sec["auto"] = {}
    _track(s, "Scan Pad")["curves"] = {}
    _track(s, "Vowel Pad")["curves"] = {}
    assert not _sig(s)[
        "a wavetable pad whose table moves (a rising lane on its position)"
    ]
    s = copy.deepcopy(good)
    _track(s, "Shimmer")["fx"] = []
    assert not _sig(s)["a shimmer on a pad"]
    s = copy.deepcopy(good)
    _track(s, "Drone")["patch"]["drift"] = 0.0
    assert not _sig(s)[
        "a drone: a bass swelling in over a second or more, held, drifting"
    ]
    s = copy.deepcopy(good)
    _track(s, "Braam")["mute"] = True
    assert not _sig(s)["a braam on a climax's first beat"]
    s = copy.deepcopy(good)
    drums = _track(s, "Drums")["id"]
    for sec in s["sections"]:
        if drums not in sec["cells"]:
            sec["cells"][drums] = next(
                pid for x in s["sections"] if (pid := x["cells"].get(drums))
            )
    assert not _sig(s)["it opens with no drums"]
    assert not _sig(s)["sparse drums: a third of the song or more with none"]
    assert all(_sig(good).values())

    good = build.start("chiptune", 2).song
    s = copy.deepcopy(good)
    _track(s, "Square")["patch"]["filter_type"] = "lowpass"
    assert not _sig(s)["a square lead with no filter"]
    s = copy.deepcopy(good)
    p = _pattern(s, "Last Chorus", "Arp")
    p["cells"] = [c for c in p["cells"] if int(c[1]) % 2 == 0]
    assert not _sig(s)["a fast arpeggio: a note every sixteenth in the choruses"]
    s = copy.deepcopy(good)
    _track(s, "Drums")["drum_params"]["snare"]["tone"] = 0.5
    assert not _sig(s)["a noise snare (the kit's: no body, tone 0.1 or under)"]
    s = copy.deepcopy(good)
    _track(s, "Square")["fx"].append(
        {"kind": "reverb", "params": {"decay": 2.0, "mix": 0.3, "size": 0.8}}
    )
    assert not _sig(s)["dry: no reverb on any track or the drums"]
    assert all(_sig(good).values())

    good = build.start("hardstyle", 2).song
    s = copy.deepcopy(good)
    _track(s, "Drums")["drum_params"]["kick"]["decay"] = 0.3
    assert not _sig(s)[
        "a long, driven kick (the kit's: 0.6 s or more, drive 3 or more)"
    ]
    s = copy.deepcopy(good)
    p = _pattern(s, "Drop", "Reverse Bass")
    p["cells"].append([p["cells"][0][0], 0])  # a note on the kick
    assert not _sig(s)[
        "a reverse bass: notes that swell in between the kicks, never on one"
    ]
    s = copy.deepcopy(good)
    _track(s, "Screech")["patch"]["osc2_sync"] = "off"
    assert not _sig(s)["a screech: a hard-synced lead"]
    s = copy.deepcopy(good)
    _pattern(s, "Drop", "Drums")["drums"] = [
        c for c in _pattern(s, "Drop", "Drums")["drums"] if c != ["kick", 4]
    ]
    assert not _sig(s)["four on the floor in every drop"]
    assert all(_sig(good).values())

    good = build.start("phonk", 2).song
    s = copy.deepcopy(good)
    _track(s, "Cowbell")["patch"].update(fm4_ratio3=2.0, fm4_ratio4=4.0)
    assert not _sig(s)[
        "a cowbell melody in every hook (a struck, inharmonic 4-op lead)"
    ]
    s = copy.deepcopy(good)
    _track(s, "808")["fx"] = [
        u
        for u in _track(s, "808")["fx"]
        if u["kind"] not in ("drive", "bitcrush", "clip")
    ]
    assert not _sig(s)["a distorted 808: a sine falling onto its note, gliding, driven"]
    s = copy.deepcopy(good)
    _without_hits(s, "hat", "r")
    assert not _sig(s)[
        "hat rolls in the hooks (a hat struck twice or more in its step)"
    ]
    s = copy.deepcopy(good)
    _track(s, "Drums")["drum_params"]["rim"]["tune"] = 420.0
    assert not _sig(s)[
        "a cowbell in the kit (the rim tuned to 500 Hz or more, playing)"
    ]
    assert all(_sig(good).values())
