"""Operator dashboard: one table, the columns Michael reviews."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

STYLE = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #14213d; }
h1 { font-size: 1.25rem; margin-bottom: 0.25rem; }
p.meta { color: #5b6478; margin-top: 0; font-size: 0.85rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { border-bottom: 1px solid #e2e6ee; padding: 0.55rem 0.6rem; text-align: left; }
td { vertical-align: top; }
th { background: #f6f8fc; font-weight: 600; text-transform: uppercase; }
th { font-size: 0.7rem; letter-spacing: 0.04em; }
.status { font-weight: 600; }
.QUEUED { color: #5b6478; }
.ACKNOWLEDGED, .RUNNING { color: #0b6bcb; }
.BLOCKED, .FAILED { color: #b3261e; }
.AWAITING_APPROVAL { color: #9a6700; }
.COMPLETE { color: #1a7f37; }
.stale { background: #fff4e5; }
.pill { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 0.25rem; }
.pill { background: #fde2e1; color: #b3261e; font-size: 0.7rem; }
.empty { color: #5b6478; font-style: italic; }
"""

COLUMNS = [
    "JOB",
    "WORKER",
    "STATUS",
    "STARTED",
    "LAST PROGRESS",
    "BLOCKER",
    "OUTPUT",
    "APPROVAL",
]


def _cell(value: Any) -> str:
    if value in (None, "", []):
        return '<span class="empty">—</span>'
    return escape(str(value))


def render_dashboard(jobs: list[dict[str, Any]], now: datetime) -> str:
    rows = []
    for job in jobs:
        status = escape(str(job["status"]))
        stale = job.get("stale") and status not in {"COMPLETE", "FAILED"}
        status_cell = f'<span class="status {status}">{status}</span>'
        if stale:
            status_cell += ' <span class="pill">STALE / INVESTIGATE</span>'
        approval = (
            f"{job['approval_status']}" if job["approval_required"] else "NOT REQUIRED"
        )
        rows.append(
            "<tr class='{cls}'>"
            "<td><strong>{job_id}</strong><br>{title}</td>"
            "<td>{worker}</td>"
            "<td>{status}</td>"
            "<td>{started}</td>"
            "<td>{heartbeat}<br>{message}</td>"
            "<td>{blocker}</td>"
            "<td>{artifact}<br>{receipt}</td>"
            "<td>{approval}</td>"
            "</tr>".format(
                cls="stale" if stale else "",
                job_id=_cell(job["job_id"]),
                title=_cell(job["title"]),
                worker=_cell(job["worker"]),
                status=status_cell,
                started=_cell(job["started_at"]),
                heartbeat=_cell(job["last_heartbeat_at"]),
                message=_cell(job["last_progress_message"]),
                blocker=_cell(job["blocker"]),
                artifact=_cell(job["artifact_reference"]),
                receipt=_cell(job["receipt_reference"]),
                approval=_cell(approval),
            )
        )
    body = "".join(rows) or (
        f'<tr><td colspan="{len(COLUMNS)}" class="empty">No jobs yet.</td></tr>'
    )
    headers = "".join(f"<th>{column}</th>" for column in COLUMNS)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>BJVI AI Job Handoff</title>"
        f"<style>{STYLE}</style></head><body>"
        "<h1>BJVI AI Job Handoff</h1>"
        "<p class='meta'>Monday is the authoritative record. "
        f"Generated {escape(now.isoformat())}.</p>"
        f"<table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table>"
        "</body></html>"
    )
