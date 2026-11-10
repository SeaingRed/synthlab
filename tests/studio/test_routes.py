import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from synthlab.studio.server import create_app


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_dir=tmp_path))


# The note grid moved to Perform (Synths is a sound bench and renders a fixed
# reference phrase instead). Grid posts must therefore identify as Perform, or
# they get the sound bar back OOB and the phrase rendered.
PERFORM = {"HX-Current-URL": "http://testserver/view/perform"}


def test_editor_page_has_all_controls_and_island(tmp_path):
    r = make_client(tmp_path).get("/")
    assert r.status_code == 200
    html = r.text
    for name in ("cutoff", "resonance", "attack", "drive", "sub_level"):
        assert f'name="{name}"' in html
    assert 'name="cell.A1.0"' not in html  # the grid is Perform's now
    assert 'id="scope"' in html and 'id="spectrum"' in html
    assert "audio.js" in html and "htmx.min.js" in html
    assert 'data-theme="system"' in html  # default theme
    assert 'aria-label="play or stop the reference phrase"' in html
    assert 'aria-label="go to the other slot"' in html
    assert 'name="root"' in html and 'name="scale_name"' in html  # key stays


def test_patch_post_returns_analysis_and_trigger(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/patch", data={"cutoff": "500", "cell.A1.0": "on", "bpm": "124"}
    )
    assert r.status_code == 200
    assert 'id="analysis"' in r.text and "hx-swap-oob" in r.text
    trigger = json.loads(r.headers["HX-Trigger"])
    url = trigger["preview-ready"]["url"]
    wav = client.get(url)
    assert wav.status_code == 200
    assert wav.content[:4] == b"RIFF"
    assert "immutable" in wav.headers.get("cache-control", "")


def test_patch_post_persists_state(tmp_path):
    # `with` runs the app lifespan: shutdown flushes the debounced save.
    with make_client(tmp_path) as client:
        client.post("/fragment/patch", data={"cutoff": "500", "bpm": "124"})
    client2 = make_client(tmp_path)
    assert 'value="500' in client2.get("/").text  # cutoff control shows 500


def test_bpm_clamp_regression(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/fragment/arrangement", data={"bpm": "40", "bars": "1"})
    # 1 bar @40bpm = 6s (under the 10s cap) — post bpm below clamp does NOT
    # error. RenderError -> 422 is covered by the unit-level test below.
    assert r.status_code == 200


def test_render_error_maps_to_422(tmp_path, monkeypatch):
    # the render path lives in StudioService now (service.py), so that is
    # where render_loop is looked up
    import synthlab.studio.service as service_mod
    from synthlab.studio.render import RenderError

    client = make_client(tmp_path)

    def boom(inp, **kw):
        raise RenderError("too extreme")

    monkeypatch.setattr(service_mod, "render_loop", boom)
    r = client.post("/fragment/patch", data={"cutoff": "500"})
    assert r.status_code == 422
    assert "too extreme" in r.text and 'id="toasts"' in r.text


def test_theme_toggle_flips_cookie(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/theme")
    assert r.headers.get("HX-Refresh") == "true"
    assert client.cookies.get("theme") == "day"
    client.post("/theme")
    assert client.cookies.get("theme") == "night"
    page = client.get("/")
    assert 'data-theme="night"' in page.text


def test_preview_404_when_not_cached(tmp_path):
    assert make_client(tmp_path).get("/preview/000000000000.wav").status_code == 404


def test_cache_fast_path_same_input_same_url(tmp_path):
    client = make_client(tmp_path)
    r1 = client.post("/fragment/patch", data={"cutoff": "500", "bpm": "124"})
    r2 = client.post("/fragment/patch", data={"cutoff": "500", "bpm": "124"})
    import json as _json

    u1 = _json.loads(r1.headers["HX-Trigger"])["preview-ready"]["url"]
    u2 = _json.loads(r2.headers["HX-Trigger"])["preview-ready"]["url"]
    assert u1 == u2


def test_the_theme_toggle_under_system_flips_the_theme_in_force(tmp_path):
    """Under "system" the toggle posts the theme the OS shows (shell.js):
    the first press must change what is seen — it set "day" on a light OS."""
    client = make_client(tmp_path)
    client.post("/theme", data={"from": "day"})
    assert client.cookies.get("theme") == "night"
    client.cookies.clear()
    client.post("/theme", data={"from": "night"})
    assert client.cookies.get("theme") == "day"
    client.post("/theme", data={"from": "night"})  # a theme chosen: `from` is moot
    assert client.cookies.get("theme") == "night"


def test_the_rail_runs_in_the_order_of_the_job_with_jump_keys(tmp_path):
    html = make_client(tmp_path).get("/").text
    nav = html[html.index('<nav class="views"') : html.index("</nav>")]
    order = re.findall(r'href="/view/(\w+)"', nav)
    assert order == [
        "editor",
        "drums",
        "perform",
        "song",
        "mixer",
        "library",
        "breed",
        "map",
        "voice",
        "lab",
        "clips",
        "visuals",  # the dock's scopes and spectrums: no jump key (eleventh+)
    ]
    keys = re.findall(r'data-keys="Alt\+Shift\+(\d)', nav)
    assert keys == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    assert ">Clips</span>" in nav and ">Hub<" not in nav


def test_no_cookie_ships_system_theme(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert 'data-theme="system"' in html


def test_cookie_still_wins(tmp_path):
    client = make_client(tmp_path)
    client.post("/theme")  # system -> day (first toggle from non-day)
    assert 'data-theme="day"' in client.get("/").text


def test_transport_v2_controls_present(tmp_path):
    grid = make_client(tmp_path).get("/view/perform").text
    for frag in (
        'name="bars"',
        'name="steps"',
        'name="root"',
        'name="scale_name"',
        'name="swing"',
        'name="vel.0"',
        'name="cell.A1.0"',
    ):
        assert frag in grid, frag


def test_arrangement_v2_roundtrip(tmp_path):
    client = make_client(tmp_path)
    client.post(
        "/fragment/arrangement",
        data={
            "bpm": "128",
            "bars": "2",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
            "swing": "0.2",
            "cell.A1.17": "on",
            "drum.kick.0": "on",
            "vel.17": "0.7",
        },
    )
    html = client.get("/view/perform").text
    assert 'name="cell.A1.17"' in html and "checked" in html


def test_enter_submit_is_intercepted(tmp_path):
    html = make_client(tmp_path).get("/").text
    assert html.count("delay:150ms, submit") >= 2  # both forms


def test_clamped_bpm_reflected_oob(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/arrangement",
        data={
            "bpm": "10",
            "bars": "1",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
        },
    )
    # bpm moved to the Synths sound bar, so that is what comes back OOB
    assert 'id="sound-bar"' in r.text and 'value="40"' in r.text


def test_cap_toast_mentions_previous_loop(tmp_path, monkeypatch):
    from synthlab.studio import render

    # nothing the controls offer reaches the real cap (72 s)
    monkeypatch.setattr(render, "LOOP_CAP_SECONDS", 10.0)
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/arrangement",
        data={
            "bpm": "40",
            "bars": "4",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
        },
        headers=PERFORM,  # only Perform renders the real loop, so only it can cap
    )
    assert r.status_code == 422 and "previous loop" in r.text


def test_panel_css_clips_overflow(tmp_path):
    css_path = Path(__file__).parents[2] / "synthlab/studio/static/style.css"
    css = css_path.read_text(encoding="utf-8")
    assert "overflow: clip" in css
    assert "minmax(0, 1fr)" in css


def test_structural_change_reshapes_transport_oob(tmp_path):
    client = make_client(tmp_path)
    r = client.post(
        "/fragment/arrangement",
        data={
            "bpm": "124",
            "bars": "2",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
        },
        headers=PERFORM,
    )
    assert 'id="transport"' in r.text and "hx-swap-oob" in r.text
    assert 'name="cell.A1.31"' in r.text  # 2 bars x 16 steps rendered
    r2 = client.post(
        "/fragment/arrangement",
        data={
            "bpm": "125",
            "bars": "2",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
        },
        headers=PERFORM,
    )
    assert 'id="transport"' not in r2.text  # bpm-only change: no OOB reshape


def test_default_is_four_steps(tmp_path):
    from synthlab.studio.state import Arrangement

    a = Arrangement()
    assert a.steps_per_bar == 4 and a.cells == [["A1", 0], ["A1", 2]]
    html = make_client(tmp_path).get("/view/perform").text
    assert 'name="cell.A1.3"' in html and 'name="cell.A1.4"' not in html
    assert ">32<" in html and ">4<" in html  # steps options


def test_bar_chips_render_and_extend(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/view/perform").text
    # the note grid's chips (Perform's drum rack below it has its own four)
    notes = html.split('id="perform-drums"')[0]
    assert notes.count(
        '<button type="button" class="barchip'
    ) == 4 and 'name="bars" value="1"' in notes.replace("'", '"')
    r = client.post(
        "/fragment/arrangement",
        data={
            "bpm": "124",
            "bars": "2",
            "steps": "16",
            "root": "A1",
            "scale_name": "pentatonic_minor",
        },
        headers=PERFORM,
    )
    assert "barchip on" in r.text and 'name="cell.A1.31"' in r.text


def test_patch_json_serves_current_patch(tmp_path):
    r = make_client(tmp_path).get("/patch.json")
    assert r.status_code == 200
    body = r.json()
    assert "osc1" in body and "cutoff" in body and "release" in body
    assert "voice_fx" not in body
    assert "no-store" in r.headers.get("cache-control", "")


def test_preset_star_and_audition(tmp_path):
    client = make_client(tmp_path)
    client.post("/fragment/preset/save", data={"name": "zap"})
    r = client.post("/fragment/preset/star", data={"name": "zap"})
    assert r.status_code == 200
    w = client.get("/preset/zap/audition.wav")
    assert w.status_code == 200 and w.content[:4] == b"RIFF"
    assert client.get("/preset/nope/audition.wav").status_code == 404


# -- the route split -------------------------------------
# Captured from the single-closure server.py before it was split into
# routers: the split is a mechanical move, so the (methods, path) inventory
# and each route's lock policy must come out exactly as they went in.
EXPECTED_ROUTES = [
    (["GET"], "/"),
    (
        ["GET"],
        "/beat.wav",
    ),  # Synths' ▶ beat: the Drums render, redirected to its preview
    (["GET"], "/breed/note/{tid}.wav"),
    (["GET"], "/breed/{tid}/audition.wav"),
    (["GET"], "/clip/{name}.wav"),
    (["GET"], "/debug/stats"),
    (["GET"], "/drum/{voice}/hit.wav"),
    (["GET"], "/events"),  # server push: render progress, change notices (SSE)
    (["GET"], "/export/loop.mid"),  # MIDI export: the Perform loop's notes
    (["GET"], "/export/song.mid"),  # ... and the song's
    (["GET"], "/export/song.wav"),
    (["GET"], "/export/song/loudness"),
    (["GET"], "/export/stems.zip"),
    (["GET"], "/factory/{name}/audition.wav"),
    (["GET"], "/fragment/bass"),
    (["GET"], "/fragment/chordlib"),
    # the clip library drawer: its list for a search, itself when opened
    (["GET"], "/fragment/clips"),
    (["GET"], "/fragment/curve-close"),
    (["GET"], "/fragment/curve/{param}"),
    (["GET"], "/fragment/free/shaper"),  # free record's shaper (routes/freerec.py)
    # the clip hub (routes/cliphub.py): an item's bench, the filtered list,
    # the player for a tune, the send panel for a Place section
    (["GET"], "/fragment/hub/bench"),
    (["GET"], "/fragment/hub/list"),
    (["GET"], "/fragment/hub/player"),
    (["GET"], "/fragment/hub/send"),
    # the Sample Lab (routes/lab.py): its body, the job's polled status line
    (["GET"], "/fragment/lab"),
    (["GET"], "/fragment/lab/job"),
    (["GET"], "/fragment/map"),
    (["GET"], "/fragment/module/{key}"),  # a module a page adds (workspace.py MODULES)
    (["GET"], "/fragment/palette"),  # the command palette's search: sounds, clips
    # song creation: the checklist, and the chord strip for one scope
    (["GET"], "/fragment/song/check"),
    (["GET"], "/fragment/sound-menu"),  # the one sound menu, again (soundmenu.js)
    (["GET"], "/fragment/vocal"),  # the live vocal chain's panel, when it opens
    (["GET"], "/fragment/wavetable/sources"),  # "+ from clip"'s menu
    (["GET"], "/fx/ir.wav"),  # a reverb's room for the live keys
    (["GET"], "/health"),
    (["GET"], "/hub/audio.wav"),  # the clip hub's player: a clip or sample, tuned
    (["GET"], "/lab/{sid}/chop/{cid}.wav"),  # a chop's audition: its mix, cleaned
    (["GET"], "/lab/{sid}/region.wav"),  # the Lab's audition: a region as a WAV
    # whole stems out of the Lab: one as FLAC or WAV, a set as a zip
    (["GET"], "/lab/{sid}/stem/{name}.flac"),
    (["GET"], "/lab/{sid}/stem/{name}.wav"),
    (["GET"], "/lab/{sid}/stems.zip"),
    (["GET"], "/library/{name}/audition.wav"),
    (["GET"], "/note/{pitch}.wav"),
    (["GET"], "/patch.json"),
    (["GET"], "/preset/{name}/audition.wav"),
    (["GET"], "/preview/{key}.wav"),
    (["GET"], "/project.json"),
    (["GET"], "/project.zip"),  # the project with its samples and clips
    (["GET"], "/recipe/{name}/audition.wav"),  # genre recipes shelf
    # the live voice's sampler buffers (sampler.live_payload), lock-free
    (["GET"], "/sampler/live/{sid}.bin"),
    (["GET"], "/song/calibration.json"),  # mic latency
    (["GET"], "/song/cell/{section_id}/{track_id}.wav"),  # "solo this cell"
    # the pattern editor's ▶ Play on a pool pattern, through a lane's sound
    (["GET"], "/song/pattern/{pattern_id}/play.wav"),
    (["GET"], "/song/plan.json"),  # the player's plan
    (["GET"], "/song/section/{section_id}.wav"),
    # the finger drums: a voice of the song drums track's own kit
    (["GET"], "/song/track/{track_id}/hit/{voice}.wav"),
    (["GET"], "/view/{name}"),
    (["GET"], "/voice/live.json"),  # the live vocal chain, resolved for vocal.js
    (["GET"], "/voice/take.wav"),
    (["GET"], "/wavetable/live/{tid}.bin"),  # your own table, live
    (["GET"], "/wavetables.bin"),  # the live voice's engine tables
    (["GET", "HEAD"], "/docs"),
    (["GET", "HEAD"], "/docs/oauth2-redirect"),
    (["GET", "HEAD"], "/openapi.json"),
    (["GET", "HEAD"], "/redoc"),
    (["POST"], "/clip/bounce"),
    (["POST"], "/clip/upload"),
    (["POST"], "/fragment/ab/{action}"),
    (["POST"], "/fragment/agent/ab"),  # the Sound agent drawer (studio/agent)
    (["POST"], "/fragment/agent/apply"),
    (["POST"], "/fragment/agent/budget"),
    (["POST"], "/fragment/agent/turn"),
    (["POST"], "/fragment/agent/undo"),
    (["POST"], "/fragment/arrangement"),
    (["POST"], "/fragment/bass/key"),
    (["POST"], "/fragment/bass/phone-audition"),
    (["POST"], "/fragment/beat/pick"),
    (["POST"], "/fragment/bend-range"),
    (["POST"], "/fragment/breed/adopt"),
    (["POST"], "/fragment/breed/audition"),
    (["POST"], "/fragment/breed/depth"),  # layered breeding: the depth select
    (["POST"], "/fragment/breed/generation"),
    (["POST"], "/fragment/breed/restore"),
    (["POST"], "/fragment/breed/star"),
    (["POST"], "/fragment/breed/wildness"),
    (["POST"], "/fragment/chords/code"),
    (["POST"], "/fragment/chords/grid"),
    # Perform's chord lane: the working loop's chord track
    (["POST"], "/fragment/chords/track"),
    (["POST"], "/fragment/clip/delete"),
    (["POST"], "/fragment/clip/rename"),  # jams are named afterwards
    # Perform's ✦ Coach (routes/coach.py)
    (["POST"], "/fragment/coach/accept"),
    (["POST"], "/fragment/coach/audition"),
    (["POST"], "/fragment/coach/chat"),
    (["POST"], "/fragment/coach/discard"),
    (["POST"], "/fragment/coach/propose"),
    (["POST"], "/fragment/coach/reverse"),
    (["POST"], "/fragment/coach/show"),
    (["POST"], "/fragment/coach/talk"),
    (["POST"], "/fragment/curve/{param}"),
    (["POST"], "/fragment/curve/{param}/clear"),
    (["POST"], "/fragment/curve/{param}/set"),
    (["POST"], "/fragment/drums"),
    (["POST"], "/fragment/drums/bank/factory"),  # factory restore
    (["POST"], "/fragment/drums/bank/load/{slot}"),
    (["POST"], "/fragment/drums/bank/save/{slot}"),
    (["POST"], "/fragment/drums/clear"),
    (["POST"], "/fragment/drums/grid"),
    (["POST"], "/fragment/drums/kit"),  # genre kits
    (["POST"], "/fragment/drums/lib/delete"),
    (["POST"], "/fragment/drums/lib/load"),
    (["POST"], "/fragment/drums/lib/save"),
    (["POST"], "/fragment/drums/mutate"),
    (["POST"], "/fragment/drums/notation"),
    (["POST"], "/fragment/drums/preset"),
    (["POST"], "/fragment/drums/randomise"),
    (["POST"], "/fragment/drums/row/{voice}/{op}"),
    (["POST"], "/fragment/drums/sample"),
    (["POST"], "/fragment/drums/transport"),
    (["POST"], "/fragment/drums/var"),  # the beat's variations A-D
    (["POST"], "/fragment/factory/load"),
    (["POST"], "/fragment/fx/add"),
    (["POST"], "/fragment/fx/chain"),  # "+ add chain…" presets
    (["POST"], "/fragment/fx/move"),
    (["POST"], "/fragment/fx/remove"),
    (["POST"], "/fragment/fx/set"),
    (["POST"], "/fragment/gen"),  # a generator on the loop's beat
    (["POST"], "/fragment/grid/span"),
    (["POST"], "/fragment/layer/add"),
    (["POST"], "/fragment/layer/clear"),
    (["POST"], "/fragment/layer/focus"),
    (["POST"], "/fragment/layer/follow"),
    (["POST"], "/fragment/layer/gain"),
    (["POST"], "/fragment/layer/mute"),
    (["POST"], "/fragment/layer/select"),
    (["POST"], "/fragment/layer/solo"),
    (["POST"], "/fragment/library/load"),
    (["POST"], "/fragment/load"),
    (["POST"], "/fragment/macro/{index}"),
    (["POST"], "/fragment/macro/{index}/assign"),
    (["POST"], "/fragment/meter"),  # the loop's meter (meter.py)
    (["POST"], "/fragment/mod/mix"),  # the mod matrix in one move (modmixes.py)
    (["POST"], "/fragment/morph"),
    (["POST"], "/fragment/panel/dice"),
    (["POST"], "/fragment/panel/preset/load"),
    (["POST"], "/fragment/panel/preset/save"),
    (["POST"], "/fragment/panel/reset"),
    (["POST"], "/fragment/patch"),
    (["POST"], "/fragment/pattern/drums/{op}"),
    (["POST"], "/fragment/pattern/import"),  # a .mid file into the note grid
    (["POST"], "/fragment/pattern/notes/{op}"),
    (["POST"], "/fragment/pattern/undo"),
    (["POST"], "/fragment/perform/bank/{slot}"),
    (["POST"], "/fragment/perform/lane/sound"),  # a lane head's sound ▾
    (["POST"], "/fragment/perform/launch/{pattern_id}"),
    (["POST"], "/fragment/perform/parts"),
    # Perform's launcher (routes/slots.py); there is no Sections
    # launcher's /fragment/perform/section/{section_id}
    (["POST"], "/fragment/perform/scene/capture"),  # → the song
    (["POST"], "/fragment/perform/scene/idea"),
    (["POST"], "/fragment/perform/scene/keep"),
    (["POST"], "/fragment/perform/scene/launch"),
    (["POST"], "/fragment/perform/scene/section"),
    (["POST"], "/fragment/perform/scene/song"),  # a scene → a section
    (["POST"], "/fragment/perform/slot/clear"),
    (["POST"], "/fragment/perform/slot/keep"),
    (["POST"], "/fragment/perform/slot/launch"),
    (["POST"], "/fragment/perform/slot/move"),
    (["POST"], "/fragment/perform/slot/place"),
    (["POST"], "/fragment/perform/slot/select"),
    (["POST"], "/fragment/perform/slot/stop"),
    (["POST"], "/fragment/perform/xy"),
    (["POST"], "/fragment/preset/delete"),
    (["POST"], "/fragment/preset/load"),
    (["POST"], "/fragment/preset/rename"),
    (["POST"], "/fragment/preset/save"),
    (["POST"], "/fragment/preset/star"),
    (["POST"], "/fragment/preview"),
    (["POST"], "/fragment/recipe/load"),  # genre recipes: Use
    (["POST"], "/fragment/reset"),
    (["POST"], "/fragment/restore_prev"),
    # the sampler instrument: a clip / a resample becomes a sound, chops
    (["POST"], "/fragment/sampler/chop"),
    (["POST"], "/fragment/sampler/clip"),
    # ● Record sample: a timed mic take into the clip store and the sampler
    (["POST"], "/fragment/sampler/record"),
    (["POST"], "/fragment/sampler/resample"),
    (["POST"], "/fragment/sampler/root"),  # "use" the detected pitch
    (["POST"], "/fragment/sampler/slice"),
    # a multi-sample instrument: one zone's edit, a clip as a zone, the map's
    # tools, auto-map (several clips as one sound)
    (["POST"], "/fragment/sampler/zone"),
    (["POST"], "/fragment/sampler/zone/add"),
    (["POST"], "/fragment/sampler/zones"),
    (["POST"], "/fragment/sampler/zones/map"),
    (["POST"], "/fragment/undo"),
    # your own wavetable, from a clip or drawn (routes/wavetable.py)
    (["POST"], "/fragment/wavetable/clip"),
    (["POST"], "/fragment/wavetable/draw"),
    (["POST"], "/fragment/xy"),
    (["POST"], "/fragment/xy/axes"),
    (["POST"], "/fragment/xy/center"),
    (["POST"], "/fragment/xy/gesture"),
    # the clip hub: make (a preview), Apply, its Undo; a sample kept as a
    # clip, a clip opened in the Lab; the tuned audio kept new or written over
    (["POST"], "/hub/apply"),
    (["POST"], "/hub/keep"),
    (["POST"], "/hub/lab"),
    (["POST"], "/hub/make"),
    (["POST"], "/hub/tune/keep"),
    (["POST"], "/hub/tune/update"),
    (["POST"], "/hub/undo"),
    # the Sample Lab: a song in, its grid and region, where the region goes,
    # and the separation job
    (["POST"], "/lab/cancel"),
    (["POST"], "/lab/chop"),  # every edit of a source's chops, by `op`
    (["POST"], "/lab/chop/keep"),  # a chop, cleaned, into the clip store
    (["POST"], "/lab/delete"),
    (["POST"], "/lab/drums"),  # the drums of a region: drum grid, pool beat, kit
    (["POST"], "/lab/grid"),
    (["POST"], "/lab/key"),  # the key named by hand, or read again from stems
    (["POST"], "/lab/place"),  # a region onto a Song audio track
    (["POST"], "/lab/region"),
    (["POST"], "/lab/select"),
    (["POST"], "/lab/send"),
    (["POST"], "/lab/separate"),
    (["POST"], "/lab/undo"),  # take back what a make just did (its said line)
    (["POST"], "/lab/upload"),
    (["POST"], "/lab/view"),  # zoom and scroll the waveform
    (["POST"], "/lab/vocal"),  # the vocal as a sampler voice, or chops
    # loops (loops.py, routes/takes.py): a take or the lane, kept with its sound
    (["POST"], "/loop/beat"),
    (["POST"], "/loop/beat/keep"),
    (["POST"], "/loop/bounce"),
    (["POST"], "/loop/delete"),
    (["POST"], "/loop/edit"),
    (["POST"], "/loop/edit/done"),
    (["POST"], "/loop/load"),
    (["POST"], "/loop/preview"),
    (["POST"], "/loop/quantise"),
    (["POST"], "/loop/rename"),
    (["POST"], "/loop/save"),
    (["POST"], "/loop/song"),
    (["POST"], "/loop/star"),
    (["POST"], "/loop/tag"),
    (["POST"], "/project/import"),
    # song creation: a style's
    # whole song / the loop arranged / one part by its generator / the
    # song's title, tempo and key / the chord track — each one commit
    (["POST"], "/song/arrange"),
    # the playlist tools (routes/song_arr.py): an edit at a bar of the
    # timeline (cut / insert / delete), a transition on a boundary
    (["POST"], "/song/bars"),
    (["POST"], "/song/boundary"),
    (["POST"], "/song/calibration"),
    (["POST"], "/song/cell"),  # arranging
    (["POST"], "/song/cell/auto"),  # section automation lanes
    (["POST"], "/song/cell/move"),  # a cell onto another, source cleared: one step
    (["POST"], "/song/cell/new"),  # a new empty pattern straight into a cell
    (["POST"], "/song/cell/span"),  # where a block sits in its section; spill
    (["POST"], "/song/cell/unique"),
    (["POST"], "/song/cell/variation"),
    (["POST"], "/song/cells"),  # a range of cells, by `op`: one undo step
    (["POST"], "/song/chords"),
    (["POST"], "/song/dock/close"),  # the sound dock's ✕: ends the track edit
    (["POST"], "/song/drawer"),
    (["POST"], "/song/drums/loop-kit"),  # → Song's offer: the loop's kit on the drums
    (["POST"], "/song/energy"),  # the energy lane; op=arrange
    (["POST"], "/song/from-loop"),
    (["POST"], "/song/gen"),
    # the jam (routes/song_jam.py): the launcher's log becomes sections
    (["POST"], "/song/jam/commit"),
    # a whole stem on the timeline: op add | set | remove | tempo
    (["POST"], "/song/long"),
    (["POST"], "/song/meta"),
    (["POST"], "/song/migration-note/clear"),  # shown once
    (["POST"], "/song/mix"),  # the mixer: balance, a return added / removed
    (["POST"], "/song/mix/return/{return_id}"),
    (["POST"], "/song/mix/strip/{track_id}"),
    (["POST"], "/song/pattern/add"),
    (["POST"], "/song/pattern/{pattern_id}/delete"),
    (["POST"], "/song/pattern/{pattern_id}/duplicate"),  # a pool copy
    (["POST"], "/song/pattern/{pattern_id}/grid"),
    (["POST"], "/song/pattern/{pattern_id}/record"),  # note takes
    (["POST"], "/song/pattern/{pattern_id}/rename"),
    (["POST"], "/song/pattern/{pattern_id}/tool/{op}"),
    (["POST"], "/song/record/undo"),
    (["POST"], "/song/redo"),  # the song's own undo ring
    (["POST"], "/song/section/add"),
    (["POST"], "/song/section/{section_id}/bars"),
    (["POST"], "/song/section/{section_id}/clip"),  # place a clip from the store
    (["POST"], "/song/section/{section_id}/delete"),
    (["POST"], "/song/section/{section_id}/duplicate"),
    (["POST"], "/song/section/{section_id}/move"),
    (["POST"], "/song/section/{section_id}/op"),  # split / unroll / insert / duplicate
    (["POST"], "/song/section/{section_id}/rename"),
    (["POST"], "/song/section/{section_id}/repeats"),
    (["POST"], "/song/section/{section_id}/time"),  # its own meter, tempo, ramp
    (["POST"], "/song/section/{section_id}/to-loop"),
    (["POST"], "/song/section/{section_id}/transition"),  # riser / impact takes
    (["POST"], "/song/start"),
    (["POST"], "/song/take"),  # mic takes
    (["POST"], "/song/take/clean"),  # ✦ Clean up: the last take, + Gemini
    (["POST"], "/song/take/edit"),  # a placed take: op move | remove
    (["POST"], "/song/take/{clip}/rename"),
    (["POST"], "/song/track/add"),
    (["POST"], "/song/track/{track_id}/delete"),
    (["POST"], "/song/track/{track_id}/edit-sound"),
    (["POST"], "/song/track/{track_id}/fx"),  # an audio track's rack
    (["POST"], "/song/track/{track_id}/mix"),
    (["POST"], "/song/track/{track_id}/rename"),
    (["POST"], "/song/track/{track_id}/sound"),  # a lane's sound menu
    (["POST"], "/song/undo"),
    # keys takes (routes/takes.py): ⏺ Rec's take, keep / discard / clear,
    # → Perform as notes or as a sample
    (["POST"], "/take/keys"),
    (["POST"], "/take/keys/capture"),
    (["POST"], "/take/keys/clean"),
    (["POST"], "/take/keys/clear"),
    (["POST"], "/take/keys/discard"),
    # free record (routes/freerec.py): the free take, the shaper's ▶ and Make loop
    (["POST"], "/take/keys/free"),
    (["POST"], "/take/keys/free/hear"),
    (["POST"], "/take/keys/free/loop"),
    (["POST"], "/take/keys/keep"),
    (["POST"], "/take/keys/notes"),
    (["POST"], "/take/keys/sample"),
    (["POST"], "/take/keys/unclean"),
    # the live vocal chain's ⏺: the processed take and the dry one; → Song
    (["POST"], "/take/vocal"),
    (["POST"], "/take/vocal/song"),
    (["POST"], "/theme"),
    # the vocal booth (4 · Treat): its rack, its tape moves, a treatment
    # heard, the audition kept as a clip or put on the song
    (["POST"], "/voice/booth/fx"),
    (["POST"], "/voice/booth/keep"),
    (["POST"], "/voice/booth/moves"),
    (["POST"], "/voice/booth/render"),
    (["POST"], "/voice/booth/song"),
    (["POST"], "/voice/carrier/clip"),  # the clip library's "as carrier"
    (["POST"], "/voice/keep"),
    # the live vocal chain (routes/vocal.py): its rack, a vocal style
    (["POST"], "/voice/live/fx"),
    (["POST"], "/voice/live/style"),
    (["POST"], "/voice/preset"),  # a named vocoder setting + its carrier Sound
    (["POST"], "/voice/render"),
    (["POST"], "/voice/result/keep"),  # the vocoded audition into the clip store
    (["POST"], "/voice/take"),
    (["POST"], "/voice/take/clip"),  # the clip library's "Use as take"
]

# the only routes that never wait on StudioState.lock
# (studio/docs/routing-and-boundary.md, "One concurrency model")
LOCK_FREE = {
    "/health",
    "/events",  # the push stream: an async generator, no thread, no lock
    "/preview/{key}.wav",
    "/debug/stats",
    "/theme",
    "/sampler/live/{sid}.bin",  # reads only the self-locking sample store
    "/wavetable/live/{tid}.bin",  # the same of the self-locking table store
    # the Sample Lab's audition and its job's status line: the source store
    # (self-locking) and the job's own state, never the session
    "/lab/{sid}/region.wav",
    "/fragment/lab/job",
    # a chop's audition and the stem downloads: the source store again (the
    # downloads are files streamed from disk, minutes long)
    "/lab/{sid}/chop/{cid}.wav",
    "/lab/{sid}/stem/{name}.flac",
    "/lab/{sid}/stem/{name}.wav",
    "/lab/{sid}/stems.zip",
    # the clip hub's player: the clip and sample stores and its own memo
    "/hub/audio.wav",
}
# the long renders: under the lock only to snapshot (`render_outside_lock`)
RENDER_OUTSIDE_LOCK = {
    "/export/song.wav",
    # the Song page's "measure": the export's own render, for its loudness
    "/export/song/loudness",
    "/export/stems.zip",
    "/song/plan.json",
    # the mixer's Balance: the song is measured (what is cold renders) from
    # a snapshot, and the gains are committed back under the lock
    "/song/mix",
    "/song/section/{section_id}.wav",
    # the drawer's "solo this cell": a section of the stems render
    "/song/cell/{section_id}/{track_id}.wav",
    # ... and on a pool pattern: a section of its own, through a lane
    "/song/pattern/{pattern_id}/play.wav",
    "/fragment/breed/generation",
    # the first visit to an empty Breed board generates its litter
    "/view/{name}",
    # a live key: a press used to hold the lock for its render
    "/note/{pitch}.wav",
    # a Sound agent turn: the Gemini calls run with the lock released; each
    # round of tool calls re-takes it to apply, render and measure
    "/fragment/agent/turn",
    # ✦ Clean up a take: Gemini's call with the lock released, the cleaned
    # pattern committed under it again
    "/song/take/clean",
    # ... and a keys take's notes (routes/takes.py), the same way
    "/take/keys/clean",
    # the loop coach's talk (routes/coach.py): one Gemini call a message,
    # typed or said, with the lock released; its reply and proposal are
    # written under it again
    "/fragment/coach/chat",
    "/fragment/coach/talk",
    # a song import: decode, resample and analyse, seconds of work on the
    # source store alone
    "/lab/upload",
    # its "read again": a song's stems decoded and analysed for their key
    "/lab/key",
    # a chop edit: Auto by transients / syllables and a cleaning's notes
    # listen to seconds of the source; all of it is the source store's
    "/lab/chop",
    # the vocal booth: a treatment runs over a take and its ring with the
    # lock released
    "/voice/booth/render",
    # the vocoder: seconds for a long take at 32 bands; the handler snapshots
    # (and renders the Synths loop when that is the carrier), the vocoding
    # runs with the lock released
    "/voice/render",
    # the clip hub: a make listens to the clip (and asks Gemini, with the
    # lock released) and renders its preview from a snapshot; an Apply makes
    # it again outside and commits under the lock; "Open in Lab" imports the
    # clip as a song (the Lab's own decode and analysis)
    "/hub/make",
    "/hub/apply",
    "/hub/lab",
}


def test_route_inventory_unchanged(tmp_path):
    client = make_client(tmp_path)
    routes = sorted(
        (sorted(r.methods), r.path) for r in client.app.routes if hasattr(r, "methods")
    )
    assert routes == EXPECTED_ROUTES


def test_lock_policy_unchanged(tmp_path):
    """Every studio route runs under the state lock (its endpoint is the
    lock wrapper around the handler) except the lock-free ones,
    whose endpoint is the bare handler."""
    import inspect

    from fastapi.routing import APIRoute

    app = make_client(tmp_path).app
    studio = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and not r.path.startswith(("/docs", "/openapi"))
    ]
    assert len(studio) == len(EXPECTED_ROUTES) - 4  # minus the FastAPI docs
    free = {r.path for r in studio if getattr(r.endpoint, "lock_free", False)}
    assert free == LOCK_FREE
    for r in studio:
        wrapped = r.endpoint is not inspect.unwrap(r.endpoint)
        assert wrapped == (r.path not in LOCK_FREE), r.path
    # snapshot under the lock, render after releasing it (routing.py)
    deferred = {
        r.path for r in studio if getattr(r.endpoint, "render_outside_lock", False)
    }
    assert deferred == RENDER_OUTSIDE_LOCK


def test_views_table_is_the_only_registry():
    from synthlab.studio import state, views

    assert set(state.RESET_VIEWS) == {
        v for v, spec in views.VIEWS.items() if spec.reset_scopes
    }
    assert state.RESET_VIEWS is views.RESET_VIEWS
    assert views.VIEW_NAMES == tuple(views.VIEWS)
    assert views.VIEW_PARTS == {v: s.parts for v, s in views.VIEWS.items()}
    assert views.BENCH_VIEWS == {v for v, s in views.VIEWS.items() if s.bench}
    # pinned literally: these are the historical tables the registry replaced
    assert views.BENCH_VIEWS == {"editor", "library", "breed", "map", "bass"}
    assert views.VIEW_PARTS["perform"] == ("synth", "drums")
    assert views.VIEW_PARTS["drums"] == ("drums",)
    assert "voice" not in state.RESET_VIEWS  # falls back to ("sound",)


# -- lean fragment contexts ----------------------------------------------------
# page_context(need=...) builds only the expensive sections a template
# references. A drum-bank click used to build the whole page's context —
# re-reading the voice take, the clip store and every preset from disk —
# to render one strip.


def _populated_client(tmp_path):
    """A session where every expensive section has something in it, so a
    lean context that dropped a section a template needs would render
    differently (an absent key is falsy to Jinja, not an error)."""
    import numpy as np

    from synthlab.studio.clips import save_clip
    from synthlab.studio.voice import save_take

    save_take(tmp_path, np.zeros(4000))
    save_clip(tmp_path, "hit", np.zeros(4000, dtype=np.float32))
    client = make_client(tmp_path)
    client.post("/fragment/preset/save", data={"name": "zap"})
    client.post("/fragment/drums/lib/save", data={"name": "groove"})
    client.post("/fragment/ab/copy_a")
    return client


def test_bank_click_does_not_read_voice_take(tmp_path, monkeypatch):
    from synthlab.studio.voice import save_take

    save_take(tmp_path, __import__("numpy").zeros(4000))
    client = make_client(tmp_path)
    calls = []
    monkeypatch.setattr(
        "synthlab.studio.voice.read_wav", lambda *a, **k: calls.append(1)
    )
    r = client.post("/fragment/drums/bank/load/0")  # real bank route
    assert r.status_code == 200 and 'id="drum-bank"' in r.text
    assert not calls
    # the probe is live: a full page does read the take
    client.get("/view/voice")
    assert calls


def test_bank_click_does_not_list_presets_or_the_library(tmp_path, monkeypatch):
    from synthlab.studio import service
    from synthlab.studio.state import StudioState

    client = make_client(tmp_path)
    seen = []
    monkeypatch.setattr(
        StudioState, "list_presets_meta", lambda self: seen.append("presets") or []
    )
    monkeypatch.setattr(
        service, "library_by_family", lambda: seen.append("library") or []
    )
    client.post("/fragment/drums/bank/load/0")
    client.post("/fragment/drums/preset", data={"voice": "kick", "preset": "x"})
    client.post(
        "/fragment/arrangement",
        data={"bpm": "10", "bars": "1"},  # clamped: the sound bar comes back OOB
    )
    assert seen == []


def test_lean_fragments_render_exactly_what_the_full_context_renders(tmp_path):
    from starlette.requests import Request

    from synthlab.studio.service import ALL

    client = _populated_client(tmp_path)
    svc = client.app.state.svc
    req = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    from synthlab.studio.service import REGIONS

    # every region's own (lean) render is what the page's full context draws
    for name, region in REGIONS.items():
        tpl = svc.templates.get_template(region.template)
        for view in sorted(region.pages):
            full = svc.page_context(req, view, need=ALL)
            if region.template.endswith("panel_viz.html"):
                key = {
                    "filter-response-wrap": "filter_response",
                    "env-amp-wrap": "env_amp",
                    "env-filter-wrap": "env_filter",
                }[name]
                full = {**full, "viz_id": name, "svg": full[key]}
            expect = tpl.render({**full, "oob": True})
            assert svc.oob_regions(req, name, view=view) == expect, (name, view)
    assert svc.drum_panels_fragment(req) == svc.drum_panels_fragment(req, need=ALL)
    lean, full = svc.bank_response(req), svc.bank_response(req, need=ALL)
    assert lean.body == full.body
    assert 'value="hit"' in lean.body.decode()  # the clip select is populated
    # (the beat library is the Loops panel's: Kit saves there)
    assert (
        "/loop/beat/keep" in lean.body.decode() and "groove" not in lean.body.decode()
    )


def test_full_pages_still_get_every_section(tmp_path):
    """Full pages pass need=ALL: every section a view template reads is
    there (a missing one would render as empty, not raise)."""
    client = _populated_client(tmp_path)
    for view in ("editor", "perform", "library", "drums", "breed", "map", "bass"):
        assert 'id="reset-menu"' in client.get(f"/view/{view}").text, view
    editor = client.get("/").text
    assert "data-chords=" in editor and '"triad"' in editor  # chords
    assert 'data-name="zap"' in editor or ">zap<" in editor  # presets
    assert 'data-name="hit"' in editor  # the clip library, on every page
    assert 'data-name="hit"' in client.get("/view/map").text
    voice = client.get("/view/voice").text
    assert 'data-has-take="true"' in voice and "Take ready" in voice  # voice
    assert "zap" in client.get("/view/perform").text  # the sound menu (sounds)
    drums = client.get("/view/drums").text
    assert "groove" in drums and "/drum/kick/hit.wav?k=" in drums  # drums
    library = client.get("/view/library").text
    assert "Dark Driven Sine Bass" in library  # library


def test_page_context_reads_presets_and_reset_rows_once(tmp_path, monkeypatch):
    from synthlab.studio.state import StudioState

    client = make_client(tmp_path)
    counts = {"presets": 0, "labels": 0}
    real_meta, real_label = StudioState.list_presets_meta, StudioState.baseline_label

    def meta(self):
        counts["presets"] += 1
        return real_meta(self)

    def label(self, scope):
        counts["labels"] += 1
        return real_label(self, scope)

    monkeypatch.setattr(StudioState, "list_presets_meta", meta)
    monkeypatch.setattr(StudioState, "baseline_label", label)
    client.get("/view/perform")
    assert counts["presets"] == 1
    # one row per scope Perform owns — built once, not once per key
    from synthlab.studio.state import RESET_VIEWS

    assert counts["labels"] == len(RESET_VIEWS["perform"])
