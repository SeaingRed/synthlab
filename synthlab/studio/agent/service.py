"""`AgentService`: one sound-agent session per studio process.

A turn is `begin` (under the studio lock: validate, render and measure the
current sound, describe it) and then `run` (the network calls OUTSIDE the
lock — the `render_outside_lock` pattern — re-taking the lock only to
apply a round of tool calls, render and measure the result). At most
`config.max_rounds` rounds of tool calls; after each the model gets the
new numbers and the self-check ("darker" must lower the centroid), and a
round that has used the last allowance gets no tools at all, only a
summary. The whole turn is ONE undo step (the patch ring) and its
outgoing stack is stashed as "Before last load"; `undo` restores the
whole stack (layers, FX, curves, macros, phrase) exactly.

The chat log and the budget (tokens, turns, the money estimate) persist
per data dir in `agent_log.json` (`schema.AGENT_LOG`: validated on load,
capped at `limits.MAX_AGENT_LOG_TURNS`, written atomically, every string
scrubbed of the key first); "New budget" zeroes the budget and keeps the
log. The snapshots `undo` needs stay runtime: after a restart the logged
turns are history, not undoable.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from pathlib import Path
from time import monotonic

from ...synth import Patch
from .. import limits
from . import context as ctx
from .client import (
    AgentError,
    GeminiClient,
    ModelClient,
    ToolTurn,
    UserTurn,
    reply_cost,
    sdk_available,
)
from .config import AgentConfig, load_config, scrub
from .tools import apply_call, declarations

log = logging.getLogger("synthlab.studio.agent")

MAX_MESSAGE = limits.MAX_AGENT_REQUEST_CHARS
MAX_CALLS_PER_ROUND = 12
MAX_TURNS_SHOWN = 12
UNDO_DEPTH = 8

CHIPS = (
    ("darker", "Make it darker"),
    ("brighter", "Make it brighter"),
    ("more movement", "Give it more movement"),
    ("wider", "Make it wider"),
    ("punchier", "Make it punchier"),
    ("festival lead", "Turn this into a festival supersaw lead"),
    ("vocal-chop feel", "Give it a vocal-chop feel"),
)


@dataclass
class Turn:
    id: int
    request: str
    reply: str = ""
    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    before: dict | None = None  # measured features
    after: dict | None = None
    checks: list[dict] = field(default_factory=list)
    tokens: int = 0
    tokens_cached: int = 0  # of `tokens`: input read from a context cache
    rounds: int = 0
    error: str = ""
    undone: bool = False
    model: str = ""  # the model that answered (the last reply's)
    cost_usd: float | None = None  # None: some reply had no known price
    # runtime: what `undo`, A/B and "apply to layer" need
    snap: dict | None = None
    patch_after: dict | None = None
    active_layer: int = 0

    @property
    def undoable(self) -> bool:
        return self.snap is not None and bool(self.changes) and not self.undone


# what `agent_log.json` leaves out of a turn: the runtime snapshots
_RUNTIME = ("snap", "patch_after", "active_layer")


def clean_message(raw: str) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    return text[:MAX_MESSAGE]


def money(usd: float | None) -> str:
    """The drawer's estimate: "≈ $0.004", "< $0.001", "" when unknown."""
    if usd is None:
        return ""
    if usd < 0.001:
        return "< $0.001" if usd > 0 else "$0"
    # two significant figures under a dollar: $0.0055, $0.004, $0.12
    return f"≈ ${usd:.2g}" if usd < 1 else f"≈ ${usd:,.2f}"


class AgentService:
    def __init__(
        self,
        config: AgentConfig | None = None,
        client_factory: Callable[[AgentConfig], ModelClient] | None = None,
        log_path: Path | None = None,
    ):
        self.config = config if config is not None else load_config()
        # tests hand in a scripted fake; the default reaches Gemini
        self.client_factory = client_factory
        self._client: ModelClient | None = None
        self.turns: list[Turn] = []
        self.tokens_used = 0
        self.turns_used = 0
        self.cost_usd = 0.0  # the budget's priced turns
        self.unpriced_turns = 0  # turns that spent tokens at no known price
        self.busy = False
        self._next_id = 1
        self._guard = threading.Lock()  # `busy` across the unlocked half
        self.log_path = log_path  # None: nothing persisted (a bare service)
        self._load_log()

    # -- the persisted log ----------------------------------------------------
    def _load_log(self) -> None:
        """Adopt `agent_log.json` through `schema.AGENT_LOG`: a missing,
        unreadable or hostile file is an empty log, never an error."""
        from .. import schema

        if self.log_path is None:
            return
        try:
            raw = json.loads(self.log_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        data = schema.conform(raw, schema.nodes().AGENT_LOG)
        self.tokens_used = data["tokens_used"]
        self.turns_used = data["turns_used"]
        self.cost_usd = data["cost_usd"]
        self.unpriced_turns = data["unpriced_turns"]
        self.turns = [Turn(**t) for t in data["turns"]]
        self._next_id = max([data["next_id"], *(t.id + 1 for t in self.turns)])

    def _save_log(self) -> None:
        """Write the log (tempfile + replace, capped, scrubbed). A failure
        is a log line: the turn itself already happened."""
        from .. import schema

        if self.log_path is None:
            return
        secrets = self.config.secrets()

        def clean(x):
            if isinstance(x, str):
                return scrub(x, secrets)
            if isinstance(x, list):
                return [clean(v) for v in x]
            if isinstance(x, dict):
                return {k: clean(v) for k, v in x.items()}
            return x

        turns = [
            {f.name: getattr(t, f.name) for f in fields(Turn) if f.name not in _RUNTIME}
            for t in self.turns[-limits.MAX_AGENT_LOG_TURNS :]
        ]
        payload = schema.conform(
            clean(
                {
                    "v": 1,
                    "tokens_used": self.tokens_used,
                    "turns_used": self.turns_used,
                    "cost_usd": self.cost_usd,
                    "unpriced_turns": self.unpriced_turns,
                    "next_id": self._next_id,
                    "turns": turns,
                }
            ),
            schema.nodes().AGENT_LOG,
        )
        tmp = None
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.log_path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=1))
            os.replace(tmp, self.log_path)
        except OSError as err:
            log.warning("could not write %s: %s", self.log_path.name, err)
        finally:
            if tmp is not None and os.path.exists(tmp):
                os.unlink(tmp)

    def new_budget(self) -> None:
        """Zero the budget (tokens, turns, money); the log stays."""
        self.tokens_used = self.turns_used = self.unpriced_turns = 0
        self.cost_usd = 0.0
        self._save_log()

    # -- availability ------------------------------------------------------
    def _refresh_config(self) -> None:
        """Pick up a key put in .env after the studio started (no restart)."""
        if not self.config.has_key and self.client_factory is None:
            self.config = load_config()
            self._client = None

    def unavailable_reason(self) -> str:
        if self.client_factory is not None:
            return ""
        if not sdk_available():
            return (
                "The sound agent needs the optional `agent` extra: "
                "run `uv sync --all-extras` (installs google-genai)."
            )
        if not self.config.has_key:
            return (
                "Add GEMINI_API_KEY=... to the .env file in the synthlab folder "
                "(optionally GEMINI_MODEL=gemini-flash-latest), then try again. "
                "The key stays on this machine; only the sound's settings and a "
                "2-second clip go to Gemini."
            )
        return ""

    def client(self) -> ModelClient:
        if self._client is None:
            factory = self.client_factory or GeminiClient
            self._client = factory(self.config)
        return self._client

    def view(self) -> dict:
        """The drawer's context: cheap, no I/O beyond the .env read."""
        self._refresh_config()
        reason = self.unavailable_reason()
        last = next((t for t in reversed(self.turns) if not t.error or t.changes), None)
        # the budget's money: its priced turns, "+" when some had no price,
        # nothing at all when none had one (no estimate beats a wrong one)
        cost = money(self.cost_usd) if self.tokens_used else ""
        if self.unpriced_turns:
            cost = f"{cost}+" if self.cost_usd else ""
        return {
            "available": not reason,
            "reason": reason,
            "model": self.config.model,
            "tokens_used": self.tokens_used,
            "token_budget": self.config.token_budget,
            "cost": cost,
            "money": money,
            "turns_used": self.turns_used,
            "turn_budget": self.config.turn_budget,
            "busy": self.busy,
            "turns": self.turns[-MAX_TURNS_SHOWN:],
            "last": last,
            "chips": CHIPS,
            "audio": self.config.audio,
        }

    # -- a turn --------------------------------------------------------------
    def _clip(self, result) -> bytes | None:
        cfg = self.config
        return ctx.audio_clip(result, stereo=cfg.audio_stereo) if cfg.audio else None

    def begin(self, svc, request, message: str):
        """Under the studio lock. A `Response` (a toast) when the turn can't
        start, else `run`: a no-argument callable the route hands to
        `OutsideLock`."""
        from ..render import RenderError

        text = clean_message(message)
        if not text:
            return svc.toast("Tell the agent what to change first.")
        if svc.view_of(request) != "editor":
            return svc.toast("The sound agent works on Synths.")
        self._refresh_config()
        reason = self.unavailable_reason()
        if reason:
            return svc.toast(f"Sound agent unavailable. {reason}")
        if self.tokens_used >= self.config.token_budget:
            return svc.toast(
                f"The agent budget is spent ({self.tokens_used:,} tokens): "
                "start another with New budget in the drawer."
            )
        if self.turns_used >= self.config.turn_budget:
            return svc.toast(
                f"The budget's {self.config.turn_budget} agent turns are used: "
                "start another with New budget in the drawer."
            )
        with self._guard:
            if self.busy:
                return svc.toast("The agent is still working on the last request.")
            self.busy = True
        try:
            s = svc.state
            result = svc.render("editor")
            before = ctx.measure(result)
            clip = self._clip(result)
            described = ctx.describe(s)
        except RenderError as err:
            self.busy = False
            return svc.toast(str(err))
        except Exception:
            self.busy = False
            raise
        turn = Turn(self._next_id, text, before=before, active_layer=s.active_layer)
        self._next_id += 1
        expect = ctx.expectations(text)
        prompt = (
            f"Request: {text}\n\nCurrent sound:\n{described}\n\n"
            f"Measured (the loop as the user hears it): {ctx.features_text(before)}"
        )
        if expect:
            prompt += "\nDirections to verify: " + ", ".join(
                f"{f} {'up' if sgn > 0 else 'down'}" for f, sgn, _t in expect
            )
        if clip:
            prompt += "\n" + ctx.clip_note(clip)

        def run():
            return self._run(svc, request, turn, prompt, clip, expect)

        return run

    def _run(self, svc, request, turn: Turn, prompt: str, clip, expect):
        """Outside the lock: the model calls. Each round of tool calls is
        applied, rendered and measured under the lock."""
        from ..render import RenderError

        s = svc.state
        cfg = self.config
        system = ctx.SYSTEM.format(rounds=cfg.max_rounds)
        tools = declarations()
        history: list = [UserTurn(prompt, clip)]
        deadline = monotonic() + cfg.turn_deadline_s
        turn.cost_usd = 0.0
        try:
            client = self.client()
            while True:
                remaining = deadline - monotonic()
                if remaining < 2.0:
                    raise AgentError("the turn ran out of time", kind="timeout")
                allow = turn.rounds < cfg.max_rounds
                reply = client.generate(
                    system=system,
                    history=history,
                    tools=tools,
                    allow_tools=allow,
                    timeout_s=min(cfg.timeout_s, remaining),
                )
                turn.tokens += max(0, int(reply.tokens))
                turn.tokens_cached += max(0, int(reply.tokens_cached_in))
                turn.model = reply.model or cfg.model
                cost = reply_cost(reply, cfg.price(turn.model))
                if cost is None or turn.cost_usd is None:
                    turn.cost_usd = None
                else:
                    turn.cost_usd += cost
                history.append(reply)
                if not reply.calls or not allow:
                    turn.reply = reply.text[: limits.MAX_AGENT_REPLY_CHARS]
                    break
                turn.rounds += 1
                results = []
                with s.lock:
                    if turn.snap is None:
                        turn.snap = ctx.snapshot(s)
                    for i, call in enumerate(reply.calls):
                        if i >= MAX_CALLS_PER_ROUND:
                            res = {"ok": False, "error": "too many calls in one round"}
                        else:
                            res = apply_call(s, svc, call.name, call.args)
                        for note in res.get("notes") or []:
                            turn.notes.append(f"{call.name}: {note}")
                        if not res.get("ok"):
                            turn.notes.append(f"{call.name}: {res.get('error')}")
                        results.append((call, res))
                    try:
                        rendered = svc.render("editor")
                        turn.after = ctx.measure(rendered)
                        after_clip = self._clip(rendered)
                        render_note = ""
                    except RenderError as err:
                        after_clip = None
                        render_note = f"The render failed: {err}. Undo that change."
                turn.checks = (
                    ctx.check(expect, turn.before, turn.after) if turn.after else []
                )
                left = cfg.max_rounds - turn.rounds
                feedback = render_note or (
                    f"Measured after these changes: {ctx.features_text(turn.after)}. "
                    + ctx.check_text(turn.checks)
                )
                feedback += (
                    f" You have {left} more round(s) of tool calls."
                    if left
                    else " No more tool calls: reply with your short summary."
                )
                history.append(ToolTurn(results, feedback, after_clip))
        except AgentError as err:
            turn.error = err.message
        except Exception as err:  # noqa: BLE001 - never a 500 with a key in it
            turn.error = "the agent failed unexpectedly"
            log.warning(
                "agent turn failed: %s",
                scrub(f"{type(err).__name__}: {err}", cfg.secrets()),
            )
        finally:
            with s.lock:
                response = self._finish(svc, request, turn)
        return response

    def _finish(self, svc, request, turn: Turn):
        """Under the lock: book the turn, make it one undo step, answer
        with the page (its drawer shows the turn) and, on an error, a toast."""
        import json

        s = svc.state
        self.busy = False
        self.tokens_used += turn.tokens
        self.turns_used += 1
        if turn.cost_usd is not None:
            self.cost_usd += turn.cost_usd
        elif turn.tokens:
            self.unpriced_turns += 1
        if turn.snap is not None:
            after = ctx.snapshot(s)
            turn.changes = ctx.diff(turn.snap, after)
            turn.patch_after = after["patch"]
            if turn.changes:
                before_patch = Patch.from_dict(turn.snap["patch"])
                s.push_undo_step(turn.snap["patch"])  # Ctrl+Z: one step
                s.slot_prev = before_patch  # "Before last load" brings back
                s.stack_prev = {  # the whole stack the turn replaced
                    "layers": turn.snap["layers"],
                    "main_fx": turn.snap["layers"][s.active_layer].get("fx") or [],
                    "curves": turn.snap["curves"],
                }
                mirror = getattr(s, "mirror_patch_to_track", None)
                if mirror is not None:
                    mirror()
                s.save()
        if turn.error and not turn.reply:
            turn.reply = ""
        self.turns.append(turn)
        # the snapshots `undo` reads: only the newest few are kept
        for old in self.turns[:-UNDO_DEPTH]:
            old.snap = None
        del self.turns[: -limits.MAX_AGENT_LOG_TURNS]
        self._save_log()
        resp = svc.full_page_with_preview(request)
        if turn.error and resp.status_code == 200:
            trig = json.loads(resp.headers.get("HX-Trigger") or "{}")
            kept = " (its changes so far were kept)" if turn.changes else ""
            trig["studio-toast"] = {
                "message": f"Sound agent: {turn.error}{kept}",
                "kind": "error",
            }
            resp.headers["HX-Trigger"] = json.dumps(trig)
        return resp

    # -- after a turn ----------------------------------------------------------
    def last_turn(self) -> Turn | None:
        return next((t for t in reversed(self.turns) if t.undoable), None)

    def undo(self, svc) -> str | None:
        """Put the whole stack back as it was before the newest undoable
        turn. An error message, or None."""
        t = self.last_turn()
        if t is None:
            return "No agent turn to undo."
        s = svc.state
        if s.active_layer != t.active_layer:
            return f"Select layer {t.active_layer + 1} again to undo that turn."
        current = s.patch_snapshot()
        ctx.restore(s, t.snap)
        s.push_undo_step(current)
        t.undone = True
        s.save()
        self._save_log()
        return None

    def store_ab(self, svc) -> str | None:
        """A = the sound before the newest turn, B = after it."""
        t = self.last_turn()
        if t is None or t.patch_after is None:
            return "No agent turn to compare."
        s = svc.state
        s.slot_a = Patch.from_dict(t.snap["patch"])
        s.slot_b = Patch.from_dict(t.patch_after)
        s.ab_live = "b"  # what plays now is the turn's result
        s.save()
        return None

    def apply_to_layer(self, svc, target: str) -> str | None:
        """Move the edited sound as it stands now onto another layer (a new
        one, following the main lane, or slot `target` 1-3), and put the
        edited layer back to how it was before the newest turn."""
        t = self.last_turn()
        if t is None:
            return "No agent turn to apply."
        s = svc.state
        if s.active_layer != t.active_layer:
            return f"Select layer {t.active_layer + 1} again first."
        result = s.patch
        before = Patch.from_dict(t.snap["patch"])
        current = s.patch_snapshot()
        if target == "new":
            if s.free_slot() is None:
                return "Every layer holds a sound: clear one first (✕)."
            s.add_layer(result, [], select=False, octave=0, gain=1.0)
        else:
            try:
                i = int(target) - 1
            except ValueError:
                return "Pick a layer."
            if not 0 <= i < len(s.layers):
                return "Pick a layer 1-3."
            if i == s.active_layer:
                return "That's the layer being edited: pick another one."
            if not s.layers[i].get("patch"):
                from ..state import _empty_layer

                if s.main_layer is None:
                    s.main_layer = s.main_index()
                s.layers[i] = {
                    **_empty_layer(),
                    "patch": result.to_json(),
                    "follow": {"octave": 0},
                }
            else:
                s.layers[i]["patch"] = result.to_json()
        s.patch = before
        s.push_undo_step(current)
        t.undone = True  # the edited layer is back where the turn found it
        s.save()
        self._save_log()
        return None
