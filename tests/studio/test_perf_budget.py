"""Two performance regressions that cost 3.2 MB per UI interaction each.

Both were invisible to the rest of the suite: the pages rendered correctly and
every assertion passed while a single slider nudge pulled down 12 note WAVs and
266 KB of uncompressed HTML.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app

PIANO = Path("synthlab/studio/static/piano.js")


def test_key_warm_is_bounded_where_it_is_not_needed():
    """The key warm-up used to be armed only by hovering the keyboard (it
    once ran on every `preview-ready`, 3.2 MB a nudge, even on Drums). LIVE
    PLAY 1a made it deliberate again — all 24 visible keys on load and on
    every patch change, or the first press after any edit is a cold render
    — so what bounds it now: nothing on a view with no keyboard (no `.pkey`,
    no pitches), nothing for a patch the worklet plays, one timer (300 ms
    debounce) and one token, so a drag's run of patch changes cancels the
    warm-ups it overtakes instead of stacking them.
    """
    src = PIANO.read_text(encoding="utf-8")
    keys = Path("synthlab/studio/static/keys.js").read_text(encoding="utf-8")
    assert 'document.querySelectorAll(".pkey")' in src
    assert "!workletCanPlay(patch)" in src
    assert '...(needWav ? [[m, holdOf(patch, holdMode), ""]] : [])' in keys
    assert "if (jobs.length) warmTimer" in keys
    assert "clearTimeout(warmTimer);" in keys and "token === warmToken" in keys


def test_pages_are_compressed(tmp_path):
    """The editor page is ~266 KB of HTML (509 chord pads, 456 SVG rects) and
    compresses ~12x. Without GZipMiddleware every navigation shipped it raw.
    """
    with TestClient(create_app(tmp_path)) as client:
        r = client.get("/", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert r.headers.get("content-encoding") == "gzip", r.headers
        # httpx decodes transparently, so compare the wire length to the body
        wire = int(r.headers["content-length"])
        assert wire < len(r.content) / 4, f"only {len(r.content) / wire:.1f}x"


def test_key_bus_scales_with_polyphony():
    """Every /note/*.wav returns at peak 1.0 (a driven patch saturates), so raw
    voice summing hit 1.98 on two keys and 7.23 on eight. The limiters were
    then asked for ~17 dB of reduction on a bass-heavy signal and answered with
    pumping and intermodulation — "static after a few keys in a row".

    The key bus must therefore do the mixing itself: voices^-POLY under a
    fixed headroom, leaving the limiter to catch peaks only. POLY is 0.25
    (1/sqrt dipped every held note 3 dB as a second joined, 6 at four:
    sounds lost volume because other sounds were playing).
    """
    src = Path("synthlab/studio/static/keys.js").read_text(encoding="utf-8")
    assert "keyPoly" in src and "KEY_HEADROOM" in src
    assert "Math.max(1, keyVoices) ** POLY" in src, "not scaled by the voices"
    poly = float(re.search(r"POLY = ([\d.]+)", src).group(1))
    assert 0.2 <= poly <= 0.5, poly
    # the gain must be ramped, or every new key clicks
    assert "keyPoly.gain.setTargetAtTime" in src
    # and it has to sit in the bus, not on a single voice
    assert "keyFilter.connect(keyGain).connect(keyPoly).connect(keyLimit)" in src
    # counted up on start and back down when the source ends (stop or ring-out)
    assert "keyVoices++; setPoly();" in src
    assert "src.onended" in src and "keyVoices--; setPoly();" in src


def test_the_keys_level_does_not_follow_the_loop():
    """The keys had two budgets — 0.7 alone, 0.32 while the loop played — and
    fell 6.8 dB the moment the loop started, a held note too (measured: -11.8
    dB RMS alone, -18.6 under the loop: sounds lost volume because other
    sounds were playing). One level now, the
    same with the loop or without (measured -15.6 dB both); the master's
    limiter (-1 dBFS) takes the rare peak of keys over a loud loop: 0.01-0.05
    % of samples over its threshold with one to four keys held over the
    loop, as before (0.03 %). The worst case stays within a dB of it."""
    src = Path("synthlab/studio/static/keys.js").read_text(encoding="utf-8")
    assert "KEY_HEADROOM_MIX" not in src and "S.isPlaying()" not in src
    level = float(re.search(r"KEY_HEADROOM = ([\d.]+)", src).group(1))
    # loop (0.89) + keys through master gain 0.8: within ~1 dB of unity
    assert (0.89 + level) * 0.8 < 1.12, level
    # nothing re-balances the keys when the transport moves
    assert "setPoly" not in Path("synthlab/studio/static/audio.js").read_text(
        encoding="utf-8"
    )


AUDIO = Path("synthlab/studio/static/audio.js")


def test_same_url_preview_does_not_restart_loop():
    """render_key covers every audible input, so an identical preview URL is
    identical audio — restarting the loop (and flushing the live-key WAV
    cache) for it turned a volume nudge into an audible glitch."""
    src = AUDIO.read_text(encoding="utf-8")
    assert "url === lastUrl" in src, "swapTo needs a same-URL guard"
    assert "detail.url !== lastUrl" in src, "flushKeys must be gated on a changed URL"


def test_volume_slider_does_not_post_the_form():
    """#volume is client-only (no name) but sits inside the sound-bar form;
    without a capture-phase stopPropagation htmx posts a no-op render per
    nudge."""
    src = AUDIO.read_text(encoding="utf-8")
    seg = src.split('id === "volume"')[1][:400]
    assert "stopPropagation" in seg, "volume handler must stop the form post"
    assert "}, true)" in seg, "volume handler must run in capture phase"


def test_crossfade_preserves_loop_phase():
    """Every tweak used to restart the phrase at beat 1 — you could never
    hear the tail while tuning. Same-duration swaps must start
    the new source at the old loop's current phase."""
    src = AUDIO.read_text(encoding="utf-8")
    assert "phase" in src.split("function startLoop")[1][:1200]
