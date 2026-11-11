from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import json
from pathlib import Path

from .task import TaskRecord


def atomic_json(path: str | Path, value: object) -> None:
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


class StateStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "scheduler.json"

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "scheduler.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f"Scheduler is still running: {self.root}") from None
            yield handle.fileno()

    def load(self) -> dict[str, TaskRecord]:
        if not self.path.exists():
            return {}
        document = json.loads(self.path.read_text())
        if document["version"] != 1:
            raise ValueError("Unsupported scheduler state version")
        records = [TaskRecord.from_dict(record) for record in document["tasks"]]
        if len({record.spec.id for record in records}) != len(records):
            raise ValueError("Duplicate task ids in scheduler state")
        if [record.sequence for record in records] != list(range(len(records))):
            raise ValueError("Invalid task sequence numbers in scheduler state")
        if any(record.status not in {"pending", "running", "committing", "complete", "failed", "blocked"}
               for record in records):
            raise ValueError("Unknown task status in scheduler state")
        return {record.spec.id: record for record in records}

    def save(self, records: dict[str, TaskRecord]) -> None:
        atomic_json(self.path, {"version": 1, "tasks": [asdict(record) for record in records.values()]})
