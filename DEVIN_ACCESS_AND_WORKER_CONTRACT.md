# Devin lane report — access inventory + Conference worker adapter contract

Date: 2026-09-23. Everything below was checked from this machine today. Nothing is
assumed or copied from marketing material.

---

## 1. What access Devin actually has (VERIFIED)

| Question | Answer | Evidence |
|---|---|---|
| Git repos visible to Devin | Exactly one: `sttmikeg-BJVI/read-me`, and it is **PUBLIC** (`"private": false`) | `gh api repos/sttmikeg-BJVI/read-me`, `installation/repositories` → `total_count: 1` |
| `sttmikeg-BJVI/command-workers` | Does **not** exist / is not visible to Devin | `gh api repos/sttmikeg-BJVI/command-workers` → 404 |
| Can Devin create a GitHub repo? | **No.** Devin authenticates as the `devin-ai-integration[bot]` GitHub App installation, which cannot create repositories under a personal account | `POST /user/repos` → `403 Resource not accessible by integration` |
| Stored secrets/credentials | **None** (no org or session secrets) | `list_secrets` → "You have no secrets" |
| Installed integrations | GitHub, Slack. **No MCP servers installed** | integrations listing; `mcp_list_servers` → none |
| Floot access | **No.** No Floot credential, no Floot connector, no Floot MCP server. Devin cannot open or export Floot project `3bff58c5-…` today | secrets list + integration list above |
| Monday.com / Zapier / Gmail / Drive / Wix / Canva / Runway / Zeely | **No connector of any kind** in this session | integration list above |
| Outbound network | Works; `https://api.devin.ai` reachable | `curl https://api.devin.ai/v3/self` → HTTP 403 `Unauthorized` (endpoint alive, auth required) |
| Machine | Ubuntu Linux VM, shell + filesystem + browser + Python/Node toolchain | this report was produced on it |

**Consequence:** Codex keeps the Floot recovery and initial import lane. Devin cannot
duplicate it even if asked — there is no Floot access here.

**Warning (P0):** `read-me` is **public**. No recovered Command Workers source,
`.env`, connector token, Neon URL, or Monday/Slack key may be pushed there.

---

## 2. How Devin can legitimately be a Conference worker (VERIFIED against docs)

Devin's official public API is the only supported machine-to-machine boundary.
Base URL `https://api.devin.ai`. Auth: `Authorization: Bearer <key>` — either a
Personal Access Token or a service-user API key.

| Capability | Endpoint | Status |
|---|---|---|
| Dispatch a job to Devin | `POST /v1/sessions` (v3: `POST /v3/organizations/{org_id}/sessions`) | documented |
| Force a machine-readable return | `structured_output_schema` (JSON Schema draft-7, ≤64 KB, no external `$ref`) on session creation | documented |
| Read the return | `GET /v1/sessions/{session_id}` → `status_enum`, `structured_output`, `pull_request.url`, `messages[]` | documented |
| Duplicate prevention | `idempotent: true` on create, plus `tags[]` and `GET /v1/sessions?tags=…` | documented |
| Follow-up / unblock a worker | `POST /v1/sessions/{id}/message` (v3: `/v3/enterprise/sessions/{devin_id}/messages`) | documented |
| Attach files to a job | attachments upload endpoint | documented |
| Devin **calls Conference** when a job finishes | **NOT AVAILABLE as a per-session callback.** There is no documented outbound "session finished" webhook. Devin's webhooks are *inbound* (Automation webhook triggers: external system → Devin). | verified — no such endpoint in the API reference |

So the return path is **Conference polls `GET /v1/sessions/{id}`** (or a Devin
Automation posts to Slack, which is a notification, not a receipt). Anyone who tells
you Devin can POST a completion callback to your API is wrong today.

`status_enum` values: `working`, `blocked`, `expired`, `finished`,
`suspend_requested`, `suspend_requested_frontend`, `resume_requested`,
`resume_requested_frontend`, `resumed`.

Note: `blocked` means Devin is waiting on a human — that is the "worker stalled"
signal Jarvis should act on, and `POST …/message` is how it gets unstuck.

### Also real, but different in kind
- **Inbound webhook triggers / Automations**: Conference (or Zapier/Monday) can POST
  to a Devin Automation webhook URL to *start* a Devin session. Useful as a second
  ingress; still not a return channel.
- **Slack**: the Devin Slack app can start sessions from a channel and post responses
  back into that channel. Good for Michael-facing notification, not for canonical state.
- **GitHub**: Devin returning a PR is strong *evidence*, but a PR is not a receipt and
  merging is not verification.

---

## 3. The worker adapter contract (IMPLEMENTED + TESTED, not yet LIVE)

Files in this bundle:
- `devin_worker_adapter.py` — reference adapter, stdlib only, transport injected so it
  runs without a key.
- `test_devin_worker_adapter.py` — 16 tests, all passing offline.
- `JARVIS_CONFERENCE_CONTRACT.md` — draft Jarvis↔Conference contract (UNVERIFIED
  against the real Conference source, which Devin has not been given yet).

Rules the adapter enforces, matching your engineering rules:

1. **Assignment ≠ completion.** `dispatch()` returns a `Claim`, never a result.
2. **Fencing.** Every job carries a `fence_token`; Devin must echo `job_id` and
   `fence_token` in its structured output. A return from a superseded attempt fails
   `has_valid_return` and produces an empty receipt. (Tested.)
3. **No duplicates.** `dispatch_once()` looks for a live session tagged `job:<id>`
   before creating one, and creation itself uses `idempotent: true`. (Tested.)
4. **No fabricated receipts.** `build_receipt()` copies only fields the worker actually
   returned, and hard-codes `verification_state: "unverified"`. Conference — not the
   adapter, not Devin — is the only thing allowed to mark a job verified/DONE. (Tested.)
5. **Finished ≠ returned.** A session that ends with no structured output is terminal
   but *not* a valid return; Conference must reroute/fallback. (Tested.)
6. **Leases.** `poll(claim, lease_seconds=…)` marks a still-running attempt
   `lease_expired`, which sets `needs_reroute`. The adapter never kills the session —
   it only tells Conference to fence it off and reassign. (Tested.)
7. **Blocked ≠ failed.** `status_enum=blocked` sets `needs_human`, not `needs_reroute`:
   that is the Jarvis "surface a Michael gate" signal. `expired` sets `needs_reroute`.
   (Tested.)
8. **Transient errors.** 429/5xx retried with exponential backoff; 4xx surfaces
   immediately as `DevinApiError`. (Tested.)
9. **v1 or v3.** Pass `org_id` to use `POST /v3/organizations/{org_id}/sessions`;
   omit it for legacy `/v1/sessions`. (Tested.)
10. **Bounded polling.** `wait_for_return(claim, lease_seconds, poll_interval,
    timeout)` returns as soon as the session is terminal, the lease expires, or the
    worker is blocked on human input. A timeout returns the last observed state and
    never mutates the session, so a slow job stays reroutable instead of being lost.
    (Tested.)

### The return schema Devin is forced to fill
```
job_id, fence_token,
outcome        ∈ returned_complete | returned_partial | blocked | refused
summary        ≤2000 chars
evidence[]     {kind ∈ pull_request|commit|file|command_output|url, ref, note?}
tests[]        {name, result ∈ passed|failed|not_run, output_ref?}
blockers[]
next_action
```
Validated as a legal draft-7 schema, 1.1 KB (limit 64 KB).

### Chain this produces
```
Michael → Jarvis → Conference.dispatch(job, fence)
        → POST /v1/sessions (schema attached, tagged job:<id>)
        → Devin works
        → Conference polls GET /v1/sessions/{id} until status_enum=finished
        → structured_output validated + fence-checked
        → receipt persisted (verification_state=unverified)
        → Conference verification step runs
        → canonical state updated → Jarvis notifies Michael
```

---

## 4. Status board

| Area | State |
|---|---|
| Devin access inventory | **VERIFIED** |
| Devin API transport reachable | **VERIFIED** (403 without key — endpoint alive) |
| Devin worker return contract | **IMPLEMENTED + TESTED** (offline, fake transport) |
| Devin worker chain end-to-end | **PENDING** — needs a Devin API key + a running Conference |
| `command-workers` private repo | **BLOCKER — needs Michael** (Devin cannot create repos) |
| Floot source inspection | **BLOCKED for Devin** — no Floot access. Codex owns it. |
| Conference/Jarvis backend work | **PENDING source** from Codex |
| LIVE ACCEPTED anywhere | **No.** Nothing has run against live infrastructure. |

**BLOCKER:** no private repo, so there is nowhere safe to put recovered source or this
adapter.
**DIRECT SOLUTION:** Michael creates the private repo and grants Devin access (§5).
**WORKAROUND used today:** adapter built and tested locally, delivered as files, not
pushed anywhere.
**PARALLEL ATTACK:** contract + tests were finished while source recovery is still
in Codex's lane, so no waiting was wasted.

**RECEIPT** — this session: access inventory verified by command output; API shape
verified against docs.devin.ai; adapter 8/8 tests passing; 0 repos created; 0 money
spent; 0 secrets exposed; nothing pushed to `read-me`.

---

## 5. MICHAEL ACTION — one action

**SCREEN:** github.com, signed in as `sttmikeg-BJVI` → https://github.com/new
**FIELDS:**
- Repository name: `command-workers`
- Visibility: **Private** (must be Private)
- Leave "Add a README" unchecked
**BUTTON:** *Create repository*
**WHY:** Devin's GitHub App cannot create repositories (403, verified today), so this
one step must be yours. It gives Codex a private destination for the recovered Floot
source and gives Devin somewhere to commit Conference work — instead of the public
`read-me` repo, which must never hold this source.

After that, one follow-up action: grant Devin access to it at
`/settings/integrations/github` → add `command-workers` to the Devin GitHub App's
repository list.

Then, when you want the live worker chain: create a Devin API key at
`/settings/personal-access-tokens` (or Devin API → API Keys) and hand it to me as a
**secret**, never pasted into chat.
