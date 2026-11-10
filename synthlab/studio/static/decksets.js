// synthlab/studio/static/decksets.js
// Dial sets: a quick way to put the four most used dials on the deck — a
// click loads them at once. A row in the deck panel, under the dials (#deck-bind's head):
//   - SETS: named fours by control (deck.js's `to` names, "id:p-<field>"
//     for a bare field); a set shows on a page that holds at least three of
//     its four. Keys: the keyboard dock's own (strum, bend range, room,
//     volume), which no page's dials reach (deck.js `pageList` skips the dock).
//   - ★ Yours: the four controls you change most on this page, learned
//     from `change` events (a drag counts once; the deck's own turns not),
//     per page and per browser (localStorage `synthlab.deck.used`) — after
//     the ones you added from a section's menu ("Add to ★ Yours", menu.js:
//     `deckSets.add`, four a page at most, `synthlab.deck.yours`).
// A click pins all four dials at once (deck.js `set({pins})`: the device
// redraws on the next frame); the lit set again unpins them, back to the
// mode's own. ▶ (`Deck dials: next set`, the device's "Sets ▶" key) steps
// through the sets this page has. Nothing posts; no route, no state.
// Budget: <= 85 code lines (75 -> 85: "Add to ★ Yours", `add`).

(() => {
  "use strict";
  const S = window.synthlab, STORE = "synthlab.deck.used", KEPT = "synthlab.deck.yours", NONE = ["", "", "", ""];
  const SETS = [
    ["Filter", "cutoff resonance filter_env drive"],
    ["Amp env", "attack decay sustain release"],
    ["Filter env", "filter_attack filter_decay filter_sustain filter_release"],
    ["Oscs", "osc1_level osc2_level osc2_detune sub_level"],
    ["Motion", "mod_lfo_rate vibrato_rate vibrato_depth filter_lfo_depth"],
    ["Width", "unison_detune unison_spread glide noise_level"],
    ["Keys", "id:kbd-strum id:bend-range id:space id:volume"],
  ].map(([name, f]) => ({ name, to: f.split(" ").map((x) => (x.includes(":") ? x : `id:p-${x}`)) }));
  let used = {}, kept = {};
  try { used = JSON.parse(localStorage.getItem(STORE) || "{}") || {}; } catch { /* blocked */ }
  try { kept = JSON.parse(localStorage.getItem(KEPT) || "{}") || {}; } catch { /* blocked */ }
  const view = () => document.getElementById("view")?.dataset.view || "";

  // the ones you put there first (a section's menu: "Add to ★ Yours"), then
  // the ones this page's hand moves most — only those on the page, four
  function yours() {
    const D = S.deck, mine = (kept[view()] || []).filter((to) => D.find(to));
    const top = [...new Set([...mine, ...Object.entries(used[view()] || {}).sort((a, b) => b[1] - a[1]).map(([to]) => to)])].filter((to) => D.find(to)).slice(0, 4);
    return top.length === 4 || mine.length ? { name: "★ Yours", to: [...top, ...NONE].slice(0, 4), yours: true } : null;
  }
  // "Add to ★ Yours": these first in this page's set (four at most: the last added win)
  function add(to, name = "") {
    const D = S.deck, v = view(), now = [...new Set([...to, ...(kept[v] || [])])].filter((x) => D.find(x)).slice(0, 4);
    if (!v || !now.length) return;
    kept[v] = now;
    try { localStorage.setItem(KEPT, JSON.stringify(kept)); } catch { /* blocked */ }
    S.toast?.(`★ Yours${name ? ` ← ${name}` : ""}: ${now.map((x) => D.nameOf(D.find(x))).join(", ")} (the deck's Dial sets)`);
    later();
  }
  S.deckSets = { add, yours };
  const here = () => [yours(), ...SETS.filter((s) => s.to.filter((to) => S.deck.find(to)).length >= 3)].filter(Boolean);
  const same = (a, b) => a.every((x, i) => x === b[i]);
  const lit = (s) => same(S.deck.state().pins, s.to);

  function paint() {
    const bind = document.getElementById("deck-bind");
    if (!bind || !S.deck) return;
    let row = document.getElementById("deck-sets");
    if (!row) {
      row = Object.assign(document.createElement("div"), { id: "deck-sets" });
      row.setAttribute("role", "group"); row.setAttribute("aria-label", "dial sets");
      bind.prepend(row);
    }
    const sets = here(), sig = sets.map((s) => `${s.name}:${s.to}:${lit(s)}`).join("|");
    row.hidden = !sets.length;
    if (row.dataset.sig === sig) return; // nothing moved: the buttons (and the focus on one) stay
    row.dataset.sig = sig;
    row.replaceChildren(Object.assign(document.createElement("span"), { className: "caps", textContent: "Dial sets" }), ...sets.map((s, i) => {
      const b = Object.assign(document.createElement("button"), { type: "button", className: "btn btn-s", textContent: s.name });
      b.dataset.set = String(i);
      b.dataset.cmd = `Deck dials: ${s.yours ? "your top four" : `the ${s.name} set`}`;
      b.dataset.cmdGroup = "Stream Deck";
      b.title = `${s.to.map((to) => { const el = S.deck.find(to); return el ? S.deck.nameOf(el) : null; }).filter(Boolean).join(" · ")} — on the four dials (again: off)`;
      b.setAttribute("aria-pressed", String(lit(s)));
      return b;
    }), Object.assign(document.createElement("button"), { type: "button", id: "deck-set-next", className: "btn btn-s btn-icon", textContent: "▶", title: "the next dial set" }));
    const next = row.lastElementChild;
    next.dataset.cmd = "Deck dials: next set"; next.dataset.cmdGroup = "Stream Deck";
  }
  const later = () => setTimeout(paint, 0);
  function pick(s) { S.deck.set({ pins: s && !lit(s) ? [...s.to] : NONE, zoom: -1 }); later(); }

  document.addEventListener("click", (e) => {
    const b = e.target.closest?.("#deck-sets [data-set], #deck-set-next");
    if (!b) return later(); // a pin from a control's menu, the deck's own buttons: the lit set follows
    const sets = here();
    if (b.id !== "deck-set-next") return pick(sets[Number(b.dataset.set)]);
    const at = sets.findIndex(lit);
    pick(sets[(at + 1) % sets.length]);
  });
  // learn: a control the hand changed (a drag is one `change`), this page's count
  document.addEventListener("change", (e) => {
    const el = e.target.closest?.("input[type=range], select, [role=radiogroup][id]");
    const to = el && !el.closest("#deck-panel, .piano-wrap, #chord-bar") && S.deck?.toOf(el);
    if (!to || !view() || S.deck.ownChange(el)) return; // not the deck's own turns
    const counts = (used[view()] ||= {});
    counts[to] = (counts[to] || 0) + 1;
    // kept small: the thirty most used a page
    used[view()] = Object.fromEntries(Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 30));
    try { localStorage.setItem(STORE, JSON.stringify(used)); } catch { /* blocked */ }
    later();
  }, true);
  document.addEventListener("htmx:after:settle", later);
  document.addEventListener("DOMContentLoaded", later); // after deckui.js built the panel
})();
