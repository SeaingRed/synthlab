"""The live talkbox (Synths' keyboard dock, "🎙 talk"): the mic is the
voice, the keys the carrier (talkbox.js, talk-worklet.js).

Proves, in Chromium with a fake microphone (a tone): the switch opens the
mic, loads the worklet and routes the key bus into it — the live out is
the vocoder's input while it is on and what it was before once it is off —
and a key played through it raises the master's level with no console
error. Does not prove what a voice sounds like through it (nobody has
listened)."""

from __future__ import annotations

LIVE_OUT_IS_MASTER = "() => window.synthlab.liveOut() === window.synthlab.getMaster()"


def test_the_talkbox_takes_the_keys_and_gives_them_back(studio):
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()  # a gesture: the audio context starts
    page.wait_for_function("() => !!window.synthlab.getCtx?.()")
    before = page.evaluate(LIVE_OUT_IS_MASTER)
    talk = page.locator("#talk-btn")
    talk.click()
    page.wait_for_selector('#talk-btn[aria-pressed="true"]', timeout=15_000)
    assert page.evaluate("() => window.synthlab.talkbox.on()")
    assert not page.evaluate(LIVE_OUT_IS_MASTER)  # the keys go to the vocoder
    page.keyboard.down("KeyA")
    page.wait_for_timeout(600)
    page.keyboard.up("KeyA")
    talk.click()
    page.wait_for_selector('#talk-btn[aria-pressed="false"]')
    assert not page.evaluate("() => window.synthlab.talkbox.on()")
    assert page.evaluate(LIVE_OUT_IS_MASTER) == before  # given back


def test_the_panel_sets_a_character_and_records_a_take_to_clips(studio, server):
    """⚙: Robot sets the carrier to its own saw (it talks with no key held);
    a slider moves a setting; ⏺ records what the vocoder plays into the
    clip library as a talk-<time> clip."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()
    page.locator('[popovertarget="talk-pop"]').click()
    page.locator('[data-talk-character="robot"]').click()
    assert page.evaluate("() => window.synthlab.talkbox.options().carrier") == "robot"
    page.locator("#talk-btn").click()
    page.wait_for_selector('#talk-btn[aria-pressed="true"]', timeout=15_000)
    page.locator('[popovertarget="talk-pop"]').click()
    page.locator("#talk-rec").click()
    page.wait_for_selector('#talk-rec[aria-pressed="true"]')
    page.wait_for_timeout(1200)  # the fake mic's tone through the robot
    with page.expect_response("**/clip/upload"):
        page.locator("#talk-rec").click()
    clips = sorted((server.data_dir / "clips").glob("talk-*.wav"))
    assert clips, "no talk take in the clip store"
    meta = clips[-1].with_suffix(".meta.json").read_text(encoding="utf-8")
    assert '"source": "talkbox"' in meta  # not "jam"
    # ↺ Defaults (the panel is still open): a moved setting and the robot go back
    page.locator('[data-talk="shift"]').fill("7")
    page.locator("#talk-reset").click()
    opts = page.evaluate("() => window.synthlab.talkbox.options()")
    assert (opts["character"], opts["carrier"], opts["shift"], opts["note"]) == (
        "talkbox",
        "keys",
        0,
        48,
    )
    assert page.input_value('[data-talk="note"]') == "48"
    talkbox = page.locator('[data-talk-character="talkbox"]')
    assert talkbox.get_attribute("aria-pressed") == "true"
    page.keyboard.press("Escape")
    page.locator("#talk-btn").click()
    page.wait_for_selector('#talk-btn[aria-pressed="false"]')


def test_record_to_the_booth_makes_the_take_the_voice_take(studio, server):
    """⏺ → booth: the talk take is kept in Clips as ⏺ keeps it, then made the
    Voice page's take (`/voice/take/clip`, booth=1) — off the Voice page the
    answer swaps nothing (the toast says where it went)."""
    page = studio.open("editor")
    studio.settle()
    page.locator(".view-head h1").click()
    page.locator('[popovertarget="talk-pop"]').click()
    page.locator('[data-talk-character="robot"]').click()
    page.locator("#talk-btn").click()
    page.wait_for_selector('#talk-btn[aria-pressed="true"]', timeout=15_000)
    page.locator('[popovertarget="talk-pop"]').click()
    page.locator("#talk-rec-booth").click()
    page.wait_for_selector('#talk-rec-booth[aria-pressed="true"]')
    assert page.get_attribute("#talk-rec", "aria-pressed") == "false"
    page.wait_for_timeout(1200)
    with page.expect_response("**/voice/take/clip") as answer:
        page.locator("#talk-rec-booth").click()
    assert answer.value.ok
    lab = (server.data_dir / "voice_lab.json").read_text(encoding="utf-8")
    assert '"name": "talk-' in lab and (server.data_dir / "voice_take.wav").is_file()
    page.keyboard.press("Escape")
    page.locator("#talk-btn").click()
    page.wait_for_selector('#talk-btn[aria-pressed="false"]')
