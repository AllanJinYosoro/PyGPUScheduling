from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest

from task_scheduling import ExecutionPolicy, LocalRuntime, Scheduler, Scheduling, TaskSpec


def task(identifier, **overrides):
    return TaskSpec(identifier, "command", payload={"args": [identifier]},
                    execution=ExecutionPolicy(**overrides))


def test_partial_retry_overrides_and_progress(tmp_path, monkeypatch, clock, capsys):
    launches = Counter()

    def launch(command, **kwargs):
        launches[command[0]] += 1
        return SimpleNamespace(pid=100, poll=lambda: 1)

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", launch)
    scheduler = Scheduler(tmp_path, Scheduling(max_retries=2), runtime=LocalRuntime(stop=lambda process: None))
    scheduler.submit_many([task("inherited"), task("disabled", max_retries=0), task("extended", max_retries=4)])
    assert all(record.status == "failed" for record in scheduler.run())
    assert launches == {"inherited": 3, "disabled": 1, "extended": 5}
    output = capsys.readouterr().err
    assert "try1/1" in output and "try5/5" in output


def test_timeout_overrides_inherit_and_explicit_zero_disables(tmp_path, monkeypatch, clock):
    stopped = []

    def launch(command, **kwargs):
        started = clock[0]
        return SimpleNamespace(pid=command[0], poll=lambda: 0 if clock[0] - started >= 3 else None)

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", launch)
    scheduler = Scheduler(tmp_path, Scheduling(task_timeout_seconds=2, max_retries=0, max_parallel_tasks=4),
                          runtime=LocalRuntime(stop=lambda process: stopped.append(process.pid)))
    scheduler.submit_many([task("inherited"), task("short", task_timeout_seconds=1),
                           task("long", task_timeout_seconds=4), task("disabled", task_timeout_seconds=0)])
    records = {record.spec.id: record for record in scheduler.run()}
    assert records["inherited"].status == records["short"].status == "failed"
    assert records["long"].status == records["disabled"].status == "complete"
    assert records["short"].attempts[0].kind == "timeout"
    assert set(stopped) == set(records)


def test_task_retry_budget_survives_restart_and_can_be_increased(tmp_path, monkeypatch, clock):
    launches = []

    def launch(command, **kwargs):
        launches.append(command)
        code = 1 if len(launches) == 1 else 0
        return SimpleNamespace(pid=100, poll=lambda: code)

    monkeypatch.setattr("task_scheduling.runtime.subprocess.Popen", launch)
    runtime = LocalRuntime(stop=lambda process: None)
    scheduler = Scheduler(tmp_path, Scheduling(max_retries=3), runtime=runtime)
    original = task("one", max_retries=0)
    scheduler.submit(original)
    assert scheduler.run()[0].status == "failed"
    assert Scheduler(tmp_path, Scheduling(max_retries=8), runtime=runtime).run()[0].status == "failed"
    assert len(launches) == 1
    restored = Scheduler(tmp_path, runtime=runtime)
    restored.submit(replace(original, execution=ExecutionPolicy(max_retries=1)))
    assert restored.run()[0].status == "complete"
    assert len(launches) == 2


@pytest.mark.parametrize("field", ["max_retries", "task_timeout_seconds"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "1"])
def test_invalid_task_execution_policy(field, value):
    with pytest.raises(ValueError):
        ExecutionPolicy(**{field: value})
