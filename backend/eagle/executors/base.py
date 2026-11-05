from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RunSpec:
    review_id: int
    clone_url: str
    pr_number: int
    base_ref: str | None
    head_sha: str | None
    git_token: str | None
    invocation: dict[str, Any]
    timeout_seconds: int
    agent: str = ""
    model: str | None = None
    prompt: str = ""
    model_source: str | None = None

    def to_dict(
        self, result_path: str | None = None, callback: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "repo": {
                "clone_url": self.clone_url,
                "pr_number": self.pr_number,
                "base_ref": self.base_ref,
                "head_sha": self.head_sha,
                "token": self.git_token,
            },
            "invocation": self.invocation,
            "model_source": self.model_source,
            "timeout_seconds": self.timeout_seconds,
            "result_path": result_path,
            "callback": callback,
        }


@dataclass
class RunState:
    state: Literal["running", "finished", "failed"]
    # Present when the executor collected the result itself (local, mock).
    # None for executors where the runner reports back through the callback API.
    result: dict[str, Any] | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


RUNNING = RunState("running")


class Executor(ABC):
    # True when runs report results through POST /api/v1/internal/reviews/{id}/result.
    uses_callback: bool = False
    # False only for the mock executor, which never calls a model.
    requires_credentials: bool = True

    @abstractmethod
    async def launch(self, spec: RunSpec, callback: dict[str, str] | None) -> str:
        """Start a run and return an opaque reference stored on the review."""

    @abstractmethod
    async def status(self, ref: str) -> RunState: ...

    @abstractmethod
    async def cancel(self, ref: str) -> None: ...

    async def cleanup(self, ref: str) -> None:  # noqa: B027 (optional hook)
        """Release resources (secrets, temp files) after the result is stored."""

    async def aclose(self) -> None:  # noqa: B027 (optional hook)
        pass
