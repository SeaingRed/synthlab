import time

import pytest

from synthlab.studio.drumfactory import DRUM_FACTORY
from synthlab.studio.notation import NotationError, parse, to_notation


def hits(steps, voice):
    return [s for v, s in steps if v == voice]


def test_repeat_splits_the_bar():
    assert hits(parse("bd*4", 16), "kick") == [0, 4, 8, 12]
    assert hits(parse("hh*8", 16), "hat") == list(range(0, 16, 2))


def test_rest_and_brackets():
    assert parse("[~ sn]", 16) == [["snare", 8]]
    assert parse("bd [sn sn] ~ cp", 16) == sorted(
        [["kick", 0], ["snare", 4], ["snare", 6], ["clap", 12]]
    )


def test_layers_run_in_parallel():
    steps = parse("bd*4, [~ sn]*2, hh*8", 16)
    assert hits(steps, "kick") == [0, 4, 8, 12]
    assert hits(steps, "snare") == [4, 12]
    assert len(hits(steps, "hat")) == 8


def test_bars_repeat_the_pattern():
    assert hits(parse("bd*4", 16, bars=2), "kick") == [0, 4, 8, 12, 16, 20, 24, 28]


def test_long_names_and_case():
    assert parse("KICK Snare", 4) == [["kick", 0], ["snare", 2]]


def test_off_grid_is_an_error():
    with pytest.raises(NotationError, match="steps per bar"):
        parse("bd*3", 16)


def test_unknown_drum_is_an_error():
    with pytest.raises(NotationError, match="cowbell"):
        parse("cowbell", 16)


@pytest.mark.parametrize("bad", ["[bd", "bd]", "bd*0", "bd,", "*4", ""])
def test_malformed(bad):
    if bad == "":
        assert parse(bad, 16) == []
    else:
        with pytest.raises(NotationError):
            parse(bad, 16)


@pytest.mark.parametrize("pattern", DRUM_FACTORY, ids=lambda p: p["name"])
def test_factory_patterns_round_trip(pattern):
    spb = pattern["steps_per_bar"]
    text = to_notation(pattern["steps"], spb)
    assert parse(text, spb) == pattern["steps"]


def test_to_notation_compresses_uniform_beats():
    assert to_notation([["kick", s] for s in (0, 4, 8, 12)], 16) == "[bd ~ ~ ~]*4"
    assert to_notation([["kick", s] for s in (0, 2)], 4) == "bd ~ bd ~"
    assert to_notation([], 16) == ""


def test_notation_route_updates_grid_and_box(tmp_path):
    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/fragment/drums/bank/load/0")  # 16-step grid
    r = client.post("/fragment/drums/notation", data={"notation": "bd*4, [~ sn]*2"})
    assert r.status_code == 200
    s = client.app.state.studio
    assert hits(s.arrangement.drums, "kick") == [0, 4, 8, 12]
    assert hits(s.arrangement.drums, "snare") == [4, 12]
    assert 'value="[bd ~ ~ ~]*4, [~ ~ ~ ~] [sn ~ ~ ~] [~ ~ ~ ~] [sn ~ ~ ~]"' in r.text
    r = client.post("/fragment/drums/notation", data={"notation": "bd*3"})
    assert r.status_code == 422 and "steps per bar" in r.text


def test_notation_bounds():
    with pytest.raises(NotationError, match="repeat count"):
        parse("bd*99999", 16)
    with pytest.raises(NotationError, match="too dense"):
        parse("[[[bd*64]*64]*64]", 16)
    with pytest.raises(NotationError, match="too long"):
        parse("bd " * 300, 16)


@pytest.mark.parametrize("text", ["[[[[~*64]*64]*64]*64]*64", "[[[[[bd]]]]]"])
def test_pathological_notation_fails_fast(text):
    t0 = time.perf_counter()
    with pytest.raises(NotationError):
        parse(text, 1, 16)
    assert time.perf_counter() - t0 < 0.5


def test_notation_route_bumps_grid_to_fit(tmp_path):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from synthlab.studio.server import create_app

    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    s.arrangement = replace(s.arrangement, drum_steps=4)  # a coarse drum grid
    r = client.post("/fragment/drums/notation", data={"notation": "hh*8"})
    assert r.status_code == 200
    assert s.arrangement.drum_steps == 8
    assert hits(s.arrangement.drums, "hat") == list(range(8))
    assert (
        client.post("/fragment/drums/notation", data={"notation": "bd*3"}).status_code
        == 422
    )
