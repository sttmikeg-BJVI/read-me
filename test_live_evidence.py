"""Tests for the real evidence checker.

Two groups. The offline ones pin the judgement rules and run anywhere. The
ones marked `live` do real network and real git work and are skipped unless
`LIVE_EVIDENCE_TESTS=1`, so nothing here can pass by pretending to have
reached the network.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from conference_verification import FAILED, NOT_RUN, PASSED
from live_evidence import LiveEvidenceChecker

live = pytest.mark.skipif(
    os.environ.get("LIVE_EVIDENCE_TESTS") != "1",
    reason="set LIVE_EVIDENCE_TESTS=1 to run checks that use the real network",
)


@pytest.fixture()
def workspace(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "kept.txt").write_text("content\n")
    (tmp_path / "empty.txt").write_text("")
    return tmp_path


def test_an_unreadable_pull_request_is_unknown_not_verified():
    checker = LiveEvidenceChecker(github_token=None)
    outcome = checker.check_pull_request("https://github.com/o/r/pull/1")
    assert outcome.result == NOT_RUN
    assert "GITHUB_TOKEN" in outcome.detail


def test_a_non_pull_request_reference_fails():
    assert LiveEvidenceChecker().check_pull_request("see my session").result == FAILED


def test_a_worker_may_not_choose_the_command_that_verifies_it(workspace):
    checker = LiveEvidenceChecker(workspace=workspace, allowed_commands=("pytest -q",))
    assert checker.check_command_output("echo 'all tests pass'").result == NOT_RUN


def test_an_allowlisted_command_is_actually_run(workspace):
    checker = LiveEvidenceChecker(workspace=workspace, allowed_commands=("true", "false"))
    assert checker.check_command_output("true").result == PASSED
    assert checker.check_command_output("false").result == FAILED


def test_file_evidence_must_exist_be_non_empty_and_stay_in_the_workspace(workspace):
    checker = LiveEvidenceChecker(workspace=workspace)
    assert checker.check_file("kept.txt").result == PASSED
    assert checker.check_file("empty.txt").result == FAILED
    assert checker.check_file("missing.txt").result == FAILED
    assert checker.check_file("../escape.txt").result == FAILED
    assert checker.check_file("/etc/passwd").result == FAILED


def test_commit_evidence_is_checked_against_real_git(workspace):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x", "--allow-empty"],
        cwd=workspace,
        check=True,
    )
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True, check=True
    ).stdout.strip()
    checker = LiveEvidenceChecker(workspace=workspace)
    assert checker.check_commit(sha).result == PASSED
    assert checker.check_commit("0" * 40).result == FAILED
    assert checker.check_commit("not-a-sha").result == FAILED


def test_a_checker_with_no_workspace_says_unknown_rather_than_passing():
    checker = LiveEvidenceChecker()
    assert checker.check_commit("a" * 40).result == NOT_RUN
    assert checker.check_file("README.md").result == NOT_RUN


def test_a_non_http_url_fails():
    assert LiveEvidenceChecker().check_url("ftp://example.com/x").result == FAILED


@live
def test_live_a_real_url_passes_and_a_real_404_fails():
    checker = LiveEvidenceChecker()
    assert checker.check_url("https://example.com").result == PASSED
    assert checker.check_url("https://example.com/definitely-not-here-4f2a").result == FAILED


@live
def test_live_an_unreachable_host_is_unknown_not_failed():
    checker = LiveEvidenceChecker(timeout=5.0)
    assert checker.check_url("https://this-host-does-not-exist-4f2a.invalid").result == NOT_RUN


@live
@pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="needs a GitHub token")
def test_live_an_open_pull_request_is_not_evidence_of_completion():
    checker = LiveEvidenceChecker()
    outcome = checker.check_pull_request("https://github.com/sttmikeg-BJVI/read-me/pull/2")
    assert outcome.result == FAILED
    assert "open" in outcome.detail
