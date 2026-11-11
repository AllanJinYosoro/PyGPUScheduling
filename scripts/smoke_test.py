from importlib.metadata import metadata, version
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from task_scheduling import ArtifactHandler, ExecutionPolicy, Scheduler, Scheduling, TaskSpec


def check_task_overrides(root):
    scheduler = Scheduler(root / "policy", Scheduling(max_retries=3, task_timeout_seconds=30))
    scheduler.submit(TaskSpec("failure", "command", payload={"args": [sys.executable, "-c", "raise SystemExit(1)"]},
                              execution=ExecutionPolicy(max_retries=0, task_timeout_seconds=0)))
    record, = scheduler.run()
    if record.status != "failed" or len(record.attempts) != 1:
        raise AssertionError("Task retry override was not honored")


def check_artifact_recovery(root):
    def valid(context):
        path = root / f"{context.task.id}.txt"
        return path.is_file() and path.read_text() == "valid"

    def scheduler():
        queue = Scheduler(root / "artifacts")
        queue.register_handler("artifact", ArtifactHandler(check_attempt=valid, check_result=valid))
        return queue

    queue = scheduler()
    for name, dependencies in (("parent", ()), ("child", ("parent",))):
        script = f"from pathlib import Path; Path({str(root / (name + '.txt'))!r}).write_text('valid')"
        queue.submit(TaskSpec(name, "artifact", payload={"args": [sys.executable, "-c", script]},
                               dependencies=dependencies))
    queue.run()
    (root / "parent.txt").unlink()
    records = scheduler().run()
    if any(record.status != "complete" or record.revision != 1 or len(record.attempts) != 2 for record in records):
        raise AssertionError("Artifact invalidation did not recompute the dependency graph")


def main():
    distribution = metadata("pygpu-scheduling")
    if distribution["License-Expression"] != "MIT":
        raise AssertionError("Installed distribution has incorrect license metadata")
    with TemporaryDirectory(prefix="pygpuscheduling-smoke-") as temporary:
        root = Path(temporary)
        output = root / "order.txt"

        def task(identifier, dependencies=()):
            script = f"open({str(output)!r}, 'a').write({identifier!r}+'\\n')"
            return TaskSpec(identifier, "command", dependencies=dependencies,
                            payload={"args": [sys.executable, "-c", script]})

        scheduler = Scheduler(root / "queue")
        scheduler.submit_many([task("second", ("first",)), task("first")])

        def finished(event):
            if event.task.id == "second":
                scheduler.submit(task("third", ("second",)))

        scheduler.subscribe("workflow", finished)
        records = scheduler.run()
        if len(records) != 3 or any(record.status != "complete" for record in records):
            raise AssertionError("Installed package did not complete dependency workflow")
        restored = Scheduler(root / "queue")

        def unexpected_event(event):
            raise AssertionError("Acknowledged event replayed")

        restored.subscribe("workflow", unexpected_event)
        restored.run()
        if output.read_text().splitlines() != ["first", "second", "third"]:
            raise AssertionError("Installed package repeated completed tasks or reordered dependencies")
        check_task_overrides(root)
        check_artifact_recovery(root)
    if any(name in sys.modules for name in ("video_baselines", "torch", "optuna", "numpy", "yaml")):
        raise AssertionError("Installed core imported a business dependency")
    print(f"Installed pygpu-scheduling {version('pygpu-scheduling')}: workflow, task overrides and artifact recovery passed")


if __name__ == "__main__":
    main()
