from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable, Literal, Protocol

from .policy import ExecutionPolicy, integer


TaskStatus = Literal["pending", "running", "committing", "complete", "failed", "blocked"]


@dataclass(frozen=True)
class ResourceRequest:
    device: Literal["cpu", "cuda"] = "cpu"
    memory_mib: int = 0

    def __post_init__(self) -> None:
        if self.device not in {"cpu", "cuda"}:
            raise ValueError("Task devices must be cpu or cuda")
        integer(self.memory_mib, "memory_mib")
        if self.device == "cpu" and self.memory_mib:
            raise ValueError("CPU tasks cannot reserve GPU memory")


@dataclass(frozen=True)
class TaskSpec:
    id: str
    handler: str
    name: str = "task"
    payload: dict = field(default_factory=dict)
    dependencies: tuple[str, ...] = ()
    priority: int = 0
    resources: ResourceRequest = field(default_factory=ResourceRequest)
    execution: ExecutionPolicy = field(default_factory=ExecutionPolicy)

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.id, self.handler, self.name)):
            raise ValueError("Task id, handler and name must be nonempty strings")
        if type(self.priority) is not int:
            raise ValueError("Task priority must be an integer")
        if not isinstance(self.dependencies, (list, tuple)) or any(
            not isinstance(value, str) or not value for value in self.dependencies
        ):
            raise ValueError("Task dependencies must contain task ids")
        if self.id in self.dependencies or len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Task dependencies must be unique and cannot reference the task itself")
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if not isinstance(self.payload, dict):
            raise ValueError("Task payload must be a JSON object")
        if not isinstance(self.resources, ResourceRequest):
            raise TypeError("Task resources must be a ResourceRequest")
        if not isinstance(self.execution, ExecutionPolicy):
            raise TypeError("Task execution must be an ExecutionPolicy")
        json.dumps(self.payload, allow_nan=False)

    def identity(self) -> dict:
        return {"id": self.id, "handler": self.handler, "payload": self.payload,
                "dependencies": list(self.dependencies)}

    @classmethod
    def from_dict(cls, values: dict) -> "TaskSpec":
        return cls(**{**values, "resources": ResourceRequest(**values["resources"]),
                      "execution": ExecutionPolicy(**values.get("execution", {}))})


@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    cwd: str | Path | None = None
    env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.args, (list, tuple)) or not self.args or any(
            not isinstance(value, str) for value in self.args
        ):
            raise ValueError("Command args must be a nonempty list of strings")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise ValueError("Command environment must map strings to strings")
        object.__setattr__(self, "args", tuple(self.args))


@dataclass
class Attempt:
    directory: str
    status: str = "running"
    gpu: str | None = None
    pid: int | None = None
    memory_mib: int = 0
    kind: str = ""
    message: str = ""
    retryable: bool = False
    revision: int = 0


@dataclass
class TaskRecord:
    spec: TaskSpec
    sequence: int
    status: TaskStatus = "pending"
    attempts: list[Attempt] = field(default_factory=list)
    result: dict = field(default_factory=dict)
    acknowledged: dict[str, str] = field(default_factory=dict)
    revision: int = 0

    @property
    def current_attempts(self) -> tuple[Attempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.revision == self.revision)

    def can_retry(self, max_retries: int) -> bool:
        failures = [attempt for attempt in self.current_attempts if attempt.status == "failed"]
        return all(attempt.retryable for attempt in failures) and len(failures) <= max_retries

    @classmethod
    def from_dict(cls, values: dict) -> "TaskRecord":
        return cls(**{**values, "spec": TaskSpec.from_dict(values["spec"]),
                      "attempts": [Attempt(**attempt) for attempt in values["attempts"]]})


@dataclass(frozen=True)
class TaskContext:
    task: TaskSpec
    root: Path
    status: TaskStatus
    attempts: tuple[Attempt, ...]
    result: dict = field(default_factory=dict)
    revision: int = 0

    @property
    def current_attempts(self) -> tuple[Attempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.revision == self.revision)

    @property
    def directory(self) -> Path:
        return Path(self.current_attempts[-1].directory) if self.current_attempts else self.root

    @property
    def gpu(self) -> str | None:
        return self.current_attempts[-1].gpu if self.current_attempts else None


@dataclass(frozen=True)
class TaskOutcome:
    returncode: int | None
    timed_out: bool = False
    device_lost: bool = False


@dataclass(frozen=True)
class Assessment:
    success: bool
    retryable: bool = False
    kind: str = "process"
    message: str = ""

    def __post_init__(self) -> None:
        if type(self.success) is not bool or type(self.retryable) is not bool:
            raise TypeError("Assessment success and retryable must be booleans")
        if not isinstance(self.kind, str) or not isinstance(self.message, str):
            raise TypeError("Assessment kind and message must be strings")


@dataclass(frozen=True)
class Recovery:
    status: Literal["complete", "failed", "committing"]
    result: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "committing"}:
            raise ValueError("Recovery status must be complete, failed or committing")
        if not isinstance(self.result, dict):
            raise TypeError("Recovery result must be a JSON object")
        json.dumps(self.result, allow_nan=False)


@dataclass(frozen=True)
class TaskEvent:
    id: str
    task: TaskSpec
    status: TaskStatus
    attempts: tuple[Attempt, ...]
    result: dict
    revision: int = 0


class TaskHandler(Protocol):
    def prepare(self, context: TaskContext) -> Command: ...

    def assess(self, context: TaskContext, outcome: TaskOutcome) -> Assessment: ...

    def commit(self, context: TaskContext) -> dict: ...

    def recover(self, context: TaskContext) -> Recovery | None: ...


class CommandHandler:
    def prepare(self, context: TaskContext) -> Command:
        return Command(**context.task.payload)

    def assess(self, context: TaskContext, outcome: TaskOutcome) -> Assessment:
        if outcome.device_lost:
            return Assessment(False, True, "device", "GPU disappeared")
        if outcome.timed_out:
            return Assessment(False, True, "timeout", "task timed out")
        return Assessment(outcome.returncode == 0, True, "process", f"worker exited {outcome.returncode}")

    def commit(self, context: TaskContext) -> dict:
        return {}

    def recover(self, context: TaskContext) -> Recovery | None:
        if context.status == "committing":
            return Recovery("committing")
        if context.status == "complete":
            return Recovery("complete", context.result)
        return None


class ArtifactHandler(CommandHandler):
    def __init__(self, *, check_attempt: Callable[[TaskContext], bool] | None = None,
                 check_result: Callable[[TaskContext], bool] | None = None):
        if any(check is not None and not callable(check) for check in (check_attempt, check_result)):
            raise TypeError("Artifact checks must be callable")
        self._check_attempt = check_attempt
        self._check_result = check_result

    def assess(self, context: TaskContext, outcome: TaskOutcome) -> Assessment:
        assessment = super().assess(context, outcome)
        if assessment.success and not self._check(self._check_attempt, context):
            return Assessment(False, False, "artifact", "Attempt artifacts failed validation")
        return assessment

    def check_result(self, context: TaskContext) -> bool:
        return self._check(self._check_result, context)

    @staticmethod
    def _check(check: Callable[[TaskContext], bool] | None, context: TaskContext) -> bool:
        valid = True if check is None else check(context)
        if type(valid) is not bool:
            raise TypeError("Artifact checks must return a boolean")
        return valid
