from pathlib import Path
import sys

from task_scheduling import ArtifactHandler, Command, Recovery, Scheduler, TaskSpec


def valid_text(path, context):
    return path.is_file() and path.read_text() == context.task.payload["content"]


class TextArtifactHandler(ArtifactHandler):
    def prepare(self, context):
        artifact = context.directory / "artifact.txt"
        content = context.task.payload["content"]
        script = f"from pathlib import Path; Path({str(artifact)!r}).write_text({content!r})"
        return Command((sys.executable, "-c", script))

    def commit(self, context):
        output = Path(context.task.payload["output"])
        if not valid_text(output, context):
            output.parent.mkdir(parents=True, exist_ok=True)
            (context.directory / "artifact.txt").replace(output)
        return {"output": str(output)}

    def recover(self, context):
        if context.status == "complete":
            return super().recover(context)
        output = Path(context.task.payload["output"])
        if valid_text(output, context):
            return Recovery("complete", {"output": str(output)})
        if context.current_attempts:
            artifact = context.directory / "artifact.txt"
            if valid_text(artifact, context):
                return Recovery("committing")
        if context.status == "committing":
            raise ValueError("Completed artifact is missing")
        return None


def main():
    root = Path("runs/artifacts").resolve()
    scheduler = Scheduler(root / "queue")
    scheduler.register_handler("text", TextArtifactHandler(
        check_attempt=lambda context: valid_text(context.directory / "artifact.txt", context),
        check_result=lambda context: valid_text(Path(context.task.payload["output"]), context),
    ))
    scheduler.submit(TaskSpec("publish-text", "text", payload={
        "content": "Artifact lifecycle completed\n", "output": str(root / "published.txt")
    }))
    record, = scheduler.run()
    print(record.result)
    return 0 if record.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
