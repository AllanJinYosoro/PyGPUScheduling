from collections import deque
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Callable, Iterable, TextIO

from .gpu import GPU, GPUQueryError, visible_devices
from .memory import MemoryPool
from .policy import Scheduling
from .progress import TaskProgress, WorkerStatus
from .runtime import LocalRuntime
from .storage import StateStore
from .task import (
    Assessment, Attempt, CommandHandler, TaskContext, TaskEvent, TaskHandler,
    TaskOutcome, TaskRecord, TaskSpec,
)


@dataclass
class RunningTask:
    process: subprocess.Popen
    gpu: GPU | None
    lock: TextIO | None
    directory: Path
    started: float
    missing: int = 0


class Scheduler:
    def __init__(self, root: str | Path, policy: Scheduling | None = None, *,
                 runtime: LocalRuntime | None = None, inherited_fds: tuple[int, ...] = (),
                 lock_namespace: str = "task-scheduling", label: str = "Tasks"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", lock_namespace):
            raise ValueError("Lock namespace must contain only letters, digits, underscores and hyphens")
        self.root = Path(root).resolve()
        self.policy = policy or Scheduling()
        self.runtime = runtime or LocalRuntime()
        self.label = label
        self.lock_namespace = lock_namespace
        self.inherited_fds = inherited_fds
        self.store = StateStore(self.root)
        self.memory = MemoryPool(self.policy)
        self._records: dict[str, TaskRecord] = {}
        self._handlers: dict[str, TaskHandler] = {"command": CommandHandler()}
        self._listeners: dict[str, Callable[[TaskEvent], None]] = {}
        self._observers: list[Callable[[tuple[TaskRecord, ...]], None]] = []
        self._running: dict[str, RunningTask] = {}
        self._cooldown: dict[str, float] = {}
        self._quarantined: set[str] = set()
        self._known: set[str] = set()
        self._reconciled: set[str] = set()
        self._session: ExitStack | None = None
        self._lock_fd: int | None = None
        self._event = "Preparing tasks"
        self._query_error = ""
        self._next_scan = 0.0

    @property
    def tasks(self) -> tuple[TaskRecord, ...]:
        return tuple(deepcopy(record) for record in self._records.values())

    @property
    def has_saved_state(self) -> bool:
        return self.store.path.exists()

    def __enter__(self) -> "Scheduler":
        if self._session is not None:
            raise RuntimeError("Scheduler session is already open")
        session = ExitStack()
        try:
            self._lock_fd = session.enter_context(self.store.lock())
            submitted = [record.spec for record in self._records.values()]
            self._records = self.store.load()
            self.memory = MemoryPool(self.policy)
            self._cooldown.clear()
            self._quarantined.clear()
            self._known.clear()
            self._next_scan = 0.0
            self._session = session
            self.submit_many(submitted)
        except BaseException:
            self._session = None
            session.close()
            raise
        return self

    def __exit__(self, *exception) -> None:
        try:
            if self._running:
                self._interrupt_running()
                self._save()
        finally:
            session, self._session = self._session, None
            self._lock_fd = None
            self._reconciled.clear()
            if session is not None:
                session.__exit__(*exception)

    def register_handler(self, name: str, handler: TaskHandler) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Handler name must be a nonempty string")
        if name in self._handlers:
            raise ValueError(f"Handler already registered: {name}")
        if any(not callable(getattr(handler, method, None)) for method in ("prepare", "assess", "commit", "recover")):
            raise TypeError("Handlers must implement prepare, assess, commit and recover")
        self._handlers[name] = handler

    def subscribe(self, name: str, listener: Callable[[TaskEvent], None]) -> None:
        if not isinstance(name, str) or not name.strip() or name in self._listeners:
            raise ValueError(f"Listener name is empty or already registered: {name}")
        if not callable(listener):
            raise TypeError("Event listeners must be callable")
        self._listeners[name] = listener

    def observe(self, observer: Callable[[tuple[TaskRecord, ...]], None]) -> None:
        if not callable(observer):
            raise TypeError("State observers must be callable")
        self._observers.append(observer)

    def submit(self, task: TaskSpec) -> None:
        self.submit_many([task])

    def submit_many(self, tasks: Iterable[TaskSpec]) -> None:
        submitted = [TaskSpec.from_dict(json.loads(json.dumps(asdict(task), allow_nan=False))) for task in tasks]
        specs = {identifier: record.spec for identifier, record in self._records.items()}
        for task in submitted:
            if task.id in specs and specs[task.id].identity() != task.identity():
                raise ValueError(f"Task definition changed: {task.id}")
            existing = self._records.get(task.id)
            if existing is not None and task.id in self._running and existing.spec != task:
                raise ValueError(f"Cannot update an executing task: {task.id}")
            specs[task.id] = task
        self._validate_graph(specs)
        for task in submitted:
            if task.id in self._records:
                self._records[task.id].spec = task
            else:
                self._records[task.id] = TaskRecord(task, len(self._records))
        if submitted:
            self._save()

    def import_records(self, records: Iterable[TaskRecord]) -> None:
        self._require_session()
        if self._records or self.store.path.exists():
            raise ValueError("Recovery records can only be imported into a new scheduler")
        imported = deepcopy(list(records))
        if len({record.spec.id for record in imported}) != len(imported):
            raise ValueError("Duplicate task ids in recovery records")
        if [record.sequence for record in imported] != list(range(len(imported))):
            raise ValueError("Recovery records must have contiguous sequence numbers")
        if any(record.status not in {"pending", "running", "committing", "complete", "failed", "blocked"}
               for record in imported):
            raise ValueError("Unknown task status in recovery records")
        self._validate_graph({record.spec.id: record.spec for record in imported})
        self._records = {record.spec.id: record for record in imported}
        self._save()

    def reconcile(self) -> None:
        self._require_session()
        if self._running:
            raise RuntimeError("Cannot reconcile while tasks are running")
        order = self._validate_graph({identifier: record.spec for identifier, record in self._records.items()})
        self._check_results(order)
        for identifier in order:
            record = self._records[identifier]
            if record.spec.id in self._reconciled:
                continue
            if record.revision and record.status == "pending" and not record.current_attempts:
                self._reconciled.add(identifier)
                continue
            handler = self._handler(record)
            recovery = handler.recover(self._context(record))
            if recovery is not None:
                record.status = recovery.status
                record.result = deepcopy(recovery.result)
                if record.status == "complete" and record.attempts:
                    record.attempts[-1].status = "complete"
            elif record.status == "complete":
                raise ValueError(f"Completed task could not be recovered: {record.spec.id}")
            elif record.status == "committing":
                raise ValueError(f"Task publication could not be recovered: {record.spec.id}")
            else:
                for attempt in record.attempts:
                    if attempt.status == "running":
                        attempt.status = "interrupted"
                record.status = "pending" if record.can_retry(self._policy(record).max_retries) else "failed"
            self._reconciled.add(record.spec.id)
            self._save()
            if record.status == "committing":
                self._commit(record)

    def _check_results(self, order: tuple[str, ...]) -> None:
        invalid = set()
        for identifier in order:
            record = self._records[identifier]
            if any(dependency in invalid for dependency in record.spec.dependencies):
                invalid.add(identifier)
                continue
            if identifier in self._reconciled or record.status != "complete":
                continue
            check = getattr(self._handler(record), "check_result", None)
            if check is not None:
                valid = check(self._context(record))
                if type(valid) is not bool:
                    raise TypeError("Result checks must return a boolean")
                if not valid:
                    invalid.add(identifier)
        if not invalid:
            return
        for identifier in invalid:
            record = self._records[identifier]
            for attempt in record.attempts:
                if attempt.status == "running":
                    attempt.status = "interrupted"
            record.revision += 1
            record.status = "pending"
            record.result.clear()
            record.acknowledged.clear()
        self._reconciled.update(invalid)
        self._save()

    def run(self) -> tuple[TaskRecord, ...]:
        if self._session is None:
            with self:
                return self.run()
        self.reconcile()
        self._deliver_events()
        deadline = time.monotonic() + self.policy.wait_seconds
        display_records = [{"status": record.status, "attempts": record.current_attempts}
                           for record in self._records.values()]
        with TaskProgress(display_records, self.policy.max_parallel_tasks, self.policy.max_retries,
                          label=self.label) as progress:
            try:
                while self._has_work():
                    now = time.monotonic()
                    devices = self._scan(now)
                    if self._finish_tasks(devices, now):
                        deadline = now + self.policy.wait_seconds
                    self._block_dependents()
                    self._deliver_events()
                    self._start_tasks(devices, now)
                    if self._pending() and not self._running:
                        self._check_wait(now, deadline)
                    display_records[:] = [{"status": record.status,
                                           "attempts": record.current_attempts} for record in self._records.values()]
                    self._render(progress)
                    if self._has_work():
                        time.sleep(1)
            finally:
                self._interrupt_running()
                self._save()
        self._deliver_events()
        return self.tasks

    @staticmethod
    def _validate_graph(specs: dict[str, TaskSpec]) -> tuple[str, ...]:
        remaining = {identifier: len(spec.dependencies) for identifier, spec in specs.items()}
        dependents = {identifier: [] for identifier in specs}
        for identifier, spec in specs.items():
            for dependency in spec.dependencies:
                if dependency not in specs:
                    raise ValueError(f"Unknown dependency: {dependency}")
                dependents[dependency].append(identifier)
        ready = deque(identifier for identifier, count in remaining.items() if count == 0)
        order = []
        while ready:
            identifier = ready.popleft()
            order.append(identifier)
            for dependent in dependents[identifier]:
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    ready.append(dependent)
        if len(order) != len(specs):
            raise ValueError("Task dependency cycle")
        return tuple(order)

    def _require_session(self) -> None:
        if self._session is None:
            raise RuntimeError("Open a scheduler session before recovering tasks")

    def _save(self) -> None:
        if self._session is None:
            return
        self.store.save(self._records)
        for observer in self._observers:
            observer(self.tasks)

    def _handler(self, record: TaskRecord) -> TaskHandler:
        if record.spec.handler not in self._handlers:
            raise ValueError(f"Handler is not registered: {record.spec.handler}")
        return self._handlers[record.spec.handler]

    def _context(self, record: TaskRecord) -> TaskContext:
        return TaskContext(deepcopy(record.spec), self.root, record.status, tuple(deepcopy(record.attempts)),
                           deepcopy(record.result), record.revision)

    def _policy(self, record: TaskRecord) -> Scheduling:
        return record.spec.execution.resolve(self.policy)

    def _has_work(self) -> bool:
        return bool(self._running) or any(record.status in {"pending", "committing"} for record in self._records.values())

    def _pending(self) -> list[TaskRecord]:
        return [record for record in self._records.values() if record.status == "pending"]

    def _ready(self) -> list[TaskRecord]:
        return sorted((record for record in self._pending()
                       if all(self._records[dependency].status == "complete" for dependency in record.spec.dependencies)),
                      key=lambda record: (-record.spec.priority, -record.spec.resources.memory_mib, record.sequence))

    def _scan(self, now: float) -> list[GPU] | None:
        if now < self._next_scan or not any(record.spec.resources.device == "cuda" for record in self._records.values()):
            return None
        self._next_scan = now + self.policy.poll_interval_seconds
        try:
            devices = self.runtime.discover()
            self.memory.refresh(devices)
            self._known.update(gpu.uuid for gpu in visible_devices(devices, self.policy))
            self._query_error = ""
            return devices
        except GPUQueryError as error:
            self._query_error = str(error)
            return None

    def _finish_tasks(self, devices: list[GPU] | None, now: float) -> bool:
        finished = False
        for identifier, running in list(self._running.items()):
            record = self._records[identifier]
            if devices is not None and running.gpu is not None:
                running.missing = 0 if any(gpu.uuid == running.gpu.uuid for gpu in devices) else running.missing + 1
            lost = running.missing >= 2
            limit = self._policy(record).task_timeout_seconds
            timeout = limit > 0 and now - running.started > limit
            code = running.process.poll()
            if code is None and not lost and not timeout:
                continue
            self.runtime.stop(running.process)
            self._release(identifier, running)
            if lost or timeout:
                assessment = Assessment(False, True, "device" if lost else "timeout",
                                        "GPU disappeared" if lost else "task timed out")
            else:
                assessment = self._handler(record).assess(self._context(record), TaskOutcome(code))
            if assessment.success:
                record.status = "committing"
                self._save()
                self._commit(record)
            else:
                self._fail(record, assessment, running.gpu, now)
            finished = True
        return finished

    def _commit(self, record: TaskRecord) -> None:
        result = self._handler(record).commit(self._context(record))
        json.dumps(result, allow_nan=False)
        if not isinstance(result, dict):
            raise TypeError("Task commit must return a JSON object")
        record.result = deepcopy(result)
        record.status = "complete"
        if record.attempts:
            record.attempts[-1].status = "complete"
        self._event = f"{record.spec.name} completed"
        self._save()

    def _fail(self, record: TaskRecord, assessment: Assessment, gpu: GPU | None, now: float) -> None:
        attempt = record.attempts[-1]
        attempt.status, attempt.kind, attempt.message = "failed", assessment.kind, assessment.message
        attempt.retryable = assessment.retryable
        retry = record.can_retry(self._policy(record).max_retries)
        record.status = "pending" if retry else "failed"
        if gpu is not None:
            if assessment.kind == "device":
                self._quarantined.add(gpu.uuid)
            else:
                self._cooldown[gpu.uuid] = now + self.policy.gpu_cooldown_seconds
        self._event = f"{record.spec.name}: {assessment.message}; {'retry queued' if retry else 'failed'}"
        self._save()

    def _acquire_gpu(self, record: TaskRecord, now: float) -> tuple[GPU | None, TextIO | None]:
        last_gpu = record.attempts[-1].gpu if record.attempts else None
        for gpu in self.memory.candidates(record.spec.resources.memory_mib, self._cooldown,
                                          self._quarantined, now, last_gpu):
            path = Path(tempfile.gettempdir()) / f"{self.lock_namespace}-{os.getuid()}-{gpu.uuid}.lock"
            handle = path.open("a")
            try:
                fcntl.flock(handle, (fcntl.LOCK_EX if self.policy.require_idle else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                continue
            self.memory.reserve(record.sequence, gpu, record.spec.resources.memory_mib)
            return gpu, handle
        return None, None

    def _start_tasks(self, devices: list[GPU] | None, now: float) -> None:
        for record in self._ready():
            if len(self._running) >= self.policy.max_parallel_tasks:
                break
            gpu, lock = None, None
            if record.spec.resources.device == "cuda":
                if devices is None:
                    continue
                gpu, lock = self._acquire_gpu(record, now)
                if gpu is None:
                    continue
            self._launch(record, gpu, lock, now)

    def _launch(self, record: TaskRecord, gpu: GPU | None, lock: TextIO | None, now: float) -> None:
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", record.spec.name)[:80]
        directory = self.root / f"{record.sequence:03d}-{name}" / f"{len(record.attempts) + 1:04d}"
        process = None
        try:
            record.status = "running"
            record.attempts.append(Attempt(str(directory), gpu=None if gpu is None else gpu.uuid,
                                           memory_mib=record.spec.resources.memory_mib,
                                           revision=record.revision))
            self._save()
            directory.mkdir(parents=True)
            command = self._handler(record).prepare(self._context(record))
            inherited = (*self.inherited_fds, self._lock_fd)
            if lock is not None:
                inherited = (*inherited, lock.fileno())
            process = self.runtime.launch(command, directory, gpu, inherited)
            record.attempts[-1].pid = process.pid
            if gpu is not None:
                self.memory.started(record.sequence, process.pid)
            self._running[record.spec.id] = RunningTask(process, gpu, lock, directory, now)
        except BaseException:
            if process is not None:
                self.runtime.stop(process)
            self.memory.release(record.sequence)
            if lock is not None:
                lock.close()
            record.status = "pending"
            record.attempts[-1].status = "interrupted"
            raise
        self._event = (f"{record.spec.name} started on {'CPU' if gpu is None else 'GPU' + str(gpu.index)} "
                       f"PID{process.pid} budget {record.spec.resources.memory_mib / 1024:g}GiB")
        self._save()

    def _release(self, identifier: str, running: RunningTask) -> None:
        self.memory.release(self._records[identifier].sequence)
        if running.lock is not None:
            running.lock.close()
        del self._running[identifier]

    def _interrupt_running(self) -> None:
        for identifier, running in list(self._running.items()):
            self.runtime.stop(running.process)
            self._release(identifier, running)
            record = self._records[identifier]
            record.status = "pending"
            record.attempts[-1].status = "interrupted"

    def _block_dependents(self) -> None:
        changed = True
        while changed:
            changed = False
            for record in self._pending():
                if any(self._records[dependency].status in {"failed", "blocked"} for dependency in record.spec.dependencies):
                    record.status = "blocked"
                    changed = True
                    self._save()

    def _deliver_events(self) -> None:
        for record in list(self._records.values()):
            if record.status not in {"complete", "failed", "blocked"}:
                continue
            event_id = f"{record.spec.id}:{len(record.attempts)}:{record.status}"
            if record.revision:
                event_id = json.dumps((record.spec.id, record.revision, len(record.attempts), record.status),
                                      separators=(",", ":"))
            for name, listener in self._listeners.items():
                if record.acknowledged.get(name) == event_id:
                    continue
                listener(TaskEvent(event_id, deepcopy(record.spec), record.status,
                                   tuple(deepcopy(record.attempts)), deepcopy(record.result), record.revision))
                record.acknowledged[name] = event_id
                self._save()

    def _check_wait(self, now: float, deadline: float) -> None:
        self._event = "Waiting for eligible GPUs" + (f": {self._query_error}" if self._query_error else "")
        if self._known and self._known <= self._quarantined:
            raise RuntimeError("All allowed GPUs quarantined; repair GPUs before rerunning")
        if now > deadline:
            raise TimeoutError("No eligible GPU became available before timeout")

    def _render(self, progress: TaskProgress) -> None:
        workers = []
        for identifier, running in self._running.items():
            state = {"current": 0, "total": 0, "phase": "initializing", "unit": "step"}
            try:
                state.update(json.loads((running.directory / "progress.json").read_text()))
            except (OSError, ValueError):
                pass
            record = self._records[identifier]
            workers.append(WorkerStatus(record.sequence, running.process.pid,
                "CPU" if running.gpu is None else f"GPU{running.gpu.index}",
                sum(attempt.status != "interrupted" for attempt in record.current_attempts),
                state["current"], state["total"], f"{record.spec.name} {state['phase']}", running.started,
                state["unit"], record.spec.resources.memory_mib, self._policy(record).max_retries + 1))
        progress.render(workers, f"{self.memory.summary()}; {self._event}" if self.memory.devices else self._event)
