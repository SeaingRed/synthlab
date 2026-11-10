"""Idle controls (paramui.idle): a panel control the engine cannot hear
under the patch's other settings is drawn idle with why (otherwise a
section such as Character seems not to work at times): unison detune
and stereo spread do nothing with one voice, glide nothing on a phrase
without slides. The rule is `params.live`, the breeder's own; this holds
the reasons and the page to it."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from synthlab.params import GENES, live
from synthlab.studio.audition import PHRASES, has_slides
from synthlab.studio.paramui import IDLE_WHY, PARAM_GROUPS, idle
from synthlab.studio.server import create_app
from synthlab.synth import Patch

ED = {"HX-Current-URL": "http://testserver/view/editor", "Origin": "http://testserver"}


def test_idle_is_the_engines_own_rule_with_a_reason():
    shown = {spec.name for _, specs in PARAM_GROUPS for spec in specs}
    for p in (
        Patch(),
        Patch(unison=3, osc2="saw", vibrato_depth=0.3),
        Patch(osc1="square"),
    ):
        got = idle(p)
        heard = live(p)
        assert all(n in shown and n in GENES and n not in heard for n in got)
        # every idle control says why — never the bare fallback for these
        assert all(got[n] == IDLE_WHY[n] for n in got if n in IDLE_WHY)
    one, five = idle(Patch(unison=1)), idle(Patch(unison=5))
    assert one["unison_detune"] == "needs 2 or more unison voices"
    assert "unison_detune" not in five and "unison_spread" not in five
    assert "pwm_depth" not in idle(Patch(osc1="square"))


def test_glide_is_idle_only_on_a_phrase_without_slides():
    assert [k for k in PHRASES if has_slides(k)] == ["riff"]
    p = Patch(glide=0.2)
    assert "glide" in idle(p, phrase_slides=False)
    assert "glide" not in idle(p, phrase_slides=True)
    assert "glide" not in idle(Patch(glide=0.0), phrase_slides=False)


def test_the_panel_draws_them_and_an_edit_names_them_again(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    s = client.app.state.studio
    s.patch = replace(s.patch, unison=1)
    page = client.get("/view/editor").text
    row = page[
        page.index('id="row-unison_detune"') - 200 : page.index(
            'id="row-unison_detune"'
        )
        + 400
    ]
    assert "data-idle" in row and "no effect now: needs 2 or more unison voices" in row
    # an edit that gives it voices: the response says it is no longer idle
    r = client.post("/fragment/patch", data={"unison": "4"}, headers=ED)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "preview-ready" in trig and "unison_detune" not in trig["patch-idle"]
