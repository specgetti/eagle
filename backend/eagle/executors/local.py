"""Runs reviews as local subprocesses. For development and single-node setups only:
there is no isolation from the worker beyond a minimal environment."""

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from eagle.executors.base import RUNNING, Executor, RunSpec, RunState

LOG_TAIL = 4000


@dataclass
class _Run:
    process: asyncio.subprocess.Process
    directory: Path

    @property
    def result_path(self) -> Path:
        return self.directory / "result.json"

    @property
    def log_path(self) -> Path:
        return self.directory / "runner.log"


class LocalExecutor(Executor):
    def __init__(self, workdir: str | None = None, python: str = sys.executable) -> None:
        self._workdir = workdir
        self._python = python
        self._runs: dict[str, _Run] = {}

    async def launch(self, spec: RunSpec, callback: dict[str, str] | None) -> str:
        if self._workdir:
            Path(self._workdir).mkdir(parents=True, exist_ok=True)
        directory = Path(
            tempfile.mkdtemp(prefix=f"eagle-review-{spec.review_id}-", dir=self._workdir)
        )
        spec_path = directory / "spec.json"
        spec_path.write_text(json.dumps(spec.to_dict(result_path=str(directory / "result.json"))))
        spec_path.chmod(0o600)

        # Do not leak the worker's own secrets (DB URL, keys) into the runner.
        env = {k: v for k, v in os.environ.items() if not k.startswith("EAGLE_")}
        log = (directory / "runner.log").open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                self._python,
                "-m",
                "eagle.runner",
                str(spec_path),
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        finally:
            log.close()
        ref = f"local:{spec.review_id}:{process.pid}"
        self._runs[ref] = _Run(process=process, directory=directory)
        return ref

    async def status(self, ref: str) -> RunState:
        run = self._runs.get(ref)
        if run is None:
            return RunState("failed", error="Run was lost (the worker restarted)")
        if run.process.returncode is None:
            return RUNNING
        if run.result_path.is_file():
            return RunState("finished", result=json.loads(run.result_path.read_text()))
        log = run.log_path.read_text(errors="replace")[-LOG_TAIL:] if run.log_path.exists() else ""
        return RunState(
            "failed", error=f"Runner exited with code {run.process.returncode}: {log.strip()}"
        )

    async def cancel(self, ref: str) -> None:
        run = self._runs.get(ref)
        if run is None:
            return
        if run.process.returncode is None:
            run.process.terminate()
            try:
                await asyncio.wait_for(run.process.wait(), timeout=10)
            except TimeoutError:
                run.process.kill()
                await run.process.wait()
        await self.cleanup(ref)

    async def cleanup(self, ref: str) -> None:
        run = self._runs.pop(ref, None)
        if run is not None:
            shutil.rmtree(run.directory, ignore_errors=True)

    async def aclose(self) -> None:
        for ref in list(self._runs):
            with contextlib.suppress(ProcessLookupError):
                await self.cancel(ref)
