"""The Sound agent (synthlab/studio/agent/), network-free.

A scripted `FakeClient` stands in for Gemini: it hands back `Reply`s with
tool calls and records what the loop sent it. `tests/conftest.py` makes
sure no test can see a real key or build the real client.

Pinned here:
- the tool schema is generated from the registry: every control the editor
  shows, one catalogue line each with `params.clamp`'s bounds; FX units
  with `FX_UNITS`' bounds; curves, macro targets, routes and phrases from
  their own tables;
- bad tool arguments are rejected or clamped, never raised;
- a turn is ONE undo step, "Before last load" holds what it replaced, and
  Undo turn restores the whole stack;
- the model is called with the studio lock released;
- the self-check reaches the model; tool rounds are capped;
- no key / no SDK -> a friendly setup message, never a 500;
- the key never appears in a response, a header, a log line or a repr —
  not even when the SDK's exception text carries it.
"""

from __future__ import annotations

import json
import logging
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from synthlab.params import MOD_DESTINATIONS, MOD_SOURCES, PARAMS
from synthlab.studio.agent import context as actx
from synthlab.studio.agent import tools as atools
from synthlab.studio.agent.client import AgentError, Call, GeminiClient, Reply, ToolTurn
from synthlab.studio.agent.config import AgentConfig, load_config, parse_dotenv, scrub
from synthlab.studio.audition import PHRASES
from synthlab.studio.curves import CURVABLE
from synthlab.studio.fxrack import FX_UNITS
from synthlab.studio.paramui import MACRO_PARAMS, UI, spec_of
from synthlab.studio.server import create_app
from synthlab.synth import Patch

EDITOR = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}
SECRET = "AIzaSyTESTSECRET0123456789abcdefghijklmn"


class FakeClient:
    """A scripted model: each `generate` pops the next Reply (or raises the
    next exception) and records what it was sent."""

    def __init__(self, script, on_call=None):
        self.script = list(script)
        self.seen: list[dict] = []
        self.on_call = on_call

    def generate(self, *, system, history, tools, allow_tools, timeout_s):
        self.seen.append(
            {
                "system": system,
                "history": list(history),
                "tools": tools,
                "allow_tools": allow_tools,
                "timeout_s": timeout_s,
            }
        )
        if self.on_call:
            self.on_call()
        item = self.script.pop(0) if self.script else Reply("done")
        if isinstance(item, BaseException):
            raise item
        return item


def calls(*pairs) -> Reply:
    return Reply(
        "",
        [Call(f"c{i}", name, args) for i, (name, args) in enumerate(pairs)],
        tokens=100,
    )


def agent_client(tmp_path, script=(), config=None, on_call=None):
    client = TestClient(create_app(data_dir=tmp_path))
    fake = FakeClient(script, on_call)
    svc = client.app.state.svc
    svc.agent.client_factory = lambda cfg: fake
    svc.agent._client = None
    if config is not None:
        svc.agent.config = config
    return client, svc, fake


def turn(client, message="make it darker"):
    return client.post(
        "/fragment/agent/turn", data={"message": message}, headers=EDITOR
    )


# --- the schema is the registry -------------------------------------------------


def _decl(name: str) -> dict:
    return next(d for d in atools.declarations() if d["name"] == name)


def test_set_params_covers_every_ui_param_with_registry_bounds():
    """The catalogue (one line per field, in `set_params`' description)
    names every control the editor shows exactly once, with what
    `params.clamp` accepts."""
    shown = {
        n
        for n, u in UI.items()
        if u.group is not None
        and (not PARAMS[n].nogene or n.startswith(atools._R6_AGENT))
    }
    assert "pitch_bend" not in shown  # a performance control, not the sound
    assert "mod_wheel" not in shown and "mod5_src" not in shown
    # filter 2 and the general modulators are the sound's (no genes)
    assert {"filter2_type", "env3_attack", "lfo2_shape"} <= shown
    groups = atools.param_groups()
    listed = [n for names, _spec in groups for n in names]
    assert sorted(listed) == sorted(shown)  # each one, once
    for names, text in groups:
        for name in names:
            p = PARAMS[name]
            assert text == atools.param_spec(name), name
            if p.kind == "cat":
                choices = ["off" if c is None else str(c) for c in p.cap]
                assert text.split("|") == choices, name
                continue
            # a number has its own line, but for a numbered run of one range
            run = {n.rstrip("0123456789") for n in names}
            assert len(names) == 1 or (len(run) == 1 and len(names) == 4), name
            assert text.startswith("int ") == (p.kind == "int"), name
            head = text.removeprefix("int ").split(",")[0].split(" ")
            assert head[0] == f"{p.cap[0]:g}..{p.cap[1]:g}", name
            assert head[1:] == ([UI[name].unit] if UI[name].unit else []), name
            spec = spec_of(name)
            if (spec.min, spec.max) != tuple(p.cap):  # the slider's range as a hint
                assert text.endswith(f", typical {spec.min:g}..{spec.max:g}"), name
            else:
                assert "typical" not in text, name
    # the routes' and LFOs' shared choices are said once, not per field
    shared = {names for names, _spec in groups if len(names) > 1}
    assert tuple(f"mod{i}_dst" for i in range(1, 5)) in shared
    assert ("lfo_sync", "filter_lfo_sync", "lfo2_sync", "lfo3_sync") in shared
    # what the model reads: every line, every field by its own name
    params = _decl("set_params")["parameters"]["properties"]["params"]
    assert params["type"] == "object" and "properties" not in params
    lines = params["description"].split("\n")[1:]
    assert lines == atools.param_catalogue().split("\n") and len(lines) == len(groups)
    assert "cutoff: 60..16000 Hz" in lines
    assert "fm_amount (Cross FM): 0..1" in lines  # a label that adds something
    assert "osc1_level: 0.1..1" in lines  # "Osc 1 level" would add nothing
    for name in shown:
        assert sum(atools.param_title(name) in line for line in lines) >= 1, name


def test_the_catalogue_is_under_half_the_schema_it_replaced():
    """`set_params` as plain JSON schema was 8,063 characters, the
    declarations 14,874 — sent again with every call."""
    sizes = {
        d["name"]: len(json.dumps(d, separators=(",", ":")))
        for d in atools.declarations()
    }
    # what the catalogue carries: hard sync, fm4 (its numbered runs on one
    # line each), drift, the wavetables, filter 2, envelopes 3 / 4, LFOs
    # 2 / 3, the filter types, LFO shapes, route sources and targets —
    # 4,406 and 12,789 characters
    assert sizes["set_params"] < 4_500
    assert sum(sizes.values()) < 12_900


def test_the_other_tools_are_generated_from_their_tables():
    units = _decl("set_fx_chain")["parameters"]["properties"]["units"]
    assert units["items"]["properties"]["kind"]["enum"] == list(atools.AGENT_FX)
    # each unit's params and bounds, one catalogue line per unit
    lines = dict(line.split(": ", 1) for line in atools.fx_catalogue().splitlines())
    assert list(lines) == list(atools.AGENT_FX)  # not the live vocal units
    for kind, spec in atools.AGENT_FX.items():
        for name, (lo, hi, _d) in spec["params"].items():
            assert f"{name} {lo:g}-{hi:g}" in lines[kind], (kind, name)
    assert atools.fx_catalogue() in units["description"]
    curve = _decl("draw_curve")["parameters"]["properties"]
    assert curve["param"]["enum"] == list(CURVABLE)
    macro = _decl("set_macro")["parameters"]["properties"]["targets"]["items"]
    assert macro["properties"]["param"]["enum"] == list(MACRO_PARAMS)
    route = _decl("add_mod_route")["parameters"]["properties"]
    assert route["source"]["enum"] == list(MOD_SOURCES)
    assert route["target"]["enum"] == list(MOD_DESTINATIONS)
    assert _decl("choose_phrase")["parameters"]["properties"]["phrase"]["enum"] == list(
        PHRASES
    )
    # every declaration is plain JSON and every one has an implementation
    blob = json.dumps(atools.declarations())
    assert len(blob) < 13_400  # the catalogue as it stands (above)
    assert atools.TOOL_NAMES == tuple(atools.TOOLS)


# --- untrusted arguments ---------------------------------------------------------


@pytest.fixture
def svc(tmp_path):
    return TestClient(create_app(data_dir=tmp_path)).app.state.svc


def test_set_params_rejects_and_clamps(svc):
    s = svc.state
    before = s.patch
    res = atools.apply_call(
        s,
        svc,
        "set_params",
        {
            "params": {
                "cutoff": 99999,  # over the cap: clamped
                "resonance": "0.4",  # a numeric string is fine
                "osc1": "banana",  # not a choice
                "osc2": "off",  # "off" is osc2's None
                "drive": float("nan"),  # not finite
                "unison": True,  # a bool is not a number
                "not_a_param": 1,
                "voice_fx": [],  # a Patch field without a control
            }
        },
    )
    assert res["ok"]
    assert s.patch.cutoff == int(PARAMS["cutoff"].cap[1])
    assert s.patch.resonance == 0.4
    assert s.patch.osc1 == before.osc1 and s.patch.osc2 is None
    assert s.patch.drive == before.drive and s.patch.unison == before.unison
    notes = " ".join(res["notes"])
    for word in ("cutoff", "banana", "drive", "unison", "not_a_param", "voice_fx"):
        assert word in notes


@pytest.mark.parametrize(
    "name, args",
    [
        ("no_such_tool", {}),
        ("set_params", "not a dict"),
        ("set_params", {"params": [1, 2]}),
        ("set_params", {"params": {}, "layer": 9}),
        ("set_layer", {"layer": 3, "level_db": -6}),  # empty slot
        ("add_mod_route", {"source": "lfo", "target": "nowhere", "amount": 0.5}),
        ("add_mod_route", {"source": "wobble", "target": "cutoff"}),
        ("draw_curve", {"param": "name", "shape": "rise"}),
        ("draw_curve", {"param": "cutoff", "shape": "points", "points": [[0.1]]}),
        ("set_fx_chain", {"scope": "drums", "units": []}),
        ("set_macro", {"index": 9, "targets": []}),
        ("choose_phrase", {"phrase": "symphony"}),
        ("add_layer", {"role": "kazoo"}),
        ("add_layer", {"from_sound": "Definitely Not A Sound"}),
    ],
)
def test_bad_calls_come_back_as_errors_not_exceptions(svc, name, args):
    s = svc.state
    snap = actx.snapshot(s)
    res = atools.apply_call(s, svc, name, args)
    assert res["ok"] is False and res["error"]
    assert actx.snapshot(s) == snap  # a refused call writes nothing


def test_fx_chain_and_macro_clamp_to_their_tables(svc):
    s = svc.state
    res = atools.apply_call(
        s,
        svc,
        "set_fx_chain",
        {
            "scope": "master",
            "units": [
                {"kind": "reverb", "params": {"mix": 7, "decay": 1.5, "bogus": 1}},
                {"kind": "laser"},
            ],
        },
    )
    assert res["ok"] and res["units"] == ["reverb"]
    assert s.fx_master[0]["params"]["mix"] == FX_UNITS["reverb"]["params"]["mix"][1]
    assert any("laser" in n for n in res["notes"])
    res = atools.apply_call(
        s,
        svc,
        "set_macro",
        {
            "index": 1,
            "name": "Open",
            "targets": [
                {"param": "cutoff", "amount": 3},
                {"param": "osc1", "amount": 1},  # categorical: not a macro target
            ],
        },
    )
    assert res["ok"] and s.macros[0]["targets"] == [{"param": "cutoff", "amt": 1.0}]


def test_add_layer_makes_a_role_layer_without_moving_the_panels(svc):
    s = svc.state
    active = s.active_layer
    res = atools.apply_call(s, svc, "add_layer", {"role": "sub", "level_db": -8})
    assert res["ok"]
    lay = s.layers[res["layer"] - 1]
    assert s.active_layer == active  # the edited slot stays
    assert lay["role"] == "sub" and lay["follow"]["octave"] == -1
    p = Patch.from_dict(lay["patch"])
    assert p.osc1 in ("sine", "triangle") and p.filter_type == "lowpass"
    assert 0.38 < lay["gain"] < 0.41  # -8 dB
    res = atools.apply_call(
        s, svc, "set_params", {"layer": res["layer"], "params": {"cutoff": 120}}
    )
    assert Patch.from_dict(lay["patch"]).cutoff == 120


# --- a turn -----------------------------------------------------------------------


def test_a_turn_is_one_undo_step_and_before_last_load(tmp_path):
    client, svc, fake = agent_client(
        tmp_path,
        [
            calls(("set_params", {"params": {"cutoff": 500, "resonance": 0.3}})),
            calls(("set_params", {"params": {"drive": 1.5}})),
            Reply("Closed the filter for a darker tone.", tokens=50),
        ],
    )
    s = svc.state
    original = s.patch
    depth = len(s._history)
    r = turn(client)
    assert r.status_code == 200 and 'id="agent-drawer"' in r.text
    assert "Closed the filter" in r.text and "Cutoff" in r.text
    assert (s.patch.cutoff, s.patch.resonance, s.patch.drive) == (500, 0.3, 1.5)
    assert len(s._history) == depth + 1  # two rounds, one step
    assert s.slot_prev == original
    t = svc.agent.turns[-1]
    assert t.rounds == 2 and t.tokens == 250 and len(t.changes) == 3
    assert t.before and t.after and t.checks  # "darker" is checkable
    assert svc.agent.tokens_used == 250 and svc.agent.turns_used == 1
    client.post("/fragment/undo", headers=EDITOR)  # Ctrl+Z: the whole turn
    assert s.patch == original


def test_undo_turn_restores_the_whole_stack(tmp_path):
    client, svc, _ = agent_client(
        tmp_path,
        [
            calls(
                ("add_layer", {"role": "pad"}),
                ("set_fx_chain", {"scope": "master", "units": [{"kind": "reverb"}]}),
                ("draw_curve", {"param": "cutoff", "shape": "rise"}),
                (
                    "set_macro",
                    {"index": 2, "targets": [{"param": "cutoff", "amount": 1}]},
                ),
                ("choose_phrase", {"phrase": "arp"}),
                ("set_params", {"params": {"unison": 5}}),
            ),
            Reply("Wider pad under it.", tokens=10),
        ],
    )
    s = svc.state
    snap = actx.snapshot(s)
    turn(client, "make it wider")
    assert actx.snapshot(s) != snap
    changes = " | ".join(svc.agent.turns[-1].changes)
    for word in ("+ Layer", "Master FX", "Curve", "Macro", "Phrase", "Unison"):
        assert word in changes
    r = client.post("/fragment/agent/undo", headers=EDITOR)
    assert r.status_code == 200
    assert actx.snapshot(s) == snap
    assert svc.agent.turns[-1].undone
    r = client.post("/fragment/agent/undo", headers=EDITOR)
    assert r.status_code == 422  # nothing left to undo


def test_before_last_load_swaps_the_whole_stack_back(tmp_path):
    client, svc, _ = agent_client(
        tmp_path,
        [
            calls(
                ("add_layer", {"role": "air"}),
                ("set_params", {"params": {"cutoff": 700}}),
            )
        ],
    )
    s = svc.state
    snap = actx.snapshot(s)
    turn(client, "add some air")
    client.post("/fragment/restore_prev", headers=EDITOR)
    assert s.patch.to_json() == snap["patch"]
    assert [bool(lay.get("patch")) for lay in s.layers] == [
        bool(lay.get("patch")) for lay in snap["layers"]
    ]


def test_store_ab_and_apply_to_a_new_layer(tmp_path):
    client, svc, _ = agent_client(
        tmp_path, [calls(("set_params", {"params": {"cutoff": 600}})), Reply("ok")]
    )
    s = svc.state
    original = s.patch
    turn(client)
    after = s.patch
    assert client.post("/fragment/agent/ab", headers=EDITOR).status_code == 200
    assert s.slot_a == original and s.slot_b == after
    r = client.post("/fragment/agent/apply", data={"target": "new"}, headers=EDITOR)
    assert r.status_code == 200
    assert s.patch == original  # the edited layer is back
    moved = [Patch.from_dict(lay["patch"]) for lay in s.layers if lay.get("patch")]
    assert any(p.cutoff == 600 for p in moved)
    r = client.post("/fragment/agent/apply", data={"target": "new"}, headers=EDITOR)
    assert r.status_code == 422  # that turn is spent


def test_the_model_is_called_with_the_lock_released(tmp_path):
    seen = []

    def probe():
        box = {}

        def other():  # another request's thread: can it take the lock now?
            box["got"] = svc.state.lock.acquire(timeout=1.0)
            if box["got"]:
                svc.state.lock.release()

        th = threading.Thread(target=other)
        th.start()
        th.join()
        seen.append(box["got"])

    client, svc, _ = agent_client(
        tmp_path,
        [calls(("set_params", {"params": {"cutoff": 800}})), Reply("ok")],
        on_call=probe,
    )
    turn(client)
    assert seen == [True, True]


def test_self_check_reaches_the_model_and_rounds_are_capped(tmp_path):
    script = [
        calls(("set_params", {"params": {"cutoff": c}})) for c in (1500, 900, 600)
    ]
    script.append(calls(("set_params", {"params": {"cutoff": 100}})))  # refused
    script.append(Reply("Darker now."))
    client, svc, fake = agent_client(tmp_path, script)
    turn(client, "darker please")
    assert [c["allow_tools"] for c in fake.seen] == [True, True, True, False]
    assert svc.state.patch.cutoff == 600  # the 4th reply's calls were not applied
    first = fake.seen[1]["history"][-1]
    assert isinstance(first, ToolTurn)
    assert "Automatic check: centroid_hz should go down" in first.text
    assert "No more tool calls" in fake.seen[3]["history"][-1].text
    prompt = fake.seen[0]["history"][0].text
    assert "Current sound:" in prompt and "Measured" in prompt
    assert fake.seen[0]["history"][0].audio[:4] == b"RIFF"  # the ~2 s clip
    assert "(Attached: ~2 s of the loop, 16 kHz" in prompt
    assert "stereo width" in prompt and "for width trust the measured" in prompt


def test_an_error_mid_turn_keeps_its_changes_and_toasts(tmp_path):
    client, svc, _ = agent_client(
        tmp_path,
        [
            calls(("set_params", {"params": {"cutoff": 700}})),
            AgentError("the model took too long to answer", kind="timeout"),
        ],
    )
    r = turn(client)
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert "too long" in trig["studio-toast"]["message"]
    assert trig["studio-toast"]["kind"] == "error"  # red, an alert: not an info toast
    assert svc.state.patch.cutoff == 700
    assert svc.agent.turns[-1].error and svc.agent.turns[-1].undoable
    assert not svc.agent.busy


def test_requests_that_cannot_start_are_toasts(tmp_path):
    client, svc, _ = agent_client(tmp_path)
    assert turn(client, "   ").status_code == 422
    r = client.post(
        "/fragment/agent/turn",
        data={"message": "darker"},
        headers={
            "HX-Request": "true",
            "HX-Current-URL": "http://testserver/view/drums",
        },
    )
    assert r.status_code == 422 and "Synths" in r.text
    svc.agent.tokens_used = svc.agent.config.token_budget
    r = turn(client)
    assert r.status_code == 422 and "budget" in r.text


# --- no key, no SDK -----------------------------------------------------------------


def test_no_key_is_a_friendly_setup_message(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    svc = client.app.state.svc
    assert not svc.agent.config.has_key  # conftest: no .env, no env var
    r = turn(client)
    assert r.status_code == 422 and "GEMINI_API_KEY" in r.text and ".env" in r.text
    page = client.get("/view/editor").text
    assert "Sound agent unavailable" in page and 'id="agent-drawer"' in page
    assert not svc.agent.turns and svc.agent.turns_used == 0


def test_no_sdk_names_the_extra(tmp_path, monkeypatch):
    from synthlab.studio.agent import service

    monkeypatch.setattr(service, "sdk_available", lambda: False)
    client = TestClient(create_app(data_dir=tmp_path))
    r = turn(client)
    assert r.status_code == 422 and "uv sync --all-extras" in r.text


def test_the_page_renders_the_drawer_with_its_budget(tmp_path):
    client, svc, _ = agent_client(tmp_path, [Reply("Nothing to do.", tokens=42)])
    turn(client, "make it wider")
    page = client.get("/view/editor").text
    assert "42 / 250,000 tokens" in page and "1/40 turns" in page
    assert "No changes." in page
    assert 'popovertarget="agent-drawer"' in page  # the sound bar opens it


# --- config and secrets ---------------------------------------------------


def test_dotenv_parser_and_env_precedence(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nexport GEMINI_API_KEY='from-file'\nGEMINI_MODEL=gemini-x # note\n"
        "SYNTHLAB_AGENT_AUDIO=off\ngarbage line\n",
        encoding="utf-8",
    )
    assert parse_dotenv(env_file.read_text())["GEMINI_MODEL"] == "gemini-x"
    cfg = load_config(env={}, env_file=env_file)
    assert cfg.api_key == "from-file" and cfg.source == ".env" and not cfg.audio
    cfg = load_config(env={"GEMINI_API_KEY": "from-env"}, env_file=env_file)
    assert cfg.api_key == "from-env" and cfg.source == "env"
    assert cfg.model == "gemini-x"
    assert load_config(env={}, env_file=tmp_path / "missing").api_key is None


def test_the_env_file_is_found_from_the_package_not_the_cwd():
    import synthlab
    from synthlab.studio.agent import config

    root = config.Path(synthlab.__file__).resolve().parents[1]
    # conftest points ENV_FILE elsewhere; the module's own default is here
    src = config.Path(config.__file__).read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parents[3] / ".env"' in src
    assert config.Path(config.__file__).resolve().parents[3] == root


def test_the_key_never_shows_in_a_repr_or_a_scrubbed_message():
    cfg = AgentConfig(api_key=SECRET)
    assert SECRET not in repr(cfg)
    msg = f"400 INVALID_ARGUMENT: API key not valid: {SECRET} (url ...?key={SECRET})"
    out = scrub(msg, cfg.secrets())
    assert SECRET not in out and "[redacted]" in out
    assert SECRET not in scrub(f"x-goog-api-key: {SECRET}")  # no secrets passed


def test_sdk_errors_are_mapped_and_scrubbed(caplog):
    client = GeminiClient.__new__(GeminiClient)  # no SDK client, no network
    client._config = AgentConfig(api_key=SECRET)

    class ClientError(Exception):
        code = 400
        message = f"API key not valid. Please pass a valid API key. key={SECRET}"

    with caplog.at_level(logging.DEBUG):
        err = client._map_error(ClientError())
    assert err.kind == "auth" and "GEMINI_API_KEY" in err.message
    assert SECRET not in err.message and SECRET not in caplog.text
    assert SECRET not in repr(client)


def test_the_key_never_reaches_a_response_or_a_log(tmp_path, caplog, monkeypatch):
    """Even when the model call fails with the key in the exception text,
    and even for an unexpected exception type."""
    monkeypatch.setenv("SYNTHLAB_DEBUG", "1")
    boom = RuntimeError(f"connection reset while sending key={SECRET}")
    client, svc, _ = agent_client(
        tmp_path,
        [calls(("set_params", {"params": {"cutoff": 900}})), boom],
        config=AgentConfig(api_key=SECRET),
    )
    with caplog.at_level(logging.DEBUG):
        responses = [
            turn(client),
            client.get("/view/editor"),
            client.get("/debug/stats"),
            client.get("/patch.json"),
            client.get("/project.json"),
        ]
    for r in responses:
        assert SECRET not in r.text, r.url
        assert SECRET not in json.dumps(dict(r.headers)), r.url
    assert SECRET not in caplog.text
    assert "the agent failed unexpectedly" in responses[0].headers["HX-Trigger"]
    state_file = (tmp_path / "state.json").read_text(encoding="utf-8")
    assert SECRET not in state_file
    # the persisted chat log and budget never hold it either
    log_file = (tmp_path / "agent_log.json").read_text(encoding="utf-8")
    assert svc.agent.turns and SECRET not in log_file


# --- context --------------------------------------------------------------------------


def test_describe_is_compact_and_names_the_stack(svc):
    s = svc.state
    text = actx.describe(s)
    assert "layer 1" in text and "MAIN" in text and "editing" in text
    assert "master fx: none" in text
    assert len(text) < 1500
    s.patch = s.patch.tweak(cutoff=1234, osc1="square")
    text = actx.describe(s)
    assert "cutoff=1234" in text and "osc1=square" in text


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("make it darker and wider", {("centroid_hz", -1), ("width", 1)}),
        ("brighter, more movement", {("centroid_hz", 1), ("movement", 1)}),
        ("punchier", {("crest_db", 1)}),
        ("darker but brighter", set()),  # contradictory: no check
        ("turn this into a festival supersaw lead", {("width", 1)}),
        ("hello", set()),
    ],
)
def test_expectations(request_text, expected):
    assert {(f, s) for f, s, _t in actx.expectations(request_text)} == expected


def test_check_judges_direction_and_size():
    before = {"centroid_hz": 2000, "width": 0.1}
    res = actx.check(
        actx.expectations("darker and wider"),
        before,
        {"centroid_hz": 1700, "width": 0.105},
    )
    assert {r["feature"]: r["met"] for r in res} == {
        "centroid_hz": True,
        "width": False,
    }


# --- loudness before the loop's normalisation -------------------


def test_loudness_is_measured_before_the_loops_normalisation():
    """The ▶ render is brought to one level, so its own loudness barely
    followed the sound's; `render_loop` keeps the gain it applied and the
    agent's loudness subtracts it."""
    from synthlab.studio.render import RenderInputs, RenderResult, render_loop
    from synthlab.studio.state import Arrangement

    arr = Arrangement(cells=[["A2", 0], ["C3", 4], ["E3", 8]])
    heard, measured = [], []
    for level in (0.8, 0.4):
        patch = Patch(osc1_level=level, osc2_level=level / 1.6)
        r = render_loop(RenderInputs(patch=patch, arr=arr, parts=("synth",)))
        assert r.norm_gain_db is not None
        heard.append(actx.measure_audio(r.audio())["loudness_db"])
        m = actx.measure(r)
        assert m["loudness_pre_norm"]
        measured.append(m["loudness_db"])
    assert abs(heard[0] - heard[1]) < 1.5  # what the metric used to report
    assert 5.0 < measured[0] - measured[1] < 7.0  # half the level: -6 dB
    assert "(before normalisation)" in actx.features_text(m)
    # a render that doesn't say its gain is read as heard, and says so
    plain = actx.measure(RenderResult(r.key, r.wav, {}))
    assert not plain["loudness_pre_norm"]
    assert plain["loudness_db"] == heard[1]
    assert "before normalisation" not in actx.features_text(plain)


def test_render_audio_is_unchanged_by_the_gain_bookkeeping():
    from synthlab.studio.render import (
        RenderInputs,
        normalize_master,
        render_audio,
        render_loop,
    )
    from synthlab.studio.state import Arrangement

    inp = RenderInputs(patch=Patch(), arr=Arrangement(cells=[["A2", 0]]))
    a = render_audio(inp)
    assert np.allclose(render_loop(inp).audio(), a, atol=1 / 32767)
    x = np.random.default_rng(0).normal(0, 0.1, (4410, 2))
    assert np.array_equal(normalize_master(x), normalize_master(x.copy()))


# --- the log and budget persist per data dir ---------------------


def test_the_log_and_budget_survive_a_restart(tmp_path):
    client, svc, _ = agent_client(
        tmp_path,
        [
            calls(("set_params", {"params": {"cutoff": 700}})),
            Reply("Darker now.", tokens=40),
        ],
    )
    turn(client)
    assert (tmp_path / "agent_log.json").exists()
    again = TestClient(create_app(data_dir=tmp_path))
    agent = again.app.state.svc.agent
    assert agent.tokens_used == 140 and agent.turns_used == 1
    assert [t.request for t in agent.turns] == ["make it darker"]
    t = agent.turns[0]
    assert t.reply == "Darker now." and t.changes and t.before and t.after
    assert not t.undoable  # the stack snapshots were runtime only
    page = again.get("/view/editor").text
    assert "Darker now." in page and "140 / 250,000 tokens" in page
    assert "↶ Undo turn" not in page
    # a new turn continues the ids, never reusing one
    agent.client_factory = lambda cfg: FakeClient([Reply("ok", tokens=1)])
    turn(again, "brighter")
    assert [t.id for t in agent.turns] == [1, 2]


def test_the_log_is_capped_and_a_hostile_file_is_an_empty_log(tmp_path):
    from synthlab.studio import limits
    from synthlab.studio.agent.service import AgentService

    path = tmp_path / "agent_log.json"
    turns = [{"id": i, "request": f"r{i}", "tokens": 5} for i in range(1, 81)]
    path.write_text(
        json.dumps({"v": 1, "tokens_used": 400, "turns": turns}), encoding="utf-8"
    )
    agent = AgentService(log_path=path)
    assert len(agent.turns) == limits.MAX_AGENT_LOG_TURNS
    assert agent.turns[-1].request == "r80" and agent.tokens_used == 400
    for junk in ("not json", "[]", '{"turns": "x", "tokens_used": -5}', "{}"):
        path.write_text(junk, encoding="utf-8")
        agent = AgentService(log_path=path)
        assert agent.turns == [] and agent.tokens_used == 0
    path.write_text(
        json.dumps({"turns": [{"id": 3, "request": 7, "before": {"width": "x"}}]}),
        encoding="utf-8",
    )
    agent = AgentService(log_path=path)  # bad fields fall back, never raise
    assert agent.turns[0].request == "" and agent.turns[0].before["width"] == 0.0


def test_the_log_never_holds_the_key_even_if_the_model_says_it(tmp_path):
    client, svc, _ = agent_client(
        tmp_path,
        [Reply(f"Your key is {SECRET}", tokens=10)],
        config=AgentConfig(api_key=SECRET),
    )
    turn(client)
    assert SECRET not in (tmp_path / "agent_log.json").read_text(encoding="utf-8")


def test_new_budget_zeroes_the_budget_and_keeps_the_log(tmp_path):
    client, svc, _ = agent_client(tmp_path, [Reply("Nothing to do.", tokens=42)])
    turn(client, "make it wider")
    svc.agent.tokens_used = svc.agent.config.token_budget
    assert turn(client).status_code == 422  # spent: a toast that says what to do
    r = client.post("/fragment/agent/budget", headers=EDITOR)
    assert r.status_code == 200 and 'id="agent-drawer"' in r.text
    assert svc.agent.tokens_used == 0 and svc.agent.turns_used == 0
    assert len(svc.agent.turns) == 1
    again = TestClient(create_app(data_dir=tmp_path)).app.state.svc.agent
    assert again.tokens_used == 0 and len(again.turns) == 1


# --- the money estimate -------------------------------------------


def test_price_table_lookup_is_exact_or_a_numbered_build():
    from datetime import date

    from synthlab.studio.agent.config import PRICES, price_for

    assert price_for("gemini-2.5-flash") == PRICES["gemini-2.5-flash"]
    assert price_for("models/gemini-2.5-flash-001") == PRICES["gemini-2.5-flash"]
    assert price_for("gemini-2.5-flash-lite") == PRICES["gemini-2.5-flash-lite"]
    for unknown in ("gemini-flash-latest", "gemini-2.5-flash-image", "", None, "gpt"):
        assert price_for(unknown) is None, unknown
    # a listed price change applies from its date
    early = price_for("gemini-3.8-flash", date(2026, 12, 31))
    late = price_for("gemini-3.8-flash", date(2027, 1, 1))
    assert (early.input, early.output) == (0.75, 3.75)
    assert (late.input, late.output) == (1.50, 7.50)


def test_reply_cost_splits_audio_and_output():
    from synthlab.studio.agent.client import reply_cost, usage_split
    from synthlab.studio.agent.config import Price

    class Detail:
        def __init__(self, modality, n):
            self.modality, self.token_count = modality, n

    class Usage:
        prompt_token_count = 10_000
        tool_use_prompt_token_count = None
        candidates_token_count = 300
        thoughts_token_count = 200
        prompt_tokens_details = [Detail("TEXT", 9_936), Detail("AUDIO", 64)]

    split = usage_split(Usage())
    assert split == {
        "tokens_in": 10_000,
        "tokens_audio_in": 64,
        "tokens_out": 500,
        "tokens_cached_in": 0,  # the API reported none
    }
    reply = Reply("", tokens=10_500, **split)
    cost = reply_cost(reply, Price(0.30, 2.50, audio_in=1.00))
    assert cost == pytest.approx((9_936 * 0.30 + 64 * 1.00 + 500 * 2.50) / 1e6)
    assert reply_cost(reply, None) is None
    assert reply_cost(Reply("", tokens=99), Price(1, 1)) is None  # no split


def test_cached_input_is_counted_and_priced_on_the_safe_side():
    """`cached_content_token_count` is part of the prompt count, never
    added to it. No row of `PRICES` has a cached rate, so cached input is
    charged at the full input rate (an over-estimate) until one is given."""
    from synthlab.studio.agent.client import reply_cost, usage_split
    from synthlab.studio.agent.config import PRICES, Price

    class Detail:
        modality, token_count = "AUDIO", 64

    class Usage:
        prompt_token_count = 4_000
        cached_content_token_count = 2_900
        candidates_token_count = 100
        thoughts_token_count = 0
        total_token_count = 4_100
        prompt_tokens_details = [Detail()]

    split = usage_split(Usage())
    assert split["tokens_in"] == 4_000 and split["tokens_cached_in"] == 2_900
    reply = Reply("", tokens=4_100, **split)
    assert all(p.cached_in is None for p in PRICES.values())
    plain = Price(0.30, 2.50, audio_in=1.00)
    full = (3_936 * 0.30 + 64 * 1.00 + 100 * 2.50) / 1e6
    assert reply_cost(reply, plain) == pytest.approx(full)  # as if uncached
    rated = Price(0.30, 2.50, audio_in=1.00, cached_in=0.03)
    assert reply_cost(reply, rated) == pytest.approx(
        (1_036 * 0.30 + 2_900 * 0.03 + 64 * 1.00 + 100 * 2.50) / 1e6
    )
    # a nonsense count never prices more cached text than there was
    Usage.cached_content_token_count = 9_999_999
    split = usage_split(Usage())
    assert split["tokens_cached_in"] == 4_000
    assert reply_cost(Reply("", tokens=4_100, **split), rated) == pytest.approx(
        (3_936 * 0.03 + 64 * 1.00 + 100 * 2.50) / 1e6
    )
    # the env override may name a cached rate; without it, the input rate
    cfg = load_config(
        env={
            "SYNTHLAB_AGENT_PRICE_IN": "1",
            "SYNTHLAB_AGENT_PRICE_OUT": "10",
            "SYNTHLAB_AGENT_PRICE_CACHED": "0.1",
        },
        env_file=config_missing(),
    )
    assert cfg.price("anything").cached_in == 0.1


def config_missing():
    from pathlib import Path

    return Path(__file__).with_name("no-such.env")


# --- the clip and the stereo numbers ------------------------------


class _Render:
    def __init__(self, audio):
        self._audio = audio

    def audio(self):
        return self._audio


def _tone(freq, seconds=3.0):
    from synthlab.core import SR

    t = np.arange(int(seconds * SR)) / SR
    return 0.5 * np.sin(2 * np.pi * freq * t)


def _wav(clip):
    import io
    import wave

    with wave.open(io.BytesIO(clip), "rb") as w:
        return w.getnchannels(), w.getframerate(), w.getnframes()


def test_the_clip_is_stereo_only_when_the_sound_is():
    wide = np.stack([_tone(220), _tone(331)], axis=1)
    mono = np.stack([_tone(220), _tone(220)], axis=1)
    nearly = mono + np.stack([_tone(331), -_tone(331)], axis=1) * 0.004  # -48 dB
    clip = actx.audio_clip(_Render(wide))
    assert _wav(clip) == (2, 16000, 32000) and len(clip) == 128_044
    assert "16 kHz stereo" in actx.clip_note(clip)
    for same in (mono, nearly, _tone(220)):
        clip = actx.audio_clip(_Render(same))
        assert _wav(clip) == (1, 16000, 32000) and len(clip) == 64_044
        assert "16 kHz mono" in actx.clip_note(clip)
    # switched off (SYNTHLAB_AGENT_AUDIO_STEREO=0): the old mono clip
    assert _wav(actx.audio_clip(_Render(wide), stereo=False))[0] == 1
    # the channels are the render's own, left first
    import io
    import wave

    left_only = np.stack([_tone(220), np.zeros_like(_tone(220))], axis=1)
    with wave.open(io.BytesIO(actx.audio_clip(_Render(left_only))), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2)
    assert np.abs(pcm[:, 0]).max() > 10_000 and np.abs(pcm[:, 1]).max() < 400


def test_the_service_follows_the_stereo_switch(tmp_path):
    client, svc, fake = agent_client(tmp_path, [Reply("ok"), Reply("ok")])
    svc.state.patch = svc.state.patch.tweak(unison=5, unison_spread=1.0)
    turn(client, "wider")
    assert _wav(fake.seen[0]["history"][0].audio)[0] == 2
    assert "kHz stereo" in fake.seen[0]["history"][0].text
    from dataclasses import replace

    svc.agent.config = replace(svc.agent.config, audio_stereo=False)
    turn(client, "wider")
    assert _wav(fake.seen[1]["history"][0].audio)[0] == 1
    cfg = load_config(
        env={"SYNTHLAB_AGENT_AUDIO_STEREO": "0"}, env_file=config_missing()
    )
    assert cfg.audio and not cfg.audio_stereo and AgentConfig().audio_stereo


def test_the_stereo_numbers_are_exact_and_reach_the_model():
    a, b = _tone(220), _tone(331)
    side, balance = actx.stereo_facts(np.stack([a, a], axis=1))
    assert side <= actx.MONO_SIDE_DB and balance == 0.0
    m = actx.measure_audio(np.stack([a, a], axis=1))
    assert "stereo width 0.000 (mono: L = R)" in actx.features_text(m)
    # two unrelated tones: as much side as mid, uncorrelated, centred
    m = actx.measure_audio(np.stack([a, b], axis=1))
    assert abs(m["side_db"]) < 0.2 and abs(m["balance_db"]) < 0.2
    text = actx.features_text(m)
    assert "L/R correlation 0.00, side" in text and "balance centred" in text
    # the right channel 6 dB down
    m = actx.measure_audio(np.stack([a, b * 0.5], axis=1))
    assert m["balance_db"] == pytest.approx(-6.0, abs=0.1)
    assert "balance L +6.0 dB" in actx.features_text(m)
    # out of phase: all side, and said so
    m = actx.measure_audio(np.stack([a, -a], axis=1))
    assert m["side_db"] == 40.0 and "out of phase!" in actx.features_text(m)
    assert actx.stereo_facts(a) == (-80.0, 0.0)  # a mono array
    assert {"side_db", "balance_db"} < set(actx.FEATURE_KEYS)  # logged with a turn


def _priced(text="Done.", model="gemini-2.5-flash"):
    return Reply(text, tokens=11_000, tokens_in=10_000, tokens_out=1_000, model=model)


def test_the_drawer_shows_a_money_estimate_for_a_priced_model(tmp_path):
    client, svc, _ = agent_client(tmp_path, [_priced()])
    turn(client, "make it wider")
    t = svc.agent.turns[-1]
    assert t.model == "gemini-2.5-flash"
    assert t.cost_usd == pytest.approx((10_000 * 0.30 + 1_000 * 2.50) / 1e6)
    page = client.get("/view/editor").text
    assert page.count("≈ $0.0055") == 2  # the turn, and the budget total
    again = TestClient(create_app(data_dir=tmp_path)).app.state.svc.agent
    assert again.cost_usd == pytest.approx(t.cost_usd)


def test_an_unknown_model_gets_no_estimate(tmp_path):
    client, svc, _ = agent_client(tmp_path, [_priced(model="gemini-9-ultra")])
    turn(client, "make it wider")
    assert svc.agent.turns[-1].cost_usd is None
    assert svc.agent.unpriced_turns == 1 and svc.agent.cost_usd == 0
    assert "≈ $" not in client.get("/view/editor").text


def test_env_prices_override_the_table(tmp_path):
    cfg = load_config(
        env={"SYNTHLAB_AGENT_PRICE_IN": "1", "SYNTHLAB_AGENT_PRICE_OUT": "10"},
        env_file=tmp_path / "missing",
    )
    assert cfg.price("gemini-9-ultra") == cfg.price("gemini-2.5-flash")
    client, svc, _ = agent_client(
        tmp_path, [_priced(model="gemini-9-ultra")], config=cfg
    )
    turn(client, "make it wider")
    assert svc.agent.turns[-1].cost_usd == pytest.approx(0.02)
    one = load_config(env={"SYNTHLAB_AGENT_PRICE_IN": "1"}, env_file=tmp_path / "x")
    assert one.price_override is None  # both or neither
