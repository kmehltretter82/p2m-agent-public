"""Typed client for the Prove2Me API.

Written against SKILL.md version SUPPORTED_VERSION. The client stops on a version mismatch,
because endpoints or response shapes may have changed, and a wrong guess here could mean a
wrong public submission. The API key is only ever sent to BASE_URL.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .gate import Target

BASE_URL = "https://prove2.me/api/v1"
SUPPORTED_VERSION = "0.13.6"
#: Refresh this long before the one-hour token expires.
TOKEN_MARGIN_SECONDS = 120
KEY_EXPIRY_WARNING_SECONDS = 3 * 24 * 3600


class PlatformError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class VersionMismatch(PlatformError):
    pass


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Environment(_Model):
    mathlib_rev: str
    toolchain: str
    display_name: str = ""
    is_default: bool = False


class Audit(_Model):
    decision: str
    # The platform can return null for either of these.
    comment: str | None = ""
    reviewer_username: str | None = ""


class Theorem(_Model):
    theorem_id: str
    theorem_name: str
    status: str
    formal_statement: str
    preamble: str = ""
    mathlib_rev: str
    natural_language_statement: str | None = ""
    source: str | None = None
    deprecated_at: str | None = None
    audits: list[Audit] = []


class SubmissionStatus(_Model):
    id: str
    theorem_id: str = ""
    status: str
    error_message: str | None = ""
    created_at: str | None = None


class OpenLeaf(_Model):
    theorem_id: str
    theorem_name: str
    status: str
    closability: int = 0


class MilestoneTheorem(_Model):
    id: str
    theorem_name: str
    status: str


class Milestone(_Model):
    id: str
    title: str
    milestone_description: str = ""
    sort_order: int = 0
    completed: bool = False
    theorem: MilestoneTheorem | None = None


class Mission(_Model):
    id: str
    name: str
    description: str = ""
    visibility: str = "public"


def load_api_key(path: Path) -> str:
    return json.loads(path.read_text())["api_key"]


class PlatformClient:
    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock=time.time,
    ):
        self._api_key = api_key
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._clock = clock
        self.key_expiry_warning: str | None = None
        self._http = httpx.Client(
            base_url=BASE_URL, transport=transport, timeout=60.0, follow_redirects=False
        )

    def close(self) -> None:
        self._http.close()

    # --- auth --------------------------------------------------------------------------

    def _check_version(self, version: str | None) -> None:
        if version != SUPPORTED_VERSION:
            raise VersionMismatch(
                f"platform is at version {version}, this client supports {SUPPORTED_VERSION}. "
                "Re-read the updated docs before submitting anything."
            )

    def health(self) -> dict:
        """Public endpoint, no key needed. Also runs the version check."""
        data = self._raise_for_status(self._http.get("/health")).json()
        self._check_version(data.get("version"))
        return data

    def _refresh(self) -> None:
        if not self._api_key:
            raise PlatformError("no Prove2Me API key configured")
        response = self._http.post("/agent/refresh", json={"api_key": self._api_key})
        if response.status_code == 401:
            raise PlatformError("the API key has expired or been invalidated", 401)
        data = self._raise_for_status(response).json()
        self._check_version(data.get("version"))
        self._token = data["access_token"]
        self._token_expires_at = float(data["expires_at"])

    def _headers(self) -> dict[str, str]:
        if not self._token or self._clock() > self._token_expires_at - TOKEN_MARGIN_SECONDS:
            self._refresh()
        return {"Authorization": f"Bearer {self._token}"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response
        raise PlatformError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text[:500]}",
            response.status_code,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, headers=self._headers(), **kwargs)
        if response.status_code == 401:
            self._refresh()
            response = self._http.request(method, path, headers=self._headers(), **kwargs)
        return self._raise_for_status(response)

    def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=clean).json()

    # --- reads -------------------------------------------------------------------------

    def environments(self) -> list[Environment]:
        return [Environment(**e) for e in self._get("/environments")["environments"]]

    def theorem(self, theorem_id: str) -> Theorem:
        return Theorem(**self._get(f"/theorems/{theorem_id}"))

    def missions(self, q: str | None = None, limit: int = 20, offset: int = 0) -> list[Mission]:
        data = self._get("/missions", q=q, limit=limit, offset=offset)
        return [Mission(**m) for m in data["missions"]]

    def milestones(self, mission_id: str, limit: int = 100) -> list[Milestone]:
        data = self._get(f"/missions/{mission_id}/milestones", limit=limit)
        return [Milestone(**m) for m in data["milestones"]]

    def open_leaves(self, theorem_id: str, limit: int = 50) -> list[OpenLeaf]:
        data = self._get(f"/theorems/{theorem_id}/open-leaves", limit=limit)
        return [OpenLeaf(**leaf) for leaf in data["open_leaves"]]

    def decompositions(self, theorem_id: str) -> list[dict]:
        return self._get(f"/theorems/{theorem_id}/decompositions")["decompositions"]

    def milestone_history(self, milestone_id: str) -> list[dict]:
        return self._get(f"/milestones/{milestone_id}/history")["events"]

    def mission_comments(self, mission_id: str) -> Any:
        return self._get(f"/missions/{mission_id}/comments")

    def theorem_mentions(self, theorem_id: str) -> Any:
        return self._get(f"/theorems/{theorem_id}/mentions")

    def theorem_submissions(self, theorem_id: str, status: str | None = None) -> list[dict]:
        return self._get(f"/theorems/{theorem_id}/submissions", status=status)["submissions"]

    def my_submissions(self, page: int = 1) -> list[SubmissionStatus]:
        data = self._get("/submissions", page=page)
        return [SubmissionStatus(**s) for s in data["submissions"]]

    def submission_source(self, submission_id: str) -> str:
        return self._get(f"/submissions/{submission_id}/solution")["content"]

    def poll(self, submission_id: str) -> SubmissionStatus:
        return SubmissionStatus(**self._get("/verify", submission_id=submission_id))

    # --- writes ------------------------------------------------------------------------

    def verify(
        self, theorem_id: str, source: str, proof_type: str, explanation: str | None = None
    ) -> str:
        """Create a submission. Despite its name, this publishes. Returns the submission id."""
        data: dict[str, str] = {"theorem_id": theorem_id, "proof_type": proof_type}
        if explanation:
            data["explanation"] = explanation
        files = {"file": ("solution.lean", source.encode(), "text/plain")}
        return self._request("POST", "/verify", data=data, files=files).json()["submission_id"]

    def submit_problem(
        self, theorem_name: str, title: str, formal_statement: str, natural_language: str,
        private: bool, preamble: str = "", source: str | None = None, env: str | None = None,
    ) -> str:
        """Publish one statement, which cannot be edited afterwards. Returns the job id.

        `private` has no default on purpose: making a statement public is always a decision.
        """
        problem: dict[str, Any] = {
            "theorem_name": theorem_name, "theorem_title": title,
            "formal_statement": formal_statement, "natural_language_statement": natural_language,
            "preamble": preamble,
        }
        if source:
            problem["source"] = source
        body: dict[str, Any] = {"problems": [problem], "private": private}
        if env:
            body["env"] = env
        data = self._request("POST", "/submit-problem", json=body).json()
        if not data.get("jobs"):
            raise PlatformError(f"problem was not queued: {data.get('errors')}")
        return data["jobs"][0]["job_id"]

    def publish_job(self, job_id: str) -> dict:
        return self._get(f"/publish-jobs/{job_id}")

    def set_theorem_deprecated(self, theorem_id: str, deprecated: bool) -> dict:
        return self._request(
            "PATCH", f"/theorems/{theorem_id}", json={"deprecated": deprecated}
        ).json()

    def patch_explanation(self, submission_id: str, explanation: str | None) -> dict:
        return self._request(
            "PATCH", f"/submissions/{submission_id}", json={"explanation": explanation}
        ).json()


def target_from_theorem(theorem: Theorem, environments: list[Environment]) -> Target:
    by_rev = {e.mathlib_rev: e for e in environments}
    environment = by_rev.get(theorem.mathlib_rev)
    if environment is None:
        raise PlatformError(f"theorem uses unknown environment {theorem.mathlib_rev}")
    return Target(
        theorem_name=theorem.theorem_name,
        formal_statement=theorem.formal_statement,
        preamble=theorem.preamble,
        mathlib_rev=theorem.mathlib_rev,
        toolchain=environment.toolchain,
        theorem_id=theorem.theorem_id,
    )
