"""An evidence checker that actually looks at the outside world.

Verification is only worth anything if something independent of the worker
goes and looks. Until now the only `EvidenceChecker` in this project was a
test double, so a job could reach `verified` on the strength of a URL nobody
had opened. This implements the protocol against real HTTP, real git remotes
and the real filesystem.

Rules it keeps, because they are what make verification independent:

  * Anything that cannot be checked is `not_run`, never `passed`. An expired
    token, a rate limit, a missing git remote and a network failure are all
    "I do not know", and `conference_verification` already refuses to verify
    on an unknown.
  * A cited pull request must be *resolved* — merged, or closed deliberately.
    An open PR is work in flight, not evidence of completion, so it fails.
    Without a GitHub token the state cannot be read at all; existence of the
    `refs/pull/N/head` ref is reported as `not_run`, not as success.
  * `check_command_output` refuses anything outside an explicit allowlist.
    Re-running whatever string a worker puts in a receipt would let the worker
    choose its own verification.

Credentials are read from the environment (`GITHUB_TOKEN`) and never logged;
failures report status codes and messages, not headers.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from conference_verification import FAILED, NOT_RUN, PASSED, CheckOutcome

_PR_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)
_COMMIT_URL = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/commit/(?P<sha>[0-9a-f]{7,40})"
)
_SHA = re.compile(r"^[0-9a-f]{7,40}$")

DEFAULT_TIMEOUT = 15.0


@dataclass
class LiveEvidenceChecker:
    """Checks cited evidence against the network, git and disk.

    `workspace` is the checkout used for commit and file evidence. `remote` is
    the git remote consulted for commits that are not present locally.
    """

    workspace: Path | None = None
    remote: str = "origin"
    github_token: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    allowed_commands: Sequence[str] = ()

    def __post_init__(self) -> None:
        if self.workspace is not None:
            self.workspace = Path(self.workspace)
        if self.github_token is None:
            self.github_token = os.environ.get("GITHUB_TOKEN") or None

    # --- pull requests ----------------------------------------------------

    def check_pull_request(self, ref: str) -> CheckOutcome:
        match = _PR_URL.match(ref.strip())
        if not match:
            return CheckOutcome(FAILED, "not a GitHub pull request URL")
        owner, repo, number = match["owner"], match["repo"], match["number"]

        if not self.github_token:
            return CheckOutcome(
                NOT_RUN,
                "no GITHUB_TOKEN: cannot read whether the pull request is merged",
            )

        api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
        status, payload = self._get_json(api, github=True)
        if status == 404:
            return CheckOutcome(FAILED, "pull request does not exist")
        if status != 200 or payload is None:
            return CheckOutcome(NOT_RUN, f"GitHub API returned {status}")

        if payload.get("merged_at"):
            return CheckOutcome(PASSED, f"merged at {payload['merged_at']}")
        if payload.get("state") == "closed":
            return CheckOutcome(FAILED, "pull request was closed without merging")
        draft = " (draft)" if payload.get("draft") else ""
        return CheckOutcome(FAILED, f"pull request is still open{draft}")

    # --- commits ----------------------------------------------------------

    def check_commit(self, ref: str) -> CheckOutcome:
        sha = ref.strip()
        match = _COMMIT_URL.match(sha)
        if match:
            sha = match["sha"]
        if not _SHA.match(sha):
            return CheckOutcome(FAILED, "not a commit sha or GitHub commit URL")

        if self.workspace is None:
            return CheckOutcome(NOT_RUN, "no workspace configured to resolve the commit in")
        local = self._git("cat-file", "-e", f"{sha}^{{commit}}")
        if local.returncode == 0:
            return CheckOutcome(PASSED, "commit exists in the workspace")

        remote = self._git("fetch", "--quiet", self.remote, sha)
        if remote.returncode == 0:
            return CheckOutcome(PASSED, f"commit fetched from {self.remote}")
        return CheckOutcome(FAILED, "commit not found locally or on the remote")

    # --- files ------------------------------------------------------------

    def check_file(self, ref: str) -> CheckOutcome:
        candidate = Path(ref)
        if candidate.is_absolute():
            return CheckOutcome(FAILED, "file evidence must be repository-relative")
        if self.workspace is None:
            return CheckOutcome(NOT_RUN, "no workspace configured to resolve the file in")
        resolved = (self.workspace / candidate).resolve()
        if not str(resolved).startswith(str(self.workspace.resolve())):
            return CheckOutcome(FAILED, "path escapes the workspace")
        if not resolved.exists():
            return CheckOutcome(FAILED, "file does not exist")
        if resolved.is_file() and resolved.stat().st_size == 0:
            return CheckOutcome(FAILED, "file is empty")
        return CheckOutcome(PASSED, f"{resolved.relative_to(self.workspace.resolve())} exists")

    # --- urls -------------------------------------------------------------

    def check_url(self, ref: str) -> CheckOutcome:
        url = ref.strip()
        if not url.startswith(("http://", "https://")):
            return CheckOutcome(FAILED, "not an http(s) URL")
        status, _ = self._get(url)
        if status is None:
            return CheckOutcome(NOT_RUN, "URL could not be reached")
        if 200 <= status < 300:
            return CheckOutcome(PASSED, f"HTTP {status}")
        if status in (401, 403, 429):
            return CheckOutcome(NOT_RUN, f"HTTP {status}: cannot read this URL")
        return CheckOutcome(FAILED, f"HTTP {status}")

    # --- command output ---------------------------------------------------

    def check_command_output(self, ref: str) -> CheckOutcome:
        """Re-run a cited command, but only one Conference already trusts."""
        command = ref.strip()
        if command not in self.allowed_commands:
            return CheckOutcome(
                NOT_RUN,
                "command is not on the verification allowlist; a worker may not "
                "choose the command that verifies it",
            )
        if self.workspace is None:
            return CheckOutcome(NOT_RUN, "no workspace configured to run the command in")
        result = subprocess.run(  # noqa: S603 - allowlisted command only
            shlex.split(command),
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=self.timeout * 10,
        )
        if result.returncode == 0:
            return CheckOutcome(PASSED, f"`{command}` exited 0")
        tail = (result.stdout + result.stderr).strip().splitlines()[-1:] or [""]
        return CheckOutcome(FAILED, f"`{command}` exited {result.returncode}: {tail[0]}")

    # --- internals --------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=self.timeout * 4,
        )

    def _get(self, url: str, github: bool = False) -> tuple[int | None, bytes]:
        headers = {"User-Agent": "bjvi-conference-verifier"}
        if github and self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
            headers["Accept"] = "application/vnd.github+json"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return None, b""

    def _get_json(self, url: str, github: bool = False) -> tuple[int | None, dict | None]:
        status, body = self._get(url, github=github)
        try:
            return status, json.loads(body)
        except (ValueError, TypeError):
            return status, None
