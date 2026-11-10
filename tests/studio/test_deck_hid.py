"""The Stream Deck + driver and MIDI learn, under node, against a fake
device.

No Stream Deck was attached when this was written. What is proved here is
that deckhid.js reads and writes the bytes the device is documented to
speak (two open-source readings of the protocol, not a capture),
and that nothing a device can send throws out of it. The journeys in a
real browser are tests/browser/test_deck.py.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synthlab.studio.server import create_app

STATIC = Path("synthlab/studio/static")

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the islands' own code"
)

# The fake HIDDevice records every report it is sent and replays input
# reports to the driver's listener, as WebHID would: `data` is a DataView
# of the bytes AFTER the report id.
HARNESS = """
globalThis.window = { synthlab: {} };
const fs = require("fs");
// eval, deliberately: the islands are classic scripts merging onto window.synthlab
for (const f of process.argv.slice(3)) eval(fs.readFileSync(f, "utf8"));
const S = window.synthlab, H = S.deckHid;
const view = (bytes) => new DataView(Uint8Array.from(bytes).buffer);
const job = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
(async () => {
  const out = {};
  if (job.parse) out.parse = job.parse.map((b) => H.parse(view(b)));
  const filled = (n, v) => new Uint8Array(n).fill(v);
  const heads = (packets, n) => packets.map((p) => [p.length, ...p.slice(0, n)]);
  if (job.key) out.key = heads(H.keyPackets(job.key[0], filled(job.key[1], 7)), 9);
  if (job.lcd) {
    const [x, y, w, h, n] = job.lcd;
    out.lcd = heads(H.lcdPackets(x, y, w, h, filled(n, 9)), 17);
  }
  if (job.replay) {
    const sent = [], events = [];
    let listener = null;
    const device = {
      opened: false, productName: "Fake Deck +",
      async open() { this.opened = true; },
      async close() { this.opened = false; },
      async sendReport(id, d) { sent.push(["out", id, d.length, d[0], d[1]]); },
      async sendFeatureReport(id, d) {
        sent.push(["feature", id, d.length, d[0], d[1]]);
      },
      addEventListener(type, fn) { listener = fn; },
      removeEventListener() { listener = null; },
    };
    const deck = await H.open(device, (e) => events.push(e));
    for (const [id, bytes] of job.replay) {
      listener({ reportId: id, data: bytes === null ? null : view(bytes) });
    }
    await deck.setKey(3, new Uint8Array(2500).fill(1));
    await deck.setStrip(200, 0, 200, 100, new Uint8Array(10).fill(1));
    await deck.setKey(99, new Uint8Array(10));
    await deck.close();
    out.replay = { events, sent, opened: device.opened, listening: !!listener };
  }
  if (job.pump) {
    // a slow link (2 ms a report): what waits for it is coalesced and ordered
    const sent = [];
    const device = {
      opened: true, productName: "Slow Deck +",
      async open() {}, async close() {}, async sendFeatureReport() {},
      addEventListener() {}, removeEventListener() {},
      async sendReport(id, d) {
        await new Promise((r) => setTimeout(r, 2));
        // [type, key or x, the strip's width, the image's first byte]
        const strip = d[0] === 0x0c, at = strip ? d[1] + 256 * d[2] : d[1];
        sent.push([d[0], at, strip ? d[5] + 256 * d[6] : 0, d[strip ? 15 : 7]]);
      },
    };
    const deck = await H.open(device, () => {});
    const img = (n, fill) => new Uint8Array(n).fill(fill);
    // a motion frame of two columns, twenty newer images of key 1, then the
    // strip zoomed (one region over both columns), then a dial's change (urgent)
    deck.setStrip(0, 0, 200, 100, img(1500, 1));
    deck.setStrip(200, 0, 200, 100, img(1500, 2));
    for (let n = 1; n <= 20; n++) deck.setKey(1, img(10, 100 + n));
    deck.setStrip(0, 0, 800, 100, img(10, 3));
    const backlog = deck.backlog();
    await deck.setKey(5, img(10, 9), true);
    const first = sent.length;
    // an image on the wire is never cut by an urgent one: it goes next
    const going = deck.setStrip(0, 0, 200, 100, img(3000, 4));
    await new Promise((r) => setTimeout(r, 3));
    deck.setKey(2, img(10, 8), true);
    await going;
    await deck.close();
    out.pump = { sent, backlog, first };
  }
  if (job.ticks) out.ticks = job.ticks.map(([mode, v]) => S.midiLearn.ticks(mode, v));
  if (job.feed) {
    const calls = [];
    S.deck = {
      turn: (...a) => calls.push(["turn", ...a]),
      setEl: (el, n) => calls.push(["setEl", el, Math.round(n * 1000) / 1000]),
      slots: () => ["s0", "s1", "s2", "s3"],
    };
    S.toast = (m) => calls.push(["toast"]);
    if (job.find) S.deck.find = (to) => (to === job.find ? "ctl" : null);
    S.deck.nudge = (el, t) => calls.push(["nudge", el, t]);
    S.deck.refresh = () => {};
    const L = S.midiLearn;
    out.feed = job.feed.map(([op, ...a]) => {
      if (op === "feed") return L.feed(...a);
      L[op](...a);
      return null;
    });
    out.calls = calls;
    out.map = S.midiLearn.list();
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def run(tmp_path, **job):
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    spec = tmp_path / "job.json"
    spec.write_text(json.dumps(job), encoding="utf-8")
    files = [str(STATIC / "deckhid.js"), str(STATIC / "midilearn.js")]
    out = subprocess.run(
        ["node", str(harness), str(spec), *files],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(out.stdout)


def report(kind: int, sub: int, *rest: int) -> list[int]:
    """An input report after its id: type, a 2-byte length, the subtype."""
    return [kind, 0, 0, sub, *rest]


@node
def test_input_reports_are_read_as_the_protocol_says(tmp_path):
    got = run(
        tmp_path,
        parse=[
            [0x00, 8, 0, 0, 1, 0, 0, 0, 0, 0, 1],  # keys 2 and 8 down
            report(0x03, 0x01, 1, 0xFF, 0, 0xFB),  # dials: +1, -1, 0, -5
            report(0x03, 0x00, 0, 0, 1, 0),  # dial 3 pressed
            report(0x02, 0x01, 0, 0x2C, 0x01, 50, 0),  # tap at 300, 50
            report(0x02, 0x02, 0, 0x90, 0x01, 10, 0),  # long press at 400, 10
            report(
                0x02, 0x03, 0, 0x58, 0x02, 40, 0, 0x64, 0x00, 45, 0
            ),  # swipe 600 -> 100
        ],
    )["parse"]
    assert got[0] == {
        "type": "keys",
        "down": [False, True, False, False, False, False, False, True],
    }
    assert got[1] == {"type": "turns", "ticks": [1, -1, 0, -5]}
    assert got[2] == {"type": "press", "down": [False, False, True, False]}
    assert got[3] == {"type": "tap", "x": 300, "y": 50}
    assert got[4] == {"type": "long", "x": 400, "y": 10}
    assert got[5] == {"type": "swipe", "x": 600, "y": 40, "x2": 100, "y2": 45}


@node
def test_a_malformed_report_is_ignored_never_thrown(tmp_path):
    """Device data: short, unknown, out of range. Each parses to nothing
    (or to a clamped value), and none throws."""
    got = run(
        tmp_path,
        parse=[
            [],
            [0x00],
            [0x00, 0, 0, 1, 1],  # keys, cut short
            report(0x03, 0x01, 1),  # a turn with one dial's byte
            report(0x03, 0x07, 1, 1, 1, 1),  # an unknown dial subtype
            report(0x02, 0x01, 0, 1),  # a tap with no coordinates
            report(0x02, 0x03, 0, 1, 0, 1, 0),  # a swipe with no end
            report(0x02, 0x09, 0, 1, 0, 1, 0),  # an unknown touch
            report(0x04, 0x00, 1, 2, 3, 4),  # NFC and anything else
            report(0x02, 0x01, 0, 0xFF, 0xFF, 0xFF, 0xFF),  # off the strip
            report(0x03, 0x01, 0x80, 0, 0, 0),  # -128 ticks
        ],
    )["parse"]
    assert got[:9] == [None] * 9
    assert got[9] == {"type": "tap", "x": 799, "y": 99}
    assert got[10] == {"type": "turns", "ticks": [-127, 0, 0, 0]}


@node
def test_a_key_image_is_paged_into_1023_byte_reports(tmp_path):
    """2500 bytes = 1016 + 1016 + 468: header 07, key, last, len, page."""
    got = run(tmp_path, key=[5, 2500])["key"]
    assert [p[0] for p in got] == [1023] * 3
    assert got[0][1:8] == [0x07, 5, 0, 1016 & 0xFF, 1016 >> 8, 0, 0]
    assert got[1][1:8] == [0x07, 5, 0, 1016 & 0xFF, 1016 >> 8, 1, 0]
    assert got[2][1:8] == [0x07, 5, 1, 468 & 0xFF, 468 >> 8, 2, 0]
    assert got[0][8:] == [7, 7]  # the image starts right after the header
    # a key that does not exist, an empty image: nothing is sent
    assert run(tmp_path, key=[8, 100])["key"] == []
    assert run(tmp_path, key=[-1, 100])["key"] == []
    assert run(tmp_path, key=[0, 0])["key"] == []


@node
def test_a_strip_region_is_paged_with_its_rectangle(tmp_path):
    """1500 bytes at x=200, 200x100 = 1008 + 492: header 0c, x, y, w, h,
    last, page, len, 00."""
    got = run(tmp_path, lcd=[200, 0, 200, 100, 1500])["lcd"]
    assert [p[0] for p in got] == [1023] * 2
    rect = [200, 0, 0, 0, 200, 0, 100, 0]
    assert got[0][1:16] == [0x0C, *rect, 0, 0, 0, 1008 & 0xFF, 1008 >> 8, 0]
    assert got[1][1:16] == [0x0C, *rect, 1, 1, 0, 492 & 0xFF, 492 >> 8, 0]
    assert got[0][16:] == [9, 9]
    # a region that leaves the 800x100 strip is refused, not clipped
    for bad in ([700, 0, 200, 100], [0, 50, 200, 100], [-1, 0, 10, 10], [0, 0, 0, 10]):
        assert run(tmp_path, lcd=[*bad, 100])["lcd"] == [], bad


@node
def test_a_fake_device_replays_reports_and_captures_what_is_sent(tmp_path):
    got = run(
        tmp_path,
        replay=[
            [1, [0x00, 8, 0, 1, 0, 0, 0, 0, 0, 0, 0]],  # key 1 down
            [1, [0x00, 8, 0, 1, 0, 0, 0, 0, 0, 0, 0]],  # the same state again
            [1, [0x00, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0]],  # and up
            [1, report(0x03, 0x00, 0, 1, 0, 0)],  # dial 2 down
            [1, report(0x03, 0x01, 0, 3, 0, 0xFE)],  # dial 2 +3, dial 4 -2
            [1, report(0x03, 0x00, 0, 0, 0, 0)],  # dial 2 up
            [1, report(0x02, 0x01, 0, 0x2C, 0x01, 50, 0)],  # a tap
            [7, report(0x03, 0x01, 9, 9, 9, 9)],  # another report id: not ours
            [1, [0x03]],  # truncated
            [1, None],  # no data at all
        ],
    )["replay"]
    assert got["events"] == [
        {"type": "keydown", "index": 0},
        {"type": "keyup", "index": 0},
        {"type": "dialdown", "index": 1},
        {"type": "turn", "index": 1, "ticks": 3},
        {"type": "turn", "index": 3, "ticks": -2},
        {"type": "dialup", "index": 1},
        {"type": "tap", "x": 300, "y": 50},
    ]
    # brightness on open; a 2500-byte key in three reports; a strip region
    # in one; key 99 in none; "back to the logo" on close — in that order
    assert got["sent"] == [
        ["feature", 3, 31, 0x08, 70],
        ["out", 2, 1023, 0x07, 3],
        ["out", 2, 1023, 0x07, 3],
        ["out", 2, 1023, 0x07, 3],
        ["out", 2, 1023, 0x0C, 200],
        ["feature", 3, 31, 0x02, 0],
    ]
    assert got["opened"] is False and got["listening"] is False


@node
def test_the_link_queue_coalesces_and_puts_a_change_first(tmp_path):
    """deckhid.js's writer is the device's queue: an image that
    waits for the link is replaced by a newer one of the same key, or of a
    strip region it overlaps; an urgent image (its content changed) goes
    before the waiting motion; an image already on the wire finishes first,
    its reports never interleaved. So the queue never holds more than one
    image a key and a region, however fast the page draws."""
    got = run(tmp_path, pump=True)["pump"]
    # twenty-three motion images queued: two are left (key 1's newest and
    # the zoomed strip; the two columns it covers are dropped)
    assert got["backlog"] == 2
    # the urgent key first, then the rest in the order they were queued
    assert got["sent"][: got["first"]] == [
        [0x07, 5, 0, 9],
        [0x07, 1, 0, 120],
        [0x0C, 0, 800, 3],
    ]
    # the 3000-byte column went out whole (three reports), then the urgent key
    assert got["sent"][got["first"] :] == [[0x0C, 0, 200, 4]] * 3 + [[0x07, 2, 0, 8]]


@node
def test_relative_encoder_encodings(tmp_path):
    got = run(
        tmp_path,
        ticks=[
            ["rel2c", 1],
            ["rel2c", 127],
            ["rel2c", 3],
            ["rel2c", 125],
            ["relbin", 65],
            ["relbin", 63],
            ["relbin", 64],
            ["relsign", 1],
            ["relsign", 65],
            ["relsign", 67],
            ["abs", 100],
        ],
    )["ticks"]
    assert got == [1, -1, 3, -3, 1, -1, 0, 1, -1, -3, 0]


@node
def test_midi_learn_binds_refuses_reserved_ccs_and_moves_a_rebound_cc(tmp_path):
    got = run(
        tmp_path,
        feed=[
            ["feed", 0xB0, 21, 64],  # nothing bound: not consumed
            ["arm", "slot:1", "dial 2"],
            ["feed", 0x90, 60, 100],  # a note while armed: not a CC
            ["feed", 0xB0, 1, 10],  # the mod wheel: refused, still armed
            ["feed", 0xB0, 64, 127],  # sustain: refused
            ["feed", 0xB0, 123, 0],  # all notes off: refused
            ["feed", 0xB2, 21, 5],  # binds CC 21 on channel 3
            ["feed", 0xB2, 21, 127],  # absolute: dial 2's control to the top
            ["feed", 0xB0, 21, 127],  # another channel: not this binding
            ["cycle", 0],  # -> rel2c
            ["feed", 0xB2, 21, 127],  # one tick down
            ["feed", 0xB2, 21, 200],  # not a data byte
            ["arm", "slot:3", "dial 4"],
            ["feed", 0xB2, 21, 0],  # the same CC learned again: it moves
            ["arm", "id:<script>", "x"],  # not a target: never armed
            ["feed", 0xB5, 30, 1],
            ["feed", 0xB0, 1, 99],  # CC 1 is midi.js's whatever the map says
        ],
    )
    assert got["feed"] == [
        False,
        None,
        False,
        False,
        False,
        False,
        True,
        True,
        False,
        None,
        True,
        False,
        None,
        True,
        None,
        False,
        False,
    ]
    assert got["map"] == [
        {"ch": 2, "cc": 21, "to": "slot:3", "mode": "abs", "name": "dial 4"}
    ]
    assert got["calls"] == [
        ["toast"],
        ["toast"],
        ["toast"],
        ["setEl", "s1", 1],
        ["turn", 1, -1, False],
    ]


@node
def test_a_cc_bound_to_a_control_goes_through_the_deck_and_can_be_forgotten(tmp_path):
    """A control's own binding ("Learn MIDI" in its menu) resolves through
    `deck.find` and is written by the deck's `setEl` / `nudge` — the same
    two calls a slot's CC makes, and so the same `synthlab.tweak`."""
    got = run(
        tmp_path,
        find="id:p-cutoff",
        feed=[
            ["arm", "id:p-cutoff", "Cutoff"],
            ["feed", 0xB0, 30, 64],  # binds
            ["feed", 0xB0, 30, 127],  # absolute: the top of the control's travel
            ["cycle", 0],  # -> rel2c
            ["feed", 0xB0, 30, 2],  # two ticks up
            ["arm", "name:bpm", "Tempo"],
            ["feed", 0xB0, 31, 1],  # a second control, not on this page
            ["feed", 0xB0, 31, 90],  # consumed, and nothing is written
            ["forget", "id:p-cutoff"],
            ["feed", 0xB0, 30, 5],  # forgotten: not consumed any more
        ],
    )
    assert got["feed"] == [None, True, True, None, True, None, True, True, None, False]
    assert got["calls"] == [["setEl", "ctl", 1], ["nudge", "ctl", 2]]
    assert got["map"] == [
        {"ch": 0, "cc": 31, "to": "name:bpm", "mode": "abs", "name": "Tempo"}
    ]


def test_the_hardware_islands_are_served_and_only_run_on_demand(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    for name in ("deck", "deckhid", "midilearn", "deckart", "deckui", "pen", "menu"):
        assert client.get(f"/static/{name}.js").status_code == 200, name
    hid = (STATIC / "deckhid.js").read_text(encoding="utf-8")
    ui = (STATIC / "deckui.js").read_text(encoding="utf-8")
    art = (STATIC / "deckart.js").read_text(encoding="utf-8")
    deck = (STATIC / "deck.js").read_text(encoding="utf-8")
    learn = (STATIC / "midilearn.js").read_text(encoding="utf-8")
    menu = (STATIC / "menu.js").read_text(encoding="utf-8")
    # the chooser needs a click: requestDevice is reached from Connect only
    assert hid.count("requestDevice(") == 1 and ui.count("request()") == 1
    assert '"deck-connect": () => {' in ui and "DO[t.id]?.()" in ui
    # a report handler and the surface's entry point swallow what they throw
    assert "catch { /* a report never throws out of its handler */ }" in hid
    assert "catch { /* device data never breaks the page */ }" in deck
    # a dial writes through the control, as a hand does: no post of its own
    for js in (deck, ui, art, learn, hid, menu):
        assert "htmx.ajax(" not in js and "fetch(" not in js
        assert "innerHTML" not in js and "eval(" not in js
        assert js.count("window.synthlab =") == 0
    # ... and through ONE hook, tweak.js's: no transport sets a control's
    # value or dispatches its events itself (the virtual knob is the one
    # `.value =` in the four files: it mirrors its slot's control)
    for js in (deck, learn, menu, ui):
        code = "\n".join(
            ln for ln in js.splitlines() if not ln.strip().startswith("//")
        )
        assert "dispatchEvent(new Event(" not in code.replace(
            'window.dispatchEvent(new Event("synthlab-geometry"))', ""
        )
        assert "selectedIndex =" not in code and "checked =" not in code
    assert deck.count(".value = ") == 0 and ui.count(".value = ") == 1
    # a radio group with an id is a value control as a select is (the pills
    # rows, the wavetable picker): stepped by tweak.js, the one writer, and
    # the same selector in each island that finds a control
    sets = (STATIC / "decksets.js").read_text(encoding="utf-8")
    assert '"input[type=range], select, [role=radiogroup][id]"' in deck
    assert '"input[type=range], select, [role=radiogroup][id]"' in sets
    assert """'input[type="range"], select, [role="radiogroup"][id]'""" in menu
    assert "S.tweak.steps(el)" in deck and "S.tweak.chosen(" in deck
    assert "S.tweak(el, value)" in deck and "S.tweak.later(el)" in deck
    assert "S.tweak.reset(el)" in deck and "S.tweak.reset(now)" in menu
    # the keys are bound to command names, resolved through the palette's
    # harvest — every one: the loop's undo was found by selector until its
    # button declared a command, and no fallback is left
    assert "S.commands?.(true)" in deck
    assert "{ q: " not in deck and "/fragment/" not in deck and "hx-post" not in deck
    assert '"Undo the last pattern edit"' in deck
    assert "nav.views" not in deck and '"#play"' not in deck
    # the panel carries no named field (htmx.ajax posts every one on the page)
    # — the one input in it is a dial's knob, which has none
    assert 'el("select"' not in ui and "name=" not in ui
    assert ui.count('el("input"') == 1 and '"data-dial": i' in ui
    # midi.js hands every message to the map first, and asks for nothing new
    midi = (STATIC / "midi.js").read_text(encoding="utf-8")
    assert "window.synthlab.midiLearn?.feed(status, d1, d2)" in midi
    assert "requestMIDIAccess" not in learn
    # preferences are the browser's: no route, nothing in the session
    html = client.get("/").text
    order = [
        html.index(f"/static/{n}.js")
        for n in ("midi", "deck", "deckhid", "midilearn", "deckart", "deckui")
    ]
    assert order == sorted(order)
    # built in script, outside <body>: base.html is the shell's
    assert "deck-panel" not in html and 'id="ctx-menu"' not in html


def test_the_docked_deck_has_its_own_css_sections_built_on_the_components():
    """The virtual deck is laid out by style.css section 23 / dense.css D23
    (it used to be styled through the CSSOM, floating over the keys dock):
    docked under or beside the page, which makes room for it."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    dense = (STATIC / "dense.css").read_text(encoding="utf-8")
    ui = (STATIC / "deckui.js").read_text(encoding="utf-8")
    # section 23 alone (it was the file's last section once; later sections
    # are their own: test_design_system.py holds the file to the tokens)
    own = css[css.index("/* ---------- 23. Control surface") :]
    own = own[: own.index("/* ---------- 24.")]
    assert "/* ---------- D23. Control surface" in dense
    for rule in (
        "html[data-deck] #deck-panel { display: grid; }",
        "html[data-deck] body { padding-bottom: var(--deck-h); }",
        "html[data-deck] .keys-dock { bottom: var(--deck-h); }",
        'html[data-deck="side"] body { padding-bottom: 0;'
        " padding-right: var(--deck-w); }",
    ):
        assert rule in own, rule
    # against the tokens: no raw colour, no literal radius or font size
    body = re.sub(r"/\*.*?\*/", "", own, flags=re.S)
    # (a colour derived from a token, `oklch(from var(--x) ...)`, is not raw)
    assert not re.findall(r"oklch\((?!from var\()|#[0-9a-f]{3,6}\b|rgba?\(", body)
    # (`font-size: 0` hides a label's text: not a size)
    assert not re.findall(r"border-radius: \d+px|font-size: (?!0;)\d", body)
    # the canonical components, not a family of its own
    for cls in ("`btn btn-s ", '"knob knob-l"', '"meter"', '"led"', '"caps"'):
        assert cls in ui, cls
    assert '"panel-head"' in ui and "midi-btn" not in ui.replace('"midi-btn")', "")
    # no layout through the CSSOM any more: custom properties are all it writes
    # — a knob's arc, the meter, and the glass's lean and light (section 23
    # draws them)
    assert "Object.assign(panel.style" not in ui and ".style, {" not in ui
    assert re.findall(r'style\.setProperty\("(--[\w-]+)"', ui) == [
        "--lean-x",
        "--lean-y",
        "--glint-x",
        "--glint-y",
        "--fill",
        "--level",
    ]
    assert "#deck-panel[data-lit]::after" in own and "--lean-x" in own
    # the strip and the device are painted in the page's own tokens — by
    # deckart.js (the "Afterhours" look), which also reads the
    # role hues; its fallback palette is numbers, never a raw colour string
    art = (STATIC / "deckart.js").read_text(encoding="utf-8")
    for token in ("--bg-inset", "--text", "--text-dim", "--signal", "--accent"):
        assert f't("{token}")' in art, token
    for token in ("--ai", "--go", "--pause", "--danger"):
        assert f't("{token}")' in art, token
    assert "getComputedStyle(root)" in art and "A.inks()" in ui
    for js in (ui, art):
        assert not re.search(r'"#[0-9a-f]{3,6}"', js)
    # the device's glass in CSS is mixed from the tokens too
    assert (
        "--deck-glass: color-mix(in oklch, color-mix(in oklab, var(--bg-inset)" in own
    )
