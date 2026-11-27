"""synthlab.kits.sfz: the kit's SFZ read into rows, articulations and mics."""

from pathlib import Path

import pytest

from synthlab.kits import fetch, sfz

FIXTURE = Path(__file__).parent / "fixtures" / "kit_sfz"


def test_defines_and_includes_resolve():
    art = sfz.read_kit(FIXTURE).arts[("hat", "closed")]
    first = art.layers[0].takes[0]["overheads"]
    # $MIC_$ART_$VEL1_$RR-$CODE_01.$ext, the code from a nested include
    assert first == "Wilkinson Audio/Naked Drums/Samples/OH_A2_10_one-CWG6.flac"
    assert art.trim_db == 1.0 and art.group == "hat"


def test_velocity_splits_and_round_robins():
    kit = sfz.read_kit(FIXTURE)
    art = kit.arts[("hat", "closed")]
    assert [(ly.lo, ly.hi) for ly in art.layers] == [(1, 85), (86, 127)]
    codes = [[t["overheads"].rsplit("-", 1)[1] for t in ly.takes] for ly in art.layers]
    assert codes == [["CWG6.flac", "PF2U.flac"], ["4I2D.flac", "X129.flac"]]
    assert "_two-" in art.layers[0].takes[1]["overheads"]


def test_mics_come_from_the_group_file():
    kit = sfz.read_kit(FIXTURE)
    assert kit.mics == ("overheads",)


def test_gated_and_unmapped_groups_are_left_out():
    # open is cc-gated (the variable hat), key 40 is snare 2: neither is a hit
    assert set(sfz.read_kit(FIXTURE).arts) == {("hat", "closed")}


def test_read_kit_is_memoised():
    assert sfz.read_kit(FIXTURE) is sfz.read_kit(FIXTURE)


ROWS = {
    "kick": ["hit"],
    "snare": ["centre", "side"],
    "tom": ["hit"],
    "tom2": ["hit"],
    "tom3": ["hit"],
    "tom4": ["hit"],
    "tom5": ["hit"],
    "ride": ["bow", "bell"],
    "crash": ["edge", "bell"],
    "crash2": ["edge", "bell"],
    "splash": ["hit"],
    "china": ["hit"],
    "hat": ["pedal", "tight", "closed", "half", "open"],
}


@pytest.mark.slow
def test_the_real_kit_maps_every_row_and_articulation():
    root = fetch.kit_dir()
    if not fetch.availability(fetch.NAKED, root)["ready"]:
        pytest.skip("the acoustic kit is not downloaded")
    kit = sfz.read_kit(root)
    for row, names in ROWS.items():
        for name in names:
            assert (row, name) in kit.arts, (row, name)
    assert len(kit.arts) == sum(map(len, ROWS.values()))
    closed = kit.arts[("hat", "closed")]
    assert len(closed.layers) >= 1
    assert all(len(ly.takes) == 10 for ly in closed.layers)
    assert set(kit.mics) == set(sfz.MICS)
    for art in kit.arts.values():
        for layer in art.layers:
            assert 1 <= layer.lo <= layer.hi <= 127
            for take in layer.takes:
                assert take
                for mic, rel in take.items():
                    assert (root / rel).is_file(), (art.row, art.name, mic, rel)
