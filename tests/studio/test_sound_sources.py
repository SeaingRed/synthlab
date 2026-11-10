"""Browse, audition, Use — one contract for every sound source.

Every browse surface finds a `sounds.Sound` (`sounds.SOURCES`) and hands it
to `svc.audition(sound, mix=...)` and `svc.adopt(request, sound)`. This one
test holds every source to that contract, fresh and in a lived-in session,
alone and in your mix:

- in your mix: the card == the preview Use lands with == ▶ on Synths, byte
  for byte, and Use after the card renders nothing (the same cache entry);
- alone: in a fresh session exactly what Synths plays after Use — moved
  to the sound's own register for a source that knows one (`Sound.register`:
  the audition log's rows, so a bass card is heard at A1) — and in a busy
  one unchanged by the session (no layers, beat, FX or curves);
- Use leaves the baseline clean (not "edited"; Revert returns here);
- Use from the source's home page lands on Synths with the URL; from any
  other page it stays put and pushes nothing.

It replaces the pairwise library/breed/preset audition-vs-load tests that
used to live in test_audition_consistency.py. A new source added to
`SOURCES` without a row in `SETUP` fails `test_every_source_is_covered`.

Render entry point: every listener of a view's loop — Bass Lab's panels,
its phone test, the clip bounce — hears the very render ▶ plays for that
view (`svc.render`), in every view.
"""

import hashlib
import io
import json
import re
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import service, sounds
from synthlab.studio.factory import FACTORY
from synthlab.studio.library import LIBRARY
from synthlab.studio.server import create_app
from synthlab.studio.views import VIEW_NAMES


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def frames(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2")


def loud(wav_bytes: bytes) -> bool:
    return float(np.abs(frames(wav_bytes)).max(initial=0)) > 30


def preview_of(response) -> str:
    return json.loads(response.headers["HX-Trigger"])["preview-ready"]["url"]


def on_synths(client: TestClient) -> bytes:
    r = client.post("/fragment/preview", data={"view": "editor"})
    return client.get(preview_of(r)).content


def _busy(client: TestClient) -> None:
    """A lived-in session: over the beat, a cutoff curve, layer and master
    FX, and the active slot muted (a muted slot once made every card the
    same). The phrase is pinned by the test, not here."""
    s = client.app.state.studio
    s.vs_beat = True
    s.curves = {"cutoff": [[0.0, 0.2], [0.5, 0.9], [1.0, 0.3]]}
    s.fx_master = [{"kind": "delay", "params": {}}]
    s.layers[s.active_layer]["fx"] = [{"kind": "chorus", "params": {}}]
    s.layers[s.active_layer]["muted"] = True
    s.save()


def _tile(client: TestClient) -> str:
    html = client.post("/fragment/breed/generation", data={}).text
    return re.search(r'data-tile-id="([a-f0-9]{12})"', html).group(1)


def _layered_tile(client: TestClient) -> str:
    """A deep card: a whole Sound (following layers, FX, maybe curves)."""
    html = client.post("/fragment/breed/generation", data={"depth": "deep"}).text
    tid = re.search(r'data-tile-id="([a-f0-9]{12})"', html).group(1)
    assert client.app.state.svc.space_store().get(tid)["sound"]["layers"]
    return tid


def _v2_preset(client: TestClient) -> str:
    """A format-2 preset whose sound and chain both differ from the
    session it is later loaded into."""
    s = client.app.state.studio
    client.post("/fragment/patch", data={"cutoff": "700"})
    s.layers[s.active_layer]["fx"] = [
        {"kind": "bitcrush", "params": {"bits": 5.0, "mix": 1.0}}
    ]
    s.curves = {"cutoff": [[0.0, 0.1], [1.0, 0.8]]}
    client.post("/fragment/preset/save", data={"name": "v2", "include_layers": "on"})
    client.post("/fragment/patch", data={"cutoff": "3000"})
    s.layers[s.active_layer]["fx"] = []
    s.curves = {}
    s.save()
    return "v2"


# source -> (make an id in a fresh client, the page it is browsed on, the
# card's audition URL or None, the Use route, the Use form's id field)
SETUP = {
    "library": (
        lambda c: LIBRARY[7]["name"],
        "library",
        "/library/{}/audition.wav",
        "/fragment/library/load",
        "name",
    ),
    "factory": (
        lambda c: FACTORY[3]["name"],
        "editor",
        "/factory/{}/audition.wav",
        "/fragment/factory/load",
        "name",
    ),
    # a genre recipe with a following layer: its whole stack, FX and all
    "recipes": (
        lambda c: "Long Cinema Pad",
        "library",
        "/recipe/{}/audition.wav",
        "/fragment/recipe/load",
        "name",
    ),
    "preset": (
        _v2_preset,
        "editor",
        "/preset/{}/audition.wav",
        "/fragment/preset/load",
        "name",
    ),
    # a layered card: ▶ is its whole stack, Use "with its layers"
    "breed": (
        _layered_tile,
        "breed",
        "/breed/{}/audition.wav",
        "/fragment/breed/adopt",
        "id",
    ),
    # its "▶ main only" and "Use sound": the main lane, the session's lanes kept
    "breed_main": (
        _layered_tile,
        "breed",
        "/breed/{}/audition.wav?main=1",
        "/fragment/breed/adopt",
        "id",
    ),
    # the Map has no card audition route: its points play through Use
    "map": (_tile, "map", None, "/fragment/load", "id"),
}
# what else a source's Use form posts
EXTRA = {"breed_main": {"main": "1"}}
# the sources whose Sound carries a chain (a v2 preset's, a layered card's)
CHAINED = {"preset", "breed", "breed_main", "recipes"}


def test_every_source_is_covered():
    assert set(SETUP) == set(sounds.SOURCES)


def _card(client: TestClient, source: str, ident: str, mix: bool) -> bytes:
    """The card's ▶: through its route where it has one (which must be the
    interface's answer), else through the interface."""
    svc = client.app.state.svc
    direct = svc.audition(sounds.find(svc, source, ident), mix=mix).body
    url = SETUP[source][2]
    if url is not None:
        sep = "&" if "?" in url else "?"
        via_route = client.get(
            url.format(ident) + (sep + "mix=1" if mix else "")
        ).content
        assert via_route == direct
    return direct


@pytest.mark.parametrize("mix", [False, True], ids=["alone", "mix"])
@pytest.mark.parametrize("busy", [False, True], ids=["fresh", "busy"])
@pytest.mark.parametrize("source", sorted(SETUP))
def test_every_source_auditions_and_lands_as_one(
    tmp_path, monkeypatch, source, busy, mix
):
    client = make_client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    make_id, page, _url, use, field = SETUP[source]
    ident = make_id(client)
    s.audition = "riff"  # the reference phrase is the user's, kept for both
    alone_fresh = _card(client, source, ident, mix=False)
    if busy:
        _busy(client)
    sound = sounds.find(svc, source, ident)
    assert sounds.find(svc, source, "no such sound") is None

    heard = _card(client, source, ident, mix)
    assert loud(heard)
    if not mix:
        # on its own: the session never reaches it
        assert heard == alone_fresh

    renders: list = []
    real = service.render_loop
    monkeypatch.setattr(
        service, "render_loop", lambda *a, **k: renders.append(1) or real(*a, **k)
    )
    used = client.post(
        use,
        data={field: ident, **EXTRA.get(source, {})},
        headers={"HX-Current-URL": f"http://x/view/{page}"},
    )
    assert used.status_code == 200
    landed = client.get(preview_of(used)).content
    if mix:
        # Use after the card is the card's own cache entry
        assert renders == []
    monkeypatch.setattr(service, "render_loop", real)

    # where Use lands: Synths (body and URL) from the source's home page
    if sound.home_view == page:
        assert 'data-view="editor"' in used.text
        assert used.headers["HX-Push-Url"] == "/view/editor"
    else:
        assert f'data-view="{page}"' in used.text
        assert "HX-Push-Url" not in used.headers

    synths = on_synths(client)
    assert landed == synths
    if mix:
        assert heard == landed == synths
    elif not busy:
        # what Synths plays — in the sound's own register where it knows
        # one (a breed card: a bass alone is heard at A1, not at A3)
        assert heard == svc.render("editor", register=sound.register).wav
        if sound.register in (None, "lead"):  # root A: the bench's own A3
            assert heard == synths

    # the sound is its own baseline: not "edited", Revert returns here
    assert not s.layers[s.active_layer].get("muted")
    assert not s.is_dirty("sound")
    assert s.baseline_label("sound") == sound.label
    # a format-2 preset / a layered card brings its chain, and Use puts
    # that chain live: its lanes too, unless it is the main lane alone
    assert (sound.chain is not None) == (source in CHAINED)
    if sound.chain is not None:
        assert s.layers[s.active_layer]["fx"] == sound.chain["main_fx"]
        assert s.curves == sound.chain["curves"]
        assert not s.is_dirty("fx")
        others = [lay for i, lay in enumerate(s.layers) if i != s.active_layer]
        if sound.chain["layers"] is None:
            assert not any(lay.get("follow") for lay in others)
        else:
            want = sound.chain["layers"]
            assert [lay for lay in others if lay.get("patch")] == want


@pytest.mark.parametrize("source", sorted(SETUP))
def test_use_away_from_the_home_page_stays_put(tmp_path, source):
    """A source with a home page lands on Synths only from there."""
    client = make_client(tmp_path)
    make_id, _page, _url, use, field = SETUP[source]
    ident = make_id(client)
    used = client.post(
        use, data={field: ident}, headers={"HX-Current-URL": "http://x/view/bass"}
    )
    assert 'data-view="bass"' in used.text
    assert "HX-Push-Url" not in used.headers


# ------------------------------------------------------ one render entry point


@pytest.mark.parametrize("view", VIEW_NAMES)
def test_every_listener_hears_the_views_play_render(tmp_path, monkeypatch, view):
    """Bass Lab's panels, its phone test and the clip bounce all hear the
    render ▶ plays for the view they are asked from — the same
    RenderInputs, the same cache entry — never inputs built by hand."""
    client = make_client(tmp_path)
    s, svc = client.app.state.studio, client.app.state.svc
    s.bass_root = "E1"  # Bass Lab's phrase differs from the other benches
    s.save()
    hdr = {"HX-Current-URL": f"http://x/view/{view}"}
    play = preview_of(
        client.post("/fragment/preview", data={"view": view}, headers=hdr)
    )
    key = play.removeprefix("/preview/").removesuffix(".wav")
    play_wav = client.get(play).content

    asked: list[str] = []
    real = svc._ensure
    monkeypatch.setattr(
        svc, "_ensure", lambda inp: asked.append(inp.key()) or real(inp)
    )

    assert client.get("/fragment/bass", headers=hdr).status_code == 200
    assert asked == [key]

    asked.clear()
    phone = client.post("/fragment/bass/phone-audition", headers=hdr)
    assert asked[0] == key
    post_key = hashlib.sha1(f"{key}:phone".encode()).hexdigest()[:12]
    assert preview_of(phone) == f"/preview/{post_key}.wav"
    assert client.get(preview_of(phone)).content[:4] == b"RIFF"

    asked.clear()
    client.post("/clip/bounce", data={"name": "b"}, headers=hdr)
    assert asked == [key]
    # the exact samples ▶ plays
    np.testing.assert_array_equal(
        frames(client.get("/clip/b.wav").content), frames(play_wav)
    )


def test_bass_lab_plays_what_its_panels_measure(tmp_path):
    """The quick key moves Bass Lab's own ▶ (the "bass" view), so the view
    swap, the quick key's preview and the panels agree — and Synths' ▶
    keeps the bench phrase."""
    client = make_client(tmp_path)
    svc = client.app.state.svc
    bass = {"HX-Current-URL": "http://x/view/bass"}
    r = client.post("/fragment/bass/key", data={"root": "E1"}, headers=bass)
    assert preview_of(r) == f"/preview/{svc.render_inputs('bass').key()}.wav"
    played = client.post("/fragment/preview", data={"view": "bass"}, headers=bass)
    assert preview_of(played) == preview_of(r)
    assert svc.render_inputs("editor").key() != svc.render_inputs("bass").key()
