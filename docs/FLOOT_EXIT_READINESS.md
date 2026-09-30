# Floot exit readiness — 2026-09-30 UTC

Verdict: BLOCKED. Repository evidence does not demonstrate safe cancellation.
This document records public repository evidence only. Detailed private-provider
observations belong in the owner's local audit, not this public repository.

## Scope and immutable references

Only sttmikeg-BJVI/read-me was returned by both repository-list and affiliation-list
queries for the connected account. This does not establish that no other repository
exists. All four visible branch trees were inspected before writing this document.

| Branch | Inspected SHA | Result |
| --- | --- | --- |
| main | 997e6a189aff574b3da79f74bf2b6acbc86b1686 | README.md and .gitignore only |
| command-workers/cycle3-recovery | 9a5b839436d36b8915da8b59a2327dbc6d8bde08 | Conference/Jarvis preservation source |
| devin/1790181637-bjvi-handoff-layer | 3e2fd38585b9f06f9af51c513926fb95f4e565e8 | Handoff service, tests and CI |
| conference/worker-job-46fe093d0417 | 85b283d50d152e4182b7415128bb4f5f6aa82178 | Worker receipt |

## Existing work and collision boundary

[PR #2](https://github.com/sttmikeg-BJVI/read-me/pull/2) explicitly says
DO NOT MERGE — preservation. It owns the Conference/Jarvis runtime, persistence,
social backend and source migration tooling. No changes to those areas are made
here. [PR #1](https://github.com/sttmikeg-BJVI/read-me/pull/1) owns the
Monday/Slack handoff service. Neither is evidence of a completed production cutover.

The inspected [architecture map](https://github.com/sttmikeg-BJVI/read-me/blob/9a5b839436d36b8915da8b59a2327dbc6d8bde08/ARCHITECTURE_MAP.md)
reports Neon unproven and social adapters absent. Its statement that all workers
are simulated is superseded for one relay run by the newer
[live receipt](https://github.com/sttmikeg-BJVI/read-me/blob/9a5b839436d36b8915da8b59a2327dbc6d8bde08/LIVE_RUN_RECEIPT.md):
a real Devin worker completed through a file spool, using SQLite. That receipt
explicitly excludes Slack/Monday delivery, the Devin HTTP adapter and Postgres.
Do not overstate either document.

## Evidence-backed blockers and minimum closure evidence

| ID | Blocker | Minimum closure evidence |
| --- | --- | --- |
| B1 | No complete Floot application export in the inspected branch trees | Owner confirms canonical destination; preserve source and compare export/destination using the existing integrity tool |
| B2 | Neon production persistence unproven in the architecture map; live receipt uses SQLite | Backup/restore receipt, required schema and data validation, reconnect/restart evidence on the actual replacement database |
| B3 | No independent production build/deployment/cutover receipt found in inspected source | Reproducible build from preserved source, deployed commit, replacement service health and rollback evidence |
| B4 | No complete storage, authentication, scheduler, domain or integration migration receipt | Inventory retained features; validate each against replacement services without relying on Floot |
| B5 | PR #2 work belongs in its canonical repository and remains preservation-only | Coordinate destination and ownership with existing Devin work; do not merge PR #2 into this repository to simulate migration |
| B6 | Scope completeness unknown outside the connected repository | Confirm all production apps/repositories and explicitly retire or migrate each; unknown is not passed |

These are evidence gaps, not a claim that an uninspected service does not exist.
Render was not verified. The connector rejected the deployments endpoint and no
Render/Neon management connector was available. Do not infer deployment from CI.

## Reuse the existing check

[source_inventory.py](https://github.com/sttmikeg-BJVI/read-me/blob/9a5b839436d36b8915da8b59a2327dbc6d8bde08/source_inventory.py)
already implements content hashing and migration comparison. A second checker is
unnecessary and would overlap PR #2.

Run its scan/compare commands on the same platform against secret-free source
exports and the destination checkout, then retain the receipt privately. A passing
comparison establishes source integrity only: it does not prove database contents,
object storage, credentials, runtime compatibility or deployment readiness.
The checker permits added destination files; review these separately.

## Cancellation acceptance checklist

All items remain unverified until receipts are attached privately:

- [ ] Every retained app mapped to a canonical repository and deployed commit.
- [ ] Source export integrity verified with the existing checker.
- [ ] Database backup restored and schema/data checked in replacement infrastructure.
- [ ] Stored objects copied with checksum/access verification and URL references reconciled.
- [ ] Authentication, password recovery and encrypted integration tokens work after migration.
- [ ] Queued/recurring work transferred with one active scheduler and no duplicate execution.
- [ ] Domains, callback URLs, webhooks and mobile clients target the replacement.
- [ ] Retained critical flows pass against the replacement with Floot dependencies unavailable.
- [ ] Rollback, retention/export terms and all retired apps explicitly accounted for.

## Checks performed for this audit

- Repository listing through two routes: one visible repository.
- All four branch trees: complete responses (truncated=false).
- Open PR discovery: PRs #1 and #2; collision boundary recorded.
- Selected source/docs read at immutable branch heads, including existing integrity tool.
- [Existing CI run 35890462413](https://github.com/sttmikeg-BJVI/read-me/actions/runs/35890462413):
  completed/success at handoff SHA 3e2fd38585b9f06f9af51c513926fb95f4e565e8.
  This is an observed prior run, not a rerun or a migration-readiness pass.
- Local clone attempt failed because this environment lacks git remote-https;
  GitHub API reads were used instead.
- No application tests, live writes, paid jobs, deployments or migrations executed.

## Human action

Confirm the canonical private destination for retained Floot source and provide
access to any existing replacement repositories and Render/Neon deployment
receipts through secure channels. Coordinate the Conference/Jarvis lane with its
existing owner. Do not paste credential values into this document.
Cancellation is not approved by this audit.
