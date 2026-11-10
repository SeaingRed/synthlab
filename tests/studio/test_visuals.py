"""The Visuals page (/view/visuals): the gallery of the top dock's scope /
spectrum variations, and the registry that draws them (static/visuals.js).

What the browser journeys prove (tests/browser/test_visuals.py) is the
drawing; this holds the page, the two lists and the hand-off from
motion.js."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app
from synthlab.studio.views import VIEW_NAMES, VIEW_PARTS
from synthlab.studio.visuals import FAMILIES, IDS, VARIATIONS

STATIC = Path("synthlab/studio/static")
# the registry is two files: visuals.js (the infrastructure, the first
# thirteen) and visuals2.js (the rest)
REGISTRY = ("visuals.js", "visuals2.js")


def _registry() -> dict[str, tuple[str, str]]:
    found = []
    for name in REGISTRY:
        js = (STATIC / name).read_text(encoding="utf-8")
        found += re.findall(
            r'add\(\{ id: "([^"]+)", name: "([^"]+)", kind: "(scope|spectrum|both)"',
            js,
        )
    ids = [i for i, _, _ in found]
    assert len(ids) == len(set(ids)), ids  # an id registered twice
    return {i: (name, kind) for i, name, kind in found}


def test_the_page_lists_what_the_registry_draws():
    """A variation is two entries — its draw in visuals.js, its card in
    visuals.py — and neither may go without the other."""
    reg = _registry()
    assert set(reg) == IDS
    for v in VARIATIONS:
        assert reg[v.id] == (v.name, v.kind), v.id
    assert len(VARIATIONS) >= 29  # 13 (visuals.js) + 16 (visuals2.js)
    # every variation is in a family, and every family has some
    assert {v.family for v in VARIATIONS} == set(FAMILIES)
    assert len({v.name for v in VARIATIONS}) == len(VARIATIONS)
    js = (STATIC / "visuals.js").read_text(encoding="utf-8")
    assert 'DEF = { scope: "classic-scope", spectrum: "classic-spectrum" }' in js


def test_the_view_is_registered_and_plays_the_loop():
    assert "visuals" in VIEW_NAMES
    assert VIEW_PARTS["visuals"] == ("synth", "drums")


def test_the_visuals_page_renders_a_card_per_variation(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    html = client.get("/view/visuals").text
    assert 'data-view="visuals"' in html and 'class="cockpit visuals-view"' in html
    view = html[html.index('id="view"') :]
    assert view.count('class="vis-card"') == len(VARIATIONS)
    # grouped by family, in FAMILIES order, after the (empty) favourites
    heads = re.findall(r'<h2 class="vis-fam-head caps">([^<]+)</h2>', view)
    assert heads == ["♥ Favourites", *FAMILIES]
    assert '<div class="vis-family" data-family="favourites" hidden>' in view
    for fam in FAMILIES:
        block = view[view.index(f'data-family="{fam.lower()}">') :]
        nxt = block.find('class="vis-family"')
        block = block[: nxt if nxt > 0 else None]
        assert re.findall(r'class="vis-card" data-vis="([^"]+)"', block) == [
            v.id for v in VARIATIONS if v.family == fam
        ]
    for v in VARIATIONS:
        assert f'data-vis="{v.id}"' in view
        assert f'aria-label="favourite {v.name}"' in view
        # the swaps are commands: the palette finds them by name
        assert f'data-cmd="Dock scope: {v.name}"' in view
        assert f'data-cmd="Dock spectrum: {v.name}"' in view
    # something to watch move: the loop's ▶ and the demo signal
    assert 'id="play"' in view and 'data-player="loop"' in view
    assert 'id="vis-demo"' in view and 'data-cmd="Demo signal: on / off"' in view
    assert 'id="vis-big"' in view and 'id="vis-full"' in view
    assert 'id="vis-shuffle"' in view
    assert 'data-cmd="Visuals: shuffle the dock"' in view
    # the rail names it, and the swap through the nav answers too
    nav = html[html.index('<nav class="views"') : html.index("</nav>")]
    assert 'href="/view/visuals"' in nav and ">Visuals</span>" in nav
    swapped = client.get("/view/visuals", headers={"HX-Request": "true"})
    assert swapped.status_code == 200 and 'data-view="visuals"' in swapped.text


def test_the_dock_draws_through_the_registry(tmp_path):
    """motion.js measures and hands the frame over; the drawing — the
    classic scope and spectrum included — is visuals.js's. Each slot names
    what it draws (the ticker)."""
    motion = (STATIC / "motion.js").read_text(encoding="utf-8")
    assert "S().visuals?.render(F, scope, spec)" in motion
    assert 'getContext("2d")' not in motion  # no drawing left behind
    page = (STATIC / "vispage.js").read_text(encoding="utf-8")
    assert "createAnalyser" not in page and "AnalyserNode" not in page
    # visuals.js's one analyser is offline: the rest picture's,
    # one note of the sound read once — never a second tap on the master
    js = (STATIC / "visuals.js").read_text(encoding="utf-8")
    assert "createAnalyser" not in js and js.count("new AnalyserNode(oc,") == 1
    assert js.count("AnalyserNode") == 1 and "new OfflineAudioContext(" in js
    # the one tap of the registry's own: the goniometer's two sides, made
    # once (off the master analyser, up-mixed to two channels) and read
    # once a frame
    two = (STATIC / "visuals2.js").read_text(encoding="utf-8")
    assert "createAnalyser" not in two and two.count("new AnalyserNode(") == 1
    assert 'channelInterpretation: "speakers"' in two
    # no `font` set on a live canvas (it resolves the page's style: a recalc a
    # frame); text is stamped from sprites drawn off the document
    assert "g.font" not in two and "new OffscreenCanvas(1, 1)" in two
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    assert '<span class="viz-name" data-viz-slot="scope"></span>' in html
    assert '<span class="viz-name" data-viz-slot="spectrum"></span>' in html
    # the registry before motion.js, the page after it
    order = [
        html.index(f"/static/{name}.js")
        for name in ("visuals", "visuals2", "motion", "vispage")
    ]
    assert order == sorted(order)


def test_every_tween_is_skipped_under_calm_motion():
    """Both islands reach GSAP only through G(), which is null under calm
    motion (the Motion setting, colours.js — never the OS's); the second
    batch tweens nothing, and what moves by itself there (the surface's
    turn, the plasma's drift, the ripples' spread) reads the same setting."""
    for name in ("visuals.js", "vispage.js"):
        js = (STATIC / name).read_text(encoding="utf-8")
        assert "const G = () => (!calm() && window.gsap) || null;" in js
        assert "gsap." not in js.replace("window.gsap", ""), name
    two = (STATIC / "visuals2.js").read_text(encoding="utf-8")
    assert "gsap" not in two
    assert "const calm = () => window.synthlab.calm();" in two
    assert two.count("calm()") >= 5


def test_the_choice_is_kept_per_browser_and_survives_a_bad_value():
    js = (STATIC / "visuals.js").read_text(encoding="utf-8")
    assert 'KEY = "synthlab.visuals"' in js
    # every storage touch is guarded: private mode, a full quota
    assert js.count("localStorage.") == 2 and js.count("try {") >= 2
    # a choice of a later file's entry (visuals2.js) is taken as it arrives
    assert "if (want.scope === v.id || want.spectrum === v.id) choice = load();" in js
    # the page's own: the big preview's pick and the favourites, guarded too
    page = (STATIC / "vispage.js").read_text(encoding="utf-8")
    assert 'BIG = "synthlab.visuals.big", FAV = "synthlab.visuals.fav"' in page
    assert page.count("localStorage.") == 2
    assert "try { return JSON.parse(localStorage.getItem(k)" in page
    assert "try { localStorage.setItem(k, JSON.stringify(v)); }" in page
