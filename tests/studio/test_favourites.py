"""A shipped sound's ★ (sounds.favourites, studio_data/favourites.json):
the Library's card toggles it, the one sound menu marks it (its ★ filter),
and nothing but a shipped sound can be ★ — your own sounds keep their ★ in
their preset meta."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from synthlab.studio import sounds
from synthlab.studio.recipes import cards
from synthlab.studio.server import create_app


def _client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path), base_url="http://127.0.0.1")


def test_a_shipped_sound_is_starred_and_unstarred(tmp_path):
    c = _client(tmp_path)
    name = cards()[0]["name"]  # a recipe: always a Library card
    r = c.post("/fragment/sound/star", data={"source": "recipes", "name": name})
    assert r.status_code == 200
    assert 'class="btn btn-ghost btn-icon btn-s lib-star"' in r.text
    assert 'aria-pressed="true"' in r.text  # the answer is the ★, now on
    assert sounds.favourites(tmp_path) == {f"recipes:{name}"}
    stored = json.loads((tmp_path / "favourites.json").read_text(encoding="utf-8"))
    assert stored == {"sounds": [f"recipes:{name}"]}
    # the sound menu marks it, for the ★ filter
    menu = c.get("/fragment/sound-menu").text
    assert f'value="recipes:{name}" data-star' in menu
    # the Library draws its card's ★ on
    lib = c.get("/view/library").text
    i = lib.index(f'aria-label="favourite {name}"')
    assert 'aria-pressed="true"' in lib[lib.rindex("<button", 0, i) : i]
    # again: off
    r = c.post("/fragment/sound/star", data={"source": "recipes", "name": name})
    assert 'aria-pressed="false"' in r.text
    assert sounds.favourites(tmp_path) == set()
    assert f'value="recipes:{name}" data-star' not in c.get("/fragment/sound-menu").text


def test_only_a_shipped_sound_can_be_starred(tmp_path):
    c = _client(tmp_path)
    for source, name in (("factory", "no such sound"), ("preset", "zap"), ("x", "y")):
        r = c.post("/fragment/sound/star", data={"source": source, "name": name})
        assert r.status_code == 200 and 'aria-pressed="false"' in r.text
    assert sounds.favourites(tmp_path) == set()
    assert not (tmp_path / "favourites.json").exists()


def test_a_hostile_favourites_file_reads_as_none_or_its_clean_part(tmp_path):
    (tmp_path / "favourites.json").write_text("{not json", encoding="utf-8")
    assert sounds.favourites(tmp_path) == set()
    junk = {"sounds": ["factory:A", 7, "factory:A", "x" * 10_000]}
    (tmp_path / "favourites.json").write_text(json.dumps(junk), encoding="utf-8")
    got = sounds.favourites(tmp_path)
    assert "factory:A" in got and 7 not in got
    assert all(len(k) < 10_000 for k in got)


def test_the_library_has_a_star_filter_chip(tmp_path):
    lib = _client(tmp_path).get("/view/library").text
    assert 'class="btn btn-s tagfilter" data-tag="★"' in lib
