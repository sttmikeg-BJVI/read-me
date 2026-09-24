# Engineering intake template + source-arrival protocol

Purpose: let Conference hand a worker a new product or workstream without
Michael re-explaining the architecture every time. This template is the human/
Jarvis-readable form of the `JobSubmission` contract in `jarvis_contract.py`;
the field names line up deliberately so an intake can be submitted
programmatically later without rewriting it.

---

## A. Intake template (fill one per workstream, not per task)

```
INTAKE
  intent_id:            <uuid — idempotency key, reused = no duplicate job>
  product/workstream:   <conference | jarvis | bjvi_os | social_intelligence |
                         music_war_machine | caribra_connect | media_production>
  objective:            <one sentence, outcome not activity>
  priority:             P0 | P1 | P2 | P3
  existing or new:      EXISTING (recover/complete) | NEW (requires approval)
  source location:      <repo/path/export, or NOT YET RECOVERED>
  source recovered?:    yes/no + file count + version id
  depends_on:           <job ids or "source arrival">
  capability_required:  code | github_pr | shell | browser | research | long_running
  michael_only_gate:    <the one thing only Michael can do, or none>
  expected_receipts:    <pull_request | commit | tests | command_output | url>
  acceptance criteria:  <specific, checkable statements — see section D>
  explicitly out of scope: <what NOT to build, to prevent duplicate systems>
  customer zero:        <who uses it first>
```

Rules for whoever fills this in:
- "EXISTING" means inventory before modifying. No rebuild without the source.
- If `source recovered? = no`, the only legal work is recovery intake and an
  acceptance plan. Implementation jobs must depend on source arrival.
- Acceptance criteria with no checkable statement produce no percentage claims.
  A job with vague criteria gets `clarification_required`, not a guess.

---

## B. Source-arrival protocol (run in this order, every time)

1. **Inventory before touching anything** — file count, entry points, existing
   tests, dependency manifests, migrations. Record the counts as evidence.
2. **Run the existing tests first**, before any edit. A failure that predates
   the work must be recorded as pre-existing, never attributed to the change.
3. **Map, do not duplicate** — for each prepared contract, find the existing
   module that already owns that concern and adopt its names. The four
   questions in `JARVIS_CONFERENCE_CONTRACT.md` §8 are the checklist.
4. **Preserve working code.** Integrate minimally; no opportunistic refactors.
5. **Typecheck and test** with the repo's own commands, not invented ones.
6. **Branch** off the default branch; never commit to it directly.
7. **Commit** in reviewable units.
8. **PR** with the mapping described: what existed, what was added, what was
   deliberately not touched.
9. **Receipt**: job_id, fence_token, outcome, evidence, tests, blockers,
   next_action — `verification_state: unverified` until Conference verifies.

---

## C. Product intakes prepared (recovery-only until source arrives)

### C1. Unified AI Music Creation System / WAR MACHINE — ONE product
Status: **NOT RECOVERED. No implementation work is legal yet.**

WAR MACHINE is not a separate product and must not be split out again. Capture,
lyrics, beats/audio assets, artist workspace, generation, revision, retrieval,
history, search, export, persistence, restart/recovery, artist separation, and
QA receipts all belong to the same system.

Recovery intake needed before any engineering:
- the ~629-file source inventory, with a version id, exactly as Codex recovered
  Command Workers;
- which persistence layer it currently assumes, and whether it duplicates the
  canonical store (if so, it consumes Conference state, it does not fork it);
- artist separation model — how Michael's workspace and SMOKES' workspace are
  isolated today (tenant key? per-artist namespace? nothing yet?);
- what "restart/recovery" currently means: resumable generation jobs, or just
  saved drafts;
- existing QA receipts format, so it maps onto the receipt contract rather than
  inventing a second one.

Customer zero: Michael George. Customer one: SMOKES / Michael McKinley.
Out of scope: any new music product, any second audio pipeline.

### C2. Caribra Connect — existing, do not rebuild
Engineering lane once source is available: tenant isolation, ownership
registry, call persistence, readback, handoff receipts, portable safeguards.
Live Retell/provider setup is a separate lane and is not Devin's.
Out of scope: merging it into Command Workers.

### C3. BJVI Operating System — existing component
Read-only consumer of canonical Conference state. Out of scope: any write path,
any local copy of truth.

### C4. Social Intelligence — existing, substantially implemented
Out of scope: rebuilding retrieval/research/classification. Legal work is
routing its actionable findings into Conference as intents.

### C5. Media Production Engine / Small Business Machine
Inventory before modification. No intake beyond inventory until the file list
and entry points are known.

---

## D. Acceptance criteria that are allowed to be called "done"

A criterion is only acceptable if a specific check can pass or fail:

| Not acceptable | Acceptable |
|---|---|
| "Conference works" | "A job dispatched through Conference reaches `verified` and its receipt cites a PR that exists" |
| "Music system is 80% complete" | "An artist workspace persists a revision and retrieves it after a process restart" |
| "Jarvis is connected" | "A Jarvis submission with a duplicate intent_id creates exactly one job" |

Percentages are not reported without criteria like the right-hand column.

---

## E. Evidence labels (used in every return)

`SOURCE-VERIFIED` (the code says so) · `TEST-VERIFIED` (a test proved it,
name the test) · `LIVE-VERIFIED` (it ran against the real system, cite the
run) · `EXTERNALLY-VERIFIED` (a third party or official doc confirms it) ·
`UNVERIFIED`.

Source existence is never live acceptance. A mock transport is never a provider
integration. An assignment is never completed work.
