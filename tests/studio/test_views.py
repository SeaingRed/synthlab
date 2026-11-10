from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_full_page_views(tmp_path):
    client = make_client(tmp_path)
    for name, marker in (
        ("breed", 'id="breed-board"'),
        ("map", 'id="map-svg"'),
        ("bass", 'id="bass-panels"'),
    ):
        r = client.get(f"/view/{name}")
        assert r.status_code == 200
        assert "<html" in r.text and marker in r.text  # full page


def test_htmx_views_are_fragments(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/view/breed", headers={"HX-Request": "true"})
    assert "<html" not in r.text and 'id="view"' in r.text


def test_view_editor_and_unknown(tmp_path):
    client = make_client(tmp_path)
    assert 'name="cutoff"' in client.get("/view/editor").text
    assert client.get("/view/nope").status_code == 404


def test_nav_links_live(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'hx-get="/view/breed"' in html and 'hx-get="/view/map"' in html


def test_editor_dock_is_collapsible(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert html.count("dock-toggle") >= 2  # collapse chevron + expand tab
    css = make_client(tmp_path).get("/static/style.css").text
    assert ".dock-collapsed" in css
    assert "max-width: 900px" in css


def test_view_headers_and_led(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/").text
    assert 'id="audio-led"' in html
    for view in ["breed", "map", "bass"]:
        v = client.get(f"/view/{view}").text
        assert "view-sub" in v
    assert "view-sub" in html  # editor too


def test_guidance_layer(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/").text.count("view-how") == 1
    for view in ["breed", "map", "bass"]:
        assert "view-how" in client.get(f"/view/{view}").text
    # Breed used to open on an empty board behind an "empty-hint"; it now
    # renders the first generation on arrival, so the board is the guidance.
    breed = client.get("/view/breed").text
    assert "empty-hint" not in breed
    assert "breed-board" in breed and 'class="tile' in breed


def test_non_editor_views_have_a_hidden_analysis_stub(tmp_path):
    client = make_client(tmp_path)
    for view in ["breed", "map", "bass"]:
        html = client.get(f"/view/{view}").text
        assert html.count('id="analysis"') == 1  # exactly one, hidden
        assert 'id="analysis" class="readouts" hidden' in html
    # editor's own visible #analysis must not be duplicated
    assert client.get("/view/editor").text.count('id="analysis"') == 1


def test_save_sound_is_on_the_sound_bar_and_above_factory(tmp_path):
    """The only Save button lived at the bottom of the collapsible dock —
    below every card — and vanished entirely when the dock collapsed; the
    user concluded saving was broken."""
    html = make_client(tmp_path).get("/").text
    bar = html.split('id="sound-bar"')[1].split("</form>")[0]
    assert 'popovertarget="save-dialog"' in bar
    # one Save for the sound: the dock no longer carries a second copy
    dock = html.split('class="dock module"')[1].split("</aside>")[0]
    assert "save-dialog" not in dock
    assert html.count('popovertarget="save-dialog"') == 1


def test_collapsed_dock_tab_is_labeled(tmp_path):
    """Folded, the sound list's way back is a labeled button at the top,
    left of the title (not a vertical tab half way down the left edge),
    and the folded column is 0 wide at every
    width — the wide layouts' own columns read --dock-w."""
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    assert ".cockpit.dock-collapsed { --dock-w: 0px !important; }" in css
    html = make_client(tmp_path).get("/").text
    head = html.split('class="view-head"')[1].split("</header>")[0]
    assert "dock-expand" in head and ">Sounds ›<" in head
    # the search rides the dock's sticky head, outside the refreshed list
    dock = html.split('class="dock module"')[1].split("</aside>")[0]
    assert dock.index('id="dock-filter"') < dock.index('id="preset-list"')


def test_filter_panel_shows_response_svg(tmp_path):
    """The most-touched panel was a guessing game without a response
    display: an inline SVG rides OOB with every patch post."""
    client = make_client(tmp_path)
    assert 'id="filter-response"' in client.get("/").text
    r = client.post("/fragment/patch", data={"cutoff": "500"})
    assert 'id="filter-response"' in r.text  # OOB refresh


def test_envelope_svgs_render_and_track_the_patch(tmp_path):
    """Nine envelope sliders, zero visualization — so both ADSRs
    draw inline and ride OOB with every patch post."""
    with make_client(tmp_path) as client:
        html = client.get("/").text
        assert 'id="env-amp"' in html and 'id="env-filter"' in html
        r = client.post("/fragment/patch", data={"attack": "1.5"})
        assert 'id="env-amp"' in r.text


def test_editor_dom_order_matches_grid_order(tmp_path):
    """Template order diverged from the grid's visual order (viz rendered
    3rd but displays below the panels), scrambling tab order — WCAG
    2.4.3."""
    html = make_client(tmp_path).get("/").text
    order = [
        html.index(m)
        for m in (
            'id="sound-bar"',
            'id="signal-chain"',
            'id="patch"',
            'id="fx-rack"',
            'id="macros"',
            'id="xypad-panel"',
            "piano-wrap",
            'id="chord-bar"',
        )
    ]
    assert order == sorted(order), "editor children must follow the grid order"


def test_curve_editor_needs_no_scroll_it_opens_under_its_row():
    # the shared editor opened ~1,400px below the ⤳ you pressed; it now opens
    # in the row's own .curve-slot, so nothing has to scroll it into view
    src = Path("synthlab/studio/static/audio.js").read_text(encoding="utf-8")
    assert "curve-editor" not in src


def test_shortcuts_sheet_documents_gestures(tmp_path):
    """The ⌨ sheet stopped at keys; hold-to-audition, the XY record flow
    and pen pressure were invisible gestures."""
    html = make_client(tmp_path).get("/").text
    sheet = html.split('id="shortcuts"')[1].split("</dialog>")[0]
    assert "Gestures" in sheet
    assert "hold" in sheet.lower() and "pressure" in sheet.lower()


def test_xy_loop_button_is_visible_when_disabled(tmp_path):
    """The loop arm was hidden until a gesture existed, so its sticky
    localStorage state was invisible — disabled, not hidden."""
    html = make_client(tmp_path).get("/").text
    before, after = html.split('id="xy-loop"')
    # the button's own tag
    tag = before[before.rindex("<button") :] + after.split(">")[0]
    assert "disabled" in tag and "hidden" not in tag
