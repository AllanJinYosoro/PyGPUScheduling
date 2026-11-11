from dataclasses import dataclass, field
import csv
import io
import os
import subprocess

from .policy import Scheduling


class GPUQueryError(RuntimeError):
    pass


@dataclass(frozen=True)
class GPU:
    index: int
    uuid: str
    free_mib: int
    busy: bool = False
    process_memory_mib: dict[int, int] = field(default_factory=dict)


def _query(fields: str, kind: str) -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-{kind}={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GPUQueryError(f"GPU query unavailable: {error}") from error
    if result.returncode and (kind != "gpu" or not result.stdout.strip()):
        raise GPUQueryError(result.stderr.strip() or "nvidia-smi query failed")
    return result.stdout


def discover() -> list[GPU]:
    devices = []
    for row in csv.reader(io.StringIO(_query("index,uuid,memory.free", "gpu"))):
        try:
            index, uuid, free = [part.strip() for part in row]
            if uuid.startswith("GPU-"):
                devices.append(GPU(int(index), uuid, int(free)))
        except (ValueError, TypeError):
            continue
    if not devices:
        raise GPUQueryError("nvidia-smi returned no healthy GPU records")
    processes = {}
    for row in csv.reader(io.StringIO(_query("gpu_uuid,pid,used_gpu_memory", "compute-apps"))):
        if len(row) == 3 and row[0].strip().startswith("GPU-"):
            try:
                uuid, pid, memory = [part.strip() for part in row]
                used = 0 if memory in {"N/A", "[N/A]", "[Not Supported]"} else int(memory)
                if int(pid) <= 0 or used < 0:
                    raise ValueError
                processes.setdefault(uuid, {})[int(pid)] = used
            except ValueError:
                raise GPUQueryError("could not determine GPU process memory") from None
        elif row and any(part.strip() for part in row) and row[0].strip() != "No running processes found":
            raise GPUQueryError("could not parse GPU compute-process occupancy")
    return [GPU(g.index, g.uuid, g.free_mib, g.uuid in processes, processes.get(g.uuid, {})) for g in devices]


def _matches(gpu: GPU, token: int | str) -> bool:
    return str(gpu.index) == str(token) or (
        isinstance(token, str) and token.startswith("GPU-") and gpu.uuid.startswith(token)
    )


def visible_devices(devices: list[GPU], policy: Scheduling) -> list[GPU]:
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    visible_tokens = None if visible is None else [part.strip() for part in visible.split(",")]
    result = []
    for gpu in devices:
        if visible_tokens is not None and not any(_matches(gpu, token) for token in visible_tokens):
            continue
        if policy.allowed_gpus is not None and not any(_matches(gpu, token) for token in policy.allowed_gpus):
            continue
        result.append(gpu)
    return result


def eligible(devices: list[GPU], policy: Scheduling, reserved: set[str],
             cooldown: dict[str, float], quarantined: set[str], now: float,
             memory_mib: int | None = None) -> list[GPU]:
    minimum = policy.min_free_memory_mib if memory_mib is None else memory_mib
    result = [gpu for gpu in visible_devices(devices, policy)
              if gpu.uuid not in quarantined
              and cooldown.get(gpu.uuid, 0) <= now and gpu.free_mib > minimum
              and not (policy.require_idle and (gpu.busy or gpu.uuid in reserved))]
    return sorted(result, key=lambda gpu: (-gpu.free_mib, gpu.index))
