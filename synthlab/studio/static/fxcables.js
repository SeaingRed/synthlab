// synthlab/studio/static/fxcables.js
// The FX racks' patch leads, drawn where the modules really are: real
// cables and plugs, joining whether the next box is below or beside. A rack (`.fx-units`: the in
// jack, a lead, a module, … a lead, the out jack — every rack draws it with
// the one macro, fragments/fx_units.html: Synths / Drums / Mix / Voice's,
// the Song drawer's audio-track rack and the vocal booth's) wraps onto new
// rows, and its
// inline leads (the templates' `lead()`, 22 px squiggles) dangled at a
// row's end. Here every rack with modules gets one overlay
// <svg class="fx-wires"> drawn from the boxes as laid out:
//   - each module has a socket on its left (in) and right (out) side at mid
//     height; the rack's in / out jacks are the chain's ends;
//   - beside: the lead sags between the two plugs, more the longer it is;
//   - on the next row: out of the right side, round and down into the gap
//     between the rows, along it, and round into the left side of the
//     module below — never across a module, as a lead laid on a desk;
//   - a lead is a dark rubber sleeve, the signal colour's core and a sheen
//     on top (style.css section 17.2), a soft shadow under it; each end a
//     plug — a barrel with a metal band round it and a collar — seated in
//     its socket; a lead that runs to the row below hangs in the gap by its
//     length, as one beside does.
// The inline leads keep their room (the gap a plug sits in) and stay the
// drawing without script; `[data-wired]` hides them. Redrawn on a settle,
// a resize of the rack (ResizeObserver) and the fonts loading: never per
// frame. An empty rack keeps its dashed, unplugged lead.
// Budget: <= 86 code lines (+6: wired before measuring, each module observed, a redraw when an animation round a rack ends).

(() => {
  "use strict";
  if (typeof document === "undefined") return;
  const NS = "http://www.w3.org/2000/svg", PLUG = 8, seen = new WeakSet();
  const mk = (tag, attrs, parent) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    parent.append(n);
    return n;
  };
  // a lead from a (heading right) to b (arriving from the left), between the plugs' tails
  function route(a, b, jack) { // jack: a hop to or from the rack's in / out jack, whose label is under it
    const x1 = a.x + PLUG, x2 = b.x - PLUG, beside = Math.abs(b.y - a.y) < 12 && x2 > x1;
    if (beside) {
      const dx = x2 - x1, sag = jack ? 3 : 5 + Math.min(18, dx * 0.3);
      return `M${x1} ${a.y}C${x1 + dx * 0.3} ${a.y + sag} ${x2 - dx * 0.3} ${b.y + sag} ${x2} ${b.y}`;
    }
    // round under the source into the row gap, along it (a little slack), round into the target
    // (the last turn stays inside the rack: its left padding is the room for it)
    const g = Math.max(a.bottom + 7, b.top - 9), k = 16, k2 = Math.max(4, Math.min(k, x2 - 2)), mid = (x1 + x2) / 2;
    return `M${x1} ${a.y}C${x1 + k} ${a.y} ${x1 + k} ${g} ${x1} ${g}`
      + `Q${mid} ${g + 2 + Math.min(8, Math.abs(x2 - x1) * 0.02)} ${x2} ${g}C${x2 - k2} ${g} ${x2 - k2} ${b.y} ${x2} ${b.y}`;
  }
  function plug(svg, p, dir) { // dir +1: the cable leaves to the right; -1: it arrives from the left
    const g = mk("g", { class: "plug" }, svg), x = dir > 0 ? p.x : p.x - PLUG;
    mk("rect", { class: "barrel", x, y: p.y - 3, width: PLUG, height: 6, rx: 1.5 }, g);
    mk("rect", { class: "band", x: x + PLUG / 2 - 0.75, y: p.y - 3, width: 1.5, height: 6 }, g);
    mk("rect", { class: "collar", x: dir > 0 ? p.x : p.x - 2, y: p.y - 4, width: 2, height: 8, rx: 0.8 }, g);
  }
  function draw(rack) {
    const mods = [...rack.querySelectorAll(":scope > .fx-unit")], io = rack.querySelectorAll(":scope > .fx-io > .jack");
    let svg = rack.querySelector(":scope > .fx-wires");
    if (!mods.length || io.length < 2 || !rack.offsetParent) { svg?.remove(); delete rack.dataset.wired; return; }
    // wired BEFORE measuring: `[data-wired]` lays the rack out (its gaps, the
    // inline leads' room) and a morph drops it (the server never sends it), so
    // measured first the plugs landed where the modules stood unwired, 28-49
    // px off, and the rack's box ended the same, so nothing redrew them
    rack.dataset.wired = "";
    const o = rack.getBoundingClientRect(), box = (el) => el.getBoundingClientRect();
    // a point on a side (or a jack's centre), with the box's top and bottom for a lead that changes row
    const at = (el, x) => { const b = box(el); return { x: x(b) - o.left, y: b.top + b.height / 2 - o.top, top: b.top - o.top, bottom: b.bottom - o.top }; };
    const side = (el, right) => at(el, (b) => (right ? b.right : b.left));
    const centre = (el) => at(el, (b) => b.left + b.width / 2);
    // the chain's hops: in jack -> module 1 -> … -> module n -> out jack
    const outs = [centre(io[0]), ...mods.map((m) => side(m, true))];
    const ins = [...mods.map((m) => side(m, false)), centre(io[io.length - 1])];
    if (!svg) { svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "fx-wires"); svg.setAttribute("aria-hidden", "true"); rack.prepend(svg); }
    svg.replaceChildren();
    // a socket on each side of each module: the hops' own points, the jacks left out
    for (const p of [...ins.slice(0, -1), ...outs.slice(1)]) mk("circle", { class: "socket", cx: p.x, cy: p.y, r: 3.5 }, svg);
    outs.forEach((a, i) => {
      const b = ins[i], d = route(a, b, i === 0 || i === outs.length - 1);
      for (const cls of ["sleeve", "core", "sheen"]) mk("path", { class: cls, d }, svg);
      plug(svg, a, 1);
      plug(svg, b, -1);
    });
  }
  const pending = new Set();
  let frame = 0;
  const soon = (rack) => { pending.add(rack); frame ||= requestAnimationFrame(() => { frame = 0; for (const r of pending) if (r.isConnected) draw(r); pending.clear(); }); };
  const ro = typeof ResizeObserver === "function"
    ? new ResizeObserver((es) => es.forEach((e) => soon(e.target.closest(".fx-units")))) : null;
  // a new rack is observed (its first resize draws it), and so is each of its
  // modules: a module that settles to another width moves the plugs while
  // the rack's own box stays put (after a knob of the live vocal chain
  // moves, its leads would otherwise be drawn from where the modules
  // stood mid-morph, 28-49 px inside them, and nothing would redraw them). One a
  // swap reached is drawn again (a morph drops the overlay the server never
  // sent); a resize of a rack or a module is the observer's — nothing else
  function scan(e) {
    const touched = e?.target?.contains ? e.target : null;
    for (const rack of document.querySelectorAll(".fx-units")) {
      for (const m of [rack, ...rack.querySelectorAll(":scope > .fx-unit")]) {
        if (!seen.has(m)) { seen.add(m); if (ro) ro.observe(m); else soon(rack); }
      }
      if (!touched || touched.contains(rack) || rack.contains(touched)) soon(rack);
    }
  }
  document.addEventListener("htmx:after:settle", scan);
  // a rack measured while its popover / panel animated or transitioned in (a
  // scale moves the plugs and no box resizes) is drawn again when it ends
  for (const t of ["animationend", "transitionend"]) document.addEventListener(t, (e) => e.target.querySelectorAll?.(".fx-units").forEach(soon));
  document.addEventListener("DOMContentLoaded", () => scan());
  document.fonts?.ready.then(() => scan());
})();
