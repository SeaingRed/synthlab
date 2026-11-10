"""The design system (studio docs/frontend.md, "Design system"): the
tokens hold WCAG AA in both
themes, the canonical components exist with every state, the shell is
built from them, and the shell's features are declared, not copied.

Nothing here opens a browser: tests/browser/test_shell.py drives the
palette, the sheet, the tooltips, the status strip and the pen.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio.clips import save_clip
from synthlab.studio.server import create_app
from synthlab.studio.views import VIEW_NAMES

STATIC = Path("synthlab/studio/static")
TEMPLATES = Path("synthlab/studio/templates")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
DENSE = (STATIC / "dense.css").read_text(encoding="utf-8")


# ---- contrast ---------------------------------------------------------------
def _rgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    """oklch -> linear sRGB, clipped to the gamut."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bb = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return tuple(min(1.0, max(0.0, x)) for x in (r, g, bb))


def _ratio(fg, bg) -> float:
    def lum(c):
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    hi, lo = sorted((lum(fg), lum(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(block: str) -> dict[str, str]:
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
    return {
        k: " ".join(v.split()) for k, v in re.findall(r"(--[\w-]+):\s*([^;]*);", block)
    }


def _block(start: str) -> str:
    i = CSS.index(start)
    return CSS[i : CSS.index("\n}", i)]


def _root() -> str:
    """Section 1's `:root` tokens: the switches, the dials and the colours
    (`:root, .look-sw`: a theme's swatch recomputes them), the scales."""
    head = CSS[: CSS.index("/* ---------- 2. Base ---------- */")]
    starts = [m.start() for m in re.finditer(r"(?m)^:root(?:, \.look-sw)? \{", head)]
    return "\n".join(head[i : head.index("\n}", i)] for i in starts)


# ---- the colour system: section 1 resolved the way the browser does ------
# Every colour is written once, `oklch(var(--l-x) var(--c-x) calc(var(--hue-x)
# + var(--hue-shift)))`, and each depth is a night | day pair `var(--night, N)
# var(--day, D)`: a theme sets one switch to `initial` (the guaranteed-invalid
# value: that column's fallback comes through) and the other to a space.
SWITCHES = {"night": {"--night": "initial", "--day": ""}}
SWITCHES["day"] = {"--night": "", "--day": "initial"}


def _close(text: str, i: int) -> int:
    """The index of the ")" that closes the "(" at text[i - 1]."""
    depth = 1
    while depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    return i - 1


def _resolve(value: str, env: dict[str, str]) -> str | None:
    """var() substitution, fallbacks included; None = invalid at computed time."""
    out, i = [], 0
    while (j := value.find("var(", i)) >= 0:
        k = _close(value, j + 4)
        name, comma, fallback = value[j + 4 : k].partition(",")
        got = env.get(name.strip())
        if got is not None and got != "initial":
            text = _resolve(got, env)
        else:
            text = _resolve(fallback, env) if comma else None
        if text is None:
            return None
        out += [value[i:j], text]
        i = k + 1
    return "".join(out + [value[i:]])


def _arith(expr: str) -> float:
    """A calc() of numbers: + - * / and parentheses, and min / max / clamp
    (the ladder's depths are clamped to 0..1), nothing else."""
    funcs = {"min": min, "max": max, "clamp": lambda lo, x, hi: max(lo, min(x, hi))}

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return float(node.value)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in funcs:
            return funcs[node.func.id](*(ev(a) for a in node.args))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.BinOp):
            ops = {
                ast.Add: operator.add,
                ast.Sub: operator.sub,
                ast.Mult: operator.mul,
                ast.Div: operator.truediv,
            }
            return ops[type(node.op)](ev(node.left), ev(node.right))
        raise ValueError(expr)

    return ev(ast.parse(expr.replace("calc", ""), mode="eval").body)


def _lch(value: str | None) -> tuple[float, float, float] | None:
    """`oklch(L C H)` with calc() channels -> numbers (None for anything else:
    a gradient, a shadow, a relative colour)."""
    if not value or not value.startswith("oklch(") or "from" in value:
        return None
    inner = value[6 : _close(value, 6)]
    args, depth, cur = [], 0, ""
    for ch in inner + " ":
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch.isspace() and depth == 0:
            if cur:
                args.append(cur)
            cur = ""
        else:
            cur += ch
    if len(args) != 3:
        return None
    return tuple(0.0 if a == "none" else _arith(a) for a in args)


def _resolved(name: str, **over: str) -> dict[str, tuple[float, float, float]]:
    """Every plain colour token of a theme as (L, C, H); `over` sets a hue
    the way the Colours popover does (on <html>, in both themes)."""
    env = _tokens(_root()) | SWITCHES[name]
    env |= {f"--{k.replace('_', '-')}": v for k, v in over.items()}
    out = {}
    for key, value in env.items():
        got = _lch(_resolve(value, env))
        if got is not None:
            out[key] = got
    return out


def _theme(name: str) -> dict[str, tuple[float, float, float]]:
    return {k: _rgb(*lch) for k, lch in _resolved(name).items()}


SURFACES = ("--bg", "--bg-raised", "--bg-inset", "--bg-over")
# (foreground, backgrounds, the WCAG 2.1 floor)
PAIRS = [
    ("--text", SURFACES, 4.5),
    ("--text-dim", SURFACES, 4.5),
    # a dimmed hint (a pad's key, the matrix's ± ✕): never an opacity on text
    ("--text-faint", SURFACES, 3.0),
    # a hue used AS text has its own token: the fills fail AA by day
    ("--accent-text", SURFACES, 4.5),
    ("--signal-text", SURFACES, 4.5),
    ("--danger-text", SURFACES, 4.5),
    ("--ai-text", SURFACES, 4.5),
    ("--go-text", SURFACES, 4.5),
    ("--pause-text", SURFACES, 4.5),
    # text on a hue fill
    ("--on-accent", ("--accent",), 4.5),
    ("--on-signal", ("--signal",), 4.5),
    ("--on-danger", ("--danger",), 4.5),
    ("--on-ai", ("--ai",), 4.5),
    ("--on-go", ("--go",), 4.5),
    ("--on-pause", ("--pause",), 4.5),
    # the edge of a field: 1.4.11 non-text contrast
    ("--line-strong", ("--bg", "--bg-raised", "--bg-inset"), 3.0),
    ("--key-label", ("--key-white",), 4.5),
]


@pytest.mark.parametrize("theme", ["night", "day"])
def test_token_pairs_hold_wcag_aa(theme):
    """Every text / surface pair a component may combine, computed from the
    stylesheet's own values, so a retuned token cannot quietly fail."""
    t = _theme(theme)
    bad = []
    for fg, backgrounds, floor in PAIRS:
        for bg in backgrounds:
            got = _ratio(t[fg], t[bg])
            if got < floor:
                bad.append(f"{fg} on {bg}: {got:.2f} < {floor}")
    assert not bad, f"{theme}: " + "; ".join(bad)


def test_the_fills_themselves_are_why_the_text_tokens_exist():
    """By day `--accent` / `--signal` as text are under 4.5:1 on the page —
    used as text they failed contrast in seven places. If a retune ever
    makes them pass, the text tokens can go; until then nothing may use a
    fill as a text colour."""
    day = _theme("day")
    assert _ratio(day["--accent"], day["--bg"]) < 4.5
    assert _ratio(day["--signal"], day["--bg"]) < 4.5
    for hue in ("accent", "signal", "danger"):
        for sheet in (CSS, DENSE):
            assert not re.search(rf"(?<![-\w])color: var\(--{hue}\)", sheet), hue


def test_one_palette_and_a_theme_is_two_switches():
    """The colours are written once, in section 1's `:root`, each depth as a
    night | day pair; `data-theme="day"` and "system" under a light OS only
    flip the two switches (they were two copies of the whole day palette,
    which a test had to hold equal). No other rule sets a colour token."""
    flip = {"--night": "", "--day": "initial"}
    day = re.search(r'\nhtml\[data-theme="day"\] \{([^}]*)\}', CSS)
    system = re.search(
        r'@media \(prefers-color-scheme: light\) \{ html\[data-theme="system"\] '
        r"\{([^}]*)\}",
        CSS,
    )
    assert _tokens(day.group(1)) == flip and _tokens(system.group(1)) == flip
    assert "color-scheme: var(--night, dark) var(--day, light);" in _block(":root {")
    colours = set(_resolved("night")) | {"--e1", "--e2", "--e3", "--scrim"}
    for sheet in (CSS, DENSE):
        body = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
        for name in colours:
            n = len(re.findall(rf"(?<![\w-]){re.escape(name)}:", body))
            # (dense.css takes the raised controls' shadow away: --e1: none)
            assert n == (sheet is CSS) + (sheet is DENSE and name == "--e1"), name


ROLES = ("accent", "signal", "danger", "ai", "go", "pause")


@pytest.mark.parametrize("theme", ["night", "day"])
def test_a_hue_moves_every_colour_of_its_role_and_the_shift_moves_all(theme):
    """One variable a role: setting `--hue-accent` (as the Colours popover
    does, on <html>) moves the accent, its text and every token mixed from
    them, and nothing else; `--hue-shift` turns every hued colour by the
    same angle. The depths (lightness, chroma) never move with a hue."""
    base = _resolved(theme)
    for role in ROLES:
        moved = _resolved(theme, **{f"hue_{role}": "123"})
        changed = {k for k in base if base[k] != moved[k]}
        assert {f"--{role}", f"--{role}-text"} <= changed, role
        own = {k for k in changed if k.startswith(f"--{role}")}
        assert changed - own <= {"--focus"}, (role, changed)  # (the accent's)
        for k in changed:
            assert moved[k][:2] == base[k][:2], k
    shifted = _resolved(theme, hue_shift="40")
    for k, (lightness, chroma, hue) in base.items():
        if chroma:
            assert shifted[k] == (lightness, chroma, hue + 40), k


# A colour written as a value: a hex, a colour function that is not relative
# to a token (`oklch(from var(--x) ...)` is how a derived one is written), or
# a CSS named colour. `transparent` and `currentColor` are not literals.
NAMED_COLOURS = (
    "aliceblue antiquewhite aqua aquamarine azure beige bisque black "
    "blanchedalmond blue blueviolet brown burlywood cadetblue chartreuse "
    "chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan "
    "darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta "
    "darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen "
    "darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink "
    "deepskyblue dimgray dimgrey dodgerblue firebrick floralwhite forestgreen "
    "fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey "
    "honeydew hotpink indianred indigo ivory khaki lavender lavenderblush "
    "lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow "
    "lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen "
    "lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime "
    "limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid "
    "mediumpurple mediumseagreen mediumslateblue mediumspringgreen "
    "mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin "
    "navajowhite navy oldlace olive olivedrab orange orangered orchid "
    "palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff "
    "peru pink plum powderblue purple rebeccapurple red rosybrown royalblue "
    "saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue "
    "slateblue slategray slategrey snow springgreen steelblue tan teal thistle "
    "tomato turquoise violet wheat white whitesmoke yellow yellowgreen"
).split()
LITERAL = re.compile(
    r"#[0-9a-f]{3,8}\b"
    r"|\b(?:oklch|oklab|lab|lch|rgba?|hsla?|hwb|color)\((?!\s*from var\()"
    rf"|(?<![-\w.#])(?:{'|'.join(NAMED_COLOURS)})(?![-\w(])",  # (not tan())
    re.I,
)


def _literals(css: str) -> list[str]:
    """The literal colours in the declarations' values (not selectors — a
    `.pkey.black` is a class — nor comments, nor quoted strings)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r'"[^"]*"' + r"|'[^']*'", '""', css)
    found = []
    decl = r"(?<=[{;])\s*(-?-?[\w-]+)\s*:([^;{}]*)(?=[;}])"
    for prop, value in re.findall(decl, css):
        found += [f"{prop}: {m.group(0)}" for m in LITERAL.finditer(value)]
    return found


def test_no_literal_colour_outside_the_tokens():
    """Every colour after section 1 derives from a token: relative colour
    syntax, color-mix, var(--white) / var(--black) (section 1: no hue, so a
    mix toward them keeps its own). So a hue or a theme moves all of it.
    Covered: style.css after section 1, dense.css, every template's inline
    `style`, and the server-built SVG (mapview.py writes a dot's brightness,
    `--b`; the stylesheet picks its hue). Allowed: song_model.PALETTE — a
    section's or a pattern's own colour is the song's data, saved with it
    and drawn as `--section-color` / `--pattern-color`, not the theme's."""
    tokens_end = CSS.index("/* ---------- 2. Base ---------- */")
    assert _literals(CSS[:tokens_end])  # (the test can see one)
    assert not _literals(CSS[tokens_end:])
    assert not _literals(DENSE)
    # an image is not a value a variable can reach into: the data-URL SVGs
    # keep theirs — the select's arrow (a mid grey that reads on both
    # themes) and a mask's black (only its alpha counts)
    inside = re.findall(r"(?:fill|stroke)='%23([0-9a-fA-F]+)'", CSS + DENSE)
    assert sorted(inside) == ["000", "888"], inside
    for name in sorted(TEMPLATES.rglob("*.html")):
        text = name.read_text(encoding="utf-8")
        styles = re.findall(r'\sstyle="([^"]*)"', text)
        styles += re.findall(r"<style[^>]*>(.*?)</style>", text, flags=re.S)
        for style in styles:
            assert not _literals("{" + style + ";}"), (name, style)
    studio = Path("synthlab/studio")
    pattern = re.compile(r"""["'](?:#[0-9a-fA-F]{6}|[^"']*\b(?:oklch|rgba?|hsla?)\()""")
    for name in sorted(studio.rglob("*.py")):
        hits = pattern.findall(name.read_text(encoding="utf-8"))
        if name.name == "song_model.py":  # PALETTE: the song's data
            assert len(hits) == 8, hits
            continue
        assert not hits, (name, hits)


# ---- tokens and the dense tier ---------------------------------------------
def test_the_scales_are_tokens():
    root = _tokens(_root())
    for name in (
        "--s0 --s1 --s15 --s2 --s3 --s4 --s5 --s6 --s7 --s8 "
        "--fs-2xs --fs-xs --fs-s --fs-m --fs-l --fs-xl "
        "--r-xs --r-s --r-m --r-l --r-pill --r-round "
        "--h-s --h-m --h-l --t-fast --t-med --t-slow --ease "
        "--e1 --e2 --e3 --scrim --focus --bg-over --line-ctl --line-strong"
    ).split():
        assert name in root, name


def test_the_dense_tier_is_its_own_stylesheet_and_derives_from_the_tokens():
    """The dense tier used to be `@media (pointer: fine)` at the end of
    style.css. It is its own file so the page — which knows a pen has been
    used — decides who gets it; every rule is still scoped to
    `html:not([data-density="comfy"])`, and it restates token VALUES, not
    components."""
    for sheet in (CSS, DENSE):  # (the comments say where it went)
        assert "pointer: fine" not in re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    head = DENSE[DENSE.index('html:not([data-density="comfy"]) {') :]
    dense = _tokens(head[: head.index("\n}")])
    for name in ("--fs-xs", "--fs-s", "--fs-m", "--r-s", "--r-pill", "--h-s", "--h-m"):
        assert name in dense, name
    # small type, never illegible: 10 px is the floor in both files
    token_sizes = [float(v[:-2]) for k, v in dense.items() if k.startswith("--fs-")]
    assert min(token_sizes) >= 10 and max(token_sizes) <= 15
    # (literal sizes: 1.8px is an SVG's own unit, 7px the A/B slots' dot glyph)
    literal = {float(x) for x in re.findall(r"font-size: ([\d.]+)px", CSS + DENSE)}
    assert {s for s in literal if s < 10} == {1.8, 7.0}
    # ... written on each rule, or (phase B3's section, a hundred rules)
    # nested under one rule that carries it
    scope = 'html:not([data-density="comfy"])'
    body = re.sub(r"/\*.*?\*/", "", DENSE, flags=re.S)
    open_rules: list[bool] = []  # per open block: is it (inside) the scope?
    for sel, brace in re.findall(r"([^{};]*)([{}])", body):
        if brace == "}":
            open_rules.pop()
            continue
        sel = sel.strip()
        inside = bool(open_rules) and open_rules[-1]
        if not sel.startswith("@"):
            parts = re.split(r",(?![^()]*\))", sel)  # not the commas inside :is()
            scoped = all(part.strip().startswith(scope) for part in parts)
            assert scoped or inside, sel
            inside = inside or scoped
        open_rules.append(inside)
    assert not open_rules


def test_the_touch_tier_stands_down_for_a_pen():
    """Every `(pointer: coarse)` block is nested under
    `html:not([data-pen])`: a pen is precise, and on a Surface the primary
    pointer reports coarse while the hand holds one. The Song drawer's pick
    list keeps its finger size (a pinned one-liner; a row is a row)."""
    blocks = re.findall(r"@media [^{]*\(pointer: coarse\) \{\s*([^\n]*)", CSS)
    assert (
        len(blocks) >= 10
    )  # the count holds with .pt and .pfx carrying no touch rules of their own
    plain = [b for b in blocks if not b.startswith("html:not([data-pen]) {")]
    assert plain == [".pick { min-height: 44px; } }"], plain


# ---- components -------------------------------------------------------------
INTERACTIVE = {
    ".btn": (
        ":hover",
        ":active",
        ":focus-visible",
        ":disabled",
        '[aria-pressed="true"]',
    ),
    ".btn-primary": (":hover", ":active", ":disabled"),
    ".btn-ghost": (":hover", ":disabled"),
    ".btn-danger": (":hover", ":disabled"),
    ".btn-ai": (":hover", ":disabled", '[aria-pressed="true"]'),
    ".btn-play": (":hover", ":disabled", '[aria-pressed="true"]'),
    ".btn-pause": (":hover", ":disabled"),
    ".btn-stop": (":hover", ":disabled"),
    ".field": (":hover", ":focus-visible", ":disabled", "aria-invalid"),
    ".check": (":hover", ":checked", ":focus-visible", ":disabled"),
    ".menu-item": (":hover", ":focus-visible", ":disabled", "aria-selected"),
    ".row": (":hover", ":focus-visible", "aria-selected"),
    'input[type="range"].knob': (":hover", ":focus-visible", ":disabled"),
}
PLAIN = (
    ".btn-s .btn-l .btn-icon .btn-play .field-s .seg .panel-head .caps .pop .menu "
    ".drawer .tip "
    ".badge .badge-accent .badge-signal .badge-danger .empty-state .skel .kbd "
    ".meter .meter-v .led .jack .cable"
).split()


def _rules_for(selector: str) -> str:
    """The preludes of every rule that names `selector` exactly."""
    esc = re.escape(selector)
    return " ".join(re.findall(rf"(?m)^[^{{}}\n]*{esc}(?![\w-])[^{{}}]*\{{", CSS))


def test_every_canonical_component_exists_with_its_states():
    section = CSS[
        CSS.index("/* ---------- 15. Components") : CSS.index("/* ---------- 16.")
    ]
    for sel in PLAIN:
        assert re.search(rf"{re.escape(sel)}(?![\w-])[^{{}}]*\{{", section), sel
    for sel, states in INTERACTIVE.items():
        preludes = _rules_for(sel)
        for state in states:
            assert state in preludes, f"{sel} has no {state} rule"
    # the segmented control's parts are its children
    seg = _rules_for(".seg")
    for state in (":hover", "aria-pressed", ":focus-visible", ":disabled"):
        assert state in seg, state
    # written against the tokens: no raw colour, no px radius in the section
    body = re.sub(r"/\*.*?\*/", "", section, flags=re.S)
    assert not re.findall(r"oklch\(|#[0-9a-f]{6}\b|rgba?\(", body)
    assert not re.findall(
        r"border-radius: \d+px", body.replace("border-radius: 1px", "")
    )
    # and motion is off when asked: the blanket rule still stands
    assert "* { animation: none !important; transition: none !important; }" in CSS


def test_one_focus_ring_survives_on_an_accent_fill():
    """The ring is the accent; on an accent-filled button it used to vanish
    into the fill. `.btn` rings carry a page-coloured halo."""
    btn = CSS.split(".btn:focus-visible {")[1].split("}")[0]
    assert "outline: 2px solid var(--focus)" in btn
    assert "box-shadow: 0 0 0 2px var(--bg)" in btn
    assert ":focus-visible { outline: 2px solid var(--accent)" in CSS  # the global one


# ---- the shell --------------------------------------------------------------
@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _tag(html: str, ident: str) -> str:
    m = re.search(rf'<[a-z]+\b[^>]*\bid="{re.escape(ident)}"[^>]*>', html)
    assert m, f"#{ident} is not on the page"
    return m.group(0)


def test_the_rail_is_built_from_the_canonical_button(client):
    html = client.get("/").text
    for ident in (
        "midi-btn",
        "click-btn",
        "keys-help",
        "density-toggle",
        "palette-btn",
        "fullscreen-btn",  # the whole studio in full screen
    ):
        tag = _tag(html, ident)
        assert re.search(r'class="btn\b', tag), tag
        assert "title=" in tag and "aria-label=" in tag, tag  # a tooltip and a name
    # the icon-only ones draw an icon, not a glyph nobody can read at 10 px
    tools = html[html.index('class="rail-tools"') :]
    tools = tools[: tools.index("</header>")]
    assert tools.count('<svg class="vi"') >= 5
    for glyph in ("&#9000;", "▦", "◐"):
        assert glyph not in tools
    assert re.search(r'class="btn [^"]*theme-toggle"', tools)
    assert re.search(
        r'<button type="button" class="btn btn-s" popovertarget="reset-panel"', html
    )  # the reset menu
    assert 'class="btn btn-s reset-btn"' in html
    assert 'class="drawer clip-drawer"' in html


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_every_page_has_the_status_strip_once(client, view):
    """The sound, the tempo, the key, the output meter and the live voice's
    CPU are on every page, in the top dock — and exactly once: the meter and
    #cpu-meter used to be drawn by Synths, Perform and Drums themselves."""
    html = client.get(f"/view/{view}").text
    for ident in ("status-bar", "meter", "cpu-meter", "st-task", "palette", "tip"):
        assert html.count(f'id="{ident}"') == 1, ident
    strip = html[html.index('class="statusbar"') : html.index('id="toasts"')]
    assert "<b>Init</b>" in strip or 'class="st st-sound"' in strip
    assert "bpm" in strip
    # the facts are the region; the client's parts sit beside it, not in it
    facts = strip[strip.index('id="status-bar"') : strip.index("</div>")]
    assert 'id="meter"' not in facts and 'id="cpu-meter"' not in facts
    # and the page head no longer prints its instructions
    head = html[html.index('<header class="view-head">') :]
    head = head[: head.index("</header>")]
    how = head[head.index('id="head-how"') :]
    assert 'popover><p class="view-how">' in how
    assert 'popovertarget="head-how"' in head


def test_the_status_strip_follows_the_session(client):
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/library"}
    r = client.post("/fragment/arrangement", data={"bpm": "97"}, headers=hx)
    assert 'id="status-bar" hx-swap-oob="outerMorph"' in r.text
    assert "<b>97</b> bpm" in r.text
    r = client.post("/fragment/patch", data={"cutoff": "900"}, headers=hx)
    assert 'id="status-bar"' in r.text and 'class="st-dirty">edited<' in r.text
    # a post that moves none of it sends none of it
    r = client.post("/fragment/patch", data={"cutoff": "950"}, headers=hx)
    assert 'id="status-bar"' not in r.text


def test_a_pen_gets_the_dense_tier_whatever_the_pointer_says(client):
    html = client.get("/").text
    link = _tag(html, "dense-css")
    assert 'media="(pointer: fine)"' in link and "<html" in html
    assert "data-pen" not in html.split(">")[1]
    client.cookies.set("pen", "1")
    html = client.get("/").text
    assert 'media="all"' in _tag(html, "dense-css")
    assert re.search(r"<html [^>]*\bdata-pen[ >]", html)


# ---- commands and shortcuts are declared on their controls ------------------
def _templates() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in TEMPLATES.rglob("*.html"))


def test_commands_are_declared_on_controls_and_nowhere_else():
    """The palette has no table of commands: it harvests `[data-cmd]`. So a
    command must sit on something that can be activated, and neither island
    may grow a list of its own."""
    js = (STATIC / "palette.js").read_text(encoding="utf-8")
    assert 'querySelectorAll("[data-cmd]")' in js
    assert "hx-post" not in js and "/fragment/" not in js.split("(() => {")[1]
    tags = re.findall(
        r"<([a-z]+)\b[^>]*\bdata-cmd=\"[^\"]+\"[^>]*>", _templates(), re.S
    )
    assert len(tags) >= 40
    assert set(tags) <= {"button", "a", "select", "summary"}, set(tags)
    # every view is reachable by name
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'data-cmd="Go to {{ label }}" data-cmd-group="Go to"' in base


def test_a_declared_shortcut_names_its_command():
    """`data-keys` is how a shortcut reaches the sheet and the palette: it
    needs a name beside it (the command's, or data-key-label)."""
    for tag in re.findall(
        r"<[a-z]+\b[^>]*\bdata-keys=\"[^\"]+\"[^>]*>", _templates(), re.S
    ):
        assert "data-cmd=" in tag or "data-key-label=" in tag, tag


def test_the_palette_finds_sounds_and_clips_as_the_apps_own_controls(client, tmp_path):
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    client.post("/fragment/preset/save", data={"name": "glass pad"}, headers=hx)
    save_clip(tmp_path, "glass hit", np.zeros(4000, dtype=np.float32))
    html = client.get("/fragment/palette", params={"q": "GLASS"}).text
    assert html.startswith('<div id="pal-found"') or 'id="pal-found"' in html[:200]
    rows = re.findall(r"<button\b[^>]*>", html, re.S)
    by = {re.search(r'data-cmd="([^"]*)"', r).group(1): r for r in rows}
    assert 'hx-post="/fragment/preset/load"' in by["glass pad"]
    assert "glass pad" in re.search(r"hx-vals='([^']*)'", by["glass pad"]).group(1)
    assert 'popovertarget="clip-drawer"' in by["glass hit"]
    assert 'data-cmd-fill="#clip-q"' in by["glass hit"]
    library = [r for r in rows if 'hx-post="/fragment/library/load"' in r]
    assert library and all('hx-target="body"' in r for r in library)
    # nothing typed: nothing found (the page's own commands are the list)
    assert "<button" not in client.get("/fragment/palette").text
    assert "<button" not in client.get("/fragment/palette", params={"q": "zzqx"}).text
    # a long query is cut, not refused
    assert client.get("/fragment/palette", params={"q": "g" * 500}).status_code == 200


def test_a_hostile_sound_name_cannot_leave_its_attribute(client):
    hx = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
    name = "x'\"><img src=x onerror=1>"
    client.post("/fragment/preset/save", data={"name": name}, headers=hx)
    html = client.get("/fragment/palette", params={"q": "x"}).text
    assert "<img" not in html and "onerror=1>" not in html


def test_the_sheet_has_a_generated_half_and_a_search(client):
    html = client.get("/").text
    sheet = html.split('id="shortcuts"')[1].split("</dialog>")[0]
    assert 'id="sc-q"' in sheet and 'id="sc-commands" hidden' in sheet
    assert "Pen" in sheet and "barrel button" in sheet and "eraser" in sheet
    js = (STATIC / "palette.js").read_text(encoding="utf-8")
    assert 'querySelectorAll("[data-keys]")' in js


def test_tooltips_lend_the_title_and_hand_it_back():
    """shell.js never rewrites the markup for good: the `title` is moved to
    data-tip only while its tooltip shows (so the native one does not come
    up over ours) and restored on hide — at rest the DOM is the server's."""
    js = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert (
        'removeAttribute("title")' in js
        and 'setAttribute("title", host.dataset.tip)' in js
    )
    assert 'e.pointerType === "touch"' in js  # a finger gets no hover tooltip
    assert "showPopover" in js  # the top layer: no overflow clips it
    assert 'popover="manual"' in (TEMPLATES / "base.html").read_text(encoding="utf-8")


def test_tweak_drives_sliders_through_their_own_events():
    js = (STATIC / "tweak.js").read_text(encoding="utf-8")
    for must in (
        'new Event("input", { bubbles: true })',
        'new Event("change", { bubbles: true })',
        "setPointerCapture",
        'e.pointerType === "pen"',
        "(e.buttons & 2) !== 0",  # the barrel button: fine mode
        "(e.buttons & 32) !== 0",  # the eraser: reset
        'new MouseEvent("dblclick"',  # ... by the reset the page already has
        "el !== document.activeElement",  # the wheel never hijacks a scroll
    ):
        assert must in js, must
    assert 'input[type="range"] { touch-action: none; }' in CSS


# ---- one of everything ---------------------
def _rule_heads(css: str) -> list[str]:
    """Every style rule's selector list, whitespace normalised, wherever it
    is nested."""
    bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    heads = re.findall(r"(?:^|[{};])\s*([^{};@]+?)\s*\{", bare)
    return [" ".join(h.split()) for h in heads]


def test_a_promoted_component_is_defined_once_and_in_the_shared_section():
    """Three packages each built parts in their own sections (a file picker,
    an audio player, a typed value, a context menu, a rack, a ledger of
    facts, a reading meter); the integration pass moved each to section 15.
    A part has ONE base rule, and it is not in a view's section."""
    shared = CSS[
        CSS.index("/* ---------- 15. Components") : CSS.index("/* ---------- 16. Shell")
    ]
    views = CSS.replace(shared, "") + DENSE
    heads_shared, heads_views = _rule_heads(shared), _rule_heads(views)
    for sel in (
        ".filepick",
        ".player",
        ".player-bar",
        ".val-edit",
        ".ctx-menu",
        ".ctx-title",
        ".panel-name",
        ".rack[open] > summary::before",
        ".rack-sec",
        ".t-facts",
        ".meter",
        ".meter-v",
        ".meter:not(button)",
        "select.field",
        ".knob-s",
        ".menu",
        ".menu-item",
    ):
        assert heads_shared.count(sel) == 1, (sel, heads_shared.count(sel))
        assert sel not in heads_views, f"{sel} is defined again outside section 15"
    # the mixer's toggles: one pressed tint each, for every view's classes
    assert shared.count('.btn:is(.mute, .l-m, .arm)[aria-pressed="true"] {') == 1
    assert shared.count('.btn:is(.solo, .l-s, .lab-solo)[aria-pressed="true"] {') == 1
    for old in ('.l-m[aria-pressed="true"]', '.rowbtn.mute[aria-pressed="true"]'):
        assert old not in CSS
    # a reading meter's cursor is said once, not per view
    said = re.findall(
        r"([^{};]*meter[^{};]*)\{[^{}]*cursor: default",
        re.sub(r"/\*.*?\*/", "", CSS + DENSE, flags=re.S),
    )
    assert [" ".join(s.split()) for s in said] == [".meter:not(button)"], said
    assert "#ctl-menu" not in CSS + DENSE
    # the top dock's height and the rack widths are the shell's tokens
    assert CSS.count("--top-h: 95px") == 1 and DENSE.count("--top-h: 59px") == 1
    for local in ("--b3-top", "--dock-h"):
        assert local not in CSS + DENSE, local
    assert CSS.count("--rack-w: 240px") == 1 and "var(--rack-w)" in DENSE
    # THE dense dial, and THE finger-sized dial: one rule each
    assert DENSE.count("--knob: 24px") == 1
    coarse = CSS[
        CSS.index("/* ---------- 9f. Touch") : CSS.index("/* ---------- 10. Motion")
    ]
    assert 'input[type="range"].knob { --knob: 40px; height: var(--knob); }' in coarse
    assert CSS.count("--knob: 40px") == 2  # the touch tier's, and .knob-l


def test_a_typed_value_and_the_reset_change_belong_to_the_value_layer():
    """Typing into a read-out was built in the bench's island (Synths, Bass,
    Voice only); it is tweak.js's second file now, so every view's knobs and
    sliders take a typed value, and bench.js has none of it."""
    typed = (STATIC / "tweaktype.js").read_text(encoding="utf-8")
    bench = (STATIC / "bench.js").read_text(encoding="utf-8")
    tweak = (STATIC / "tweak.js").read_text(encoding="utf-8")
    assert "S.tweak(el, v, true)" in typed and '"val-edit"' in typed
    assert "#patch" not in typed and "data-view" not in typed  # no view named
    code = bench.split("(() => {", 1)[1]
    assert "val-edit" not in code and "dblclick" not in code
    assert 'addEventListener("dblclick"' in tweak
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.index("/static/tweak.js") < base.index("/static/tweaktype.js")
    # the Lab's audition and its import box are the shared parts, not a
    # native <audio controls> and a local drop box
    lab = (TEMPLATES / "fragments" / "lab_panel.html").read_text(encoding="utf-8")
    assert "<audio controls" not in lab and 'class="player lab-player"' in lab
    assert 'class="filepick lab-drop"' in (TEMPLATES / "views" / "lab.html").read_text(
        encoding="utf-8"
    )
    for name in TEMPLATES.rglob("*.html"):
        assert " controls" not in re.sub(
            r"aria-controls",
            "",
            "".join(re.findall(r"<audio[^>]*>", name.read_text(encoding="utf-8"))),
        ), name


def test_nothing_makes_the_dock_a_containing_block_for_the_rail():
    """The dense rail (.topbar) is position: fixed INSIDE .topdock. A
    filter, backdrop-filter, transform, perspective, contain or
    will-change on the dock makes the dock the containing block of every
    fixed descendant: the rail shrank to the dock's height and vanished
    (Afterhours' glass did exactly that). The glass is on .topdock::before."""
    props = "backdrop-filter|filter|transform|perspective|contain|will-change"
    trap = re.compile(rf"(?:^|[\s;{{])(?:{props})\s*:")
    dock = re.compile(r"\.topdock(?![\w:-])")
    for sheet in (CSS, DENSE):
        for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", sheet):
            sels = [s.strip() for s in m.group(1).split(",")]
            if any(dock.search(s) and "::" not in s for s in sels):
                assert not trap.search(m.group(2)), (m.group(1).strip(), m.group(2))


@pytest.mark.parametrize("theme", ["night", "day"])
def test_the_hand_is_never_the_colour_of_delete(theme):
    """Amber is the hand (a lit step, Save sound…, the primary), red is
    delete / record / stop. By day the accent was a hot orange 10 degrees
    off --danger, and every primary read as a delete; both themes keep
    them at least 25 degrees of hue apart."""
    t = _resolved(theme)
    for a, d in (("--accent", "--danger"), ("--accent-text", "--danger-text")):
        assert abs(t[a][2] - t[d][2]) >= 25, (theme, a, t[a], d, t[d])


# ---- the palette's tiers -------------------------
# frontend.md, "The palette": every role's fill at one depth (L) a theme —
# two by day, the light fills under a deep ink and the deep ones under white
# — and every `-text` at one; chroma held under a ceiling or the sRGB
# gamut's edge, whichever is lower, and near it, so no role shouts.
TIERS = {
    "night": [(0.73, 0.18, ROLES)],
    "day": [
        (0.72, 0.15, ("accent", "go", "pause")),
        (0.54, 0.17, ("signal", "danger", "ai")),
    ],
}
TEXT_TIERS = {"night": (0.78, 0.14), "day": (0.44, 0.13)}  # (day 0.48 on porcelain)


def _in_gamut(lightness: float, chroma: float, hue: float) -> bool:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )
    return all(-1e-3 <= x <= 1 + 1e-3 for x in rgb)


def _gamut_chroma(lightness: float, hue: float) -> float:
    lo, hi = 0.0, 0.4
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _in_gamut(lightness, mid, hue) else (lo, mid)
    return lo


def _on_tier(lch, tier: float, ceiling: float) -> str | None:
    lightness, chroma, hue = lch
    if abs(lightness - tier) > 0.02 + 1e-9:
        return f"L {lightness:.3f} is off its tier {tier}"
    top = min(ceiling, _gamut_chroma(lightness, hue))
    if not top - 0.025 <= chroma <= top + 0.005:
        return f"C {chroma:.3f} is not at its ceiling {top:.3f}"
    return None


@pytest.mark.parametrize("theme", ["night", "day"])
def test_every_role_sits_on_its_tier(theme):
    """Perceptual alignment: no role's fill is lighter, darker or more
    saturated than its tier allows (all within ±0.02 L of it), every role's
    `-text` at one depth too, and the text ON every fill holds AA (PAIRS)."""
    t = _resolved(theme)
    bad = []
    for tier, ceiling, roles in TIERS[theme]:
        for role in roles:
            if why := _on_tier(t[f"--{role}"], tier, ceiling):
                bad.append(f"--{role}: {why}")
    tier, ceiling = TEXT_TIERS[theme]
    for role in ROLES:
        if why := _on_tier(t[f"--{role}-text"], tier, ceiling):
            bad.append(f"--{role}-text: {why}")
    assert not bad, f"{theme}: " + "; ".join(bad)
    assert {r for *_, roles in TIERS[theme] for r in roles} == set(ROLES)


def test_the_hues_are_one_scheme():
    """A split complement round the amber: its complement is the night's
    slate, its two splits the signal's teal and the AI's violet (each within
    15 degrees of 180 ± 55 off the accent); the warm side — red, orange,
    amber — at least 20 degrees apart, so a stop, a pause and the hand never
    read as each other."""
    h = {r: _resolved("night")[f"--{r}"][2] for r in ROLES}
    for role, side in (("signal", -55), ("ai", 55)):
        want = (h["accent"] + 180 + side) % 360
        assert abs((h[role] - want + 180) % 360 - 180) <= 15, (role, h[role], want)
    assert h["pause"] - h["danger"] >= 20 and h["accent"] - h["pause"] >= 20, h
    assert h["danger"] < h["pause"] < h["accent"] < h["go"] < h["signal"] < h["ai"], h


# ---- the ladder's two dials: --contrast and --black-level ----------------------
def _fails(theme: str, **over: str) -> list[str]:
    t = {k: _rgb(*lch) for k, lch in _resolved(theme, **over).items()}
    return [
        f"{fg} on {bg}"
        for fg, backgrounds, floor in PAIRS
        for bg in backgrounds
        if _ratio(t[fg], t[bg]) < floor
    ]


@pytest.mark.parametrize("theme", ["night", "day"])
def test_the_ladder_dials_leave_every_depth_as_written_at_their_defaults(theme):
    """Every `--l-*` depth goes through `clamp(0, --ladder-0 + --ladder-k *
    <night | day>, 1)` (the Colours popover's contrast and black level); at
    the defaults that is the number written, exactly — a
    ΔE check (every token and element, four theme modes) measured 0.000."""
    env = _tokens(_root()) | SWITCHES[theme]
    col = 1 if theme == "night" else 2
    written = 0
    for name, value in env.items():
        if not name.startswith("--l-"):
            continue
        pair = re.search(r"var\(--night, ([\d.]+)\) var\(--day, ([\d.]+)\)", value)
        if pair:
            written += 1
            assert _arith(_resolve(value, env).strip()) == float(pair.group(col)), name
    assert written >= 25


def test_contrast_up_and_any_black_level_keep_wcag_aa():
    """From 100 % up, at any black level, every pair above holds AA in both
    themes: the fills and the text ON them keep their written depths, so
    contrast never pushes a fill into its own text (by day the signal's
    did, at 110 %, when the fills moved with the ladder)."""
    for theme in ("night", "day"):
        for contrast in ("1", "1.2", "1.4"):
            for black in ("0", "0.5", "1"):
                got = _fails(theme, contrast=contrast, black_level=black)
                assert not got, (theme, contrast, black, got)


def test_contrast_down_trades_aa_away_and_says_so():
    """Under 100 % the ladder closes up: the dim text and a field's edge are
    the first under AA (frontend.md, "Colour: hues and depths" says so, and
    the slider's title). At the popover's floor (80 %) both themes fail."""
    for theme in ("night", "day"):
        got = _fails(theme, contrast="0.8")
        assert any(f.startswith("--text-dim") for f in got), (theme, got)
        assert any(f.startswith("--line-strong") for f in got), (theme, got)
    assert "80, 140, 100" in (TEMPLATES / "fragments" / "colours.html").read_text(
        encoding="utf-8"
    )


def test_the_black_level_takes_the_night_to_black_and_keeps_its_steps():
    """At 1 the night's page is L 0.04 and its inset true black; each
    surface above keeps a step at least as big as it had, the text goes to
    white, and the fills and the text on them stay put. By day: nothing."""
    base, oled = _resolved("night"), _resolved("night", black_level="1")
    ladder = ("--bg-inset", "--bg", "--bg-raised", "--bg-over", "--line")
    assert oled["--bg-inset"][0] == 0 and abs(oled["--bg"][0] - 0.04) < 1e-9
    for lo, hi in zip(ladder, ladder[1:], strict=False):
        assert oled[hi][0] - oled[lo][0] >= base[hi][0] - base[lo][0] - 0.005, (lo, hi)
    assert oled["--text"][0] >= base["--text"][0]
    for role in ROLES:
        assert oled[f"--{role}"] == base[f"--{role}"], role
        assert oled[f"--on-{role}"] == base[f"--on-{role}"], role
    assert _resolved("day", black_level="1") == _resolved("day")


# ---- the themes (frontend.md, "Themes") ---------------------------
# A theme is a named set of section 1's dials — `html[data-look="<key>"]`,
# `.look-sw[data-look="<key>"]` (its swatch) — never a colour.
THEMES = dict(
    re.findall(
        r'\nhtml\[data-look="([\w-]+)"\], \.look-sw\[data-look="\1"\] \{([^}]*)\}', CSS
    )
)
DIALS = re.compile(r"--hue-[a-z-]+|--tint|--tint-ink|--chroma|--contrast|--black-level")
NUMBER = re.compile(r"-?[\d.]+|var\(--night, -?[\d.]+\) var\(--day, -?[\d.]+\)")


def _look(key: str) -> dict[str, str]:
    """A theme's dials, as the Colours popover's `over` (studio: none)."""
    body = THEMES.get(key, "")
    return {k[2:].replace("-", "_"): v for k, v in _tokens(body).items()}


def test_a_theme_is_a_named_set_of_the_dials_and_its_swatch_is_in_the_popover():
    """Six themes beside Studio Teal (the defaults, no rule of its own), each
    only dials — a hue, a chroma scale, contrast, black level — a number or a
    night | day pair of numbers; each has its swatch, which is its command."""
    assert len(THEMES) >= 6 and "warm-tube" in THEMES
    for key, body in THEMES.items():
        for name, value in _tokens(body).items():
            assert DIALS.fullmatch(name), (key, name)
            assert NUMBER.fullmatch(value), (key, name, value)
    pop = (TEMPLATES / "fragments" / "colours.html").read_text(encoding="utf-8")
    row = pop[pop.index('class="look-row"') : pop.index("{% endfor %}")]
    swatches = re.findall(r'\("([\w-]+)", "[^"]+", "[^"]+"\)', row)
    assert swatches == ["studio", *THEMES], swatches
    assert 'class="look-sw" data-look="{{ key }}"' in pop
    assert 'data-cmd="Theme: {{ name }}"' in pop
    # the swatch recomputes every colour from its own dials: the dials and
    # the colours are one rule for the page and the swatch
    assert "--hue-accent:" in _block(":root, .look-sw {")
    assert "--accent: oklch(" in _block(":root, .look-sw {")


@pytest.mark.parametrize("look", ["studio", *THEMES])
def test_every_theme_holds_wcag_aa_night_and_day(look):
    """Every text / surface pair, as `test_token_pairs_hold_wcag_aa`, in
    each theme's night and day — and with the contrast pushed up and any
    black level (the popover's dials over a theme), as the default does."""
    for theme in ("night", "day"):
        got = _fails(theme, **_look(look))
        assert not got, (look, theme, got)
        for contrast in ("1.2", "1.4"):
            for black in ("0", "1"):
                over = _look(look) | {"contrast": contrast, "black_level": black}
                assert not _fails(theme, **over), (look, theme, contrast, black)


@pytest.mark.parametrize("look", ["studio", *THEMES])
def test_every_theme_keeps_one_meaning_per_hue(look):
    """The six roles at least 20 degrees of hue apart in every theme (a stop,
    a pause, the hand, what sounds, play and the AI never read as each
    other), the hand at least 25 from delete, and the danger a red."""
    for theme in ("night", "day"):
        t = _resolved(theme, **_look(look))
        hues = {r: t[f"--{r}"][2] % 360 for r in ROLES}
        for i, a in enumerate(ROLES):
            for b in ROLES[i + 1 :]:
                gap = abs((hues[a] - hues[b] + 180) % 360 - 180)
                assert gap >= 20 - 1e-9, (look, theme, a, b, gap)
        gap = abs((hues["accent"] - hues["danger"] + 180) % 360 - 180)
        assert gap >= 25, (look, theme, gap)
        assert 10 <= hues["danger"] <= 40, (look, theme, hues["danger"])


def test_the_theme_is_on_html_before_the_first_paint():
    """colours.js (blocking, in <head>) reads `theme` from synthlab.colours
    and sets `data-look` before anything waits on the document; a swatch's
    click and Reset keep it; the decks and the Visuals read their colours
    again when it changes."""
    js = (STATIC / "colours.js").read_text(encoding="utf-8")
    head = js[: js.index("DOMContentLoaded")]
    assert "stored.theme" in head and "root.dataset.look = t" in head
    assert ".look-sw[data-look]" in js and "{ theme: root.dataset.look }" in js
    for island in ("deckart.js", "visuals.js"):
        text = (STATIC / island).read_text(encoding="utf-8")
        # (not "style": every --keys-h written on <html> re-read them; a
        # hue's move says so itself, synthlab-colours)
        assert '"data-theme", "data-look"]' in text, island
        assert "synthlab-colours" in text, island


def test_motion_is_full_whatever_the_os_says_and_calm_on_request():
    """Motion: full | calm, deliberately. The OS's prefers-reduced-motion
    is not followed
    anywhere — no media block in the stylesheets, no matchMedia in an
    island —: calm is `html[data-motion="calm"]`, set by colours.js in
    <head> from `synthlab.motion` before the first paint, the stylesheet's
    calm rules hang off it, and every island that moves asks
    `synthlab.calm()`. The toggle is the Colours popover's last row, a
    command each way."""
    assert not re.search(r"@media[^{]*prefers-reduced-motion", CSS + DENSE)
    for path in STATIC.glob("*.js"):
        if path.name.endswith(".min.js"):
            continue
        text = path.read_text(encoding="utf-8")
        assert 'matchMedia("(prefers-reduced-motion' not in text, path.name
    js = (STATIC / "colours.js").read_text(encoding="utf-8")
    head = js[: js.index("const showMotion")]  # (nothing waits on the document)
    assert "DOMContentLoaded" not in head[head.index("const MOTION") :]
    assert 'localStorage.getItem(MOTION) === "calm"' in head
    assert 'root.dataset.motion = "calm"' in head
    assert "(window.synthlab ??= {}).calm = calm" in head
    assert 'new CustomEvent("synthlab-motion"' in js
    # the calm rules: section 10's kill switch and the light's two dials
    calm = CSS[CSS.index("/* ---------- 10. Motion") : CSS.index("/* ---------- 11.")]
    assert 'html[data-motion="calm"] {' in calm
    assert "* { animation: none !important; transition: none !important; }" in calm
    assert CSS.count('html[data-motion="calm"]') >= 18
    for island in (
        "deckart.js",
        "deckui.js",
        "grid.js",
        "layout.js",
        "motion.js",
        "songcell.js",
        "transport.js",
        "vispage.js",
        "visuals.js",
        "visuals2.js",
    ):
        text = (STATIC / island).read_text(encoding="utf-8")
        assert re.search(r"(synthlab|S)\.calm\(\)", text), island
    # going calm mid-play takes the light down (motion.js hears the toggle)
    assert '"synthlab-motion"' in (STATIC / "motion.js").read_text(encoding="utf-8")
    pop = (TEMPLATES / "fragments" / "colours.html").read_text(encoding="utf-8")
    for mode in ("full", "calm"):
        assert f'data-set-motion="{mode}"' in pop
        assert f'data-cmd="Motion: {mode}"' in pop


# ---- the light model and the energy -----------
@pytest.mark.parametrize("look", ["studio", *THEMES])
def test_the_glow_is_each_themes_own_light_and_never_text(look):
    """`--glow` (frontend.md, "The light model") is a light, not a role: its
    own hue dial, set by every theme (Warm Tube's an amber filament,
    Phosphor's green), at one bright depth by night and a softer one by day,
    turned with the shift — and never the colour of text (it has no `-text`
    tone and no AA pair: a glow must not wash text out)."""
    assert look == "studio" or "--hue-glow" in THEMES[look], look
    for theme in ("night", "day"):
        lightness, chroma, _ = _resolved(theme, **_look(look))["--glow"]
        assert 0.55 <= lightness <= 0.8 and chroma > 0.05, (look, theme)
    assert _resolved("night", hue_glow="120")["--glow"][2] == 120
    assert not re.search(r"(?<![-\w])color:\s*var\(--glow\)", CSS + DENSE)
    # (a stale `@property --glow` registered a percentage and every glow drew nothing)
    assert "@property --glow " not in CSS


def test_the_light_model_is_tokens():
    """One light on every surface, written once in section 1: a module rests
    on the chassis (--e-ambient in --e-mod), a well's far lip catches the
    light, a control lifts (--e-lift), a cap and a knob carry a specular
    spot, glass a reflection, the chassis is brushed, a played edge halos."""
    root = _tokens(_root())
    for name in (
        "--e-ambient",
        "--e-lift",
        "--spec",
        "--knob-spec",
        "--glass",
        "--brushed",
        "--halo-edge",
        "--filament",
        "--a-halo",
    ):
        assert name in root, name
    assert "var(--e-ambient)" in root["--e-mod"]
    assert CSS.count("var(--knob-spec),") == 2  # the knob and the two-sided knob
    # the hand: a key lifts under it and flashes when pressed, never a latched one
    assert re.search(r"\.btn:hover \{[^}]*translate: 0 -1px", CSS)
    assert re.search(r"\.btn:active \{[^}]*transition-duration: 0s", CSS)
    assert re.search(
        r'\.btn\[aria-pressed="true"\], \.btn\[data-state="on"\] \{ translate: none;',
        CSS,
    )


def test_no_selector_reads_an_ancestors_inline_style():
    """`.roll[style*="--mx"] .cell.sel` made every inline-style write on any
    element (the playheads' --phase each frame, motion.js's --live) restyle
    every step cell under it: 2.5 ms a frame on Perform while the loop
    played. A `[style…]` test may stand only on a selector's subject."""
    bare = re.sub(r"/\*.*?\*/", "", CSS + DENSE, flags=re.S)
    for head in _rule_heads(bare):
        for sel in head.split(","):
            m = re.search(r"\[style[^\]]*\]", sel)
            if m:
                rest = sel[m.end() :]
                while re.search(
                    r"\([^()]*\)", rest
                ):  # (a :not() / :has() is the subject's own)
                    rest = re.sub(r"\([^()]*\)", "", rest)
                assert not re.match(r"[^\s>+~]*[\s>+~]+[\w.#\[*:]", rest), sel


def test_the_energy_is_published_once_and_drawn_by_its_consumers():
    """motion.js writes the level and the kick on its consumers AND, the
    same numbers, `--energy` / `--kick` on <body> (non-inherited: <body>
    alone restyles; never <html>, whose style the decks and the visualisers
    watch for the hues), `data-alive` while it sounds, nothing under calm
    motion; grid.js marks a running grid once (no per-frame attribute)."""
    js = (STATIC / "motion.js").read_text(encoding="utf-8")
    assert '"--energy"' in js and '"data-alive"' in js and "synthlab.energy" in js
    assert "calm()) return" in js
    assert 'el === document.body ? "--energy"' in js
    assert '@property --energy { syntax: "<number>"; inherits: false;' in CSS
    grid = (STATIC / "grid.js").read_text(encoding="utf-8")
    assert "if (!x.running) x.running" in grid and '"data-running", false' in grid
    # the played module's edge is a layer whose opacity alone moves
    alive = CSS[CSS.index("/* ---------- 39. Alive") :]
    lit = alive[alive.index(":has(.grid[data-running])::after {") :]
    lit = lit[: lit.index("}")]
    # never on a key press: `:has(.pkey.down)` restyled and repainted the
    # whole dock's layer on every key (a press blocked 40 ms, not 17)
    assert ":has(.pkey.down)" not in CSS
    assert (
        "opacity:" in lit and "box-shadow" not in lit and "will-change: opacity" in lit
    )
    # every consumer stands down under calm motion
    reduced = CSS[
        CSS.index("/* ---------- 10. Motion") : CSS.index("/* ---------- 11.")
    ]
    for consumer in (".keys-dock", ".drum-bank", ".btn-play"):
        assert consumer in reduced, consumer


# the hints a page dims (a contrast read of every page): each
# was `opacity` on a glyph — 1.7-2.3:1 rendered, and invisible to the token
# test — and is the `--text-faint` tone now (>= 3:1 on every surface: PAIRS)
DIMMED_HINTS = (
    ".pad-key",  # Loops' pad letters while typing plays the keys
    ".cell-add",  # Song's empty cell "+"
    ".sl-plus",  # an empty launcher slot's "+"
    '.route:has(select[name$="_src"] option[value="none"]:checked) > .btn',  # ± ✕
)


def test_a_dimmed_hint_is_a_faint_tone_never_an_opacity():
    """No rule that dims a hint glyph uses `opacity` below 1: the hint takes
    `--text-faint`, a tone with its own contrast row (>= 3:1)."""
    assert ("--text-faint", SURFACES, 3.0) in PAIRS
    bare = re.sub(r"/\*.*?\*/", "", CSS + DENSE, flags=re.S)
    for head, body in re.findall(r"([^{}]+)\{([^{}]*)\}", bare):
        if not any(h in head for h in DIMMED_HINTS):
            continue
        m = re.search(r"(?<![-\w])opacity:\s*([0-9.]+)", body)
        assert not m or float(m.group(1)) >= 1, head.strip()
    for hint in DIMMED_HINTS:
        pat = re.escape(hint) + r"[^{}]*\{[^}]*color: var\(--text-faint\)"
        assert re.search(pat, bare), hint
