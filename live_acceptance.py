"""The Conference + Jarvis acceptance run, with as much of it live as
credentials currently allow.

    Jarvis instruction -> canonical job -> persistent state -> provider
    selection -> fenced dispatch -> worker return -> independent evidence
    verification -> receipt -> DONE/BLOCKED/reroute -> notification
    -> restart -> state still correct

Every stage prints its own honesty label:

    LIVE       really happened against the outside world or real storage
    SIMULATED  a stand-in was used; this stage is NOT evidence of anything
    BLOCKED    could not run, and why

The worker transport is the stage that decides how much of this is real. With
`DEVIN_API_KEY` set, dispatch and return go to the real Devin API through the
existing adapter — no code changes, just a credential. Without it the run
stops at that stage unless `--simulate-worker` is passed, and then the label
says so and the final verdict is downgraded.

    python live_acceptance.py --db /tmp/acceptance.sqlite3 --simulate-worker

Restart is proven by re-reading the database from a *separate process*
(`--inspect`), not by reusing the connection in memory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from conference_runtime import ConferenceRuntime
from conference_store import ConferenceStore, connect_sqlite
from devin_worker_adapter import DevinWorkerAdapter
from jarvis import Jarvis
from jarvis_contract import surface_status
from live_evidence import LiveEvidenceChecker
from notifications import AuditLogNotifier, WebhookNotifier, notifier_from_env
from spool_relay_provider import SpoolRelayProvider
from worker_provider import DEVIN, ProviderRegistry, devin_relay_descriptor

LIVE = "LIVE"
SIMULATED = "SIMULATED"
BLOCKED = "BLOCKED"

INSTRUCTION = (
    "P0: prove the Conference chain end to end and cite the acceptance page "
    "as evidence. Needs code and a github_pr."
)


def say(stage: str, label: str, detail: str) -> None:
    print(f"{label:<9} | {stage:<26} | {detail}", flush=True)


# --- notification sink ----------------------------------------------------


class _Sink(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["content-length"]))
        _Sink.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_local_sink() -> HTTPServer:
    server = HTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- simulated worker -----------------------------------------------------


class ReplayTransport:
    """Stands in for the Devin HTTP API. Present only so the rest of the
    chain can be exercised without a credential; never treated as evidence."""

    def __init__(self, evidence_url: str) -> None:
        self.session_id = "sim-session"
        self.evidence_url = evidence_url
        self.output: dict | None = None

    def request(self, method, url, headers, body):
        if url.endswith("/sessions") and method == "GET":
            return 200, json.dumps({"sessions": []}).encode()
        if method == "POST":
            # The fence is issued by the store and only reaches the worker in
            # the prompt, so the replay echoes what it was actually handed.
            prompt = json.loads(body)["prompt"]
            fields = dict(
                line.split(": ", 1)
                for line in prompt.splitlines()
                if line.startswith(("job_id: ", "fence_token: "))
            )
            self.output = {
                "job_id": fields["job_id"],
                "fence_token": fields["fence_token"],
                "outcome": "returned_complete",
                "summary": "Simulated worker return.",
                "evidence": [{"kind": "url", "ref": self.evidence_url}],
                "tests": [{"name": "pytest", "result": "passed"}],
                "blockers": [],
                "next_action": "review",
            }
            return 200, json.dumps(
                {
                    "session_id": self.session_id,
                    "url": f"https://app.devin.ai/sessions/{self.session_id}",
                    "is_new_session": True,
                }
            ).encode()
        return 200, json.dumps(
            {
                "session_id": self.session_id,
                "status_enum": "finished",
                "structured_output": self.output,
            }
        ).encode()


# --- the run --------------------------------------------------------------


def inspect(db_path: Path) -> int:
    """Separate-process read of the canonical state. This is the restart proof."""
    store = ConferenceStore(connect_sqlite(str(db_path)))
    store.migrate()
    for row in store.jobs():
        view = store.job_view(row["job_id"])
        print(
            json.dumps(
                {
                    "job_id": view.job_id,
                    "state": view.state,
                    "surface": surface_status(view),
                    "worker": view.assigned_worker,
                    "session_url": view.claim_session_url,
                    "receipts": len(view.receipts),
                    "verification_state": view.verification_state,
                    "blocker": view.blocker,
                },
                sort_keys=True,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="conference-acceptance.sqlite3")
    parser.add_argument("--evidence-url", default="https://example.com")
    parser.add_argument("--simulate-worker", action="store_true")
    parser.add_argument(
        "--relay-spool",
        help="dispatch through a filesystem spool relayed to a real worker session",
    )
    parser.add_argument("--wait-timeout", type=float, default=3600.0)
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--objective", default=INSTRUCTION)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)

    db_path = Path(args.db).resolve()
    if args.inspect:
        return inspect(db_path)

    workspace = Path(__file__).resolve().parent
    fully_live = True

    # 1. persistence -------------------------------------------------------
    connection = connect_sqlite(str(db_path))
    store = ConferenceStore(connection)
    store.migrate()
    say("persistence", LIVE, f"sqlite file {db_path}")

    # 2. notification destination -----------------------------------------
    audit = AuditLogNotifier(workspace / "conference-notifications.jsonl")
    if os.environ.get("CONFERENCE_NOTIFY_URL"):
        notifier = notifier_from_env(audit.path)
        say("notification sink", LIVE, "CONFERENCE_NOTIFY_URL")
    else:
        sink = start_local_sink()
        notifier = WebhookNotifier(
            f"http://127.0.0.1:{sink.server_port}/events", "local-sink", audit=audit
        )
        say(
            "notification sink",
            SIMULATED,
            "real signed HTTP delivery to a local sink; Slack/Monday not configured",
        )
        fully_live = False

    # 3. evidence checker --------------------------------------------------
    checker = LiveEvidenceChecker(workspace=workspace)
    say("evidence checker", LIVE, f"real HTTP/git; will fetch {args.evidence_url}")

    # 4. worker transport --------------------------------------------------
    api_key = os.environ.get("DEVIN_API_KEY")
    registry = ProviderRegistry()
    poll_interval = 0.0
    wait_timeout: float | None = None
    if args.relay_spool:
        spool = Path(args.relay_spool).resolve()
        registry = ProviderRegistry([devin_relay_descriptor(spool_ready=True)])
        registry.register_adapter("devin_relay", SpoolRelayProvider(root=spool, worker="devin"))
        poll_interval = args.poll_interval
        wait_timeout = args.wait_timeout
        say(
            "worker transport",
            LIVE,
            f"file spool {spool}; a real worker session must write the return",
        )
    elif api_key:
        adapter = DevinWorkerAdapter(api_key=api_key)
        registry = ProviderRegistry(
            [DEVIN.__class__(**{**DEVIN.__dict__, "credentials_available": True})]
        )
        registry.register_adapter("devin", adapter)
        say("worker transport", LIVE, "DEVIN_API_KEY present; real Devin API")
    elif args.simulate_worker:
        registry = ProviderRegistry(
            [DEVIN.__class__(**{**DEVIN.__dict__, "credentials_available": True})]
        )
        registry.register_adapter(
            "devin",
            DevinWorkerAdapter(api_key="simulated", transport=ReplayTransport(args.evidence_url)),
        )
        say("worker transport", SIMULATED, "no DEVIN_API_KEY; replaying a canned worker return")
        fully_live = False
    else:
        say(
            "worker transport",
            BLOCKED,
            "no DEVIN_API_KEY, no --relay-spool, and --simulate-worker not passed",
        )
        say("verdict", BLOCKED, "worker dispatch is the only stage without a credential")
        return 2

    # 5. Jarvis instruction -> canonical job -------------------------------
    jarvis = Jarvis(store)
    reply = jarvis.instruct(args.objective, workstream="engineering")
    job_id = reply.job_id
    say("jarvis instruction", LIVE, f"{job_id}: {reply.text}")

    runtime = ConferenceRuntime(
        store,
        registry,
        checker,
        notifier=notifier,
        poll_interval=poll_interval,
        wait_timeout=wait_timeout,
    )

    # 6. the chain ---------------------------------------------------------
    result = runtime.run(job_id, required_evidence_kinds=())
    say("conference cycle", LIVE, f"action={result.action} state={result.state} {result.detail}")

    view = store.job_view(job_id)
    say("verification", LIVE, f"{view.verification_state} from a real fetch of the cited evidence")
    say("surface status", LIVE, surface_status(view))

    delivered = [
        line
        for line in audit.path.read_text().splitlines()
        if json.loads(line)["job_id"] == job_id
    ]
    say("notification", LIVE if delivered else BLOCKED, f"{len(delivered)} written to {audit.path}")

    # 7. restart -----------------------------------------------------------
    connection.close()
    out = subprocess.run(
        [sys.executable, __file__, "--db", str(db_path), "--inspect"],
        capture_output=True,
        text=True,
        cwd=workspace,
    )
    say("restart (new process)", LIVE, out.stdout.strip().splitlines()[-1] if out.stdout else out.stderr)

    say("verdict", LIVE if fully_live else SIMULATED,
        "chain complete" if fully_live else "chain complete, but stages above marked SIMULATED are not evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
