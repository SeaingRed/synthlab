"""The sound agent: a Gemini-driven assistant that edits the Synths sound
through tools generated from the parameter registry.

- `config`  — the key (from `<repo>/.env` or the environment), model,
  budgets, timeouts; `scrub` for anything that came off the network
- `tools`   — tool declarations built from `params`/`paramui`/`fxrack`,
  and their validated, clamped application to the session
- `context` — the compact sound description, measured features, the
  self-check, stack snapshots and the per-turn changes list
- `client`  — the `ModelClient` interface and the google-genai adapter
- `service` — `AgentService`: a turn (lock for state, network outside it),
  undo, A/B, apply to a layer; the session's budget

See `synthlab/studio/docs/agent.md` ("Sound agent").
"""

from .config import AgentConfig, load_config, scrub
from .service import AgentService

__all__ = ["AgentConfig", "AgentService", "load_config", "scrub"]
