"""The FX racks' patch leads (fxcables.js): drawn from where the modules
are laid out — one lead a hop (in → each module → out), a plug at each end,
a socket on each side of each module — and joined whether the next module
sits beside or on the row below, inside the rack. Proves the geometry, not
the look."""

from __future__ import annotations

WIRES = """() => {
  const rack = document.querySelector('#fx-rack .fx-units');
  const o = rack.getBoundingClientRect(), svg = rack.querySelector('.fx-wires');
  const units = [...rack.querySelectorAll(':scope > .fx-unit')].map((u) => {
    const b = u.getBoundingClientRect();
    return { left: b.left - o.left, right: b.right - o.left,
      top: Math.round(b.top - o.top) };
  });
  const cores = svg ? [...svg.querySelectorAll('.core')].map((p) => {
    const b = p.getBBox();
    return { x0: b.x, x1: b.x + b.width };
  }) : [];
  return {
    wired: rack.hasAttribute('data-wired'), units, cores, width: o.width,
    plugs: svg ? svg.querySelectorAll('.plug').length : 0,
    sockets: svg ? svg.querySelectorAll('.socket').length : 0,
  };
}"""


def test_the_leads_join_the_modules_beside_and_below(studio):
    page = studio.open("editor")
    studio.settle()
    for kind in ("vinyl", "pump", "reverb", "chorus"):
        with page.expect_response("**/fragment/fx/add"):
            page.evaluate(
                "(k) => htmx.ajax('POST', '/fragment/fx/add', {values: {scope: 'layer',"
                " kind: k}, target: '#fx-rack', swap: 'outerHTML'})",
                kind,
            )
        studio.settle()
    page.set_viewport_size({"width": 1400, "height": 900})  # the layer rack wraps
    # the leads follow a resize on the next frames (ResizeObserver, then rAF)
    page.evaluate(
        "() => new Promise((r) => setTimeout("
        "() => requestAnimationFrame(() => requestAnimationFrame(r)), 100))"
    )
    page.wait_for_function(
        "() => document.querySelectorAll('#fx-rack .fx-wires .core').length === 5"
    )
    w = page.evaluate(WIRES)
    assert w["wired"] and len(w["units"]) == 4
    assert w["plugs"] == 10 and w["sockets"] == 8  # two a lead, two a module
    assert len({u["top"] for u in w["units"]}) >= 2, "the rack should wrap at 1400"
    # every lead stays inside the rack, a row change included
    assert all(-2 <= c["x0"] and c["x1"] <= w["width"] + 2 for c in w["cores"]), (
        w["units"],
        w["cores"],
    )
    # the inline leads are hidden, not removed (their room is the plugs' gap)
    assert page.evaluate(
        "() => [...document.querySelectorAll("
        "'#fx-rack .fx-units[data-wired] > svg.cable')]"
        ".every((c) => getComputedStyle(c).visibility === 'hidden')"
    )
    # one module removed: the leads follow on the next settle
    with page.expect_response("**/fragment/fx/remove"):
        page.locator("#fx-rack .fx-unit").first.locator(
            "[hx-post='/fragment/fx/remove']"
        ).click()
    page.wait_for_function(
        "() => document.querySelectorAll('#fx-rack .fx-wires .core').length === 4"
    )


def test_the_booths_rack_is_wired_as_every_rack_is(studio):
    """The booth draws THE rack (fragments/fx_units.html): jacks at both
    ends, so its modules get leads, plugs and sockets as Synths' do (the
    Song drawer's: tests/browser/test_fx_presets.py)."""
    from .test_vocal_booth import _take

    page = studio.page
    _take(studio)
    page.get_by_role("button", name="Dreamy plate").click()  # chorus -> plate
    page.wait_for_selector(".vb-rack .fx-unit[data-unit='plate']")
    studio.settle()
    page.wait_for_function(
        "() => document.querySelectorAll('.vb-rack .fx-wires .core').length === 3"
    )
    w = page.evaluate(WIRES.replace("#fx-rack .fx-units", ".vb-rack .fx-units"))
    assert w["wired"] and len(w["units"]) == 2
    assert w["plugs"] == 6 and w["sockets"] == 4
