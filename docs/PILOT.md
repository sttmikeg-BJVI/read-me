# Runbook — BJVI-CREDIT-PILOT-001

Job: `BJVI-CREDIT-PILOT-001`
Monday item: `12990994496` (P0 — Credit Reconstruction Forensic Matrix)
Slack channel: `#claude-handoff`
Worker: `claude`

## Status of the acceptance test

Verified end-to-end against the real code path in
`tests/test_pilot_acceptance.py`: job creation → signed ACKNOWLEDGED → PROGRESS →
COMPLETE with artifact → Monday writeback payload → Slack receipt → dashboard row.
The Monday and Slack calls in that run go to test doubles.

**Not yet executed against live Monday/Slack.** Required credentials and
connections are listed below; nothing about the live run is simulated or assumed
to have happened.

## Credentials required to run it for real

| Value | Env var | Why |
| --- | --- | --- |
| Monday API token (write access to the board holding item 12990994496) | `MONDAY_API_TOKEN` | Writeback to the authoritative item |
| Monday status column id on that board | `MONDAY_STATUS_COLUMN_ID` | Defaults to `status`; confirm the real column id |
| Slack bot token (or incoming webhook) with post access to `#claude-handoff` | `SLACK_BOT_TOKEN` / `SLACK_WEBHOOK_URL` | Completion and blocked receipts |
| Shared secret issued to Claude | `BJVI_WORKER_SECRET_CLAUDE` | Signs Claude's callbacks |
| Operator token | `BJVI_OPERATOR_TOKEN` | Dashboard and job API |
| A reachable HTTPS host for this service | — | Claude/Zapier must be able to POST to `/api/worker-events` |

Claude also needs to be able to make an outbound HTTPS call, or the Zap fallback
in `ZAPIER.md` needs to be built.

## Live run, once credentials exist

```bash
set -a && . ./.env && set +a

python -m bjvi_handoff.cli create-job \
  --job-id BJVI-CREDIT-PILOT-001 \
  --title "P0 — Credit Reconstruction Forensic Matrix" \
  --worker claude --monday-item 12990994496 \
  --security-level RESTRICTED --approval-required

# hand to Claude: job_id, the endpoint URL, its shared secret, and the
# source pointer (Monday item) — not the source documents themselves.
```

Claude then posts, in order:

```
{"job_id":"BJVI-CREDIT-PILOT-001","worker":"claude","event":"ACKNOWLEDGED"}
{"job_id":"BJVI-CREDIT-PILOT-001","worker":"claude","event":"PROGRESS","message":"..."}
{"job_id":"BJVI-CREDIT-PILOT-001","worker":"claude","event":"COMPLETE",
 "artifact_reference":"<drive/doc pointer>","message":"<one-line summary>"}
```

Expected results:

- Monday item 12990994496 gets a status column update and an update post with
  status, worker, completion time, summary, artifact reference and approval
  requirement. No duplicate item is created.
- `#claude-handoff` receives:

  ```
  CLAUDE_COMPLETE
  JOB_ID: BJVI-CREDIT-PILOT-001
  STATUS: COMPLETE
  MONDAY_ITEM: 12990994496
  ARTIFACT: <reference>
  APPROVAL_REQUIRED: YES
  ```

- `python -m bjvi_handoff.cli show BJVI-CREDIT-PILOT-001` reports
  `receipt_reference` pointing at the Monday update.

If Claude blocks instead, `#claude-handoff` receives `CLAUDE_BLOCKED` with the
blocker line, and the dashboard shows the blocker.

## Verification by ChatGPT

Every terminal state is represented in Monday (item 12990994496) and Slack
(`#claude-handoff`), both of which ChatGPT already has connected. No completion
state lives only in this service.
