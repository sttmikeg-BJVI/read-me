# Live run receipt — first real external-worker execution

One real external AI worker executed a Conference job end to end. Nothing in
this run was replayed or canned except the notification destination, which is
still a local sink because no Slack/Monday credential exists here.

## What ran

```
python live_acceptance.py --db /tmp/relay.sqlite3 --relay-spool /tmp/relay-spool \
  --poll-interval 10 --wait-timeout 5400 --objective "<worker task>"
```

| stage | label | detail |
| --- | --- | --- |
| persistence | LIVE | sqlite file, re-read from a separate process |
| notification sink | SIMULATED | signed HTTP to a local sink; Slack/Monday not configured |
| evidence checker | LIVE | real git |
| worker transport | LIVE | file spool relayed to a real Devin session |
| conference cycle | LIVE | `state=verified` |
| verification | LIVE | `verified` |
| surface status | LIVE | `DONE` |
| restart (new process) | LIVE | state unchanged |

## The job

- job: `job-46fe093d0417`
- fence: `job-46fe093d0417:a0`
- provider: `devin_relay` (spool transport, Devin executor)
- worker session: https://app.devin.ai/sessions/d14c69eb96f740e0800303aff4ce2105
- worker return: `returned_complete`, evidence
  `{"kind": "commit", "ref": "85b283d50d152e4182b7415128bb4f5f6aa82178"}`
- independent verification: the cited commit was resolved by git, not taken on
  the worker's word. `refs/heads/conference/worker-job-46fe093d0417` on
  `sttmikeg-BJVI/read-me` points at that sha.
- canonical state after verification: `verified` / `DONE`, one receipt.

The worker's first return was `blocked` — it could not find a checkout — and
Conference held the job short of `DONE` until a second, evidenced return
arrived. `RETURNED != VERIFIED != DONE` held in both directions.

## What this does not prove

- No Slack or Monday message was delivered.
- The transport is a file spool relayed by an operator, not a webhook, and the
  descriptor still reports `live_verified=False` for the Devin HTTP adapter,
  which remains unexercised for lack of `DEVIN_API_KEY`.
- Claude and Codex transports are still uninspected.
- Postgres was not used for this run; the sqlite file was.
