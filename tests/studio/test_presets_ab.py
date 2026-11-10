import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_preset_save_list_load_delete(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/patch", data={"cutoff": "555"})
    r = client.post("/fragment/preset/save", data={"name": "deep one"})
    assert r.status_code == 200 and "deep one" in r.text

    client.post("/fragment/patch", data={"cutoff": "3000"})
    r = client.post("/fragment/preset/load", data={"name": "deep one"})
    assert r.status_code == 200
    assert 'value="555' in r.text  # patch restored in form
    assert "preview-ready" in r.headers.get("HX-Trigger", "")

    r = client.post("/fragment/preset/delete", data={"name": "deep one"})
    assert "deep one" not in r.text


def test_preset_load_with_unknown_keys_is_tolerant(tmp_path):
    """A preset file with fields the current build doesn't recognise (a
    field renamed/removed since it was saved) used to be treated as
    corrupt and rejected outright. Patch loading is tolerant:
    the unknown keys are dropped (with a warning) and whatever's left
    loads as a patch — never a 422, never fatal."""
    client = make_client(tmp_path)
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir(parents=True, exist_ok=True)
    (preset_dir / "bogus.json").write_text('{"nonsense": 1}', encoding="utf-8")

    r = client.post("/fragment/preset/load", data={"name": "bogus"})
    assert r.status_code == 200
    assert client.app.state.studio.patch == Patch()


def test_preset_load_of_missing_file_returns_422_toast(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/preset/load", data={"name": "does not exist"})
    assert r.status_code == 422
    assert "toast" in r.text


def _pressed(html: str) -> list[str]:
    """The A/B buttons drawn as on (aria-pressed), by label."""
    strip = html.split('id="ab-slots"', 1)[1].split("</span>", 1)[0]
    return re.findall(r'aria-pressed="true"[^>]*>([AB])', strip)


def test_ab_compare_flow(tmp_path):
    """Store A, tweak, B: two versions. Only the one you hear is lit; each
    keeps its own later edits across switches; ⇄ goes to the other."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "555"})
        r = client.post("/fragment/ab/slot_a")  # store 555 as A
        assert _pressed(r.text) == ["A"]
        client.post("/fragment/patch", data={"cutoff": "3000"})
        r = client.post("/fragment/ab/slot_b")  # the tweak branches into B
        assert _pressed(r.text) == ["B"]
        assert float(s.slot_a.cutoff) == 555.0 and float(s.slot_b.cutoff) == 3000.0
        client.post("/fragment/patch", data={"cutoff": "4000"})  # editing B
        r = client.post("/fragment/ab/slot_a")
        assert _pressed(r.text) == ["A"] and 'value="555' in r.text
        assert "preview-ready" in r.headers.get("HX-Trigger", "")
        assert float(s.slot_b.cutoff) == 4000.0  # B kept its edit
        r = client.post("/fragment/ab/swap")  # ⇄: the other slot
        assert _pressed(r.text) == ["B"] and float(s.patch.cutoff) == 4000.0


def test_both_slots_filled_is_not_both_on(tmp_path):
    """Holding a sound is the ● dot, never the lit state: the strip used to
    light every filled slot, so A and B both looked on."""
    with make_client(tmp_path) as client:
        client.post("/fragment/ab/slot_a")
        r = client.post("/fragment/ab/slot_b")
        strip = r.text.split('id="ab-slots"', 1)[1].split("</span>", 1)[0]
        assert strip.count("●") == 2 and _pressed(r.text) == ["B"]
        # a new sound from the library is in neither slot: nothing lit
        from synthlab.studio.factory import FACTORY

        r = client.post("/fragment/factory/load", data={"name": FACTORY[0]["name"]})
        assert _pressed(r.text) == []
        assert client.app.state.studio.ab_live is None


def test_swap_with_empty_slot_is_noop_with_toast(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/ab/swap")
    assert r.status_code == 422
    assert "toast" in r.text


def test_sound_library_cards(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/preset/save", data={"name": "glide"})
    html = client.get("/").text
    assert "sound-card" in html and "spark" in html and "tagchips" in html
    assert 'class="del"' in html  # two-tap delete details
    assert "Factory" in html  # empty-state section present


def test_load_stashes_previous_sound_and_restore_swaps_back(tmp_path):
    """Loading destroys unsaved tweaks AND re-marks the revert baseline over
    them — every load path must stash the outgoing
    patch so there is a guaranteed way back."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "777"})
        client.post("/fragment/preset/save", data={"name": "keeper"})
        client.post("/fragment/patch", data={"cutoff": "555"})  # unsaved tweak
        client.post("/fragment/preset/load", data={"name": "keeper"})
        assert float(s.slot_prev.cutoff) == 555.0  # tweak stashed
        r = client.post("/fragment/restore_prev")
        assert r.status_code == 200
        assert float(s.patch.cutoff) == 555.0  # and returned


def test_factory_and_library_loads_stash_too(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "444"})
        from synthlab.studio.factory import FACTORY

        r = client.post("/fragment/factory/load", data={"name": FACTORY[0]["name"]})
        assert r.status_code == 200
        assert float(s.slot_prev.cutoff) == 444.0
        client.post("/fragment/patch", data={"cutoff": "333"})
        lib = client.get("/view/library").text
        # the first generated-family card (the Recipes shelf comes first)
        families = lib.split('id="shelf-recipes"')[-1].split("</section>", 1)[1]
        name = families.split('data-name="')[1].split('"')[0]
        client.post("/fragment/library/load", data={"name": name})
        assert float(s.slot_prev.cutoff) == 333.0


def test_restore_prev_with_nothing_stashed_is_a_toast(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/fragment/restore_prev")
        assert r.status_code == 422


def test_b_slot_is_reachable_while_a_is_filled(tmp_path):
    """The old swap always preferred A: once A held anything, B was
    unreachable forever — a two-slot compare that was really one slot plus
    a decoy."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "500"})
        client.post("/fragment/ab/slot_a")  # store 500 in A
        client.post("/fragment/patch", data={"cutoff": "900"})
        client.post("/fragment/ab/slot_b")  # store 900 in B
        client.post("/fragment/patch", data={"cutoff": "1200"})
        client.post("/fragment/ab/slot_a")  # to A: B keeps the 1200 edit
        assert float(s.patch.cutoff) == 500.0
        assert float(s.slot_b.cutoff) == 1200.0
        assert float(s.slot_a.cutoff) == 500.0


def test_store_returns_lit_slot_fragment(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/fragment/ab/slot_a")
        assert r.status_code == 200
        assert 'id="ab-slots"' in r.text and "filled" in r.text


def test_morph_halfway_lerps_lin_and_log_fields(tmp_path):
    """Patch is a flat typed genome (LIN/LOG/INT/CAT) built for exactly
    this: interpolating the live sound toward a slot."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "400", "resonance": "0.2"})
        client.post("/fragment/ab/slot_a")  # store, live patch unchanged...
        s.slot_a = s.patch.tweak(cutoff=400.0, resonance=0.2)
        client.post("/fragment/patch", data={"cutoff": "1600", "resonance": "0.6"})
        r = client.post("/fragment/morph", data={"t": "0.5", "slot": "a"})
        assert r.status_code == 200
        assert abs(float(s.patch.cutoff) - 800.0) < 1.0  # log lerp
        assert abs(float(s.patch.resonance) - 0.4) < 1e-6  # lin lerp


def test_morph_with_empty_slot_is_a_toast(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/fragment/morph", data={"t": "0.5", "slot": "b"})
        assert r.status_code == 422


def test_rename_preset_moves_both_files(tmp_path):
    """Rename must not require a load (destroying tweaks) + re-save."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/preset/save", data={"name": "old name"})
        r = client.post(
            "/fragment/preset/rename",
            data={"name": "old name"},
            headers={"HX-Prompt": "new name"},
        )
        assert r.status_code == 200
        assert s.list_presets() == ["new name"]
        assert not (tmp_path / "presets" / "old name.json").exists()
        assert (tmp_path / "presets" / "new name.meta.json").exists()


def test_rename_refuses_collisions(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/fragment/preset/save", data={"name": "one"})
        client.post("/fragment/preset/save", data={"name": "two"})
        r = client.post(
            "/fragment/preset/rename",
            data={"name": "one"},
            headers={"HX-Prompt": "two"},
        )
        assert r.status_code == 422


def test_dock_has_a_search_input(tmp_path):
    html = make_client(tmp_path).get("/").text
    dock = html.split('class="dock module"')[1].split("</aside>")[0]
    assert 'id="dock-filter"' in dock
    assert "data-tags" in dock  # cards expose tags for the client filter


def test_save_v2_round_trips_layers_fx_and_curves(tmp_path):
    """'Save sound…' captured only the active layer's patch — layers, FX
    and curves silently dropped while the audition played them. Format 2
    keeps the whole sound; flat v1 JSON still loads."""
    import json as _json

    with make_client(tmp_path) as client:
        s = client.app.state.studio
        client.post("/fragment/patch", data={"cutoff": "555"})
        s.layers[1] = {**s.layers[1], "patch": {"name": "pad"}, "cells": [["A1", 0]]}
        s.layers[s.active_layer]["fx"] = [{"kind": "reverb", "params": {}}]
        s.curves = {"cutoff": [[0, 50], [100, 80]]}
        client.post(
            "/fragment/preset/save", data={"name": "stack", "include_layers": "on"}
        )
        raw = _json.loads((tmp_path / "presets" / "stack.json").read_text("utf-8"))
        assert raw.get("format") == 2
        # wipe, then load back
        client.post("/fragment/reset", data={"scope": "all", "mode": "defaults"})
        s.layers[1] = {"patch": None, "cells": [], "fx": [], "muted": False}
        s.curves = {}
        client.post("/fragment/preset/load", data={"name": "stack"})
        assert float(s.patch.cutoff) == 555.0
        assert s.layers[1].get("patch", {}).get("name") == "pad"
        assert s.layers[s.active_layer]["fx"][0]["kind"] == "reverb"
        assert "cutoff" in s.curves


def test_save_without_flag_stays_flat_v1(tmp_path):
    import json as _json

    with make_client(tmp_path) as client:
        client.post("/fragment/preset/save", data={"name": "plain"})
        raw = _json.loads((tmp_path / "presets" / "plain.json").read_text("utf-8"))
        assert "format" not in raw and "osc1" in raw  # a bare Patch dict
