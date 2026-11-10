import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.explore import SpaceStore
from synthlab.studio.audition import phrase_arrangement
from synthlab.studio.render import RenderInputs, render_loop
from synthlab.studio.server import create_app
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_generation_returns_8_tiles(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/breed/generation", data={})
    assert r.status_code == 200
    assert r.text.count('class="tile') == 8


def test_generation_names_have_no_lineage_asterisk(tmp_path):
    import re

    client = make_client(tmp_path)
    html = client.post("/fragment/breed/generation", data={}).text
    names = re.findall(r'<span class="t-name">([^<]*)</span>', html)
    assert len(names) == 8
    assert all("*" not in name for name in names)


def test_generation_renders_mutation_badges(tmp_path):
    client = make_client(tmp_path)
    html = client.post("/fragment/breed/generation", data={}).text
    assert "badge-gene" in html


def test_steered_generation(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/breed/generation", data={"direction": "more_sub"})
    assert r.status_code == 200 and r.text.count('class="tile') == 8


def test_star_and_audition_and_adopt(tmp_path):
    import re

    client = make_client(tmp_path)
    html = client.post("/fragment/breed/generation", data={}).text
    match = re.search(r'data-tile-id="([a-f0-9]{12})"', html)
    assert match is not None
    tid = match.group(1)
    starred = client.post("/fragment/breed/star", data={"id": tid})
    assert "starred" in starred.text
    aud = client.post("/fragment/breed/audition", data={"id": tid})
    assert "preview-ready" in aud.headers.get("HX-Trigger", "")
    assert '"autoplay": true' in aud.headers.get("HX-Trigger", "")
    adopted = client.post("/fragment/breed/adopt", data={"id": tid})
    assert adopted.status_code == 200 and 'name="cutoff"' in adopted.text


def test_tile_card_plays_what_use_gives_you(tmp_path):
    import re

    client = make_client(tmp_path)
    html = client.post("/fragment/breed/generation", data={}).text
    match = re.search(r'data-note="(/breed/[a-f0-9]{12}/audition\.wav)"', html)
    assert match is not None, "tile card should carry its faithful audition URL"
    assert "/breed/note/" not in html  # the dry A1 note no longer fronts the card
    wav = client.get(match.group(1))
    assert wav.status_code == 200
    assert wav.headers["content-type"] == "audio/wav"
    assert wav.content[:4] == b"RIFF"


def test_breed_note_unknown_id_404s(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/breed/note/deadbeef0000.wav").status_code == 404


def test_space_store_grows(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    assert (tmp_path / "space.jsonl").exists()
    assert len((tmp_path / "space.jsonl").read_text().strip().splitlines()) >= 8


def test_breed_view_has_steering_buttons(tmp_path):
    html = make_client(tmp_path).get("/view/breed").text
    for d in ("more_sub", "darker", "brighter", "grittier", "cleaner"):
        assert f'"direction": "{d}"' in html or f"'direction': '{d}'" in html.replace(
            "&quot;", '"'
        )


def test_history_restore(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/breed/generation", data={})
    first_ids = set(
        __import__("re").findall(
            r'data-tile-id="([a-f0-9]{12})"', client.get("/view/breed").text
        )
    )
    client.post("/fragment/breed/generation", data={})
    r = client.post("/fragment/breed/restore", data={"index": "0"})
    restored = set(__import__("re").findall(r'data-tile-id="([a-f0-9]{12})"', r.text))
    assert restored == first_ids


def _mid_generation(monkeypatch, during):
    """`new_generation` with `during()` run while it breeds — outside the
    studio lock, where an import or a reset from another request lands."""
    from synthlab.studio.routes import breed

    real = breed.new_generation

    def slow(*a, **kw):
        tiles = real(*a, **kw)
        during()
        return tiles

    monkeypatch.setattr(breed, "new_generation", slow)


def test_a_litter_bred_for_a_session_that_was_replaced_is_dropped(
    tmp_path, monkeypatch
):
    """A generation renders outside the lock and commits when done. An
    import that replaced the session meanwhile got the OLD session's
    litter written over its board, history and stars."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/breed/generation", data={})
    theirs = json.loads(json.dumps(s.project_payload()))  # the project to import
    theirs["breed_tiles"] = theirs["breed_tiles"][:3]
    theirs["breed_history"] = [theirs["breed_tiles"]]
    theirs["breed_starred"] = [theirs["breed_tiles"][0]["id"]]
    _mid_generation(monkeypatch, lambda: s.load_project(theirs))
    r = client.post("/fragment/breed/generation", data={})
    assert r.status_code == 422 and "session changed" in r.text
    assert s.breed_tiles == theirs["breed_tiles"]
    assert s.breed_history == theirs["breed_history"]
    assert s.breed_starred == theirs["breed_starred"]


def test_a_board_reset_mid_generation_stays_reset(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/breed/generation", data={})
    _mid_generation(monkeypatch, lambda: s.reset_defaults("board"))
    r = client.post("/fragment/breed/generation", data={})
    assert r.status_code == 422 and s.breed_tiles == [] and s.breed_history == []


def test_other_edits_mid_generation_do_not_drop_the_litter(tmp_path, monkeypatch):
    """Only a replaced board is stale: a tweak, a star, a save or another
    scope's reset while a litter breeds leaves it landing as before."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/breed/generation", data={})

    def during():
        s.arrangement.bpm = 100.0
        s.save()
        assert s.reset_defaults("sound")
        client.post("/fragment/breed/star", data={"id": s.breed_tiles[0]["id"]})

    _mid_generation(monkeypatch, during)
    r = client.post("/fragment/breed/generation", data={})
    assert r.status_code == 200 and r.text.count('class="tile') == 8
    assert len(s.breed_history) == 2


def test_breed_cards_have_primary_action(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/breed/generation")
    html = client.get("/view/breed").text
    assert "Use on Synths" in html
    assert "gen-strip" in html
    assert "Breeding from" in html


def test_short_steer_pool_topped_up(tmp_path, monkeypatch):
    import numpy as np

    from synthlab.studio import breedboard
    from synthlab.studio.state import StudioState
    from synthlab.synth import preset

    state = StudioState(tmp_path)
    state.patch = preset("bass_reese")
    # the steered pool comes back short (steer() used to be the call that
    # could shrink; the board now draws one litter and measures it itself)
    real = breedboard.generation
    calls = []

    def short_first(parents, n=8, **k):
        calls.append(n)
        return real(parents, n=3 if len(calls) == 1 else n, **k)

    monkeypatch.setattr(breedboard, "generation", short_first)
    tiles = breedboard.new_generation(state, "more_sub", rng=np.random.default_rng(1))
    assert len(tiles) == 8
    assert len(calls) >= 2  # topped up


def test_audition_renders_the_breed_view_mix_not_perform_parts(tmp_path):
    """The tile's "in loop" button must audition the *synth* — the Breed
    view renders parts=("synth",). It used to call _layer_render_args()
    with its default view="perform", so it inherited state.perform_parts:
    with the synth toggled off in Perform, clicking a tile played the drum
    kit and never the sound you were breeding."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    s.perform_parts = ["drums"]  # synth switched off over in Perform
    s.save()

    html = client.post("/fragment/breed/generation", data={}).text
    tid = re.findall(r'data-tile-id="([a-f0-9]{12})"', html)[0]

    r = client.post("/fragment/breed/audition", data={"id": tid})
    assert r.status_code == 200
    url = json.loads(r.headers["HX-Trigger"])["preview-ready"]["url"]

    # the same tile rendered explicitly synth-only must be the same audio
    row = SpaceStore(s.data_dir / "space.jsonl").get(tid)
    expected = render_loop(
        RenderInputs(
            patch=Patch(**row["patch"]),
            # Breed is a bench view: it renders the reference phrase, not the
            # Perform grid, so a tile is judged against a constant and is
            # never silent because the grid happens to hold no synth notes
            arr=phrase_arrangement(s.arrangement, s.audition),
            curves=s.curves,
            layers=s.other_layers(),
            main_muted=bool(s.layers[s.active_layer].get("muted")),
            main_fx=s.layers[s.active_layer].get("fx") or [],
            master_fx=s.fx_master,
            drums_fx=s.fx_drums,
            parts=("synth",),
        )
    )
    assert url == f"/preview/{expected.key}.wav"
