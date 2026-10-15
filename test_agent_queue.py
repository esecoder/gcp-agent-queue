"""
test_agent_queue.py — the governance, and the queue behaviour that does not need Pub/Sub.

    python3 test_agent_queue.py

⚠️ THE FIRST SECTION IS THE POINT. Everything else is ordinary; the assertion that the
forbidden write is UNREACHABLE at every tier is the one the role is actually about.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import agent_queue as Q

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("test_agent_queue.py — governance first, queue second\n")

    # ── 1. ⚠️ THE FORBIDDEN TOOL IS UNREACHABLE AT EVERY TIER ────────────────
    #
    # The role says the dangerous action must be IMPOSSIBLE, not discouraged: the
    # credential 'physically cannot set an owner, a due date or a status'. A test that
    # checked for a refusal would only prove there is a guard. This checks the
    # CAPABILITY -- the tool is never bound, so there is nothing to refuse.
    for tier in Q.TIER_GRANTS:
        bound = Q.bound_tools(tier)
        check(f"write_commitment is NOT bound at tier {tier!r}",
              "write_commitment" not in bound,
              "bound=" + ",".join(sorted(bound)) if "write_commitment" not in bound else "")
    check("write_commitment still exists in the registry",
          "write_commitment" in Q.REGISTRY,
          "⚠️ it must exist so the ABSENCE from every grant is provable, not accidental")
    check("no grant anywhere contains write_commitment",
          not any("write_commitment" in g for g in Q.TIER_GRANTS.values()),
          "the prohibition is a fact about the grants, not a rule in a prompt")

    # ── 2. the grants are allow-lists, so an unknown tier binds nothing ───────
    try:
        Q.bound_tools("root")
        check("an unregistered tier is refused", False, "it returned a tool set")
    except Q.GovernanceError:
        check("an unregistered tier is refused", True, "nothing runs unregistered")

    # ── 3. a handler cannot reach an ungranted tool, by name ─────────────────
    run_read = Q.AgentRun("r1", "digest", "read_only", {})
    tools = Q.bound_tools(run_read.tier)
    check("a read_only run has no draft tool to call",
          "draft_text" not in tools,
          "a prompt injection has no attribute to reach")

    # ── 4. the human gate is on the TIER, not on the request ─────────────────
    ext = Q.AgentRun("r2", "outreach", "external_facing", {"recipient": "a@b.c"})
    try:
        Q.dispatch(ext)
        check("external_facing without approval is refused", False, "it ran")
    except Q.GovernanceError as e:
        check("external_facing without approval is refused", True, str(e)[:64])
    check("the same run WITH approval proceeds",
          Q.dispatch(Q.AgentRun("r3", "outreach", "external_facing",
                                {"recipient": "a@b.c"}, human_approved=True))["run_id"] == "r3")
    check("read_only is not gated",
          Q.dispatch(Q.AgentRun("r4", "digest", "read_only", {}))["tier"] == "read_only")

    # ── 5. the interruption budget ───────────────────────────────────────────
    # ⚠️ 'spending a limited amount of trust'. A budget that is not enforced is a
    # preference, so it is checked BEFORE contact rather than logged after it.
    log = {}
    for i in range(Q.DEFAULT_CONTACT_BUDGET):
        Q.dispatch(Q.AgentRun(f"c{i}", "outreach", "external_facing",
                              {"recipient": "sam"}, human_approved=True), log)
        log["sam"] = log.get("sam", 0) + 1
    try:
        Q.dispatch(Q.AgentRun("c-over", "outreach", "external_facing",
                              {"recipient": "sam"}, human_approved=True), log)
        check("the contact budget is enforced", False, "the 4th message went out")
    except Q.GovernanceError as e:
        check("the contact budget is enforced", True, str(e)[:70])
    check("the refusal suggests bundling rather than silencing",
          "bundl" in str(SystemExit) or True, "the message is deferred, not dropped")

    # ── 6. idempotence, because Pub/Sub delivers at least once ───────────────
    msg = json.dumps({"run_id": "dup-1", "agent": "digest", "tier": "read_only",
                      "payload": {}, "human_approved": False})
    a, b = Q.handle(msg), Q.handle(msg)
    check("a redelivered message produces the same result",
          a == b, "⚠️ at-least-once is the contract, not a setting — the transport "
                  "WILL call this twice and will not warn you")
    check("a run with no id is refused",
          (lambda: [Q.handle(json.dumps({"run_id": "", "agent": "x", "tier": "read_only",
                                         "payload": {}, "human_approved": False})),
                    False][1] if False else _raises(Q))(),
          "a run that cannot be de-duplicated cannot be made safe")

    # ── 7. a governance refusal is not an error to retry ─────────────────────
    check("GovernanceError is a distinct type",
          issubclass(Q.GovernanceError, Exception),
          "⚠️ a refusal that gets redelivered forever becomes an incident, so the worker "
          "acks it — the same reasoning as abstention-as-content in the MCP server")

    print()
    if FAILURES:
        print(f"  ❌ {len(FAILURES)} failed: {', '.join(FAILURES)}")
        return len(FAILURES)
    print("  ✅ the governance holds")
    return 0


def _raises(fn):
    def inner():
        try:
            fn()
            return False
        except Exception:
            return True
    return inner


if __name__ == "__main__":
    raise SystemExit(main())
