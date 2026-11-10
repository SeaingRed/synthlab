"""The workspace's server half (studio/workspace.py, fragments/ws_menu.html,
`GET /fragment/module/{key}`): each page's Layout menu, its presets as data
the island can draw, and a module a page adds. The layout itself is the
browser's (static/workspace.js): tests/browser/test_workspace.py drives it."""

import json
import re

from fastapi.testclient import TestClient

from synthlab.studio import workspace
from synthlab.studio.server import create_app

DOCKS = {"editor": "synths", "perform": "perform", "drums": "drums", "clips": "clips"}


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _ids(tree: dict) -> list[str]:
    return list(tree["t"]) if "t" in tree else [i for k in tree["k"] for i in _ids(k)]


def _well_formed(tree: dict) -> None:
    if "t" in tree:
        assert tree["t"] and 0 <= tree["a"] < len(tree["t"]), tree
        return
    assert tree["d"] in ("row", "col") and len(tree["k"]) == len(tree["s"]) >= 2, tree
    for k in tree["k"]:
        _well_formed(k)


def test_each_page_names_its_dock_and_draws_its_layout_menu(tmp_path):
    client = _client(tmp_path)
    for view, dock in DOCKS.items():
        html = client.get(f"/view/{view}").text
        assert f'data-view="{view}" data-ws-dock="{dock}"' in html, view
        assert (
            'popovertarget="ws-menu"' in html and 'data-cmd="Reset the layout"' in html
        )
        for name, _ in workspace.PRESETS.get(view, []):
            assert f'data-cmd="Layout: {name}"' in html
    # Synths' panels are one module of the page and a dock of their own
    html = client.get("/view/editor").text
    assert 'data-module="synths.panels" data-ws-dock="synths.panels"' in html
    # every block of the page a module of its own: the two FX
    # racks, the macros, the XY pad, the Bass check and the sub spectrum —
    # their wrappers only places in the server's grid (`data-ws-pass`)
    for mid in (
        "fx.layer",
        "fx.master",
        "synths.macros",
        "synths.xy",
        "synths.bass",
        "synths.sub",
    ):
        assert f'data-module="{mid}"' in html, mid
    assert 'data-module="fx.rack"' not in html
    assert 'data-module="synths.perform"' not in html
    for wrapper in ('id="fx-rack"', 'id="perform-tools"', 'class="bass-check rack"'):
        tag = html[html.index(wrapper) :].split(">", 1)[0]
        assert "data-ws-pass" in tag and "data-module" not in tag, tag
    # each rack answers for itself: its posts take only its own fieldset
    assert 'hx-target="#fx-scope-master" hx-select="#fx-scope-master"' in html
    # a panel out of #patch still posts: the form hears `ws-input` and the
    # sound bar's post takes the fields that name the form
    assert 'hx-trigger="input delay:150ms, ws-input delay:150ms, submit"' in html
    assert html.count('hx-include="#patch, [form=patch], #sound-bar"') == 2


def test_the_bass_answer_carries_the_sub_spectrum_to_its_own_module(tmp_path):
    """On Synths the sub spectrum is a module of its own (#bass-sub): the
    Bass check's answer sends it out of band; the Bass page keeps its card."""
    client = _client(tmp_path)
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    r = client.get("/fragment/bass", headers=hx)
    assert r.status_code == 200
    panels, sub = r.text.split('<section class="sub-module', 1)
    assert 'id="bass-panels"' in panels and "meter-card module wide" not in panels
    assert 'id="bass-sub"' in sub and 'hx-swap-oob="outerMorph"' in sub
    assert 'data-module="synths.sub"' in sub and "<svg" in sub
    hx["HX-Current-URL"] = "http://testserver/view/bass"
    r = client.get("/fragment/bass", headers=hx)
    assert "meter-card module wide" in r.text and 'id="bass-sub"' not in r.text


def test_a_preset_names_only_the_modules_its_page_draws(tmp_path):
    client = _client(tmp_path)
    for view, presets in workspace.PRESETS.items():
        html = client.get(f"/view/{view}").text
        drawn = set(re.findall(r'data-module="([^"]+)"', html))
        assert 2 <= len(presets) <= 3, view
        for name, spec in presets:
            for dock, tree in spec["docks"].items():
                assert f'data-ws-dock="{dock}"' in html, (view, name, dock)
                _well_formed(tree)
                ids = _ids(tree)
                assert len(ids) == len(set(ids)) and set(ids) <= drawn, (
                    name,
                    set(ids) - drawn,
                )
            assert set(spec.get("fold", [])) <= drawn
        # the preset rides its button as JSON the island parses
        m = re.search(r"data-ws-preset='([^']+)'", html)
        assert json.loads(m.group(1).replace("&#34;", '"').replace("&quot;", '"'))[
            "docks"
        ]


def test_a_page_adds_the_fx_rack_from_its_module_route(tmp_path):
    client = _client(tmp_path)
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/perform"}
    r = client.get("/fragment/module/fx-rack", headers=hx)
    assert r.status_code == 200
    assert 'id="fx-rack"' in r.text and 'data-module="fx.rack"' in r.text
    assert "Layer 1 FX" in r.text and "Master FX" in r.text
    # Perform's menu offers it; Synths draws its own and may not ask for a second
    assert 'data-ws-url="/fragment/module/fx-rack"' in client.get("/view/perform").text
    hx["HX-Current-URL"] = "http://testserver/view/editor"
    assert client.get("/fragment/module/fx-rack", headers=hx).status_code == 404
    assert client.get("/fragment/module/nope", headers=hx).status_code == 404
