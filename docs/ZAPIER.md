# Zapier bridge assessment

## Can Zapier safely relay Claude → BJVI?

Yes, but only in one direction and only as a transport. Zapier must not become a
second place where job state lives.

Constraints that drove the decision:

- Zapier task history stores every payload it passes. Anything routed through a
  Zap is readable by anyone with Zapier access, so **no credit-report content,
  source files, or credentials may transit a Zap** — only pointers (`job_id`,
  `artifact_reference`, Monday item id).
- Zapier cannot compute the HMAC signature in a no-code step. A Code step can,
  which means the worker secret would be stored in Zapier. That is one more copy
  of a credential.
- Zapier gives no ordering or idempotency guarantees; duplicate deliveries are
  normal. The service already dedupes on `event_id` and treats a repeated
  identical `COMPLETE` as a no-op, so replays are safe.

## Recommended (simplest reliable) shape

**Claude posts directly to `POST /api/worker-events` with a signed request.**
Monday writeback and the Slack receipt then happen inside this service, in one
transaction path, with receipts recorded. No Zap is required for the pilot — the
fewer relays, the fewer places a completion can silently die.

Use this unless Claude's runtime genuinely cannot make an outbound signed HTTP
call.

## Zap fallback (only if Claude cannot sign requests)

Minimum Zap — one Zap, three steps, no Monday/Slack logic inside Zapier:

1. **Trigger — Catch Hook** (`https://hooks.zapier.com/...`): Claude POSTs the
   worker-event JSON (same body as the API contract).
2. **Filter**: continue only if `job_id` is present, `event` is one of
   `ACKNOWLEDGED|PROGRESS|BLOCKED|COMPLETE|FAILED`, and `worker` is `claude`.
3. **Code by Zapier (Python)**: sign and forward to `POST /api/worker-events`.

```python
import hashlib, hmac, json, time, urllib.request

worker = "claude"
secret = input_data["worker_secret"]          # Zapier storage, never inline
payload = json.loads(input_data["raw_body"])
body = json.dumps(payload, separators=(",", ":")).encode()
stamp = str(int(time.time()))
signature = "v1=" + hmac.new(
    secret.encode(), b".".join([b"v1", worker.encode(), stamp.encode(), body]), hashlib.sha256
).hexdigest()

request = urllib.request.Request(
    input_data["endpoint"], data=body, method="POST",
    headers={
        "content-type": "application/json",
        "x-bjvi-worker": worker,
        "x-bjvi-timestamp": stamp,
        "x-bjvi-signature": signature,
    },
)
with urllib.request.urlopen(request, timeout=20) as response:
    output = {"status": response.status, "body": response.read().decode()[:500]}
```

Monday update and Slack receipt stay in the service, **not** in extra Zap steps:
duplicating them in Zapier would create two writers to the authoritative record
and two different receipt formats.

Optional second Zap: a scheduled trigger (every 15 min) calling
`POST /api/maintenance/sweep-stale` with the operator token, if no cron host is
available.

## What must not be built

- A Zap that writes job status straight into Monday from Claude's raw output —
  that bypasses the state machine and would let an unvalidated payload mark a job
  COMPLETE with no artifact.
- Storing job state in Zapier Tables or Google Sheets as a second source of truth.
