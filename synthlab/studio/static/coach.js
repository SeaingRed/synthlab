// synthlab/studio/static/coach.js
// Perform's ✦ Coach (loopcoach.py, loopchat.py, routes/coach.py;
// agent.md, "The loop coach"). The panel is the server's — its body the
// region `coach-panel`, every action an htmx post that answers with
// nothing — so this island holds only what a page must:
// - Open / closed: `#coach-dock[hidden]` — the
//   lane inspector's other tab: open, it stands in the inspector's body in
//   the lane's place (style.css 42). The head's ✦ Coach, the dock's ✕ and
//   Alt+K (on the Loops page, not in a field) toggle it; the inspector's
//   tabs (`data-coach-toggle="open"` / `"close"`, `data-coach-tab`: their
//   aria-selected) open or close it; per browser (localStorage
//   `synthlab.coach`).
// - The proposal on the grids: `#coach-panel[data-diff]` names the cells
//   it adds and takes away on the note grid and the drum grid; after every
//   settle those cells get `.coach-add` (outlined) / `.coach-del` (struck).
//   The classes are only this island's: a morph of a grid drops them and
//   the next settle puts them back.
// - The message box: emptied once its post is answered.
// - 🎙 (`[data-coach-mic]`, shown only when the coach can talk and audio
//   to Gemini is on): held, the mic records (voice.js's capture); let go,
//   up to 10 s goes to /fragment/coach/talk as a 16 kHz mono WAV, its
//   answer swapped in like any htmx answer (the regions ride on it).
// Budget: <= 110 code lines.

(() => {
  "use strict";
  const S = (window.synthlab ??= {});
  if (typeof document === "undefined") return;
  const KEY = "synthlab.coach", TALK_S = 10, RATE = 16000;
  const dock = () => document.getElementById("coach-dock");
  let open = false;
  try { open = localStorage.getItem(KEY) === "open"; } catch { /* closed */ }

  // ---- open / closed -----------------------------------------------------------
  function lay() {
    const d = dock();
    if (!d) return;
    d.hidden = !open;
    for (const b of document.querySelectorAll("[data-coach-toggle]:not([data-coach-tab])")) b.setAttribute("aria-expanded", String(open));
    for (const b of document.querySelectorAll("[data-coach-tab]")) b.setAttribute("aria-selected", String((b.dataset.coachTab === "coach") === open));
    mark();
  }
  function toggle(to = !open) {
    const inside = dock()?.contains(document.activeElement);
    open = to;
    try { localStorage.setItem(KEY, open ? "open" : ""); } catch { /* this page still shows it */ }
    lay();
    if (open) document.querySelector('.folded [data-fold="perform-lane"]')?.click(); // its inspector, folded to its head, opens
    if (open) dock()?.querySelector('[name="coach_say"]')?.focus({ preventScroll: true });
    else if (inside) document.querySelector(".coach-open")?.focus({ preventScroll: true });
  }

  // ---- the proposal, drawn on the grids ----------------------------------------------
  function mark() {
    for (const el of document.querySelectorAll(".coach-add, .coach-del")) el.classList.remove("coach-add", "coach-del");
    const raw = document.getElementById("coach-panel")?.dataset.diff;
    if (!raw || !open) return;
    let d;
    try { d = JSON.parse(raw); } catch { return; }
    const put = (root, names, cls) => {
      for (const n of names || []) root?.querySelector(`input[name="${CSS.escape(n)}"]`)?.classList.add(cls);
    };
    const notes = document.querySelector("#transport .grid.roll"), drums = document.getElementById("perform-drum-grid");
    put(notes, d.notes?.add, "coach-add"); put(notes, d.notes?.del, "coach-del");
    put(drums, d.drums?.add, "coach-add"); put(drums, d.drums?.del, "coach-del");
  }
  document.addEventListener("htmx:after:settle", () => { if (dock()) lay(); });
  document.addEventListener("htmx:after:request", (e) => {
    const form = e.detail?.ctx?.sourceElement?.closest?.(".coach-say");
    const status = e.detail?.ctx?.response?.status || 0;
    if (form && status && status < 400) form.querySelector('[name="coach_say"]').value = "";
  });

  // ---- the clicks and the key -----------------------------------------------------------
  document.addEventListener("click", (e) => {
    const b = e.target instanceof Element && e.target.closest("[data-coach-toggle]");
    if (b) toggle({ open: true, close: false }[b.dataset.coachToggle] ?? !open);
  });
  window.addEventListener("keydown", (e) => {
    if (e.code !== "KeyK" || !e.altKey || e.ctrlKey || e.metaKey || e.repeat || !dock()) return;
    if (S.isTypingTarget?.(e.target) && !e.target.closest?.("#coach-dock")) return;
    e.preventDefault(); e.stopImmediatePropagation();
    toggle();
  }, true);

  // ---- 🎙 hold to talk ----------------------------------------------------------------
  let rec = null; // {mic, btn, timer} while the button is held
  async function start(btn) {
    if (rec) return;
    S.ensureCtx?.();
    rec = { btn };
    btn.setAttribute("aria-pressed", "true");
    let mic;
    try { mic = await S.voice.capture(S.getCtx()); } catch { rec = null; btn.setAttribute("aria-pressed", "false"); return S.toast?.("Microphone permission denied.", "error"); }
    if (!rec) return void mic.stop().catch(() => {}); // let go while the mic was being asked for
    rec.mic = mic;
    rec.timer = setTimeout(stop, TALK_S * 1000);
  }
  async function stop() {
    const r = rec;
    if (!r?.mic) { rec = null; return; }
    rec = null;
    clearTimeout(r.timer);
    r.btn.setAttribute("aria-pressed", "false");
    try {
      const buf = await r.mic.stop(), n = Math.ceil(Math.min(buf.duration, TALK_S) * RATE);
      if (buf.duration < 0.3) return S.toast?.("Hold 🎙 while you talk.", "error");
      const off = new OfflineAudioContext(1, n, RATE), src = off.createBufferSource();
      src.buffer = buf; src.connect(off.destination); src.start();
      const body = new FormData();
      body.append("file", S.voice.toWav(await off.startRendering(), 1, TALK_S), "talk.wav");
      dock()?.classList.add("htmx-request");
      await S.send("/fragment/coach/talk", body, { refused: "The coach did not hear that." });
    } catch (err) {
      S.toast?.(String(err?.message || err), "error");
    } finally { dock()?.classList.remove("htmx-request"); }
  }
  document.addEventListener("pointerdown", (e) => {
    const b = e.target instanceof Element && e.target.closest("[data-coach-mic]");
    if (b && e.button === 0) { e.preventDefault(); start(b); }
  });
  for (const ev of ["pointerup", "pointercancel"]) document.addEventListener(ev, () => { if (rec) stop(); });
  document.addEventListener("keydown", (e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat && e.target.closest?.("[data-coach-mic]")) { e.preventDefault(); start(e.target.closest("[data-coach-mic]")); } });
  document.addEventListener("keyup", (e) => { if ((e.key === " " || e.key === "Enter") && rec && e.target.closest?.("[data-coach-mic]")) stop(); });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", lay); else lay();
})();
