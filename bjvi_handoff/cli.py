"""Operator CLI: create jobs, sweep stale workers, inspect state."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .adapters import MondayClient, SlackClient
from .config import Settings
from .models import SourceSecurityLevel
from .service import HandoffService, JobNotFound
from .store import JobStore


def _service(settings: Settings) -> tuple[HandoffService, JobStore]:
    store = JobStore(settings.database_path)
    return (
        HandoffService(
            store=store,
            settings=settings,
            monday=MondayClient(settings),
            slack=SlackClient(settings),
        ),
        store,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bjvi-handoff")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-job", help="register a job for an AI worker")
    create.add_argument("--job-id", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--worker", required=True)
    create.add_argument("--monday-item", dest="source_reference")
    create.add_argument(
        "--security-level",
        default=SourceSecurityLevel.CONFIDENTIAL.value,
        choices=[level.value for level in SourceSecurityLevel],
    )
    create.add_argument("--approval-required", action="store_true")

    show = sub.add_parser("show", help="print a job with its events and receipts")
    show.add_argument("job_id")

    sub.add_parser("list", help="list all jobs")
    sub.add_parser("sweep-stale", help="flag RUNNING jobs whose worker went silent")
    sub.add_parser("retry-receipts", help="retry completion receipts that never landed")

    args = parser.parse_args(argv)
    settings = Settings.from_env(dict(os.environ))
    service, store = _service(settings)

    if args.command == "create-job":
        job = service.create_job(
            job_id=args.job_id,
            title=args.title,
            worker=args.worker,
            source_reference=args.source_reference,
            source_security_level=SourceSecurityLevel(args.security_level),
            approval_required=args.approval_required,
        )
        print(json.dumps(job.to_dict(), indent=2))
        return 0

    if args.command == "show":
        try:
            job = service.get_job(args.job_id)
        except JobNotFound:
            print(f"job {args.job_id} not found", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    **job.to_dict(),
                    "stale": service.is_stale(job),
                    "events": store.list_events(job.job_id),
                    "receipts": store.list_receipts(job.job_id),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "list":
        print(json.dumps([job.to_dict() for job in store.list_jobs()], indent=2))
        return 0

    if args.command == "sweep-stale":
        flagged = service.sweep_stale()
        print(json.dumps({"flagged": [job.job_id for job in flagged]}, indent=2))
        return 0

    if args.command == "retry-receipts":
        results = service.retry_undelivered()
        print(json.dumps([result.__dict__ for result in results], indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
