"""Filter 2 and the modulators on Synths: filter 2 as one block in the
Filter panel, the Modulators panel (envelopes 3 / 4, LFOs 2 / 3), a drawn
LFO's drawing on its shape row, the twelve-slot mod matrix, the filter
drawing and the chain bar following filter 2, the form taking a drawing.
Every field is still its own row with its own name. The browser journeys
are tests/browser/test_mod_sources.py."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from synthlab.params import MOD_SLOTS
from synthlab.studio import oscfaces
from synthlab.studio.chain import chain_summary
from synthlab.studio.forms import patch_from_form
from synthlab.studio.paramui import PARAM_GROUPS, PERFORMANCE_PARAMS, spec_of
from synthlab.studio.server import create_app
from synthlab.studio.soundmeta import filter_response_svg
from synthlab.synth import Patch


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _view(client, view="editor"):
    html = client.get(f"/view/{view}", headers={"HX-Request": "true"}).text
    return html.split('id="clip-library"')[0]


def _block(html, start, end="</fieldset>"):
    return html[html.index(start) :].split(end)[0]


def test_filter_2_is_one_block_of_its_own_rows_in_the_filter_panel(client):
    html = _view(client)
    panel = _block(html, 'id="panel-filter"')
    block = _block(panel, 'class="f2-block')
    for name in ("route", "type", "cutoff", "resonance", "key_track", "env"):
        assert f'id="row-filter2_{name}"' in block, name
        assert panel.count(f'id="row-filter2_{name}"') == 1
    # the route is a stepped control (an id'd radio group), the type a select
    assert 'id="p-filter2_route" role="radiogroup"' in block
    assert '<select id="p-filter2_type" name="filter2_type">' in block
    # filter 1's type is a select of the thirteen, labelled
    sel = _block(panel, '<select id="p-filter_type"', "</select>")
    assert sel.count("<option") == 13 and ">Comb +<" in sel and ">Ladder 24<" in sel
    # its knobs are knobs
    for name in ("cutoff", "resonance", "key_track", "env"):
        assert 'class="param param-knob" id="row-filter2_' + name in block


def test_the_modulators_panel_and_the_drawings(client):
    groups = dict(PARAM_GROUPS)
    assert [s.name for s in groups["Modulators"]][:2] == ["env3_attack", "env3_decay"]
    html = _view(client)
    panel = _block(html, 'id="panel-modulators"')
    for name in ("env3_release", "env4_curve", "lfo2_rate", "lfo3_mode"):
        assert f'id="row-{name}"' in panel
    # each LFO's drawing, under its shape row, with its hidden field
    for lfo, where in (
        ("lfo", "panel-motion"),
        ("lfo2", "panel-modulators"),
        ("lfo3", "panel-modulators"),
    ):
        box = _block(_block(html, f'id="{where}"'), f'id="draw-{lfo}"', "</div>\n")
        assert f'<input type="hidden" name="{lfo}_draw" value="">' in box
        assert 'class="lfo-draw-line"' in box and "data-draw=" in box
    # an empty drawing is drawn as the triangle it plays
    assert oscfaces.draw_face("")["line"] == "0,38 0,38 60,2 120,38"


def test_the_matrix_has_twelve_routes_the_extra_eight_marked(client):
    html = _view(client)
    matrix = _block(html, 'id="panel-mod-matrix"')
    assert matrix.count('class="route"') == len(MOD_SLOTS) == 12
    assert matrix.count("data-extra") == 8
    assert 'data-slot="mod12" data-ctx="Route 12" data-extra' in matrix
    assert "data-route-add" in matrix
    bay = _block(matrix, 'id="patchbay"', "</div>")
    for src in ("lfo2", "env4", "wheel", "aftertouch", "key", "random"):
        assert f'data-src="{src}"' in bay
    for dst in ("filter2_cutoff", "fm4_level3", "formant_vowel"):
        assert f'data-dst="{dst}"' in bay


def test_the_form_takes_a_drawing_cleaned():
    p = patch_from_form({"lfo2_draw": "0.5:3,0:-1,junk"}, Patch())
    assert p.lfo2_draw == "0:-1,0.5:1"
    assert patch_from_form({"lfo2_draw": ""}, p).lfo2_draw == ""


def test_the_drawing_and_the_chain_bar_follow_filter_2():
    p = Patch(filter_type="lowpass", cutoff=500.0)
    one = filter_response_svg(p)
    two = filter_response_svg(
        replace(p, filter2_route="serial", filter2_type="comb_plus")
    )
    assert one != two and two.count(",") > one.count(",")  # the comb's teeth
    assert filter_response_svg(replace(p, filter_type="phaser"))
    stages = {s["id"]: s["text"] for s in chain_summary(p, [], [])}
    assert "Low 12" in stages["panel-filter"]
    s2 = chain_summary(
        replace(p, filter2_route="parallel", filter2_type="peak"), [], []
    )
    assert "‖ Peak" in {s["id"]: s["text"] for s in s2}["panel-filter"]


def test_the_wheels_are_performance_controls():
    assert {"mod_wheel", "aftertouch"} <= PERFORMANCE_PARAMS
    assert spec_of("mod_wheel").kind == "range"
