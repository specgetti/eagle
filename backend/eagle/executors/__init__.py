from eagle.config import Settings
from eagle.executors.base import Executor, RunSpec, RunState


def create_executor(settings: Settings) -> Executor:
    match settings.executor:
        case "kubernetes":
            from eagle.executors.kubernetes import KubernetesExecutor

            return KubernetesExecutor(settings)
        case "mock":
            from eagle.executors.mock import MockExecutor

            return MockExecutor()
        case _:
            from eagle.executors.local import LocalExecutor

            return LocalExecutor(settings.local_workdir)


__all__ = ["Executor", "RunSpec", "RunState", "create_executor"]
