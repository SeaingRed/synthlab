"""The sampler instrument in the studio: "Use as sampler" on a clip or a
take, Resample, the chops, render keys by content, the live-key path and
persistence (studio/samplerview.py, routes/sampler.py)."""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.core import SR
from synthlab.studio.clips import save_clip
from synthlab.studio.render import keyed_patch, patch_key
from synthlab.studio.server import create_app
from synthlab.studio.worklet import blockers
from synthlab.synth import Patch

HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}


def _hx(view: str) -> dict:
    return {**HX, "HX-Current-URL": f"http://testserver/view/{view}"}


def _tone(hz: float, seconds: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    x = np.sin(2 * np.pi * hz * t) * 0.5
    return np.column_stack([x, x])


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def _use(client, name: str, view: str = "editor"):
    return client.post("/fragment/sampler/clip", data={"name": name}, headers=_hx(view))


def test_use_as_sampler_makes_a_playable_sound_from_the_clip(tmp_path):
    client = _client(tmp_path)
    s = client.app.state.studio
    save_clip(tmp_path, "tone", _tone(220))
    r = _use(client, "tone")
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    p = s.patch
    assert p.osc1 == "sampler" and sampler.valid_id(p.sampler_sample)
    assert (tmp_path / "samples" / f"{p.sampler_sample}.wav").is_file()
    assert p.name == "tone" and s.slot_prev is not None  # "before last load"
    # the page draws the sampler block with its waveform
    assert 'id="sampler-panel"' in r.text and "sampler-svg" in r.text
    # ▶ plays it
    audio = client.app.state.svc.render("editor").audio()
    assert np.abs(audio).max() > 0.05
    # one undo step back to the synth
    client.post("/fragment/undo", headers=HX)
    assert s.patch.osc1 == "saw"


def test_from_perform_or_the_song_it_lands_on_synths(tmp_path):
    client = _client(tmp_path)
    save_clip(tmp_path, "tone", _tone(220))
    for view in ("perform", "song"):
        r = _use(client, "tone", view)
        assert r.headers.get("HX-Push-Url") == "/view/editor", view
        assert 'data-view="editor"' in r.text


def test_silent_or_missing_audio_is_a_toast(tmp_path):
    client = _client(tmp_path)
    save_clip(tmp_path, "hush", np.zeros((4410, 2)))
    for name in ("hush", "nope"):
        r = _use(client, name)
        assert r.status_code == 422
    assert client.app.state.studio.patch.osc1 == "saw"


def test_the_clip_library_and_song_takes_offer_it(tmp_path):
    client = _client(tmp_path)
    save_clip(tmp_path, "tone", _tone(220))
    page = client.get("/view/editor").text
    assert 'hx-post="/fragment/sampler/clip"' in page
    assert 'hx-post="/fragment/sampler/resample"' in page  # the sound bar
    tpl = (
        client.app.state.svc.templates.get_template("fragments/song_arranger.html")
    ).filename
    assert "/fragment/sampler/clip" in open(tpl, encoding="utf-8").read()


def test_the_render_key_carries_the_audio_by_content(tmp_path):
    """A re-recorded clip taken again is a new sample, and so a new key; the
    same audio under another name is the same sample and the same key."""
    client = _client(tmp_path)
    svc = client.app.state.svc
    save_clip(tmp_path, "take", _tone(220))
    _use(client, "take")
    first = (svc.state.patch.sampler_sample, svc.render_inputs("editor").key())
    save_clip(tmp_path, "take", _tone(330))  # re-recorded
    _use(client, "take")
    second = (svc.state.patch.sampler_sample, svc.render_inputs("editor").key())
    assert first[0] != second[0] and first[1] != second[1]
    save_clip(tmp_path, "copy", _tone(330))
    _use(client, "copy")
    svc.state.patch = replace(svc.state.patch, name="take")
    assert svc.state.patch.sampler_sample == second[0]
    assert svc.render_inputs("editor").key() == second[1]


def test_new_fields_stay_out_of_every_older_key():
    assert not [k for k in keyed_patch(Patch()) if k.startswith("sampler_")]
    moved = replace(Patch(), sampler_start=0.25)
    assert keyed_patch(moved)["sampler_start"] == 0.25
    assert patch_key(moved) != patch_key(Patch())


def test_resample_turns_a_render_into_the_sampler(tmp_path):
    client = _client(tmp_path)
    s = client.app.state.studio
    r = client.post("/fragment/sampler/resample", data={"source": "phrase"}, headers=HX)
    assert r.status_code == 200
    assert s.patch.osc1 == "sampler" and s.patch.name == "resampled phrase"
    assert sampler.seconds(s.patch.sampler_sample) > 1.0
    # "loop" is the Perform loop, drums and all
    r = client.post("/fragment/sampler/resample", data={"source": "loop"}, headers=HX)
    assert r.status_code == 200 and s.patch.name == "resampled loop"
    loop = client.app.state.svc.render("perform").audio()
    assert sampler.seconds(s.patch.sampler_sample) == len(loop) / SR
    # an empty loop is silence: a toast, the sound kept
    s.arrangement = replace(s.arrangement, cells=[], drums=[])
    for i, lay in enumerate(s.layers):
        s.layers[i] = {**lay, "cells": []}
    before = s.patch
    r = client.post("/fragment/sampler/resample", data={"source": "loop"}, headers=HX)
    assert r.status_code == 422 and s.patch == before


def test_resample_says_what_it_replaced_and_undo_clears_edited(tmp_path):
    """⤷ Resample loop replaces the edited layer's sound: the answer says
    so (a done toast naming the way back), and Ctrl+Z lands on the saved
    sound with "edited" off — the load had moved the baseline, and undo
    used to leave it on the resample."""
    client = _client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/preset/save", data={"name": "my acid"}, headers=HX)
    assert not s.is_dirty("sound")
    r = client.post("/fragment/sampler/resample", data={"source": "loop"}, headers=HX)
    toast = json.loads(r.headers["HX-Trigger"])["studio-toast"]
    assert toast["kind"] == "done"
    assert toast["message"].startswith("Layer 1 is now a sampler of the working loop (")
    assert "Ctrl+Z gives “my acid” back" in toast["message"]
    client.post("/fragment/undo", headers=HX)
    assert s.patch.name == "my acid" and not s.is_dirty("sound")


def test_chops_map_slices_to_keys_and_slice_edits_are_undoable(tmp_path):
    client = _client(tmp_path)
    s = client.app.state.studio
    save_clip(tmp_path, "tone", _tone(220, 1.0))
    _use(client, "tone")
    r = client.post("/fragment/sampler/chop", data={"mode": "16"}, headers=HX)
    assert r.status_code == 200
    assert len(sampler.parse_slices(s.patch.sampler_slices)) == 16
    assert 'id="sampler-panel"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
    assert r.text.count('class="slice') >= 16
    client.post(
        "/fragment/sampler/slice", data={"index": "3", "action": "reverse"}, headers=HX
    )
    client.post(
        "/fragment/sampler/slice", data={"index": "3", "action": "pitch_up"}, headers=HX
    )
    sl = sampler.parse_slices(s.patch.sampler_slices)[3]
    assert sl.rev and sl.pitch == 1
    before = sampler.parse_slices(s.patch.sampler_slices)
    client.post(
        "/fragment/sampler/slice",
        data={"index": "5", "action": "nudge_left"},
        headers=HX,
    )
    after = sampler.parse_slices(s.patch.sampler_slices)
    assert after[5].a < before[5].a and after[4].b == after[5].a
    client.post("/fragment/undo", headers=HX)
    assert sampler.parse_slices(s.patch.sampler_slices)[5].a == before[5].a
    # bad input is refused, not guessed at
    for data in ({"index": "x", "action": "reverse"}, {"index": "1", "action": "x"}):
        assert (
            client.post("/fragment/sampler/slice", data=data, headers=HX).status_code
            == 422
        )
    assert (
        client.post(
            "/fragment/sampler/chop", data={"mode": "7"}, headers=HX
        ).status_code
        == 422
    )
    client.post("/fragment/sampler/chop", data={"mode": "clear"}, headers=HX)
    assert s.patch.sampler_slices == ""


def test_the_chopped_instrument_is_saved_with_the_project(tmp_path):
    save_clip(tmp_path, "tone", _tone(220, 1.0))
    with _client(tmp_path) as client:
        _use(client, "tone")
        client.post("/fragment/sampler/chop", data={"mode": "8"}, headers=HX)
        client.post(
            "/fragment/sampler/slice",
            data={"index": "2", "action": "reverse"},
            headers=HX,
        )
        saved = client.app.state.studio.patch
        project = client.get("/project.json").json()
    assert project["patch"]["sampler_slices"] == saved.sampler_slices
    assert project["patch"]["sampler_sample"] == saved.sampler_sample
    with _client(tmp_path) as client:  # a restart: state.json
        assert client.app.state.studio.patch == saved
        # a fresh process-level cache still finds the audio on disk
        sampler._MEM._d.pop(saved.sampler_sample, None)
        assert np.abs(client.app.state.svc.render("editor").audio()).max() > 0.01
    # and a preset keeps it too
    with _client(tmp_path) as client:
        client.post("/fragment/preset/save", data={"name": "kit"}, headers=HX)
        client.app.state.studio.patch = Patch()
        client.post("/fragment/preset/load", data={"name": "kit"}, headers=HX)
        assert client.app.state.studio.patch.sampler_slices == saved.sampler_slices


def test_sampler_keys_take_the_server_rendered_path(tmp_path):
    """The live voice plays a sampler only as sampler.live_ok admits it
    (the tape read, or formant mode's grain train): the WSOLA stretch keeps
    the keys on /note renders, and /patch.json says which field. Sampler
    fields left on a synth block nothing — the engine never reads them
    there. (test_live_sampler.py: the sampler the worklet plays.)"""
    client = _client(tmp_path)
    save_clip(tmp_path, "tone", _tone(220, 1.0))
    _use(client, "tone")
    plain = client.app.state.studio.patch
    client.app.state.studio.patch = replace(plain, sampler_mode="formant")
    pj = client.get("/patch.json?view=editor").json()
    assert pj["worklet_ok"] is True and pj["sampler_live"]  # the grain train: live
    client.app.state.studio.patch = replace(plain, sampler_stretch=1.5)
    pj = client.get("/patch.json?view=editor").json()
    assert pj["worklet_ok"] is False and "sampler_stretch" in pj["worklet_blockers"]
    assert pj["sampler_live"] is None
    r = client.get("/note/A3.wav?view=editor")
    assert r.status_code == 200 and len(r.content) > 10_000
    p = client.app.state.studio.patch
    assert blockers(replace(p, osc1="saw")) == []
    assert json.loads(json.dumps(p.to_json()))["sampler_sample"] == p.sampler_sample


def test_the_sampler_block_hides_while_no_osc_samples(tmp_path):
    client = _client(tmp_path)
    page = client.get("/view/editor").text
    assert 'id="sampler-panel" class="sampler-panel" hidden' in page
    # the sampler rows are shape rows: hidden by the osc pills' CSS
    assert "#row-sampler_root" in page
    assert 'value="sampler"' in page  # the osc 1 pill


# --- pitch detection: the root follows the recording --------------------------


def _noise(seconds: float = 0.5) -> np.ndarray:
    x = np.random.default_rng(7).standard_normal(int(seconds * SR)) * 0.3
    return np.column_stack([x, x])


def test_a_pitched_sample_takes_its_detected_root(tmp_path):
    """220 Hz is A3 (57): the new sound's root is the recording's pitch, so
    formant mode and the keys start in tune; the panel says what it heard."""
    client = _client(tmp_path)
    save_clip(tmp_path, "tone", _tone(220))
    r = _use(client, "tone")
    assert client.app.state.studio.patch.sampler_root == 57
    assert "detected A3 · 100%" in r.text
    # the root already is it: "use" is there but has nothing to do
    assert 'hx-post="/fragment/sampler/root"' in r.text
    button = r.text.split('hx-post="/fragment/sampler/root"', 1)[1].split(">", 1)[0]
    assert "disabled" in button


def test_noise_keeps_the_default_root_and_says_so(tmp_path):
    client = _client(tmp_path)
    save_clip(tmp_path, "hiss", _noise())
    r = _use(client, "hiss")
    s = client.app.state.studio
    assert s.patch.osc1 == "sampler" and s.patch.sampler_root == 60
    assert "no clear pitch" in r.text
    assert 'hx-post="/fragment/sampler/root"' not in r.text
    # "use" posted anyway is a toast, and the root stays
    r = client.post("/fragment/sampler/root", headers=HX)
    assert r.status_code == 422 and s.patch.sampler_root == 60


def test_use_sets_the_root_as_one_undo_step_and_redraws_its_slider(tmp_path):
    client = _client(tmp_path)
    s = client.app.state.studio
    save_clip(tmp_path, "tone", _tone(330))  # E4 = 64
    _use(client, "tone")
    client.post("/fragment/patch", data={"sampler_root": "48"}, headers=HX)
    assert s.patch.sampler_root == 48
    page = client.get("/view/editor").text
    assert "detected E4" in page
    r = client.post("/fragment/sampler/root", headers=HX)
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    assert s.patch.sampler_root == 64
    # the #patch form's root row comes back, or its next post undoes this
    assert 'id="row-sampler_root"' in r.text and "hx-swap-oob" in r.text
    client.post("/fragment/undo", headers=HX)
    assert s.patch.sampler_root == 48
    # not a sampler: refused
    s.patch = Patch()
    assert client.post("/fragment/sampler/root", headers=HX).status_code == 422


def test_resample_detects_too(tmp_path):
    """A resampled phrase goes through the same adopt: its root is either
    what the detector is sure of or the default, never something else."""
    client = _client(tmp_path)
    s = client.app.state.studio
    client.post("/fragment/sampler/resample", data={"source": "phrase"}, headers=HX)
    midi, confidence = sampler.sample_pitch(s.patch.sampler_sample)
    want = round(midi) if confidence >= 0.8 else 60
    assert s.patch.sampler_root == want


# --- slice markers: drag a chop's start -----------------------------------------


def _chopped_client(tmp_path, count: str = "8"):
    client = _client(tmp_path)
    save_clip(tmp_path, "tone", _tone(220, 1.0))
    _use(client, "tone")
    r = client.post("/fragment/sampler/chop", data={"mode": count}, headers=HX)
    return client, r


def _move(client, index, at):
    return client.post(
        "/fragment/sampler/slice",
        data={"index": str(index), "action": "move", "at": str(at)},
        headers=HX,
    )


def test_a_chop_is_its_own_undo_step(tmp_path):
    """Ctrl+Z after a chop unchops; it used to skip past the chop to the
    sound before the sampler (the step was pushed before the edit, when
    `push_undo_step` sees nothing moved and keeps nothing)."""
    client, _ = _chopped_client(tmp_path)
    s = client.app.state.studio
    assert s.patch.sampler_slices
    client.post("/fragment/undo", headers=HX)
    assert s.patch.osc1 == "sampler" and s.patch.sampler_slices == ""


def test_every_slice_start_is_a_focusable_marker(tmp_path):
    client, r = _chopped_client(tmp_path)
    assert r.text.count('class="slice-mark') == 8
    marks = r.text.split('class="slice-mark')[1:]
    third = marks[3]
    assert 'role="slider"' in third and 'tabindex="0"' in third
    assert 'data-index="3"' in third and 'data-at="0.375"' in third
    # it may go from the previous start to its own end, 10 ms short of each
    assert 'data-lo="0.26"' in third and 'data-hi="0.49"' in third
    assert 'style="left: 37.5%"' in third
    assert 'data-step="0.005"' in r.text  # 5 ms of this 1 s sample
    # unchopped: no markers
    r = client.post("/fragment/sampler/chop", data={"mode": "clear"}, headers=HX)
    assert "slice-mark" not in r.text


def test_a_dragged_marker_moves_its_start_and_the_neighbour_follows(tmp_path):
    client, _ = _chopped_client(tmp_path)
    s = client.app.state.studio
    before = sampler.parse_slices(s.patch.sampler_slices)
    r = _move(client, 3, 0.4)
    assert r.status_code == 200 and "preview-ready" in r.headers["HX-Trigger"]
    after = sampler.parse_slices(s.patch.sampler_slices)
    assert after[3].a == 0.4 and after[2].b == 0.4
    assert after[3].b == before[3].b and after[2].a == before[2].a
    untouched = [i for i in range(8) if i not in (2, 3)]
    assert [after[i] for i in untouched] == [before[i] for i in untouched]
    # the panel comes back with the marker where it now is
    assert 'data-at="0.4"' in r.text and 'id="sampler-panel"' in r.text
    # one undo step puts it back
    client.post("/fragment/undo", headers=HX)
    assert sampler.parse_slices(s.patch.sampler_slices) == before


def test_a_marker_is_clamped_to_its_neighbours_and_a_minimum_length(tmp_path):
    client, _ = _chopped_client(tmp_path)
    s = client.app.state.studio
    _move(client, 3, -5)  # past the previous slice's start
    sl = sampler.parse_slices(s.patch.sampler_slices)
    assert sl[3].a == 0.26 and sl[2].b == 0.26  # 0.25 + 10 ms of a 1 s sample
    _move(client, 3, 5)  # past its own end
    sl = sampler.parse_slices(s.patch.sampler_slices)
    assert sl[3].a == 0.49 and sl[3].b == 0.5
    _move(client, 0, -1)  # the first slice's start stops at the sample's start
    sl = sampler.parse_slices(s.patch.sampler_slices)
    assert sl[0].a == 0.0
    assert all(x.b - x.a >= 0.01 - 1e-9 for x in sl)  # none under 10 ms


def test_a_move_without_a_real_position_is_refused(tmp_path):
    client, _ = _chopped_client(tmp_path)
    s = client.app.state.studio
    before = s.patch.sampler_slices
    for data in (
        {"index": "3", "action": "move"},
        {"index": "3", "action": "move", "at": "x"},
        {"index": "3", "action": "move", "at": "nan"},
        {"index": "3", "action": "move", "at": "inf"},
    ):
        r = client.post("/fragment/sampler/slice", data=data, headers=HX)
        assert r.status_code == 422, data
    assert s.patch.sampler_slices == before
    _move(client, 99, 0.5)  # no such slice: nothing moves
    assert s.patch.sampler_slices == before
