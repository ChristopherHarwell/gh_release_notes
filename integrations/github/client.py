"""
Simplified GitHub Integration Module

All GitHub integration logic is in this file, with minimal abstraction and clear, top-down flow.
"""

from flask import jsonify
import hashlib
import hmac
import logging
import re
from datetime import datetime
from pprint import pprint
from typing import Any, Literal, cast, override

# from flask_af30.settings import settings
from integrations.common.json_api_client import BaseJSONAPIClient, JSONResponse
from httpx import Response, Request

from .constants import CONVENTIONAL_TYPE_MAP, GITHUB_API_BASE, SEMVER_RE
from .models import Commit, Release
from .parsing import parse_commit_message


# class GitHubAPIError(ExternalAPIError):
#     pass


class GitHubClient(BaseJSONAPIClient):
    """Client for interacting with the GitHub REST API v3."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        rate_limit_delay: float = 1.0,
        max_backoff: float = 60.0,
        log_level: int | None = None,
        debug: bool = False,
    ):
        super().__init__(
            base_url=GITHUB_API_BASE,
            timeout=timeout,
            max_retries=max_retries,
            rate_limit_delay=rate_limit_delay,
            max_backoff=max_backoff,
            log_level=log_level,
            debug=debug,
            auto_authenticate=False,
        )
        self.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": "AvatarFleet-GitHub-Client/1.0",
            }
        )
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def get(self, endpoint: str, **kwargs) -> JSONResponse:
        return self.request("GET", endpoint, **kwargs)

    def request_all_pages(
        self,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        endpoint: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json: Any = None,
        form_data: dict[str, Any] | None = None,
        max_pages: int = 100,
    ) -> JSONResponse:
        """Fetch all items from a Github API endpoint (single request, no pagination)."""
        response = self.request(
            method,
            endpoint,
            params=params,
            headers=headers,
            json=json,
            form_data=form_data,
        )
        all_data = []
        if isinstance(response, dict):
            data = response.get("data")
            if isinstance(data, list):
                all_data.extend(data)
            elif data is not None:
                all_data.append(data)
            response["data"] = all_data
            return response
        return cast("JSONResponse", {"data": []})

    def list_releases(
        self,
        owner: str,
        repo: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 100,
    ) -> JSONResponse:
        """List the releases for a repository."""
        result = self.request_all_pages(
            "GET",
            f"/repos/{owner}/{repo}/releases",
            params=params or {},
            max_pages=max_pages,
        )
        # Print the type for the result
        print(type(result))
        if isinstance(result, list):
            pprint(result)
            # raise RuntimeError("request_all_pages must return response and request objects for JSONResponse")
        return result

    def get_release_assets(
        self,
        owner: str,
        repo: str,
        release_id: int,
    ) -> JSONResponse:
        """Retrieve assets for a specific release in a repository."""
        return self.request_all_pages(
            "GET",
            f"/repos/{owner}/{repo}/releases/{release_id}/assets",
        )

    def get_commits_between_tags(
        self, owner: str, repo: str, base: str, head: str
    ) -> JSONResponse:
        """Get the commits between two tags.

        Args:
            self: The GitHubClient instance.
            owner: The owner of the repository.
            repo: The name of the repository.
            base: The base tag.
            head: The head tag.

        Returns:
            A JSONResponse containing the commits between the two tags.
        """
        return self.request_all_pages(
            "GET", f"/repos/{owner}/{repo}/compare/{base}...{head}"
        )  # TODO: Ask if this should go in support/github.py or if it should be here.

    def get_releases(
        self,
        owner: str,
        repo: str,
        max_pages: int = 100,
    ) -> list[Release]:
        raw_releases = self.list_releases(owner, repo, max_pages=max_pages)
        releases = []
        data = raw_releases.get("data", [])
        if not isinstance(data, list):
            data = []
        for release in data:
            published_at = release.get("published_at")
            if published_at:
                published_at = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )
            else:
                published_at = datetime.now()
            tag = release.get("tag_name", "")
            version = tag.partition("-rc")[0]
            releases.append(
                Release(published_at=published_at, tag=tag, version=version, commits=[])
            )
        return releases

    def get_commits_for_release(
        self,
        owner: str,
        repo: str,
        base_tag: str,
        head_tag: str,
    ) -> list[Commit]:
        compare_data = self.get_commits_between_tags(owner, repo, base_tag, head_tag)
        commits = []
        commit_list = []
        if "data" in compare_data and isinstance(compare_data["data"], dict):
            data_dict = cast(dict[str, Any], compare_data["data"])
            commit_list = data_dict.get("commits", [])
        if not isinstance(commit_list, list):
            commit_list = []
        commit_list = [
            commit
            for commit in commit_list
            if isinstance(commit, dict) and "commit" in commit and "sha" in commit
        ]
        for commit_wrapper in commit_list:
            commit_wrapper = cast(dict[str, Any], commit_wrapper)
            commit_obj = commit_wrapper["commit"]
            if (
                not isinstance(commit_obj, dict)
                or "author" not in commit_obj
                or "message" not in commit_obj
            ):
                continue
            commit_obj = cast(dict[str, Any], commit_obj)
            author = (
                commit_obj["author"] if isinstance(commit_obj["author"], dict) else {}
            )
            sha = commit_wrapper["sha"]
            message = commit_obj["message"]
            date = author["date"] if "date" in author else ""
            author_name = author["name"] if "name" in author else ""
            parsed = parse_commit_message(message)
            commits.append(
                Commit(
                    sha=sha,
                    type=parsed.type,
                    scope=parsed.scope,
                    breaking=parsed.breaking,
                    message=parsed.description,
                    body=parsed.body,
                    footer=parsed.footer,
                    date=datetime.fromisoformat(date.replace("Z", "+00:00"))
                    if date
                    else datetime.utcnow(),
                    author=author_name,
                )
            )
        return commits

    @override
    def authenticate(self) -> bool:
        # GitHub uses token-based authentication via headers; nothing to do here
        return True


# =========================
# client api -Utility Functions
# =========================


def parse_version_from_ref_or_tag(ref_or_tag: str) -> tuple[int, int, int]:
    match = SEMVER_RE.search(ref_or_tag)
    if match:
        groups = match.groups()
        if len(groups) == 3:
            return int(groups[0]), int(groups[1]), int(groups[2])
    return 0, 0, 0


def extract_conventional_type_from_message(message: str) -> str:
    if not message:
        return "other"
    match = re.match(r"(\w+)(\(.+\))?:", message)
    if match:
        return match.group(1).lower()
    return "other"


def extract_release_data(
    payload: dict[str, Any],
) -> tuple[str, str, str, tuple[int, int, int]]:
    """Extract release data from a GitHub webhook payload.
    
    Example payload:
    ```json
    ```

    Example return value:
    ```python
    ("ChristopherHarwell", "Flask_Python_Webhook_Tutorial", "1.2.018", (1, 2, 18))
    ```
    """
    release = payload.get("release", {})
    tag_name = release.get("tag_name", "")
    owner = payload.get("repository", {}).get("owner", {}).get("login", "ChristopherHarwell")
    repo = payload.get("repository", {}).get("name", "Flask_Python_Webhook_Tutorial")
    version_major, version_minor, version_patch = parse_version_from_ref_or_tag(
        tag_name
    )
    return owner, repo, tag_name, (version_major, version_minor, version_patch)


def find_previous_tag(
    client: GitHubClient,
    owner: str,
    repo: str,
    current_tag: str,
    logger: logging.Logger,
) -> str | None:
    try:
        tags_resp = client.get(f"/repos/{owner}/{repo}/tags")
        # Handle both list and dict responses
        if isinstance(tags_resp, list):
            tags_data = tags_resp
        elif isinstance(tags_resp, dict):
            tags_data = tags_resp.get("data", [])
        else:
            tags_data = []
        tag_names = (
            [tag["name"] for tag in tags_data if isinstance(tag, dict) and tag.get("name") != current_tag]
            if isinstance(tags_data, list)
            else []
        )
        tag_names_sorted = sorted(
            tag_names, key=parse_version_from_ref_or_tag, reverse=True
        )
        prev_tag = tag_names_sorted[0] if tag_names_sorted else None
        if not prev_tag:
            logger.warning(
                "No previous tag found; only creating release note for this release."
            )
        return prev_tag
    except Exception as e:
        logger.error(f"Failed to fetch tags for {owner}/{repo}: {e}")
        return None


def verify_github_signature(request, logger: logging.Logger) -> bool:
    secret = ""
    if not secret:
        logger.warning(
            "No github_webhook_secret in settings; skipping signature validation"
        )
        return True
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        logger.warning("No X-Hub-Signature-256 header found")
        return False
    sha_name, signature = signature.split("=")
    if sha_name != "sha256":
        logger.warning(f"Unsupported signature type: {sha_name}")
        return False
    mac = hmac.new(secret.encode(), msg=request.get_data(), digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        logger.warning("GitHub webhook signature mismatch")
        return False
    return True
