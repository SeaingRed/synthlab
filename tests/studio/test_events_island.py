"""events.js, run under node from the island's own file.

The server half is `test_events.py`. Here: the page names itself on every
htmx request, ignores the notice of its own edit, re-fetches its view after
somebody else's (debounced, never mid-gesture, caught up afterwards), draws
a long render's progress as one line in #toasts and never one for a quick
render, lets go of the stream while hidden and catches up when shown.

The harness gives the island a fake clock, a fake EventSource and just
enough document; each test is a script of steps and reads back snapshots.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

EVENTS = Path("synthlab/studio/static/events.js")

HARNESS = r"""
const fs = require("fs");
let now = 1000, seq = 0;
const timers = [];
Date.now = () => now;
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, at: now + (ms || 0), id: ++seq }); return seq; };
globalThis.clearTimeout = (id) => { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); };
function advance(ms) {
  const end = now + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at || a.id - b.id);
    const t = timers[0];
    if (!t || t.at > end) break;
    timers.shift(); now = t.at; t.fn();
  }
  now = end;
}
const state = { view: "editor", ids: ["sound-id"], recording: false, typing: false };
const listeners = { doc: {}, win: {} }, calls = [], dispatched = [], sources = [];
const toasts = { children: [], append(el) { el.isConnected = true; this.children.push(el); } };
function element() {
  return {
    attrs: {}, className: "", textContent: "", isConnected: false,
    setAttribute(k, v) { this.attrs[k] = v; },
    remove() { this.isConnected = false; const i = toasts.children.indexOf(this); if (i >= 0) toasts.children.splice(i, 1); },
  };
}
const fire = (bag, type, e = {}) => { for (const fn of bag[type] || []) fn(e); };
globalThis.document = {
  hidden: false,
  get activeElement() { return state.typing ? { matches: () => true } : { matches: () => false }; },
  addEventListener(t, fn) { (listeners.doc[t] ??= []).push(fn); },
  dispatchEvent(e) { dispatched.push(e.type); fire(listeners.doc, e.type, e); },
  getElementById(id) {
    if (id === "toasts") return toasts;
    if (id === "view") return state.view ? { dataset: { view: state.view } } : null;
    return state.ids.includes(id) ? {} : null;
  },
  querySelector() { return state.recording ? {} : null; },
  createElement: element,
};
globalThis.window = { synthlab: {}, addEventListener(t, fn) { (listeners.win[t] ??= []).push(fn); } };
globalThis.location = { href: "http://127.0.0.1:8420/view/perform" };
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.readyState = 1; this.l = {}; sources.push(this); }
  addEventListener(t, fn) { this.l[t] = fn; }
  close() { this.readyState = 2; this.closed = true; }
};
globalThis.htmx = { ajax(method, url, opts) { calls.push([method, url, opts]); return Promise.resolve(); } };
const spec = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
if (spec.no_event_source) delete globalThis.EventSource;
// eval, deliberately: events.js is a classic script (an IIFE), not a module
eval(fs.readFileSync(process.argv[2], "utf8"));
const S = window.synthlab, out = [];
const snap = () => ({
  calls: calls.map((c) => [c[0], c[1], c[2]]),
  toasts: toasts.children.map((el) => [el.className, el.textContent]),
  sources: sources.map((s) => (s.closed ? "closed" : "open")),
  dispatched: [...dispatched],
});
(async () => {
  for (const [op, a, b] of spec.steps) {
    const es = sources[sources.length - 1];
    if (op === "emit") es.l[a]({ data: JSON.stringify(b === "$me" ? { origin: S.pageId, view: "editor", regions: ["sound-id"], whole: false, rev: "b:9" } : b) });
    else if (op === "raw") es.l[a]({ data: b });
    else if (op === "advance") advance(a);
    else if (op === "pointer") fire(listeners.win, a);
    else if (op === "request") fire(listeners.doc, a === "start" ? "htmx:before:request" : "htmx:finally:request");
    else if (op === "set") state[a] = b;
    else if (op === "hidden") { document.hidden = a; fire(listeners.doc, "visibilitychange"); }
    else if (op === "error") { es.readyState = a; es.onerror(); }
    else if (op === "headers") {
      const e = { detail: { ctx: { request: { headers: { "HX-Request": "true" } } } } };
      fire(listeners.doc, "htmx:config:request", e);
      out.push({ sent: e.detail.ctx.request.headers, own: S.pageHeaders(), id: S.pageId });
    }
    else if (op === "snap") { await null; await null; out.push(snap()); }
  }
  process.stdout.write(JSON.stringify(out));
})();
"""  # noqa: E501 (JavaScript, not Python)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run events.js's own code"
)

VIEW = ["GET", "/view/editor", {"target": "#view", "swap": "outerMorph"}]
THEIRS = {
    "origin": "another-tab",
    "view": "editor",
    "regions": ["sound-id"],
    "whole": False,
    "rev": "b:1",
}


def run(tmp_path, *steps, **spec) -> list[dict]:
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"steps": list(steps), **spec}), encoding="utf-8")
    out = subprocess.run(
        ["node", str(harness), str(EVENTS), str(path)],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )  # fmt: skip
    return json.loads(out.stdout)


def test_the_page_names_itself_on_every_htmx_request(tmp_path):
    (got,) = run(tmp_path, ["headers"])
    assert got["sent"] == {"HX-Request": "true", "X-Studio-Page": got["id"]}
    # the islands' fetch() posts: the page's URL too, as htmx sends it, so
    # the server re-sends the asking page's regions (`view_of`)
    assert got["own"] == {
        "X-Studio-Page": got["id"],
        "HX-Current-URL": "http://127.0.0.1:8420/view/perform",
    }
    assert len(got["id"]) >= 12


def test_its_own_echo_is_ignored_and_another_pages_edit_is_fetched(tmp_path):
    mine, theirs = run(
        tmp_path,
        ["emit", "hello", {"rev": "b:0"}],
        ["emit", "changed", "$me"],
        ["advance", 5000],
        ["snap"],
        ["emit", "changed", THEIRS],
        ["advance", 399],
        ["snap"],
        ["advance", 1],
        ["snap"],
    )[0::2]
    assert mine["calls"] == []  # this page made that edit: it already shows it
    assert theirs["calls"] == [VIEW]  # the view's own route, morphed in


def test_a_burst_of_changes_is_one_fetch_after_the_last(tmp_path):
    steps = []
    for _ in range(10):
        steps += [["emit", "changed", THEIRS], ["advance", 100]]
    before, after = run(tmp_path, *steps, ["snap"], ["advance", 400], ["snap"])
    assert before["calls"] == [] and after["calls"] == [VIEW]


def test_a_change_made_on_another_view_waits_longer(tmp_path):
    far = THEIRS | {"view": "drums", "regions": ["drum-bank"]}
    soon, later = run(
        tmp_path,
        ["emit", "changed", far],
        ["advance", 2000],
        ["snap"],
        ["advance", 500],
        ["snap"],
    )
    assert soon["calls"] == [] and later["calls"] == [VIEW]
    # ... unless it names a region this page draws too
    shared = THEIRS | {"view": "drums", "regions": ["sound-id"]}
    (got,) = run(tmp_path, ["emit", "changed", shared], ["advance", 400], ["snap"])
    assert got["calls"] == [VIEW]


@pytest.mark.parametrize(
    "hold, release",
    [
        (["pointer", "pointerdown"], ["pointer", "pointerup"]),
        (["pointer", "pointerdown"], ["pointer", "pointercancel"]),
        (["request", "start"], ["request", "end"]),
        (["set", "typing", True], ["set", "typing", False]),
        (["set", "recording", True], ["set", "recording", False]),
    ],
)
def test_a_gesture_in_hand_is_never_clobbered_and_is_caught_up(tmp_path, hold, release):
    during, after = run(
        tmp_path,
        hold,
        ["emit", "changed", THEIRS],
        ["advance", 5000],
        ["snap"],
        release,
        ["advance", 400],
        ["snap"],
    )
    assert during["calls"] == []  # a drag, a request in flight, typing, a take
    assert after["calls"] == [VIEW]


def test_the_song_player_rereads_its_plan_after_a_catch_up(tmp_path):
    (got,) = run(
        tmp_path,
        ["set", "view", "song"],
        ["emit", "changed", THEIRS | {"view": "song"}],
        ["advance", 400],
        ["snap"],
    )
    assert got["calls"][0][1] == "/view/song"
    assert got["dispatched"] == ["song-changed"]


def test_progress_is_one_determinate_line_and_then_goes(tmp_path):
    task = {"id": "t1", "label": "Exporting song", "done": 0, "total": 8}
    early, shown, moved, done, gone = run(
        tmp_path,
        ["emit", "progress", task],
        ["advance", 399],
        ["snap"],
        ["advance", 1],
        ["snap"],
        ["emit", "progress", task | {"done": 3}],
        ["snap"],
        ["emit", "done", task | {"done": 8}],
        ["snap"],
        ["advance", 2500],
        ["snap"],
    )
    assert early["toasts"] == []
    assert shown["toasts"] == [["toast progress", "Exporting song… 0/8"]]
    assert moved["toasts"] == [["toast progress", "Exporting song… 3/8"]]  # one line
    assert done["toasts"] == [["toast progress", "Exporting song — done"]]
    assert gone["toasts"] == []


def test_a_quick_render_never_shows_a_line(tmp_path):
    task = {"id": "t1", "label": "Rendering Verse", "done": 0, "total": 1}
    (got,) = run(
        tmp_path,
        ["emit", "progress", task],
        ["advance", 120],
        ["emit", "done", task | {"done": 1}],
        ["advance", 5000],
        ["snap"],
    )
    assert got["toasts"] == []


def test_a_failed_render_says_so_and_a_single_step_has_no_count(tmp_path):
    task = {"id": "t1", "label": "Rendering Verse", "done": 0, "total": 1}
    shown, failed = run(
        tmp_path,
        ["emit", "progress", task],
        ["advance", 400],
        ["snap"],
        ["emit", "failed", task],
        ["snap"],
    )
    assert shown["toasts"] == [["toast progress", "Rendering Verse…"]]
    assert failed["toasts"] == [["toast error", "Rendering Verse — failed"]]


def test_two_tasks_are_two_lines_and_a_lost_done_expires(tmp_path):
    a = {"id": "a", "label": "Exporting song", "done": 1, "total": 9}
    b = {"id": "b", "label": "Exporting stems", "done": 2, "total": 4}
    both, later = run(
        tmp_path,
        ["emit", "progress", a],
        ["emit", "progress", b],
        ["advance", 400],
        ["snap"],
        ["advance", 60000],
        ["snap"],
    )
    assert [t[1] for t in both["toasts"]] == [
        "Exporting song… 1/9",
        "Exporting stems… 2/4",
    ]
    assert later["toasts"] == []


def test_a_hidden_tab_lets_the_stream_go_and_catches_up_when_shown(tmp_path):
    hidden, back, caught = run(
        tmp_path,
        ["emit", "hello", {"rev": "b:4"}],
        ["hidden", True],
        ["snap"],
        ["hidden", False],
        ["emit", "hello", {"rev": "b:6"}],  # two changes went by
        ["snap"],
        ["advance", 400],
        ["snap"],
    )
    assert hidden["sources"] == ["closed"]
    assert back["sources"] == ["closed", "open"] and back["calls"] == []
    assert caught["calls"] == [VIEW]


def test_a_reconnect_that_missed_nothing_fetches_nothing(tmp_path):
    (got,) = run(
        tmp_path,
        ["emit", "hello", {"rev": "b:4"}],
        ["hidden", True],
        ["hidden", False],
        ["emit", "hello", {"rev": "b:4"}],
        ["advance", 5000],
        ["snap"],
    )
    assert got["calls"] == []


def test_a_resync_clears_the_lines_and_fetches(tmp_path):
    task = {"id": "t1", "label": "Exporting song", "done": 0, "total": 8}
    (got,) = run(
        tmp_path,
        ["emit", "progress", task],
        ["advance", 400],
        ["emit", "resync", {"rev": "b:9"}],
        ["advance", 400],
        ["snap"],
    )
    assert got["toasts"] == [] and got["calls"] == [VIEW]


def test_a_refused_stream_is_quiet_and_tried_again_slowly(tmp_path):
    """CONNECTING (0) is the browser's own retry; CLOSED (2) — a 503 at the
    cap — is retried here with a backoff. Neither toasts anything."""
    retrying, closed, again, much_later = run(
        tmp_path,
        ["error", 0],
        ["advance", 60000],
        ["snap"],
        ["error", 2],
        ["advance", 1999],
        ["snap"],
        ["advance", 1],
        ["snap"],
        ["error", 2],
        ["advance", 3999],
        ["snap"],
    )
    assert retrying["sources"] == ["open"] and retrying["toasts"] == []
    assert closed["sources"] == ["closed"]
    assert again["sources"] == ["closed", "open"]
    assert much_later["sources"] == ["closed", "closed"]  # 4 s the second time
    assert much_later["toasts"] == [] and much_later["calls"] == []


def test_without_event_source_the_page_is_what_it_was(tmp_path):
    (ids, got) = run(
        tmp_path, ["headers"], ["advance", 60000], ["snap"], no_event_source=True
    )
    assert ids["sent"]["X-Studio-Page"] == ids["id"]
    assert got == {"calls": [], "toasts": [], "sources": [], "dispatched": []}


def test_a_malformed_event_is_dropped(tmp_path):
    (got,) = run(tmp_path, ["raw", "changed", "{not json"], ["advance", 5000], ["snap"])
    assert got["calls"] == [] and got["toasts"] == []
