# Command Workers / Conference / Jarvis — actual state

Written from the recovered source in this branch, not from memory. Anything
not observed in code here is marked as such.

## Where the code is

The original working copy lived on a session VM that is no longer reachable.
The modules below were recovered from that session's attachments and pushed to
`sttmikeg-BJVI/read-me`, branch `command-workers/cycle3-recovery`, purely as
off-VM preservation. They do **not** belong to the `read-me` handoff service in
PR #1 and must not be merged into it. They need their own repository
(`sttmikeg-BJVI/command-workers`), which does not exist yet.

Three test modules were never attached anywhere and are lost:
`test_worker_provider.py`, `test_receipt_ledger.py`, `test_jarvis_contract.py`.
The modules they covered are intact; only their tests are gone. That is why the
suite here reports fewer tests than the 81 reported at head `5ef9823`.

## Components

| Component | File | What it actually is |
| --- | --- | --- |
| Jarvis ↔ Conference boundary | `jarvis_contract.py` | Types + projection only. Canonical states, the six surfaces Michael sees, the fields Jarvis may not set, notification eligibility. No storage, no runtime. |
| Provider boundary | `worker_provider.py` | `WorkerProvider` protocol, capability vocabulary, `ProviderDescriptor` per worker, registry, provider-neutral job payload. Every descriptor is `credentials_available=False, live_verified=False`. |
| Devin adapter | `devin_worker_adapter.py` | The only concrete provider. Dispatch, poll, fenced return parsing, `build_receipt`. Devin has no outbound completion webhook, so the return mode is polling. |
| Reroute / gating policy | `reroute_policy.py` | Pure decisions: dependency gate, fence minting per attempt, attempt caps, blocked → human, zero-provider → escalate. |
| Independent verification | `conference_verification.py` | Turns a receipt into a `Verdict` by checking cited evidence through an injected checker. `not_run` never counts as success. |
| Receipt semantics | `receipt_ledger.py` | Reference in-memory ledger: content digest + duplicate-return protection. Superseded by the store below for anything that must survive a restart. |
| Canonical store | `conference_store.py` | **New in this branch.** SQL persistence of jobs, attempts/leases, receipts and verification, enforcing the above rules in the schema. |
| Conference runtime | `conference_runtime.py` | **New in this branch.** The loop: plan → claim → dispatch → return → receipt → verify → state → reroute/escalate → notify. Owns no policy; transport and evidence checker are injected. |
| Jarvis runtime | `jarvis.py` | **New in this branch.** Michael's surface: instruct, status, attention, follow_up, recall, escalate. Holds no job state; its only writes are `submit_intent` and an escalation that can only ever ask for a human. |
| Notifications | `conference_runtime.Notifier` | Seam exists and is called at the right moments; the default notifier **records rather than delivers**. No Slack/Monday delivery is wired up here — that lives in the separate PR #1 service. |
| Social publishing backend | `social_engine.py` | **New in this branch.** Accounts/scopes (token by reference), assets, publish jobs, adapter protocol, queue/scheduler decisions, classified retry. No platform adapter is implemented. Codex's Zeely browser work is untouched. |
| Claude / Codex handoff | — | Declared as provider descriptors with UNVERIFIED capabilities and no credentials. No adapter, no transport. |

## What is proven, and how

* 90 tests pass, with the store suite parametrised over **real PostgreSQL 16**
  (local container) as well as SQLite, including reconnect-after-restart.
  75 of those pass without Postgres available.
* Conference acceptance runs against the persisted store, not a dict: verified,
  reroute, attempt cap, blocked worker, no-eligible-provider, and restart.
* Jarvis acceptance pins the limits: it cannot mint an id, cannot verify, and
  reports VERIFYING (never DONE) for work a worker merely returned.
* Every worker interaction is still a fake transport. No live dispatch, no live
  return, no live receipt, no live notification has happened in this project.
* Neon specifically is **unproven**: the Postgres code path is proven, the Neon
  instance is not, because no DSN exists.

## The chain, and where it breaks today

```
Michael → Jarvis            jarvis.instruct                    DONE (typed text; no voice)
→ canonical job             conference_store.submit_intent     DONE (persisted, idempotent)
→ provider selection        registry + store.plan              DONE (offline)
→ fenced dispatch           store.claim + devin adapter        DONE offline, NO CREDENTIALS
→ return                    adapter.poll + build_receipt       FAKE TRANSPORT ONLY
→ receipt                   store.record_return                DONE (deduped, fenced, persisted)
→ verification              conference_verification            DONE (FAKE CHECKER ONLY)
→ DONE / BLOCKED / reroute  store.apply_verdict + plan         DONE (persisted)
→ notification              runtime notifier seam              DECIDED, NOT DELIVERED
```

The two remaining fakes are injected objects, not control flow: a Devin API key
replaces the transport, and a checker that really opens a PR/URL replaces the
evidence checker. Nothing else in the loop changes to go live.

## Gates that are not engineering work

1. A repository for this code (`sttmikeg-BJVI/command-workers`) with Devin
   granted write access.
2. A Neon connection string, to turn the proven Postgres path into proven Neon
   persistence.
3. A Devin API key, to replace the fake transport with a live dispatch/return.
4. Claude and Codex transports, which have never been inspected — their
   declared capabilities are unverified guesses and should be treated as such.
5. Where notifications should land (the PR #1 Slack/Monday service, or direct
   credentials here). The seam is ready; the destination is a decision.
6. Platform app registrations + OAuth for anything the social backend should
   eventually publish to.
