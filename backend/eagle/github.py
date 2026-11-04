import re
from dataclasses import dataclass

import httpx

from eagle import __version__

_PR_URL = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>\d+)(?:[/?#].*)?$"
)
COMMENT_MAX = 65000


@dataclass(frozen=True)
class PullRequestRef:
    host: str
    owner: str
    repo: str
    number: int

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def canonical_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}/pull/{self.number}"


def parse_pr_url(url: str, allowed_hosts: list[str]) -> PullRequestRef:
    match = _PR_URL.match(url.strip())
    if not match:
        raise ValueError("Not a pull request URL (expected https://<host>/<owner>/<repo>/pull/<n>)")
    host = match["host"].lower()
    if host not in {h.lower() for h in allowed_hosts}:
        raise ValueError(f"Git host {host!r} is not allowed")
    return PullRequestRef(
        host=host, owner=match["owner"], repo=match["repo"], number=int(match["number"])
    )


def default_api_url(host: str) -> str:
    return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"


@dataclass(frozen=True)
class PullInfo:
    title: str
    author: str
    head_sha: str
    base_ref: str
    state: str  # "open" or "closed"
    merged: bool
    clone_url: str

    @property
    def is_open(self) -> bool:
        return self.state == "open"


class GitHubError(Exception):
    pass


class GitHubClient:
    def __init__(
        self,
        api_url: str,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"eagle-pr-review/{__version__}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=api_url.rstrip("/"), headers=headers, transport=transport, timeout=30
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if resp.status_code >= 400:
            message = resp.json().get("message", resp.text) if resp.content else resp.reason_phrase
            raise GitHubError(f"GitHub {method} {path} returned {resp.status_code}: {message}")
        return resp.json()

    async def get_pull(self, ref: PullRequestRef) -> PullInfo:
        data = await self._call("GET", f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}")
        return PullInfo(
            title=data["title"],
            author=data["user"]["login"],
            head_sha=data["head"]["sha"],
            base_ref=data["base"]["ref"],
            state=data["state"],
            merged=bool(data.get("merged")),
            clone_url=data["base"]["repo"]["clone_url"],
        )

    async def post_comment(self, ref: PullRequestRef, body: str) -> str:
        if len(body) > COMMENT_MAX:
            suffix = "\n\n_(truncated, see Eagle for full review)_"
            body = body[: COMMENT_MAX - len(suffix)] + suffix
        data = await self._call(
            "POST",
            f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments",
            json={"body": body},
        )
        return data["html_url"]
