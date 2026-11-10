import wave
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app
from synthlab.synth import Patch


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


def test_note_endpoint_serves_wav(tmp_path):
    r = make_client(tmp_path).get("/note/C4.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    assert "no-store" in r.headers.get("cache-control", "")


def test_note_endpoint_rejects_garbage(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/note/HAX.wav").status_code == 404
    assert client.get("/note/C99.wav").status_code == 404
    # octave 10 is past MIDI 127 (G9): the route rejects it by design, so the
    # client must never ask for it (see the bridge fold test below)
    assert client.get("/note/C10.wav").status_code == 404
    assert client.get("/note/G9.wav").status_code == 200


def test_piano_bridge_folds_notes_into_midi_range():
    # Chord pads, stacked typing rows and MIDI all enter through
    # window.synthlab.piano.noteOn with a raw MIDI number. Row lifts plus a
    # high octave used to push it past 127, so the WAV fallback asked for
    # /note/C10.wav and 404ed (the worklet still sounded, hiding it).
    src = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    bridge = src[src.index("window.synthlab.piano = {") :]
    bridge = bridge[: bridge.index("};")]
    assert "noteOn: (id, midi, vel) => noteOn(id, fakeKey(inMidiRange(midi))" in bridge
    fold = src[src.index("const inMidiRange") :].split("\n", 1)[0]
    assert "127" in fold and "12" in fold


def test_note_endpoint_survives_long_release_patch(tmp_path):
    client = make_client(tmp_path)
    client.app.state.studio.patch = replace(Patch(), release=1.59)
    r = client.get("/note/C4.wav")
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    with wave.open(BytesIO(r.content), "rb") as w:
        frames = w.readframes(w.getnframes())
    assert max(frames) > 0  # genuinely non-silent, not just a valid header


def test_piano_strip_in_editor(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="piano"' in html
    assert html.count('class="pkey') == 60  # five octaves: CSS shows what fits
    assert html.count("pkey black") == 25  # 5 black keys per octave
    assert 'data-dir="-1"' in html and 'data-dir="1"' in html
    assert "keyhints" not in html  # old hint row gone


def test_space_slider_in_editor(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'id="space"' in html


def test_panel_sliders_reach_the_live_mod_bus():
    """The XY pad changes sounding voices in real time; the panel sliders —
    the controls you actually tune with — would be silent mid-drag if they
    only posted on a trailing debounce. They must ride the same worklet
    mod + liveMod path."""
    src = Path("synthlab/studio/static/piano.js").read_text(encoding="utf-8")
    assert 'startsWith("p-")' in src
    # every numeric p-* range, not five LIVE_PARAMS, read
    # through logVal on log-taper sliders
    assert 'typeof patch[name] !== "number"' in src
    assert "window.synthlab.logVal(e.target)" in src
    assert '{ type: "mod", param: name, value }' in src
