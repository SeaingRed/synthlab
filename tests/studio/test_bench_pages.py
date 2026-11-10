# ruff: noqa: E501  (selectors and page scripts read better on one line)
"""The bench views on the design system (Synths, Bass, Voice). What a
browser must see is in tests/browser/test_bench.py; this is
what the markup and the stylesheet alone can say."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from synthlab.params import MOD_DESTINATIONS, MOD_SLOTS, MOD_SOURCES
from synthlab.studio.server import STUDIO_DIR, create_app

TEMPLATES = STUDIO_DIR / "templates"
CSS = (STUDIO_DIR / "static" / "style.css").read_text(encoding="utf-8")
DENSE = (STUDIO_DIR / "static" / "dense.css").read_text(encoding="utf-8")
B1 = [
    "views/editor.html", "views/bass.html", "views/voice.html",
    *(f"fragments/{n}.html" for n in (
        "sound_bar", "sound_id", "layer_strip", "chain", "presets", "panel_presets", "panel_viz",
        "param_row", "fx_rack", "curve", "ab_slots", "macros", "xypad", "piano", "chord_bar",
        "chord_library", "bass_panels", "analysis", "sampler_panel", "voice_panel", "agent_drawer",
        "track_edit_banner")),
]  # fmt: skip


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path))


def _view(client, view):
    html = client.get(f"/view/{view}", headers={"HX-Request": "true"}).text
    return html.split('id="clip-library"')[0]  # the view itself, not the shell's drawer


def _section(css, start, end):
    return css[css.index(start) : css.index(end)]


def test_the_old_button_families_are_gone_from_the_bench_templates():
    """Every button is a `.btn`, a `.seg` member or a named surface (a key, a
    pad, a card's body): no dashed `.ghost`, no `.mini`, no `.use-btn`."""
    for name in B1:
        src = (TEMPLATES / name).read_text(encoding="utf-8")
        for tag in re.findall(
            r"<(?:button|summary|a|label|select|input)\b[^>]*>", src, re.S
        ):
            cls = re.search(r'class="([^"]*)"', tag)
            words = set(
                (cls.group(1) if cls else "")
                .replace("{", " ")
                .replace("}", " ")
                .split()
            )
            assert not words & {"ghost", "mini", "use-btn", "pill", "pills"}, (
                f"{name}: {tag[:90]}"
            )
    # and their rules left the sections this package owns
    for dead in (".sb-save {", ".ls-seg {", ".l-tog {", ".xy-arm {", ".kbd-mode {", ".keys-toggle {",
                 ".pp-open {", ".code-line button", ".ad-chips .chip {", ".sound-card.factory {",
                 ".lamp.ok", ".oct-btn:hover", ".shape-note", ".curve-hint", ".macro-assign summary {"):  # fmt: skip
        assert dead not in CSS, dead


def test_no_instruction_paragraph_is_always_visible(client):
    """A hint is a `title`; how the page works is behind the head's (i)."""
    for view in ("editor", "bass", "voice"):
        html = _view(client, view)
        assert html.count("view-how") == 1, view  # the one inside #head-how
        for gone in (
            "fx-note",
            "shape-note",
            "panel-note",
            "curve-hint",
            "teb-hint",
            "save-hint",
            "voice-fx-note",
        ):
            assert f'class="{gone}' not in html, (view, gone)
    assert '<p class="quickkeys-hint' not in _view(client, "bass")


def test_one_primary_action_a_view(client):
    # the page's one primary, and the Save dialog's own submit (a dialog's
    # primary is its own: the dialog is shut until Save sound… opens it)
    editor = _view(client, "editor")
    head, _, rest = editor.partition('id="save-dialog"')
    assert (
        head.count("btn-primary") + rest.split("</form>", 1)[1].count("btn-primary")
        == 1
    )
    assert 'class="btn btn-s btn-primary sb-save"' in _view(client, "editor")
    assert (
        _view(client, "voice").count("btn-primary") == 1
    )  # Vocode it; Record is a normal button
    assert 'id="mic-btn" class="btn btn-rec"' in _view(client, "voice")  # a transport ⏺
    assert "btn-primary" not in _view(client, "bass")


def test_every_action_on_the_bench_is_a_declared_command():
    """A control that posts (or opens a panel of the page) carries `data-cmd`,
    so the palette, the shortcut sheet and a hardware key can find it. Not
    commands: a sound card's and a slice's buttons (a list — the palette
    finds sounds itself), the panel-preset rows, the curve editor's point
    tools, the agent's own requests, the forms' submit buttons — and the
    per-row tools the palette would drown in (an FX unit's move / remove,
    each parameter's automate, a panel's presets flyout, the A/B slots, an
    empty layer slot; the booth rack's move / remove and its list of
    talk takes, a list like the sounds; the chain browser's rows, a list
    behind its own "+ add chain…" command): its unfiltered list shows 80,
    and the navigation must stay on it."""
    listed = (
        "presets.html",
        "panel_presets.html",
        "curve.html",
        "agent_drawer.html",
        "chord_library.html",
    )
    missing = []
    for name in B1:
        if name.endswith(listed):
            continue
        src = (TEMPLATES / name).read_text(encoding="utf-8")
        for tag in re.findall(r"<(?:button|select)\b[^>]*>", src, re.S):
            acts = (
                re.search(r"hx-(post|get)=|popovertarget=", tag)
                and 'popovertargetaction="hide"' not in tag
            )
            skip = ("sampler/slice" in tag or "panel/preset" in tag or 'name="octave"' in tag
                    or 'name="xy_' in tag or "disabled\n" in tag or 'class="sb-sounds' in tag
                    or "fx/move" in tag or "fx/remove" in tag or "curve-toggle" in tag or "pp-open" in tag
                    or "ab/slot_" in tag or "l-pick" in tag or "dock-" in tag
                    or '"op": "move"' in tag or '"op": "remove"' in tag or "/voice/take/clip" in tag
                    or 'class="fxc-row"' in tag)  # fmt: skip
            if acts and not skip and "data-cmd=" not in tag:
                missing.append(f"{name}: {' '.join(tag.split())[:110]}")
    assert not missing, "\n".join(missing)
    voice = (TEMPLATES / "views/voice.html").read_text(encoding="utf-8") + (
        TEMPLATES / "fragments/voice_panel.html"
    ).read_text(encoding="utf-8")
    assert voice.count("data-cmd=") >= 8  # it had none


def test_the_mod_matrix_is_a_patch_bay_over_the_form_fields(client):
    html = _view(client, "editor")
    bay = html[html.index('id="patchbay"') : html.index('class="route"')]
    assert 'aria-hidden="true"' in bay[:80]  # a view: the rows are the controls
    for src in MOD_SOURCES:
        assert (f'data-src="{src}"' in bay) == (src != "none")
    for dst in MOD_DESTINATIONS:
        assert f'data-dst="{dst}"' in bay
    assert '<svg class="pb-cables"' in bay
    matrix = html[html.index('id="panel-mod-matrix"') :].split("</fieldset>")[0]
    for slot in MOD_SLOTS:
        row = matrix.split(f'data-slot="{slot}"')[1].split('class="route"')[0]
        for k in ("src", "dst", "amt"):
            assert f'name="{slot}_{k}"' in row, (slot, k)
        assert 'data-route="off"' in row and 'data-route="flip"' in row
    # the fields left Motion's module but not the form
    form = html[html.index('<form id="patch"') :].split("</form>")[0]
    assert 'id="panel-mod-matrix"' in form and form.count('class="route"') == len(
        MOD_SLOTS
    )
    motion = html[html.index('id="panel-motion"') :].split("</fieldset>")[0]
    assert "mod1_src" not in motion and 'name="mod_lfo_rate"' not in matrix


def test_every_patch_field_is_still_posted_once(client):
    """The panels moved into columns and the matrix into its own module: no
    field was lost or doubled on the way."""
    from synthlab.studio.paramui import PARAM_GROUPS

    form = _view(client, "editor")
    form = form[form.index('<form id="patch"') :].split("</form>")[0]
    for _, specs in PARAM_GROUPS:
        for spec in specs:
            n = len(
                set(
                    re.findall(
                        rf'<(?:input type="hidden"|select[^>]*|input type="range"[^>]*) name="{spec.name}"',
                        form,
                    )
                )
            )
            radios = form.count(f'type="radio" name="{spec.name}"')
            assert n == 1 or radios >= 2, spec.name
            assert form.count(f'id="row-{spec.name}"') == 1
    assert (
        form.count('class="pcol"') == 4
        # Modulators among them; .patch-panel: styled wherever it stands
        and form.count('<fieldset class="panel patch-panel module"') == 8
    )


def test_dials_are_the_same_range_inputs(client):
    html = _view(client, "editor")
    knob = re.search(r'<input type="range" id="p-osc1_level"[^>]*>', html).group(0)
    for must in (
        'name="osc1_level"',
        'class="knob"',
        "min=",
        "max=",
        "step=",
        "data-default=",
        "--fill:",
    ):
        assert must in knob, must
    assert 'class="param param-knob" id="row-osc1_level"' in html
    # long-travel parameters stay sliders
    for name in ("cutoff", "resonance", "attack", "release", "filter_env"):
        assert "knob" not in re.search(
            rf'<input type="range" id="p-{name}"[^>]*>', html
        ).group(0), name
    # an FX unit's parameters and a layer's level are dials with a read-out
    client.post("/fragment/fx/add", data={"scope": "master", "kind": "delay"})
    html = _view(client, "editor")
    unit = html[html.index('class="fx-unit module"') :].split("</div>\n    </div>")[0]
    assert (
        unit.count('class="knob"') == unit.count("<output>") >= 2
        and "data-default=" in unit
    )
    assert re.search(
        r'<input type="range" class="knob knob-s" id="l0-gain" name="gain"', html
    )
    assert '<output for="l0-gain">' in html


def test_cables_run_layer_to_fx_to_master(client):
    client.post("/fragment/layer/add")
    client.post("/fragment/layer/mute", data={"index": "0"})
    html = _view(client, "editor")
    strip = html[html.index('id="layer-strip"') :].split("</section>")[0]
    leads = re.findall(r'<g class="(cable[^"]*)" data-layer="(\d)">', strip)
    assert leads == [
        ("cable off", "0"),
        ("cable", "1"),
    ]  # the muted layer's lead is unplugged
    assert (
        strip.count('class="jack l-out"') == 2
        and 'class="ls-out" href="#fx-rack"' in strip
    )
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "reverb"})
    client.post("/fragment/fx/add", data={"scope": "layer", "kind": "chorus"})
    rack = _view(client, "editor")
    rack = rack[rack.index('id="fx-rack"') :].split("</section>")[0]
    layer, master = rack.split("</fieldset>")[:2]
    assert layer.count('<svg class="cable"') == 3  # in -> reverb -> chorus -> out
    assert master.count('<svg class="cable off"') == 2 and "empty-state" in master


def test_native_chrome_and_old_states_are_themed(client, tmp_path):
    import numpy as np

    from synthlab.studio.voice import save_take

    voice = _view(client, "voice")
    assert 'class="btn filepick"' in voice and 'type="file" id="voice-file"' in voice
    assert "empty-state" in voice  # no take, no result yet
    save_take(tmp_path, np.sin(np.arange(22050) / 20.0) * 0.3)
    voice = _view(client, "voice")
    assert (
        "<audio controls" not in voice
        and 'class="player"' in voice
        and 'src="/voice/take.wav"' in voice
    )
    bass = client.get("/fragment/bass").text
    assert bass.count('class="meter bass-meter"') == 2 and 'class="sub-plot"' in bass
    assert (
        'class="lamp led ok" data-on="on"' in bass
        or 'class="lamp led bad" data-on="clip"' in bass
    )
    editor = _view(client, "editor")
    assert 'class="empty-state"' in editor and 'class="skel"' in editor
    assert 'class="agent-drawer drawer"' in editor and 'id="agent-pin"' in editor


def test_the_bench_layout_rules():
    sec = _section(
        CSS,
        "/* ---------- 17. The bench pages",
        "/* ---------- 18. The loop pages",
    )
    # the three views tile: no gap, no padding, one surface
    assert re.search(
        r'#view:is\(\[data-view="editor"\], \[data-view="bass"\], \[data-view="voice"\]\) \{ gap: 0; padding: 0;',
        sec,
    )
    # more modules at once on a wider window
    for width in ("1200px", "1600px", "2200px"):
        assert f"@media (min-width: {width})" in sec
    assert (
        '"dock panels fx perform" "dock panels fx bass"' in sec
    )  # six across from 2200
    assert '"source form result" "take form fx"' in sec  # Voice's three columns
    assert '"keys read mono phone" "chart chart chart chart"' in sec
    # nothing a user tweaks is behind a disclosure on a desktop window
    assert ".rack::details-content { content-visibility: visible; }" in sec
    # tokens, not literals, in what this package wrote
    assert not re.findall(
        r"oklch\(|#[0-9a-f]{6}\b|rgba?\(", re.sub(r"/\*.*?\*/", "", sec, flags=re.S)
    )
    # the top dock's height is the shell's token (D1), one name for every
    # window-high view: B1 called it --top-h, B2 --dock-h, B3 --b3-top
    d1 = _section(DENSE, "/* ---------- D1 + D2.", "/* ---------- D17.")
    assert "--top-h: 59px" in d1  # 37 until the status row went under the visualisers
    assert "--dock-h" not in DENSE and "--b3-top" not in DENSE


def test_bench_island_is_served_and_draws_only_on_change(client):
    js = (STUDIO_DIR / "static" / "bench.js").read_text(encoding="utf-8")
    assert client.get("/static/bench.js").status_code == 200
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert base.index("/static/tweak.js") < base.index("/static/bench.js")
    assert (
        "new ResizeObserver(redraw)" in js
        and 'addEventListener("htmx:after:settle", settle)' in js
    )
    assert (
        "setInterval" not in js and js.count("requestAnimationFrame") == 1
    )  # the coalesced redraw
    assert (
        "S.tweak" in js and "hx-post" not in js and "/fragment/" not in js
    )  # it moves controls, it posts nothing
    assert "Object.assign(S," not in js and "window.synthlab = " not in js
