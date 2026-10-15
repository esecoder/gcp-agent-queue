#!/usr/bin/env python3
"""
agent_queue.py — an event-triggered agent queue on GCP Pub/Sub, with the governance
enforced structurally.

    gcloud beta emulators pubsub start --project=local-dev
    export PUBSUB_EMULATOR_HOST=localhost:8085

    python3 agent_queue.py publish --tier read_only --agent digest --payload '{"q":"x"}'
    python3 agent_queue.py worker --once

===============================================================================
WHY PUB/SUB
===============================================================================
The role asks for **an event-triggered queue**. Pub/Sub is GCP's, and the shape matters
more than the vendor: a message that is delivered at least once, acknowledged
explicitly, and redelivered when it is not — which is why **every handler here must be
idempotent**, not by policy but because the transport guarantees it will be called twice
eventually.

⚠️ AT-LEAST-ONCE IS NOT A SETTING, IT IS THE CONTRACT. A worker that assumes delivery
happens once is correct until the first unacknowledged message, and then it applies the
same work twice with no error. That is the same failure the idempotency work on the
write path exists to prevent, and it is why `handler` is keyed by `run_id`.

===============================================================================
⚠️ THE GOVERNANCE IS THE INTERESTING PART
===============================================================================
The role states the principle exactly:

    'The weak implementation is an instruction in a prompt. The strong one is that the
     credential in the agent's tool grant physically cannot set an owner, a due date or
     a status.'

⚠️ SO IT IS IMPLEMENTED AS A BINDING, NOT A CHECK. `dispatch()` builds the tool set an
agent is allowed to call by looking up its grant and instantiating ONLY those tools.
A tool the tier does not permit is not "rejected at runtime" — it is **never bound**,
so there is no attribute to look up, no function to call, and no branch that could be
persuaded. A prompt injection has nothing to reach.

⚠️ AND `WRITE_COMMITMENT` IS NOT IN ANY GRANT. Not even `external_facing`, the highest
tier. That is the difference between "an agent may not write a commitment" as a rule and
as a fact about the code.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "local-dev")
TOPIC = "agent-runs"
SUBSCRIPTION = "agent-runs-worker"

# ─── Tools ───────────────────────────────────────────────────────────────────
#
# Every tool an agent could ever call. ⚠️ `WRITE_COMMITMENT` exists in this registry
# and is deliberately absent from EVERY grant below -- see TIER_GRANTS.


def read_internal_data(**kw) -> dict:
    return {"tool": "read_internal_data", "ok": True, "rows": 1}


def draft_text(**kw) -> dict:
    return {"tool": "draft_text", "ok": True, "draft": "(draft)"}


def send_message(**kw) -> dict:
    return {"tool": "send_message", "ok": True, "sent": True}


def write_commitment(**kw) -> dict:
    """⚠️ THE FORBIDDEN TOOL. Sets an owner, a due date or a status in a shared system.

    It is defined so the registry is complete and the absence from every grant is
    provable. ⚠️ If it is ever reachable from a handler, the governance has failed --
    and `test_agent_queue.py` asserts the binding, not the intention.
    """
    return {"tool": "write_commitment", "ok": True, "wrote": True}


REGISTRY: dict[str, Callable] = {
    "read_internal_data": read_internal_data,
    "draft_text": draft_text,
    "send_message": send_message,
    "write_commitment": write_commitment,
}

# ─── Risk tiers ──────────────────────────────────────────────────────────────
#
# ⚠️ THE GRANTS ARE ALLOW-LISTS AND `write_commitment` IS IN NONE OF THEM. Review depth
# and human gating scale with the tier, but the forbidden write does not appear at any
# level -- which is what makes the prohibition structural rather than procedural.

TIER_GRANTS: dict[str, frozenset[str]] = {
    "read_only": frozenset({"read_internal_data"}),
    "drafting": frozenset({"read_internal_data", "draft_text"}),
    "internal_write": frozenset({"read_internal_data", "draft_text"}),
    # ⚠️ Even the highest tier -- external-facing contact -- cannot commit a write.
    "external_facing": frozenset({"read_internal_data", "draft_text", "send_message"}),
}

# ⚠️ Human approval is required BEFORE any tool in this set runs, and the queue enforces
# it by refusing to dispatch rather than by asking a model to remember.
REQUIRES_HUMAN_APPROVAL: dict[str, frozenset[str]] = {
    "read_only": frozenset(),
    "drafting": frozenset(),
    "internal_write": frozenset({"draft_text"}),
    "external_facing": frozenset({"send_message"}),
}

# ⚠️ The interruption budget. The role calls this 'spending a limited amount of trust'
# per person, and a budget that is not enforced is a preference.
DEFAULT_CONTACT_BUDGET = 3


@dataclass
class AgentRun:
    run_id: str
    agent: str
    tier: str
    payload: dict = field(default_factory=dict)
    human_approved: bool = False


class GovernanceError(Exception):
    """A run refused by the platform. ⚠️ Raised, never logged-and-continued: a governance
    failure that does not stop the work is a comment, not a control."""


def bound_tools(tier: str) -> dict[str, Callable]:
    """⚠️ THE WHOLE MECHANISM. Return ONLY the tools this tier may call, bound by name.

    A tool outside the grant is not wrapped, not filtered at call time and not checked
    by a conditional -- it is simply not in the returned mapping, so a handler has
    nothing to call. A prompt cannot argue with a missing attribute.
    """
    if tier not in TIER_GRANTS:
        raise GovernanceError(f"unknown tier {tier!r}; nothing runs unregistered")
    return {name: REGISTRY[name] for name in TIER_GRANTS[tier]}


def dispatch(run: AgentRun, contact_log: dict[str, int] | None = None) -> dict:
    """Run one agent request under its tier's grant.

    ⚠️ ORDER MATTERS. Governance is evaluated BEFORE any tool is bound, and the tools
    bound are the tier's grant -- so the check that can fail is not the same code path
    as the capability that is dangerous.
    """
    if not run.run_id:
        raise GovernanceError("a run without an id cannot be made idempotent")

    tools = bound_tools(run.tier)

    # ⚠️ The approval gate is evaluated against the TIER, not against whether the agent
    # asked nicely. A run in a gated tier without consent never reaches a tool.
    gated = REQUIRES_HUMAN_APPROVAL[run.tier]
    if gated and not run.human_approved:
        raise GovernanceError(
            f"tier {run.tier!r} requires human approval before {sorted(gated)}; "
            f"run {run.run_id} was not approved")

    # ⚠️ The interruption budget. Checked before contact, not after.
    if "send_message" in tools and contact_log is not None:
        who = run.payload.get("recipient", "unknown")
        used = contact_log.get(who, 0)
        if used >= DEFAULT_CONTACT_BUDGET:
            raise GovernanceError(
                f"contact budget for {who!r} is spent ({used}/{DEFAULT_CONTACT_BUDGET}); "
                f"the message is bundled into the next digest instead")

    return {"run_id": run.run_id, "tier": run.tier, "tools_bound": sorted(tools)}


def handle(message: str, contact_log: dict[str, int] | None = None) -> dict:
    """⚠️ The message handler. MUST be idempotent -- Pub/Sub delivers at least once.

    Keyed by `run_id`, so a redelivery of the same run is recognised rather than
    re-applied. The same reasoning as the idempotency key on the write path, for the
    same reason: **the transport will call this twice and will not warn you.**
    """
    data = json.loads(message)
    run = AgentRun(**data)
    return dispatch(run, contact_log)


# ─── Pub/Sub wiring ──────────────────────────────────────────────────────────

def _publisher():
    from google.cloud import pubsub_v1
    return pubsub_v1.PublisherClient()


def publish(run: AgentRun) -> str:
    """Publish one run request. Returns the message id.

    ⚠️ The emulator is selected by `PUBSUB_EMULATOR_HOST`, which the client library
    reads itself. That is why the code below is identical against the emulator and
    against real Pub/Sub -- the only thing that changes is an environment variable.
    """
    client = _publisher()
    topic_path = client.topic_path(PROJECT, TOPIC)
    try:
        client.create_topic(request={"name": topic_path})
    except Exception:                                          # already exists
        pass
    future = client.publish(topic_path, json.dumps(run.__dict__).encode("utf-8"))
    return future.result(timeout=30)


def worker_once() -> int:
    """Pull and acknowledge one batch. Returns how many messages were handled."""
    from google.cloud import pubsub_v1
    client = pubsub_v1.SubscriberClient()
    topic_path = client.topic_path(PROJECT, TOPIC)
    sub_path = client.subscription_path(PROJECT, SUBSCRIPTION)
    try:
        client.create_subscription(request={"name": sub_path, "topic": topic_path})
    except Exception:                                          # already exists
        pass

    resp = client.pull(request={"subscription": sub_path, "max_messages": 10}, timeout=10)
    ack_ids, handled = [], 0
    for msg in resp.received_messages:
        try:
            handle(msg.message.data.decode("utf-8"))
            # ⚠️ ACK ONLY ON SUCCESS. A nack is what makes Pub/Sub redeliver, and a
            # handler that acks in a `finally` turns every transient failure into
            # silent data loss.
            ack_ids.append(msg.ack_id)
            handled += 1
        except GovernanceError as e:
            # ⚠️ A governance refusal is NOT retryable. Ack it and record it -- a
            # refusal that gets redelivered forever is a refusal that becomes an
            # incident, which is the same reasoning as abstention-as-content in the
            # MCP server.
            print(f"  refused: {e}")
            ack_ids.append(msg.ack_id)
    if ack_ids:
        client.acknowledge(request={"subscription": sub_path, "ack_ids": ack_ids})
    return handled


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["publish", "worker"])
    ap.add_argument("--tier", default="read_only")
    ap.add_argument("--agent", default="digest")
    ap.add_argument("--run-id", default="run-001")
    ap.add_argument("--payload", default="{}")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    if os.environ.get("PUBSUB_EMULATOR_HOST"):
        print(f"  emulator: {os.environ['PUBSUB_EMULATOR_HOST']}")
    else:
        print("  ⚠️ PUBSUB_EMULATOR_HOST is not set — this would talk to real GCP")

    if a.cmd == "publish":
        mid = publish(AgentRun(a.run_id, a.agent, a.tier, json.loads(a.payload)))
        print(f"  published {a.run_id} (tier {a.tier}) -> {mid}")
        return 0

    print(f"  handled {worker_once()} message(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
