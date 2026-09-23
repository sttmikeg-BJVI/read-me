# BJVI AI Job Handoff + Notification Layer

One shared operating layer for work handed to external AI workers (Claude, Codex,
Devin, Floot agents, ChatGPT). Every job's final state lands in **Monday** (the
authoritative record) and **Slack** (the notification/receipt layer), so ChatGPT
can verify a worker's return state through its connected sources and Michael does
not carry messages between apps.

```
JOB CREATED → WORKER ASSIGNED → ACKNOWLEDGED → RUNNING → PROGRESS
           → COMPLETE | BLOCKED → MONDAY WRITEBACK → SLACK RECEIPT → REVIEW
```

## What is in here

| Path | Purpose |
| --- | --- |
| `bjvi_handoff/models.py` | Normalized job schema, status enum, allowed transitions, redaction |
| `bjvi_handoff/store.py` | SQLite store for jobs, worker events, and delivery receipts |
| `bjvi_handoff/security.py` | HMAC worker-identity verification for callbacks |
| `bjvi_handoff/service.py` | State machine, Monday writeback, Slack receipts, stale detection |
| `bjvi_handoff/adapters/` | Monday GraphQL and Slack clients |
| `bjvi_handoff/api.py` | `POST /api/worker-events`, operator job API, dashboard |
| `bjvi_handoff/dashboard.py` | Operator dashboard (job/worker/status/started/progress/blocker/output/approval) |
| `bjvi_handoff/client.py` | Reference signing client for workers and Zapier Code steps |
| `bjvi_handoff/cli.py` | `create-job`, `list`, `show`, `sweep-stale`, `retry-receipts` |
| `docs/ZAPIER.md` | Whether/how to use Zapier as the relay, and the simpler alternative |
| `docs/PILOT.md` | Runbook for `BJVI-CREDIT-PILOT-001` |

## Job schema

`job_id, title, worker, status, source_reference, source_system,
source_security_level, created_at, started_at, last_heartbeat_at, completed_at,
blocker, artifact_reference, approval_required, approval_status,
return_destination, receipt_reference` (plus `last_progress_message` and
`stale_flagged_at` for operator visibility).

Statuses: `QUEUED, ACKNOWLEDGED, RUNNING, BLOCKED, AWAITING_APPROVAL, COMPLETE, FAILED`.

Two rules are enforced in code, not by convention:

- A job is `RUNNING` only after a worker acknowledged it and reported progress —
  existing in the database is not enough.
- `COMPLETE` is rejected without an `artifact_reference`, and `receipt_reference`
  is only set when Monday or Slack actually accepted the write. A completion whose
  receipt never landed shows as COMPLETE with no receipt and is retryable via
  `retry-receipts`.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in real values, never commit it
set -a && . ./.env && set +a
.venv/bin/uvicorn bjvi_handoff.api:create_app --factory --port 8080
```

Operator dashboard: `GET /dashboard` with `Authorization: Bearer $BJVI_OPERATOR_TOKEN`.

Create a job:

```bash
.venv/bin/python -m bjvi_handoff.cli create-job \
  --job-id BJVI-CREDIT-PILOT-001 \
  --title "P0 — Credit Reconstruction Forensic Matrix" \
  --worker claude --monday-item 12990994496 \
  --security-level RESTRICTED --approval-required
```

## Worker callback contract

`POST /api/worker-events`

```json
{
  "job_id": "BJVI-CREDIT-PILOT-001",
  "worker": "claude",
  "event": "COMPLETE",
  "message": "forensic matrix complete",
  "artifact_reference": "gdrive:file/bjvi-credit-matrix-v1",
  "blocker": null,
  "timestamp": "2026-01-01T00:00:00Z",
  "metadata": {"rows": 42},
  "event_id": "optional-idempotency-key"
}
```

Events: `ACKNOWLEDGED`, `PROGRESS`, `BLOCKED`, `COMPLETE`, `FAILED`.

Headers (every request):

| Header | Value |
| --- | --- |
| `X-BJVI-Worker` | worker id, e.g. `claude` |
| `X-BJVI-Timestamp` | unix seconds, must be within 5 minutes |
| `X-BJVI-Signature` | `v1=` + HMAC-SHA256 of `v1.<worker>.<timestamp>.<raw body>` using that worker's shared secret |

Each worker's secret lives in `BJVI_WORKER_SECRET_<WORKER>` (e.g.
`BJVI_WORKER_SECRET_CLAUDE`). Unsigned, wrongly-signed, replayed, or
wrong-worker callbacks are rejected — an anonymous caller cannot complete a job.
`bjvi_handoff/client.py` is a copyable reference implementation of the signing.

Responses: `202` accepted / `duplicate_ignored` / `already_complete`,
`401` bad signature, `403` wrong worker, `404` unknown job, `409` illegal
transition or conflicting second completion, `422` `COMPLETE` without artifact.

## Heartbeat / stale jobs

Every accepted event updates `last_heartbeat_at`. `sweep-stale` (CLI or
`POST /api/maintenance/sweep-stale`) flags any `ACKNOWLEDGED`/`RUNNING`/
`AWAITING_APPROVAL` job silent for longer than `BJVI_STALE_THRESHOLD_SECONDS`
(default 30 min), posts a `*_STALE` Slack notice once, and marks it
`STALE / INVESTIGATE` on the dashboard. Run it from cron or a Zapier schedule.

## Security notes

- No credentials in code; everything is read from the environment.
- Worker text is redacted (SSNs, long account numbers, key/token patterns) before
  it is stored or relayed.
- Slack receipts and Monday updates carry pointers (`job_id`, `artifact_reference`,
  Monday item id) — never credit report contents or source files.
- The operator API and dashboard require `BJVI_OPERATOR_TOKEN`.

## Tests

```bash
.venv/bin/python -m pytest
```

Covers job creation, acknowledgement, progress, blocked, completion, artifact
receipt, Monday writeback, Slack notification, unauthorized/tampered/replayed
callback rejection, duplicate and conflicting completion, stale detection, and
the full `BJVI-CREDIT-PILOT-001` acceptance flow.
