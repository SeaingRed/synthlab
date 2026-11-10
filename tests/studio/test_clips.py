"""The clip store: 'record a sound I made'."""

import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.studio import limits
from synthlab.studio.clips import list_clips, load_clip, save_clip
from synthlab.studio.server import create_app
from synthlab.studio.voice import read_wav


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def make_wav(sr: int, frames: int, channels: int = 1) -> bytes:
    """A minimal 16-bit PCM WAV, silent, at an arbitrary (possibly
    hostile) sample rate and frame count."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(max(1, sr))
        w.writeframes(b"\x00\x00" * frames * channels)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


@pytest.fixture
def client_with_clip(tmp_path):
    with make_client(tmp_path) as c:
        save_clip(tmp_path, "bounce", np.zeros((4410, 2)))
        yield c, (lambda: c.app.state.studio)


def test_save_load_list_delete_round_trip(tmp_path):
    x = np.column_stack([np.linspace(-0.5, 0.5, 4410)] * 2)
    save_clip(tmp_path, "my chop", x, meta={"bpm": 120.0})
    assert (tmp_path / "clips" / "my chop.wav").exists()
    loaded, sr = load_clip(tmp_path, "my chop")
    assert sr == 44100 and len(loaded) == 4410
    rows = list_clips(tmp_path)
    assert rows[0]["name"] == "my chop" and rows[0]["bpm"] == 120.0


def test_degenerate_clip_name_falls_back_to_clip_not_untitled(tmp_path):
    """The clip store keeps its own fallback word: the
    shared safe_name() default is "untitled", but clips._safe always gave
    "clip" for a name that sanitizes away to nothing, and save_clip/
    load_clip must still agree with each other under that name."""
    save_clip(tmp_path, "...", np.zeros((100, 2)))
    assert (tmp_path / "clips" / "clip.wav").exists()
    assert not (tmp_path / "clips" / "untitled.wav").exists()
    loaded, sr = load_clip(tmp_path, "...")
    assert loaded is not None and sr == 44100


def test_clip_serve_finds_a_degenerately_named_clip(tmp_path):
    """`GET /clip/{name}.wav` must fall back to "clip" the same way
    `save_clip` does. On plain `safe_name(...)` (the shared "untitled"
    fallback), a clip saved under a degenerate name (on disk as "clip.wav")
    is unreachable — the route looks for "untitled.wav" and 404s. The
    routes share `clips.clip_safe` with `save_clip`."""
    with make_client(tmp_path) as c:
        save_clip(tmp_path, "...", np.zeros((100, 2)))
        assert (tmp_path / "clips" / "clip.wav").exists()
        # (the serve route is the one reader of the degenerate name)
        wav = c.get("/clip/....wav")
        assert wav.status_code == 200 and wav.content[:4] == b"RIFF"


def _place(s, clip, at_bar=0):
    """Put a clip on the working loop's lane (the Perform placer is gone;
    a section opened in Perform brings its takes here)."""
    from dataclasses import replace

    s.arrangement = replace(
        s.arrangement,
        clips=[*s.arrangement.clips, {"clip": clip, "at_bar": at_bar, "gain": 1.0}],
    )


def test_bounce_persists_the_current_preview(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post("/clip/bounce", headers={"HX-Prompt": "bench take"})
        assert r.status_code == 200
        # it says where it went (the drawer it fills stays shut): a done toast
        toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]
        assert toast["kind"] == "done"
        assert toast["message"].startswith("Bounced “bench take” (1 bar, ")
        assert toast["message"].endswith(" s) to Clips.")
        wav = client.get("/clip/bench take.wav")
        assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
        html = client.get("/").text
        assert 'data-name="bench take"' in html  # the clip library lists it


def test_bounce_matches_play_with_clips(tmp_path):
    """The bounce route used to build its RenderInputs positionally and drop
    clip_root on the floor, so a Perform loop with a clip in the
    lane bounced without that clip's audio — a different render from the
    one ▶ just played. render_inputs(s, v) makes both routes build the same
    typed input, so they can no longer drift apart.

    An audibly non-silent clip is essential here: a silent clip would pass
    whether or not clip_root actually reached the render, defeating the
    test's purpose."""
    with make_client(tmp_path) as c:
        x = np.column_stack([np.sin(np.linspace(0, 900, 44100))] * 2) * 0.4
        save_clip(tmp_path, "riser", x)
        state = c.app.state.studio
        _place(state, "riser")
        played = c.post("/fragment/preview", data={"view": "perform"})
        url = json.loads(played.headers["HX-Trigger"])["preview-ready"]["url"]
        play_audio = read_wav(c.get(url).content)
        c.post(
            "/clip/bounce",
            data={"name": "b2"},
            headers={"HX-Current-URL": "http://testserver/view/perform"},
        )
        bounced = read_wav((state.data_dir / "clips" / "b2.wav").read_bytes())
    assert np.max(np.abs(play_audio)) > 0.01  # fixture actually carries audio
    assert np.allclose(play_audio, bounced, atol=1 / 16000)


def test_clip_delete_removes_both_files(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/clip/bounce", headers={"HX-Prompt": "gone"})
        r = client.post("/fragment/clip/delete", data={"name": "gone"})
        assert r.status_code == 200
        assert not (tmp_path / "clips" / "gone.wav").exists()
        assert not (tmp_path / "clips" / "gone.meta.json").exists()


def _kick_arr():
    from synthlab.studio.state import Arrangement

    return Arrangement(drums=[["kick", 0], ["kick", 8]])


def test_drum_sample_slot_changes_the_render(tmp_path):
    """A drum voice referencing a clip plays the clip through the whole
    groove machinery instead of the synthesized hit."""
    from synthlab.studio.render import RenderInputs, render_audio

    x = np.column_stack([np.sin(np.linspace(0, 800, 22050))] * 2) * 0.5
    save_clip(tmp_path, "chop", x)
    arr = _kick_arr()
    synth_hit = render_audio(
        RenderInputs(patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path)
    )
    arr.drum_samples = {"kick": "chop"}
    sampled = render_audio(
        RenderInputs(patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path)
    )
    assert sampled.shape == synth_hit.shape
    assert float(np.max(np.abs(sampled - synth_hit))) > 1e-4


def test_missing_clip_falls_back_to_the_synth_hit(tmp_path):
    from synthlab.studio.render import RenderInputs, render_audio

    arr = _kick_arr()
    plain = render_audio(
        RenderInputs(patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path)
    )
    arr.drum_samples = {"kick": "nope"}
    fallback = render_audio(
        RenderInputs(patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path)
    )
    assert np.allclose(plain, fallback)


def test_render_key_tracks_clip_content(tmp_path):
    from synthlab.studio.render import RenderInputs

    x = np.zeros((4410, 2))
    save_clip(tmp_path, "c", x)
    arr = _kick_arr()
    arr.drum_samples = {"kick": "c"}
    k1 = RenderInputs(
        patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path
    ).key()
    save_clip(tmp_path, "c", x + 0.1)  # same name, new content
    k2 = RenderInputs(
        patch=_patch(), arr=arr, parts=("drums",), clip_root=tmp_path
    ).key()
    assert k1 != k2


def test_merge_arrangement_keeps_drum_samples(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.arrangement.drum_samples = {"kick": "chop"}
        client.post("/fragment/patch", data={"cutoff": "500", "bpm": "124"})
        assert s.arrangement.drum_samples == {"kick": "chop"}


def _patch():
    from synthlab.synth import Patch

    return Patch()


def test_drums_sample_route_sets_and_clears(tmp_path):
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        x = np.zeros((4410, 2))
        save_clip(tmp_path, "chop", x)
        r = client.post(
            "/fragment/drums/sample", data={"voice": "kick", "clip": "chop"}
        )
        assert r.status_code == 200
        assert s.arrangement.drum_samples == {"kick": "chop"}
        client.post("/fragment/drums/sample", data={"voice": "kick", "clip": ""})
        assert s.arrangement.drum_samples == {}
        assert (
            client.post(
                "/fragment/drums/sample", data={"voice": "nope", "clip": "chop"}
            ).status_code
            == 404
        )


def test_a_row_takes_percussion_rendered_once_into_the_store(tmp_path):
    """A row's "Percussion" choice (`perc:<kind>`, synthlab/percussion.py)
    renders the one-shot into the clip store the first time — `perc-<kind>`,
    its meta saying so — and points the row at it; the next row reuses it.
    Every row's menu offers the five, store empty or not."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        page = client.get("/view/drums").text
        assert page.count('<optgroup label="Percussion">') == 8
        assert 'value="perc:cowbell"' in page and 'value="perc:clank"' in page
        r = client.post(
            "/fragment/drums/sample", data={"voice": "rim", "clip.rim": "perc:cowbell"}
        )
        assert r.status_code == 200
        assert s.arrangement.drum_samples == {"rim": "perc-cowbell"}
        wav = tmp_path / "clips" / "perc-cowbell.wav"
        assert wav.exists()
        meta = json.loads((tmp_path / "clips" / "perc-cowbell.meta.json").read_text())
        assert meta["source"] == "percussion" and meta["percussion"] == "cowbell"
        stamp = wav.stat().st_mtime_ns
        client.post(
            "/fragment/drums/sample", data={"voice": "tom", "clip.tom": "perc:cowbell"}
        )
        assert wav.stat().st_mtime_ns == stamp  # rendered once
        assert 'value="perc:cowbell" selected' in client.get("/view/drums").text
        bad = client.post(
            "/fragment/drums/sample", data={"voice": "rim", "clip.rim": "perc:gong"}
        )
        assert bad.status_code == 422
        # a clip of the user's own that only shares the name is never taken
        # for the one-shot, and one merely starting "perc-" stays listed
        save_clip(tmp_path, "perc-shaker", np.zeros((441, 2)))
        save_clip(tmp_path, "perc-loop", np.zeros((441, 2)))
        client.post(
            "/fragment/drums/sample", data={"voice": "hat", "clip.hat": "perc:shaker"}
        )
        assert s.arrangement.drum_samples["hat"] == "perc-shaker-2"
        page = client.get("/view/drums").text
        assert 'value="perc-loop"' in page and 'value="perc-shaker-2"' in page


def test_vs_beat_adds_drums_to_the_bench_phrase(tmp_path):
    """Bench views judged a sound half-blind — no way to hear it over the
    beat it is destined for. The vs-beat toggle adds the drum
    lanes to the reference phrase; ▶ and Use keep agreeing because both go
    through the same render_inputs(s, view)."""
    with make_client(tmp_path) as client:
        s = client.app.state.studio
        s.arrangement.drums = [["kick", 0], ["kick", 8]]
        r1 = client.post("/fragment/patch", data={"cutoff": "700"})
        url1 = r1.headers["HX-Trigger"]
        client.post(
            "/fragment/arrangement",
            data={"phrase": "hold", "vs_beat": "on", "bpm": "124"},
        )
        assert s.vs_beat is True
        r2 = client.post("/fragment/patch", data={"cutoff": "700"})
        assert r2.headers["HX-Trigger"] != url1  # the beat joined the phrase
        # unchecking (checkbox absent) turns it back off
        client.post("/fragment/arrangement", data={"phrase": "hold", "bpm": "124"})
        assert s.vs_beat is False


def test_keep_take_as_clip(tmp_path):
    """A voice take was one overwritten page-local file — 'keep as clip'
    promotes it to loop material."""
    from synthlab.studio.voice import save_take

    with make_client(tmp_path) as client:
        save_take(tmp_path, np.sin(np.linspace(0, 100, 22050)) * 0.4)
        r = client.post("/voice/keep", headers={"HX-Prompt": "my take"})
        assert r.status_code == 200
        assert (tmp_path / "clips" / "my take.wav").exists()


def test_keep_take_without_a_take_is_a_toast(tmp_path):
    with make_client(tmp_path) as client:
        assert client.post("/voice/keep", headers={"HX-Prompt": "x"}).status_code == 422


def test_clip_lane_joins_the_perform_render_and_exports(tmp_path):
    """The clip lane: a fourth stem kind beside main/layer/drums —
    bounced phrases and takes become loop material."""
    from synthlab.studio.render import RenderInputs, render_audio
    from synthlab.studio.state import Arrangement

    x = np.column_stack([np.sin(np.linspace(0, 900, 44100))] * 2) * 0.4
    save_clip(tmp_path, "riser", x)
    arr = Arrangement(cells=[["A1", 0]])
    plain = render_audio(
        RenderInputs(
            patch=_patch(),
            arr=arr,
            parts=("synth", "drums", "clips"),
            clip_root=tmp_path,
        )
    )
    arr.clips = [{"clip": "riser", "at_bar": 0, "gain": 1.0}]
    with_clip = render_audio(
        RenderInputs(
            patch=_patch(),
            arr=arr,
            parts=("synth", "drums", "clips"),
            clip_root=tmp_path,
        )
    )
    assert with_clip.shape == plain.shape
    assert float(np.max(np.abs(with_clip - plain))) > 1e-4


def test_clip_lane_absent_from_bench_previews(tmp_path):
    from synthlab.studio.render import RenderInputs
    from synthlab.studio.state import Arrangement

    arr = Arrangement()
    k1 = RenderInputs(
        patch=_patch(), arr=arr, parts=("synth",), clip_root=tmp_path
    ).key()
    arr.clips = [{"clip": "riser", "at_bar": 0, "gain": 1.0}]
    k2 = RenderInputs(
        patch=_patch(), arr=arr, parts=("synth",), clip_root=tmp_path
    ).key()
    assert k1 == k2  # synth-only previews never see the lane


def _wav_bytes(seconds=0.2, sr=44100):
    import io
    import wave

    x = (np.sin(np.linspace(0, 300, int(sr * seconds))) * 20000).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.column_stack([x, x]).tobytes())
    return buf.getvalue()


def test_clip_upload_accepts_pcm_wav(tmp_path):
    """Jam capture: the browser records the master bus —
    performance FX included, exactly as performed — and uploads a PCM WAV
    into the clip store."""
    with make_client(tmp_path) as client:
        r = client.post(
            "/clip/upload",
            data={"name": "jam one"},
            files={"file": ("jam.wav", _wav_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        assert (tmp_path / "clips" / "jam one.wav").exists()
        loaded, sr = load_clip(tmp_path, "jam one")
        assert sr == 44100 and len(loaded) > 0


def test_clip_upload_rejects_garbage(tmp_path):
    with make_client(tmp_path) as client:
        r = client.post(
            "/clip/upload",
            data={"name": "bad"},
            files={"file": ("x.wav", b"not a wav", "audio/wav")},
        )
        assert r.status_code == 422


def test_jam_capture_surface_exists():
    src = Path("synthlab/studio/static/audio.js").read_text(encoding="utf-8")
    assert "createMediaStreamDestination" in src and "jam-rec" in src
    html = Path("synthlab/studio/templates/views/perform.html").read_text(
        encoding="utf-8"
    )
    assert 'id="jam-rec"' in html


def test_clip_wav_refuses_path_traversal(tmp_path):
    """/clip/{name}.wav must never read outside the clip dir."""
    with make_client(tmp_path) as client:
        (tmp_path / "secret.wav").write_bytes(b"RIFFxxxx")
        for evil in ("..%2Fsecret", "..%5Csecret", "%2e%2e%2fsecret"):
            assert client.get(f"/clip/{evil}.wav").status_code in (404, 422), evil


# --------------------------------------------------------- bounded input


def test_upload_rejects_hostile_sample_rate(client):
    wav = make_wav(sr=1, frames=100)
    r = client.post("/clip/upload", files={"file": ("j.wav", wav, "audio/wav")})
    assert r.status_code == 422


def test_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr("synthlab.studio.limits.MAX_UPLOAD_BYTES", 1000)
    # A *valid* WAV comfortably over the cap: b"\0" * 5000 isn't a real WAV,
    # so a plain (413, 422) check couldn't tell a size rejection from a
    # content rejection. This one only fails for its size.
    wav = _wav_bytes(seconds=0.5)
    r = client.post("/clip/upload", files={"file": ("j.wav", wav, "audio/wav")})
    assert r.status_code == 413


def test_jam_upload_at_the_client_cap_fits_under_the_body_size_limit(client):
    """IMPORTANT #2: a jam recorded past ~131s (limits.MAX_UPLOAD_BYTES /
    (48kHz * stereo * 16-bit)) used to be rejected by BodySizeLimit with no
    client-side copy kept — the take was just gone. The jam encoder is now
    capped client-side at limits.MAX_CLIP_SECONDS (30s); a WAV at exactly
    that length, at the jam's own format (stereo, 48kHz, 16-bit), must
    comfortably fit under the body cap and be accepted."""
    wav = _wav_bytes(seconds=limits.MAX_CLIP_SECONDS, sr=48000)
    assert len(wav) < limits.MAX_UPLOAD_BYTES
    r = client.post(
        "/clip/upload",
        data={"name": "long jam"},
        files={"file": ("jam.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200
    loaded, sr = load_clip(client.app.state.studio.data_dir, "long jam")
    assert sr == 48000 and len(loaded) > 0


# ------------------------------------ jams are named later


def test_rename_clip_moves_audio_meta_and_placements(client_with_clip, tmp_path):
    c, state = client_with_clip
    _place(state(), "bounce")
    state().arrangement.drum_samples = {"kick": "bounce"}
    r = c.post("/fragment/clip/rename", data={"name": "bounce", "new": "big hit"})
    assert r.status_code == 200
    assert (tmp_path / "clips" / "big hit.wav").exists()
    assert not (tmp_path / "clips" / "bounce.wav").exists()
    meta = json.loads((tmp_path / "clips" / "big hit.meta.json").read_text("utf-8"))
    assert meta["name"] == "big hit"
    assert [e["clip"] for e in state().arrangement.clips] == ["big hit"]
    assert state().arrangement.drum_samples == {"kick": "big hit"}
    assert 'data-name="big hit"' in r.text


def test_rename_clip_case_only_change_succeeds(tmp_path):
    """Minor #6: `jam` -> `Jam` hit `dst.exists()` on the case-insensitive
    Windows filesystem and was refused as "already exists" — dst literally
    IS src there, so the rename must go through a temp name to actually
    flip the case on disk."""
    import os

    with make_client(tmp_path) as c:
        save_clip(tmp_path, "jam", np.zeros((100, 2)))
        r = c.post("/fragment/clip/rename", data={"name": "jam", "new": "Jam"})
        assert r.status_code == 200
        names = os.listdir(tmp_path / "clips")
        assert "Jam.wav" in names and "jam.wav" not in names
        meta = json.loads((tmp_path / "clips" / "Jam.meta.json").read_text("utf-8"))
        assert meta["name"] == "Jam"
        assert 'data-name="Jam"' in r.text


def test_rename_clip_still_refuses_a_genuinely_different_taken_name(tmp_path):
    """A case-only rename is allowed (same underlying file); a rename onto
    an unrelated existing clip that merely differs in case must still be
    refused as taken, not silently treated as a same-file rename."""
    with make_client(tmp_path) as c:
        save_clip(tmp_path, "jam", np.zeros((100, 2)))
        save_clip(tmp_path, "other", np.zeros((100, 2)))
        r = c.post("/fragment/clip/rename", data={"name": "jam", "new": "Other"})
        assert r.status_code == 422 and "toast" in r.text


def test_rename_clip_replaces_a_stray_meta_for_a_name_with_no_audio(tmp_path):
    """A `<new>.meta.json` with no `<new>.wav` would make
    `Path.rename` raise FileExistsError on Windows after the audio had
    already moved. The stray describes no clip, so it is replaced by the
    renamed clip's own meta."""
    from synthlab.studio.clips import rename_clip

    d = tmp_path / "clips"
    save_clip(tmp_path, "jam", np.zeros((100, 2)), meta={"bpm": 124})
    (d / "keeper.meta.json").write_text('{"name": "keeper", "bpm": 90}', "utf-8")
    assert rename_clip(tmp_path, "jam", "keeper")
    assert (d / "keeper.wav").exists() and not (d / "jam.wav").exists()
    assert not (d / "jam.meta.json").exists()
    meta = json.loads((d / "keeper.meta.json").read_text("utf-8"))
    assert meta["name"] == "keeper" and meta["bpm"] == 124


def test_rename_clip_without_meta_drops_the_stray_rather_than_adopting_it(tmp_path):
    from synthlab.studio.clips import rename_clip

    d = tmp_path / "clips"
    save_clip(tmp_path, "jam", np.zeros((100, 2)))
    (d / "jam.meta.json").unlink()
    (d / "keeper.meta.json").write_text('{"name": "keeper", "bars": 16}', "utf-8")
    assert rename_clip(tmp_path, "jam", "keeper")
    assert (d / "keeper.wav").exists()
    assert not (d / "keeper.meta.json").exists()  # not someone else's bars


def test_rename_clip_onto_a_real_clip_touches_neither(tmp_path):
    from synthlab.studio.clips import rename_clip

    d = tmp_path / "clips"
    save_clip(tmp_path, "jam", np.zeros((100, 2)), meta={"bpm": 124})
    save_clip(tmp_path, "other", np.ones((50, 2)) * 0.5, meta={"bpm": 90})
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    assert not rename_clip(tmp_path, "jam", "other")
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before


def test_rename_clip_refuses_a_taken_name_and_sanitizes(client_with_clip, tmp_path):
    c, _ = client_with_clip
    save_clip(tmp_path, "other", np.zeros((100, 2)))
    r = c.post("/fragment/clip/rename", data={"name": "bounce", "new": "other"})
    assert r.status_code == 422 and "toast" in r.text
    assert (tmp_path / "clips" / "bounce.wav").exists()  # nothing overwritten
    r = c.post("/fragment/clip/rename", data={"name": "bounce", "new": "../../evil"})
    assert r.status_code == 200
    assert not (tmp_path.parent / "evil.wav").exists()
    assert list((tmp_path / "clips").glob("*evil*.wav"))
    assert (
        c.post("/fragment/clip/rename", data={"name": "nope", "new": "x"}).status_code
        == 404
    )


def test_every_clip_in_the_library_can_be_renamed_inline(client_with_clip):
    """The clip library drawer is on every page (it replaced the shelf
    Synths and Perform drew), and ✎ is on every clip in it."""
    c, _ = client_with_clip
    for view in ("editor", "perform", "song", "voice", "lab"):
        html = c.get(f"/view/{view}").text
        assert 'hx-post="/fragment/clip/rename"' in html, view
        assert 'name="new" value="bounce"' in html, view


def test_row_sample_reads_its_own_rows_select(client_with_clip):
    """Every row's sample select sits inside #drum-grid, so htmx posts all
    eight: the route must read the posting row's `clip.<voice>`, not the
    first `clip` in the form."""
    c, state = client_with_clip
    rows = {f"clip.{v}": "" for v in ("kick", "snare", "hat")}
    r = c.post(
        "/fragment/drums/sample",
        data={**rows, "clip.snare": "bounce", "voice": "snare"},
    )
    assert r.status_code == 200
    assert state().arrangement.drum_samples == {"snare": "bounce"}
    html = c.get("/view/drums").text
    assert 'name="clip.snare"' in html and 'name="clip"' not in html


def test_rename_follows_the_clip_into_scenes_and_the_song_export(tmp_path):
    """Scenes hold `asdict` copies of the arrangement, so a
    rename that touched only the live lane orphaned every captured scene —
    song export, scene launch and the scene's drum one-shots silently
    dropped the clip. `StudioState.rename_clip_refs` is the one traversal.
    (v3: a capture is a song section — the lane is its takes, the one-shot
    slots the drums track's.)"""
    import shutil

    with make_client(tmp_path) as c:
        s = c.app.state.studio
        t = np.arange(44100) / 44100
        x = 0.5 * np.sin(2 * np.pi * 330 * t)
        save_clip(tmp_path, "riser", np.stack([x, x], 1))
        _place(s, "riser")
        c.post("/fragment/drums/sample", data={"voice": "kick", "clip.kick": "riser"})
        c.post("/song/from-loop", data={"name": "a"})
        # a baseline that owns the lane must follow too
        s.baselines["probe"] = {
            "label": "",
            "data": {"arr.clips": [{"clip": "riser", "at_bar": 0, "gain": 1.0}]},
        }
        r = c.post("/fragment/clip/rename", data={"name": "riser", "new": "lift"})
        assert r.status_code == 200
        sec = s.song["sections"][0]
        assert [e["clip"] for e in sec["audio"]] == ["lift"]
        drums = next(t for t in s.song["tracks"] if t["kind"] == "drums")
        assert drums["drum_samples"] == {"kick": "lift"}
        assert s.baselines["probe"]["data"]["arr.clips"][0]["clip"] == "lift"
        s.baselines.pop("probe")
        with_clip = c.get("/export/song.wav")
        assert with_clip.status_code == 200
        # the same song with the clip gone from disk renders differently:
        # the renamed clip really is in the export
        shutil.move(tmp_path / "clips" / "lift.wav", tmp_path / "lift.wav")
        without = c.get("/export/song.wav")
        assert without.status_code == 200
        assert with_clip.content != without.content


def test_rename_refuses_a_blank_name(client_with_clip, tmp_path):
    c, _ = client_with_clip
    for blank in ("", "   "):
        r = c.post("/fragment/clip/rename", data={"name": "bounce", "new": blank})
        assert r.status_code == 422 and "toast" in r.text
    assert (tmp_path / "clips" / "bounce.wav").exists()
    assert not (tmp_path / "clips" / "clip.wav").exists()


# -- review round: a save never lands on a clip that is there ----------------


def test_a_second_bounce_never_overwrites_the_first(tmp_path):
    """Two bounces under the default name used to be one clip: the second
    wrote straight over `bounce.wav`, changing audio already in the song."""
    with make_client(tmp_path) as client:
        assert client.post("/clip/bounce").headers["x-clip-name"] == "bounce"
        first = (tmp_path / "clips" / "bounce.wav").read_bytes()
        client.post("/fragment/patch", data={"cutoff": "300"})
        r = client.post("/clip/bounce")
        assert r.status_code == 200 and r.headers["x-clip-name"] == "bounce 2"
        assert (tmp_path / "clips" / "bounce.wav").read_bytes() == first
        assert (tmp_path / "clips" / "bounce 2.wav").read_bytes() != first
        # the answer says which name it became, and the shelf lists both
        toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]["message"]
        assert "bounce 2" in toast
        assert 'data-name="bounce 2"' in r.text and 'data-name="bounce"' in r.text
        meta = json.loads((tmp_path / "clips" / "bounce 2.meta.json").read_text())
        assert meta["name"] == "bounce 2"


def test_a_taken_name_is_taken_in_any_case(tmp_path):
    """Windows' filesystem holds "BOUNCE" and "bounce" as one file."""
    with make_client(tmp_path) as client:
        client.post("/clip/bounce", headers={"HX-Prompt": "Drop"})
        before = (tmp_path / "clips" / "Drop.wav").read_bytes()
        client.post("/fragment/patch", data={"cutoff": "300"})
        r = client.post("/clip/bounce", headers={"HX-Prompt": "DROP"})
        assert r.headers["x-clip-name"] == "DROP 2"
        assert (tmp_path / "clips" / "Drop.wav").read_bytes() == before
        assert [c["name"] for c in list_clips(tmp_path)] == ["DROP 2", "Drop"]


def test_keep_take_and_jam_upload_pick_a_free_name_too(tmp_path):
    from synthlab.studio.voice import save_take

    with make_client(tmp_path) as client:
        save_clip(tmp_path, "take", np.zeros((4410, 2)))
        save_clip(tmp_path, "jam", np.zeros((4410, 2)))
        names = ("take", "jam")
        kept = {n: (tmp_path / "clips" / f"{n}.wav").read_bytes() for n in names}
        save_take(tmp_path, np.sin(np.linspace(0, 100, 22050)) * 0.4)
        r = client.post("/voice/keep")
        assert r.status_code == 200 and r.headers["x-clip-name"] == "take 2"
        assert "take 2" in r.headers["HX-Trigger"]
        r = client.post(
            "/clip/upload", files={"file": ("jam.wav", _wav_bytes(), "audio/wav")}
        )
        assert r.status_code == 200 and r.headers["x-clip-name"] == "jam 2"
        for n, audio in kept.items():
            assert (tmp_path / "clips" / f"{n}.wav").read_bytes() == audio
            assert (tmp_path / "clips" / f"{n} 2.wav").exists()


@pytest.mark.parametrize(
    "meta",
    [
        '{"bpm": "fast"}',
        '{"bpm": [120], "bars": {"n": 4}, "sr": true}',
        '{"bpm": NaN, "seconds": "long", "name": 7, "source": ["x"]}',
        '{"bpm": 1e999}',
        '{"bpm": 1' + "0" * 400 + "}",
        '["not", "an", "object"]',
        '"bpm"',
        "{broken",
    ],
)
def test_a_bad_meta_on_disk_never_fails_a_page(tmp_path, meta):
    """The clip shelf did arithmetic on a clip's bpm (`| round | int`): a
    hand-edited `"bpm": "fast"` was a 500 on every page that drew it — the
    one holding the clip's delete button included. The library drawer that
    replaced it is on every page (tests/studio/test_clip_library.py holds
    it to metas of any shape)."""
    save_clip(tmp_path, "evil", np.zeros((4410, 2)), meta={"bpm": 120.0})
    (tmp_path / "clips" / "evil.meta.json").write_text(meta, encoding="utf-8")
    (row,) = list_clips(tmp_path)
    assert row["name"] == "evil" and row["seconds"] == 0.1  # the file's word
    assert "bpm" not in row and "bars" not in row and "sr" not in row
    assert isinstance(row.get("source", ""), str)
    with make_client(tmp_path) as client:
        for path in ("/", "/view/perform"):
            r = client.get(path)
            assert r.status_code == 200 and 'data-name="evil"' in r.text
        r = client.post("/fragment/clip/delete", data={"name": "evil"})
        assert r.status_code == 200 and list_clips(tmp_path) == []


def test_a_good_meta_still_reaches_the_library(tmp_path):
    spec = {"kind": "riser", "direction": "up", "bars": 2, "bpm": 124.0}
    meta = {"bpm": 124.0, "bars": 2, "source": "transition", "transition": spec}
    save_clip(tmp_path, "riser", np.zeros((4410, 2)), meta=meta)
    (row,) = list_clips(tmp_path)
    assert row["bpm"] == 124.0 and row["bars"] == 2 and row["sr"] == 44100
    assert row["source"] == "transition" and row["transition"] == spec
    with make_client(tmp_path) as client:
        assert "124 bpm" in client.get("/").text
