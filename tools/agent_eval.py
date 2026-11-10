"""Eval harness for the Sound agent: ~30 requests, each scored by something
measurable. Talks to the REAL Gemini API, so it is never collected by
pytest (tests/studio/test_agent.py is the network-free suite) and only runs
when invoked by hand with a key:

    uv run python tools/agent_eval.py              # every case
    uv run python tools/agent_eval.py --only 1,3,7 # a few
    uv run python tools/agent_eval.py --list       # print the cases, no calls
    uv run python tools/agent_eval.py --out eval.json
    uv run python tools/agent_eval.py --cache      # with the prefix cache on
    uv run python tools/agent_eval.py --dry-run    # no key, no network

`--dry-run` needs no key and reaches nothing: the real route, the real
`GeminiClient` and the real google-genai SDK build every request, and a
scripted httpx transport answers them (one `set_params` call, then a
summary). It runs each picked case (the first two by default) without and
with the prefix cache and prints what each call put on the wire, in bytes
and estimated tokens: the request-building path, checked offline. It
scores nothing.

The key comes from where the studio reads it: GEMINI_API_KEY in the
environment or in this checkout's .env (GEMINI_MODEL too). It is never
printed. Each case runs in a fresh studio (a temp data dir) on a factory
sound, sends one request through the real route (`/fragment/agent/turn`),
and checks the turn's measured before/after features (`context.check`: the
same numbers and thresholds the agent's self-check uses) and/or the state
it left (a layer added, a route, a curve, an FX unit...). Cost is printed:
roughly 5-15k tokens a case on gemini-flash.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from synthlab.params import slot_active  # noqa: E402
from synthlab.studio.agent import context as actx  # noqa: E402
from synthlab.studio.server import create_app  # noqa: E402
from synthlab.studio.sounds import find  # noqa: E402
from synthlab.synth import Patch  # noqa: E402

EDITOR = {"HX-Request": "true", "HX-Current-URL": "http://testserver/view/editor"}

# a state check: (before snapshot, after snapshot) -> passed
Check = Callable[[dict, dict], bool]


def P(snap: dict) -> Patch:
    return Patch.from_dict(snap["patch"])


def new_layers(before: dict, after: dict) -> list[dict]:
    return [
        b
        for a, b in zip(before["layers"], after["layers"], strict=False)
        if not a.get("patch") and b.get("patch")
    ]


def all_fx(snap: dict) -> set[str]:
    kinds = {u["kind"] for u in snap["fx_master"]}
    for lay in snap["layers"]:
        kinds |= {u["kind"] for u in lay.get("fx") or []}
    return kinds


def routed(snap: dict, src: str, dst: str) -> bool:
    p = P(snap)
    return any(
        slot_active(p, s)
        and getattr(p, f"{s}_src") == src
        and getattr(p, f"{s}_dst") == dst
        for s in ("mod1", "mod2", "mod3", "mod4")
    )


@dataclass
class Case:
    sound: str  # factory sound it starts from
    request: str
    features: list[tuple[str, int, float]] = field(default_factory=list)
    checks: dict[str, Check] = field(default_factory=dict)


CASES: list[Case] = [
    Case("Trance Anthem", "make it darker", [("centroid_hz", -1, 0.05)]),
    Case("Starlight Keys", "make it brighter", [("centroid_hz", +1, 0.05)]),
    Case(
        "Trance Anthem",
        "make it darker and wider",
        [("centroid_hz", -1, 0.05), ("width", +1, 0.02)],
    ),
    Case("DX Tine", "give it more movement", [("movement", +1, 0.01)]),
    Case("Acid Alley", "make it wider", [("width", +1, 0.02)]),
    Case("Nylon Pluck", "make it punchier", [("crest_db", +1, 0.3)]),
    Case(
        "Starlight Keys",
        "turn this into a festival supersaw lead",
        [("width", +1, 0.02)],
        {
            "supersaw or unison >= 4": lambda b, a: (
                P(a).osc1 == "supersaw" or P(a).unison >= 4
            )
        },
    ),
    Case(
        "French Stab",
        "give it a vocal-chop feel",
        checks={"vowel filter on": lambda b, a: P(a).formant_amount >= 0.2},
    ),
    Case("Trance Anthem", "more sub weight", [("sub_pct", +1, 1.0)]),
    Case("Boom Bap Sub", "less bass, thinner", [("sub_pct", -1, 1.0)]),
    Case(
        "Trance Anthem",
        "add a sub layer an octave down",
        checks={
            "a sub layer": lambda b, a: any(
                lay.get("role") == "sub"
                or (lay.get("follow") or {}).get("octave", 0) < 0
                for lay in new_layers(b, a)
            )
        },
    ),
    Case(
        "Nylon Pluck",
        "add a soft pad layer underneath",
        checks={"a new layer": lambda b, a: bool(new_layers(b, a))},
    ),
    Case("Starlight Keys", "make it grittier and dirtier", [("flatness", +1, 0.005)]),
    Case("FM Growler", "make it cleaner and purer", [("flatness", -1, 0.005)]),
    Case(
        "Nebula Sweep", "make it static, no wobble or sweep", [("movement", -1, 0.01)]
    ),
    Case(
        "DX Tine",
        "put a reverb and a synced delay on the master",
        checks={
            "reverb + delay on master": lambda b, a: (
                {"reverb", "delay"} <= {u["kind"] for u in a["fx_master"]}
            )
        },
    ),
    Case(
        "Acid Alley",
        "make the filter open up over each note",
        checks={
            "cutoff curve or fenv route": lambda b, a: (
                "cutoff" in a["curves"]
                or routed(a, "fenv", "cutoff")
                or P(a).filter_env > P(b).filter_env
            )
        },
    ),
    Case(
        "Trance Anthem",
        "set up macro 1 to open the filter and add drive",
        checks={
            "macro 1 -> cutoff": lambda b, a: any(
                t["param"] == "cutoff" for t in a["macros"][0]["targets"]
            )
        },
    ),
    Case(
        "Boom Bap Sub",
        "add an LFO wobble on the cutoff synced to eighth notes",
        [("movement", +1, 0.01)],
        {
            "lfo -> cutoff at 1/8": lambda b, a: (
                (routed(a, "lfo", "cutoff") and P(a).lfo_sync == "1/8")
                or P(a).filter_lfo_sync == "1/8"
            )
        },
    ),
    Case(
        "Nylon Pluck",
        "slower attack, more like a pad",
        checks={"attack >= 0.1 s": lambda b, a: P(a).attack >= 0.1},
    ),
    Case(
        "Hoover Rave",
        "shorter and pluckier",
        checks={
            "decay/sustain shorter": lambda b, a: (
                P(a).sustain < P(b).sustain
                or P(a).decay < P(b).decay
                or P(a).release < P(b).release
            )
        },
    ),
    Case(
        "Nylon Pluck",
        "make it a melodic techno lead",
        checks={
            "saw family + a delay": lambda b, a: (
                P(a).osc1 in ("saw", "supersaw", "wavetable") and "delay" in all_fx(a)
            )
        },
    ),
    Case(
        "Trance Anthem",
        "UK garage organ stab",
        checks={
            "short sustain or stab phrase": lambda b, a: (
                P(a).sustain <= 0.4 or a["audition"] == "stab"
            )
        },
    ),
    Case(
        "Sub Bomb",
        "brighter with more movement",
        [("centroid_hz", +1, 0.05), ("movement", +1, 0.01)],
    ),
    Case(
        "Hoover Rave",
        "turn it into a warm, slow pad",
        [("centroid_hz", -1, 0.05)],
        {"attack >= 0.1 s": lambda b, a: P(a).attack >= 0.1},
    ),
    Case("Trance Anthem", "make it mono and narrow", [("width", -1, 0.02)]),
    Case(
        "French Stab",
        "add some air on top",
        checks={
            "a layer above": lambda b, a: (
                any(
                    lay.get("role") == "air"
                    or (lay.get("follow") or {}).get("octave", 0) > 0
                    for lay in new_layers(b, a)
                )
                or P(a).noise_level > P(b).noise_level
            )
        },
    ),
    Case(
        "Acid Alley",
        "tame the resonance, it's too whistly",
        checks={"resonance lower": lambda b, a: P(a).resonance < P(b).resonance},
    ),
    Case(
        "Starlight Keys",
        "make it a squelchy 303 acid bass playing the riff",
        checks={
            "riff + resonance >= 0.5": lambda b, a: (
                a["audition"] == "riff" and P(a).resonance >= 0.5
            )
        },
    ),
    Case("Hoover Rave", "softer and less aggressive", [("centroid_hz", -1, 0.05)]),
]


class DryTransport:
    """Gemini, scripted and offline (`--dry-run`): an httpx transport that
    answers `cachedContents` with a cache and `generateContent` with one
    tool call, then a summary — and keeps every request body it was sent."""

    def __init__(self):
        self.calls: list[dict] = []

    def transport(self):
        import httpx

        def handle(request):
            body = json.loads(request.content or b"{}")
            path = request.url.path
            self.calls.append({"path": path, "body": body})
            if path.endswith("cachedContents"):
                size = len(json.dumps(body)) // 4
                return httpx.Response(
                    200,
                    json={
                        "name": "cachedContents/dry-run",
                        "expireTime": "2099-01-01T00:00:00Z",
                        "usageMetadata": {"totalTokenCount": size},
                    },
                )
            turns = [c for c in body["contents"] if c.get("role") == "model"]
            part = (
                {"text": "Dry run: closed the filter."}
                if turns
                else {
                    "functionCall": {
                        "name": "set_params",
                        "args": {"params": {"cutoff": 900}},
                    }
                }
            )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [part]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {},
                    "modelVersion": "dry-run",
                },
            )

        return httpx.MockTransport(handle)


def wire_sizes(call: dict) -> dict:
    """One request body by part, in characters of JSON; the clip's base64
    apart from the text around it."""
    body = call["body"]
    audio = sum(
        len(p["inlineData"]["data"])
        for c in body.get("contents") or []
        for p in c.get("parts") or []
        if "inlineData" in p
    )
    size = lambda k: len(json.dumps(body[k])) if k in body else 0  # noqa: E731
    return {
        "what": "create cache"
        if call["path"].endswith("cachedContents")
        else ("cached call" if "cachedContent" in body else "call"),
        "system": size("systemInstruction"),
        "tools": size("tools") + size("toolConfig"),
        "history": size("contents") - audio,
        "audio": audio,
    }


def dry_run(picked) -> int:
    from synthlab.studio.agent.client import CACHES, GeminiClient
    from synthlab.studio.agent.config import AgentConfig

    ok = True
    print("dry run: no key, no network; ~tok = characters / 4 (an estimate),")
    print("audio = its base64 (billed by length: ~32 tokens a second)")
    for i, case in picked:
        for cache in (False, True):
            CACHES.clear()
            dry = DryTransport()
            cfg = AgentConfig(api_key="dry-run-no-key", cache=cache)
            res = run_case(
                i, case, cfg, lambda c, d=dry: GeminiClient(c, transport=d.transport())
            )
            ok = ok and not res.get("error") and bool(res.get("changes"))
            print(
                f"\n{i:2d}. [{case.sound}] {case.request!r}, cache "
                f"{'on' if cache else 'off'}: {res.get('rounds')} round(s)"
                + (f" ERROR {res['error']}" if res.get("error") else "")
            )
            head = ("system", "tools", "history", "~tok", "audio")
            print(" " * 17 + "".join(f"{h:>8s}" for h in head))
            for call in dry.calls:
                w = wire_sizes(call)
                text = w["system"] + w["tools"] + w["history"]
                print(
                    f"    {w['what']:13s}{w['system']:8d}{w['tools']:8d}"
                    f"{w['history']:8d}{text // 4:8d}{w['audio']:8d}"
                )
    return 0 if ok else 1


def run_case(i: int, case: Case, config=None, client_factory=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="agent-eval-") as tmp:
        # a loopback Host: the boundary refuses TestClient's own "testserver"
        app = create_app(data_dir=Path(tmp))
        with TestClient(app, base_url="http://127.0.0.1") as client:
            svc = client.app.state.svc
            if config is not None:  # --dry-run: a scripted transport, no key
                svc.agent.config = config
                svc.agent.client_factory = client_factory
            sound = find(svc, "factory", case.sound)
            if sound is None:
                return {"case": i, "error": f"no factory sound {case.sound!r}"}
            svc.state.load_sound(sound.patch, sound.chain, label=sound.label)
            before = actx.snapshot(svc.state)
            t0 = time.perf_counter()
            r = client.post(
                "/fragment/agent/turn", data={"message": case.request}, headers=EDITOR
            )
            secs = time.perf_counter() - t0
            after = actx.snapshot(svc.state)
            turns = svc.agent.turns
            turn = turns[-1] if turns else None
    out: dict = {
        "case": i,
        "sound": case.sound,
        "request": case.request,
        "status": r.status_code,
        "seconds": round(secs, 1),
    }
    if turn is None:
        out["error"] = "no turn (a toast: key or budget?)"
        return out
    feats = actx.check(case.features, turn.before, turn.after) if turn.after else []
    states = {name: bool(fn(before, after)) for name, fn in case.checks.items()}
    passed = (
        not turn.error
        and (bool(turn.after) or not case.features)
        and all(f["met"] for f in feats)
        and all(states.values())
    )
    out.update(
        passed=passed,
        features=feats,
        checks=states,
        before=turn.before,
        after=turn.after,
        tokens=turn.tokens,
        tokens_cached=turn.tokens_cached,
        cost_usd=turn.cost_usd,
        rounds=turn.rounds,
        changes=turn.changes,
        reply=turn.reply,
        error=turn.error,
    )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated case numbers (1-based)")
    ap.add_argument("--list", action="store_true", help="print the cases and exit")
    ap.add_argument("--out", type=Path, help="write the full results as JSON")
    ap.add_argument(
        "--cache",
        action="store_true",
        help="run with the prefix cache on (SYNTHLAB_AGENT_CACHE=1)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="no key, no network: build every request, print its size",
    )
    args = ap.parse_args(argv)
    picked = list(enumerate(CASES, start=1))
    if args.only:
        want = {int(x) for x in args.only.split(",") if x.strip()}
        picked = [(i, c) for i, c in picked if i in want]
    elif args.dry_run:
        picked = picked[:2]
    if args.list:
        for i, c in picked:
            what = [f"{f} {'up' if s > 0 else 'down'}" for f, s, _t in c.features]
            print(
                f"{i:2d}. [{c.sound}] {c.request!r}: {', '.join(what + list(c.checks))}"
            )
        return 0
    from synthlab.studio.agent import load_config
    from synthlab.studio.agent.client import CACHES, sdk_available
    from synthlab.studio.agent.service import money

    if args.dry_run:
        if not sdk_available():
            print("needs `uv sync --all-extras` (google-genai)")
            return 2
        return dry_run(picked)
    if args.cache:
        os.environ["SYNTHLAB_AGENT_CACHE"] = "1"
    cfg = load_config()
    if not (cfg.has_key and sdk_available()):
        print("needs GEMINI_API_KEY (env or .env) and `uv sync --all-extras`")
        return 2
    # the agent's own log lines (a cache refused or skipped: scrubbed)
    logging.basicConfig(format="  [%(name)s] %(message)s")
    logging.getLogger("synthlab.studio.agent").setLevel(logging.INFO)
    print(
        f"model {cfg.model}, {len(picked)} case(s), prefix cache "
        f"{'on' if cfg.cache else 'off'} — real API calls"
    )
    results, tokens, cached, cost, unpriced = [], 0, 0, 0.0, 0
    for i, case in picked:
        res = run_case(i, case)
        results.append(res)
        tokens += res.get("tokens", 0)
        cached += res.get("tokens_cached", 0)
        if res.get("cost_usd") is None:
            unpriced += 1
        else:
            cost += res["cost_usd"]
        mark = "PASS" if res.get("passed") else "FAIL"
        detail = "; ".join(
            f"{f['feature']} {f['before']}->{f['after']}"
            + ("" if f["met"] else " (missed)")
            for f in res.get("features", [])
        )
        detail += "".join(
            f"; {k}{'' if v else ' (missed)'}" for k, v in res.get("checks", {}).items()
        )
        print(
            f"{mark} {i:2d}. [{res.get('sound')}] {res.get('request')!r} "
            f"{res.get('seconds')} s, {res.get('tokens', 0)} tok"
            f" ({res.get('tokens_cached', 0)} cached)"
            + (f": {detail}" if detail else "")
            + (f" ERROR {res['error']}" if res.get("error") else "")
        )
    n = sum(1 for r in results if r.get("passed"))
    spent = f", {money(cost)}" if cost else ""
    spent += f" (+ {unpriced} unpriced)" if unpriced else ""
    print(
        f"\n{n}/{len(results)} passed, {tokens:,} tokens ({cached:,} read from a "
        f"cache){spent}"
    )
    if cfg.cache:  # cached tokens with 0 made are Gemini's implicit cache
        print(f"prefix cache: {CACHES.made} made, {CACHES.refused} refused")
    if args.out:
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0 if n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
