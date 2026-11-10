"""Real-browser smoke suite: headless Chromium against a scratch studio.

    uv run --group browser playwright install chromium     # once
    uv run --group browser pytest tests/browser -q

Every test here is marked `browser` and is SKIPPED (never an error) when
Playwright or its Chromium is not installed — the `browser` dependency
group is not part of `uv sync --all-extras`, so the default
`uv run pytest -q` collects these and skips them in milliseconds.

The studio: `create_app(<pytest tmp dir>)` served by uvicorn in a thread of
this process, on the first free port from 8430 up (never 8420, never the
real studio_data/). One studio per
test, so no journey sees another's session; shutdown runs the app's
lifespan (the debounced save is flushed) and joins the thread.

The browser: one Chromium for the session, a fresh context per test,
launched with `--autoplay-policy=no-user-gesture-required` so the page's
AudioContext runs without a trusted gesture, and with Chromium's fake
microphone (`--use-fake-device-for-media-stream`: a device that beeps;
`--use-fake-ui-for-media-stream`: no permission prompt) so a journey that
records from the mic has one. Every page records console
errors, uncaught page errors, CSP violations (the `securitypolicyviolation`
event) and same-origin HTTP 5xx; the `page` fixture FAILS the test on any
of them at teardown. `ALLOWED` is the allow-list: each entry says why.

What the audio probes prove (nobody *listens* here):
- `Studio.rms()` reads audio.js's own AnalyserNode — the one the scope
  draws from, on the master bus after the key bus — so a non-zero level
  means samples reached the page's output stage, not that they sound right.
- `Studio.meter()` is #cpu-meter's text, written only from the worklet
  nodes' own `load` reports: "N voices · wasm" proves the voice worklet is
  running, holds N voices and instantiated the WebAssembly module.
- `Studio.live_ready()` is livefx.js's `live.ready()` plus the context
  state: the modules loaded and every live sampler buffer was posted.
"""

from __future__ import annotations

import io
import re
import socket
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest

BROWSER_DIR = Path(__file__).parent

# Console/page messages that are known and explained. Empty on purpose:
# add a pattern only with the reason it is not a bug.
ALLOWED: tuple[tuple[str, str], ...] = ()


def pytest_collection_modifyitems(items):
    for item in items:
        if BROWSER_DIR in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.browser)


def _free_port(start: int = 8430) -> int:
    for port in range(start, start + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("no free port in 8430-8629")


class Server:
    """A studio on its own port and data dir, served from a thread."""

    def __init__(self, data_dir: Path):
        import uvicorn

        from synthlab.studio.server import create_app

        self.data_dir = data_dir
        self.app = create_app(data_dir)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                self.app, host="127.0.0.1", port=self.port, log_level="warning"
            )
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> Server:
        self._thread.start()
        deadline = time.monotonic() + 20
        while not self._server.started:
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the scratch studio did not start")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        # a page left open holds a `/events` stream, and uvicorn waits for
        # open connections before it shuts the app down: end the streams
        # first, as `server.make_server`'s exit handler does
        self.app.state.svc.events.close()
        self._server.should_exit = True
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            # a request still in flight: a render the page asked for that
            # the test no longer waits on (a cold song plan under the full
            # run's load). uvicorn waits for its connection to close —
            # asyncio's `Server.wait_closed`, which `force_exit` does not
            # skip — so the studio outlived the test by the render's whole
            # length: the full run's "did not stop". Drop those
            # connections; the render ends on its worker thread and its
            # answer goes nowhere.
            self._server.force_exit = True
            for conn in list(self._server.server_state.connections):
                conn.loop.call_soon_threadsafe(conn.transport.abort)
            self._thread.join(timeout=10)
        if self._thread.is_alive():  # say where every thread stands
            import sys
            import traceback

            stacks = "\n".join(
                f"--- thread {ident}\n" + "".join(traceback.format_stack(frame)[-12:])
                for ident, frame in sys._current_frames().items()
            )
            raise AssertionError(f"the scratch studio did not stop\n{stacks}")

    @property
    def state(self):
        return self.app.state.studio


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path / "studio_data").start()
    yield srv
    srv.stop()


_unavailable: str | None = None  # why there is no browser, once known


@pytest.fixture(scope="package")
def chromium():
    """Headless Chromium, or a skip when Playwright / the binary is missing.
    A fixture that skips is set up again for the next test: the reason is
    kept, so the other tests skip without trying a launch each.

    Package-scoped, not session-scoped: Playwright's sync API keeps an
    event loop running in this thread until `pw.stop()`, and every later
    test that calls `asyncio.run` (the event stream's, the body cap's)
    failed with "cannot be called from a running event loop" when the
    browser tests shared a run with the rest of the suite."""
    global _unavailable
    if _unavailable:
        pytest.skip(_unavailable)
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        _unavailable = "playwright is not installed (uv run --group browser ...)"
        pytest.skip(_unavailable)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(
            args=[
                "--autoplay-policy=no-user-gesture-required",
                # a fake microphone (a tone), allowed without asking: the
                # live talkbox's and the sampler's record journeys open it
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ]
        )
    except Error as e:
        pw.stop()
        first = str(e).strip().splitlines()[0]
        _unavailable = (
            f"Chromium is not installed (playwright install chromium): {first}"
        )
        pytest.skip(_unavailable)
    yield browser
    browser.close()
    pw.stop()


# Runs before any page script: CSP violations are collected where the test
# can read them (they also reach the console; this names the directive).
_INIT = """
window.__csp = [];
document.addEventListener("securitypolicyviolation", (e) => {
  const at = `${e.sourceFile}:${e.lineNumber}`;
  window.__csp.push(`${e.violatedDirective} blocked ${e.blockedURI} (${at})`);
});
"""


class Studio:
    """A page on the scratch studio, with the probes the journeys share."""

    def __init__(self, page, server: Server):
        self.page = page
        self.server = server
        self.url = server.url
        self.problems: list[str] = []
        page.on("console", self._console)
        page.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        page.on("response", self._response)

    def _console(self, msg) -> None:
        if msg.type == "error":
            where = msg.location.get("url", "")
            self.problems.append(f"console.error: {msg.text} [{where}]")

    def _response(self, resp) -> None:
        if resp.status >= 500 and resp.url.startswith(self.url):
            self.problems.append(f"HTTP {resp.status}: {resp.url}")

    def unexplained(self) -> list[str]:
        if not self.page.is_closed():
            try:
                for v in self.page.evaluate("window.__csp || []"):
                    self.problems.append(f"CSP: {v}")
            except Exception as e:  # a page mid-navigation: say so, don't hide it
                self.problems.append(f"could not read CSP violations: {e}")
        return [
            p for p in self.problems if not any(re.search(a, p) for a, _ in ALLOWED)
        ]

    # ---- navigation -------------------------------------------------------
    def open(self, view: str = "editor"):
        """A cold page load of one view, scripts run and htmx settled."""
        self.page.goto(f"{self.url}/view/{view}")
        self.page.wait_for_selector(f'#view[data-view="{view}"]')
        # always an arrow function: Playwright evals a bare expression in the
        # page, which the studio's CSP (no 'unsafe-eval') rightly refuses
        self.page.wait_for_function("() => !!window.synthlab?.piano && !!window.htmx")
        return self.page

    def nav(self, view: str) -> None:
        """Switch views the way a user does: the nav link's htmx swap."""
        self.page.click(f'nav.views a[href="/view/{view}"]')
        self.page.wait_for_selector(f'#view[data-view="{view}"]')
        # the page transition landed (pagemotion.js): a box measured while
        # the new page's modules arrive is a moving one
        self.page.wait_for_function("() => !window.synthlab.pageMotion?.busy()")

    # ---- audio probes (see the module docstring for what each proves) -----
    def ctx_state(self) -> str | None:
        return self.page.evaluate("window.synthlab.getCtx()?.state ?? null")

    def rms(self) -> float:
        """RMS of the master analyser's current time-domain window."""
        return self.page.evaluate(
            """() => {
              const an = window.synthlab.getAnalyser?.();
              if (!an) return 0;
              const b = new Float32Array(an.fftSize);
              an.getFloatTimeDomainData(b);
              let s = 0;
              for (const v of b) s += v * v;
              return Math.sqrt(s / b.length);
            }"""
        )

    def wait_sound(self, floor: float = 1e-3, timeout: float = 8.0) -> float:
        """The master analyser carries signal: returns the level seen."""
        deadline = time.monotonic() + timeout
        peak = 0.0
        while time.monotonic() < deadline:
            peak = max(peak, self.rms())
            if peak > floor:
                return peak
            self.page.wait_for_timeout(40)
        raise AssertionError(f"no signal on the master analyser (peak rms {peak:.2e})")

    def wait_silence(self, ceil: float = 1e-4, timeout: float = 12.0) -> None:
        """The master analyser falls quiet (a release has run out)."""
        deadline = time.monotonic() + timeout
        level = 1.0
        while time.monotonic() < deadline:
            level = self.rms()
            if level < ceil:
                return
            self.page.wait_for_timeout(80)
        raise AssertionError(f"still sounding: master rms {level:.2e}")

    def meter(self) -> str:
        return self.page.evaluate(
            "document.getElementById('cpu-meter')?.textContent ?? ''"
        )

    def wait_meter(self, pattern: str, timeout: float = 8.0) -> str:
        """#cpu-meter (the worklet nodes' own reports) matches `pattern`."""
        deadline = time.monotonic() + timeout
        text = ""
        while time.monotonic() < deadline:
            text = self.meter()
            if re.search(pattern, text):
                return text
            self.page.wait_for_timeout(60)
        raise AssertionError(f"cpu meter never matched {pattern!r}: {text!r}")

    def start_voice(self) -> None:
        """Bring the live voice up the way a first key does, and wait for
        it: the worklet modules, the tables and the wasm module load on the
        first press, and that press itself plays the WAV fallback."""
        self.page.evaluate(
            """() => { const p = window.synthlab.piano;
                 p.noteOn("warm", 60, 0.5); p.noteOff("warm"); }"""
        )
        self.wait_live()

    def wait_live(self, timeout: float = 10.0) -> str:
        """The live voice is up: a voice node exists and has reported (the
        meter's first text — `live.ready()` alone is vacuously true before
        the first node is made), and every sampler buffer has been posted.
        Returns the meter's text. Needs a view with #cpu-meter."""
        text = self.wait_meter(r"\d+ voices? · (wasm|js)", timeout)
        self.page.wait_for_function(
            """() => window.synthlab.getCtx()?.state === "running"
                 && window.synthlab.live.ready()
                 && !!window.synthlab.piano.patch()""",
            timeout=timeout * 1000,
        )
        return text

    def level(self, reads: int = 12) -> float:
        """The master level averaged over ~0.6 s (one analyser window is
        ~40 ms: a single read wobbles with the note's phase)."""
        total = 0.0
        for _ in range(reads):
            total += self.rms()
            self.page.wait_for_timeout(50)
        return total / reads

    def stereo(self, floor: float = 5e-3, timeout: float = 5.0) -> dict:
        """The key bus's two channels once it carries signal: RMS of left,
        right and of (left - right) over the same window, from a channel
        splitter + two analysers this probe hangs on keys.js's own bus node
        (`synthlab.keyBus()`, where the worklet voice and the WAV keys
        meet). `diff` ~ 0 means the two channels are the same signal."""
        read = """() => {
          const S = window.synthlab, ctx = S.getCtx();
          if (!window.__tap) {
            const split = ctx.createChannelSplitter(2);
            const l = ctx.createAnalyser(), r = ctx.createAnalyser();
            S.keyBus().connect(split); split.connect(l, 0); split.connect(r, 1);
            window.__tap = { l, r };
          }
          const { l, r } = window.__tap, n = l.fftSize;
          const a = new Float32Array(n), b = new Float32Array(n);
          l.getFloatTimeDomainData(a); r.getFloatTimeDomainData(b);
          let sa = 0, sb = 0, sd = 0;
          for (let i = 0; i < n; i++) {
            sa += a[i] * a[i]; sb += b[i] * b[i]; sd += (a[i] - b[i]) ** 2;
          }
          const rms = (s) => Math.sqrt(s / n);
          return { l: rms(sa), r: rms(sb), diff: rms(sd) };
        }"""
        deadline = time.monotonic() + timeout
        got = {"l": 0.0, "r": 0.0, "diff": 0.0}
        while time.monotonic() < deadline:
            got = self.page.evaluate(read)
            if got["l"] > floor and got["r"] > floor:
                return got
            self.page.wait_for_timeout(30)
        raise AssertionError(f"the key bus never carried signal on both sides: {got}")

    def pointer_down(self, selector: str) -> None:
        """A real pointer press, held: the mouse goes down on the element."""
        box = self.page.locator(selector).first.bounding_box()
        assert box, f"{selector} is not on screen"
        self.page.mouse.move(
            box["x"] + box["width"] / 2, box["y"] + box["height"] * 0.8
        )
        self.page.mouse.down()

    def pointer_up(self) -> None:
        self.page.mouse.up()

    def settle(self) -> None:
        """Pending htmx requests and the fetches they trigger have landed."""
        self.page.wait_for_load_state("networkidle")

    def responsive(self) -> bool:
        """The main thread answers within a second (a frozen page doesn't)."""
        return self.page.evaluate(
            "new Promise((r) => { const t = performance.now();"
            " setTimeout(() => r(performance.now() - t < 1000), 0); })"
        )


@pytest.fixture
def studio(chromium, server):
    context = chromium.new_context(
        viewport={"width": 1400, "height": 900}, accept_downloads=True
    )
    context.add_init_script(_INIT)
    context.set_default_timeout(10_000)
    page = context.new_page()
    st = Studio(page, server)
    yield st
    problems = st.unexplained()
    context.close()
    assert not problems, "the page reported errors:\n  " + "\n  ".join(problems)


def wav_bytes(seconds: float = 0.5, hz: float = 220.0, stereo: bool = False) -> bytes:
    """A small 16-bit PCM WAV: a steady tone with 10 ms fades (steady, so a
    probe's timing never decides what it reads); `stereo` puts a different
    pitch in the right channel, so the two channels do not match."""
    sr = 44100
    t = np.arange(int(sr * seconds)) / sr
    env = np.minimum(1.0, np.minimum(t, t[-1] - t) / 0.01)
    left = np.sin(2 * np.pi * hz * t) * env * 0.6
    chans = [left, np.sin(2 * np.pi * hz * 1.5 * t) * env * 0.6] if stereo else [left]
    pcm = (np.stack(chans, axis=1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(len(chans))
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def name_it(page, name: str) -> None:
    """Answer the name popover (tweaktype.js `#name-pop`: every `hx-prompt`
    asks in it, never the browser's `prompt()`) with `name`, and save: the
    request the control makes leaves on the Enter."""
    page.wait_for_selector("#name-pop:popover-open")
    page.fill("#name-pop input", name)
    page.press("#name-pop input", "Enter")


def confirm_it(page, ok: bool = True) -> str:
    """Answer the confirm popover (tweaktype.js `#confirm-pop`: every
    `hx-confirm` asks in it, never the browser's `confirm()`) — OK by its
    button, else Esc — and return its question."""
    page.wait_for_selector("#confirm-pop:popover-open")
    said = page.inner_text("#confirm-pop .confirm-q")
    if ok:
        page.click("#confirm-pop .btn-primary")
    else:
        page.keyboard.press("Escape")
    page.wait_for_selector("#confirm-pop:not(:popover-open)", state="attached")
    return said


_CONFIRM_ALL = (
    "document.addEventListener('htmx:confirm', (e) => { e.preventDefault();"
    " e.stopImmediatePropagation(); e.detail.issueRequest(); }, true);"
)


def confirm_all(page) -> None:
    """Every `hx-confirm` on this page answered OK at once, unasked (what
    `page.on("dialog", accept)` did): for a journey whose subject is not
    the question. `test_frontend_contracts.py` drives the popover itself."""
    page.add_init_script(_CONFIRM_ALL)
    page.evaluate(f"() => {{ {_CONFIRM_ALL} }}")
