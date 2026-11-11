from importlib.metadata import metadata, version
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from task_scheduling import Scheduler, TaskSpec


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
    if any(name in sys.modules for name in ("video_baselines", "torch", "optuna", "numpy", "yaml")):
        raise AssertionError("Installed core imported a business dependency")
    print(f"Installed pygpu-scheduling {version('pygpu-scheduling')}: workflow and recovery passed")


if __name__ == "__main__":
    main()
