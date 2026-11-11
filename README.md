# PyGPUScheduling

单机 CPU 与 NVIDIA GPU 任务调度库。业务注册任务、依赖和生命周期处理器，调度器管理资源准入、子进程、重试及恢复。运行时仅依赖 Python 标准库，交互式进度条可选安装 `tqdm`。

仓库名为 `PyGPUScheduling`，分发包名为 `pygpu-scheduling`，Python 导入名为 `task_scheduling`。当前版本为 `0.1.0`，首次公开发布的准备状态见[维护与发布指南](docs/maintaining.md#当前交接状态)。

## 环境与安装

需要 Python 3.11 或更新版本及 POSIX 系统。GPU 功能通过 `nvidia-smi` 工作，需要 NVIDIA 驱动；调度库不要求安装 CUDA Toolkit、Torch 或 Optuna。每个 GPU 任务使用一张卡，CPU 任务不查询 GPU。

在本仓库开发：

```bash
uv sync --locked
uv run pytest
uv run python examples/basic.py
```

需要交互式进度条时：

```bash
uv sync --locked --extra progress
```

首次发布到 PyPI 后，其他项目可添加依赖：

```bash
uv add "pygpu-scheduling>=0.1.0,<0.2.0"
```

发布前可从本地 checkout 接入：

```bash
uv add --editable ../PyGPUScheduling
```

GitHub 固定 tag 接入及升级方式见[维护指南](docs/maintaining.md#业务仓库接入)。

## 注册任务

```python
import sys

from task_scheduling import Scheduler, Scheduling, TaskSpec

scheduler = Scheduler("runs/example", Scheduling(max_parallel_tasks=2))
scheduler.submit_many([
    TaskSpec("prepare", "command", payload={"args": [sys.executable, "prepare.py"]}),
    TaskSpec("train", "command", dependencies=("prepare",),
             payload={"args": [sys.executable, "train.py"]}),
])
records = scheduler.run()
```

`command` 是按退出码判定结果的内置处理器。`run()` 返回全部任务记录，调用方检查 `complete`、`failed`、`blocked` 等状态并决定业务退出码。

GPU 任务通过 `resources=ResourceRequest("cuda", 4096)` 声明 4096 MiB 预算。预算用于启动准入，不是显存硬限制。调度器不解释模型、训练轮数、业务参数或产物文件。

## 功能与边界

- 静态依赖图与动态追加任务，拒绝缺失依赖、重复定义和依赖环。
- 按优先级、显存预算和提交顺序选择任务，支持共享或独占 GPU。
- 启动前预留显存，结合实际进程占用更新预算，保留每卡安全余量。
- 子进程日志、超时、有限重试、GPU 冷却和故障隔离。
- 注册 `prepare`、`assess`、`commit`、`recover`，由业务提供校验及产物流程。
- 持久化任务状态，完成事件按至少一次语义重放。

当前提供单机本地子进程执行，不包含多机、抢占或单任务多卡调度。预算账本属于单个调度实例；跨实例不会合并预算。更完整的约束见[API 与恢复协议](docs/api.md)。

## 文档与示例

- [API、调度策略和恢复协议](docs/api.md)
- [开发、版本管理、GitHub 与 PyPI 发布](docs/maintaining.md)
- [变更记录](CHANGELOG.md)
- [依赖顺序示例](examples/basic.py)
- [业务产物处理示例](examples/artifacts.py)

采用 [MIT 许可证](LICENSE)。
