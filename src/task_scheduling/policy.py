from dataclasses import dataclass


def integer(value: int, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class Scheduling:
    allowed_gpus: tuple[int | str, ...] | None = None
    min_free_memory_mib: int = 4096
    memory_margin_mib: int = 0
    max_parallel_tasks: int = 3
    require_idle: bool = False
    max_retries: int = 3
    poll_interval_seconds: int = 10
    gpu_cooldown_seconds: int = 60
    task_timeout_seconds: int = 0
    wait_seconds: int = 3600

    def __post_init__(self) -> None:
        for name in ("min_free_memory_mib", "memory_margin_mib", "max_retries",
                     "gpu_cooldown_seconds", "task_timeout_seconds"):
            integer(getattr(self, name), name)
        for name in ("max_parallel_tasks", "poll_interval_seconds", "wait_seconds"):
            integer(getattr(self, name), name, 1)
        if type(self.require_idle) is not bool:
            raise ValueError("require_idle must be a boolean")
        if self.allowed_gpus is not None:
            allowed = self.allowed_gpus
            if not isinstance(allowed, (list, tuple)) or not allowed:
                raise ValueError("allowed_gpus must be null or a nonempty list of GPU indices/UUIDs")
            if any(not ((type(gpu) is int and gpu >= 0) or
                        (isinstance(gpu, str) and gpu.startswith("GPU-") and len(gpu) > 4))
                   for gpu in allowed):
                raise ValueError("allowed_gpus entries must be nonnegative indices or GPU- UUIDs")
            if len(set(allowed)) != len(allowed):
                raise ValueError("allowed_gpus must not contain duplicates")
            object.__setattr__(self, "allowed_gpus", tuple(allowed))

    @classmethod
    def from_settings(cls, settings: dict) -> "Scheduling":
        if not isinstance(settings, dict) or set(settings) - cls.__dataclass_fields__.keys():
            raise ValueError("Unknown scheduling settings")
        return cls(**settings)
