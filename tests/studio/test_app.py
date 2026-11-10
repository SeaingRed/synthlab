from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_health(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_htmx_is_vendored_and_served(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200
    assert len(r.content) > 10_000
    assert b"htmx" in r.content


def test_htmx_v4_vendored(tmp_path):
    # The vendored build embeds its real version marker as
    # `.version="4.0.0-beta6"` (minified `this.version=`); a bare "4." check
    # would also false-positive on htmx 2 builds, so match the marker itself.
    js = TestClient(create_app(data_dir=tmp_path)).get("/static/htmx.min.js").text
    assert '.version="4.' in js


def test_htmx_error_statuses_dont_auto_swap(tmp_path):
    # htmx 4 swaps every non-204/304 response into its target by default
    # (v2 only swapped 2xx), which would let a 422 toast fragment wipe
    # request-issuing elements that don't declare hx-swap="none"
    # themselves. base.html extends htmx.config.noSwap globally via the
    # documented meta[name="htmx-config"] mechanism instead of touching
    # every element; assert that config actually reaches the page.
    client = make_client(tmp_path)
    html = client.get("/").text
    assert (
        '<meta name="htmx-config" content=\'{"noSwap": [204, 304, "4xx", "5xx"]}\'>'
        in html
    )


def test_422_toast_fragment_is_not_oob(tmp_path):
    # toast.html must NOT carry hx-swap-oob: htmx 4 processes
    # hx-swap-oob elements unconditionally (regardless of hx-swap="none"
    # or the noSwap status list above), so an OOB attribute here would
    # make htmx replace #toasts a second time on top of audio.js's own
    # DOMParser-based insertion, orphaning its auto-dismiss timer.
    client = make_client(tmp_path)
    r = client.post("/fragment/preset/load", data={"name": "does-not-exist"})
    assert r.status_code == 422
    assert "hx-swap-oob" not in r.text
    assert 'id="toasts"' in r.text
    assert "Preset not found." in r.text


def test_nav_links_are_click_guarded_against_full_navigation(tmp_path):
    # htmx 4's own click interception was observed (real-browser smoke)
    # to sometimes let the native <a href> navigation through instead of
    # (or racing) htmx's fragment fetch. audio.js adds an unconditional
    # capture-phase guard on .views clicks so the browser default is
    # always cancelled before any bubble-phase listener — including
    # htmx's own — runs. Assert both halves are still wired: the nav
    # markup still carries hx-get/href/hx-target/hx-push-url, and the
    # guard is actually present in the served JS.
    client = make_client(tmp_path)
    html = client.get("/").text
    assert (
        'hx-get="/view/breed" hx-target="#view" hx-swap="outerHTML" hx-push-url="true"'
        in html
    )
    assert 'href="/view/breed"' in html

    js = client.get("/static/audio.js").text
    assert '.closest(".views a[hx-get]")' in js
    assert "e.preventDefault()" in js


def test_patch_and_transport_triggers_survive_htmx4_changed_bug(tmp_path):
    # htmx 4.0.0-beta6's `changed` trigger modifier gates on
    # `elt.value` for every element the listener is bound to. With no
    # explicit `from:`, that element is the <form> itself (input events
    # bubble up to it) — and a <form> has no `.value`, so
    # `e.get(t) !== t.value` is always `undefined !== undefined`
    # (false), the "did it change" flag never flips true, and the
    # early `if(!r) return;` guard silently swallows every bubbled
    # input event before the debounce delay/handler ever runs. Sliders
    # never POST. `input delay:150ms, submit` (no `changed`) sidesteps
    # the broken gate entirely while still debouncing via `delay:`.
    # (#patch also hears `ws-input`: a panel moved to another workspace
    # column is outside its form, and workspace.js re-sends its input
    # events to the form under that name: studio frontend.md.)
    client = make_client(tmp_path)
    html = client.get("/").text
    assert (
        'hx-post="/fragment/patch" hx-trigger="input delay:150ms,'
        ' ws-input delay:150ms, submit"' in html
    )
    assert (
        'hx-post="/fragment/arrangement" hx-trigger="input delay:150ms, submit"' in html
    )
    assert "changed delay:150ms" not in html


def test_no_v2_era_oob_false_opt_out(tmp_path):
    # htmx 4 beta6's #He/#Ie OOB pass picks up *any* element carrying an
    # hx-swap-oob attribute during a full-body swap, then treats the raw
    # attribute value as a swap-style string when it isn't exactly
    # "true" — so a v2-era `hx-swap-oob="false"` opt-out (there is no
    # such opt-out in v4; the attribute's mere presence now opts an
    # element in) gets parsed as the style "false" and throws "Unknown
    # swap style: false" in the console on every full-body swap. The
    # in-page #analysis placeholder in editor.html never needs to be an
    # OOB target itself — the live analysis fragment is delivered
    # separately with its own hx-swap-oob="true" — so the fix is to
    # drop the attribute rather than find a v4 opt-out value.
    client = make_client(tmp_path)
    html = client.get("/").text
    assert 'hx-swap-oob="false"' not in html


def test_network_errors_are_surfaced():
    """A dead/restarting server used to be completely silent: htmx:error
    (fetch failures) had no listener and nothing showed in-flight state, so
    a failed render was indistinguishable from a slow one."""
    src = Path("synthlab/studio/static/audio.js").read_text(encoding="utf-8")
    assert 'addEventListener("htmx:error"' in src
    assert "server unreachable" in src
    assert 'addEventListener("htmx:before:request"' in src
    assert 'addEventListener("htmx:finally:request"' in src
    css = Path("synthlab/studio/static/style.css").read_text(encoding="utf-8")
    assert "#audio-led.busy" in css and "#audio-led.err" in css
