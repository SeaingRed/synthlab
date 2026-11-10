"""A breed card on its own plays in the sound's own register.

Every card's ▶ used to play the reference phrase at octave 3 whatever the
sound: a bass was heard two octaves above where it is played. The register
is read off what the breeder already measured (`audition.register_of`), the
phrase moves there (`audition.listening_root`), and the notes are part of
the render key, so two registers are two cache entries. The probe every
breeding decision is made on stays at A3.
"""

import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.analysis.features import Features
from synthlab.explore.probe import PROBE_PITCH, probe
from synthlab.studio import sounds
from synthlab.studio.audition import (
    REGISTER_PITCH,
    listening_root,
    phrase_arrangement,
    register_of,
)
from synthlab.studio.library import FAMILIES, LIBRARY
from synthlab.studio.server import create_app
from synthlab.studio.state import ROOTS, Arrangement
from synthlab.synth import Patch


def _features(**kw) -> Features:
    base = dict(
        centroid=800.0,
        rolloff=3000.0,
        flatness=0.01,
        crest=10.0,
        sub_20_60=0.0,
        sub_60_120=0.0,
        low_120_250=0.3,
        attack_time=0.01,
        stereo_width=0.0,
    )
    return Features(**{**base, **kw})


def test_the_register_pitches_are_the_librarys_family_pitches():
    assert REGISTER_PITCH == {k: FAMILIES[k]["pitch"] for k in ("bass", "pad", "lead")}
    # keys and plucks are measured where leads are
    assert FAMILIES["keys"]["pitch"] == FAMILIES["pluck"]["pitch"] == "C4"


def test_a_sound_under_its_key_is_a_bass():
    assert register_of(Patch(), _features(sub_20_60=0.2, sub_60_120=0.4)) == "bass"
    # the main oscillator an octave down says so without a measure
    assert register_of(replace(Patch(), osc1_octave=-1)) == "bass"
    assert register_of(replace(Patch(), osc1_octave=-1), _features()) == "bass"


def test_a_slow_attack_is_a_pad_and_the_rest_are_leads():
    assert register_of(Patch(), _features(attack_time=0.3)) == "pad"
    assert register_of(Patch(), _features()) == "lead"
    assert register_of(Patch()) == "lead"
    # a slow bass is still a bass: where it lives beats how it starts
    low = _features(sub_60_120=0.8, attack_time=0.3)
    assert register_of(Patch(), low) == "bass"


def test_a_sound_with_a_layer_under_its_main_never_drops_to_the_bass_register():
    low = _features(sub_60_120=0.8)
    assert register_of(Patch(), low, (1,)) == "bass"
    assert register_of(Patch(), low, (-1, 1)) == "lead"
    assert register_of(Patch(), _features(sub_60_120=0.8, attack_time=0.3), (-2,)) == (
        "pad"
    )


def test_the_librarys_families_land_in_their_own_register():
    """Probed as a breed card is (A3), 15 of 16 basses, 16 of 16 pads and
    44 of the 48 leads, keys and plucks are heard in their family's
    register (two slow leads read as pads, two plucks whose energy sits
    under the note as basses)."""
    hits: Counter = Counter()
    for e in LIBRARY:
        p = Patch.from_dict(e["patch"])
        want = {"keys": "lead", "pluck": "lead"}.get(e["family"], e["family"])
        if want in REGISTER_PITCH:
            hits[want] += register_of(p, probe(p).features) == want
    assert hits["bass"] >= 15 and hits["pad"] == 16 and hits["lead"] >= 44, hits


def test_each_register_plays_the_roots_nearest_octave():
    assert listening_root("A1") == "A3"  # no register: the bench's own
    assert listening_root("A1", "bass") == "A1"
    assert listening_root("A1", "pad") == "A2"
    assert listening_root("A1", "lead") == "A3"
    assert listening_root("E2", "bass") == "E1"
    assert listening_root("E2", "pad") == "E3"
    assert listening_root("E2", "lead") == "E4"
    assert listening_root("F#1", "lead") == "F#3"  # a tritone: the lower one
    assert listening_root("A1", "no such register") == "A3"
    for root in ROOTS:
        for register in REGISTER_PITCH:
            got = listening_root(root, register)
            assert got.rstrip("-0123456789") == root.rstrip("-0123456789")


def test_the_phrase_moves_with_the_register_and_nothing_else_does():
    arr = Arrangement()
    lead = phrase_arrangement(arr, "hold", register="lead")
    assert lead == phrase_arrangement(arr, "hold")  # root A: A3 either way
    bass = phrase_arrangement(arr, "hold", register="bass")
    assert bass.root == "A1" and lead.root == "A3"
    assert [step for _, step in bass.cells] == [step for _, step in lead.cells]
    assert replace(bass, root=lead.root, cells=lead.cells) == lead


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _tile(client: TestClient, patch: Patch) -> str:
    s = client.app.state.studio
    s.patch = patch
    html = client.post("/fragment/breed/generation", data={"wildness": "tame"}).text
    return re.search(r'data-tile-id="([a-f0-9]{12})"', html).group(1)


def test_the_preview_key_changes_with_the_register(tmp_path):
    client = _client(tmp_path)
    svc = client.app.state.svc
    p = Patch()
    keys = {
        reg: svc.render_inputs("editor", patch=p, mix=False, register=reg).key()
        for reg in (None, "bass", "pad", "lead")
    }
    assert keys[None] == keys["lead"]  # root A: the same notes, the same render
    assert len({keys["bass"], keys["pad"], keys["lead"]}) == 3
    # in your mix is the session's register whatever the sound is
    mix = svc.render_inputs("editor", patch=p, mix=True).key()
    low = replace(sounds.Sound(p), register="bass")
    assert (
        svc.audition(low, mix=True).body == svc.render("editor", patch=p, mix=True).wav
    )
    assert svc.render("editor", patch=p, mix=True).key == mix


def test_a_bass_cards_own_play_is_the_phrase_at_its_register(tmp_path):
    """The card's ▶ is the bench phrase moved to the sound's register and
    nothing else; "in your mix" and the measuring note stay where they
    were (the probe's A3)."""
    client = _client(tmp_path)
    svc = client.app.state.svc
    tid = _tile(client, replace(Patch(), osc1_octave=-1, sub_level=0.6))
    sound = sounds.find(svc, "breed", tid)
    row = svc.space_store().get(tid)
    assert sound.register == register_of(sound.patch, Features(**row["features"]))
    low = replace(sound, register="bass")  # whatever the tame child measured as
    heard = svc.audition(low).body
    assert heard == svc.render("editor", patch=low.patch, register="bass").wav
    assert heard != svc.render("editor", patch=low.patch).wav
    via_route = client.get(f"/breed/{tid}/audition.wav").content
    assert via_route == svc.audition(sound).body
    in_mix = client.get(f"/breed/{tid}/audition.wav?mix=1").content
    assert in_mix == svc.render("editor", patch=sound.patch, mix=True).wav
    assert PROBE_PITCH == "A3"
    assert client.get(f"/breed/note/{tid}.wav").status_code == 200


def test_a_row_without_features_still_finds_its_register(tmp_path):
    client = _client(tmp_path)
    svc = client.app.state.svc
    tid = _tile(client, Patch())
    row = dict(svc.space_store().get(tid))
    patch = Patch.from_dict(row["patch"])
    row.pop("features")
    assert sounds._row_register(patch, row, None) == register_of(patch)
