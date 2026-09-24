"""Canonical persistence for Conference job state.

The existing modules are pure logic: `jarvis_contract` defines the boundary,
`reroute_policy` decides, `conference_verification` judges, `receipt_ledger`
defines duplicate-return semantics, and `devin_worker_adapter` talks to a
provider. None of them hold state, so every guarantee they express disappeared
when the process exited. This is the store they all assumed: the single place
canonical identity, claims, leases, receipts and verification live.

It is not a second Conference and not a router. It stores and enforces; it
calls `reroute_policy.decide` for decisions and accepts verdicts produced by
`conference_verification`, and it refuses anything those modules forbid:

  * Jarvis cannot mint identity — `submit_intent` takes a `JobSubmission`,
    which structurally cannot carry one, and mints `job_id` here.
  * A worker cannot mark its own work done — the only path to `verified` is
    `apply_verdict` with a `Verdict` whose state is `verified`, and a job
    cannot reach it without a stored valid fenced return.
  * A late return from a superseded attempt is rejected by fence, and the same
    return observed twice records one receipt (unique index, not a Python set).
  * A dependency counts only when it is `verified`, checked against persisted
    state rather than in-memory hope.

Runs on stdlib `sqlite3` (offline, tests) and on Neon Postgres through any
DB-API 2.0 driver (`psycopg`); the only difference is the paramstyle and the
identifier for "now". No ORM, no migration framework, no new dependency.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from conference_verification import NEEDS_HUMAN, REJECTED, VERIFIED, Verdict
from jarvis_contract import (
    STATE_BLOCKED_HUMAN,
    STATE_CLAIMED,
    STATE_QUEUED,
    STATE_REJECTED,
    STATE_RETURNED_UNVERIFIED,
    STATE_VERIFIED,
    ContractViolation,
    JobSubmission,
    JobView,
)
from receipt_ledger import return_digest
from reroute_policy import Attempt, Decision, dependency_gate, decide, next_fence_token

SQLITE = "sqlite"
POSTGRES = "postgres"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        intent_id TEXT NOT NULL UNIQUE,
        objective TEXT NOT NULL,
        workstream TEXT NOT NULL,
        priority TEXT NOT NULL,
        state TEXT NOT NULL,
        verification_state TEXT NOT NULL DEFAULT 'unverified',
        assigned_worker TEXT,
        blocker TEXT,
        michael_only_gate INTEGER NOT NULL DEFAULT 0,
        notify_on_complete INTEGER NOT NULL DEFAULT 0,
        depends_on TEXT NOT NULL DEFAULT '[]',
        capability_required TEXT NOT NULL DEFAULT '[]',
        expected_receipts TEXT NOT NULL DEFAULT '[]',
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts (
        job_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL,
        fence_token TEXT NOT NULL,
        provider TEXT NOT NULL,
        session_id TEXT,
        session_url TEXT,
        outcome TEXT,
        claimed_at DOUBLE PRECISION NOT NULL,
        lease_expires_at DOUBLE PRECISION NOT NULL,
        closed_at DOUBLE PRECISION,
        PRIMARY KEY (job_id, attempt_no)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS receipts (
        job_id TEXT NOT NULL,
        fence_token TEXT NOT NULL,
        session_id TEXT NOT NULL,
        return_digest TEXT NOT NULL,
        return_valid INTEGER NOT NULL,
        outcome TEXT,
        payload TEXT NOT NULL,
        recorded_at DOUBLE PRECISION NOT NULL,
        verification_state TEXT NOT NULL DEFAULT 'unverified',
        verdict TEXT,
        UNIQUE (job_id, fence_token, session_id, return_digest)
    )
    """,
    "CREATE INDEX IF NOT EXISTS receipts_job_idx ON receipts (job_id)",
)


class StoreRejection(ValueError):
    """The store refused a write that would break a canonical guarantee."""


@dataclass(frozen=True)
class Claim:
    job_id: str
    attempt_no: int
    fence_token: str
    provider: str
    lease_expires_at: float


@dataclass(frozen=True)
class ReturnRecord:
    stored: bool
    reason: str
    fence_token: str


def connect_sqlite(path: str = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class ConferenceStore:
    def __init__(self, connection: Any, dialect: str = SQLITE) -> None:
        if dialect not in (SQLITE, POSTGRES):
            raise ValueError(f"unknown dialect: {dialect!r}")
        self._conn = connection
        self._dialect = dialect

    # --- plumbing ---------------------------------------------------------

    def _sql(self, statement: str) -> str:
        if self._dialect == POSTGRES:
            return statement.replace("?", "%s")
        return statement.replace("DOUBLE PRECISION", "REAL")

    def _execute(self, statement: str, params: Sequence[Any] = ()) -> Any:
        cursor = self._conn.cursor()
        cursor.execute(self._sql(statement), tuple(params))
        return cursor

    def _fetchone(self, statement: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        cursor = self._execute(statement, params)
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_dict(cursor, row)

    def _fetchall(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self._execute(statement, params)
        rows = cursor.fetchall()
        return [self._row_to_dict(cursor, row) for row in rows]

    @staticmethod
    def _row_to_dict(cursor: Any, row: Any) -> dict[str, Any]:
        if isinstance(row, sqlite3.Row):
            return dict(row)
        return {column[0]: value for column, value in zip(cursor.description, row)}

    def migrate(self) -> None:
        for statement in SCHEMA:
            self._execute(statement)
        self._conn.commit()

    # --- intake -----------------------------------------------------------

    def submit_intent(
        self, submission: JobSubmission, now: float, notify_on_complete: bool = False
    ) -> str:
        """Persist an intent and mint the canonical job id.

        Idempotent on `intent_id`: a retried submission returns the existing
        job rather than creating a second canonical record of the same work.
        """
        errors = submission.validate()
        if errors:
            raise ContractViolation("; ".join(errors))

        existing = self._fetchone("SELECT job_id FROM jobs WHERE intent_id = ?", (submission.intent_id,))
        if existing:
            return str(existing["job_id"])

        job_id = f"job-{uuid.uuid4().hex[:12]}"
        self._execute(
            """
            INSERT INTO jobs (job_id, intent_id, objective, workstream, priority, state,
                              michael_only_gate, notify_on_complete, depends_on,
                              capability_required, expected_receipts, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                submission.intent_id,
                submission.objective,
                submission.workstream,
                submission.priority,
                STATE_QUEUED,
                int(submission.michael_only_gate),
                int(notify_on_complete),
                json.dumps(list(submission.depends_on)),
                json.dumps(list(submission.capability_required)),
                json.dumps(list(submission.expected_receipts)),
                now,
                now,
            ),
        )
        self._conn.commit()
        return job_id

    # --- reads ------------------------------------------------------------

    def job_row(self, job_id: str) -> dict[str, Any]:
        row = self._fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            raise StoreRejection(f"unknown job: {job_id}")
        return row

    def job_states(self) -> dict[str, str]:
        return {row["job_id"]: row["state"] for row in self._fetchall("SELECT job_id, state FROM jobs")}

    def attempts(self, job_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM attempts WHERE job_id = ? ORDER BY attempt_no", (job_id,)
        )

    def active_attempt(self, job_id: str) -> dict[str, Any] | None:
        rows = [row for row in self.attempts(job_id) if row["closed_at"] is None]
        return rows[-1] if rows else None

    def receipts(self, job_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM receipts WHERE job_id = ? ORDER BY recorded_at", (job_id,)
        )

    def job_view(self, job_id: str) -> JobView:
        row = self.job_row(job_id)
        attempt = self.active_attempt(job_id)
        depends_on = tuple(json.loads(row["depends_on"]))
        _, unmet = dependency_gate({"depends_on": depends_on}, self.job_states())
        return JobView(
            job_id=job_id,
            state=row["state"],
            objective=row["objective"],
            workstream=row["workstream"],
            priority=row["priority"],
            assigned_worker=row["assigned_worker"],
            claim_session_url=attempt["session_url"] if attempt else None,
            lease_expires_at=attempt["lease_expires_at"] if attempt else None,
            blocker=row["blocker"],
            michael_only_gate=bool(row["michael_only_gate"]),
            receipts=tuple(json.loads(r["payload"]) for r in self.receipts(job_id)),
            verification_state=row["verification_state"],
            depends_on=depends_on,
            unmet_dependencies=unmet,
            notify_on_complete=bool(row["notify_on_complete"]),
        )

    # --- decisions --------------------------------------------------------

    def plan(self, job_id: str, eligible_providers: Sequence[str], max_attempts: int = 3) -> Decision:
        """Ask the existing policy what to do next, from persisted history."""
        row = self.job_row(job_id)
        attempts = tuple(
            Attempt(a["provider"], a["fence_token"], a["outcome"] or "no_valid_return")
            for a in self.attempts(job_id)
            if a["closed_at"] is not None
        )
        return decide(
            {"job_id": job_id, "depends_on": json.loads(row["depends_on"])},
            attempts,
            eligible_providers,
            self.job_states(),
            max_attempts=max_attempts,
        )

    # --- claim / lease ----------------------------------------------------

    def claim(
        self,
        job_id: str,
        provider: str,
        now: float,
        lease_seconds: float = 900.0,
        session_id: str | None = None,
        session_url: str | None = None,
    ) -> Claim:
        """Take the job for one attempt, minting the fence that guards it."""
        row = self.job_row(job_id)
        if row["state"] in (STATE_VERIFIED, STATE_BLOCKED_HUMAN):
            raise StoreRejection(f"{job_id} is {row['state']}; it cannot be claimed")

        ok, unmet = dependency_gate({"depends_on": json.loads(row["depends_on"])}, self.job_states())
        if not ok:
            raise StoreRejection(f"{job_id} has unverified dependencies: {list(unmet)}")

        active = self.active_attempt(job_id)
        if active is not None and active["lease_expires_at"] > now:
            raise StoreRejection(
                f"{job_id} is already claimed by {active['provider']} until {active['lease_expires_at']}"
            )
        if active is not None:
            self._close_attempt(
                job_id, int(active["attempt_no"]), active["outcome"] or "lease_expired", now
            )

        attempt_no = len(self.attempts(job_id))
        fence_token = next_fence_token(job_id, attempt_no)
        self._execute(
            """
            INSERT INTO attempts (job_id, attempt_no, fence_token, provider, session_id,
                                  session_url, claimed_at, lease_expires_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (job_id, attempt_no, fence_token, provider, session_id, session_url, now, now + lease_seconds),
        )
        self._set_job(job_id, state=STATE_CLAIMED, assigned_worker=provider, blocker=None, now=now)
        self._conn.commit()
        return Claim(job_id, attempt_no, fence_token, provider, now + lease_seconds)

    def attach_session(self, job_id: str, fence_token: str, session_id: str, session_url: str) -> None:
        self._require_current_fence(job_id, fence_token)
        self._execute(
            "UPDATE attempts SET session_id = ?, session_url = ? WHERE job_id = ? AND fence_token = ?",
            (session_id, session_url, job_id, fence_token),
        )
        self._conn.commit()

    def heartbeat(self, job_id: str, fence_token: str, now: float, lease_seconds: float = 900.0) -> float:
        """Extend the lease of the current attempt. A stale fence cannot."""
        attempt = self._require_current_fence(job_id, fence_token)
        if attempt["lease_expires_at"] <= now:
            raise StoreRejection(f"lease for {fence_token} already expired")
        expires = now + lease_seconds
        self._execute(
            "UPDATE attempts SET lease_expires_at = ? WHERE job_id = ? AND fence_token = ?",
            (expires, job_id, fence_token),
        )
        self._conn.commit()
        return expires

    def expire_leases(self, now: float) -> list[str]:
        """Release attempts whose worker went silent. Never marks work done."""
        expired: list[str] = []
        rows = self._fetchall(
            """
            SELECT job_id, attempt_no FROM attempts
            WHERE closed_at IS NULL AND outcome IS NULL AND lease_expires_at <= ?
            """,
            (now,),
        )
        for row in rows:
            job_id = str(row["job_id"])
            self._close_attempt(job_id, int(row["attempt_no"]), "lease_expired", now)
            job = self.job_row(job_id)
            if job["state"] in (STATE_CLAIMED, STATE_QUEUED):
                self._set_job(job_id, state=STATE_QUEUED, assigned_worker=None, now=now)
            expired.append(job_id)
        self._conn.commit()
        return expired

    # --- returns ----------------------------------------------------------

    def record_return(self, receipt: Mapping[str, Any], now: float) -> ReturnRecord:
        """Persist a worker return as a receipt.

        A receipt is always `unverified` here — this method has no path to
        `verified`, by construction.
        """
        job_id = str(receipt.get("job_id", ""))
        fence_token = str(receipt.get("fence_token", ""))
        attempt = self.active_attempt(job_id)
        if attempt is None or attempt["fence_token"] != fence_token:
            current = attempt["fence_token"] if attempt else None
            raise StoreRejection(
                f"stale return for {job_id}: fence {fence_token!r} is not the current fence {current!r}"
            )

        digest = return_digest(receipt)
        duplicate = self._fetchone(
            """
            SELECT 1 AS hit FROM receipts
            WHERE job_id = ? AND fence_token = ? AND session_id = ? AND return_digest = ?
            """,
            (job_id, fence_token, str(receipt.get("session_id", "")), digest),
        )
        if duplicate:
            return ReturnRecord(False, "duplicate return already recorded", fence_token)

        valid = bool(receipt.get("return_valid"))
        self._execute(
            """
            INSERT INTO receipts (job_id, fence_token, session_id, return_digest, return_valid,
                                  outcome, payload, recorded_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                fence_token,
                str(receipt.get("session_id", "")),
                digest,
                int(valid),
                receipt.get("outcome"),
                json.dumps(dict(receipt), default=str),
                now,
            ),
        )
        if valid:
            # The attempt stays open until a verdict exists: it is neither
            # rerouteable nor finished while verification is outstanding, and
            # keeping it current is what makes a re-observed return a
            # duplicate rather than a stale-fence error.
            self._mark_attempt_outcome(job_id, int(attempt["attempt_no"]), "returned_valid")
            self._set_job(job_id, state=STATE_RETURNED_UNVERIFIED, now=now)
        else:
            self._close_attempt(job_id, int(attempt["attempt_no"]), "no_valid_return", now)
        self._conn.commit()
        return ReturnRecord(True, "recorded", fence_token)

    def record_blocked(self, job_id: str, fence_token: str, blocker: str, now: float) -> None:
        attempt = self._require_current_fence(job_id, fence_token)
        self._close_attempt(job_id, int(attempt["attempt_no"]), "blocked", now)
        self._set_job(job_id, state=STATE_BLOCKED_HUMAN, blocker=blocker, now=now)
        self._conn.commit()

    # --- verification -----------------------------------------------------

    def apply_verdict(self, verdict: Verdict, now: float) -> str:
        """The only route to `verified`, and only for a stored valid return."""
        job_id, fence_token = verdict.job_id, verdict.fence_token
        receipt = self._fetchone(
            """
            SELECT * FROM receipts
            WHERE job_id = ? AND fence_token = ? AND return_valid = 1
            ORDER BY recorded_at DESC
            """,
            (job_id, fence_token),
        )
        if receipt is None:
            raise StoreRejection(
                f"no valid fenced return stored for {job_id}/{fence_token}; nothing to verify"
            )

        self._execute(
            """
            UPDATE receipts SET verification_state = ?, verdict = ?
            WHERE job_id = ? AND fence_token = ? AND return_digest = ?
            """,
            (
                verdict.verification_state,
                json.dumps(verdict.as_dict()),
                job_id,
                fence_token,
                receipt["return_digest"],
            ),
        )

        attempt = self.active_attempt(job_id)
        if attempt is not None and attempt["fence_token"] == fence_token:
            closing_outcome = (
                "no_valid_return" if verdict.verification_state == REJECTED else "returned_valid"
            )
            self._close_attempt(job_id, int(attempt["attempt_no"]), closing_outcome, now)

        if verdict.verification_state == VERIFIED:
            state = STATE_VERIFIED
            blocker = None
        elif verdict.verification_state == NEEDS_HUMAN:
            state = STATE_BLOCKED_HUMAN
            blocker = "; ".join(verdict.reasons) or "verification needs a human"
        elif verdict.verification_state == REJECTED:
            state = STATE_REJECTED
            blocker = None
        else:
            raise StoreRejection(f"unknown verification state: {verdict.verification_state!r}")

        self._set_job(job_id, state=state, verification_state=verdict.verification_state,
                      blocker=blocker, now=now)
        self._conn.commit()
        return state

    # --- internals --------------------------------------------------------

    def _require_current_fence(self, job_id: str, fence_token: str) -> dict[str, Any]:
        attempt = self.active_attempt(job_id)
        if attempt is None or attempt["fence_token"] != fence_token:
            raise StoreRejection(f"{fence_token!r} is not the current fence for {job_id}")
        return attempt

    def _mark_attempt_outcome(self, job_id: str, attempt_no: int, outcome: str) -> None:
        self._execute(
            "UPDATE attempts SET outcome = ? WHERE job_id = ? AND attempt_no = ?",
            (outcome, job_id, attempt_no),
        )

    def _close_attempt(self, job_id: str, attempt_no: int, outcome: str, now: float) -> None:
        self._execute(
            "UPDATE attempts SET outcome = ?, closed_at = ? WHERE job_id = ? AND attempt_no = ?",
            (outcome, now, job_id, attempt_no),
        )

    def _set_job(self, job_id: str, now: float, **fields: Any) -> None:
        columns: list[str] = []
        values: list[Any] = []
        for key, value in fields.items():
            columns.append(f"{key} = ?")
            values.append(value)
        columns.append("updated_at = ?")
        values.extend([now, job_id])
        self._execute(f"UPDATE jobs SET {', '.join(columns)} WHERE job_id = ?", values)


def open_store(dsn: str | None = None, *, sqlite_path: str = ":memory:") -> ConferenceStore:
    """Open the canonical store.

    With a Neon (or any Postgres) DSN this uses `psycopg`; without one it falls
    back to SQLite so the same code path is exercised offline. No DSN is
    configured in this project yet, so nothing here has run against Neon — that
    is a credential gate, not a code gap.
    """
    if dsn:
        import psycopg  # imported lazily: not installed in the offline path

        return ConferenceStore(psycopg.connect(dsn), POSTGRES)
    return ConferenceStore(connect_sqlite(sqlite_path), SQLITE)


def iter_unverified(store: ConferenceStore, job_ids: Iterable[str]) -> list[str]:
    """Jobs that returned but have not been independently verified yet."""
    return [
        job_id
        for job_id in job_ids
        if store.job_row(job_id)["state"] == STATE_RETURNED_UNVERIFIED
    ]
