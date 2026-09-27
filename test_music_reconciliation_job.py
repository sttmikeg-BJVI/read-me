"""The music reconciliation job must stay defined and unstarted."""

from __future__ import annotations

import time

from conference_store import ConferenceStore, connect_sqlite
from jarvis_contract import STATE_QUEUED
from music_reconciliation_job import reconciliation_submission


def test_the_job_cannot_start_without_michael():
    submission = reconciliation_submission()
    assert submission.michael_only_gate is True


def test_the_job_demands_inventory_and_a_move_log_before_completion():
    receipts = " ".join(reconciliation_submission().expected_receipts)
    assert "inventory" in receipts
    assert "move log" in receipts


def test_submitting_it_twice_yields_one_job():
    store = ConferenceStore(connect_sqlite())
    store.migrate()
    now = time.time()
    first = store.submit_intent(reconciliation_submission(), now)
    second = store.submit_intent(reconciliation_submission(), now + 60)
    assert first == second
    assert store.job_row(first)["state"] == STATE_QUEUED
