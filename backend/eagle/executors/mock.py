"""Fake executor that returns a canned review without calling any model.

Use EAGLE_EXECUTOR=mock to click through the whole UI flow locally.
"""

from typing import Any

from eagle.executors.base import RUNNING, Executor, RunSpec, RunState

MOCK_REVIEW = """\
## Summary
Mock review of {repo}#{pr} by {agent} ({model}). No model was called.

## Findings
1. **[minor]** This is placeholder output from the mock executor.

## Suggestions
Configure a real executor and credential in the admin panel.

VERDICT: comment
"""


class MockExecutor(Executor):
    requires_credentials = False

    def __init__(self, polls_until_done: int = 1, fail_with: str | None = None) -> None:
        self.polls_until_done = polls_until_done
        self.fail_with = fail_with
        self.launched: dict[str, RunSpec] = {}
        self.cancelled: list[str] = []
        self.cleaned: list[str] = []
        self._polls: dict[str, int] = {}
        # Tests may set a per-review result dict to return instead of the canned one.
        self.results: dict[int, dict[str, Any]] = {}

    async def launch(self, spec: RunSpec, callback: dict[str, str] | None) -> str:
        ref = f"mock:{spec.review_id}"
        self.launched[ref] = spec
        self._polls[ref] = 0
        return ref

    async def status(self, ref: str) -> RunState:
        spec = self.launched.get(ref)
        if spec is None:
            return RunState("failed", error="Unknown run")
        self._polls[ref] += 1
        if self._polls[ref] < self.polls_until_done:
            return RUNNING
        if self.fail_with:
            return RunState("failed", error=self.fail_with)
        if spec.review_id in self.results:
            return RunState("finished", result=self.results[spec.review_id])
        markdown = MOCK_REVIEW.format(
            repo=spec.clone_url, pr=spec.pr_number, agent=spec.agent, model=spec.model or "default"
        )
        return RunState(
            "finished",
            result={
                "status": "succeeded",
                "markdown": markdown,
                "head_sha": spec.head_sha,
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "cost_usd": 0.0,
            },
        )

    async def cancel(self, ref: str) -> None:
        self.cancelled.append(ref)

    async def cleanup(self, ref: str) -> None:
        self.cleaned.append(ref)
