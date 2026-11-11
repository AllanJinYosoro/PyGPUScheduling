import importlib
from types import SimpleNamespace

import pytest

from task_scheduling import LocalRuntime, ResourceRequest, Scheduler, Scheduling, TaskSpec
from task_scheduling.gpu import GPU, GPUQueryError


@pytest.fixture
def clock(monkeypatch):
    current = [0.0]
    scheduler_module = importlib.import_module("task_scheduling.scheduler")

    def sleep(seconds):
        current[0] += seconds

    monkeypatch.setattr(scheduler_module, "time", SimpleNamespace(monotonic=lambda: current[0], sleep=sleep))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return current


def gpu_task():
    return TaskSpec("gpu", "command", resources=ResourceRequest("cuda", 4096), payload={"args": ["worker"]})


@pytest.mark.parametrize("failure", ["process", "timeout", "device"])
def test_gpu_failure_retry_and_quarantine(tmp_path, monkeypatch, clock, failure):
    launches, stopped = [], []

    def discover():
        return [] if failure == "device" and launches else [GPU(0, "GPU-a", 8192)]

    def launch(command, **kwargs):
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "GPU-a"
        assert kwargs["pass_fds"]
        launches.append(clock[0])
        return SimpleNamespace(pid=100 + len(launches), poll=lambda: 1 if failure == "process" else None)

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", launch)
    runtime = LocalRuntime(discover=discover, stop=lambda process: stopped.append(process.pid))
    scheduler = Scheduler(tmp_path, Scheduling(max_retries=1, poll_interval_seconds=1,
                          gpu_cooldown_seconds=3, task_timeout_seconds=2), runtime=runtime)
    scheduler.submit(gpu_task())
    if failure == "device":
        with pytest.raises(RuntimeError, match="quarantined"):
            scheduler.run()
        records = scheduler.tasks
        assert len(launches) == 1
    else:
        records = scheduler.run()
        assert records[0].status == "failed"
        assert len(launches) == 2 and launches[1] - launches[0] >= 3
    assert len(stopped) == len(launches)
    assert records[0].attempts[0].kind == failure


def test_gpu_query_error_waits_without_launching(tmp_path, monkeypatch, clock):
    def unavailable():
        raise GPUQueryError("driver unavailable")

    def unexpected_launch(*args, **kwargs):
        raise AssertionError("Query failure must not launch a worker")

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", unexpected_launch)
    scheduler = Scheduler(tmp_path, Scheduling(wait_seconds=2, poll_interval_seconds=1),
                          runtime=LocalRuntime(discover=unavailable))
    scheduler.submit(gpu_task())
    with pytest.raises(TimeoutError, match="No eligible GPU"):
        scheduler.run()
    assert scheduler.tasks[0].status == "pending"
    assert not scheduler.tasks[0].attempts


def test_retry_prefers_another_gpu(tmp_path, monkeypatch, clock):
    devices = [GPU(0, "GPU-a", 8192), GPU(1, "GPU-b", 8192)]

    def launch(command, **kwargs):
        return SimpleNamespace(pid=100, poll=lambda: 1)

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", launch)
    scheduler = Scheduler(tmp_path, Scheduling(max_retries=1, poll_interval_seconds=1, gpu_cooldown_seconds=0),
                          runtime=LocalRuntime(discover=lambda: devices, stop=lambda process: None))
    scheduler.submit(gpu_task())
    record, = scheduler.run()
    assert [attempt.gpu for attempt in record.attempts] == ["GPU-a", "GPU-b"]
