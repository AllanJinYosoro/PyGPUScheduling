from dataclasses import dataclass, replace

from .gpu import GPU, eligible, visible_devices
from .policy import Scheduling


@dataclass(frozen=True)
class Reservation:
    gpu_uuid: str
    memory_mib: int
    pid: int | None = None


class MemoryPool:
    def __init__(self, policy: Scheduling):
        self.policy = policy
        self.devices: list[GPU] = []
        self.reservations: dict[int, Reservation] = {}

    def refresh(self, devices: list[GPU]) -> None:
        self.devices = visible_devices(devices, self.policy)

    def available(self, gpu: GPU) -> int:
        reserved = sum(max(0, reservation.memory_mib - gpu.process_memory_mib.get(reservation.pid, 0))
                       for reservation in self.reservations.values() if reservation.gpu_uuid == gpu.uuid)
        return max(0, gpu.free_mib - self.policy.memory_margin_mib - reserved)

    def candidates(self, memory_mib: int, cooldown: dict[str, float], quarantined: set[str],
                   now: float, last_gpu: str | None = None) -> list[GPU]:
        reserved = {reservation.gpu_uuid for reservation in self.reservations.values()}
        devices = eligible(self.devices, self.policy, reserved, cooldown, quarantined, now, memory_mib=0)
        return sorted((gpu for gpu in devices if self.available(gpu) >= memory_mib),
                      key=lambda gpu: (gpu.uuid == last_gpu, self.available(gpu), gpu.index))

    def reserve(self, identifier: int, gpu: GPU, memory_mib: int) -> None:
        self.reservations[identifier] = Reservation(gpu.uuid, memory_mib)

    def started(self, identifier: int, pid: int) -> None:
        self.reservations[identifier] = replace(self.reservations[identifier], pid=pid)

    def release(self, identifier: int) -> None:
        self.reservations.pop(identifier, None)

    def summary(self) -> str:
        return ", ".join(f"GPU{gpu.index} available {self.available(gpu) / 1024:g}GiB" for gpu in self.devices)
