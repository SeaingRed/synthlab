"""Gate rhythm presets (`fxrack.GATE_PATTERNS`): eight named patterns for
the trance gate, as data a menu can offer. A preset is a string the unit's
pattern field could already hold — one character a 16th, at most 32 — so
choosing one (`POST /fragment/fx/set`, `name=preset`) writes that string
and nothing else; the DSP is the gate's own."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import effects as fx
from synthlab.core import SR
from synthlab.studio.fxrack import (
    GATE_PATTERNS,
    build_chain,
    clean_chain,
    gate_pattern,
)
from synthlab.studio.server import create_app
from synthlab.studio.state import Arrangement

BPM = 125.0  # a 16th is 5292 samples
STEP = 5292


def test_the_eight_presets_are_valid_gate_patterns():
    assert list(GATE_PATTERNS) == [
        "eighths",
        "onbeat",
        "offbeat",
        "trance",
        "gallop",
        "rolling",
        "dotted",
        "two_bar",
    ]
    seen = set()
    for name, preset in GATE_PATTERNS.items():
        pat = preset["pattern"]
        assert preset["label"] and set(pat) == {"x", "."}, name
        assert len(pat) in (16, 32), name  # whole bars
        # the unit's own rule keeps it as written (an invalid pattern
        # falls back to the default "x.x.x.x.")
        (unit,) = clean_chain([{"kind": "gate", "params": {}, "pattern": pat}])
        assert unit["pattern"] == pat, name
        assert gate_pattern(name) == pat
        seen.add(pat)
    assert len(seen) == 8  # eight different rhythms
    assert len(GATE_PATTERNS["two_bar"]["pattern"]) == 32
    assert GATE_PATTERNS["trance"]["pattern"] == "x.xxx.x.x.xxx.x."
    assert GATE_PATTERNS["rolling"]["pattern"] == ".xxx" * 4  # never the kick's 16th
    assert GATE_PATTERNS["offbeat"]["pattern"] == "..xx" * 4


@pytest.mark.parametrize("name", list(GATE_PATTERNS))
def test_a_preset_opens_and_shuts_on_its_own_sixteenths(name):
    """A steady tone through the gate: each 16th's level is the pattern's
    character there (open > 0.9 of the tone, shut < 0.05 — measured in the
    middle half of the step, clear of the 4 ms ramps)."""
    pat = GATE_PATTERNS[name]["pattern"]
    n = len(pat) * STEP
    x = np.ones((n, 2))
    unit = [{"kind": "gate", "params": {"depth": 1.0}, "pattern": pat}]
    y = fx.chain(x, build_chain(unit, Arrangement(bpm=BPM)))
    for i, ch in enumerate(pat):
        mid = y[i * STEP + STEP // 4 : i * STEP + 3 * STEP // 4, 0]
        if ch == "x":
            assert mid.min() > 0.9, (name, i)
        else:
            assert mid.max() < 0.05, (name, i)
    assert STEP == int(round(60.0 / BPM / 4.0 * SR))


@pytest.mark.parametrize("bad", ["", "nope", "TRANCE", "trance ", "x.x.", None, 7, []])
def test_an_unknown_preset_name_is_none(bad):
    assert gate_pattern(bad) is None


def _set(client, **data):
    return client.post("/fragment/fx/set", data={k: str(v) for k, v in data.items()})


def test_the_rack_sets_a_gates_pattern_by_preset_name(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "gate"})
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "beat_repeat"})
    chain = s.layers[s.active_layer]["fx"]
    r = _set(client, scope="layer", index=0, name="preset", value="gallop")
    assert r.status_code == 200
    chain = s.layers[s.active_layer]["fx"]
    assert chain[0]["pattern"] == GATE_PATTERNS["gallop"]["pattern"]
    assert "preset" not in chain[0]  # the name is not kept: it is a pattern
    before = [dict(u) for u in chain]
    for value in ("nope", "", "<script>", "x" * 5000, "two_bar\x00"):
        r = _set(client, scope="layer", index=0, name="preset", value=value)
        assert r.status_code == 422, value
    # a preset is the gate's: the beat repeat's pattern does not take one
    r = _set(client, scope="layer", index=1, name="preset", value="trance")
    assert r.status_code == 422
    assert [dict(u) for u in s.layers[s.active_layer]["fx"]] == before


def test_the_step_editor_toggles_a_sixteenth_and_sets_the_length(tmp_path):
    """G6: the rack draws a pattern unit's string as checks — one a 16th —
    with 16 / 32 for its length and (a gate) a menu of the named rhythms.
    A tick is `name=step`, a length `name=steps`: the same string the
    pattern field holds, so nothing in the DSP or a key is new."""
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "gate"})
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "beat_repeat"})

    def pattern(i=0) -> str:
        return s.layers[s.active_layer]["fx"][i]["pattern"]

    assert pattern() == "x.x.x.x."
    r = _set(client, scope="layer", index=0, name="step", value="1")
    assert r.status_code == 200 and pattern() == "xxx.x.x."
    _set(client, scope="layer", index=0, name="step", value="0")
    assert pattern() == ".xx.x.x."
    # the length: the pattern repeated to a bar, to two, and cut back
    _set(client, scope="layer", index=0, name="steps", value="16")
    assert pattern() == ".xx.x.x..xx.x.x."
    _set(client, scope="layer", index=0, name="steps", value="32")
    assert pattern() == ".xx.x.x..xx.x.x." * 2
    _set(client, scope="layer", index=0, name="step", value="31")
    assert pattern().endswith("x.xx") and len(pattern()) == 32  # the last one on
    r = _set(client, scope="layer", index=0, name="steps", value="16")
    assert pattern() == ".xx.x.x..xx.x.x."
    # drawn: sixteen checks, the ticked ones the pattern's, and the menu
    html = r.text
    gate = html.split('class="fx-steps"')[1].split("</div>")[0]
    assert gate.count('type="checkbox"') == 16 and gate.count("checked") == 8
    assert '"name": "preset"' in html and "Trance" in html
    assert html.count("gate rhythm") == 1  # the beat repeat has no rhythms
    # the beat repeat's pattern has the steps too
    r = _set(client, scope="layer", index=1, name="step", value="0")
    assert r.status_code == 200 and pattern(1)[0] == "x"
    # hostile: past the pattern, not a number, a length that is not offered
    before = (pattern(), pattern(1))
    for name, value in (
        ("step", "16"),
        ("step", "-1"),
        ("step", "x"),
        ("step", ""),
        ("steps", "64"),
        ("steps", "0"),
        ("steps", "sixteen"),
    ):
        r = _set(client, scope="layer", index=0, name=name, value=value)
        assert r.status_code == 422, (name, value)
    assert (pattern(), pattern(1)) == before
    # a unit with no pattern has no steps
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "reverb"})
    r = _set(client, scope="layer", index=2, name="step", value="0")
    assert r.status_code == 422
