"""The oscillator panels on Synths: the FM4 operator panel, the
wavetable picker, the sync block and drift under Warmth — drawn from the
engine's own data (`oscfaces.py`), every field still its own parameter row
with its own name, so the form, undo, the deck, MIDI learn, a macro's and
an A/B morph's out-of-band rows reach them as before. The browser journeys
are tests/browser/test_osc_panels.py."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.core import FM4_ALGOS, WAVETABLES
from synthlab.studio import oscfaces
from synthlab.studio.paramui import PARAM_GROUPS, SHAPE_DEPS
from synthlab.studio.server import create_app

FM4_KNOBS = [f"fm4_{k}{i}" for k in ("ratio", "level", "decay") for i in (1, 2, 3, 4)]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _view(client, view="editor"):
    html = client.get(f"/view/{view}", headers={"HX-Request": "true"}).text
    return html.split('id="clip-library"')[0]


def _block(html, start, end="</fieldset>"):
    return html[html.index(start) :].split(end)[0]


def test_the_fm4_panel_holds_its_fourteen_fields_as_their_own_rows(client):
    html = _view(client)
    panel = _block(html, 'id="fm4-panel"', '<h4 class="subhead" id="sub-pluck_damping"')
    # the matrix in the form's own order: ratios, levels, decays, feedback
    names = re.findall(
        r'<input type="range" id="p-(fm4_\w+)" name="\1" class="knob"', panel
    )
    assert names == [*FM4_KNOBS, "fm4_feedback"]
    for name in names:  # named for the deck, MIDI learn and a screen reader
        assert f'<label for="p-{name}" class="visually-hidden">FM4 ' in panel, name
    assert panel.count('<select id="p-fm4_algo" name="fm4_algo">') == 1
    assert '<div class="fm4 shape-fm4" id="fm4-panel">' in html  # hides with the shape
    assert html.count('id="sub-fm4_algo"') == 1
    # each algorithm drawn once, as a view of the select (hidden from AT)
    algos = _block(panel, 'class="fm4-algos"', "</span>\n  </span>")
    assert 'aria-hidden="true"' in algos[:40]
    assert re.findall(r'data-algo="(\w+)"', algos) == list(FM4_ALGOS)
    # four lamps, and the rule that lights the select's algorithm and its
    # carriers ("pairs": operators 1 and 3)
    assert panel.count('class="fm4-head"') == 4
    rule = html[html.index('option[value="pairs"]:checked)') :].split("}")[0]
    assert '.fm4-algo[data-algo="pairs"]' in rule
    assert '.fm4-head[data-op="1"]' in rule and '.fm4-head[data-op="3"]' in rule
    assert '.fm4-head[data-op="2"]' not in rule
    assert ".shape-fm4) { display: none; }" in html


def test_the_ratio_presets_are_commands_with_four_ratios_each(client):
    html = _view(client)
    menu = _block(html, 'id="fm4-ratios"', "</div>")
    presets = re.findall(
        r'data-fm4-ratios="([\d. ]+)"\s+data-cmd="FM4 ratios: ([^"]+)"', menu
    )
    assert len(presets) == len(oscfaces.FM4_RATIOS) >= 4
    for ratios, name in presets:
        assert len(ratios.split()) == 4, name
    assert ("1.0 1.0 1.0 14.0", "E-piano tine") in presets


def test_every_algorithm_is_drawn_from_its_wiring():
    """Carriers on the bottom line; every modulator one row above the
    operator it bends and a wire between them; op 4's loop over its box."""
    for name, (mods, carriers) in FM4_ALGOS.items():
        face = oscfaces.FM4_FACES[name]
        at = {op: (x, y) for op, x, y, _ in face.ops}
        assert sorted(at) == [1, 2, 3, 4]
        bottom = max(y for _, y in at.values())
        for op, _x, y, carrier in face.ops:
            assert carrier == (op in carriers)
            assert (y == bottom) == carrier
        for target, sources in mods.items():
            for s in sources:
                assert at[s][1] < at[target][1]
        assert face.wires.count("M") == sum(map(len, mods.values())) + len(carriers) + 1
        w, h = oscfaces.FM4_VIEW
        for _, x, y, _ in face.ops:
            assert 0 <= x and x + 9 <= w and 0 <= y and y + 8 <= h
    assert oscfaces.FM4_CARRIERS["three"] == (1,)


def test_the_wavetables_are_a_radio_group_of_their_own_frames(client):
    client.post(
        "/fragment/patch",
        data={"osc1": "wavetable", "wt_table": "vocal", "wt_pos": "0.4"},
    )
    html = _view(client)
    pick = _block(html, 'class="wt-pick"', '<div class="param" id="row-wt_pos"')
    # its id makes it a stepped value control (tweak.js `steps`): the deck,
    # MIDI learn and the menu reach it as they reach a select
    assert 'id="p-wt_table" role="radiogroup" aria-labelledby="lbl-wt_table"' in pick
    assert 'style="--wt-pos: 0.4"' in pick
    radios = re.findall(r'<input type="radio" name="wt_table" value="(\w+)"', pick)
    assert radios == list(WAVETABLES)
    assert re.findall(r'value="(\w+)" class="visually-hidden"\s+checked', pick) == [
        "vocal"
    ]
    # a drawing a table, a path a frame (data the page already has: no
    # fetch of /wavetables.bin)
    assert pick.count("<svg ") == len(WAVETABLES)
    assert pick.count('class="wt-f"') == sum(len(f) for f in WAVETABLES.values())
    for name, paths in oscfaces.WT_FACES.items():
        assert len(paths) == len(WAVETABLES[name])
        assert all(re.fullmatch(r"M0 \d+(L\d+ \d+){48}", p) for p in paths), name
    assert sum(map(len, (p for ps in oscfaces.WT_FACES.values() for p in ps))) < 16_000


def test_a_morph_sends_the_drawn_rows_back_drawn(client):
    """An A/B morph re-sends every row it moved by id: the wavetables come
    back as their drawings and the algorithm with its wiring, never as the
    bare select the generic row would draw."""
    s = client.app.state.studio
    s.patch = replace(s.patch, osc1="fm4", fm4_algo="stack", wt_table="basic")
    s.slot_a = replace(
        s.patch, name="a", fm4_algo="additive", wt_table="crush", fm4_ratio2=5.0
    )
    r = client.post(
        "/fragment/morph", data={"slot": "a", "t": "1"}, headers={"HX-Request": "true"}
    )
    assert r.status_code == 200
    wt = _block(r.text, 'id="row-wt_table"', "</div>\n</div>")
    assert (
        'class="wt-pick"' in wt
        and 'value="crush" class="visually-hidden"\n             checked' in wt
    )
    algo = _block(r.text, 'id="row-fm4_algo"', "</select>")
    assert '<option value="additive" selected>' in algo
    assert 'class="fm4-algos"' in r.text
    assert '<div class="param param-knob" id="row-fm4_ratio2"' in r.text
    ratio = _block(r.text, 'id="row-fm4_ratio2"', "</div>")
    assert 'class="visually-hidden"' in ratio and 'value="5.0"' in ratio


def test_sync_is_one_block_and_drift_sits_under_warmth(client):
    html = _view(client)
    sync = _block(html, 'class="sync-block', '<h4 class="subhead"')
    assert re.findall(r'id="row-(\w+)"', sync) == ["osc2_sync", "sync_pitch"]
    assert 'class="param param-knob" id="row-sync_pitch"' in sync
    character = _block(html, 'id="panel-character"')
    assert re.findall(r'id="row-(\w+)"', character)[:3] == ["drive", "drift", "glide"]
    assert "subhead" not in character  # six knobs, one line (style.css 17.1)
    assert 'class="param param-knob" id="row-drift"' in character
    # nothing lost or doubled by the moves
    for _, specs in PARAM_GROUPS:
        for spec in specs:
            assert html.count(f'id="row-{spec.name}"') == 1, spec.name
    assert set(SHAPE_DEPS["fm4"]) == {"fm4_algo", "fm4_feedback", *FM4_KNOBS}
