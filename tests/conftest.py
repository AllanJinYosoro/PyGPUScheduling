import importlib
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def clock(monkeypatch):
    current = [0.0]

    def sleep(seconds):
        current[0] += seconds

    monkeypatch.setattr(importlib.import_module("task_scheduling.scheduler"), "time",
                        SimpleNamespace(monotonic=lambda: current[0], sleep=sleep))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    return current


@pytest.fixture
def fast_poll(monkeypatch):
    sleep = time.sleep
    monkeypatch.setattr(importlib.import_module("task_scheduling.scheduler"), "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=lambda seconds: sleep(0.01)))
