"""The live vocal chain (vocal.js, #vox-pop on every page): the mic through
a live rack, the monitor, ⏺ in time.

Proves, in Chromium with a fake microphone (a beeping tone): the panel opens
from the rail, a style loads its chain onto the rack, the mic opens and the
processed voice carries signal (an analyser on the chain's output), and ⏺
keeps two clips — the processed take and the dry one — with no console
error. Does not prove what a voice sounds like through it, or the latency
on a real interface (nobody has sung into it)."""

from __future__ import annotations

PEAK = """async () => {
  const S = window.synthlab, c = S.getCtx(), a = c.createAnalyser();
  const buf = new Float32Array(a.fftSize);
  S.vocal.out().connect(a);
  let pk = 0;
  for (let i = 0; i < 30; i++) {
    await new Promise((ok) => setTimeout(ok, 50));
    a.getFloatTimeDomainData(buf);
    for (const v of buf) pk = Math.max(pk, Math.abs(v));
  }
  S.vocal.out().disconnect(a);
  return pk;
}"""


def test_a_style_loads_the_mic_sings_through_it_and_rec_keeps_both(studio, server):
    page = studio.open("song")
    studio.settle()
    page.locator(".view-head h1").first.click()  # a gesture: the audio context starts
    page.locator("#vox-open").click()
    page.wait_for_selector("#vox-btn")
    with page.expect_response("**/voice/live/style"):
        page.locator(".vox-styles button", has_text="Pop polish").click()
    page.wait_for_selector('#vox-panel .fx-unit[data-unit="autotune"]')
    page.locator("#vox-btn").click()
    page.wait_for_selector('#vox-btn[aria-pressed="true"]', timeout=15_000)
    assert page.locator('#vox-open[aria-pressed="true"]').count() == 1
    assert "ms" in page.locator("#vox-status").inner_text()  # the latency, said
    assert page.evaluate(PEAK) > 1e-4  # the fake mic's tone, through the chain
    # ⏺ with nothing playing and no count-in: free length, from now
    if page.get_attribute("#vox-count", "aria-pressed") == "true":
        page.locator("#vox-count").click()
    page.locator("#vox-rec").click()
    page.wait_for_selector('#vox-rec[aria-pressed="true"]')
    page.wait_for_timeout(1200)
    with page.expect_response("**/take/vocal") as got:
        page.locator("#vox-rec").click()
    assert got.value.status == 200
    clips = server.data_dir / "clips"
    wet = sorted(p for p in clips.glob("vocal-*.wav") if not p.stem.endswith(" dry"))
    assert wet, "no vocal take in the clip store"
    assert (clips / f"{wet[-1].stem} dry.wav").is_file()
    meta = wet[-1].with_suffix(".meta.json").read_text(encoding="utf-8")
    assert '"source": "vocal"' in meta and '"style": "pop"' in meta
    page.wait_for_selector(".vox-takes li")
    page.locator("#vox-btn").click()
    page.wait_for_selector('#vox-btn[aria-pressed="false"]')


# each module's sockets against its sides, in px (0: the plug is where the box is)
OFF_SIDES = """() => {
  const rack = document.querySelector('#vox-panel .fx-units');
  const o = rack.getBoundingClientRect();
  const mods = [...rack.querySelectorAll(':scope > .fx-unit')], n = mods.length;
  const s = [...rack.querySelectorAll('.fx-wires circle.socket')]
    .map((c) => [+c.getAttribute('cx'), +c.getAttribute('cy')]);
  const off = (p, x, y) => Math.hypot(p[0] - x, p[1] - y);
  return Math.max(...mods.flatMap((m, i) => {
    const b = m.getBoundingClientRect(), y = b.top + b.height / 2 - o.top;
    return [off(s[i], b.left - o.left, y), off(s[n + i], b.right - o.left, y)];
  }));
}"""


def test_reset_puts_the_chain_and_the_input_back_and_the_leads_stay_on_the_modules(
    studio, server
):
    """The panel resets to its defaults, and the cords stay put while
    things change: a morph of the panel must keep the rack's
    `[data-wired]`, which lays it out, or the leads are measured before it
    comes back and land 28-49 px inside the modules."""
    page = studio.open("editor")
    studio.settle()
    page.locator("#vox-open").click()
    page.wait_for_selector("#vox-panel .fx-unit")
    page.wait_for_function(
        "() => document.querySelector('#vox-panel .fx-units[data-wired]')"
    )
    knob = page.locator("#vox-panel .fx-unit input[type=range]").first
    before = knob.input_value()
    knob.focus()
    for _ in range(6):
        page.keyboard.press("ArrowUp")
    page.wait_for_function(
        "(v) => document.querySelector('#vox-panel .fx-unit input[type=range]')"
        ".value !== v",
        arg=before,
    )
    studio.settle()
    page.wait_for_timeout(300)
    assert page.evaluate(OFF_SIDES) < 1.5
    assert "(edited)" in page.locator("#vox-panel legend .panel-name").text_content()
    page.locator('[data-vox="gain"]').fill("2.5")
    page.locator('[data-vox="gain"]').dispatch_event("input")
    with page.expect_response("**/voice/live/style"):
        page.locator("#vox-reset").click()
    studio.settle()
    assert (
        "(edited)" not in page.locator("#vox-panel legend .panel-name").text_content()
    )
    assert (
        page.locator("#vox-panel .fx-unit input[type=range]").first.input_value()
        == before
    )
    assert page.locator('[data-vox="gain"]').input_value() == "1"
    page.wait_for_timeout(300)
    assert page.evaluate(OFF_SIDES) < 1.5
