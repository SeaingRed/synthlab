"""Front-end bugs and quick wins, held over the template sources and the
rendered pages. The browser half — Back /
Forward keeping the shell, a confirm while the song plays, "Open Song ▸"
keeping the AudioContext, a menu closing on Esc and a click away — is
`tests/browser/test_frontend_contracts.py`.

- htmx 4's history restore swaps `[hx-history-elt]` (else the whole
  `<body>`) with the answer of a GET to the URL, and `/view/{name}` answers
  an htmx GET with the view alone: Back wiped the rail, both docks and the
  toasts. Every view root is the history element; one URL, two answers, so
  `Vary: HX-Request`.
- `hx-disabled-elt` is htmx 2's: htmx 4 reads `hx-disable`.
- no `window.confirm()` (it freezes every script, the song with them):
  `hx-confirm` asks in tweaktype.js's `#confirm-pop`.
- an in-page link to another page swaps it (`view_link`), never reloads.
- the dropdown `<details>` menus are popovers.
- the Quantise confirm's markup.
- the CSP's `base-uri` / `form-action` / `object-src`; a name in
  `hx-vals` through `tojson`.
- no `hx-boost`, a `<title>` per page, `closedby="any"`.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import synthlab.studio as studio
from synthlab.studio.app import make_templates
from synthlab.studio.server import create_app
from synthlab.studio.views import VIEW_LABELS, VIEW_NAMES

STUDIO = Path(studio.__file__).parent
TEMPLATES = STUDIO / "templates"
STATIC = STUDIO / "static"
VENDORED = {"htmx.min.js", "hx-prompt.min.js", "gsap.min.js"}
HX = {"HX-Request": "true"}


def _sources():
    return {
        p.relative_to(TEMPLATES).as_posix(): p.read_text(encoding="utf-8")
        for p in TEMPLATES.rglob("*.html")
    }


class _Tags(HTMLParser):
    """Every start tag of a page, its attributes decoded as a browser does."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html: str):
    p = _Tags()
    p.feed(html)
    return p.tags


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as c:
        yield c


# ---- Back / Forward keep the shell ----------------------------------------


def test_every_view_root_is_the_history_element():
    for name in VIEW_NAMES:
        src = (TEMPLATES / "views" / f"{name}.html").read_text(encoding="utf-8")
        root = re.search(r'<main\b[^>]*\bid="view"[^>]*>', src)
        assert root and "hx-history-elt" in root.group(0), name


def test_a_view_answers_vary_on_hx_request_and_names_the_tab(client):
    for name in ("editor", "song", "drums"):
        whole = client.get(f"/view/{name}")
        swap = client.get(f"/view/{name}", headers=HX)
        for r in (whole, swap):  # (gzip adds Accept-Encoding)
            assert "HX-Request" in r.headers["vary"].split(", "), name
        title = f"<title>{VIEW_LABELS[name]} · synthlab studio</title>"
        assert title in whole.text, name
        # htmx applies a swap's <title> (svg titles aside) and drops it
        assert swap.text.startswith(title), name
        assert 'hx-history-elt data-view="' in swap.text


# ---- names htmx 4 and the browser no longer read ---------------


def test_no_htmx_2_names_and_no_boost():
    for name, src in _sources().items():
        assert "hx-disabled-elt" not in src, f"{name}: htmx 4 reads hx-disable"
        assert "hx-boost" not in src, f"{name}: hx-boost is dead in htmx 4"


def test_no_island_asks_with_the_browsers_confirm():
    for js in STATIC.glob("*.js"):
        if js.name in VENDORED:
            continue
        code = re.sub(r"//.*", "", js.read_text(encoding="utf-8"))  # (comments name it)
        assert not re.search(r"\bconfirm\(", code), js.name
    tt = (STATIC / "tweaktype.js").read_text(encoding="utf-8")
    assert '"htmx:confirm"' in tt and "issueRequest" in tt and "dropRequest" in tt


def test_the_palette_and_the_sheet_close_on_a_click_outside():
    for name in ("palette", "shortcuts"):
        src = (TEMPLATES / "fragments" / f"{name}.html").read_text(encoding="utf-8")
        assert re.search(rf'<dialog id="{name}"[^>]*closedby="any"', src), name
    # (palette.js's own backdrop branch went with it)
    assert "the backdrop\n" not in (STATIC / "palette.js").read_text(encoding="utf-8")


# ---- a link to another page swaps it ------------------------------------------


def test_every_link_to_a_page_swaps_it():
    for name, src in _sources().items():
        for tag in re.findall(r"<a\b[^>]*>", src, re.S):
            if 'href="/view/' in tag or 'href="{{ it.lab_href' in tag:
                assert "hx-get=" in tag, f"{name}: {' '.join(tag.split())[:120]}"


def test_the_rendered_pages_have_no_reloading_page_link(client):
    for name in ("editor", "perform", "song", "drums", "lab", "clips"):
        for tag, a in _tags(client.get(f"/view/{name}").text):
            href = a.get("href") or ""
            if tag == "a" and href.startswith("/view/"):
                assert a.get("hx-get") and a.get("hx-target") == "#view", (name, a)
                assert a.get("hx-push-url") == "true", (name, a)


# ---- the dropdown menus are popovers -------------------------------------------


def test_the_dropdown_menus_are_popovers():
    for name, src in _sources().items():
        for cls in ("pt-menu", "row-menu", "reset-menu", "macro-assign"):
            assert not re.search(rf'<details class="{cls}\b', src), f"{name}: {cls}"
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    menus = ".pt-menu-panel, .row-menu-panel, .reset-panel, .macro-assign > form"
    assert f":is({menus})[popover]" in css
    # the reset menu is a region: morphed, so a redraw keeps it open
    reset = (TEMPLATES / "fragments" / "reset_menu.html").read_text(encoding="utf-8")
    assert (
        'hx-swap-oob="outerMorph"' in reset and 'hx-swap-oob="outerHTML"' not in reset
    )


def test_every_popover_a_page_opens_is_on_the_page(client):
    for name in ("editor", "perform", "drums", "song"):
        tags = _tags(client.get(f"/view/{name}").text)
        ids = {a.get("id") for _, a in tags}
        for _, a in tags:
            target = a.get("popovertarget")
            if target and "{" not in target:
                assert target in ids, (name, target)


def test_no_inline_anchor_pair_where_popovertarget_anchors():
    for name, src in _sources().items():
        assert "anchor-name:" not in src and "position-anchor:" not in src, name
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert (
        "@supports not (position-area" not in css
        and "@supports (position-area" not in css
    )


# ---- attributes that survive what is in a name ---------------------------


NASTY = 'my "big" bass\'s loop'


def test_the_quantise_confirm_keeps_a_name_with_spaces_and_quotes():
    env = make_templates().env
    tmpl = env.from_string(
        '{% from "fragments/loop_rows.html" import quantise_menu %}'
        "{{ quantise_menu(r) }}"
    )
    r = {"quantise": 0, "name": NASTY, "edited": True, "rates": [(4, "1/16")],
         "steps": 4, "ref": {"loop": "x"}, "cleanable": False}  # fmt: skip
    asks = [a["hx-confirm"] for _, a in _tags(tmpl.render(r=r)) if "hx-confirm" in a]
    assert len(asks) == 6  # one rate, five strengths
    assert set(asks) == {
        f"“{NASTY}” was edited on the grid: place the notes as played again over it?"
    }
    r["edited"] = False
    assert "hx-confirm" not in tmpl.render(r=r)


def test_a_name_in_hx_vals_is_json_whatever_it_holds():
    env = make_templates().env
    html = env.get_template("fragments/panel_presets.html").render(
        group="Filter", presets=[NASTY]
    )
    vals = [a["hx-vals"] for _, a in _tags(html) if "hx-vals" in a]
    assert vals and json.loads(vals[0]) == {"group": "Filter", "name": NASTY}
    interpolated = re.compile(r"""hx-vals='\{[^']*"\{\{ *(p|e)?\.?name *\}\}""")
    for name in ("fragments/presets.html", "views/library.html",
                 "fragments/drum_panels.html", "fragments/panel_presets.html",
                 "fragments/voice_panel.html"):  # fmt: skip
        src = (TEMPLATES / name).read_text(encoding="utf-8")
        assert not interpolated.search(src), name


def test_the_csp_closes_base_forms_and_plugins(client):
    csp = client.get("/").headers["content-security-policy"]
    for d in ("base-uri 'none'", "form-action 'self'", "object-src 'none'"):
        assert d in csp
