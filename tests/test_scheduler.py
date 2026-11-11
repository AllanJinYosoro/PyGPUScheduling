from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from task_scheduling import (
    Assessment, CommandHandler, Recovery, Scheduler, Scheduling, TaskSpec,
)


def command_task(identifier, script, *, dependencies=(), priority=0):
    return TaskSpec(identifier, "command", payload={"args": [sys.executable, "-c", script]},
                    dependencies=dependencies, priority=priority)


def test_dependency_order_and_dynamic_submission(tmp_path):
    output = tmp_path / "order.txt"

    def append_task(identifier, dependencies=()):
        script = f"from pathlib import Path; p=Path({str(output)!r}); p.open('a').write({identifier!r}+'\\n')"
        return command_task(identifier, script, dependencies=dependencies)

    scheduler = Scheduler(tmp_path / "queue", Scheduling(max_parallel_tasks=3))
    scheduler.submit_many([append_task("last", ("first",)), append_task("first")])
    events = []

    def finished(event):
        events.append(event.task.id)
        if event.task.id == "last":
            scheduler.submit(append_task("dynamic", ("last",)))

    scheduler.subscribe("workflow", finished)
    records = scheduler.run()
    assert output.read_text().splitlines() == ["first", "last", "dynamic"]
    assert events == ["first", "last", "dynamic"]
    assert all(record.status == "complete" for record in records)


def test_failed_dependency_blocks_descendants(tmp_path):
    scheduler = Scheduler(tmp_path, Scheduling(max_retries=0))
    scheduler.submit_many([
        command_task("failure", "raise SystemExit(7)"),
        command_task("child", "raise AssertionError('must not launch')", dependencies=("failure",)),
        command_task("grandchild", "raise AssertionError('must not launch')", dependencies=("child",)),
    ])
    records = {record.spec.id: record for record in scheduler.run()}
    assert records["failure"].status == "failed"
    assert records["child"].status == records["grandchild"].status == "blocked"
    assert not records["child"].attempts and not records["grandchild"].attempts


def test_invalid_dependencies_and_changed_definition_are_rejected_atomically(tmp_path):
    scheduler = Scheduler(tmp_path)
    with pytest.raises(ValueError, match="Unknown dependency"):
        scheduler.submit(command_task("child", "", dependencies=("missing",)))
    assert scheduler.tasks == ()
    with pytest.raises(ValueError, match="cycle"):
        scheduler.submit_many([command_task("a", "", dependencies=("b",)), command_task("b", "", dependencies=("a",))])
    assert scheduler.tasks == ()
    task = command_task("a", "")
    scheduler.submit(task)
    scheduler.submit(task)
    assert len(scheduler.tasks) == 1
    with pytest.raises(ValueError, match="definition changed"):
        scheduler.submit(replace(task, payload={"args": ["different"]}))


def test_event_replay_after_crash_does_not_repeat_execution(tmp_path):
    output = tmp_path / "executions.txt"
    task = command_task("one", f"open({str(output)!r}, 'a').write('executed\\n')")
    first = Scheduler(tmp_path / "queue")
    first.submit(task)
    first_events = []

    def interrupted(event):
        first_events.append(event.id)
        raise KeyboardInterrupt

    first.subscribe("consumer", interrupted)
    with pytest.raises(KeyboardInterrupt):
        first.run()
    second_events = []
    second = Scheduler(tmp_path / "queue")
    second.subscribe("consumer", lambda event: second_events.append(event.id))
    assert second.run()[0].status == "complete"
    third_events = []
    third = Scheduler(tmp_path / "queue")
    third.subscribe("consumer", lambda event: third_events.append(event.id))
    third.run()
    assert first_events == second_events
    assert third_events == []
    assert output.read_text().splitlines() == ["executed"]


def test_interrupted_event_can_register_tasks_idempotently(tmp_path):
    initial = command_task("first", "")
    next_task = command_task("next", "", dependencies=("first",))
    scheduler = Scheduler(tmp_path)
    scheduler.submit(initial)

    def interrupted(event):
        scheduler.submit(next_task)
        raise KeyboardInterrupt

    scheduler.subscribe("workflow", interrupted)
    with pytest.raises(KeyboardInterrupt):
        scheduler.run()
    restored = Scheduler(tmp_path)

    def finished(event):
        if event.task.id == "first":
            restored.submit(next_task)

    restored.subscribe("workflow", finished)
    records = restored.run()
    assert len(records) == 2
    assert all(record.status == "complete" and len(record.attempts) == 1 for record in records)


def test_publication_is_recovered_without_reexecution(tmp_path):
    output = tmp_path / "executions.txt"
    published = tmp_path / "published.txt"

    class Publisher(CommandHandler):
        def commit(self, context):
            published.write_text("published")
            raise KeyboardInterrupt

    task = replace(command_task("one", f"open({str(output)!r}, 'a').write('executed\\n')"), handler="publisher")
    scheduler = Scheduler(tmp_path / "queue")
    scheduler.register_handler("publisher", Publisher())
    scheduler.submit(task)
    with pytest.raises(KeyboardInterrupt):
        scheduler.run()

    class RecoveredPublisher(Publisher):
        def recover(self, context):
            assert context.status == "committing"
            return Recovery("complete", {"published": str(published)})

    restored = Scheduler(tmp_path / "queue")
    restored.register_handler("publisher", RecoveredPublisher())
    record, = restored.run()
    assert record.result == {"published": str(published)}
    assert output.read_text().splitlines() == ["executed"]


def test_business_validation_controls_success_and_retry(tmp_path):
    class Validator(CommandHandler):
        def assess(self, context, outcome):
            assert outcome.returncode == 0
            return Assessment(len(context.attempts) == 2, True, "validation", "external validation failed")

    scheduler = Scheduler(tmp_path, Scheduling(max_retries=1))
    scheduler.register_handler("validated", Validator())
    scheduler.submit(replace(command_task("one", ""), handler="validated"))
    record, = scheduler.run()
    assert record.status == "complete"
    assert [attempt.status for attempt in record.attempts] == ["failed", "complete"]
    assert record.attempts[0].kind == "validation"


def test_handler_errors_stop_without_consuming_retry_budget(tmp_path):
    class BrokenHandler(CommandHandler):
        def assess(self, context, outcome):
            raise ValueError("broken validator")

    scheduler = Scheduler(tmp_path)
    scheduler.register_handler("broken", BrokenHandler())
    scheduler.submit(replace(command_task("one", ""), handler="broken"))
    with pytest.raises(ValueError, match="broken validator"):
        scheduler.run()
    restored = Scheduler(tmp_path)
    restored.register_handler("broken", CommandHandler())
    record, = restored.run()
    assert record.status == "complete"
    assert [attempt.status for attempt in record.attempts] == ["interrupted", "complete"]


def test_core_executes_without_repository_or_third_party_packages(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
from pathlib import Path
import sys
from task_scheduling import Scheduler, TaskSpec
scheduler = Scheduler('queue')
scheduler.submit(TaskSpec('standalone', 'command', payload={'args': [sys.executable, '-c', "open('output.txt', 'w').write('ok')"]}))
record, = scheduler.run()
assert record.status == 'complete'
assert not any(name in sys.modules for name in ('video_baselines', 'torch', 'optuna', 'numpy', 'yaml', 'tqdm'))
"""
    result = subprocess.run([sys.executable, "-S", "-c", script], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(source)}, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "output.txt").read_text() == "ok"


def test_registered_handler_is_required_after_restart(tmp_path):
    scheduler = Scheduler(tmp_path)
    scheduler.register_handler("custom", CommandHandler())
    scheduler.submit(replace(command_task("one", ""), handler="custom"))
    scheduler.run()
    with pytest.raises(ValueError, match="not registered"):
        Scheduler(tmp_path).run()


def test_payload_and_state_snapshots_do_not_mutate_registered_tasks(tmp_path):
    task = command_task("one", "")
    scheduler = Scheduler(tmp_path)
    scheduler.submit(task)
    task.payload["args"].append("external mutation")
    snapshot, = scheduler.tasks
    snapshot.spec.payload["args"].append("snapshot mutation")
    record, = scheduler.run()
    assert record.status == "complete"
    document = json.loads((tmp_path / "scheduler.json").read_text())
    assert len(document["tasks"][0]["spec"]["payload"]["args"]) == 3
