"""The clip hub (`/view/clips`): every clip and sample in one place.

Held here: the item references and the tune (hostile input never reaches a
path or a number unchecked), the list (filter, sort, where used), the
routes (bench, list, the tuned audio, Keep / Update and the hub's Undo, a
sample kept as a clip, "Open in Lab"), the makers (a beat and a note part
from a synthetic clip; the plan held to the studio's names), Apply as one
step that the hub's Undo takes back whole — and the pattern's own Ctrl+Z
too — and the Gemini morph against a scripted client (`takecoach.
client_for`, the suite's guard, replaced): what is sent, what comes back
checked, the key scrubbed. Nobody listened to anything here: the audio is
test tones and synthetic drums.
"""

from __future__ import annotations

import html
import io
import json
import re
import types
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab import sampler
from synthlab.studio import cliphub, clipmake, clipmorph, loops, takecoach
from synthlab.studio.agent.config import AgentConfig
from synthlab.studio.clips import list_clips, save_clip
from synthlab.studio.drumfactory import DRUM_KITS
from synthlab.studio.server import create_app

SR = 44100
HX = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/clips"}


def drum_loop(bpm: float = 120.0, bars: int = 2) -> np.ndarray:
    """Kick on 1 and 3, a snare-ish noise on 2 and 4, eighth hats."""
    beat = 60.0 / bpm
    n = int(bars * 4 * beat * SR)
    out = np.zeros(n)
    t = np.arange(int(0.25 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * np.exp(-t * 12)
    rng = np.random.default_rng(1)
    snare = rng.standard_normal(len(t)) * np.exp(-t * 25) * 0.6
    hat = np.diff(rng.standard_normal(int(0.05 * SR)), prepend=0.0)
    hat *= np.exp(-np.arange(len(hat)) / SR * 80) * 0.3
    for b in range(bars * 4):
        at = int(b * beat * SR)
        hit = kick if b % 2 == 0 else snare
        out[at : at + len(hit)] += hit[: n - at]
        for half in (0.0, 0.5):
            a2 = int((b + half) * beat * SR)
            out[a2 : a2 + len(hat)] += hat[: max(0, n - a2)]
    return np.column_stack([out, out]) * 0.7


def melody(notes=(57, 60, 64, 67, 64, 60, 62, 65), step: float = 0.25) -> np.ndarray:
    parts = []
    for m in notes:
        t = np.arange(int(step * SR)) / SR
        env = np.minimum(1.0, t / 0.01) * np.exp(-t * 2)
        parts.append(np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t) * env * 0.5)
    x = np.concatenate(parts)
    return np.column_stack([x, x])


@pytest.fixture
def client(tmp_path):
    c = TestClient(create_app(data_dir=tmp_path))
    save_clip(
        tmp_path, "loop", drum_loop(), meta={"source": "Bass", "bpm": 120.0, "bars": 2}
    )
    save_clip(tmp_path, "tune", melody(), meta={"source": "voice take"})
    return c


def _st(client):
    return client.app.state.studio


def _plan(r) -> str:
    m = re.search(r'name="plan" value="([^"]*)"', r.text)
    assert m, r.text[:400]
    return html.unescape(m.group(1))


# --- references and the tune --------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        None,
        7,
        "",
        "clip:",
        "clip:   ",
        "song:x",
        "sample:../../etc",
        "sample:ABCDEF0123456789",
        "stem:../x:htdemucs:vocals",
        "stem:abcdef0123456789:../m:vocals",
        "stem:abcdef0123456789:m:VOCALS",
        "stem:abcdef12:m:vocals",
    ],
)
def test_a_hostile_reference_is_none(raw):
    assert cliphub.parse_ref(raw) is None


def test_a_clip_name_is_sanitized_as_every_clip_path_is():
    ref = cliphub.parse_ref("clip:../../secret")
    # no separator survives: whatever is left is one file name in the store
    assert ref is not None and not {"/", "\\", ":"} & set(ref.name)
    assert cliphub.parse_ref("sample:" + "a" * 16).kind == "sample"
    stem = cliphub.parse_ref("stem:abcdef0123456789:htdemucs:vocals")
    assert (stem.source, stem.model, stem.name) == (
        "abcdef0123456789",
        "htdemucs",
        "vocals",
    )


def test_the_tune_is_parsed_clamped_and_finite():
    t = cliphub.clean_tune(
        {"semis": "99", "cents": "nan", "gain_db": "inf", "rate": "1e400",
         "start": "-5", "end": "2", "reverse": "on"}
    )  # fmt: skip
    assert t["semis"] == 24 and t["cents"] == 0 and t["gain_db"] == 0.0
    assert t["rate"] == 1.0 and t["start"] == 0.0 and t["end"] == 2.0 and t["reverse"]
    assert (
        cliphub.clean_tune({"start": "3", "end": "1"})["end"] == 0.0
    )  # no backwards span
    assert cliphub.is_identity(cliphub.clean_tune({}))
    assert cliphub.clean_tune("junk") == cliphub.TUNE_DEFAULTS


def test_tune_trims_reverses_pitches_and_speeds():
    x = melody()
    n = len(x)
    t = cliphub.clean_tune({"start": "0.5", "end": "1.5"})
    assert len(cliphub.tune(x, SR, t)) == SR
    rev = cliphub.tune(x, SR, cliphub.clean_tune({"reverse": "on"}))
    assert np.allclose(rev[0], x[-1])
    up = cliphub.tune(x, SR, cliphub.clean_tune({"semis": "12"}))
    assert abs(len(up) - n) < n * 0.02  # pitch keeps the length
    fast = cliphub.tune(x, SR, cliphub.clean_tune({"rate": "2"}))
    assert abs(len(fast) - n / 2) < 64  # the tape rate halves it
    quiet = cliphub.tune(x, SR, cliphub.clean_tune({"gain_db": "-6"}))
    assert np.max(np.abs(quiet)) == pytest.approx(np.max(np.abs(x)) * 10 ** (-6 / 20))


def test_a_tuned_copy_says_where_it_came_from_and_keeps_its_clock():
    meta = {"source": "Bass", "bpm": 120.0, "bars": 2, "vocoder": {"x": 1}}
    t = cliphub.clean_tune({"rate": "1.5"})
    out = cliphub.tuned_meta(meta, t, "clip:loop")
    assert out["bpm"] == 180.0 and out["bars"] == 2 and "vocoder" not in out
    assert out["tune"]["from"] == "clip:loop" and out["tune"]["rate"] == 1.5
    trimmed = cliphub.tuned_meta(meta, cliphub.clean_tune({"end": "1"}), "clip:loop")
    assert "bars" not in trimmed


# --- the list ---------------------------------------------------------------------


def test_the_list_holds_clips_and_samples_filters_and_sorts(client, tmp_path):
    s = _st(client)
    sampler.save(s.sample_dir, melody()[: SR // 2])
    found = cliphub.items(tmp_path, s.clip_usage(), cliphub.sample_usage(s))
    kinds = {it.kind for it in found}
    assert {"bounce", "voice", "sample"} <= kinds
    loop = next(it for it in found if it.name == "loop")
    assert loop.bpm == "120" and loop.wave.startswith("M0 1L")
    assert [it.name for it in cliphub.view(found, "LOO")] == ["loop"]
    assert [it.kind for it in cliphub.view(found, "", "sample")] == ["sample"]
    longest = cliphub.view(found, "", "all", "longest")
    assert longest[0].name == "loop"
    assert cliphub.view(found, "", "bogus", "bogus") == cliphub.view(found)
    assert cliphub.counts(found)["all"] == len(found)


def test_the_page_lists_every_item_and_where_it_is_used(client):
    client.post(
        "/fragment/drums/sample", data={"voice": "kick", "clip": "loop"}, headers=HX
    )
    r = client.get("/view/clips")
    assert r.status_code == 200
    assert 'id="hub-list"' in r.text and 'id="hub-bench"' in r.text
    assert "in use: 1 drum row" in r.text
    assert 'data-note="/clip/loop.wav"' in r.text


def test_the_bench_is_never_empty_on_arrival(client):
    """No item in the URL: the bench opens the item this browser
    last heard or opened (the `hub_item` cookie, cliphub.js), while it is
    there, else the first row the list shows — never "Pick a clip"."""
    page = client.get("/view/clips").text
    assert 'data-ref="clip:loop"' in page.split('id="hub-bench"')[1][:200]
    assert "/hub/audio.wav?item=clip%3Aloop" in page
    client.cookies.set("hub_item", "clip%3Atune")
    page = client.get("/view/clips").text
    assert "/hub/audio.wav?item=clip%3Atune" in page
    assert 'aria-selected="true"' in page.split('data-ref="clip:tune"')[1][:120]
    client.cookies.set("hub_item", "clip%3Agone")  # gone: the first again
    assert "/hub/audio.wav?item=clip%3Aloop" in client.get("/view/clips").text
    # the list sorted another way opens its own first row
    page = client.get("/view/clips", params={"order": "kind"}).text
    assert "hub-pick" not in page and "/hub/audio.wav?item=" in page
    client.cookies.clear()


def test_the_bench_and_the_list_answer_with_the_hubs_url(client):
    r = client.get("/fragment/hub/bench", params={"item": "clip:loop"}, headers=HX)
    assert r.status_code == 200 and 'id="hub-bench"' in r.text
    assert r.headers["HX-Replace-Url"] == "/view/clips?item=clip%3Aloop"
    assert "/hub/audio.wav?item=clip%3Aloop" in r.text
    assert 'hx-post="/fragment/drums/sample"' in r.text  # the destination's own route
    r = client.get(
        "/fragment/hub/list", params={"find": "tun", "show": "voice"},
        headers={**HX, "HX-Current-URL": "http://testserver/view/clips?item=clip%3Aloop"},
    )  # fmt: skip
    assert r.headers["HX-Replace-Url"] == (
        "/view/clips?item=clip%3Aloop&find=tun&show=voice"
    )
    assert ">tune<" in r.text and ">loop<" not in r.text
    assert (
        client.get("/fragment/hub/bench", params={"item": "clip:nope"}).status_code
        == 422
    )


def test_the_player_hears_the_tune_it_asks_for(client):
    r = client.get("/fragment/hub/player", params={"item": "clip:tune", "semis": "5"})
    assert "semis=5" in r.text and "tuned" in r.text
    a = client.get("/hub/audio.wav", params={"item": "clip:tune"})
    b = client.get("/hub/audio.wav", params={"item": "clip:tune", "rate": "2"})
    assert a.status_code == b.status_code == 200 and a.content[:4] == b"RIFF"
    assert len(b.content) < len(a.content) * 0.6
    for bad in ("clip:../../state", "sample:zz", "stem:abcdef0123456789:m:vocals", ""):
        assert client.get("/hub/audio.wav", params={"item": bad}).status_code == 404


# --- tune: keep, update, undo ---------------------------------------------------------


def test_keep_makes_a_new_clip_and_never_writes_over_one(client, tmp_path):
    before = (tmp_path / "clips" / "tune.wav").read_bytes()
    r = client.post(
        "/hub/tune/keep", data={"item": "clip:tune", "semis": "-3"}, headers=HX
    )
    assert r.status_code == 200 and "item=clip%3Atune+tuned" in r.headers["HX-Push-Url"]
    assert (tmp_path / "clips" / "tune.wav").read_bytes() == before
    meta = next(c for c in list_clips(tmp_path) if c["name"] == "tune tuned")
    assert meta["tune"]["semis"] == -3 and meta["source"] == "voice take"
    assert (
        client.post(
            "/hub/tune/keep", data={"item": "clip:tune"}, headers=HX
        ).status_code
        == 422
    )


def test_update_writes_over_the_clip_and_the_hubs_undo_puts_it_back(client, tmp_path):
    path = tmp_path / "clips" / "loop.wav"
    before = path.read_bytes()
    r = client.post(
        "/hub/tune/update", data={"item": "clip:loop", "reverse": "on"}, headers=HX
    )
    assert r.status_code == 200 and path.read_bytes() != before
    assert client.app.state.svc.hub_undo_label() == "update “loop”"
    r = client.post("/hub/undo", headers=HX)
    assert r.status_code == 200 and path.read_bytes() == before
    assert "Undone" in r.headers["HX-Trigger"]
    assert client.post("/hub/undo", headers=HX).status_code == 422  # nothing left


def test_update_asks_first_naming_where_the_clip_is_used(client):
    client.post(
        "/fragment/drums/sample", data={"voice": "kick", "clip": "loop"}, headers=HX
    )
    r = client.get("/fragment/hub/bench", params={"item": "clip:loop"}, headers=HX)
    assert "hx-confirm=" in r.text and "used in: 1 drum row" in r.text


def test_a_sample_is_kept_as_a_clip(client, tmp_path):
    sid = sampler.save(_st(client).sample_dir, melody()[: SR // 2])
    r = client.post("/hub/keep", data={"item": f"sample:{sid}"}, headers=HX)
    assert r.status_code == 200
    assert any(
        c["source"] == "sampler sample" for c in list_clips(tmp_path) if "source" in c
    )


def test_open_in_lab_brings_a_clip_in_as_a_song(client):
    r = client.post("/hub/lab", data={"item": "clip:loop"}, headers=HX)
    assert r.status_code == 200
    loc = json.loads(r.headers["HX-Location"])
    assert loc["path"].startswith("/view/lab?source=")


# --- the plan --------------------------------------------------------------------


def test_a_hostile_plan_is_held_to_the_studios_names_and_ranges():
    raw = {
        "target": "symphony",
        "kit": "No Such Kit",
        "style": "<script>",
        "amount": 99,
        "swing": "lots",
        "transpose": -1e9,
        "fx": "../../etc",
        "sound": {"source": "library", "name": "No Such Pad"},
        "summary": "x" * 999,
        "why": ["a", 7, {"b": 1}] * 10,
        "loop_tempo": "yes",
    }
    plan, notes = clipmake.clean_plan(raw, clipmake.sound_exists)
    assert plan["target"] == "beat" and plan["kit"] == "own" and plan["style"] == "clip"
    assert plan["amount"] == 1.0 and plan["swing"] == 0.0 and plan["transpose"] == -24
    assert plan["fx"] == "keep" and plan["sound"] == {"source": "keep", "name": ""}
    assert (
        len(plan["summary"]) == clipmake.MAX_TEXT
        and len(plan["why"]) <= clipmake.MAX_WHY
    )
    assert plan["loop_tempo"] is False
    said = " ".join(notes)
    assert "No Such Kit" in said and "No Such Pad" in said
    assert clipmake.clean_plan("not a dict")[0]["target"] == "beat"


def test_a_real_name_is_kept_and_the_players_picks_win():
    kit = next(iter(DRUM_KITS))
    name = clipmake.sound_catalogue()["recipes"][0][1][0]
    plan, notes = clipmake.clean_plan(
        {"target": "notes", "kit": kit, "sound": {"source": "recipes", "name": name}},
        clipmake.sound_exists,
    )
    assert plan["kit"] == kit and plan["sound"] == {"source": "recipes", "name": name}
    assert not notes
    picked = clipmake.locked({"kit": "keep", "style": "auto", "transpose": "7"})
    assert picked == {"kit": "keep", "transpose": 7.0}
    merged = clipmorph.merge({"kit": kit, "transpose": 0}, picked)
    assert merged["kit"] == "keep" and merged["transpose"] == 7.0


def test_the_clip_grid_is_its_meta_then_measured_then_the_loop():
    x = drum_loop()
    g = clipmake.clip_grid(x, {"bpm": 120.0}, {}, 99.0)
    assert (g.bpm, g.bars, g.offset) == (120.0, 2, 0.0)
    g = clipmake.clip_grid(x, {}, {"bpm": 120.0, "bpm_conf": 0.9}, 99.0)
    assert g.bpm == 120.0 and "measured" in g.said
    g = clipmake.clip_grid(x, {}, {"bpm": 120.0, "bpm_conf": 0.01}, 99.0)
    assert g.bpm == 99.0 and "loop's" in g.said


# --- make, apply, undo --------------------------------------------------------------


def test_a_beat_from_the_clip_previews_then_applies_as_one_step(client):
    s = _st(client)
    arr0 = s.arrangement
    r = client.post(
        "/hub/make",
        data={"item": "clip:loop", "target": "beat", "kit": "own"},
        headers=HX,
    )
    assert r.status_code == 200 and "/preview/" in r.text and "kick" in r.text
    assert s.arrangement is arr0  # a preview writes nothing
    assert not list((s.data_dir / "clips").glob("loop *"))
    r = client.post(
        "/hub/apply",
        data={"item": "clip:loop", "plan": _plan(r), "seed": "0"},
        headers=HX,
    )
    assert r.status_code == 200 and "Applied" in r.headers["HX-Trigger"]
    a = s.arrangement
    assert ["kick", 0] in a.drums and a.drum_bars == 2
    cut = [n for n in a.drum_samples.values() if n.startswith("loop ")]
    assert cut, a.drum_samples  # the clip's own hits (those that stand alone)
    assert 'id="hub-list"' in r.text and "hub-undo" in r.text  # the list, out of band
    # the hub's Undo takes ALL of it back, the cut hits with it
    r = client.post("/hub/undo", headers=HX)
    assert r.status_code == 200
    assert s.arrangement == arr0 and not list((s.data_dir / "clips").glob("loop *"))


def test_the_drum_grids_own_ctrl_z_takes_an_applied_beat_back(client):
    s = _st(client)
    drums0 = list(s.arrangement.drums)
    plan = json.dumps({"target": "beat", "kit": "keep"})
    client.post("/hub/apply", data={"item": "clip:loop", "plan": plan}, headers=HX)
    assert s.arrangement.drums != drums0
    assert s.pop_pattern_undo() is None
    assert s.arrangement.drums == drums0


def test_a_named_kit_half_time_and_a_style_blend(client):
    s = _st(client)
    kit = next(iter(DRUM_KITS))
    plan = json.dumps(
        {
            "target": "beat",
            "kit": kit,
            "feel": "half-time",
            "style": "techno",
            "fx": "washy",
        }
    )
    r = client.post("/hub/apply", data={"item": "clip:loop", "plan": plan}, headers=HX)
    assert r.status_code == 200, r.text[:300]
    a = s.arrangement
    assert a.drum_bars == 4  # two bars, half-time
    assert {
        k: v for k, v in a.drum_params.items() if k in DRUM_KITS[kit]["params"]
    } == {k: dict(v) for k, v in DRUM_KITS[kit]["params"].items()}
    assert s.beat_style == "techno" and s.fx_drums and s.fx_drums[0]["kind"]


def test_undo_is_refused_once_the_session_moved_on(client):
    s = _st(client)
    plan = json.dumps({"target": "beat", "kit": "keep"})
    client.post("/hub/apply", data={"item": "clip:loop", "plan": plan}, headers=HX)
    client.post(
        "/fragment/drums/clear",
        headers={**HX, "HX-Current-URL": "http://testserver/view/drums"},
    )
    r = client.post("/hub/undo", headers=HX)
    assert r.status_code == 422 and "moved since" in r.text
    assert not s.arrangement.drums  # the later edit stands


def test_notes_follow_the_clips_melody_in_the_loops_key(client):
    s = _st(client)
    r = client.post(
        "/hub/make",
        data={
            "item": "clip:tune",
            "target": "notes",
            "notes_from": "melody",
            "snap": "none",
        },
        headers=HX,
    )
    assert r.status_code == 200 and "Followed its melody" in r.text, r.text[:400]
    patch0, cells0 = s.patch.to_json(), list(s.arrangement.cells)
    name = clipmake.sound_catalogue()["library"][0][1][0]
    plan = json.dumps(
        {"target": "notes", "notes_from": "melody", "snap": "loop",
         "sound": {"source": "library", "name": name}}
    )  # fmt: skip
    r = client.post("/hub/apply", data={"item": "clip:tune", "plan": plan}, headers=HX)
    assert r.status_code == 200, r.text[:400]
    a = s.arrangement
    assert a.cells and a.steps_per_bar == 16
    key = clipmake.key_classes(a.root, a.scale_name)
    from synthlab.sequencer import midi

    assert {midi(p) % 12 for p, _ in a.cells} <= key  # snapped into the loop's key
    assert s.patch.name == name
    # the note part is a loop too, its audio the clip it was made from, open
    # on the grid (loops.py): audio -> loop is the Loops list's one object
    loop = loops.load(s.data_dir, "tune notes")
    assert loop["take"] == "tune" and loop["lane"]["cells"] == a.cells
    assert s.loop_edit_of()["name"] == "tune notes"
    client.post("/hub/undo", headers=HX)
    assert s.patch.to_json() == patch0
    assert s.arrangement.cells == cells0
    assert not loops.exists(s.data_dir, "tune notes") and not s.loop_edit


def test_a_generator_writes_over_the_clips_key_and_the_clip_can_play_it(client):
    s = _st(client)
    plan = json.dumps({"target": "notes", "notes_from": "bass_root", "sound": "clip"})
    r = client.post("/hub/apply", data={"item": "clip:tune", "plan": plan}, headers=HX)
    assert r.status_code == 200, r.text[:400]
    assert s.arrangement.cells and s.patch.osc1 == "sampler"
    assert (s.sample_dir / f"{s.patch.sampler_sample}.wav").is_file()  # kept on Apply


def test_a_sound_from_the_clip_lands_on_synths(client):
    s = _st(client)
    plan = json.dumps({"target": "sound", "sound_mode": "whole"})
    r = client.post("/hub/apply", data={"item": "clip:tune", "plan": plan}, headers=HX)
    assert r.status_code == 200 and r.headers.get("HX-Push-Url") == "/view/editor"
    assert s.patch.osc1 == "sampler"


def test_a_preview_leaves_nothing_in_the_sample_store(client):
    s = _st(client)
    r = client.post(
        "/hub/make", data={"item": "clip:tune", "target": "sound"}, headers=HX
    )
    assert r.status_code == 200 and "/preview/" in r.text
    assert not list(s.sample_dir.glob("*.wav"))


def test_apply_refuses_what_is_not_a_plan(client):
    for plan in ("", "[1]", "{" * 9000, "nan"):
        r = client.post(
            "/hub/apply", data={"item": "clip:loop", "plan": plan}, headers=HX
        )
        assert r.status_code == 422
    r = client.post("/hub/apply", data={"item": "clip:nope", "plan": "{}"}, headers=HX)
    assert r.status_code == 422


def test_another_take_rolls_the_plan_shown_with_a_new_seed(client):
    plan = json.dumps({"target": "beat", "style": "techno", "density": "busier"})
    a = client.post(
        "/hub/make", data={"item": "clip:loop", "plan": plan, "seed": "1"}, headers=HX
    )
    b = client.post(
        "/hub/make", data={"item": "clip:loop", "plan": plan, "seed": "2"}, headers=HX
    )
    assert a.status_code == b.status_code == 200
    assert '"density": "busier"' in html.unescape(_plan(a))
    key = re.compile(r"/preview/(\w+)\.wav")
    assert key.search(a.text).group(1) != key.search(b.text).group(1)


# --- ✦ the morph: Gemini listens ----------------------------------------------------


class _FakeGemini:
    """Stands in for `genai.Client`: one JSON reply, and what it was asked."""

    def __init__(self, reply):
        self.asked: dict = {}
        text = reply if isinstance(reply, str) else json.dumps(reply)

        def generate_content(**kw):
            self.asked = kw
            usage = types.SimpleNamespace(
                prompt_token_count=700, candidates_token_count=90
            )
            return types.SimpleNamespace(text=text, usage_metadata=usage)

        self.models = types.SimpleNamespace(generate_content=generate_content)


@pytest.fixture
def gemini(monkeypatch):
    def use(reply):
        fake = _FakeGemini(reply)
        monkeypatch.setattr(takecoach, "client_for", lambda config: fake)
        monkeypatch.setattr(takecoach, "sdk_available", lambda: True)
        monkeypatch.setattr(
            takecoach,
            "load_config",
            lambda: AgentConfig(api_key="k-secret", source="env"),
        )
        return fake

    return use


def test_without_a_key_the_morph_says_what_it_needs(client):
    r = client.post(
        "/hub/make",
        data={"item": "clip:loop", "ai": "1", "describe": "darker"},
        headers=HX,
    )
    assert r.status_code == 422 and "GEMINI_API_KEY" in r.text
    page = client.get("/view/clips?item=clip:loop").text
    assert (
        "GEMINI_API_KEY" in page and "btn-ai" in page and 'data-cmd-group="AI"' in page
    )


def test_the_morph_listens_and_its_plan_is_checked(client, gemini):
    kit = list(DRUM_KITS)[-1]
    fake = gemini(
        {"target": "beat", "kit": kit, "style": "made-up", "feel": "half-time",
         "summary": "Half-time with the k-secret kit", "why": ["heavy kick on 1"]}
    )  # fmt: skip
    r = client.post(
        "/hub/make",
        data={"item": "clip:loop", "ai": "1", "describe": "darker and half-time",
              "fx": "tape_lofi"},
        headers=HX,
    )  # fmt: skip
    assert r.status_code == 200, r.text[:400]
    text = html.unescape(r.text)
    assert "Half-time with the" in text and "k-secret" not in text  # scrubbed
    assert "made-up" in text  # refused, and said so
    plan = json.loads(_plan(r))
    assert (
        plan["kit"] == kit and plan["feel"] == "half-time" and plan["style"] == "clip"
    )
    assert plan["fx"] == "tape_lofi"  # the player's pick wins
    contents = fake.asked["contents"]
    words = contents[0].text
    assert "darker and half-time" in words and kit in words and "k-secret" not in words
    audio = contents[1].inline_data
    assert audio.mime_type == "audio/wav"
    with wave.open(io.BytesIO(audio.data)) as w:
        assert w.getnchannels() == 1 and w.getframerate() == clipmorph.MORPH_RATE
        assert w.getnframes() <= clipmorph.MORPH_SECONDS * clipmorph.MORPH_RATE
    cfg = fake.asked["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema["properties"]["kit"]["enum"][0] == "own"


def test_a_broken_answer_is_a_toast_with_the_key_scrubbed(client, gemini):
    gemini("not json k-secret")
    r = client.post(
        "/hub/make",
        data={"item": "clip:loop", "ai": "1", "describe": "busier"},
        headers=HX,
    )
    assert r.status_code == 422 and "Gemini" in r.text and "k-secret" not in r.text


def test_the_morph_needs_words(client, gemini):
    gemini({"target": "beat", "summary": "x"})
    r = client.post("/hub/make", data={"item": "clip:loop", "ai": "1"}, headers=HX)
    assert r.status_code == 422 and "Say what" in r.text


def test_the_audio_sent_is_short_mono_and_from_the_first_sound():
    x = np.concatenate([np.zeros((SR, 2)), drum_loop(bars=8)])
    data = clipmorph.audio_wav(x)
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
        assert w.getnframes() == int(clipmorph.MORPH_SECONDS * clipmorph.MORPH_RATE)
    first = np.frombuffer(frames, "<i2")[:200]
    assert np.any(first != 0)  # the leading second of silence is skipped
