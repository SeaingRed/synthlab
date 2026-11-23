"""What a small DOM change restyles on Loops, Synths and the Library, in
Chromium.

Chromium keeps one invalidation set for every `:has()` rule on the page;
a DOM change under an element some `:has()` rule tests schedules it there,
and every descendant matching any feature in it restyles (frontend.md,
"Page weight": "No `:has()` on a big container"). The deck's bar readout,
rewritten each bar while a loop plays, restyled ~2,850 elements — a
100-120 ms stall a bar, the loop's stutter — through `.cockpit:has(.dock)`
(#view) and `.deck:has(#jam-rec…)`; a node into the note grid restyled its
~1,500 cells through `.grid.roll:has(> .chancerow)`. These read the
browser's own count of the elements a style pass touched.
"""

from __future__ import annotations

LIMIT = 300  # elements: a few modules' worth, never the page (it was 1,500-2,850)


def _restyled(page, change: str) -> int:
    """The elements `change` (a JS function body run in the page)
    restyles: the most one style pass touched around it, the lesser of two
    tries (a page still settling restyles on its own inside the window)."""
    return min(_pass(page, change) for _ in range(2))


def _pass(page, change: str) -> int:
    cdp = page.context.new_cdp_session(page)
    events: list[dict] = []
    cdp.on("Tracing.dataCollected", lambda d: events.extend(d["value"]))
    done: list[bool] = []
    cdp.on("Tracing.tracingComplete", lambda _d: done.append(True))
    trace = {"categories": "devtools.timeline", "transferMode": "ReportEvents"}
    cdp.send("Tracing.start", trace)
    page.wait_for_timeout(100)
    page.evaluate(f"() => {{ {change}; document.body.offsetWidth; }}")
    page.wait_for_timeout(100)
    cdp.send("Tracing.end")
    for _ in range(50):  # the trace's events arrive after its end
        if done:
            break
        page.wait_for_timeout(50)
    cdp.detach()
    counts = [
        e.get("args", {}).get("elementCount", 0)
        for e in events
        if e.get("name") == "UpdateLayoutTree"
    ]
    assert counts, "no style pass traced"
    return max(counts)


def test_the_deck_readout_and_the_roll_restyle_only_themselves(studio):
    page = studio.open("perform")
    page.wait_for_selector("#perform-transport .deck-pos")
    page.wait_for_selector("#view .grid.roll")
    studio.settle()
    # the readout's text node replaced, as any island's textContent write does
    pos = "document.querySelector('#perform-transport .deck-pos')"
    readout = _restyled(page, f"{pos}.textContent = '3 / 4'")
    # a node into the roll and out again
    roll = _restyled(
        page,
        "const g = document.querySelector('#view .grid.roll'),"
        " s = document.createElement('span');"
        " g.append(s); document.body.offsetWidth; s.remove()",
    )
    assert readout < LIMIT, readout
    assert roll < LIMIT, roll
    assert studio.unexplained() == []


# a node into a Synths panel (it was ~1,500: #view:has() on the osc
# radios) and into a Library shelf (~1,100: .library-view:has(.lib-card…))
PROBE = (
    "const m = document.querySelector('{sel}'), s = document.createElement('i');"
    " m.append(s); document.body.offsetWidth; s.remove()"
)


def test_synths_and_library_changes_restyle_only_themselves(studio):
    page = studio.open("editor")
    page.wait_for_selector("#panel-source")
    studio.settle()
    synths = _restyled(page, PROBE.format(sel="#panel-source .param label"))
    studio.nav("library")
    page.wait_for_selector(".library-view .shelf .lib-card")
    studio.settle()
    library = _restyled(page, PROBE.format(sel=".library-view .shelf .lib-name"))
    assert synths < LIMIT, synths
    assert library < LIMIT, library
    assert studio.unexplained() == []
