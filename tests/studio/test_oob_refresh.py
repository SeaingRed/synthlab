"""No stale regions, checked by behaviour.

One page draws the same state in several places, and a route that changed
it used to refresh only what it targeted: a captured scene left Perform's
launcher saying "No scenes yet", an FX add left the chain bar and the reset
menu behind. Now each region in `service.REGIONS` declares the state it
shows (`watch`), and the route wrapper appends every region of the
requesting page whose watch moved (`StudioService.refresh`).

The contract, for every POST route (`CASES`, one representative request
per route — `test_every_post_route_has_a_case` keeps it complete):

- render every region of the requesting page before and after the request;
- the response carries, out of band, exactly the regions whose HTML changed
  — minus the ones the route suppresses (the form that posted, a launcher
  perform.js shows "queued") and any it draws itself (its main target) —
  and each one is the current render: nothing stale, nothing sent for
  nothing;
- a whole-page answer, a navigation, JSON or a toast carries none;
- and for every region on every page, whenever its HTML changed its
  `watch` changed too (the request is the fuzz: every route in the table
  mutates something).
"""

from __future__ import annotations

import io
import json
import math
import re
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from synthlab import midifile
from synthlab.studio import keytakes, loops
from synthlab.studio.agent.client import Reply
from synthlab.studio.clips import save_clip, write_meta
from synthlab.studio.drumlab import DRUM_PARAMS
from synthlab.studio.factory import FACTORY
from synthlab.studio.library import by_family
from synthlab.studio.server import create_app
from synthlab.studio.service import PAGE_REGIONS, REGIONS
from synthlab.synth import Patch
from tests.studio.test_agent import FakeClient
from tests.studio.test_agent import calls as agent_calls
from tests.studio.test_loop_coach import seed as coach_seed
from tests.studio.test_perform_view import _pool_beat
from tests.studio.test_routes import EXPECTED_ROUTES, LOCK_FREE
from tests.studio.test_song_routes import use_song


def _hx(view: str) -> dict:
    return {"HX-Request": "true", "HX-Current-URL": f"http://testserver/view/{view}"}


def _mid() -> bytes:
    """A MIDI file with one note the default grid has a row for."""
    track = midifile.Track(notes=[midifile.Note(1.0, 1.0, 38, 100)])  # D2
    return midifile.write(midifile.MidiFile(tracks=[track]))


def _wav(channels: int = 2, frames: int = 4410) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(44100)
        tone = (np.sin(np.arange(frames * channels) * 0.05) * 8000).astype("<i2")
        w.writeframes(tone.tobytes())
    return buf.getvalue()


def _oob_ids(html: str) -> set[str]:
    """ids of every element in `html` that carries hx-swap-oob."""
    out = set()
    for tag in re.findall(r"<[a-z]+\b[^>]*>", html):
        m = re.search(r'\bid="([^"]+)"', tag)
        if m and "hx-swap-oob" in tag:
            out.add(m.group(1))
    return out


def _request(view: str) -> Request:
    headers = [(b"hx-current-url", f"http://testserver/view/{view}".encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


# the Synths drawings are one template fed from the page's own svg keys
_VIZ = {
    "filter-response-wrap": "filter_response",
    "env-amp-wrap": "env_amp",
    "env-filter-wrap": "env_filter",
}


def _page(svc, view: str) -> dict[str, str]:
    """Every region of `view`'s page as the page itself draws it: the
    template over the page's `page_context` (not the region's own lean
    context — test_routes holds those to this), each inner region cut out
    of its outer one, so each string is what that region alone shows."""
    names = PAGE_REGIONS[view]
    need = frozenset().union(*(REGIONS[n].needs for n in names), {"resets"})
    with svc.state.lock:
        ctx = svc.page_context(_request(view), view, need=need)
    out = {}
    for n in names:
        extra = {"viz_id": n, "svg": ctx[_VIZ[n]]} if n in _VIZ else {}
        tpl = svc.templates.get_template(REGIONS[n].template)
        out[n] = tpl.render({**ctx, **extra, "oob": False})
    for n in names:
        if (outer := REGIONS[n].inside) in out:
            out[outer] = out[outer].replace(out[n], f"<{n}/>")
    return out


def _watches(svc, view: str) -> dict:
    with svc.state.lock:
        return svc.watch_page(view)


# --- setups ---------------------------------------------------------------------


def _st(client):
    return client.app.state.studio


def _nothing(client) -> None:
    pass


def _clip(client) -> None:
    save_clip(_st(client).data_dir, "hit", np.zeros((4410, 2)))


def _tone(client) -> None:
    t = np.sin(np.arange(22050) * 2 * np.pi * 220 / 44100) * 0.5
    save_clip(_st(client).data_dir, "tone", np.column_stack([t, t]))


_HUB_BEAT = json.dumps({"target": "beat", "style": "techno", "kit": "Trance"})


def _hub_applied(client) -> None:
    """The clip hub has applied a beat made from "tone" (its Undo is live)."""
    _tone(client)
    r = client.post(
        "/hub/apply",
        data={"item": "clip:tone", "plan": _HUB_BEAT},
        headers=_hx("clips"),
    )
    assert r.status_code == 200, r.text[:300]


def _hub_sampled(client) -> None:
    from synthlab import sampler

    t = np.sin(np.arange(22050) * 2 * np.pi * 330 / 44100) * 0.5
    sampler.save(_st(client).sample_dir, np.column_stack([t, t]))


def _keys_take(client) -> None:
    """An unkept keys take (keytakes.py): a tone with two notes."""
    _tone(client)
    d = _st(client).data_dir
    (d / "clips" / "tone.wav").rename(d / "clips" / "keys-t.wav")
    (d / "clips" / "tone.meta.json").unlink()
    meta = {"source": "keys", "bpm": 124, "bars": 1, "kept": False, "at": 1.0}
    write_meta(d, "keys-t", meta)
    keytakes.write_notes(d, "keys-t", {"notes": [[0, 1, 33, 0.8], [2, 1, 36, 1]]})


def _cleaned_take(client) -> None:
    _keys_take(client)
    client.post("/take/keys/clean", data={"name": "keys-t", "ai": "0"})


def _a_loop(client) -> None:
    s = _st(client)
    loops.save(s.data_dir, "riff", s.arrangement, s.sound_payload())


def _a_beat(client) -> None:
    """A beat loop (the Loops panel's kind "beat"): the bank's first slot."""
    s = _st(client)
    loops.save_beat(s.data_dir, "techno", s.drum_bank[0], s.arrangement)


def _beat_on_grid(client) -> None:
    client.post("/fragment/drums/bank/load/1", headers=_hx("perform"))


def _recorded(client) -> None:
    """One ⏺ Rec: the take keys-r and its loop, with its notes as played."""
    data = {"name": "keys-r", "bpm": "124", "bars": "1"}
    data["notes"] = "[[0.02, 1, 33, 0.8], [0.96, 1, 36, 1]]"
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    client.post("/take/keys", data=data, files=files, headers=_hx("editor"))


def _editing(client) -> None:
    """keys-r open on Perform's grid (`/loop/edit`)."""
    _recorded(client)
    client.post("/loop/edit", data={"name": "keys-r"}, headers=_hx("perform"))


def _hub_sample(client) -> dict:
    sid = next(p.stem for p in _st(client).sample_dir.glob("*.wav"))
    return {"item": f"sample:{sid}"}


def _sampled(client) -> None:
    _tone(client)
    client.post("/fragment/sampler/clip", data={"name": "tone"}, headers=_hx("editor"))


def _chopped(client) -> None:
    _sampled(client)
    client.post("/fragment/sampler/chop", data={"mode": "8"}, headers=_hx("editor"))


def _two_tones(client) -> None:
    _tone(client)
    t = np.sin(np.arange(22050) * 2 * np.pi * 110 / 44100) * 0.5
    save_clip(_st(client).data_dir, "low", np.column_stack([t, t]))


def _zoned(client) -> None:
    """A sampler of two zones: "low" (A2) under "tone" (A3)."""
    _two_tones(client)
    client.post(
        "/fragment/sampler/zones/map",
        data={"names": "tone\nlow", "mode": "pitch"},
        headers=_hx("editor"),
    )


def _rooted_off(client) -> None:
    """A sampler whose root is not the tone's detected A3."""
    _sampled(client)
    client.post("/fragment/patch", data={"sampler_root": "48"}, headers=_hx("editor"))


def _song(client) -> None:
    """The song routes' fixture song (fixed ids: sections s1/s2, tracks
    td/t1/t2/ta, patterns pd/p1/p2/p3 — p3 unused, ta empty)."""
    use_song(client)


def _song_loop_kit(client) -> None:
    """The fixture song, and a loop whose kit (Trance's voices) is not its
    drums track's: → Song's kit offer has something to do."""
    use_song(client)
    client.post("/fragment/drums/kit", data={"kit": "Trance"}, headers=_hx("drums"))


def _song_mixed(client) -> None:
    """The fixture song with a return ("0a0b0c0d") that t1 sends to, and
    roles on its two synth tracks (Balance goes by role)."""
    use_song(client)
    s = client.app.state.studio.song
    s["returns"] = [
        {
            "id": "0a0b0c0d",
            "name": "A Reverb",
            "fx": [{"kind": "reverb", "params": {"mix": 1.0}}],
        }
    ]
    for t in s["tracks"]:
        if t["id"] == "t1":
            t["sends"] = {"0a0b0c0d": 0.4}
            t["role"] = "bass"
        if t["id"] == "t2":
            t["role"] = "lead"
    use_song(client, s)


def _song_long(client) -> None:
    """The fixture song with a whole stem on "ta" (long audio; its source
    is not in the Lab, so it is drawn "missing" — the page's own case)."""
    use_song(client)
    client.app.state.studio.song["long"] = [
        {"id": "lg", "track_id": "ta", "source": "0123456789abcdef", "name": "Vox"}
    ]
    use_song(client, client.app.state.studio.song)


def _song_drawer(client) -> None:
    use_song(client)
    client.post("/song/drawer", data={"pattern_id": "p1"}, headers=_hx("song"))


def _beat_drawer(client) -> None:
    """The fixture song with the drawer open on its beat (a drums cell)."""
    use_song(client)
    client.post(
        "/song/drawer",
        data={"section_id": "s1", "track_id": "td"},
        headers=_hx("song"),
    )


def _song_take(client) -> None:
    use_song(client)
    client.post(
        "/song/take",
        data={"section_id": "s1", "track_id": "ta", "at_bar": "0"},
        files={"file": ("t.wav", _wav(), "audio/wav")},
        headers=_hx("song"),
    )


def _audio_drawer(client) -> None:
    """A take on the audio track "ta" in s1, and the drawer open on that
    row: its rack and its lanes."""
    _song_take(client)
    client.post(
        "/song/drawer",
        data={"section_id": "s1", "track_id": "ta"},
        headers=_hx("song"),
    )


def _song_recorded(client) -> None:
    use_song(client)
    _record(client, "t0")


def _record(client, take: str):
    events = [{"midi": 45, "t": 0.5, "vel": 0.7}]
    data = {"events": json.dumps(events), "take": take, "t0": "0", "t1": "2"}
    data |= {"section_id": "s2", "track_id": "t2"}
    return client.post("/song/pattern/p2/record", data=data, headers=_hx("song"))


def _song_edited(client) -> None:
    """One song edit on the ring: Lead's cell in Intro filled."""
    use_song(client)
    data = {"section_id": "s1", "track_id": "t2", "pattern_id": "p3"}
    client.post("/song/cell", data=data, headers=_hx("song"))


def _song_undone(client) -> None:
    _song_edited(client)
    client.post("/song/undo", headers=_hx("song"))


def _migration_note(client) -> None:
    use_song(client)
    s = _st(client)
    s.song = {**s.song, "migration_note": "Your scenes are now song sections."}


def _fx(*kinds: str):
    def setup(client) -> None:
        for kind in kinds:
            client.post(
                "/fragment/fx/add",
                data={"scope": "layer", "kind": kind},
                headers=_hx("editor"),
            )

    return setup


def _fx_baselined(*kinds: str):
    """FX already in the rack and baselined: the next edit lights "changed"."""

    def setup(client) -> None:
        _fx(*kinds)(client)
        _st(client).mark_baseline("fx", "saved")

    return setup


def _slot_a(client) -> None:
    _st(client).slot_a = Patch(name="bright", cutoff=6000.0, attack=0.4)


def _preset(client) -> None:
    client.post("/fragment/preset/save", data={"name": "zap"}, headers=_hx("editor"))


def _loaded(client) -> None:
    client.post(
        "/fragment/factory/load",
        data={"name": FACTORY[3]["name"]},
        headers=_hx("editor"),
    )


def _panel_preset(client) -> None:
    client.post(
        "/fragment/panel/preset/save",
        data={"group": "Filter", "name": "f1"},
        headers=_hx("editor"),
    )


def _edited(client) -> None:
    client.post("/fragment/patch", data={"cutoff": "700"}, headers=_hx("editor"))


def _generation(client) -> None:
    client.post("/fragment/breed/generation", data={}, headers=_hx("breed"))


def _layered(client) -> None:
    """A deep litter, over a session whose other lanes hold notes: "Use
    with its layers" replaces them, "Use sound" keeps them."""
    s = _st(client)
    s.layers[1] = {
        **s.layers[1],
        "patch": s.patch.to_json(),
        "cells": [["A1", 0]],
    }
    client.post(
        "/fragment/breed/generation", data={"depth": "deep"}, headers=_hx("breed")
    )


def _starred(client) -> None:
    _generation(client)
    tid = _st(client).breed_tiles[2]["id"]
    client.post("/fragment/breed/star", data={"id": tid}, headers=_hx("breed"))
    _generation(client)


def _beat(client) -> None:
    """The House slot on the grid: a beat for a generator to work on."""
    client.post("/fragment/drums/bank/load/0", headers=_hx("drums"))


def _loop_chords(client) -> None:
    """The working loop with a chord track of its own (Perform's chord lane)."""
    client.post(
        "/fragment/chords/track",
        data={"op": "roll", "seed": "3"},
        headers=_hx("perform"),
    )


def _hit_beat(client) -> None:
    """... with per-hit data on the hat row."""
    _beat(client)
    s = _st(client)
    hat = [c for c in s.arrangement.drums if c[0] == "hat"]
    s.arrangement = replace(s.arrangement, drum_hits={f"hat.{hat[0][1]}": {"p": 0.5}})


def _libbeat(client) -> None:
    client.post("/fragment/drums/lib/save", data={"name": "mine"}, headers=_hx("drums"))


def _pool(client) -> None:
    _pool_beat(_st(client), 2)


def _coach_loop(client) -> None:
    """A typical loop (test_loop_coach.seed): off-key notes, flat
    velocities, a note off the roll's rows, a bass lane, a beat."""
    coach_seed(_st(client))


def _coach_proposal(client) -> None:
    _coach_loop(client)
    client.post(
        "/fragment/coach/propose", data={"what": "fix:all"}, headers=_hx("perform")
    )


def _coach_accepted(client) -> None:
    _coach_proposal(client)
    client.post("/fragment/coach/accept", headers=_hx("perform"))


def _coach_entry(client) -> dict:
    thread = _st(client).coach["thread"]
    return {"entry": str(next(e["id"] for e in thread if e["kind"] == "accepted"))}


def _section(client) -> None:
    s = _st(client)
    s.arrangement = replace(s.arrangement, cells=[["A1", 0]], drums=[["kick", 0]])
    s.loop_to_section("intro")
    s.arrangement = replace(s.arrangement, cells=[], drums=[])


def _slotted(client) -> None:
    """Perform's launcher: "riff" in lane 1's slot of scene 1, the beat
    "techno" in the drums' (routes/slots.py)."""
    _a_loop(client)
    _a_beat(client)
    s = _st(client)
    s.set_slot(0, 0, "riff")
    s.set_slot(s.DRUM_LANE, 0, "techno")


def _scened(client) -> None:
    """Scene 1 launched: both its slots play (open on their lanes)."""
    _slotted(client)
    client.post(
        "/fragment/perform/scene/launch", data={"row": "0"}, headers=_hx("perform")
    )


def _an_idea(client) -> None:
    """Scene 1 kept as the idea "verse idea"."""
    _slotted(client)
    s = _st(client)
    loops.save_idea(s.data_dir, "verse idea", s.slot_rows()[0]["slots"], s.arrangement)


def _take(client) -> None:
    client.post(
        "/voice/take",
        files={"file": ("t.wav", _wav(channels=1, frames=22050), "audio/wav")},
        headers=_hx("voice"),
    )


def _vocoded(client) -> None:
    _take(client)
    client.post("/voice/render", data={"bands": "8"}, headers=_hx("voice"))


def _booth_heard(client) -> None:
    """A take treated in the booth: an audition held to keep."""
    _take(client)
    client.post("/voice/booth/render", data={"treatment": "adt"}, headers=_hx("voice"))


def _booth_on_song(client) -> None:
    _song(client)
    _booth_heard(client)


def _vocal_on_song(client) -> None:
    """The fixture song, and a live vocal take in the clip store."""
    _song(client)
    _tone(client)
    d = _st(client).data_dir
    (d / "clips" / "tone.wav").rename(d / "clips" / "vox.wav")


def _vocal_files(c) -> dict:
    return {
        "file": ("vocal.wav", _wav(), "audio/wav"),
        "dry": ("dry.wav", _wav(), "audio/wav"),
    }


def _lab_wav() -> bytes:
    """A second of a tone: a song for the Sample Lab's routes."""
    from synthlab.audio import wav_bytes

    t = np.arange(44100) / 44100
    return wav_bytes(np.column_stack([np.sin(2 * np.pi * 220 * t)] * 2) * 0.5)


def _lab_song(client) -> None:
    client.post(
        "/lab/upload",
        files={"file": ("s.wav", _lab_wav(), "audio/wav")},
        headers=_hx("lab"),
    )


def _lab_source(client) -> dict:
    from synthlab.studio import sources

    return {"source": sources.list_sources(client.app.state.svc.data_dir)[0]["id"]}


def _lab_bars(client) -> dict:
    """The Lab's song on a typed grid with a one-bar region: what the drum
    actions need (a second of a tone has no tempo of its own)."""
    from synthlab.studio import sources

    src = _lab_source(client)
    sources.update_meta(
        client.app.state.svc.data_dir,
        src["source"],
        lambda m: m["user"].update(bpm=240.0, offset=0.0),
    )
    client.post(
        "/lab/region",
        data={**src, "mode": "bars", "bar": "1", "bars": "1"},
        headers=_hx("lab"),
    )
    return src


def _lab_chop(client) -> dict:
    """The Lab's song with one chop cut in it: {source, chop}."""
    from synthlab.studio import sources

    src = _lab_source(client)
    client.post(
        "/lab/chop", data={**src, "op": "split", "at": "0.2"}, headers=_hx("lab")
    )
    meta = sources.read_meta(client.app.state.svc.data_dir, src["source"])
    return {**src, "chop": meta["chops"][0]["id"]}


def _lab_kept(client) -> None:
    """The Lab's song with a chop kept as the clip "chop 1"."""
    _lab_song(client)
    r = client.post("/lab/chop/keep", data=_lab_chop(client), headers=_hx("lab"))
    assert r.headers["X-Clip-Name"] == "chop 1"


def _lab_and_song(client) -> None:
    _lab_song(client)
    use_song(client)


def _song_with_clip(client) -> None:
    use_song(client)
    save_clip(client.app.state.svc.state.data_dir, "loop", np.full(4410, 0.2))


def _song_placed(client) -> None:
    """The fixture song with the clip "loop" placed as a take in Intro."""
    _song_with_clip(client)
    client.post(
        "/song/section/s1/clip",
        data={"clip": "loop", "at_bar": "0"},
        headers=_hx("song"),
    )


def _lane2(client) -> None:
    """Lane 2 holds a sound (the launcher's slot of it, empty)."""
    s = _st(client)
    s.layers[1] = {**s.layers[1], "patch": s.patch.to_json()}


# a take's (and a capture's) fields: one note, 124 bpm, a bar
_TAKE = {
    "name": "keys-20261108-101010",
    "bpm": "124",
    "bars": "1",
    "notes": "[[0, 1, 57, 0.8]]",
}
# free record's shaper: the free take keys-f as shaped (routes/freerec.py)
_SHAPED = {
    "take": "keys-f",
    "played": "[[0, 0.5, 57, 0.8], [1.1, 0.4, 60, 0.7]]",
    "bpm": "118",
    "bars": "1",
    "steps": "16",
    "strength": "50",
    "swing": "0",
}


def _free_take(client) -> None:
    """A free take, keys-f: free record's (no loop yet)."""
    data = {"name": "keys-f", "bpm": "124", "bars": "1"}
    data["notes"] = "[[0, 0.5, 57, 0.8], [1.1, 0.4, 60, 0.7, 1]]"
    files = {"file": ("take.wav", _wav(), "audio/wav")}
    client.post("/take/keys/free", data=data, files=files, headers=_hx("perform"))


def _layer2(client) -> None:
    client.post("/fragment/layer/select", data={"index": "1"}, headers=_hx("editor"))
    client.post("/fragment/layer/select", data={"index": "0"}, headers=_hx("editor"))


def _agent(client) -> None:
    """A scripted model (test_agent.FakeClient): one round of tool calls
    that moves the patch, a layer and the master FX, then a summary."""
    fake = FakeClient(
        [
            agent_calls(
                ("set_params", {"params": {"cutoff": 700, "unison": 3}}),
                ("add_layer", {"role": "pad"}),
                ("set_fx_chain", {"scope": "master", "units": [{"kind": "reverb"}]}),
            ),
            Reply("Darker, with a pad under it."),
        ]
    )
    client.app.state.svc.agent.client_factory = lambda cfg: fake


def _agent_turned(client) -> None:
    _agent(client)
    client.post(
        "/fragment/agent/turn", data={"message": "darker"}, headers=_hx("editor")
    )


# --- data needing the app -------------------------------------------------------


def _first_tile(client) -> dict:
    return {"id": _st(client).breed_tiles[0]["id"]}


def _grid_post(client) -> dict:
    a = _st(client).arrangement
    return {
        "bars": str(a.bars),
        "steps": str(a.steps_per_bar),
        "root": a.root,
        "scale_name": a.scale_name,
        "swing": str(a.swing),
        "arp_mode": a.arp_mode,
        "arp_octaves": str(a.arp_octaves),
        "cell.A1.1": "on",
    }


def _library_name(client) -> dict:
    _family, sounds = by_family()[0]
    return {"name": sounds[0]["name"]}


@dataclass
class Case:
    path: str  # the route's path as the inventory lists it
    view: str  # the page the request comes from
    data: dict | Callable = field(default_factory=dict)
    setup: Callable = _nothing
    url: str | None = None  # the concrete path, when it differs
    suppressed: frozenset[str] = frozenset()
    files: Callable | None = None

    @property
    def id(self) -> str:
        return f"{self.url or self.path}@{self.view}"


def C(path, view, data=None, setup=_nothing, url=None, suppressed=(), files=None):
    return Case(path, view, data or {}, setup, url, frozenset(suppressed), files)


_KICK = DRUM_PARAMS[0][1][1]["name"]  # a kick parameter the panels post
# a drawn wavetable's post: two frames of 64 points (wavetables.parse_drawing)
_DRAWN = ";".join(
    ",".join(f"{math.sin(2 * math.pi * k * i / 64):.3f}" for i in range(64))
    for k in (1, 3)
)

CASES: list[Case] = [
    # --- Synths ---
    C("/fragment/patch", "editor", {"cutoff": "900"}),
    C("/fragment/patch", "editor", {"attack": "0.3", "filter_decay": "0.9"}),
    C("/fragment/arrangement", "editor", {"bpm": "124", "root": "C9"}),
    # a real key change (ROOTS has no C9): the chord bar follows the key
    C("/fragment/arrangement", "editor", {"root": "E2", "scale_name": "dorian"}),
    # the phrase pill and a plain bpm edit: the sound bar that posted them
    # keeps what was clicked or typed
    C("/fragment/arrangement", "editor", {"phrase": "riff"}, suppressed={"sound-bar"}),
    # Beat ▾ and its kit post themselves: a pick moves the loop's beat (and
    # the sound bar comes back by its watch, the pick undone in its select)
    C("/fragment/beat/pick", "editor", {"beat_pick": "style:techno:7"}),
    C("/fragment/beat/pick", "editor", {"beat_pick": "bank:2"}),
    C("/fragment/beat/pick", "editor", {"beat_kit": "Trance"}),
    C("/fragment/beat/pick", "perform", {"beat_pick": "bank:2"}),
    C("/fragment/arrangement", "perform", _grid_post, suppressed={"transport"}),
    C("/fragment/chords/grid", "editor", {"code": "Am F C G"}),
    C("/fragment/chords/code", "editor", {"code": "Am F"}),
    C("/fragment/morph", "editor", {"slot": "a", "t": "0.6"}, _slot_a),
    C("/fragment/panel/reset", "editor", {"group": "Filter"}, _edited),
    C("/fragment/panel/dice", "editor", {"group": "Filter"}),
    C("/fragment/mod/mix", "editor", {"name": "wobble"}),
    C(
        "/fragment/curve/{param}",
        "editor",
        {"x": "30", "y": "20"},
        url="/fragment/curve/cutoff",
    ),
    C(
        "/fragment/curve/{param}/clear",
        "editor",
        setup=lambda c: c.post(
            "/fragment/curve/cutoff", data={"x": "30", "y": "20"}, headers=_hx("editor")
        ),
        url="/fragment/curve/cutoff/clear",
    ),
    C(
        "/fragment/curve/{param}/set",
        "editor",
        {"points": "[[0, 0.5], [0.5, 1.0], [1, 0.5]]"},
        url="/fragment/curve/pitch_bend/set",
    ),
    C("/fragment/bend-range", "editor", {"value": "12"}),
    C("/fragment/ab/{action}", "editor", url="/fragment/ab/slot_a"),
    # the Sound agent: every answer is the whole page (or a toast)
    C("/fragment/agent/turn", "editor", {"message": "darker"}, _agent),
    C("/fragment/agent/turn", "editor", {"message": "darker"}),  # no key: a toast
    C("/fragment/agent/undo", "editor", setup=_agent_turned),
    C("/fragment/agent/ab", "editor", setup=_agent_turned),
    C("/fragment/agent/apply", "editor", {"target": "new"}, _agent_turned),
    C("/fragment/agent/budget", "editor", setup=_agent_turned),
    C("/fragment/ab/{action}", "editor", url="/fragment/ab/copy_b"),
    C("/fragment/layer/select", "editor", {"index": "1"}),
    C("/fragment/layer/mute", "editor", {"index": "0"}, _layer2),
    C("/fragment/layer/solo", "editor", {"index": "1"}, _layer2),
    # the level slider posted it: its strip is not redrawn under the hand
    C(
        "/fragment/layer/gain",
        "editor",
        {"index": "1", "gain": "0.5"},
        _layer2,
        suppressed={"layer-strip"},
    ),
    C("/fragment/layer/follow", "editor", {"index": "1", "octave": "-1"}, _layer2),
    C("/fragment/layer/focus", "editor", {"on": "1"}),
    C("/fragment/layer/add", "editor"),
    # Perform's lane switch: a lane with its own grid, and the page stays
    C("/fragment/layer/add", "perform", {"own": "1"}),
    C("/fragment/layer/select", "perform", {"index": "1"}, _layer2),
    C("/fragment/layer/follow", "perform", {"index": "1", "on": "0"}, _layer2),
    C("/fragment/layer/clear", "editor", {"index": "1"}, _layer2),
    C(
        "/fragment/macro/{index}",
        "editor",
        {"value": "0.8"},
        lambda c: c.post(
            "/fragment/macro/0/assign",
            data={"param": "cutoff", "amt": "0.5"},
            headers=_hx("editor"),
        ),
        url="/fragment/macro/0",
    ),
    C(
        "/fragment/macro/{index}/assign",
        "editor",
        {"param": "cutoff", "amt": "0.5"},
        url="/fragment/macro/0/assign",
    ),
    C("/fragment/xy", "editor", {"x": "0.3", "y": "0.7"}),
    C("/fragment/xy/center", "editor"),
    C("/fragment/xy/axes", "editor", {"xy_x": "osc1_level", "xy_y": "osc2_level"}),
    C("/fragment/xy/gesture", "editor", {"points": "[[0, 0.2, 0.4], [0.5, 0.6, 0.1]]"}),
    C("/fragment/fx/add", "editor", {"scope": "layer", "kind": "reverb"}),
    C("/fragment/fx/add", "editor", {"scope": "master", "kind": "drive"}),
    C("/fragment/fx/chain", "editor", {"scope": "layer", "chain": "phone_mic"}),
    C("/fragment/fx/chain", "editor", {"scope": "master", "chain": "washy"}),
    C("/fragment/fx/chain", "drums", {"scope": "drums", "chain": "tape_lofi"}),
    C("/fragment/fx/remove", "editor", {"scope": "layer", "index": "0"}, _fx("reverb")),
    C(
        "/fragment/fx/move",
        "editor",
        {"scope": "layer", "index": "0", "dir": "1"},
        _fx("reverb", "drive"),
    ),
    C(
        "/fragment/fx/set",
        "editor",
        {"scope": "layer", "index": "0", "name": "mix", "value": "0.7"},
        _fx("reverb"),
    ),
    C("/fragment/preset/save", "editor", {"name": "zap"}),
    C("/fragment/preset/rename", "editor", {"name": "zap", "new": "zip"}, _preset),
    C("/fragment/preset/load", "editor", {"name": "zap"}, _preset),
    C("/fragment/preset/delete", "editor", {"name": "zap"}, _preset),
    C("/fragment/preset/star", "editor", {"name": "zap"}, _preset),
    C("/fragment/factory/load", "editor", {"name": FACTORY[2]["name"]}),
    C("/fragment/restore_prev", "editor", setup=_loaded),
    C("/fragment/panel/preset/save", "editor", {"group": "Filter", "name": "f1"}),
    C(
        "/fragment/panel/preset/load",
        "editor",
        {"group": "Filter", "name": "f1"},
        _panel_preset,
    ),
    C("/fragment/library/load", "library", _library_name),
    C("/fragment/recipe/load", "library", {"name": "Long Cinema Pad"}),
    C("/fragment/recipe/load", "editor", {"name": "Sine Sub"}),
    C("/fragment/undo", "editor", setup=_edited),
    C("/fragment/reset", "editor", {"scope": "sound", "mode": "defaults"}, _edited),
    C("/fragment/preview", "editor", {"view": "editor"}),
    C("/clip/bounce", "editor", {"name": "b1"}),
    C("/fragment/clip/delete", "editor", {"name": "hit"}, _clip),
    C("/fragment/clip/rename", "editor", {"name": "hit", "new": "thud"}, _clip),
    # the sampler: making one is a load (a whole page); chops are patch edits
    C("/fragment/sampler/clip", "editor", {"name": "tone"}, _tone),
    C("/fragment/sampler/clip", "perform", {"name": "tone"}, _tone),
    C("/fragment/sampler/clip", "editor", {"name": "hit"}, _clip),  # silent: toast
    C("/fragment/sampler/resample", "editor", {"source": "phrase"}),
    # your own wavetable — made from a clip or drawn, a patch
    # edit answered with the whole page (the mod matrix's mixes do the same)
    C(
        "/fragment/wavetable/clip",
        "editor",
        {"source": "clip:tone", "frames": "8"},
        _tone,
    ),
    C("/fragment/wavetable/clip", "editor", {"source": "clip:gone"}),  # a toast
    C("/fragment/wavetable/draw", "editor", {"points": _DRAWN, "name": "wave"}),
    # ● Record sample: a mic take kept as a clip, then loaded like one
    C(
        "/fragment/sampler/record",
        "editor",
        {"bars": "1"},
        files=lambda c: {"file": ("blob", _wav(channels=1, frames=88200), "audio/wav")},
    ),
    C(
        "/fragment/sampler/record",
        "editor",
        {"bars": "3"},  # not a length the panel offers: toast
        files=lambda c: {"file": ("blob", _wav(channels=1), "audio/wav")},
    ),
    # a start-point drag moves the waveform's markers: the block comes back
    C("/fragment/patch", "editor", {"sampler_start": "0.3"}, _sampled),
    C("/fragment/sampler/chop", "editor", {"mode": "16"}, _sampled),
    C("/fragment/sampler/chop", "editor", {"mode": "clear"}, _chopped),
    C(
        "/fragment/sampler/slice",
        "editor",
        {"index": "2", "action": "reverse"},
        _chopped,
    ),
    C(
        "/fragment/sampler/slice",
        "editor",
        {"index": "1", "action": "nudge_right"},
        _chopped,
    ),
    # a dragged slice marker: the panel comes back with it where it landed
    C(
        "/fragment/sampler/slice",
        "editor",
        {"index": "2", "action": "move", "at": "0.3"},
        _chopped,
    ),
    # "use" the detected pitch: a patch edit (its slider row rides along)
    C("/fragment/sampler/root", "editor", setup=_rooted_off),
    C("/fragment/sampler/root", "editor", setup=_sampled),  # already the root
    # a multi-sample instrument: a clip as a zone, auto-map (both loads of
    # a whole page), and the map's edits and tools (patch edits)
    C("/fragment/sampler/zone/add", "editor", {"name": "tone"}, _sampled),
    C("/fragment/sampler/zone/add", "clips", {"name": "tone"}, _tone),
    C("/fragment/sampler/zone/add", "editor", {"name": "hit"}, _clip),  # silent
    C("/fragment/sampler/zones/map", "editor", {"names": "tone\nlow"}, _two_tones),
    C("/fragment/sampler/zones/map", "editor", {"names": "gone"}),  # toast
    C(
        "/fragment/sampler/zone",
        "editor",
        {"index": "1", "action": "set", "lo": "40", "hi": "70"},
        _zoned,
    ),
    C("/fragment/sampler/zone", "editor", {"index": "0", "action": "remove"}, _zoned),
    C("/fragment/sampler/zones", "editor", {"action": "spread"}, _zoned),
    C("/fragment/sampler/zones", "editor", {"action": "chops"}, _chopped),
    C(
        "/project/import",
        "editor",
        files=lambda c: {
            "file": ("p.json", json.dumps(_st(c).project_payload()), "application/json")
        },
    ),
    # the bundle with its audio: the same whole-page reload
    C(
        "/project/import",
        "editor",
        files=lambda c: {
            "file": ("p.zip", c.get("/project.zip").content, "application/zip")
        },
        setup=_sampled,
    ),
    C("/fragment/bass/key", "bass", {"root": "E1"}),
    C("/fragment/bass/phone-audition", "bass"),
    C("/fragment/load", "map", _first_tile, _generation),
    # --- Breed ---
    C("/fragment/breed/generation", "breed", {"wildness": "tame"}),
    C("/fragment/breed/generation", "breed", {}, _starred),
    C("/fragment/breed/star", "breed", _first_tile, _generation),
    C("/fragment/breed/wildness", "breed", {"wildness": "feral"}),
    C("/fragment/breed/depth", "breed", {"depth": "deep"}),
    C("/fragment/breed/generation", "breed", {"depth": "layered"}),
    C(
        "/fragment/breed/restore",
        "breed",
        {"index": "0"},
        lambda c: [_generation(c), _generation(c)],
    ),
    C("/fragment/breed/audition", "breed", _first_tile, _generation),
    C("/fragment/breed/adopt", "breed", _first_tile, _generation),
    C("/fragment/breed/adopt", "breed", _first_tile, _layered),
    C(
        "/fragment/breed/adopt",
        "breed",
        lambda c: _first_tile(c) | {"main": "1"},
        _layered,
    ),
    # --- Drums ---
    # the kit's strips: a level is on a row head's knob too, which steps.js
    # moves with the strip's — the grid is never redrawn for a kit tweak
    C(
        "/fragment/drums",
        "drums",
        {f"kick.{_KICK}": "0.4", "kick.level": "0.8"},
        suppressed={"drum-bank"},
    ),
    C(
        "/fragment/drums/preset",
        "drums",
        {"voice": "kick", "preset": "Default"},
        lambda c: c.post(
            "/fragment/drums", data={f"kick.{_KICK}": "0.4"}, headers=_hx("drums")
        ),
    ),
    C("/fragment/drums/transport", "drums", {"bars": "2"}),
    C("/fragment/drums/transport", "drums", {"steps": "8"}),
    # a tempo/feel edit: the transport shows what was typed or dragged
    C(
        "/fragment/drums/transport",
        "drums",
        {"swing": "0.3", "bpm": "131"},
        # ... and the rows' swing knobs rest on the kit's (steps.js)
        suppressed={"drum-transport", "drum-bank"},
    ),
    C("/fragment/drums/transport", "perform", {"bpm": "131"}),
    # the loop's meter (meter.py): every grid fitted, the picker follows
    C("/fragment/meter", "perform", {"meter": "3/4"}),
    C("/fragment/meter", "drums", {"meter": "6/8"}),
    C("/fragment/drums/sample", "drums", {"voice": "kick", "clip.kick": "hit"}, _clip),
    C("/fragment/drums/row/{voice}/{op}", "drums", url="/fragment/drums/row/kick/mute"),
    C(
        "/fragment/drums/row/{voice}/{op}",
        "drums",
        url="/fragment/drums/row/kick/clear",
    ),
    C(
        "/fragment/drums/grid",
        "drums",
        {"bars": "1", "steps": "16", "drum.kick.0": "on", "drum.hat.3": "on"},
        suppressed={"drum-bank"},
    ),
    C("/fragment/drums/bank/load/{slot}", "drums", url="/fragment/drums/bank/load/2"),
    C(
        "/fragment/drums/bank/save/{slot}",
        "drums",
        {"slot": "1", "name": "x"},
        url="/fragment/drums/bank/save/0",
    ),
    C(
        "/fragment/drums/bank/factory",
        "drums",
        setup=lambda c: c.post(
            "/fragment/drums/bank/save/0",
            data={"slot": "0", "name": "x"},
            headers=_hx("drums"),
        ),
    ),
    C("/fragment/drums/randomise", "drums", {"style": "techno", "seed": "7"}),
    C("/fragment/drums/randomise", "drums", {"style": "garage-rim", "seed": "7"}),
    C("/fragment/drums/kit", "drums", {"kit": "Garage Rim"}),
    # --- the beat workflow: per-hit data, the rows' own
    # swing, Euclid a row, the kit under the beat, variations, generators ---
    C(
        "/fragment/drums/kit",
        "drums",
        {"kit": "Trance", "keep": "pattern"},
        _beat,
    ),
    C(
        "/fragment/drums/grid",
        "drums",
        {
            "bars": "1",
            "steps": "16",
            "drum.kick.0": "on",
            "drum.hat.3": "on",
            "hits": '{"hat.3": {"p": 0.5, "r": 2}}',
            "rswing": '{"hat": 0.2}',
            "rlevel": '{"kick": 0.7}',
        },
        suppressed={"drum-bank"},
    ),
    C(
        "/fragment/drums/row/{voice}/{op}",
        "drums",
        {"hits": "3", "of": "8", "rotate": "1"},
        url="/fragment/drums/row/rim/euclid",
    ),
    C(
        "/fragment/drums/row/{voice}/{op}",
        "perform",
        setup=_hit_beat,
        url="/fragment/drums/row/hat/hitclear",
    ),
    C("/fragment/drums/var", "drums", {"var": "B"}, _beat),
    C("/fragment/drums/var", "perform", {"var": "C", "copy": "A"}, _beat),
    C("/fragment/gen", "drums", {"kind": "fill", "seed": "3"}, _beat),
    C("/fragment/gen", "drums", {"kind": "build", "seed": "3"}, _beat),
    C("/fragment/gen", "perform", {"kind": "busier", "seed": "3"}, _beat),
    C("/fragment/gen", "perform", {"kind": "sparser", "seed": "3"}, _beat),
    C("/fragment/drums/mutate", "drums"),
    C("/fragment/drums/notation", "drums", {"notation": "bd*8"}),
    C("/fragment/drums/clear", "drums"),
    C("/fragment/drums/lib/save", "drums", {"name": "mine"}),
    C("/fragment/drums/lib/load", "drums", {"name": "mine"}, _libbeat),
    C("/fragment/drums/lib/delete", "drums", {"name": "mine"}, _libbeat),
    # --- pattern tools (routes/pattern.py) ---
    # the loop's chord track: Perform's chord lane (the roll) and the keys'
    # chord bar (its data-track), on Perform and off it
    C("/fragment/chords/track", "perform", {"op": "roll", "seed": "3"}),
    C(
        "/fragment/chords/track",
        "perform",
        {"op": "set", "slot": "0", "deg": "4"},
        _loop_chords,
    ),
    C("/fragment/chords/track", "editor", {"op": "clear"}, _loop_chords),
    C(
        "/fragment/pattern/notes/{op}",
        "perform",
        url="/fragment/pattern/notes/shift_right",
    ),
    C(
        "/fragment/pattern/notes/{op}",
        "perform",
        url="/fragment/pattern/notes/duplicate",
    ),
    C("/fragment/pattern/notes/{op}", "perform", url="/fragment/pattern/notes/up"),
    # the piano roll's tools ride the same route: a selection moved, a
    # note's end dragged, a chord stamped (the default loop holds A1 on 0 and 2)
    C(
        "/fragment/pattern/notes/{op}",
        "perform",
        {"sel": "0:0:A1:A1", "dx": "1", "dy": "1"},
        url="/fragment/pattern/notes/move",
    ),
    C(
        "/fragment/pattern/notes/{op}",
        "perform",
        {"sel": "0:0:A1:A1", "by": "1"},
        url="/fragment/pattern/notes/resize",
    ),
    C(
        "/fragment/pattern/notes/{op}",
        "perform",
        {"row": "A1", "step": "1", "q": "triad", "len": "1"},
        url="/fragment/pattern/notes/stamp",
    ),
    C(
        "/fragment/pattern/drums/{op}",
        "perform",
        setup=lambda c: c.post("/fragment/drums/row/hat/fill2", headers=_hx("perform")),
        url="/fragment/pattern/drums/shift_right",
    ),
    C(
        "/fragment/pattern/drums/{op}",
        "drums",
        setup=lambda c: c.post("/fragment/drums/row/hat/fill4", headers=_hx("drums")),
        url="/fragment/pattern/drums/vel_ramp",
    ),
    # a .mid file into the note grid: the transport comes back by its watch
    C(
        "/fragment/pattern/import",
        "perform",
        files=lambda c: {"file": ("riff.mid", _mid(), "audio/midi")},
    ),
    C(
        "/fragment/pattern/undo",
        "perform",
        setup=lambda c: c.post(
            "/fragment/pattern/notes/shift_right", headers=_hx("perform")
        ),
    ),
    C("/fragment/grid/span", "perform", {"span": "2"}),
    # chromatic rows, and the roll's octave window
    C("/fragment/grid/span", "perform", {"span": "3"}),
    C("/fragment/grid/span", "perform", {"oct_by": "1"}),
    C("/fragment/grid/span", "song", {"oct_by": "1"}, _song_drawer),
    # the drawer's grid follows the same range
    C("/fragment/grid/span", "song", {"span": "2"}, _song_drawer),
    C(
        "/fragment/drums/row/{voice}/{op}",
        "perform",
        url="/fragment/drums/row/kick/fill4",
    ),
    C(
        "/fragment/drums/grid",
        "perform",
        {"bars": "1", "steps": "16", "drum.kick.0": "on", "drum.hat.3": "on"},
        suppressed={"perform-drums"},
    ),
    # --- Perform ---
    C("/fragment/perform/xy", "perform", {"x": "0.2", "y": "0.9"}),
    # a launch never redraws the launcher perform.js shows "queued"
    C(
        "/fragment/perform/bank/{slot}",
        "perform",
        url="/fragment/perform/bank/2",
        suppressed={"launcher"},
    ),
    C(
        "/fragment/perform/launch/{pattern_id}",
        "perform",
        setup=_pool,
        suppressed={"launcher"},
    ),
    C("/fragment/perform/parts", "perform", {"part": "drums"}),
    # a lane head's sound ▾: the lane's sound, its name on the heads
    C(
        "/fragment/perform/lane/sound",
        "perform",
        {"index": "0", "sound": "preset:zap"},
        setup=_preset,
    ),
    # Perform's launcher (routes/slots.py) — the grid's
    # own edits move it alone; a launch, the lanes it moved
    C(
        "/fragment/perform/slot/place",
        "perform",
        {"lane": "1", "row": "0", "name": "riff"},
        _a_loop,
    ),
    C(
        "/fragment/perform/slot/place",
        "perform",
        {"lane": "4", "row": "0", "name": "riff"},
        _a_loop,
    ),  # toast
    C("/fragment/perform/slot/keep", "perform", {"lane": "0", "row": "1"}),
    # an empty slot selected — its lane edited when it has a sound
    C("/fragment/perform/slot/select", "perform", {"lane": "1", "row": "0"}, _lane2),
    C("/fragment/perform/slot/select", "perform", {"lane": "2", "row": "0"}),
    C("/fragment/perform/slot/keep", "perform", {"lane": "4", "row": "2"}),
    C("/fragment/perform/slot/clear", "perform", {"lane": "0", "row": "0"}, _slotted),
    C(
        "/fragment/perform/slot/move",
        "perform",
        {"lane": "0", "row": "0", "to_lane": "1", "to_row": "2", "copy": "1"},
        _slotted,
    ),
    C("/fragment/perform/slot/launch", "perform", {"lane": "0", "row": "0"}, _slotted),
    C("/fragment/perform/slot/launch", "perform", {"lane": "4", "row": "0"}, _slotted),
    C("/fragment/perform/scene/launch", "perform", {"row": "0"}, _slotted),
    C("/fragment/perform/scene/launch", "perform", {"row": "3"}),  # toast: empty
    C("/fragment/perform/slot/stop", "perform", {"lane": "0"}, _scened),
    C("/fragment/perform/slot/stop", "perform", {"lane": "all"}, _scened),
    C(
        "/fragment/perform/scene/keep",
        "perform",
        {"row": "0", "new": "verse"},
        _slotted,
    ),
    C("/fragment/perform/scene/idea", "perform", {"name": "verse idea"}, _an_idea),
    C(
        "/fragment/perform/scene/section",
        "perform",
        lambda c: {"section": _st(c).song["sections"][0]["id"]},
        _section,
    ),
    # a scene → the song (the song's regions by their watches);
    # the launcher's scene launches as played, captured
    C("/fragment/perform/scene/song", "perform", {"row": "0"}, _slotted),
    C("/fragment/perform/scene/song", "perform", {"row": "5"}),  # toast: empty
    C(
        "/fragment/perform/scene/capture",
        "perform",
        {
            "events": '[{"bar": 0, "slots": [null, null, null, null, "techno"]}]',
            "bars": "2",
        },
        _a_beat,
    ),
    C(
        "/clip/upload",
        "perform",
        {"name": "jam-20260926-101500"},
        files=lambda c: {"file": ("jam.wav", _wav(), "audio/wav")},
    ),
    # Perform's ✦ Coach (routes/coach.py): its panel (`coach-panel`) comes
    # back by its watch; an accepted or reversed change brings the grids
    C("/fragment/coach/propose", "perform", {"what": "fix:all"}, _coach_loop),
    C("/fragment/coach/propose", "perform", {"what": "option:beat_fill"}, _coach_loop),
    C("/fragment/coach/propose", "perform", {"what": "fix:nope"}, _coach_loop),  # toast
    C("/fragment/coach/audition", "perform", {"which": "proposed"}, _coach_proposal),
    C("/fragment/coach/accept", "perform", {}, _coach_proposal),
    C("/fragment/coach/discard", "perform", {}, _coach_proposal),
    C("/fragment/coach/reverse", "perform", _coach_entry, _coach_accepted),
    C("/fragment/coach/show", "perform", {}, _coach_loop),
    # no key in the suite: the thread says how to add one
    C(
        "/fragment/coach/chat",
        "perform",
        {"coach_say": "less busy in bar 3"},
        _coach_loop,
    ),
    C(
        "/fragment/coach/talk",
        "perform",
        {},
        _coach_loop,
        files=lambda c: {
            "file": ("talk.wav", _wav(channels=1, frames=22050), "audio/wav")
        },
    ),
    # keys takes (routes/takes.py): the strip on Synths, the bin on Perform
    C(
        "/take/keys",
        "editor",
        {"name": "keys-1", "bpm": "124", "bars": "1", "notes": "[[0, 1, 33, 0.8]]"},
        files=lambda c: {"file": ("take.wav", _wav(), "audio/wav")},
    ),
    C(
        "/take/keys",
        "perform",
        {"name": "keys-1", "bpm": "124", "bars": "1", "notes": "[[0, 1, 33, 0.8]]"},
        files=lambda c: {"file": ("take.wav", _wav(), "audio/wav")},
    ),
    C("/take/keys", "editor", {"bpm": "124", "bars": "1", "notes": "x"}),  # toast
    # ⏺ Rec into the launcher's selected slot (a new loop there,
    # launched), and ⟲ Capture — free, into an empty slot, overdubbing one
    C(
        "/take/keys",
        "perform",
        {**_TAKE, "lane": "1", "row": "0", "how": "new"},
        files=lambda c: {"file": ("take.wav", _wav(), "audio/wav")},
    ),
    C("/take/keys/capture", "editor", _TAKE),
    C("/take/keys/capture", "perform", {**_TAKE, "lane": "1", "row": "2"}),
    C(
        "/take/keys/capture",
        "perform",
        {**_TAKE, "lane": "0", "row": "0", "how": "overdub"},
        _slotted,
    ),
    C("/take/keys/capture", "perform", {"bpm": "124", "bars": "1"}),  # toast
    # free record (routes/freerec.py): the free take (its row in the Loops
    # lists), the shaper's ▶ (nothing moves) and Make loop — free, or into
    # an empty slot of the launcher (launched)
    C(
        "/take/keys/free",
        "perform",
        _TAKE,
        files=lambda c: {"file": ("take.wav", _wav(), "audio/wav")},
    ),
    C(
        "/take/keys/free",
        "editor",
        _TAKE,
        files=lambda c: {"file": ("take.wav", _wav(), "audio/wav")},
    ),
    C("/take/keys/free", "perform", {"bpm": "124", "bars": "1"}),  # toast
    C("/take/keys/free/hear", "perform", _SHAPED, _free_take),
    C("/take/keys/free/loop", "perform", _SHAPED, _free_take),
    C("/take/keys/free/loop", "editor", _SHAPED, _free_take),
    C(
        "/take/keys/free/loop",
        "perform",
        {**_SHAPED, "lane": "1", "row": "0", "how": "new"},
        _free_take,
    ),
    C("/take/keys/free/loop", "perform", {**_SHAPED, "bars": "9"}, _free_take),
    C("/take/keys/keep", "editor", {"name": "keys-t", "new": "riff"}, _keys_take),
    C("/take/keys/keep", "perform", {"name": "keys-t"}, _keys_take),
    C("/take/keys/discard", "editor", {"name": "keys-t"}, _keys_take),
    C("/take/keys/discard", "perform", {"name": "keys-t"}, _keys_take),
    C("/take/keys/clear", "editor", setup=_keys_take),
    # → Perform: a whole page, landing there
    C("/take/keys/notes", "editor", {"name": "keys-t"}, _keys_take),
    C("/take/keys/notes", "perform", {"name": "keys-t"}, _keys_take),
    C("/take/keys/sample", "perform", {"name": "keys-t"}, _keys_take),
    # a cleaned take's notes are in its sidecar: the strip moves, nothing
    # the clip library draws (no suppress: its watch never sees them)
    C("/take/keys/clean", "editor", {"name": "keys-t", "ai": "0"}, _keys_take),
    C("/take/keys/clean", "perform", {"name": "keys-t", "ai": "0"}, _keys_take),
    C("/take/keys/unclean", "editor", {"name": "keys-t"}, _cleaned_take),
    # loops (loops.py): saved from a take or the lane, into Perform, bounced
    C("/loop/save", "editor", {"name": "keys-t", "bars": "2"}, _keys_take),
    C("/loop/save", "perform", {"lane": "1", "bars": "1", "loop": "lane"}),
    C("/loop/load", "perform", {"name": "riff"}, _a_loop),
    C("/loop/load", "editor", {"name": "riff"}, _a_loop),
    C("/loop/rename", "perform", {"name": "riff", "new": "riff two"}, _a_loop),
    C("/loop/delete", "editor", {"name": "riff"}, _a_loop),
    C("/loop/bounce", "perform", {"name": "riff"}, _a_loop),
    # one Rec = a take and its loop: Edit (a whole page, landing on Perform;
    # a take from before makes its loop first), Done, Quantise, → Song
    C("/loop/edit", "perform", {"name": "keys-r"}, _recorded),
    C("/loop/edit", "editor", {"take": "keys-t"}, _keys_take),
    C("/loop/edit/done", "perform", setup=_editing),
    C("/loop/edit/done", "perform"),  # toast: nothing open
    C("/loop/quantise", "perform", {"name": "keys-r", "strength": "0"}, _recorded),
    C("/loop/quantise", "perform", {"name": "keys-r", "steps": "8"}, _editing),
    C("/loop/quantise", "editor", {"name": "keys-r", "steps": "12"}, _recorded),
    C("/loop/song", "perform", {"name": "riff"}, _a_loop),
    C("/loop/song", "editor", {"name": "keys-r", "as": "audio"}, _recorded),
    C("/loop/song", "song", {"take": "keys-t"}, _keys_take),
    C("/loop/song", "song", {"name": "techno", "at_section": "0"}, _a_beat),
    C("/loop/delete", "perform", {"name": "keys-r", "audio": "1"}, _editing),
    # the Loops panel: ★, tags (a bank slot is kept first), ▶
    # in time (a loop with no audio is given its own), beats opened, kept
    C("/loop/star", "perform", {"name": "riff"}, _a_loop),
    C("/loop/star", "perform", {"slot": "0"}),
    C("/loop/tag", "perform", {"name": "riff", "tags": "dark wide"}, _a_loop),
    C("/loop/tag", "editor", {"slot": "1", "tags": "dark"}),
    C("/loop/preview", "perform", {"name": "riff"}, _a_loop),
    C("/loop/preview", "perform", {"name": "riff", "bpm": "90"}, _a_loop),
    C("/loop/preview", "editor", {"slot": "1", "bpm": "128"}),
    C("/loop/beat", "perform", {"slot": "2"}),
    C("/loop/beat", "perform", {"name": "techno"}, _a_beat),
    C("/loop/beat", "editor", {"name": "techno"}, _a_beat),
    C("/loop/beat/keep", "perform", setup=_beat_on_grid),
    C("/loop/beat/keep", "perform"),  # toast: an empty grid
    # --- Song ---
    C("/song/section/add", "song", {"name": "Chorus"}, _song),
    C(
        "/song/section/{section_id}/rename",
        "song",
        {"name": "Start"},
        _song,
        url="/song/section/s1/rename",
    ),
    C(
        "/song/section/{section_id}/bars",
        "song",
        {"bars": "4"},
        _song_drawer,
        url="/song/section/s1/bars",
    ),
    C(
        "/song/section/{section_id}/repeats",
        "song",
        {"repeats": "2"},
        _song,
        url="/song/section/s1/repeats",
    ),
    C(
        "/song/section/{section_id}/duplicate",
        "song",
        setup=_song,
        url="/song/section/s1/duplicate",
    ),
    # a section's own bar and tempo, and a ramp (meter.py)
    C(
        "/song/section/{section_id}/time",
        "song",
        {"meter": "6/8", "bpm": "100"},
        _song,
        url="/song/section/s1/time",
    ),
    C(
        "/song/section/{section_id}/time",
        "song",
        {"ramp": "140"},
        _song,
        url="/song/section/s1/time",
    ),
    C(
        "/song/section/{section_id}/move",
        "song",
        {"delta": "-1"},
        _song,
        url="/song/section/s2/move",
    ),
    C(
        "/song/section/{section_id}/delete",
        "song",
        setup=_song_drawer,
        url="/song/section/s1/delete",
    ),
    C(
        "/song/section/{section_id}/to-loop",
        "song",
        setup=_song,
        url="/song/section/s1/to-loop",
    ),
    C("/song/track/add", "song", {"kind": "synth"}, _song),
    C(
        "/song/track/{track_id}/rename",
        "song",
        {"name": "Sub"},
        _song,
        url="/song/track/t1/rename",
    ),
    C(
        "/song/track/{track_id}/mix",
        "song",
        {"gain": "0.5"},
        _song,
        url="/song/track/t1/mix",
    ),
    C(
        "/song/track/{track_id}/delete",
        "song",
        setup=_song,
        url="/song/track/ta/delete",
    ),
    C(
        "/song/track/{track_id}/edit-sound",
        "song",
        setup=_song,
        url="/song/track/t1/edit-sound",
    ),
    C(
        "/song/track/{track_id}/edit-sound",
        "song",
        {"dock": "1"},
        _song,
        url="/song/track/t1/edit-sound",
    ),
    C("/song/dock/close", "song", setup=_song),
    C(
        "/song/track/{track_id}/sound",
        "song",
        {"sound": "factory:" + FACTORY[0]["name"]},
        _song,
        url="/song/track/t1/sound",
    ),
    C("/song/pattern/add", "song", {"kind": "notes"}, _song),
    C(
        "/song/pattern/{pattern_id}/rename",
        "song",
        {"name": "Bassline"},
        _song_drawer,
        url="/song/pattern/p1/rename",
    ),
    C(
        "/song/pattern/{pattern_id}/delete",
        "song",
        setup=_song,
        url="/song/pattern/p3/delete",
    ),
    C(
        "/song/cell",
        "song",
        {"section_id": "s1", "track_id": "t2", "pattern_id": "p3"},
        _song,
    ),
    # a new pattern straight into an empty cell: the cell, the pool, its
    # (empty) thumbnail and the drawer it opens
    C("/song/cell/new", "song", {"section_id": "s1", "track_id": "t2"}, _song),
    C(
        "/song/pattern/{pattern_id}/duplicate",
        "song",
        setup=_song,
        url="/song/pattern/p2/duplicate",
    ),
    C("/song/cell/unique", "song", {"section_id": "s2", "track_id": "t1"}, _song),
    # a cell dragged onto another with Alt: both cells, in one answer
    C(
        "/song/cell/move",
        "song",
        {
            "from_section": "s2",
            "from_track": "t2",
            "section_id": "s1",
            "track_id": "t2",
        },
        _song,
    ),
    # a block trimmed to bars 2-2 of its section: the playlist draws it
    # there (and the drawer, were it open on that cell, says so)
    C(
        "/song/cell/span",
        "song",
        {"section_id": "s1", "track_id": "t1", "at": "1", "len": "1"},
        _song,
    ),
    C(
        "/song/cell/variation",
        "song",
        {"section_id": "s2", "track_id": "t1", "seed": "3"},
        _song,
    ),
    # a beat's family: a variation by letter, its fill, a section picking one
    C(
        "/song/cell/variation",
        "song",
        {"section_id": "s2", "track_id": "td", "var": "B", "seed": "3"},
        _song,
    ),
    C(
        "/song/cell/variation",
        "song",
        {"section_id": "s1", "track_id": "td", "fill": "1", "seed": "3"},
        _song,
    ),
    C("/song/drawer", "song", {"section_id": "s1", "track_id": "t1"}, _song),
    # a lane on a cell: its marker (arranger) and, were it open, the drawer
    C(
        "/song/cell/auto",
        "song",
        {"section_id": "s1", "track_id": "t1", "op": "add", "target": "cutoff"},
        _song,
    ),
    # the drawer's grid posts it: redrawn only when the pattern's shape moved
    C(
        "/song/pattern/{pattern_id}/grid",
        "song",
        {"bars": "1", "steps": "16", "cell.A1.0": "on", "cell.C2.5": "on"},
        _song_drawer,
        url="/song/pattern/p1/grid",
        suppressed={"song-drawer"},
    ),
    C(
        "/song/pattern/{pattern_id}/grid",
        "song",
        {"bars": "2", "steps": "16", "cell.A1.0": "on"},
        _song_drawer,
        url="/song/pattern/p1/grid",
    ),
    # the drawer's pattern tools: the drawer comes back by its watch
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        setup=_song_drawer,
        url="/song/pattern/p1/tool/shift_right",
    ),
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        setup=_song_drawer,
        url="/song/pattern/p1/tool/duplicate",
    ),
    # the beat workflow's tools on a pool beat, and its grid's per-hit fields
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        {"voice": "rim", "hits": "3", "of": "8"},
        setup=_beat_drawer,
        url="/song/pattern/pd/tool/euclid",
    ),
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        setup=_beat_drawer,
        url="/song/pattern/pd/tool/busier",
    ),
    C(
        "/song/pattern/{pattern_id}/grid",
        "song",
        {
            "bars": "1",
            "steps": "16",
            "drum.kick.0": "on",
            "drum.hat.2": "on",
            "hits": '{"hat.2": {"r": 3}}',
            "rswing": '{"hat": 0.2}',
        },
        _beat_drawer,
        url="/song/pattern/pd/grid",
        suppressed={"song-drawer"},
    ),
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        {"sel": "0:63:A2:A1", "dx": "1"},
        setup=_song_drawer,
        url="/song/pattern/p1/tool/move",
    ),
    C(
        "/song/pattern/{pattern_id}/tool/{op}",
        "song",
        {"row": "A1", "step": "3", "q": "track", "len": "2"},
        setup=_song_drawer,
        url="/song/pattern/p1/tool/stamp",
    ),
    C(
        "/song/pattern/{pattern_id}/record",
        "song",
        lambda c: {
            "events": json.dumps([{"midi": 45, "t": 0.5}]),
            "take": "t1",
            "t0": "0",
            "t1": "2",
            "section_id": "s2",
            "track_id": "t2",
        },
        _song,
        url="/song/pattern/p2/record",
    ),
    C("/song/record/undo", "song", setup=_song_recorded),
    # ✦ Clean up (deterministic: ai=0) — the take's pattern, one undo step
    C("/song/take/clean", "song", {"ai": "0"}, setup=_song_recorded),
    # the song's ring: the cell goes back (arranger, pool usage) and the
    # topbar's two buttons say what is next
    C("/song/undo", "song", setup=_song_edited),
    C("/song/redo", "song", setup=_song_undone),
    C(
        "/song/take",
        "song",
        {"section_id": "s1", "track_id": "ta", "at_bar": "0"},
        _song,
        files=lambda c: {"file": ("t.wav", _wav(), "audio/wav")},
    ),
    C(
        "/song/take/{clip}/rename",
        "song",
        {"name": "vox one"},
        _song_take,
        url="__take__",
    ),
    # a riser rendered into the clip store and placed as a take on "ta"
    C(
        "/song/section/{section_id}/transition",
        "song",
        {"kind": "riser", "direction": "up", "bars": "1", "track_id": "ta"},
        _song,
        url="/song/section/s1/transition",
    ),
    # a clip already in the store, placed as a take on the first audio track
    C(
        "/song/section/{section_id}/clip",
        "song",
        {"clip": "loop", "at_bar": "1"},
        _song_with_clip,
        url="/song/section/s1/clip",
    ),
    # a placed take dragged to another bar: the arranger draws it there
    C(
        "/song/take/edit",
        "song",
        {
            "op": "move",
            "section_id": "s1",
            "track_id": "ta",
            "clip": "loop",
            "at_bar": "0",
            "to_bar": "1",
        },
        _song_placed,
    ),
    # a whole stem moved along the timeline: the arranger draws it
    C("/song/long", "song", {"op": "set", "id": "lg", "bar": "3"}, _song_long),
    # the latency field posted it (JSON answer): it shows what was typed
    C(
        "/song/calibration",
        "song",
        {"latency_ms": "30"},
        _song,
        suppressed={"song-topbar"},
    ),
    C("/song/migration-note/clear", "song", setup=_migration_note),
    C("/song/from-loop", "song", {"name": "Jam"}, _song),
    # → Song from Perform and Synths: the same section, a note for the button
    C("/song/from-loop", "perform", {}, _song_loop_kit),
    C("/song/from-loop", "editor", {}, _song),
    C("/song/drums/loop-kit", "perform", {}, _song_loop_kit),
    # --- song creation: a starter replaces the song (every Song region
    # follows), the loop arranged, one part rolled, title / tempo / key,
    # the chord track ---
    # --- Mix (routes/mixer.py): a strip's controls, a return, Balance —
    # and the song's own routes asked from the Mix page, whose strips
    # follow by their watch ---
    C(
        "/song/mix/strip/{track_id}",
        "mixer",
        {"pan": "-0.4"},
        _song,
        url="/song/mix/strip/t1",
    ),
    C(
        "/song/mix/strip/{track_id}",
        "mixer",
        {"send.1": "0.5"},
        _song,
        url="/song/mix/strip/t1",
    ),
    C(
        "/song/mix/strip/{track_id}",
        "mixer",
        {"duck": "0.6", "eq.low": "-3"},
        _song,
        url="/song/mix/strip/t2",
    ),
    C(
        "/song/mix/strip/{track_id}",
        "mixer",
        {"mute": "on"},
        _song,
        url="/song/mix/strip/td",
    ),
    # ... from the Song page: its arranger draws the track's level
    C(
        "/song/mix/strip/{track_id}",
        "song",
        {"gain": "0.5"},
        _song,
        url="/song/mix/strip/t1",
    ),
    C(
        "/song/mix/return/{return_id}",
        "mixer",
        {"gain": "0.5", "p.decay": "3.5"},
        _song_mixed,
        url="/song/mix/return/0a0b0c0d",
    ),
    # an audio track's rack: from the Song page's drawer (the drawer and
    # the arranger's fx badge follow), and seen from the Mix view's strip
    C(
        "/song/track/{track_id}/fx",
        "song",
        {"op": "add", "kind": "lowpass"},
        _audio_drawer,
        url="/song/track/ta/fx",
    ),
    C(
        "/song/track/{track_id}/fx",
        "mixer",
        {"op": "add", "kind": "reverb"},
        _song,
        url="/song/track/ta/fx",
    ),
    # ... and its level lane, on its row in a section
    C(
        "/song/cell/auto",
        "song",
        {"section_id": "s1", "track_id": "ta", "op": "add", "target": "gain"},
        _audio_drawer,
    ),
    C("/song/mix", "mixer", {"op": "return-add"}, _song),
    C("/song/mix", "mixer", {"op": "return-remove", "id": "0a0b0c0d"}, _song_mixed),
    C("/song/mix", "mixer", {"op": "balance"}, _song_mixed),
    C(
        "/song/track/{track_id}/mix",
        "mixer",
        {"role": "pad"},
        _song,
        url="/song/track/t1/mix",
    ),
    C("/song/start", "mixer", {"style": "garage", "seed": "3"}, _song),
    C("/song/start", "song", {"style": "garage", "seed": "3"}, _song),
    C("/song/start", "song", {"style": "trance", "seed": "1"}),
    C("/song/arrange", "song", {"source": "loop", "seed": "2"}, _song),
    C("/song/arrange", "song", {"add": "outro", "seed": "2"}, _song),
    C("/song/gen", "song", {"track_id": "t1", "kind": "lead", "seed": "4"}, _song),
    C("/song/gen", "song", {"add": "bass", "seed": "4"}, _song),
    C("/song/meta", "song", {"title": "Night Drive"}, _song),
    C("/song/meta", "song", {"root": "D2", "scale_name": "dorian", "move": "1"}, _song),
    C("/song/chords", "song", {"op": "preset", "prog": "2"}, _song),
    C("/song/chords", "song", {"op": "own", "scope": "s1"}, _song),
    # --- the playlist tools: a range of cells, a section cut / unrolled,
    # bars inserted at the marker, the energy lane, a transition on a
    # boundary — each redraws the arranger (and what its edit touched) ---
    C(
        "/song/cells",
        "song",
        {"op": "copy", "cells": "s1:td,s1:t1", "to_section": "s2", "to_track": "td"},
        _song,
    ),
    C("/song/cells", "song", {"op": "unique", "cells": "s2:td,s2:t1"}, _song),
    C(
        "/song/section/{section_id}/op",
        "song",
        {"op": "split", "bar": "2"},
        _song,
        url="/song/section/s1/op",
    ),
    C(
        "/song/section/{section_id}/op",
        "song",
        {"op": "unroll"},
        _song,
        url="/song/section/s2/op",
    ),
    C("/song/bars", "song", {"op": "insert", "bar": "3", "bars": "4"}, _song),
    C("/song/energy", "song", {"section_id": "s1", "energy": "0.8"}, _song),
    C("/song/boundary", "song", {"section_id": "s2", "kind": "impact"}, _song),
    # the jam captured: two launches become sections at the song's end
    C(
        "/song/jam/commit",
        "song",
        {
            "events": '[{"bar": 0, "scene": "s1"},'
            ' {"bar": 2, "track": "t2", "target": "s2"}]',
            "bars": "4",
        },
        _song,
    ),
    # --- Voice ---
    C(
        "/voice/take",
        "voice",
        files=lambda c: {
            "file": ("t.wav", _wav(channels=1, frames=22050), "audio/wav")
        },
    ),
    C("/voice/render", "voice", {"bands": "8", "pitch": "A2"}, _take),
    C("/voice/keep", "voice", {"name": "kept"}, _take),
    # a preset: the settings file, and its carrier Sound loaded as Use loads one
    C("/voice/preset", "voice", {"name": "talkbox"}),
    C("/voice/preset", "voice", {"name": "nope"}),  # a toast
    # the clip library's "Use as take": the panel it targets, nothing else
    C("/voice/take/clip", "voice", {"name": "tone"}, _tone),
    C("/voice/take/clip", "voice", {"name": "nope"}),  # no such clip: a toast
    # ...and "as carrier": the same panel; the vocoder's settings are a file
    # beside the take, so no region moves
    C("/voice/carrier/clip", "voice", {"name": "tone"}, _tone),
    C("/voice/carrier/clip", "voice", {"name": "nope"}),  # a toast
    # each carrier kind renders through the one route (the loop one renders
    # the Synths loop under the lock first)
    C("/voice/render", "voice", {"carrier": "loop"}, _take),
    C("/voice/render", "voice", {"carrier": "follow"}, _take),  # no pitch: a toast
    # "Keep result": a clip is made, so the library comes back; nothing else
    C("/voice/result/keep", "voice", {"name": "robot"}, _vocoded),
    C("/voice/result/keep", "voice", {"key": "voice:nope"}, _take),  # gone: a toast
    # the vocal booth: a treatment heard, the rack and the moves edited -
    # the panel it targets, nothing else (its state is voice_lab.json's)
    C("/voice/booth/render", "voice", {"treatment": "adt"}, _take),
    C("/voice/booth/render", "voice", {"treatment": "nope"}, _take),  # a toast
    C("/voice/booth/render", "voice", {"swell": "0.4"}),  # no take: a toast
    C("/voice/booth/fx", "voice", {"op": "add", "kind": "plate"}),
    C("/voice/booth/fx", "voice", {"op": "remove", "index": "3"}),  # a toast
    C("/voice/booth/moves", "voice", {"reverse": "backwards", "rate": "1.2"}),
    # kept: a clip is made, so the library comes back
    C("/voice/booth/keep", "voice", {"name": "double"}, _booth_heard),
    C("/voice/booth/keep", "voice", {"key": "booth:nope"}, _take),  # gone: a toast
    # to the Song: a clip kept and placed - the song's regions and the library
    C("/voice/booth/song", "voice", {"name": "double"}, _booth_on_song),
    C("/voice/booth/song", "voice", {"key": "booth:nope"}, _take),  # a toast
    # the live vocal chain (routes/vocal.py): its panel is every page's and
    # its state voice_lab.json's - the panel it answers with, nothing else
    C("/voice/live/fx", "voice", {"op": "add", "kind": "autotune"}),
    C("/voice/live/fx", "editor", {"op": "remove", "index": "9"}),  # a toast
    C("/voice/live/style", "song", {"name": "psych_haze"}),
    C("/voice/live/style", "voice", {"name": "nope"}),  # a toast
    # ⏺'s take: two clips made, so the library comes back
    C("/take/vocal", "perform", {"bpm": "120", "bars": "1"}, files=_vocal_files),
    C("/take/vocal", "editor", {"bpm": "120", "bars": "1"}),  # no audio: a toast
    # → Song: placed, the song's regions and the library
    C("/take/vocal/song", "voice", {"name": "vox"}, _vocal_on_song),
    C("/take/vocal/song", "editor", {"name": "nope"}),  # a toast
    # the clip library is on every page: a clip made, renamed or deleted
    # anywhere, a take placed on the song (delete then says where it is
    # used), and the Lab's songs coming and going all reach it
    C("/clip/bounce", "song", {"name": "b2"}, _song),
    C("/fragment/clip/delete", "voice", {"name": "hit"}, _clip),
    C("/fragment/clip/rename", "lab", {"name": "hit", "new": "thud"}, _clip),
    # ...and from the Lab page, a clip a chop was kept as: the open song's
    # chop list rides along (`#lab-chops`; the Lab has no region of its own)
    C("/fragment/clip/delete", "lab", {"name": "chop 1"}, _lab_kept),
    C("/fragment/clip/rename", "lab", {"name": "chop 1", "new": "thud"}, _lab_kept),
    # the Sample Lab draws no region of its own: every post answers with its
    # own body, and what it stores is no session state. What reaches the
    # session is a clip (the library, out of band) and the sampler sound (a
    # whole page, on Synths).
    C(
        "/lab/upload",
        "lab",
        files=lambda c: {"file": ("s.wav", _lab_wav(), "audio/wav")},
    ),
    C("/lab/select", "lab", _lab_source, _lab_song),
    C("/lab/grid", "lab", lambda c: {**_lab_source(c), "action": "half"}, _lab_song),
    C("/lab/view", "lab", lambda c: {**_lab_source(c), "action": "in"}, _lab_song),
    C("/lab/key", "lab", lambda c: {**_lab_source(c), "key": "D minor"}, _lab_song),
    C(
        "/lab/region",
        "lab",
        lambda c: {
            **_lab_source(c),
            "mode": "seconds",
            "start": "0.2",
            "length": "0.5",
        },
        _lab_song,
    ),
    C("/lab/send", "lab", lambda c: {**_lab_source(c), "target": "clip"}, _lab_song),
    C(
        "/lab/send",
        "lab",
        lambda c: {**_lab_source(c), "target": "sampler"},
        _lab_song,
        url="/lab/send?to=sampler",
    ),
    # what a region becomes: the vocal lands on Synths (a whole page); the
    # drums change the loop's grid, the song's pool or the drum rows — none
    # of them on the Lab's page, whose reset menu is all that can move; a
    # region placed on the song changes the song
    C(
        "/lab/vocal",
        "lab",
        lambda c: {**_lab_source(c), "what": "instrument"},
        _lab_song,
    ),
    C(
        "/lab/vocal",
        "lab",
        lambda c: {**_lab_source(c), "what": "chops"},
        _lab_song,
        url="/lab/vocal?what=chops",
    ),
    C("/lab/drums", "lab", lambda c: {**_lab_bars(c), "to": "grid"}, _lab_song),
    C(
        "/lab/drums",
        "lab",
        lambda c: {**_lab_bars(c), "to": "pool"},
        _lab_song,
        url="/lab/drums?to=pool",
    ),
    C(
        "/lab/drums",
        "lab",
        lambda c: {**_lab_bars(c), "to": "kit"},
        _lab_song,
        url="/lab/drums?to=kit",
    ),
    C(
        "/lab/place",
        "lab",
        lambda c: {**_lab_bars(c), "section_id": "s1", "at_bar": "1"},
        _lab_and_song,
    ),
    # chops live in the source's meta, and a kept chop is a clip nothing
    # places yet: neither moves a region of the Lab's page
    C(
        "/lab/chop",
        "lab",
        lambda c: {**_lab_source(c), "op": "split", "at": "0.3"},
        _lab_song,
    ),
    C("/lab/chop/keep", "lab", _lab_chop, _lab_song),
    # the line under a make takes it back: a kept clip deleted (the library
    # comes back), the drum grid as it was, a refusal is a toast
    C(
        "/lab/undo",
        "lab",
        lambda c: {**_lab_source(c), "what": "clip", "clip": "chop 1"},
        _lab_kept,
    ),
    C(
        "/lab/undo",
        "lab",
        lambda c: {**_lab_source(c), "what": "pattern", "bpm": "120"},
        _lab_song,
        url="/lab/undo?what=pattern",
    ),
    C(
        "/lab/undo",
        "lab",
        lambda c: {**_lab_source(c), "what": "song", "label": "place clip"},
        _lab_song,
        url="/lab/undo?what=song",
    ),
    C("/lab/separate", "lab", _lab_source, _lab_song),
    C("/lab/cancel", "lab", _lab_source, _lab_song),
    C("/lab/delete", "lab", _lab_source, _lab_song),
    # the clip hub: its list (region `hub-list`) draws where each clip is
    # used and the hub's Undo; a make draws nothing but its preview (the
    # list moves only by the tempo / key it measured), Apply and Undo move
    # the list, the tune's Keep / Update and a sample kept are whole pages,
    # "Open in Lab" a navigation
    C(
        "/hub/make",
        "clips",
        {"item": "clip:tone", "target": "beat", "style": "techno"},
        _tone,
    ),
    C("/hub/make", "clips", {"item": "clip:nope", "target": "beat"}),  # gone: a toast
    C(
        "/hub/make",
        "clips",
        {"item": "clip:tone", "target": "notes", "notes_from": "bass_root"},
        _tone,
    ),
    C("/hub/apply", "clips", {"item": "clip:tone", "plan": _HUB_BEAT}, _tone),
    C("/hub/apply", "clips", {"item": "clip:tone", "plan": "[1]"}, _tone),  # no plan
    C("/hub/undo", "clips", {}, _hub_applied),
    C("/hub/undo", "clips"),  # nothing to undo: a toast
    C("/hub/tune/keep", "clips", {"item": "clip:tone", "semis": "3"}, _tone),
    C("/hub/tune/update", "clips", {"item": "clip:tone", "reverse": "on"}, _tone),
    C("/hub/keep", "clips", {"item": f"sample:{'0' * 16}"}),  # no such sample
    C("/hub/keep", "clips", _hub_sample, _hub_sampled),
    C("/hub/lab", "clips", {"item": "clip:tone"}, _tone),
    # ...and a send from the hub's bench: the destination's own route; the
    # list says where the clip is used now
    C("/fragment/drums/sample", "clips", {"voice": "kick", "clip": "tone"}, _tone),
]


def test_every_post_route_has_a_case():
    posts = {p for methods, p in EXPECTED_ROUTES if "POST" in methods} - LOCK_FREE
    assert {c.path for c in CASES} == posts


def _url(case: Case, client) -> str:
    if case.url == "__take__":
        clip = _st(client).song["sections"][0]["audio"][0]["clip"]
        return f"/song/take/{clip}/rename"
    if case.url:
        return case.url
    if "{pattern_id}" in case.path:  # Perform's beat launcher
        pid = next(p for p in _st(client).song["patterns"].values())["id"]
        return case.path.format(pattern_id=pid)
    if "{section_id}" in case.path:
        return case.path.format(section_id=_st(client).song["sections"][0]["id"])
    return case.path


def _answers_a_fragment(r) -> bool:
    """A response that swaps part of the page: the only kind that carries
    regions (a whole page redraws them all, a navigation leaves)."""
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith(
        "text/html"
    ):
        return False
    if {"hx-location", "hx-redirect", "hx-refresh"} & {k.lower() for k in r.headers}:
        return False
    return 'id="view"' not in r.text


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_a_route_refreshes_exactly_the_regions_it_changed(tmp_path: Path, case):
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    case.setup(client)
    views = [v for v in PAGE_REGIONS if v != case.view] + [case.view]
    before = {v: _page(svc, v) for v in views}
    watched = {v: _watches(svc, v) for v in views}

    data = case.data(client) if callable(case.data) else case.data
    files = case.files(client) if case.files else None
    r = client.post(_url(case, client), data=data, files=files, headers=_hx(case.view))
    assert r.status_code < 500, r.text[:300]

    after = {v: _page(svc, v) for v in views}
    moved = {v: _watches(svc, v) for v in views}
    # every region, on every page: its HTML never changes behind its watch
    for v in views:
        for n in PAGE_REGIONS[v]:
            if before[v][n] != after[v][n]:
                assert watched[v][n] != moved[v][n], f"{n} on {v}: watch missed it"

    page = PAGE_REGIONS[case.view]
    changed = {n for n in page if before[case.view][n] != after[case.view][n]}
    oob = _oob_ids(r.text) & set(page)
    if not _answers_a_fragment(r):
        # the page is redrawn, left, or answered with data/a toast
        assert not oob, f"{case.id} sent {sorted(oob)} with a {r.status_code}"
        if r.status_code >= 400 or "json" in r.headers.get("content-type", ""):
            assert changed <= case.suppressed, f"{case.id} left {sorted(changed)}"
        return
    drawn = {n for n in page if f'id="{n}"' in r.text}  # OOB or the main target
    drawn |= {n for n in page if REGIONS[n].inside in drawn}
    stale = changed - case.suppressed - drawn
    assert not stale, f"{case.id} left {sorted(stale)} stale"
    assert not (oob - changed), f"{case.id} re-sent unchanged {sorted(oob - changed)}"
    assert not (oob & case.suppressed), f"{case.id} redrew {sorted(oob)}"
    for n in oob:  # each one is the current render
        assert svc.oob_regions(_request(case.view), n, view=case.view) in r.text, n


# The regions routes used to forget (each route had to name them by hand):
# an FX edit left the chain bar and the reset menu stale; a Perform parts or
# sound launch, a drum kit preset and a macro assignment left the reset menu.
PREVIOUSLY_STALE = [
    (
        "/fragment/fx/add",
        "editor",
        {"scope": "layer", "kind": "reverb"},
        _nothing,
        {"signal-chain", "reset-menu"},
    ),
    (
        "/fragment/fx/remove",
        "editor",
        {"scope": "layer", "index": "0"},
        _fx("reverb"),
        {"signal-chain", "reset-menu"},
    ),
    (
        "/fragment/fx/move",
        "editor",
        {"scope": "layer", "index": "0", "dir": "1"},
        _fx_baselined("reverb", "drive"),
        {"signal-chain", "reset-menu"},
    ),
    (
        "/fragment/fx/set",
        "editor",
        {"scope": "layer", "index": "0", "name": "mix", "value": "0.7"},
        _fx_baselined("reverb"),
        {"reset-menu"},
    ),
    ("/fragment/perform/parts", "perform", {"part": "drums"}, _nothing, {"reset-menu"}),
    (
        "/fragment/perform/lane/sound",
        "perform",
        {"index": "0", "sound": "preset:zap"},
        _preset,
        {"reset-menu"},
    ),
    (
        "/fragment/drums/preset",
        "drums",
        {"voice": "kick", "preset": "Default"},
        lambda c: c.post(
            "/fragment/drums", data={f"kick.{_KICK}": "0.4"}, headers=_hx("drums")
        ),
        {"reset-menu"},
    ),
    (
        "/fragment/macro/0/assign",
        "editor",
        {"param": "cutoff", "amt": "0.5"},
        _nothing,
        {"reset-menu"},
    ),
]


@pytest.mark.parametrize(
    "path,view,data,setup,regions",
    PREVIOUSLY_STALE,
    ids=[r[0] for r in PREVIOUSLY_STALE],
)
def test_the_regions_routes_used_to_forget(tmp_path, path, view, data, setup, regions):
    client = TestClient(create_app(data_dir=tmp_path))
    setup(client)
    r = client.post(path, data=data, headers=_hx(view))
    assert r.status_code == 200, r.text[:300]
    assert regions <= _oob_ids(r.text), sorted(regions - _oob_ids(r.text))


def test_a_state_change_on_another_page_reaches_its_regions(tmp_path):
    """Watches read state, not requests: a Song edit moves Perform's
    launchers' watches, which the next Perform request would carry."""
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    use_song(client)
    perform = _watches(svc, "perform")
    client.post("/song/section/s1/rename", data={"name": "Start"}, headers=_hx("song"))
    # (the song's sections are the Loops panel's: there is no Sections
    # launcher, `scene-launcher`)
    assert _watches(svc, "perform")["loops-panel"] != perform["loops-panel"]
    client.post("/song/pattern/pd/rename", data={"name": "Groove"}, headers=_hx("song"))
    assert _watches(svc, "perform")["launcher"] != perform["launcher"]


@pytest.mark.parametrize("name", sorted(REGIONS))
def test_every_region_matches_the_element_its_page_renders(tmp_path, name):
    """An OOB fragment only lands if its page has an element with the same
    id — a region whose id drifted from its page would silently drop."""
    client = TestClient(create_app(data_dir=tmp_path))
    save_clip(tmp_path, "riser", np.zeros((4410, 2)))
    svc = client.app.state.svc
    for view in sorted(REGIONS[name].pages):
        assert f'id="{name}"' in client.get(f"/view/{view}").text, (name, view)
        html = svc.oob_regions(_request(view), name, view=view)
        assert _oob_ids(html) == {name}
        assert html.count("hx-swap-oob") == 1  # exactly one region, the root
