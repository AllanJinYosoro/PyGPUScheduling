from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from task_scheduling import ArtifactHandler, Attempt, Command, ExecutionPolicy, Scheduler, Scheduling, TaskRecord, TaskSpec


class FileHandler(ArtifactHandler):
    def prepare(self, context):
        return Command((sys.executable, "-c", context.task.payload["script"]))

    def commit(self, context):
        return {"output": context.task.payload["output"]}


def file_task(root, name, dependencies=()):
    output = root / f"{name}.txt"
    script = f"from pathlib import Path; Path({str(output)!r}).write_text({name!r})"
    return TaskSpec(name, "file", payload={"script": script, "output": str(output)}, dependencies=dependencies)


def valid_file(context):
    path = Path(context.task.payload["output"])
    return path.is_file() and path.read_text() == context.task.id


def file_scheduler(root, handler=None):
    scheduler = Scheduler(root / "queue", Scheduling(max_retries=0))
    scheduler.register_handler("file", handler or FileHandler(check_attempt=valid_file, check_result=valid_file))
    return scheduler


@pytest.mark.parametrize("damage", ["deleted", "corrupted"])
def test_invalid_result_recomputes_dependents_and_preserves_independent_branch(tmp_path, fast_poll, damage):
    events = []
    initial = file_scheduler(tmp_path)
    initial.submit_many([file_task(tmp_path, "child", ("parent",)), file_task(tmp_path, "parent"),
                          file_task(tmp_path, "grandchild", ("child",)), file_task(tmp_path, "independent")])
    initial.subscribe("consumer", events.append)
    initial.run()
    independent_stamp = (tmp_path / "independent.txt").stat().st_mtime_ns
    path = tmp_path / "parent.txt"
    if damage == "deleted":
        path.unlink()
    else:
        path.write_text("damaged")
    checked = []

    def check_result(context):
        checked.append(context.task.id)
        assert context.result["output"] == context.task.payload["output"]
        return valid_file(context)

    restored = file_scheduler(tmp_path, FileHandler(check_attempt=valid_file, check_result=check_result))
    restored.subscribe("consumer", events.append)
    records = {record.spec.id: record for record in restored.run()}
    assert checked == ["parent", "independent"]
    assert (tmp_path / "independent.txt").stat().st_mtime_ns == independent_stamp
    for name in ("parent", "child", "grandchild"):
        record = records[name]
        assert record.status == "complete" and record.revision == 1
        assert [attempt.revision for attempt in record.attempts] == [0, 1]
        assert len(record.current_attempts) == 1
        assert (tmp_path / f"{name}.txt").read_text() == name
        delivered = [event for event in events if event.task.id == name]
        assert [event.revision for event in delivered] == [0, 1]
        assert delivered[0].id != delivered[1].id
    assert records["independent"].revision == 0
    assert len([event for event in events if event.task.id == "independent"]) == 1


def test_attempt_check_runs_before_commit_and_result_check_only_on_restore(tmp_path, fast_poll):
    phases = []

    def check_attempt(context):
        phases.append(("attempt", context.status))
        return False

    def check_result(context):
        raise AssertionError("An unsuccessful task has no published result to check")

    class Handler(FileHandler):
        def commit(self, context):
            raise AssertionError("Invalid attempt must not be published")

    scheduler = file_scheduler(tmp_path, Handler(check_attempt=check_attempt, check_result=check_result))
    scheduler.submit(file_task(tmp_path, "one"))
    record, = scheduler.run()
    assert record.status == "failed" and record.attempts[0].kind == "artifact"
    assert phases == [("attempt", "running")]


def test_optional_checks_preserve_saved_result(tmp_path, fast_poll):
    scheduler = file_scheduler(tmp_path, FileHandler())
    scheduler.submit(file_task(tmp_path, "one"))
    original, = scheduler.run()
    (tmp_path / "one.txt").unlink()
    restored, = file_scheduler(tmp_path, FileHandler()).run()
    assert restored.result == original.result
    assert len(restored.attempts) == 1 and restored.revision == 0


def test_invalidation_survives_crash_before_new_attempt(tmp_path, fast_poll):
    initial = file_scheduler(tmp_path)
    initial.submit(file_task(tmp_path, "one"))
    initial.run()
    (tmp_path / "one.txt").unlink()
    interrupted = file_scheduler(tmp_path)

    def crash(records):
        if records[0].revision == 1:
            raise KeyboardInterrupt

    interrupted.observe(crash)
    with pytest.raises(KeyboardInterrupt):
        interrupted.run()

    class NoStaleRecovery(FileHandler):
        def recover(self, context):
            raise AssertionError("Invalidated artifacts must not be recovered from the old revision")

    record, = file_scheduler(tmp_path, NoStaleRecovery(check_attempt=valid_file, check_result=valid_file)).run()
    assert record.status == "complete" and record.revision == 1
    assert len(record.attempts) == 2


def test_recomputation_has_fresh_retry_budget(tmp_path, fast_poll):
    counter = tmp_path / "counter.txt"
    original = file_task(tmp_path, "one")
    script = (f"from pathlib import Path; import sys; p=Path({str(counter)!r}); "
              "n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); "
              f"sys.exit(1) if n==1 else Path({original.payload['output']!r}).write_text('one')")
    original = replace(original, payload={**original.payload, "script": script},
                       execution=ExecutionPolicy(max_retries=1))
    initial = file_scheduler(tmp_path)
    initial.submit(original)
    assert len(initial.run()[0].attempts) == 2
    (tmp_path / "one.txt").unlink()
    restored = file_scheduler(tmp_path)
    restored.submit(replace(original, execution=ExecutionPolicy(max_retries=0)))
    record, = restored.run()
    assert record.status == "complete" and record.revision == 1
    assert len(record.current_attempts) == 1 and len(record.attempts) == 3


def test_result_check_error_preserves_state_and_does_not_launch(tmp_path, fast_poll):
    initial = file_scheduler(tmp_path)
    initial.submit(file_task(tmp_path, "one"))
    initial.run()

    def broken(context):
        raise RuntimeError("validator unavailable")

    restored = file_scheduler(tmp_path, FileHandler(check_result=broken))
    with pytest.raises(RuntimeError, match="validator unavailable"):
        restored.run()
    record, = restored.tasks
    assert record.status == "complete" and record.revision == 0 and len(record.attempts) == 1


def test_new_revision_completion_event_replays_without_reexecution(tmp_path, fast_poll):
    initial = file_scheduler(tmp_path)
    initial.submit(file_task(tmp_path, "one"))
    initial.run()
    (tmp_path / "one.txt").unlink()
    interrupted = file_scheduler(tmp_path)
    events = []

    def crash(event):
        events.append(event)
        raise KeyboardInterrupt

    interrupted.subscribe("consumer", crash)
    with pytest.raises(KeyboardInterrupt):
        interrupted.run()
    restored = file_scheduler(tmp_path)
    restored.subscribe("consumer", events.append)
    record, = restored.run()
    assert len(record.attempts) == 2 and record.revision == 1
    assert len(events) == 2 and events[0].id == events[1].id


def test_version_one_state_keeps_event_acknowledgements_and_results(tmp_path):
    root = tmp_path / "queue"
    root.mkdir()
    document = {"version": 1, "tasks": [{
        "spec": {"id": "legacy", "handler": "command", "name": "task", "payload": {"args": ["must-not-run"]},
                 "dependencies": [], "priority": 0, "resources": {"device": "cpu", "memory_mib": 0}},
        "sequence": 0, "status": "complete", "attempts": [], "result": {"token": 7},
        "acknowledged": {"consumer": "legacy:0:complete"},
    }]}
    (root / "scheduler.json").write_text(json.dumps(document))
    scheduler = Scheduler(root)
    scheduler.subscribe("consumer", lambda event: pytest.fail("Acknowledged legacy event replayed"))
    record, = scheduler.run()
    assert record.result == {"token": 7} and record.revision == 0
    assert record.spec.execution == ExecutionPolicy()
    assert json.loads((root / "scheduler.json").read_text())["version"] == 2


def test_revision_events_do_not_collide_with_task_id_suffixes(tmp_path):
    scheduler = Scheduler(tmp_path)
    events = []
    scheduler.subscribe("consumer", events.append)
    with scheduler:
        scheduler.import_records([
            TaskRecord(TaskSpec("one", "command", payload={"args": ["must-not-run"]}), 0, status="complete",
                       revision=1, attempts=[Attempt(str(tmp_path), status="complete", revision=1)]),
            TaskRecord(TaskSpec("one:r1", "command", payload={"args": ["must-not-run"]}), 1, status="complete",
                       attempts=[Attempt(str(tmp_path), status="complete")]),
        ])
        scheduler.run()
    assert len(events) == 2 and len({event.id for event in events}) == 2
