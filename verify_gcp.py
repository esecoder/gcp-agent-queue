#!/usr/bin/env python3
"""
verify_gcp.py — a REAL publish → deliver → ack round trip against your own GCP project.

    gcloud auth application-default login
    gcloud config set project YOUR_PROJECT_ID
    python3 verify_gcp.py

===============================================================================
⚠️ WHY THIS FILE EXISTS SEPARATELY
===============================================================================
`agent_queue.py` is the platform. This is the **evidence** — one command that creates a
topic and a subscription, publishes real messages, pulls them back, acknowledges them,
and prints every step so the output is the proof rather than a claim.

    gcloud auth application-default login
    gcloud config set project YOUR_PROJECT_ID
    python3 verify_gcp.py

⚠️ IT REFUSES TO RUN AGAINST AN EMULATOR. If `PUBSUB_EMULATOR_HOST` is set it exits,
because a green run against an emulator is exactly the kind of evidence this repository
should not produce. The emulator is not GCP, and reporting one as the other is the
failure mode every other project here is written against.

⚠️ AND IT CLEANS UP AFTER ITSELF. Topics and subscriptions left behind are a bill and a
footgun; the `finally` block deletes both even on failure.
"""

from __future__ import annotations

import os
import sys
import time

SUFFIX = time.strftime("%Y%m%d-%H%M%S")
TOPIC = f"agent-runs-verify-{SUFFIX}"
SUBSCRIPTION = f"agent-runs-verify-sub-{SUFFIX}"


def main() -> int:
    print("verify_gcp.py — a real Pub/Sub round trip\n")

    # ⚠️ The emulator check is first, before anything is created.
    if os.environ.get("PUBSUB_EMULATOR_HOST"):
        print(f"  ❌ PUBSUB_EMULATOR_HOST is set ({os.environ['PUBSUB_EMULATOR_HOST']})")
        print("     This script is for REAL GCP. Unset it and re-run.")
        return 2

    try:
        from google.cloud import pubsub_v1
        from google.auth import default
    except ImportError:
        print("  ❌ pip install google-cloud-pubsub")
        return 2

    creds, project = default()
    if not project:
        print("  ❌ no project resolved. Run: gcloud config set project YOUR_PROJECT_ID")
        return 2

    print(f"  project    {project}")
    print(f"  credential {type(creds).__name__}")
    print(f"  topic      {TOPIC}\n")

    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    topic_path = publisher.topic_path(project, TOPIC)
    sub_path = subscriber.subscription_path(project, SUBSCRIPTION)

    steps: list[tuple[str, bool, str]] = []
    try:
        # ── 1. create ────────────────────────────────────────────────────────
        publisher.create_topic(request={"name": topic_path})
        steps.append(("topic created", True, TOPIC))

        subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})
        steps.append(("subscription created", True, SUBSCRIPTION))

        # ── 2. publish ───────────────────────────────────────────────────────
        import json
        ids = []
        for i in range(3):
            payload = json.dumps({"run_id": f"verify-{i}", "agent": "digest",
                                  "tier": "read_only", "payload": {}, "human_approved": False})
            mid = publisher.publish(topic_path, payload.encode("utf-8")).result(timeout=30)
            ids.append(mid)
        steps.append(("3 messages published", len(ids) == 3, ", ".join(x[:12] for x in ids)))

        # ── 3. deliver ───────────────────────────────────────────────────────
        # ⚠️ Pub/Sub delivery is not instant on a brand-new subscription. A single pull
        # that returns nothing is NOT evidence of failure, and treating it as one is how
        # people conclude a working system is broken.
        received = []
        deadline = time.time() + 60
        while time.time() < deadline and len(received) < 3:
            resp = subscriber.pull(request={"subscription": sub_path, "max_messages": 10},
                                   timeout=20)
            received.extend(resp.received_messages)
            if len(received) < 3:
                time.sleep(2)
        steps.append(("3 messages delivered", len(received) == 3, f"{len(received)} received"))

        got_ids = sorted(json.loads(m.message.data.decode())["run_id"] for m in received)
        steps.append(("payloads intact", got_ids == ["verify-0", "verify-1", "verify-2"],
                      ",".join(got_ids)))

        # ── 4. ack ───────────────────────────────────────────────────────────
        if received:
            subscriber.acknowledge(request={"subscription": sub_path,
                                           "ack_ids": [m.ack_id for m in received]})
        steps.append(("acknowledged", bool(received), f"{len(received)} acked"))

        # ── 5. ⚠️ THE PART THAT PROVES THE ACK MEANT SOMETHING ───────────────
        # A pull after acking must return NOTHING. Without this the previous step proves
        # only that an API call did not raise -- which is not the same as delivery having
        # happened and been confirmed.
        empty = subscriber.pull(request={"subscription": sub_path, "max_messages": 10},
                                timeout=20)
        steps.append(("acked messages are gone", len(empty.received_messages) == 0,
                      f"{len(empty.received_messages)} redelivered"))

    except Exception as e:
        steps.append((f"FAILED: {type(e).__name__}", False, str(e)[:90]))
    finally:
        # ⚠️ CLEANUP RUNS EVEN ON FAILURE. A leftover topic is a recurring bill and a
        # footgun; a verification script that leaks resources is not finished.
        for label, fn in (("subscription deleted", lambda: subscriber.delete_subscription(
                              request={"subscription": sub_path})),
                          ("topic deleted", lambda: publisher.delete_topic(
                              request={"topic": topic_path}))):
            try:
                fn()
                steps.append((label, True, ""))
            except Exception as e:
                steps.append((label, False, type(e).__name__))

    print()
    for name, ok, detail in steps:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")

    failed = [n for n, ok, _ in steps if not ok]
    print()
    if failed:
        print(f"  ❌ {len(failed)} failed: {', '.join(failed)}")
        return 1
    print("  ✅ REAL GCP: published, delivered, acknowledged, and confirmed acked")
    print(f"     project {project} — this is not an emulator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
