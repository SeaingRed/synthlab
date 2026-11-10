"""What you hear when you press ▶ must be what you get when you press Use.

Every browse surface has two renders of the same sound: the audition, and
the preview that arrives after loading it. They were built independently and
drifted — the breed board's "in loop" button inherited Perform's part
toggles and auditioned as the drum kit, and a Library sound loaded into a
grid with no synth notes rendered silence. This harness renders both sides
of every path and holds them to the same output.

The per-source ▶ == Use == Synths contract (every source, fresh and busy,
alone and in the mix, baseline and landing) is ONE parametrized test in
`test_sound_sources.py`; what stays here are the cross-cutting guards.
"""

import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio.library import LIBRARY
from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def mono(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        n, ch = w.getnframes(), w.getnchannels()
        raw = w.readframes(n)
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    return a.reshape(-1, ch).mean(axis=1) if ch == 2 else a


def loudness(wav_bytes: bytes) -> float:
    a = mono(wav_bytes)
    return float(np.sqrt(np.mean(a**2))) if a.size else 0.0


def centroid(wav_bytes: bytes) -> float:
    a = mono(wav_bytes)
    if a.size == 0:
        return 0.0
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    freqs = np.fft.rfftfreq(len(a), 1 / 44100)
    return float((spec * freqs).sum() / max(spec.sum(), 1e-9))


def preview_of(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["preview-ready"]["url"]


def _empty_the_synth_grid(client: TestClient) -> None:
    """The state that exposed the bug: drums programmed, no synth notes.
    Browsing views rendered s.arrangement synth-only, which is silence."""
    s = client.app.state.studio
    from dataclasses import replace

    s.arrangement = replace(s.arrangement, cells=[])
    s.save()


# --------------------------------------------------------------- the guarantee


@pytest.mark.parametrize("view", ["library", "breed", "map", "bass", "editor"])
def test_loading_a_sound_is_never_silent_on_a_browse_view(tmp_path, view):
    """The sharp failure: press ▶ on a Library sound and hear it, press Use
    and get nothing, because the Perform grid holds no synth notes."""
    client = make_client(tmp_path)
    _empty_the_synth_grid(client)
    name = LIBRARY[0]["name"]
    r = client.post(
        "/fragment/library/load",
        data={"name": name},
        headers={"HX-Current-URL": f"http://x/view/{view}"},
    )
    assert r.status_code == 200
    wav = client.get(preview_of(r)).content
    assert loudness(wav) > 0.001, f"{view}: loading {name} rendered silence"


def test_auditions_run_through_the_session_chain(tmp_path):
    """A sound auditioned dry and then loaded into a bitcrushed master would
    not be the sound you were promised. The audition has to hear the chain
    the load will put it through."""
    client = make_client(tmp_path)
    name = LIBRARY[0]["name"]
    dry = client.get(f"/library/{name}/audition.wav?mix=1").content
    s = client.app.state.studio
    s.fx_master = [{"kind": "bitcrush", "params": {"bits": 4.0, "downsample": 8.0}}]
    s.save()
    wet = client.get(f"/library/{name}/audition.wav?mix=1").content
    assert loudness(wet) > 0.001
    assert abs(centroid(dry) - centroid(wet)) > 40, (centroid(dry), centroid(wet))


def test_perform_and_drums_still_render_the_real_arrangement(tmp_path):
    """The bench views judge a sound against a constant phrase; the views
    that ARE the arrangement must keep rendering it."""
    client = make_client(tmp_path)
    s = client.app.state.studio
    from synthlab.studio.server import create_app as _  # noqa: F401

    before = len(s.arrangement.cells)
    assert before > 0, "fixture expects a seeded grid"
    r = client.post(
        "/fragment/library/load",
        data={"name": LIBRARY[0]["name"]},
        headers={"HX-Current-URL": "http://x/view/perform"},
    )
    assert loudness(client.get(preview_of(r)).content) > 0.001


def _busy(client: TestClient) -> None:
    """A lived-in session: over the beat, the riff phrase, a cutoff curve,
    layer and master FX, a second layer with its own sound."""
    s = client.app.state.studio
    s.audition = "riff"
    s.vs_beat = True
    s.curves = {"cutoff": [[0.0, 0.2], [0.5, 0.9], [1.0, 0.3]]}
    s.fx_master = [{"kind": "delay", "params": {}}]
    s.layers[s.active_layer]["fx"] = [{"kind": "chorus", "params": {}}]
    s.save()


def test_library_marks_the_sound_on_synths(tmp_path):
    client = make_client(tmp_path)
    name = LIBRARY[3]["name"]
    client.post("/fragment/library/load", data={"name": name})
    html = client.get("/view/library").text
    card = html.split(f'data-name="{name}"')[0].rsplit("<article", 1)[1]
    assert "active" in card
    card_html = html.split(f'data-name="{name}"')[1].split("</article>")[0]
    # a fact, not a control: a badge, nothing to press
    # (on the card a ✓; in the dock "✓ On Synths": the <i> is the rest)
    assert "✓<i> On Synths</i>" in card_html and "badge-accent" in card_html
    assert "lib-use" not in card_html
    # edited on Synths: loading it again would discard the edits, and says so
    client.post("/fragment/patch", data={"cutoff": "900"})
    html = client.get("/view/library").text
    card_html = html.split(f'data-name="{name}"')[1].split("</article>")[0]
    assert ">Reload<i> on Synths</i><" in card_html and "badge-accent" not in card_html


def test_a_muted_slot_never_makes_every_candidate_sound_the_same(tmp_path):
    """The user's report: "all the breed sounds seem the same". Their active
    layer was muted, so every card, "in your mix" and Use rendered the
    candidate muted — identical audio. Auditions hear the slot unmuted, the
    card plays the sound on its own, and Use unmutes the slot it lands in."""
    import re

    client = make_client(tmp_path)
    _busy(client)
    s = client.app.state.studio
    s.layers[s.active_layer]["muted"] = True
    s.save()
    breed = {"HX-Current-URL": "http://x/view/breed"}
    html = client.post("/fragment/breed/generation", data={}, headers=breed).text
    ids = re.findall(r'data-tile-id="([a-f0-9]{12})"', html)[:3]
    alone = [client.get(f"/breed/{t}/audition.wav").content for t in ids]
    mixed = [client.get(f"/breed/{t}/audition.wav?mix=1").content for t in ids]
    assert len(set(alone)) == 3 and len(set(mixed)) == 3
    used = client.post("/fragment/breed/adopt", data={"id": ids[0]}, headers=breed)
    assert not s.layers[s.active_layer].get("muted")
    assert client.get(preview_of(used)).content == mixed[0]


def test_a_slot_at_level_zero_never_swallows_the_sound_you_pick(tmp_path):
    """The user's report: "every time I pick a new breed sound or play drums
    and go back to synth, no synth sounds come through". The main lane's
    level slider sat at 0 and a load kept it, so every sound picked after
    that landed silent — ▶ played the drums alone — while the cards, heard
    on their own at 1.0, sounded fine. A load opens the slot's level as it
    unmutes it, "in your mix" hears it opened, and the strip says why a
    lane at 0 is silent."""
    import re

    client = make_client(tmp_path)
    _busy(client)
    s, svc = client.app.state.studio, client.app.state.svc
    client.post(
        "/fragment/layer/gain", data={"index": str(s.active_layer), "gain": "0"}
    )
    assert s.layers[s.active_layer]["gain"] == 0.0
    row = svc.layer_context()["strip"][s.active_layer]
    assert not row["hear"] and "level" in row["why"]
    breed = {"HX-Current-URL": "http://x/view/breed"}
    html = client.post("/fragment/breed/generation", data={}, headers=breed).text
    tid = re.findall(r'data-tile-id="([a-f0-9]{12})"', html)[0]
    mixed = client.get(f"/breed/{tid}/audition.wav?mix=1").content
    used = client.post("/fragment/breed/adopt", data={"id": tid}, headers=breed)
    assert s.layers[s.active_layer]["gain"] == 1.0
    assert svc.render_inputs("editor").main_gain == 1.0
    assert client.get(preview_of(used)).content == mixed
    # a quiet level you chose is yours: only a closed one is reopened
    client.post(
        "/fragment/layer/gain", data={"index": str(s.active_layer), "gain": "0.3"}
    )
    client.post("/fragment/breed/adopt", data={"id": tid}, headers=breed)
    assert s.layers[s.active_layer]["gain"] == 0.3


@pytest.mark.parametrize("busy", [False, True])
def test_breeds_loop_button_is_the_mix_card(tmp_path, busy):
    """Breed's "in your mix" loop button (POST /fragment/breed/audition) is
    the card's `?mix=1`: the same render, which test_sound_sources holds
    to the landing and to ▶ on Synths."""
    import re

    client = make_client(tmp_path)
    if busy:
        _busy(client)
    breed = {"HX-Current-URL": "http://x/view/breed"}
    html = client.post("/fragment/breed/generation", data={}, headers=breed).text
    tid = re.search(r'data-tile-id="([a-f0-9]{12})"', html).group(1)
    card = client.get(f"/breed/{tid}/audition.wav?mix=1").content
    r = client.post("/fragment/breed/audition", data={"id": tid}, headers=breed)
    assert json.loads(r.headers["HX-Trigger"])["preview-ready"]["autoplay"] is True
    assert client.get(preview_of(r)).content == card
