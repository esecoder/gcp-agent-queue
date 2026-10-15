# gcp-agent-queue

**An event-triggered agent queue on GCP Pub/Sub, with the governance enforced structurally.**

```bash
python3 test_agent_queue.py     # 16 checks, no dependencies
```

## ⚠️ STATUS: PARTIALLY VERIFIED — READ THIS FIRST

| | |
|---|---|
| Governance layer (the interesting part) | ✅ **tested, 16 checks passing** |
| `gcloud` SDK + Pub/Sub emulator | ✅ **installed and running locally** |
| Publish/subscribe against the emulator | ❌ **NOT WORKING** — see below |

⚠️ **Nothing has run against real GCP.** No account, no project, no credentials. What
follows separates what was demonstrated from what was not, because a green test suite
next to an unchecked claim is exactly the failure this whole portfolio argues against.

## ⚠️ What is demonstrated: the governance

The role states the principle precisely:

> *"The weak implementation is an instruction in a prompt. The strong one is that the
> credential in the agent's tool grant **physically cannot** set an owner, a due date or
> a status."*

**Implemented as a binding, not a check.** `dispatch()` builds the tool set by looking up
the tier's grant and instantiating **only those tools**. A tool outside the grant is not
rejected at runtime — it is **never bound**, so there is no attribute to look up and no
branch that could be persuaded. A prompt injection has nothing to reach.

```
PASS  write_commitment is NOT bound at tier 'read_only'
PASS  write_commitment is NOT bound at tier 'drafting'
PASS  write_commitment is NOT bound at tier 'internal_write'
PASS  write_commitment is NOT bound at tier 'external_facing'
PASS  write_commitment still exists in the registry
PASS  no grant anywhere contains write_commitment
```

⚠️ **`write_commitment` is in the registry and in no grant.** It is defined *so the absence
is provable* — a tool that does not exist proves nothing about the governance; a tool that
exists and is unreachable at every tier is the guarantee. ⚠️ **Not even `external_facing`,
the highest tier, can commit a write.**

The test asserts the **capability**, not the refusal. Checking for a refusal would only
prove there is a guard; checking the binding proves there is nothing to guard against.

Also enforced and tested:

- **an unregistered tier binds nothing** — nothing runs without a tier
- **the human gate is on the tier, not the request** — `external_facing` without approval never reaches a tool
- **the contact budget** — the fourth message to one person is refused, and the refusal says *bundle*, not *silence*
- **idempotence by `run_id`** — ⚠️ at-least-once is the transport's contract, not a setting, so the handler is keyed and the same message returns the same result
- **a run with no id is refused at the door** — a run that cannot be de-duplicated cannot be made safe

## ✅ How to prove the transport on real GCP

⚠️ The local emulator is **broken on this machine** — it detects HTTP/2 and then fails in
its internal `TcpProxyHandler` with a broken pipe, so the round trip never completes. The
Docker image route was abandoned: a ~2GB pull for something that is still an emulator.

**The supported path is a real project, and it is quicker:**

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
pip install -r requirements.txt
./run.sh verify          # publish -> deliver -> ack -> confirmed
```

`verify_gcp.py` prints each step as PASS/FAIL, so the output is the evidence. ⚠️ Its last
step pulls *again* after acking and requires that **nothing comes back** — without it, the
ack step proves only that an API call did not raise.

⚠️ `agent_queue.py` needed **no changes** for this. It reads `GOOGLE_CLOUD_PROJECT` and
only consults `PUBSUB_EMULATOR_HOST` when that is set, which is why an emulator run and a
real run are the same code path.

## ⚠️ What is NOT demonstrated: the queue against a live project

The **emulator runs** — `cloud-pubsub-emulator` starts and listens on `127.0.0.1:8085` —
and the **client library is installed**. But a publish from the Python client never
completes; it hangs rather than erroring.

Diagnosed as far as this went:

- the emulator binds **IPv4 `127.0.0.1`**, while `localhost` resolves to **`::1`** first — so `PUBSUB_EMULATOR_HOST=127.0.0.1:8085` was tried, and it still hung
- `gcloud beta emulators pubsub start` refuses to run non-interactively, so the emulator jar was invoked directly
- a second emulator start crashed with a Netty bind exception, consistent with the first still holding the port

⚠️ **So the transport is written, reviewed and untested.** What that means concretely:
the Pub/Sub calls, the ack-only-on-success logic and the refusal-acking path are
**code that has never executed.** They are the part of this repository a reviewer should
not trust yet.

## The design decisions that do hold

**⚠️ Ack only on success.** A nack is what makes Pub/Sub redeliver; a handler that acks in a
`finally` turns every transient failure into silent data loss.

**⚠️ A governance refusal is acked, not retried.** A refusal that gets redelivered forever
is a refusal that becomes an incident — the same reasoning as returning an abstention as
content rather than as an error in `mcp-retrieval-server`.

**⚠️ The budget is checked before contact, not logged after it.** *"Spending a limited
amount of trust"* is only true if the limit is enforced.
