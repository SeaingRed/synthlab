"""audio.js's post-swap value sync, run under node from the island's own code.

htmx 4's morph (and an OOB swap onto a kept node) copies attributes only. A
control the user has touched is "dirty": its live value, checked state or
selection no longer follows its attribute. So when the server redrew a
control with the value attribute the page was first drawn with — Undo, a
panel's Defaults, Morph, a root change redrawing the bpm field — the thumb
stayed where the hand left it and the next edit posted that stale value
back. `window.synthlab.syncControls(nodes)` re-asserts each swapped
control's live state from its attributes after every settle, except
client-owned controls and the one being edited. These tests run that exact
function against minimal fake elements.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

AUDIO = Path("synthlab/studio/static/audio.js")

HARNESS = r"""
const fs = require("fs");
const listeners = {};
globalThis.document = {
  addEventListener(type, fn) { (listeners[type] ??= []).push(fn); },
  querySelector() { return null; },
  querySelectorAll() { return []; },
  getElementById() { return null; },
};
globalThis.window = { synthlab: {} };
globalThis.CSS = { escape: (s) => s };
// eval, deliberately (as test_song_schedule.py does): audio.js is our own
// classic-script IIFE, not a module, and the point is to run the file the
// browser runs. With stub listeners it only wires handlers and publishes.
eval(fs.readFileSync(process.argv[2], "utf8"));

// a fake control: attributes in `attrs`, live state separate, as in a browser
function el(tag, attrs, live = {}) {
  const e = {
    tagName: tag.toUpperCase(), nodeType: 1, attrs: { ...attrs }, dataset: {},
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    type: attrs.type || (tag === "select" ? "select-one" : "text"),
    min: attrs.min ?? "0", max: attrs.max ?? "100",
    value: live.value ?? attrs.value ?? "", checked: live.checked ?? ("checked" in attrs),
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    hasAttribute(k) { return k in this.attrs; },
    matches(sel) {
      if (sel === ":active") return !!live.active;
      if (sel === "input, select") return tag === "input" || tag === "select";
      return (live.clientOwned && sel.includes("#volume")) || false;
    },
    querySelectorAll() { return []; },
    contains(x) { return x === this; },
    parentElement: null, previousElementSibling: null,
  };
  if (tag === "select") {
    e.options = (attrs.options || []).map((o) => ({ hasAttribute: (k) => k === "selected" && o.selected }));
    e.selectedIndex = live.selectedIndex ?? 0;
    e.multiple = false;
  }
  return e;
}
const fire = (type, ev) => (listeners[type] || []).forEach((fn) => fn(ev));

const cases = {
  stale_range() {
    const r = el("input", { type: "range", value: "80" }, { value: "50" });
    const n = window.synthlab.syncControls([r]);
    return { n, value: r.value, fill: r.style.props["--fill"] };
  },
  stale_checkbox_radio_select() {
    const c = el("input", { type: "checkbox" }, { checked: true });
    const s = el("select", { options: [{}, { selected: true }, {}] }, { selectedIndex: 2 });
    const n = window.synthlab.syncControls([c, s]);
    return { n, checked: c.checked, index: s.selectedIndex };
  },
  in_sync_is_untouched() {
    const r = el("input", { type: "number", value: "124" });
    return { n: window.synthlab.syncControls([r]), value: r.value };
  },
  client_owned_is_skipped() {
    const v = el("input", { type: "range", value: "0.8" }, { value: "0.3", clientOwned: true });
    return { n: window.synthlab.syncControls([v]), value: v.value };
  },
  dragging_is_skipped() {
    const r = el("input", { type: "range", value: "80" }, { value: "50", active: true });
    return { n: window.synthlab.syncControls([r]), value: r.value };
  },
  just_edited_is_skipped_until_the_hand_moves_on() {
    const r = el("input", { type: "number", value: "124" }, { value: "13" });
    fire("input", { target: r, stopPropagation() {} });
    const first = window.synthlab.syncControls([r]);
    const typing = r.value;
    fire("pointerdown", { target: { closest: () => null } });  // clicked Undo
    const second = window.synthlab.syncControls([r]);
    return { first, typing, second, value: r.value };
  },
  springs_resync_even_right_after_its_own_edit() {
    const r = el("input", { type: "range", value: "0", "data-springs": "" }, { value: "0.6" });
    fire("input", { target: r, stopPropagation() {} });
    return { n: window.synthlab.syncControls([r]), value: r.value };
  },
};
process.stdout.write(JSON.stringify(cases[process.argv[3]]()));
"""  # noqa: E501 (JavaScript, not Python)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run audio.js's own code"
)


def _run(tmp_path, case):
    h = tmp_path / "harness.js"
    h.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(
        ["node", str(h), str(AUDIO.resolve()), case],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    return json.loads(out.stdout)


def test_a_stale_range_takes_its_attribute_and_refills(tmp_path):
    r = _run(tmp_path, "stale_range")
    assert r == {"n": 1, "value": "80", "fill": "80%"}


def test_checkbox_and_select_follow_their_attributes(tmp_path):
    r = _run(tmp_path, "stale_checkbox_radio_select")
    assert r == {"n": 2, "checked": False, "index": 1}


def test_a_control_already_in_sync_is_not_counted(tmp_path):
    assert _run(tmp_path, "in_sync_is_untouched") == {"n": 0, "value": "124"}


def test_client_owned_controls_keep_their_live_value(tmp_path):
    assert _run(tmp_path, "client_owned_is_skipped") == {"n": 0, "value": "0.3"}


def test_the_slider_under_the_pointer_is_left_alone(tmp_path):
    assert _run(tmp_path, "dragging_is_skipped") == {"n": 0, "value": "50"}


def test_the_field_being_typed_into_waits_until_the_hand_moves_on(tmp_path):
    r = _run(tmp_path, "just_edited_is_skipped_until_the_hand_moves_on")
    assert r == {"first": 0, "typing": "13", "second": 1, "value": "124"}


def test_the_morph_slider_springs_home_after_its_own_post(tmp_path):
    r = _run(tmp_path, "springs_resync_even_right_after_its_own_edit")
    assert r == {"n": 1, "value": "0"}
