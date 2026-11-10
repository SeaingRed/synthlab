import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.drumfactory import DRUM_FACTORY
from synthlab.studio.server import create_app
from synthlab.studio.state import BANK_SLOTS, StudioState


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_fresh_bank_is_factory(tmp_path):
    s = StudioState(tmp_path)
    assert len(s.drum_bank) == BANK_SLOTS == 8
    assert [p["name"] for p in s.drum_bank] == [p["name"] for p in DRUM_FACTORY]
    assert s.active_slot is None


def test_factory_patterns_are_clean():
    for p in DRUM_FACTORY:
        assert p["steps"] == sorted(p["steps"])
        assert any(v == "kick" for v, _ in p["steps"])


def test_bank_save_load_round_trip_via_arrangement(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement.drums = [["kick", 0], ["hat", 2]]
    s.arrangement.drum_bars = 2
    s.arrangement.drum_steps = 4
    s.arrangement.bars, s.arrangement.steps_per_bar = 1, 8  # the synth grid
    assert s.bank_save(3, "mine")
    assert s.drum_bank[3]["name"] == "mine"
    assert s.bank_load(0)
    assert s.arrangement.drum_steps == 16
    assert s.arrangement.drums == DRUM_FACTORY[0]["steps"]
    assert s.bank_load(3)
    assert s.arrangement.drums == [["hat", 2], ["kick", 0]]
    assert s.arrangement.drum_bars == 2 and s.arrangement.drum_steps == 4
    # the drum clock never touches the synth grid's own bars/steps
    assert (s.arrangement.bars, s.arrangement.steps_per_bar) == (1, 8)
    assert s.active_slot == 3
    assert not s.bank_load(8)


def test_bank_persists_across_app_lifespan(tmp_path):
    with make_client(tmp_path) as client:
        s: StudioState = client.app.state.studio
        s.arrangement.drums = [["clap", 1]]
        s.bank_save(7, "last")
        s.save_soon()
    again = StudioState(tmp_path)
    assert again.drum_bank[7]["steps"] == [["clap", 1]]
    assert again.active_slot == 7


def test_old_state_without_bank_loads_factory(tmp_path):
    s = StudioState(tmp_path)
    s.save()
    data = json.loads((tmp_path / "state.json").read_text())
    del data["drum_bank"]
    (tmp_path / "state.json").write_text(json.dumps(data))
    again = StudioState(tmp_path)
    assert [p["name"] for p in again.drum_bank] == [p["name"] for p in DRUM_FACTORY]


def test_apply_pattern_drops_invalid_cells(tmp_path):
    s = StudioState(tmp_path)
    s.apply_pattern(
        {"steps": [["kick", 0], ["cowbell", 1], ["hat", 99], "junk"], "bars": 1}
    )
    assert s.arrangement.drums == [["kick", 0]]


def test_drum_library_round_trip(tmp_path):
    s = StudioState(tmp_path)
    s.arrangement.drums = [["snare", 1]]
    s.save_drum_pattern("my/../beat?")
    assert s.list_drum_patterns() == ["my_.._beat_"]
    s.arrangement.drums = []
    assert s.load_drum_pattern("my/../beat?")["steps"] == [["snare", 1]]
    assert s.arrangement.drums == [["snare", 1]]
    assert s.load_drum_pattern("missing") is None
    s.delete_drum_pattern("my/../beat?")
    assert s.list_drum_patterns() == []


# -- routes -----------------------------------------------------------------


def test_kit_view_has_the_beat_but_no_bank_slots(tmp_path):
    """The Kit page (served at /view/drums): the loop's
    beat and its tools, no bank slots and no library list — beats are the
    Loops panel's; ✚ Save beat posts there, Beats ▸ opens the drawer."""
    html = make_client(tmp_path).get("/view/drums").text
    assert 'id="drum-bank"' in html
    assert 'data-slot="' not in html and "bank-lib" not in html
    assert 'hx-post="/loop/beat/keep"' in html and 'popovertarget="clip-drawer"' in html
    assert 'id="drum-grid"' in html
    assert 'name="style"' in html


def test_bank_load_route_puts_factory_pattern_on_grid(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/drums/bank/load/0")
    assert r.status_code == 200
    assert 'id="drum-bank"' in r.text
    assert "preview-ready" in r.headers["HX-Trigger"]
    s: StudioState = client.app.state.studio
    assert s.arrangement.drums == DRUM_FACTORY[0]["steps"]
    assert s.active_slot == 0
    assert client.post("/fragment/drums/bank/load/9").status_code == 404


def test_bank_save_route_uses_form_slot_and_name(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/grid", data={"drum.kick.0": "on", "drum.hat.2": "on"})
    r = client.post("/fragment/drums/bank/save/0", data={"slot": "5", "name": "mine"})
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert s.drum_bank[5]["name"] == "mine"
    assert s.drum_bank[5]["steps"] == [["hat", 2], ["kick", 0]]
    assert s.active_slot == 5  # (the Kit page draws no slots)


def test_randomise_route_matches_beatgen(tmp_path):
    from synthlab.studio import beatgen

    client = make_client(tmp_path)
    r = client.post("/fragment/drums/randomise", data={"style": "breaks", "seed": "42"})
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    arr = s.arrangement
    assert arr.drums == beatgen.generate("breaks", 42, arr.drum_bars, arr.drum_steps)
    assert '<option value="breaks" selected>' in r.text
    assert (
        client.post("/fragment/drums/randomise", data={"style": "x"}).status_code == 404
    )


def test_mutate_and_clear_routes(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/bank/load/2")
    s: StudioState = client.app.state.studio
    before = list(s.arrangement.drums)
    assert client.post("/fragment/drums/mutate").status_code == 200
    assert s.arrangement.drums != before
    assert client.post("/fragment/drums/clear").status_code == 200
    assert s.arrangement.drums == []


def test_randomise_route_spans_the_drum_grid(tmp_path):
    """The drum machine has its own clock (drum_bars/drum_steps), separate
    from the synth grid (bars/steps_per_bar). Randomise must span the
    drum clock, not get truncated to a shorter synth grid."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(
        s.arrangement, bars=1, steps_per_bar=4, drum_bars=1, drum_steps=16
    )
    r = client.post("/fragment/drums/randomise", data={"style": "techno", "seed": "7"})
    assert r.status_code == 200
    arr = s.arrangement
    # the synth clock (steps_per_bar=4) would cap every step at <= 3
    assert max(st for _, st in arr.drums) >= 4


def test_mutate_route_keeps_hits_past_the_synth_grid(tmp_path):
    """Mutate must not truncate existing hits to the synth grid's length:
    a pattern on the drum machine's own 16-step clock has to
    survive a mutate even though the synth grid is only 4 steps long."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement = replace(
        s.arrangement,
        bars=1,
        steps_per_bar=4,
        drum_bars=1,
        drum_steps=16,
        drums=[["kick", i] for i in range(0, 16, 2)],
    )
    r = client.post("/fragment/drums/mutate")
    assert r.status_code == 200
    steps = [st for _, st in s.arrangement.drums]
    # the synth clock (steps_per_bar=4) would cap every step at <= 3
    assert max(steps) >= 4


def test_library_routes_round_trip(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/bank/load/1")
    r = client.post("/fragment/drums/lib/save", data={"name": "deep one"})
    assert r.status_code == 200 and "deep one" in s_lib(client)
    client.post("/fragment/drums/clear")
    r = client.post("/fragment/drums/lib/load", data={"name": "deep one"})
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert s.arrangement.drums == DRUM_FACTORY[1]["steps"]
    assert (
        client.post("/fragment/drums/lib/load", data={"name": "nope"}).status_code
        == 404
    )
    assert (
        client.post("/fragment/drums/lib/save", data={"name": " "}).status_code == 422
    )
    r = client.post("/fragment/drums/lib/delete", data={"name": "deep one"})
    assert r.status_code == 200 and "deep one" not in s_lib(client)


def s_lib(client) -> list[str]:
    """The beat library's names (the Kit page lists none:
    the Loops panel shows them as "lib" beats)."""
    return client.app.state.studio.list_drum_patterns()


# -- the transport reshapes the grid ------------------------
def _hxd() -> dict:
    return {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/drums"}


def test_transport_bars_redraws_the_grid_out_of_band(tmp_path):
    """The transport posts with hx-swap="none": a #drum-bank without
    hx-swap-oob was discarded, so the grid stayed at one bar."""
    import re

    client = make_client(tmp_path)
    r = client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hxd())
    assert r.status_code == 200
    bank_tag = re.search(r'<section id="drum-bank"[^>]*>', r.text).group(0)
    assert "hx-swap-oob" in bank_tag
    steps = client.app.state.studio.arrangement.drum_steps
    cells = re.findall(r'name="drum\.kick\.\d+"', r.text)
    assert len(cells) == 2 * steps
    # the transport comes back with it: chips and the "n bars" note agree
    tr = re.search(r'<form id="drum-transport"[^>]*>', r.text).group(0)
    assert "hx-swap-oob" in tr
    assert "2 bars · " in r.text


def test_growing_bars_copies_the_beat_into_the_new_bars(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/bank/load/0")
    s: StudioState = client.app.state.studio
    s.arrangement.drum_vel = {"4": 0.5}
    bar1 = list(s.arrangement.drums)
    client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hxd())
    arr = s.arrangement
    assert arr.drum_bars == 2
    assert [c for c in arr.drums if c[1] < 16] == bar1
    assert [[v, st - 16] for v, st in arr.drums if st >= 16] == bar1
    assert arr.drum_vel == {"4": 0.5, "20": 0.5}


def test_growing_bars_brings_hidden_hits_back_and_undo_covers_it(tmp_path):
    """Shrinking 2 -> 1 keeps bar 2, hidden; growing back replaced it with
    a copy of bar 1 (and its velocities), where the note grid shows what
    was there. A bar with nothing hidden still gets the repeat."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    hits = [["kick", 0], ["hat", 4], ["snare", 20], ["kick", 30]]
    s.arrangement = replace(
        s.arrangement, drums=hits, drum_bars=2, drum_vel={"4": 0.5, "20": 0.3}
    )
    client.post("/fragment/drums/transport", data={"bars": "1"}, headers=_hxd())
    assert s.arrangement.drum_bars == 1 and s.arrangement.drums == hits  # hidden
    client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hxd())
    arr = s.arrangement
    assert arr.drum_bars == 2 and sorted(arr.drums) == sorted(hits)
    assert arr.drum_vel == {"4": 0.5, "20": 0.3}
    # 2 -> 4 from one visible bar's worth: bars 3-4 hold nothing, so the
    # two-bar phrase (bar 2 included) repeats into them
    client.post("/fragment/drums/transport", data={"bars": "4"}, headers=_hxd())
    arr = s.arrangement
    assert sorted(arr.drums) == sorted(hits + [[v, st + 32] for v, st in hits])
    assert arr.drum_vel == {"4": 0.5, "20": 0.3, "36": 0.5, "52": 0.3}
    # one pattern undo step back
    r = client.post("/fragment/pattern/undo", headers=_hxd())
    assert r.status_code == 200
    assert s.arrangement.drum_bars == 2 and sorted(s.arrangement.drums) == sorted(hits)
    assert s.arrangement.drum_vel == {"4": 0.5, "20": 0.3}


def test_hostile_velocity_key_is_dropped_not_stored(tmp_path):
    """Minor #3: `"²".isdigit()` is True but `int("²")` raises, so a
    crafted `dvel.<key>` posted straight to the grid route (never through
    schema._step_key) could store a non-int-able key in drum_vel. Growing
    the drum bars later crashed _extend_vel with an unhandled ValueError.
    The ingestion loop must drop a non-ASCII-digit key outright."""
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/drums/grid",
        data={"drum.kick.0": "on", "dvel.²": "0.5", "dvel.3": "0.4"},
        headers=_hxd(),
    )
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert "²" not in s.arrangement.drum_vel
    assert s.arrangement.drum_vel["3"] == 0.4


def test_growing_bars_survives_a_hostile_velocity_key(tmp_path):
    """End-to-end reproduction: even if a hostile key somehow reached
    drum_vel (a hand-edited state.json, a future bypass of the ingestion
    guard), growing the drum bars must not 500."""
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.arrangement.drum_vel = {"4": 0.5, "²": 0.9}
    r = client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hxd())
    assert r.status_code == 200


def test_a_velocity_key_past_every_grid_is_dropped_and_poisons_nothing(tmp_path):
    """The ASCII-digit check alone let any run of digits into drum_vel: a
    step no grid has was stored for good (each post could add more), and
    one of 5000 digits — past what int() converts — made every later drum
    pattern tool and bar change a 500 until the studio restarted."""
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/drums/grid",
        data={
            "drum.kick.0": "on",
            "dvel." + "9" * 5000: "0.5",
            "dvel.999999": "0.5",  # a number, but a step of no grid
            "dvel.007": "0.3",  # the lane's keys are plain numbers
        },
        headers=_hxd(),
    )
    assert r.status_code == 200
    s: StudioState = client.app.state.studio
    assert s.arrangement.drum_vel == {"7": 0.3}
    for url in (
        "/fragment/pattern/drums/shift_left",
        "/fragment/pattern/drums/reverse",
    ):
        assert client.post(url, headers=_hxd()).status_code == 200
    r = client.post("/fragment/drums/transport", data={"bars": "2"}, headers=_hxd())
    assert r.status_code == 200 and s.arrangement.drum_bars == 2


def test_bar_lines_are_marked_every_bar(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/drums/transport", data={"bars": "2", "steps": "8"})
    html = client.get("/view/drums").text
    import re

    bars = re.findall(
        r'class="cell[^"]*\bbar\b[^"]*"\s+(?:checked\s+)?aria-label="kick step (\d+)"',
        html,
    )
    assert bars == ["9"]  # step 8 (0-based) opens bar 2; step 0 needs no line


# -- the bank never overwrites silently --------------------------
# (the Kit page has no Save to slot; the route keeps the rule)
def test_save_defaults_to_the_first_empty_slot(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    s.drum_bank[4] = None
    s.drum_bank[6] = None
    s.active_slot = 2
    assert s.default_save_slot() == 4
    # a blank slot field lands in the first empty slot too, never slot 2
    client.post("/fragment/drums/randomise", data={"style": "trap", "seed": "3"})
    client.post("/fragment/drums/bank/save/2", data={"slot": "", "name": ""})
    assert s.drum_bank[4]["name"] == "trap"  # named after the style, not "slot 5"
    assert s.drum_bank[2]["name"] == DRUM_FACTORY[2]["name"]
    client.post("/fragment/drums/bank/save/6", data={"name": ""})
    assert s.drum_bank[6]["name"] == "trap 2"


def test_kits_save_beat_goes_to_the_loops_only_and_never_over_a_loop(tmp_path):
    from synthlab.studio import loops

    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    bank = [dict(p) if p else None for p in s.drum_bank]
    client.post("/fragment/drums/randomise", data={"style": "trap", "seed": "3"})
    r = client.post("/loop/beat/keep", data={"name": "mine"}, headers=_hxd())
    assert r.status_code == 200 and "mine" in r.headers.get("HX-Trigger", "")
    assert loops.load(s.data_dir, "mine")["kind"] == "beat"
    assert s.drum_bank == bank and s.list_drum_patterns() == []
    # the same beat again: not kept twice
    client.post("/loop/beat/keep", data={"name": "again"}, headers=_hxd())
    assert not loops.exists(s.data_dir, "again")
    # another beat under a taken name: "… 2", the first untouched
    was = loops.load(s.data_dir, "mine")["beat"]
    client.post("/fragment/drums/randomise", data={"style": "techno", "seed": "9"})
    client.post("/loop/beat/keep", data={"name": "mine"}, headers=_hxd())
    assert loops.load(s.data_dir, "mine")["beat"] == was
    assert loops.load(s.data_dir, "mine 2")["kind"] == "beat"


def test_factory_slots_restore_from_the_drums_reset_menu(tmp_path):
    client = make_client(tmp_path)
    s: StudioState = client.app.state.studio
    client.post("/fragment/drums/bank/save/2", data={"slot": "2", "name": "mine"})
    s.drum_bank[5] = None
    assert "/fragment/drums/bank/factory" in client.get("/view/drums").text
    assert "/fragment/drums/bank/factory" not in client.get("/view/perform").text
    r = client.post("/fragment/drums/bank/factory", headers=_hxd())
    assert r.status_code == 200
    assert [p["name"] for p in s.drum_bank] == [p["name"] for p in DRUM_FACTORY]
    assert 'data-view="drums"' in r.text  # stays on the Kit page
