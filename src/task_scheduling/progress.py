from contextlib import ExitStack
from dataclasses import dataclass
import shutil
import sys
import time
from typing import TextIO


@dataclass(frozen=True)
class WorkerStatus:
    identifier: int
    pid: int
    device: str
    attempt: int
    current: int
    total: int
    phase: str
    started: float
    unit: str = "epoch"
    memory_mib: int = 0
    max_attempts: int | None = None


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


class TaskProgress:

    def __init__(self, records: list[dict], slots: int, max_retries: int,
                 *, label: str = "Tasks", stream: TextIO | None = None):
        self.label = label
        self.records, self.slots, self.max_attempts = records, slots, max_retries + 1
        self.stream = sys.stderr if stream is None else stream
        self.interactive = self.stream.isatty()
        self.started = time.monotonic()
        self.initial_completed = sum(record["status"] == "complete" for record in records)
        self.slot_ids: list[int | None] = [None] * slots
        self.lines: list = []
        self.last_signature = None
        self.last_printed = float("-inf")
        self.stack = ExitStack()

    def __enter__(self):
        try:
            from tqdm.auto import tqdm
        except ImportError:
            self.interactive = False
        if self.interactive:
            try:
                for position in range(self.slots + 2):
                    self.lines.append(self.stack.enter_context(tqdm(
                        total=0, bar_format="{desc}", position=position,
                        leave=position == 0, dynamic_ncols=True, file=self.stream,
                    )))
            except BaseException:
                self.stack.close()
                raise
        return self

    def __exit__(self, *exception):
        return self.stack.__exit__(*exception)

    def snapshot(self, workers: list[WorkerStatus], event: str, *, now: float,
                 width: int) -> list[str]:
        by_id = {worker.identifier: worker for worker in workers}
        self.slot_ids = [identifier if identifier in by_id else None for identifier in self.slot_ids]
        for worker in workers:
            if worker.identifier not in self.slot_ids:
                self.slot_ids[self.slot_ids.index(None)] = worker.identifier

        completed = sum(record["status"] == "complete" for record in self.records)
        failed = sum(record["status"] in {"failed", "blocked"} for record in self.records)
        pending = sum(record["status"] == "pending" for record in self.records)
        retries = sum(max(0, len(record.get("attempts", [])) - 1) for record in self.records)
        total = len(self.records)
        elapsed = max(0, now - self.started)
        fresh_completed = completed - self.initial_completed
        remaining = total - completed - failed
        if remaining == 0:
            eta = "0s"
        elif fresh_completed > 0 and elapsed > 0:
            eta = "~" + duration(remaining * elapsed / fresh_completed)
        else:
            eta = "estimating"
        count = f"{self.label} {completed}/{total}"
        fraction = completed / total if total else 1
        tail = (f" {fraction:.0%} | run{len(workers)}/{self.slots} wait{pending} fail{failed} retry{retries}"
                f" | t{duration(elapsed)} ETA {eta}")
        filled = int(20 * fraction)
        bar = " [" + "#" * filled + "-" * (20 - filled) + "]"
        header = count + (bar if len(count + bar + tail) <= width else "") + tail
        result = [header]
        for index, identifier in enumerate(self.slot_ids, start=1):
            if identifier is None:
                result.append(f"Slot {index} | idle")
                continue
            worker = by_id[identifier]
            budget = f" budget{worker.memory_mib / 1024:g}GiB" if worker.memory_mib else ""
            result.append(
                f"Slot {index} | {worker.device} PID{worker.pid}{budget} | {self.label} {identifier:03d} | "
                f"try{worker.attempt}/{worker.max_attempts or self.max_attempts} | {worker.unit}{worker.current}/{worker.total} | "
                f"{worker.phase} | elapsed {duration(now - worker.started)}"
            )
        result.append("Latest: " + " ".join(event.split()))
        return [line if len(line) <= width else line[:max(0, width - 3)] + "..." for line in result]

    def render(self, workers: list[WorkerStatus], event: str) -> None:
        now = time.monotonic()
        width = max(20, shutil.get_terminal_size(fallback=(120, 24)).columns) if self.interactive else 160
        frame = self.snapshot(workers, event, now=now, width=width)
        if self.interactive:
            for line, text in zip(self.lines, frame):
                line.set_description_str(text, refresh=False)
                line.refresh()
        else:
            signature = (tuple((r["status"], len(r.get("attempts", []))) for r in self.records),
                         tuple((w.identifier, w.pid, w.current, w.phase) for w in workers), event)
            if signature != self.last_signature or now - self.last_printed >= 60:
                self.stream.write("\n".join(frame) + "\n")
                self.stream.flush()
                self.last_printed, self.last_signature = now, signature
