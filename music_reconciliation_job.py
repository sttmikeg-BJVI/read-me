"""The future AI Music Machine reconciliation job — defined, not executed.

Architecture, as locked by Michael:

    AI MUSIC MACHINE          the complete music platform/ecosystem
    └── WAR MACHINE           an engine/module inside it
        └── existing War Machine functionality and assets

Music files are currently scattered and duplicated across systems. Nothing is
moved today. This builds the Conference job that will do the reconciliation
once Conference is operational, so that when it runs it runs under fencing,
verification and a single canonical owner — which is the whole reason for not
letting a worker loose on these files now.

The job is `michael_only_gate=True`: it cannot start without Michael, because
its first destructive-looking step (moving files into the canonical tree) is
irreversible in the way a code change is not.

Submit it with:

    from music_reconciliation_job import reconciliation_submission
    store.submit_intent(reconciliation_submission(), time.time())
"""

from __future__ import annotations

from jarvis_contract import JobSubmission

OBJECTIVE = """\
Reconcile every music file in the estate into one canonical AI MUSIC MACHINE
tree, with WAR MACHINE as a module inside it, without losing or recreating any
existing work.

Order of operations is mandatory:

1. INVENTORY FIRST. Enumerate every location holding music assets, sessions,
   stems, renders, lyrics, prompts, model artifacts and project files. Produce
   the inventory as evidence before touching anything.
2. HASH AND COMPARE. Content-hash every file. Group by hash to find true
   duplicates; group by name/duration/metadata to find near-duplicates that are
   NOT the same file and must not be collapsed automatically.
3. IDENTIFY THE CANONICAL VERSION of each work: the latest genuine version,
   not merely the newest mtime. Where the canonical version is ambiguous,
   escalate the specific item rather than guessing.
4. PRESERVE HISTORY. Superseded versions are retained in the tree, marked as
   history. Nothing is deleted in this job.
5. MOVE, DO NOT RECREATE. Files are moved or hard-linked into place with their
   provenance recorded. Re-rendering, re-exporting or regenerating an asset is
   forbidden: a regenerated file is a new file wearing an old name.
6. ONE WRITER. While this job holds the fence, no other worker may write to
   the music estate. Any worker that finds itself needing to write reports a
   blocker instead.

Evidence required for completion: the inventory, the hash/duplicate report,
the canonical-version decisions with reasons, and the move log mapping every
source path to its destination path.
"""

EXPECTED_RECEIPTS = (
    "inventory of every music location and file",
    "content-hash report with duplicate and near-duplicate groups",
    "canonical-version decision list with reasons",
    "move log: source path -> destination path for every file moved",
    "list of items escalated instead of decided",
)


def reconciliation_submission(workstream: str = "ai-music-machine") -> JobSubmission:
    return JobSubmission(
        intent_id="ai-music-machine/reconciliation/v1",
        objective=OBJECTIVE,
        workstream=workstream,
        priority="P2",
        capability_required=("shell", "long_running"),
        michael_only_gate=True,
        expected_receipts=EXPECTED_RECEIPTS,
        context_refs=(
            {"kind": "note", "ref": "AI MUSIC MACHINE > WAR MACHINE > existing assets"},
        ),
    )
