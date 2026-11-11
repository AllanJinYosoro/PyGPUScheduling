from pathlib import Path
import sys

from task_scheduling import Assessment, Command, CommandHandler, Recovery, Scheduler, TaskSpec


class TextArtifactHandler(CommandHandler):
    def prepare(self, context):
        artifact = context.directory / "artifact.txt"
        content = context.task.payload["content"]
        script = f"from pathlib import Path; Path({str(artifact)!r}).write_text({content!r})"
        return Command((sys.executable, "-c", script))

    def assess(self, context, outcome):
        execution = super().assess(context, outcome)
        if not execution.success:
            return execution
        artifact = context.directory / "artifact.txt"
        valid = artifact.is_file() and artifact.read_text() == context.task.payload["content"]
        return Assessment(valid, False, "artifact", "artifact content validation")

    def commit(self, context):
        output = Path(context.task.payload["output"])
        if output.exists():
            if output.read_text() != context.task.payload["content"]:
                raise ValueError("Published artifact content changed")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            (context.directory / "artifact.txt").replace(output)
        return {"output": str(output)}

    def recover(self, context):
        output = Path(context.task.payload["output"])
        if output.exists():
            if output.read_text() != context.task.payload["content"]:
                raise ValueError("Published artifact content changed")
            return Recovery("complete", {"output": str(output)})
        if context.attempts:
            artifact = context.directory / "artifact.txt"
            if artifact.is_file() and artifact.read_text() == context.task.payload["content"]:
                return Recovery("committing")
        if context.status in {"complete", "committing"}:
            raise ValueError("Completed artifact is missing")
        return None


def main():
    root = Path("runs/artifacts").resolve()
    scheduler = Scheduler(root / "queue")
    scheduler.register_handler("text", TextArtifactHandler())
    scheduler.submit(TaskSpec("publish-text", "text", payload={
        "content": "Artifact lifecycle completed\n", "output": str(root / "published.txt")
    }))
    record, = scheduler.run()
    print(record.result)
    return 0 if record.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
