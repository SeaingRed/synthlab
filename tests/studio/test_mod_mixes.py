"""Mod mixes (studio/modmixes.py, POST /fragment/mod/mix): the mod matrix
set in one move — a named mix, a random musical one, or every route
unplugged — as one undo step that says what it routed."""

from __future__ import annotations

import json
import random

import pytest
from fastapi.testclient import TestClient

from synthlab.params import (
    MOD_DESTINATIONS,
    MOD_SLOTS,
    MOD_SOURCES,
    SYNC_DIVISIONS,
    clamp,
)
from synthlab.studio import modmixes
from synthlab.studio.paramui import MOD_SOURCE_LABELS, MOD_TARGET_LABELS
from synthlab.studio.server import create_app
from synthlab.synth import Patch


def _routes(fields: dict) -> list[tuple]:
    return [
        (fields[f"{s}_src"], fields.get(f"{s}_dst"), fields.get(f"{s}_amt"))
        for s in MOD_SLOTS
        if fields[f"{s}_src"] != "none"
    ]


@pytest.mark.parametrize("name", list(modmixes.MIXES))
def test_every_named_mix_is_a_valid_patch_that_clamp_leaves_alone(name):
    fields = modmixes.mix_fields(name)
    assert {
        f"{s}_src" for s in MOD_SLOTS
    } <= fields.keys()  # every slot named: no stacking
    for src, dst, amt in _routes(fields):
        assert src in MOD_SOURCES and dst in MOD_DESTINATIONS and -1 <= amt <= 1
    assert fields["lfo_sync"] in SYNC_DIVISIONS
    p = Patch(**fields)
    assert clamp(p) == p


def test_a_random_mix_is_two_to_four_routes_on_distinct_targets():
    for seed in range(200):
        words, fields = modmixes.random_mix(random.Random(seed))
        routes = _routes(fields)
        assert 2 <= len(routes) <= len(MOD_SLOTS)
        assert len({dst for _, dst, _ in routes}) == len(routes)
        # the matrix's labels and the cables' percentages, never field values
        assert all(
            f"{MOD_SOURCE_LABELS[src]} → {MOD_TARGET_LABELS[dst]} {amt:+.0%}" in words
            for src, dst, amt in routes
        )
        p = Patch(**fields)
        assert clamp(p) == p
    # it is a draw: two seeds give two mixes
    assert modmixes.random_mix(random.Random(1)) != modmixes.random_mix(
        random.Random(2)
    )


def test_the_route_applies_a_mix_says_so_and_undoes_in_one_step(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    studio_state = client.app.state.svc.state
    before = studio_state.patch
    r = client.post("/fragment/mod/mix", data={"name": "wobble"})
    assert r.status_code == 200
    p = studio_state.patch
    assert (p.mod1_src, p.mod1_dst, p.lfo_sync) == ("lfo", "cutoff", "1/8")
    assert p.mod3_src == p.mod4_src == "none"
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
    assert toast.startswith("Mod mix: Wobble")
    client.post("/fragment/undo")
    assert studio_state.patch.mod1_src == before.mod1_src
    # clear unplugs every route; random routes two or more
    client.post("/fragment/mod/mix", data={"name": "clear"})
    assert all(getattr(studio_state.patch, f"{s}_src") == "none" for s in MOD_SLOTS)
    r = client.post("/fragment/mod/mix", data={"name": "random"})
    assert "Random mod mix" in r.headers["HX-Trigger"]
    assert (
        sum(getattr(studio_state.patch, f"{s}_src") != "none" for s in MOD_SLOTS) >= 2
    )
    assert client.post("/fragment/mod/mix", data={"name": "nope"}).status_code == 404


def test_a_routes_amount_reads_in_percent_as_its_cable_does(tmp_path):
    """The matrix's amount read-out is "29%", as the patch bay's lead
    prints it (it read "0.29", cut to "0...."); typed, it is a percent."""
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/fragment/mod/mix", data={"name": "wobble"})
    amt = client.app.state.svc.state.patch.mod1_amt
    page = client.get("/view/editor").text
    want = f'<output for="p-mod1_amt" data-pct>{round(amt * 100)}%</output>'
    assert want in page
