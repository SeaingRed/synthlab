"""The generated sound library: frozen JSON, six families, each spread
dark -> bright, served on its own page and loadable into the editor."""

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from synthlab.analysis import extract
from synthlab.studio.library import (
    FAMILIES,
    LIBRARY,
    PER_FAMILY,
    build_family,
    by_family,
    draw_patch,
    get_library,
)
from synthlab.studio.recipes import CORE_SHELF, RECIPES
from synthlab.studio.server import create_app
from synthlab.synth import Patch, Synth


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_library_json_is_complete_and_ordered():
    assert len(LIBRARY) == PER_FAMILY * len(FAMILIES)
    assert {e["family"] for e in LIBRARY} == set(FAMILIES)
    assert len({e["name"] for e in LIBRARY}) == len(LIBRARY)  # unique names
    for family, members in by_family():
        assert len(members) == PER_FAMILY, family
        cents = [m["centroid"] for m in members]
        assert cents == sorted(cents), family  # dark -> bright
        assert cents[-1] > cents[0] * 2, f"{family} has no real dark->bright spread"
        shades = {
            t for m in members for t in m["tags"] if t in ("dark", "mid", "bright")
        }
        assert shades == {"dark", "mid", "bright"}, family
        for m in members:
            assert 0.0 <= m["brightness"] <= 1.0 and len(m["spectrum"]) == 24
            assert m["patch"]["name"] == m["name"]


def test_every_name_says_what_the_sound_is_and_is_its_own():
    """The 96 were once named by `namegen` — "Obsidian Bed", "Wave Beam
    2", nothing about the sound. Each is
    named for what it measures as (shade, a trait, its oscillator, its
    family: "Dark Driven Sine Bass"), unique across the library and apart
    from every recipe's and factory sound's name (the agent looks a name up
    in each source in turn). The old name is kept as `was` and still
    finds the sound — a saved session, a URL, a clip-hub plan, the agent —
    which loads under its name now."""
    from synthlab.studio.factory import FACTORY
    from synthlab.studio.library import NOUNS, describe

    names = [e["name"] for e in LIBRARY]
    assert len(set(names)) == len(LIBRARY) == 96
    others = {e["name"] for e in RECIPES} | {e["name"] for e in FACTORY}
    assert not set(names) & others
    for e in LIBRARY:
        assert e["name"].split()[-1] == NOUNS[e["family"]], e["name"]
        assert e["was"] and e["was"] not in names
        p = get_library(e["was"])
        assert p is not None and p.name == e["name"] == e["patch"]["name"]
    assert len({e["was"] for e in LIBRARY}) == 96
    # a rebuild names its draws the same way (`build_library`): today's
    # names are what `describe` gave the stored entries. Not pinned to it:
    # a name is the sound's id, and a remeasure may move a shade tag
    assert len(set(describe(LIBRARY))) == 96


def test_library_entries_round_trip_and_still_sound_as_stored():
    """Drift check (sampled): the stored spectrum must match a fresh render
    so a synth-engine change can't silently leave the bank stale."""
    for e in LIBRARY[::11]:
        p = get_library(e["name"])
        assert isinstance(p, Patch) and p.name == e["name"]
        from synthlab.studio.soundmeta import band_magnitudes, note_duration

        pitch = FAMILIES[e["family"]]["pitch"]
        audio = Synth(p).note(pitch, note_duration(p))
        fresh = band_magnitudes(audio)
        assert max(abs(a - b) for a, b in zip(e["spectrum"], fresh, strict=True)) < 0.15
    assert get_library("nope") is None


def test_family_recipes_draw_in_range_and_render():
    for family in FAMILIES:
        r = np.random.default_rng(3)
        for _ in range(4):
            p = draw_patch(family, r)
            audio = Synth(p).note(FAMILIES[family]["pitch"], 0.4)
            assert np.all(np.isfinite(audio))
    bass = [draw_patch("bass", np.random.default_rng(s)) for s in range(6)]
    assert all(b.sub_level >= 0.2 and b.cutoff <= 1800 for b in bass)
    pads = [draw_patch("pad", np.random.default_rng(s)) for s in range(6)]
    assert all(p.attack >= 0.25 and p.release >= 0.8 for p in pads)


def test_build_family_is_deterministic_and_deduplicated():
    a = build_family("keys", n=4, seed=11, oversample=3)
    b = build_family("keys", n=4, seed=11, oversample=3)
    assert [e["name"] for e in a] == [e["name"] for e in b]
    vecs = [
        extract(
            Synth(Patch(**e["patch"])).note(FAMILIES["keys"]["pitch"], 0.6)
        ).vector()
        for e in a
    ]
    for i in range(len(vecs)):
        for j in range(i + 1, len(vecs)):
            assert float(np.linalg.norm(vecs[i] - vecs[j])) >= 0.1


def test_library_page_and_load(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/library").text
    assert 'data-view="library"' in html
    # the genre recipes, the Core shelf (its own and what it lists), the 96
    genre = [e for e in RECIPES if "core" not in e["tags"]]
    on_core = sum(len(refs) for _, refs in CORE_SHELF)
    assert html.count('class="lib-card"') == len(LIBRARY) + len(genre) + on_core
    assert html.count('class="shelf module"') == len(FAMILIES)
    assert 'hx-post="/fragment/library/load"' in html
    name = LIBRARY[0]["name"]
    assert f"/library/{name.replace(' ', '%20')}/audition.wav" in html
    r = client.get(f"/library/{name}/audition.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    r = client.post("/fragment/library/load", data={"name": name})
    assert r.status_code == 200
    assert client.app.state.studio.patch.name == name
    assert (
        client.post("/fragment/library/load", data={"name": "zzz"}).status_code == 422
    )
    nav = client.get("/").text
    assert 'href="/view/library"' in nav


def test_use_lands_you_on_synths_with_the_url_to_match(tmp_path):
    """Picking a sound is a decision, not a browse: Use puts it under the
    keys on Synths and goes there. The URL has to travel with the body —
    the old bug was the editor on screen while the URL still said
    /view/library, so Back and reload disagreed with what you could see."""
    client = TestClient(create_app(data_dir=tmp_path))
    name = LIBRARY[0]["name"]
    r = client.post(
        "/fragment/library/load",
        data={"name": name},
        headers={"HX-Current-URL": "http://x/view/library"},
    )
    assert r.status_code == 200
    assert 'data-view="editor"' in r.text, "Use did not land on Synths"
    assert r.headers["HX-Push-Url"] == "/view/editor", "the URL stayed behind"
    assert client.app.state.studio.patch.name == name
    # and the sound bar came with it — Synths' own transport, not the shelf's
    assert 'id="sound-bar"' in r.text


def test_use_from_the_editor_still_renders_the_editor(tmp_path):
    """The same helper serves the editor's own preset loads; it must keep
    answering with the editor when that is where the request came from."""
    client = TestClient(create_app(data_dir=tmp_path))
    r = client.post(
        "/fragment/library/load",
        data={"name": LIBRARY[0]["name"]},
        headers={"HX-Current-URL": "http://x/view/editor"},
    )
    assert r.status_code == 200
    assert 'data-view="editor"' in r.text


def test_filter_is_reapplied_after_a_swap():
    """Morph keeps what the user typed in the search box, but the cards
    arrive carrying the server's `hidden` state, which knows nothing about a
    client-side filter. Without a re-apply, "Use" on a filtered Library left
    the box reading "bass" above all 96 sounds — the box and the list
    disagreeing is the same lying-UI bug as [hidden] losing to `display`."""
    # (the Library's own island)
    js = Path("synthlab/studio/static/library.js").read_text(encoding="utf-8")
    assert 'addEventListener("htmx:after:settle", apply)' in js


def test_library_use_morphs_rather_than_rebuilding():
    """3381 nodes is the most expensive swap in the app."""
    html = Path("synthlab/studio/templates/views/library.html").read_text(
        encoding="utf-8"
    )
    assert 'hx-target="body" hx-swap="innerMorph"' in html
    assert 'hx-swap="innerHTML"' not in html


def test_a_card_reads_at_a_glance(tmp_path):
    """A card's centre reads "12.7 kHz", never
    "12682 Hz" (it ran into the name); its tags are chips, the genre marked
    apart from the role and the tone; its keys carry a short label and the
    rest in an <i> the dock shows — so none is cut off on a card ("Use on
    Synt") and the dock still says it all."""
    from synthlab.studio.words import hz

    assert [hz(v) for v in (0, 26, 999.4, 999.6, 1904, 12682, None)] == [
        "0 Hz",
        "26 Hz",
        "999 Hz",
        "1.0 kHz",
        "1.9 kHz",
        "12.7 kHz",
        "0 Hz",
    ]
    html = make_client(tmp_path).get("/view/library").text
    cymbal = html.split('data-name="Backwards Cymbal"')[1].split("</article>")[0]
    assert '<span class="lib-hz">12.7 kHz</span>' in cymbal
    assert '<i class="g">sixties</i>' in cymbal and "<i>fx</i>" in cymbal
    assert ">▶<i> in your</i> mix</button>" in cymbal
    assert ">+<i> Layer</i></button>" in cymbal
    assert ">Use<i> on Synths</i></button>" in cymbal
    assert '<dd class="lib-chain"><i>' in cymbal  # the chain, a chip a unit
    first = LIBRARY[0]["name"]
    card = html.split(f'data-name="{first}"')[1].split("</article>")[0]
    assert '<i class="tone-dark">dark</i>' in card
    # the filter: three captioned runs, and cards or rows
    for run in ("family", "tone", "genre"):
        assert f'<span class="seg" role="group" aria-label="{run}">' in html
    assert (
        'data-lib-view="rows"' in html and 'data-cmd="Show the library as rows"' in html
    )
    # each family's head strip says which way it runs
    # (the recipes', the core library's eight categories, the six families)
    assert html.count('<span class="lib-legend" aria-hidden="true">') == 1 + len(
        CORE_SHELF
    ) + len(FAMILIES)


def test_the_librarys_window_is_laid_out_in_both_densities():
    """The dock (the selected sound) lived in dense.css alone, so the comfy
    density — and a touch-first screen, which never loads dense.css — got
    a wall of cards and no dock at all.
    The window-high grid and the dock's placement are style.css's now."""
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    dense = Path("synthlab/studio/static/dense.css").read_text(encoding="utf-8")
    at = css.index("/* ---------- 9b1. Sound library")
    section = css[at : css.index("/* ---------- 9b. Sound map", at)]
    assert '"browser dock" minmax(0, 1fr)' in section
    assert (
        ".lib-card.active > .lib-detail { position: absolute; grid-area: dock;"
        in section
    )
    assert "grid-area: dock" not in dense and ".lib-grid" not in dense
