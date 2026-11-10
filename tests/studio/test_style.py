import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def test_stylesheet_served_with_theme_tokens(tmp_path):
    r = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css")
    assert r.status_code == 200
    css = r.text
    assert 'data-theme="day"' in css or "[data-theme='day']" in css.replace('"', "'")
    assert "oklch(" in css
    # calm motion is the Motion setting (colours.js), never the OS's
    assert 'html[data-motion="calm"]' in css
    assert "--accent" in css


def test_polish_css_markers(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    assert "animation-timeline" in css  # scroll-driven reveal
    assert "@container" in css  # container query actually used
    assert 'html[data-motion="calm"]' in css  # calm motion still there, on request


def test_control_polish_markers(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    assert "var(--fill" in css
    assert "appearance: none" in css  # styled selects/native chrome removed
    html = client.get("/").text
    assert "--fill:" in html  # server-rendered fill positions


def test_dynamic_fit_css_markers(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    # .param grid: label / value / value-chip / curve-toggle, all fixed via
    # max-content so a chip or toggle can never push past the panel edge.
    assert "max-content max-content" in css
    assert ".curve-toggle {" in css
    assert "border-radius: 50%" in css
    assert "@container (max-width: 300px)" in css
    assert "@container (max-width: 240px)" in css


def test_fmt_macro_shrinks_value_chips(tmp_path):
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    # Patch() defaults: cutoff=2200.0, filter_env=2500.0 — the raw
    # float repr used to overflow the chip; fmt() drops the trailing ".0".
    assert ">2200 Hz<" in html  # cutoff chip now carries its unit
    assert ">2200.0<" not in html
    assert ">2500 Hz<" in html  # filter_env carries Hz too
    assert ">2500.0<" not in html
    # inputs still carry the raw, unrounded value — only the <output> chips
    # are reformatted for display.
    assert 'value="2200.0"' in html


def test_narrow_param_tier_matches_runtime_measured_fit(tmp_path):
    """Real panels sit ~283px wide under the 5-group auto-fit layout, so
    the @container (max-width: 300px) tier IS the default rendering, not
    an edge case. Runtime-measured (Playwright, 1400px window) with these
    values: zero .param label elements had scrollWidth > clientWidth.
    """
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    assert "60px minmax(0, 1fr) max-content max-content" in css
    assert "letter-spacing: 0.02em;" in css


def test_synths_grid_is_laid_out_by_job(tmp_path):
    # UX review (Sep 2026): strip, chain, panels, fx, perform, bass, keys —
    # the XY pad and macros share the folded Perform controls
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    for area in ("strip", "chain", "panels", "fx", "perform", "bass", "keys"):
        assert f'"dock {area}"' in css
    assert ".keys-dock { position: sticky; bottom: 0;" in css


def test_view_transitions_only_for_a_page_change(tmp_path):
    """The View Transitions API once froze rendering and threw
    InvalidStateError with htmx swaps (htmx 2: every swap one). Now one
    runs for a page change alone (pagemotion.js asks for it
    on the swap into #view that brings another page; no link carries
    `transition:true`), its names exist only while it runs, set inline on
    three elements (a name on #view at rest is a stacking context; an
    attribute on <html> restyled the whole page), the pointer goes through
    it, and a skipped one's `ready` rejection is handled."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    assert "view-transition-name" not in css  # (pagemotion.js names them inline)
    assert "::view-transition-old(page-down)" in css
    assert "::view-transition { pointer-events: none; }" in css
    html = client.get("/").text
    assert "transition:true" not in html
    js = Path("synthlab/studio/static/pagemotion.js").read_text(encoding="utf-8")
    assert "transition: true" in js and "S.calm?.()" in js
    assert "ready.catch(" in js and "skipTransition()" in js
    assert 'setProperty("view-transition-name"' in js


def test_dockless_views_escape_cockpit_grid(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    # single-column override must exist for all three dockless views
    assert ".breed-view" in css and ".map-view" in css and ".bass-view" in css
    # native-chrome escapees are styled
    assert ".quickkeys button" in css
    assert ".tile > button" in css
    assert ".tile .star" in css
    assert "grid-template-rows: none" in css


def test_coarse_pointer_touch_targets(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    assert "pointer: coarse" in css
    assert "-webkit-touch-callout" in css


def test_luxe_tokens(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    for tok in ["--s1", "--s8", "--fs-xs", "--fs-xl", "--r-m", "--live"]:
        assert tok in css, tok
    assert "text-wrap: balance" in css


def test_modern_css_layer(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    for marker in [
        "content-visibility",
        "@starting-style",
        "interpolate-size",
        "anchor-name",
        "field-sizing",
    ]:
        assert marker in css, marker


def test_reactive_light_css(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    # the LED, the slider track glow (x2) and the panel edge-light all
    # consume the reduced-motion-safe indirection, never --live
    # directly, so a media query can pin the derived var without fighting
    # the inline style the JS writes on --live itself.
    assert "--live-eff: var(--live);" in css
    assert css.count("var(--live-eff)") >= 5
    assert "--live-eff: 0" in css
    # the panels' live light is a layer: its opacity alone
    # follows the level; no border-color or box-shadow reads --live-eff
    # (each write repainted every panel). The rest edge rule still comes
    # after every .viz/.transport `border:` shorthand it must win over.
    assert ".panel::before {" in css
    layer = css[css.index(".panel::before {") :]
    layer = layer[: layer.index("}")]
    assert (
        "opacity: max(var(--live-eff), var(--ignite))" in layer
        and "border-radius: inherit" in layer
    )
    assert "var(--live-eff) * 30%" not in css  # the old edge mix
    assert "var(--live-eff) * var(--ah-glow) * 26px" not in css  # the outer glow
    assert "var(--live-eff) * var(--ah-stage) * 30px" not in css  # the inner wash
    assert "calc(var(--live-eff) * 3px) var(--cable)" not in css  # the leads' shadow
    edge = css.rindex(".panel, .viz, .transport { border-color: var(--line); }") + 30
    assert edge > css.rindex(".viz {") and edge > css.rindex(".transport {")


def test_controls_restyle(tmp_path):
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    assert "subgrid" in css
    assert css.count("--knob-face") >= 2  # machined knob styling retained


def test_day_theme_signal_hue():
    """C1 polish: day theme uses deep teal for --signal, not the accent orange
    (resolved from the one palette: the day's depths, the signal's hue)."""
    from tests.studio.test_design_system import _resolved

    # (the palette: one teal, 195, on the theme's tier)
    assert _resolved("day")["--signal"] == (0.54, 0.092, 195.0)
    assert _resolved("night")["--signal"] == (0.75, 0.125, 195.0)


def test_non_editor_analysis_stub_stays_hidden(tmp_path):
    """Non-editor views host a hidden #analysis stub as an OOB target; the
    arriving fragment isn't [hidden], so CSS must hide it structurally."""
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    assert ".bass-view #analysis" in css
    assert "display: none" in css.split(".bass-view #analysis")[1][:120]


def test_beat_grouped_grid_and_clustered_transport(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    # the clusters split with the views: Synths keeps the bench controls,
    # Perform keeps everything that shapes the pattern
    bench = client.get("/").text
    assert '<fieldset class="tcluster"><legend>Phrase</legend>' in bench
    # the layers have their own strip, outside the sound bar's form
    assert 'id="layer-strip"' in bench
    seq = client.get("/view/perform").text
    for legend in ["length", "pattern", "feel"]:
        assert f'<fieldset class="tcluster"><legend>{legend}</legend>' in seq
    # play button is outside every cluster, first in the bench strip
    assert "<legend>Key</legend>" in bench
    assert bench.index('id="play"') < bench.index('class="tcluster"')
    assert 'class="grid roll" aria-label="Note grid"' in seq  # the piano roll
    css = client.get("/static/style.css").text
    assert ".tcluster {" in css
    assert ".tcluster legend {" in css


def test_xypad_height_and_quadrant_grid(tmp_path):
    """Header row now holds legend + axis selects inline, freeing height the
    pad itself no longer needs at 260px; quarter-tick gradient marks add a
    faint secondary grid alongside the existing strong center lines."""
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    xypad_rule = css.split("#xypad {")[1].split("}")[0]
    assert "aspect-ratio: 1 / 1" in xypad_rule  # square: equal x/y measures
    assert "width: min(100%, 320px)" in xypad_rule
    assert "25%" in xypad_rule  # faint quarter-tick gradient layer
    assert ".xy-head {" in css


def test_accessibility_pass_css_markers(tmp_path):
    """Accessibility pinned: one global focus ring, no undefined tokens, the
    step grid scrolls inside the transport instead of widening the cockpit,
    and the coarse-pointer tier covers the small targets."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    assert ":focus-visible { outline: 2px solid var(--accent)" in css
    assert "--bg-base" not in css
    assert ".transport { grid-area: transport; min-width: 0; }" in css
    assert "minmax(var(--step-min), var(--step-max, 1fr))" in css
    assert "overflow-x: auto" in css.split(".grid {")[1].split("}")[0]
    # every touch-tier block (a later section's own sits after the shell's)
    coarse = "".join(css.split("@media (pointer: coarse) {")[1:])
    assert "font-size: 16px" in coarse  # no iOS zoom on focus
    assert ".dock-toggle { width: 44px; height: 44px; }" in coarse
    assert ".dot { width: 24px; height: 24px; }" in coarse
    # disclosures have escaped the tier before — every new <details>/mini
    # control must join it
    for sel in (
        ".pp-open",
        ".macro-assign > .btn",
        ".pads-label",
        ".bass-check summary",
        "#xy-center",
        ".sound-card .rename",
        # measured on a real page: the Library's tag filters and the drum
        # panel's pills were the last controls with no touch size at all
        ".tagfilter",
    ):
        assert sel in coarse, f"{sel} missing from the coarse-pointer tier"
    # (the drum panel's pills are `.btn`s: their touch size is the
    # canonical component's — there is no `.pill-btn` line, which lost to
    # it on every page)
    assert (
        ".btn, .btn-s, .btn-l, .field, .field-s, .menu-item, .row { min-height: 44px; }"
        in coarse
    )
    assert 'class="btn btn-s pill-btn' in client.get("/view/drums").text
    assert "font-size: 9px" not in css
    assert ".note-pads {" in css and ".note-pad {" in css
    html = client.get("/").text
    assert 'aria-current="page"' in html
    assert '<nav class="views" aria-label="Views">' in html
    assert 'role="grid"' not in html
    assert 'id="toasts" role="status" aria-live="polite"' in html


def test_hidden_attribute_beats_author_display_rules(tmp_path):
    """The UA's `[hidden] { display: none }` loses to any author `display`
    declaration. `.lib-card { display: grid }` therefore made `el.hidden =
    true` a no-op: filtering the Library reported "18 of 96" while 32 cards
    stayed painted on screen."""
    import re

    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    # the rule itself, not the comment above it that quotes the UA default
    assert re.search(
        r"^\[hidden\]\s*\{\s*display:\s*none\s*!important;?\s*\}", css, re.M
    ), "style.css needs a top-level `[hidden] { display: none !important; }`"


def test_piano_keys_have_their_own_contrast_tokens(tmp_path):
    """On the generic --bg-raised/--bg-inset surface tokens the two key types
    sat 1.14:1 apart (night) and 1.21:1 (day), and a black key was 1.04:1
    against the page — the keyboard rendered as a flat slab. WCAG 1.4.11
    wants >= 3:1 for a control boundary."""
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    from tests.studio.test_design_system import _resolved

    for theme in ("night", "day"):  # one formula each, a depth per theme
        keys = _resolved(theme)
        for token in ("--key-white", "--key-black", "--key-line", "--key-label"):
            assert token in keys, f"{token} missing from the {theme} theme"
    assert "background: var(--key-white)" in css
    assert "background: var(--key-black)" in css
    # and the keys must no longer reach for the generic surface tokens
    keyrule = css[css.index(".pkey {") : css.index(".pkey .qhint")]
    assert "--bg-raised" not in keyrule and "--bg-inset" not in keyrule


def test_perform_names_every_grid_area_at_both_breakpoints(tmp_path):
    """The narrow override declared only "head"/"pads"/"right", so `bar`
    (#perform-transport), `seq` (.transport) and `song` (#song-strip) fell
    into implicit tracks and the page scrolled sideways at 375px. `right`
    later split into `xy` + `launch`: as one half-width column the launcher
    ran ~600px past the pads and put the beats, sounds and scenes you reach
    for mid-performance below the fold, with dead space beside them."""
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    import re

    areas = re.findall(r"\.perform-view\s*\{[^}]*?grid-template-areas:\s*([^;]+);", css)
    assert len(areas) >= 2, f"expected a desktop and a narrow definition, got {areas}"
    for block in areas:
        named = set(re.findall(r"[a-z]+", block))
        # `loops`: the Loops panel's column; `slots`, `rack` and `side`: the
        # launcher, the drum grid and the right column (→ Song, the lane
        # inspector and Play stand in `side`; the pads, the XY, the FX and
        # the beats are Play's tabs, not areas of the page)
        expected = {"head", "bar", "loops", "slots", "seq", "rack", "side"}
        assert expected <= named, block


def test_dockless_view_children_can_shrink(tmp_path):
    """A grid item defaults to min-width:auto, so #drum-grid was propped open
    by its rows' min-content and the whole page scrolled sideways instead of
    the grid scrolling inside itself."""
    css = TestClient(create_app(data_dir=tmp_path)).get("/static/style.css").text
    block = css[css.index(":is(.breed-view, .map-view, .bass-view, .drums-view") :][
        :400
    ]
    assert "min-width: 0" in block
    assert "#drum-grid, #drum-grid > * { min-width: 0; }" in css


def test_dead_shape_controls_are_dimmed(tmp_path):
    """FM/pluck/wavetable rows rendered identically whether or not any osc
    used the shape — live and dead sliders looked the same.
    A generated :has() style block dims them, read from the shape rules
    in synthlab.params (the breeder's own liveness)."""
    html = TestClient(create_app(data_dir=tmp_path)).get("/").text
    assert ":not(:has(" in html
    assert "#row-fm_ratio" in html and "#row-pluck_damping" in html
    from synthlab.studio.paramui import SHAPE_DEPS

    assert set(SHAPE_DEPS) == {"fm", "fm4", "pluck", "wavetable", "sampler"}
    assert "#row-sampler_root" in html


def test_perform_section_headings_do_not_sit_on_their_controls(tmp_path):
    """`.launch-title` carried a -8px bottom margin, so the row beneath it
    started 8px inside the heading's own box: on Perform the FX / Beats /
    Sounds / Scenes captions collided with the controls they label."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    rule = css.split(".launch-title {")[1].split("}")[0]
    assert "-8px" not in rule, f"negative bottom margin is back: {rule.strip()}"


def test_the_sections_claiming_perform_grid_areas_are_actually_rendered(tmp_path):
    """A named area with no element in it is just a gap, and an element with
    no area lands in an implicit row below the layout."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    html = client.get("/view/perform").text
    # (the pads, the XY and the beats are the Play module's tabs, in the
    # right column, `side`)
    pairs = (
        ("perform-slots", "slots"),
        ("perform-side", "side"),
        ("perform-drums", "rack"),
    )
    for cls, area in pairs:
        assert cls in html, f"{cls} is not rendered"
        assert f"grid-area: {area}" in css, f"{cls} has no grid-area rule"


def test_the_launchers_fill_the_width_they_are_given(tmp_path):
    """At full width the pattern bank should lay its 8 slots out in one run
    rather than staying on the 4 columns it needed inside a half-width
    column."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    rule = css.split(".launcher {")[1].split("}")[0]
    assert "auto-fill" in rule, f"launcher still has fixed columns: {rule.strip()}"


def test_song_names_every_grid_area_at_both_breakpoints(tmp_path):
    """Layout A: the Song view's regions each own a named area on
    desktop AND at <= 900px, where the pool becomes a bottom sheet and the
    drawer drops under the arranger. A region without an area lands in an
    implicit row below the layout (the Perform lesson above)."""
    import re

    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    areas = re.findall(r"\.song-view\s*\{[^}]*?grid-template-areas:\s*([^;]+);", css)
    assert len(areas) >= 2, f"expected a desktop and a narrow definition, got {areas}"
    narrow = re.search(
        r"@media \(max-width: 900px\) \{\s*/\* one column; the pool", css
    )
    assert narrow, "the narrow Song layout block is missing"
    assert re.search(r"\.song-view\s*\{[^}]*grid-template-areas", css[narrow.start() :])
    expected = {"head", "top", "pool", "arr", "drawer"}
    for block in areas:
        assert expected <= set(re.findall(r"[a-z]+", block)), block
    for cls, area in (
        (".song-topbar", "top"),
        (".song-pool", "pool"),
        (".song-arranger", "arr"),
        (".song-drawer", "drawer"),
        (".view-head", "head"),
    ):
        assert re.search(
            rf"\.song-view {re.escape(cls)} \{{ grid-area: {area};", css
        ), cls
    html = client.get("/view/song").text
    for cls in (
        "song-topbar",
        "song-pool",
        "song-arranger",
        "song-drawer",
        "view-head",
    ):
        assert re.search(rf'class="{cls}[ "]', html), f"{cls} is not rendered"
    # the song view must not fall back into the dockless single-column rule
    dockless = css.split(".breed-view, .map-view, .bass-view, .drums-view")[1][:200]
    assert ".song-view" not in dockless


def test_song_pattern_making_css_markers(tmp_path):
    """The Song page's pattern-making pass (browser-verified, Oct 2026)."""
    import re

    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    # #song-thumbs is a child of the .song-view grid with no area of its
    # own: it must stay out of flow, or it lands in an implicit row
    thumbs = re.search(r"\.song-thumbs \{([^}]*)\}", css).group(1)
    assert "position: absolute" in thumbs and "width: 0" in thumbs
    assert 'id="song-thumbs" class="song-thumbs"' in client.get("/view/song").text
    # a cell stacks its name over its thumbnail, in the pattern's colour
    assert ".arr-cell.filled .cell-btn { flex-direction: column;" in css
    assert re.search(r"\.cell-thumb \{[^}]*fill: color-mix\([^;]*--pattern-color", css)
    # the cells sharing a pattern light up together (songcell.js: .same)
    assert ".arr-cell.same .cell-btn" in css and ".pool-pattern.same" in css
    # length and tools share one wrapping row; the pick list is touch-sized
    assert re.search(r"\.drawer-rack \{[^}]*flex-wrap: wrap", css)
    assert "@media (pointer: coarse) { .pick { min-height: 44px; } }" in css


def test_browser_verified_fix_markers(tmp_path):
    """Pins the layout fixes a browser pass found (Sep 2026)."""
    client = TestClient(create_app(data_dir=tmp_path))
    css = client.get("/static/style.css").text
    html = client.get("/").text
    # panel presets are popovers (top layer): .panel is overflow: clip, and a
    # <details> flyout inside it opened cut off / behind the next panel
    assert '<details class="panel-presets"' not in html
    opens = html.count('pp-open" popovertarget="pp-')
    assert opens == html.count('<div popover id="pp-') >= 6
    assert "position-area: bottom span-left" in css
    # the dock's popovers (What plays, the arrow bend) open from their own
    # buttons — the typing rows' used to hang off the far end of the dock
    assert "position-anchor: --kbd-rows; position-area: top span-right;" in css
    assert "position-anchor: --bend-set; position-area: top span-right;" in css
    # curve time/value fields are wide enough to read "50"
    assert '.curve-add input[type="number"] { field-sizing: fixed; width: 8ch; }' in css
    # the drum "bars" label wraps with its chips
    assert "white-space: nowrap" in css.split(".bar-pick {")[1].split("}")[0]
    drums = client.get("/view/drums", headers={"HX-Request": "true"}).text
    assert '<span class="bar-pick"><span class="label-text">bars</span>' in drums


def test_every_range_input_draws_its_fill(tmp_path):
    """A range without an inline --fill drew the 50% fallback until touched
    (strum, morph, every FX param, drum swing/humanise)."""
    client = TestClient(create_app(data_dir=tmp_path))
    for kind in ("delay", "reverb"):
        client.post("/fragment/fx/add", data={"scope": "master", "kind": kind})
    # the morph slider shows with a sound in the slot you're not hearing
    client.post("/fragment/ab/slot_a")
    client.post("/fragment/ab/slot_b")
    pages = [client.get("/").text]
    pages += [
        client.get(f"/view/{v}", headers={"HX-Request": "true"}).text
        for v in ("drums", "perform")
    ]
    ranges = [t for h in pages for t in re.findall(r'<input[^>]*type="range"[^>]*>', h)]
    assert any('name="t"' in t for t in ranges)  # morph
    assert sum('hx-post="/fragment/fx/set"' in t for t in ranges) >= 2
    # #space / #volume are client-owned and filled by JS (volume carries one);
    # the Colours popover's hue sliders draw a hue wheel, not a fill
    bare = [
        t
        for t in ranges
        if "--fill" not in t and 'id="space"' not in t and "colours-hue" not in t
    ]
    assert not bare, bare
