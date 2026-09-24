# Jarvis ↔ Conference integration contract (DRAFT — contract only, no implementation)

Status labels used here: SOURCE-VERIFIED, TEST-VERIFIED, LIVE-VERIFIED,
EXTERNALLY-VERIFIED, UNVERIFIED.

**Everything in this document is UNVERIFIED against the real Conference source.**
Devin has not yet been given the recovered Floot/Conference source, so this is a
contract proposal written to fit the architecture Michael stated, deliberately
shaped so it can be mapped onto the existing Conference modules rather than
replacing them. Nothing here creates a second Conference, a second Jarvis, or a
second source of truth.

## 0. Roles (restating the boundary so the contract cannot drift)

| Component | Owns | Must never |
|---|---|---|
| Jarvis | Michael's intent, conversation, notification policy, operational memory | Write canonical job state directly; decide a job is DONE |
| Conference | Canonical jobs, claims, fences, leases, dispatch, receipts, verification, canonical state | Become a chat UI; trust a provider's self-report |
| Worker (Devin/Codex/Claude) | Doing the work, returning a fenced structured return | Mark its own work verified |
| BJVI OS | Reading authoritative state | Write state; hold its own copy of truth |

The single authoritative writer is Conference. Jarvis is a **client** of
Conference, exactly like BJVI OS is, but with command privileges.

## 1. Jarvis → Conference (intent in)

Jarvis never writes jobs. It submits an *intent*; Conference decides whether that
becomes one job, several jobs, or a clarification request.

```
POST /conference/intents
{
  "intent_id":    "<uuid, Jarvis-generated, idempotency key>",
  "actor":        "michael",
  "channel":      "jarvis",
  "utterance":    "<what Michael actually said/typed>",
  "interpreted":  { "goal": "...", "constraints": [...], "deadline": null },
  "context_refs": [ { "kind": "email|doc|url|job", "ref": "..." } ]
}
→ 202 { "intent_id", "jobs": [ { "job_id", "state": "queued" } ],
        "clarification_required": false }
```

Rules:
- `intent_id` is the idempotency key. Re-submitting the same intent must never
  create a second job. This is the Jarvis-side mirror of the worker-side
  duplicate guard already implemented in the adapter (TEST-VERIFIED there).
- Jarvis may not supply `job_id`, `fence_token`, `state`, or `verification_state`.
  Conference mints all of those. If Jarvis could mint them, there would be two
  writers.
- If Conference cannot safely interpret the intent, it returns
  `clarification_required: true` with `questions[]`, and Jarvis asks Michael.
  Conference never guesses in order to look productive.

## 2. Conference → worker (dispatch out)

This half is IMPLEMENTED and TEST-VERIFIED for Devin in
`devin_worker_adapter.py`:

```
job (job_id, fence_token, lease_seconds)
  → dispatch_once()  # duplicate guard + idempotent:true
  → Claim(session_id, session_url, dispatched_at)
  → wait_for_return(lease_seconds, poll_interval, timeout)
  → WorkerReturn(terminal | lease_expired | needs_human)
  → build_receipt(...)  # verification_state ALWAYS "unverified"
```

Other providers (Codex, Claude) must implement the same three methods —
`dispatch_once`, `wait_for_return`, and a receipt builder that cannot write
anything except `"unverified"`. That is the whole worker-provider interface;
Conference should depend on that interface, not on Devin specifics.

## 3. Worker → Conference (return in)

**EXTERNALLY-VERIFIED negative finding:** Devin has no outbound per-session
completion webhook. Devin's webhooks are inbound triggers. Therefore the return
path is Conference polling `GET /v1/sessions/{id}`, not Devin calling Conference.
Any design that assumes a Devin callback is false as of today's documentation.

A return produces a persisted receipt row before anything else happens:

```
receipt {
  job_id, fence_token, worker, session_id, session_status, received_at,
  return_valid, outcome, summary, evidence[], tests[], blockers[], next_action,
  pull_request_url, lease_expired, needs_reroute,
  verification_state: "unverified"
}
```

Receipts are append-only. A superseded attempt's receipt is kept, marked
`return_valid: false` when its fence token is stale. Evidence is never deleted to
make a job look clean.

## 4. Conference verification (the step that actually decides DONE)

`returned_complete` from a worker means "the worker claims it finished". It is an
input to verification, not an outcome.

```
verify(job, receipt) →
  { verification_state: "verified" | "rejected" | "needs_human",
    checks: [ { name, result, evidence_ref } ] }
```

Minimum checks per evidence kind:
- `pull_request` → the PR exists, targets the expected repo/branch, and CI status
  is readable. A PR URL alone is not verification.
- `commit` → the commit exists on the claimed branch.
- `command_output` / `tests` → re-run independently where cheap; a worker's own
  test output is testimony, not proof.
- `file` / `url` → fetchable and non-empty.

Only `verified` may advance canonical state. `rejected` reroutes. Jobs must be
able to sit in `unverified` indefinitely without anything downstream treating them
as complete.

## 5. Canonical state transitions (the only legal moves)

```
queued → claimed → running
running → returned_unverified        (receipt persisted)
returned_unverified → verified       (Conference verification only)
returned_unverified → rejected → queued   (reroute, new fence_token)
running → lease_expired → queued     (reroute, new fence_token)
running → blocked_human              (Jarvis surfaces a Michael gate)
any → cancelled                      (explicit Michael/Jarvis command)
```

Every reroute mints a **new** fence token, which is what makes a late return from
the old attempt harmless — the adapter already rejects it (TEST-VERIFIED).

## 6. Conference → Jarvis (notifications out)

Jarvis subscribes; it does not poll Michael's behalf in a loop.

```
GET  /conference/events?since=<cursor>    # cursor-based, replayable
POST /jarvis/webhook                      # Conference pushes, Jarvis ACKs
```

Event types worth waking Michael for (everything else is silent):
- `job.blocked_human` — a genuine Michael-only gate.
- `job.verified` — only if the job was flagged `notify_on_complete`.
- `job.rejected` after N reroutes — the work is not converging.
- `intent.clarification_required`.

Explicitly NOT notification-worthy: dispatch, claim, polling, a provider
returning, or a receipt being written. Michael asked to be notified only when
necessary; "a worker said it's done" is not necessary, because it is not yet true.

## 7. BJVI OS

Read-only over the same canonical state:
`GET /conference/state/executive` — derived views only, no writes, no local copy
treated as truth.

## 8. What must be checked against the recovered source before any of this is built

1. Does the existing Conference kernel already define job/claim/fence/lease
   tables? If so, adopt its names — do not introduce parallel ones.
2. Does the existing canonical job writer already enforce single-writer
   semantics? If so, this contract's rule 1 is already satisfied and Jarvis just
   needs to call it.
3. Does the existing notifications module already have an event taxonomy? Map
   section 6 onto it rather than adding a second notifier.
4. Does the existing worker routing / fallback module already model reroute? If
   so, `needs_reroute` from the adapter should feed it directly.

These four questions are the first thing Devin will answer when Codex delivers the
source.
