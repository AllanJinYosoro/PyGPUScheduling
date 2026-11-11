from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
from typing import Callable

from .gpu import GPU, discover
from .task import Command


def stop_process_tree(process: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    process.wait()


@dataclass
class LocalRuntime:
    discover: Callable[[], list[GPU]] = discover
    stop: Callable[[subprocess.Popen], None] = stop_process_tree

    def launch(self, command: Command, directory: Path, gpu: GPU | None, inherited_fds: tuple[int, ...]):
        environment = {**os.environ, **command.env, "PYTHONUNBUFFERED": "1",
                       "CUDA_VISIBLE_DEVICES": "" if gpu is None else gpu.uuid,
                       "TASK_PROGRESS_FILE": str(directory / "progress.json")}
        with (directory / "worker.log").open("w") as log:
            return subprocess.Popen(command.args, cwd=command.cwd, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True, pass_fds=inherited_fds)
