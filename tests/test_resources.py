from types import SimpleNamespace

import pytest

from task_scheduling import Scheduling
from task_scheduling.gpu import GPU, GPUQueryError, discover, eligible, visible_devices
from task_scheduling.memory import MemoryPool


def test_visibility_and_idle_policy(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,GPU-b")
    devices = [GPU(0, "GPU-a", 6000), GPU(1, "GPU-b", 8000, True), GPU(2, "GPU-c", 9000)]
    policy = Scheduling(require_idle=True)
    assert [gpu.uuid for gpu in visible_devices(devices, policy)] == ["GPU-a", "GPU-b"]
    assert [gpu.uuid for gpu in eligible(devices, policy, set(), {}, set(), 0)] == ["GPU-a"]
    assert not eligible(devices, policy, {"GPU-a"}, {}, set(), 0)
    assert not eligible(devices, policy, set(), {"GPU-a": 10}, set(), 0)
    assert not eligible(devices, policy, set(), {}, {"GPU-a"}, 0)


@pytest.mark.parametrize("reported,expected", [("2048", 2048), ("[N/A]", 0)])
def test_gpu_process_memory_query(monkeypatch, reported, expected):
    def query(command, **kwargs):
        output = (f"GPU-a, 123, {reported}\n" if "--query-compute-apps=gpu_uuid,pid,used_gpu_memory" in command
                  else "0, GPU-a, 8192\n")
        return SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr("task_scheduling.gpu.subprocess.run", query)
    gpu, = discover()
    assert gpu.busy and gpu.process_memory_mib == {123: expected}


def test_failed_process_query_is_not_treated_as_available_memory(monkeypatch):
    def query(command, **kwargs):
        if "--query-compute-apps=gpu_uuid,pid,used_gpu_memory" in command:
            return SimpleNamespace(stdout="", stderr="query failed", returncode=1)
        return SimpleNamespace(stdout="0, GPU-a, 8192\n", returncode=0)

    monkeypatch.setattr("task_scheduling.gpu.subprocess.run", query)
    with pytest.raises(GPUQueryError, match="query failed"):
        discover()


def test_reservations_cover_startup_and_actual_growth(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    pool = MemoryPool(Scheduling(memory_margin_mib=1024))
    gpu = GPU(0, "GPU-a", 12288)
    pool.refresh([gpu])
    pool.reserve(0, gpu, 4096)
    pool.started(0, 101)
    pool.reserve(1, gpu, 3072)
    pool.started(1, 102)
    assert pool.available(gpu) == 4096
    gpu = GPU(0, "GPU-a", 10240, True, {101: 1024, 102: 1024})
    pool.refresh([gpu])
    assert pool.available(gpu) == 4096
    gpu = GPU(0, "GPU-a", 5120, True, {101: 4096, 102: 3072})
    pool.refresh([gpu])
    assert pool.available(gpu) == 4096
    gpu = GPU(0, "GPU-a", 4096, True, {101: 5120, 102: 3072})
    pool.refresh([gpu])
    assert pool.available(gpu) == 3072
    pool.release(0)
    pool.release(1)
    assert not pool.reservations


def test_smallest_fitting_card_and_exclusive_reservation(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    pool = MemoryPool(Scheduling(memory_margin_mib=1024, allowed_gpus=(0, 1)))
    devices = [GPU(0, "GPU-small", 5120), GPU(1, "GPU-large", 12288), GPU(2, "GPU-hidden", 8192)]
    pool.refresh(devices)
    assert [gpu.uuid for gpu in pool.candidates(4096, {}, set(), 0)] == ["GPU-small", "GPU-large"]
    pool.reserve(0, devices[0], 4096)
    assert [gpu.uuid for gpu in pool.candidates(4096, {}, set(), 0)] == ["GPU-large"]
    assert not pool.candidates(4096, {"GPU-large": 10}, set(), 0)
    assert not pool.candidates(4096, {}, {"GPU-large"}, 0)
    exclusive = MemoryPool(Scheduling(require_idle=True))
    exclusive.refresh([devices[1]])
    exclusive.reserve(0, devices[1], 4096)
    assert not exclusive.candidates(4096, {}, set(), 0)
    exclusive.release(0)
    assert exclusive.candidates(4096, {}, set(), 0)


@pytest.mark.parametrize("settings", [
    {"max_parallel_tasks": 0}, {"require_idle": 1}, {"allowed_gpus": []},
    {"max_retries": -1}, {"poll_interval_seconds": 0}, {"memory_margin_mib": True},
    {"model_memory_mib": {"anything": 1024}},
])
def test_invalid_resource_policy(settings):
    with pytest.raises(ValueError):
        Scheduling.from_settings(settings)
