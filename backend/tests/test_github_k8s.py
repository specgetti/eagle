import json

import httpx
import pytest

from eagle.config import Settings
from eagle.executors.base import RunSpec
from eagle.executors.kubernetes import KubernetesError, KubernetesExecutor, build_job
from eagle.github import GitHubClient, GitHubError, PullRequestRef
from tests.fakes import FakeGitHub

REF = PullRequestRef(host="github.com", owner="acme", repo="widgets", number=42)


# GitHub client


async def test_get_pull_and_auth_header():
    fake = FakeGitHub()
    fake.add_pull(head_sha="b" * 40, base_ref="release-1")
    async with GitHubClient("https://api.github.com", "ghp_x", transport=fake.transport) as gh:
        info = await gh.get_pull(REF)
    assert info.head_sha == "b" * 40
    assert info.base_ref == "release-1"
    assert info.is_open
    assert info.clone_url == "https://github.com/acme/widgets.git"
    request = fake.requests[0]
    assert request.headers["authorization"] == "Bearer ghp_x"
    assert request.url.path == "/repos/acme/widgets/pulls/42"


async def test_get_pull_without_token_and_404():
    fake = FakeGitHub()
    async with GitHubClient("https://api.github.com", None, transport=fake.transport) as gh:
        with pytest.raises(GitHubError, match="404"):
            await gh.get_pull(REF)
    assert "authorization" not in fake.requests[0].headers


async def test_enterprise_api_path():
    fake = FakeGitHub()
    fake.add_pull()
    async with GitHubClient("https://ghe.example.com/api/v3", None, transport=fake.transport) as gh:
        await gh.get_pull(REF)
    assert str(fake.requests[0].url).startswith("https://ghe.example.com/api/v3/repos/")


async def test_post_comment_truncates():
    fake = FakeGitHub()
    async with GitHubClient("https://api.github.com", "t", transport=fake.transport) as gh:
        url = await gh.post_comment(REF, "x" * 70000)
    assert url.endswith("#c1")
    assert len(fake.comments[0]["body"]) <= 65000
    assert "truncated" in fake.comments[0]["body"]


async def test_network_error_wrapped():
    def boom(request):
        raise httpx.ConnectError("down")

    async with GitHubClient(
        "https://api.github.com", None, transport=httpx.MockTransport(boom)
    ) as gh:
        with pytest.raises(GitHubError, match="request failed"):
            await gh.get_pull(REF)


# Kubernetes executor


@pytest.fixture
def k8s_settings() -> Settings:
    return Settings(
        _env_file=None,
        k8s_namespace="eagle-ns",
        instance_name="prod",
        runner_image="quay.io/x/runner:1",
        runner_service_account="prod-runner",
        runner_cpu_limit="1",
        runner_memory_limit="2Gi",
    )


def spec() -> RunSpec:
    return RunSpec(
        review_id=9,
        clone_url="https://github.com/acme/widgets.git",
        pr_number=42,
        base_ref="main",
        head_sha="a" * 40,
        git_token="ghp_tok",
        invocation={"argv": ["claude"], "env": {"ANTHROPIC_API_KEY": "sk-secret"}},
        timeout_seconds=600,
    )


def test_job_manifest_is_locked_down(k8s_settings):
    job = build_job("eagle-review-9-abc", spec(), k8s_settings)
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 600 + k8s_settings.timeout_grace_seconds
    assert pod["automountServiceAccountToken"] is False
    assert pod["serviceAccountName"] == "prod-runner"
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["resources"]["limits"] == {"cpu": "1", "memory": "2Gi"}
    assert container["image"] == "quay.io/x/runner:1"
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels["app.kubernetes.io/component"] == "eagle-runner"
    assert labels["app.kubernetes.io/instance"] == "prod"
    # No secrets in the Job itself.
    assert "sk-secret" not in json.dumps(job)
    # The runner's UID is assigned at admission (random on OpenShift) and does not
    # own the root-owned Secret files, so the mode must allow reads by any user.
    # An owner-only mode (0o400) made every review fail with "Permission denied".
    volumes = {v["name"]: v for v in job["spec"]["template"]["spec"]["volumes"]}
    assert volumes["spec"]["secret"]["defaultMode"] == 0o444


class FakeKube:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.job_status: dict = {}
        self.fail_secret = False
        self.job_exists = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if request.method == "POST" and path.endswith("/jobs"):
            return httpx.Response(201, json={"metadata": {"uid": "uid-123"}})
        if request.method == "POST" and path.endswith("/secrets"):
            if self.fail_secret:
                return httpx.Response(403, json={"message": "forbidden"})
            return httpx.Response(201, json={})
        if request.method == "GET" and "/jobs/" in path:
            if not self.job_exists:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"status": self.job_status})
        if request.method == "GET" and path.endswith("/pods"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "pod-1"}}]})
        if request.method == "GET" and path.endswith("/log"):
            return httpx.Response(200, text="runner log line")
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(500)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://kube", transport=httpx.MockTransport(self.handler)
        )


async def test_k8s_launch_creates_job_then_owned_secret(k8s_settings):
    kube = FakeKube()
    executor = KubernetesExecutor(k8s_settings, client=kube.client())
    name = await executor.launch(spec(), {"url": "http://web/cb", "token": "cb-token"})
    assert name.startswith("eagle-review-9-")

    (m1, p1, job), (m2, p2, secret) = kube.calls
    assert (m1, p1) == ("POST", "/apis/batch/v1/namespaces/eagle-ns/jobs")
    assert (m2, p2) == ("POST", "/api/v1/namespaces/eagle-ns/secrets")
    assert secret["metadata"]["name"] == name
    assert secret["metadata"]["ownerReferences"][0]["uid"] == "uid-123"
    import base64

    run_spec = json.loads(base64.b64decode(secret["data"]["spec.json"]))
    assert run_spec["callback"] == {"url": "http://web/cb", "token": "cb-token"}
    assert run_spec["invocation"]["env"]["ANTHROPIC_API_KEY"] == "sk-secret"


async def test_k8s_secret_failure_deletes_job(k8s_settings):
    kube = FakeKube()
    kube.fail_secret = True
    executor = KubernetesExecutor(k8s_settings, client=kube.client())
    with pytest.raises(KubernetesError):
        await executor.launch(spec(), None)
    assert kube.calls[-1][0] == "DELETE"
    assert "/jobs/" in kube.calls[-1][1]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({}, "running"),
        ({"active": 1}, "running"),
        ({"succeeded": 1}, "finished"),
        ({"failed": 1}, "failed"),
        (
            {"conditions": [{"type": "Failed", "status": "True", "message": "DeadlineExceeded"}]},
            "failed",
        ),
    ],
)
async def test_k8s_status_mapping(k8s_settings, status, expected):
    kube = FakeKube()
    kube.job_status = status
    executor = KubernetesExecutor(k8s_settings, client=kube.client())
    state = await executor.status("eagle-review-9-abc")
    assert state.state == expected
    assert state.result is None
    if expected == "failed":
        assert "runner log line" in state.error


async def test_k8s_missing_job_and_cleanup(k8s_settings):
    kube = FakeKube()
    kube.job_exists = False
    executor = KubernetesExecutor(k8s_settings, client=kube.client())
    assert (await executor.status("gone")).state == "failed"
    await executor.cleanup("eagle-review-9-abc")
    await executor.cancel("eagle-review-9-abc")
    assert kube.calls[-2][:2] == (
        "DELETE",
        "/api/v1/namespaces/eagle-ns/secrets/eagle-review-9-abc",
    )
    method, path, body = kube.calls[-1]
    assert (method, path) == (
        "DELETE",
        "/apis/batch/v1/namespaces/eagle-ns/jobs/eagle-review-9-abc",
    )
    assert body == {"propagationPolicy": "Background"}
