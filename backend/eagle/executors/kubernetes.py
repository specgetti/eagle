"""Runs each review as a Kubernetes Job in an isolated pod.

The run spec (including the inference credential) goes into a per-run Secret
owned by the Job. The runner reports its result back to eagle-web through the
callback API, and the worker deletes the Secret once the review is stored.
"""

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx

from eagle.config import Settings
from eagle.executors.base import RUNNING, Executor, RunSpec, RunState

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SPEC_MOUNT = "/var/run/eagle"
LOG_TAIL_LINES = 50


class KubernetesError(Exception):
    pass


def in_cluster_client() -> httpx.AsyncClient:
    host = os.environ["KUBERNETES_SERVICE_HOST"]
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    token = (SA_DIR / "token").read_text().strip()
    return httpx.AsyncClient(
        base_url=f"https://{host}:{port}",
        headers={"Authorization": f"Bearer {token}"},
        verify=str(SA_DIR / "ca.crt"),
        timeout=30,
    )


def runner_labels(settings: Settings, review_id: int) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": "eagle",
        "app.kubernetes.io/component": "eagle-runner",
        "app.kubernetes.io/instance": settings.instance_name,
        "eagle.redhat.com/review-id": str(review_id),
    }


def build_job(name: str, spec: RunSpec, settings: Settings) -> dict[str, Any]:
    labels = runner_labels(settings, spec.review_id)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": spec.timeout_seconds + settings.timeout_grace_seconds,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": settings.runner_service_account,
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [
                        {
                            "name": "runner",
                            "image": settings.runner_image,
                            "command": ["python3", "-m", "eagle.runner", f"{SPEC_MOUNT}/spec.json"],
                            "env": [{"name": "TMPDIR", "value": "/tmp"}],
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "512Mi"},
                                "limits": {
                                    "cpu": settings.runner_cpu_limit,
                                    "memory": settings.runner_memory_limit,
                                },
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "spec", "mountPath": SPEC_MOUNT, "readOnly": True},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        # 0444: the runner's UID is assigned at admission (a random
                        # one on OpenShift) and never owns these root-owned files,
                        # so owner-only modes are unreadable. The pod has a single
                        # container and the agent runs as the same user anyway.
                        {"name": "spec", "secret": {"secretName": name, "defaultMode": 0o444}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "10Gi"}},
                    ],
                },
            },
        },
    }


def build_secret(name: str, spec_json: str, job_uid: str, review_id: int, settings: Settings):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name,
            "labels": runner_labels(settings, review_id),
            # Garbage-collected with the Job even if the worker never cleans up.
            "ownerReferences": [
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "name": name,
                    "uid": job_uid,
                    "blockOwnerDeletion": False,
                }
            ],
        },
        "data": {"spec.json": base64.b64encode(spec_json.encode()).decode()},
    }


class KubernetesExecutor(Executor):
    uses_callback = True

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or in_cluster_client()
        self._ns = settings.k8s_namespace

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code >= 400 and resp.status_code != 404:
            raise KubernetesError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp

    def _jobs(self, name: str = "") -> str:
        return f"/apis/batch/v1/namespaces/{self._ns}/jobs" + (f"/{name}" if name else "")

    def _secrets(self, name: str = "") -> str:
        return f"/api/v1/namespaces/{self._ns}/secrets" + (f"/{name}" if name else "")

    async def launch(self, spec: RunSpec, callback: dict[str, str] | None) -> str:
        name = f"eagle-review-{spec.review_id}-{secrets.token_hex(3)}"
        resp = await self._request("POST", self._jobs(), json=build_job(name, spec, self._settings))
        job_uid = resp.json()["metadata"]["uid"]
        spec_json = json.dumps(spec.to_dict(callback=callback))
        try:
            await self._request(
                "POST",
                self._secrets(),
                json=build_secret(name, spec_json, job_uid, spec.review_id, self._settings),
            )
        except Exception:
            await self.cancel(name)
            raise
        return name

    async def status(self, ref: str) -> RunState:
        resp = await self._request("GET", self._jobs(ref))
        if resp.status_code == 404:
            return RunState("failed", error="Review Job no longer exists")
        status = resp.json().get("status", {})
        if status.get("succeeded"):
            return RunState("finished")
        failed_condition = next(
            (
                c
                for c in status.get("conditions", [])
                if c.get("type") == "Failed" and c.get("status") == "True"
            ),
            None,
        )
        if status.get("failed") or failed_condition:
            reason = (failed_condition or {}).get("message") or "Runner pod failed"
            logs = await self._pod_logs(ref)
            return RunState("failed", error=f"{reason}\n{logs}".strip())
        return RUNNING

    async def _pod_logs(self, job_name: str) -> str:
        try:
            pods = await self._request(
                "GET",
                f"/api/v1/namespaces/{self._ns}/pods",
                params={"labelSelector": f"job-name={job_name}"},
            )
            items = pods.json().get("items", [])
            if not items:
                return ""
            pod = items[0]["metadata"]["name"]
            logs = await self._request(
                "GET",
                f"/api/v1/namespaces/{self._ns}/pods/{pod}/log",
                params={"tailLines": LOG_TAIL_LINES},
            )
            return logs.text if logs.status_code == 200 else ""
        except (KubernetesError, httpx.HTTPError, KeyError, ValueError):
            return ""

    async def cancel(self, ref: str) -> None:
        await self._request("DELETE", self._jobs(ref), json={"propagationPolicy": "Background"})

    async def cleanup(self, ref: str) -> None:
        # Remove the credential right away; the Job itself stays until its TTL for debugging.
        await self._request("DELETE", self._secrets(ref))

    async def aclose(self) -> None:
        await self._client.aclose()
