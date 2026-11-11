# API 与恢复协议

## 职责

调用方声明任务、依赖、资源需求、业务参数和处理器。调度器只负责依赖约束、资源准入、执行状态及事件，不解释业务参数中的内容。

子进程是执行边界；生命周期方法在调度进程内执行，应保持简短。训练、转换文件等耗时操作应由 `prepare` 返回的命令完成。

## 公共对象

| 对象 | 用途 |
| --- | --- |
| `Scheduler` | 注册处理器、提交任务、订阅事件、运行与恢复 |
| `Scheduling` | GPU 筛选、并发、重试、查询间隔等全局策略 |
| `TaskSpec` | 稳定 ID、处理器、显示名、JSON 参数、依赖、优先级和资源需求 |
| `ResourceRequest` | `cpu` 或 `cuda`，以及单进程显存预算 |
| `Command` | 命令参数列表、工作目录和环境变量 |
| `TaskContext` | 任务、根目录、当前状态、历史尝试以及最近一次的目录与 GPU |
| `Assessment` | 成功与否、能否重试、失败种类和信息 |
| `Recovery` | 外部校验后的恢复状态和 JSON 结果 |
| `TaskEvent` | 稳定事件 ID、任务、终态、历史尝试和业务发布结果 |
| `TaskRecord` | 任务快照、执行状态、尝试、结果和事件确认记录 |
| `LocalRuntime` | 本地子进程运行与可注入的 GPU 查询、停止操作 |

`TaskSpec` 的 `id`、`handler` 和 `name` 必须是非空字符串，`payload` 必须是可序列化的 JSON 对象。`dependencies` 是任务 ID 序列，`priority` 是整数。资源默认 `ResourceRequest("cpu", 0)`，GPU 任务应显式给出预算。

`Command.args` 是非空字符串序列；不经过 shell。`cwd` 省略时沿用调度进程的工作目录，`env` 用于覆盖继承的环境变量。核心最终设置 `CUDA_VISIBLE_DEVICES`，GPU 子进程只看到分配到的卡，CPU 子进程看不到 GPU。

## 注册、依赖和顺序

```python
from task_scheduling import CommandHandler, Scheduler

scheduler = Scheduler("runs/workflow")
scheduler.register_handler("custom", CommandHandler())
scheduler.subscribe("workflow", on_finished)
scheduler.submit_many(tasks)
records = scheduler.run()
```

同一批任务可向前引用依赖；依赖必须存在且不能形成环。批量校验失败时不提交这一批任务。相同 ID 和业务定义可重复提交；改变处理器、参数或依赖会报错。显示名、优先级和资源可以在任务未执行时调整，执行中的任务不能更换定义。

同层任务按优先级降序、显存预算降序及提交顺序选择。显存不足时继续尝试后续可放入的小任务。依赖全部 `complete` 后任务才会启动，前置任务最终 `failed` 或 `blocked` 时，下游成为 `blocked`。严格先后关系使用依赖表达。

## 生命周期处理器

自定义处理器实现 `TaskHandler` 协议，也可继承 `CommandHandler` 覆盖部分方法：

| 方法 | 契约 |
| --- | --- |
| `prepare(context)` | 检查输入、准备尝试目录，返回 `Command` |
| `assess(context, outcome)` | 检查退出码与业务产物，返回 `Assessment` |
| `commit(context)` | 幂等发布产物，返回可序列化的 JSON 对象 |
| `recover(context)` | 检查旧产物及旧状态，决定恢复或重新执行 |

`context.attempts` 是历史快照，可能为空。`context.directory` 返回最近一次尝试目录，没有尝试时返回调度根目录；`context.gpu` 返回 GPU UUID 或 `None`。

设备丢失和超时由核心直接判定为可重试失败；普通退出会交给 `assess`，内置处理器以退出码 0 为成功。处理器自身抛错会停止调度并保留现场，不会当成业务失败自动消耗重试预算。

业务产物完整示例见 [`examples/artifacts.py`](../examples/artifacts.py)。

## 状态与恢复

成功流程为 `pending → running → committing → complete`。核心先保存 `committing`，再调用业务发布方法。失败根据 `Assessment.retryable` 和剩余预算回到 `pending` 或进入 `failed`，中断尝试记录为 `interrupted`，不消耗失败重试次数。

`recover` 可返回：

- `Recovery("complete", result)`：业务确认已经完成，复用结果。
- `Recovery("committing")`：计算已结束，继续调用幂等 `commit`。
- `Recovery("failed")`：业务确认永久失败。
- `None`：按已保存的失败次数及可重试标记重新排队。

已有 `complete` 或 `committing` 状态无法恢复时应抛错，不能静默覆盖已发布结果。业务自己定义数据身份、文件校验及发布流程。内置命令处理器仅依靠保存的成功状态恢复，不检查业务文件。

`scheduler.json` 当前格式版本为 1，保存任务定义、尝试与事件确认信息；`scheduler.lock` 防止同一运行目录被重复调度。每次尝试目录包含 `worker.log`，以及 worker 选择上报的 `progress.json`。保留完整运行目录，并在恢复时重新注册同名处理器和订阅者。Python 函数、对象和闭包不写入状态文件。

`with Scheduler(...) as scheduler` 可显式控制锁持有范围；`reconcile()` 在持锁会话中恢复任务。`import_records()` 用于调用方迁入已有通用记录，只允许在尚无状态的新调度器中导入。状态包含绝对尝试路径，当前不承诺运行目录搬迁后仍可恢复。

## 完成事件与动态任务

`subscribe(name, listener)` 接收 `complete`、`failed`、`blocked` 终态事件。订阅回调可继续提交任务；新任务必须使用稳定 ID。

事件按至少一次语义交付：回调成功返回后才保存确认信息，期间中断会重复投递相同事件 ID。调用方按事件 ID 幂等更新业务状态。`observe(callback)` 提供通用状态快照，适合导出报告，不具有终态事件的确认语义。

## 策略默认值

| 字段 | 默认值及含义 |
| --- | --- |
| `allowed_gpus` | `None`，使用所有可见 GPU；可用索引或 `GPU-` UUID 前缀限制 |
| `min_free_memory_mib` | 4096，底层 `eligible` 可用性查询的默认门槛；任务自身使用显式资源预算 |
| `memory_margin_mib` | 0，每卡预留的安全余量 |
| `max_parallel_tasks` | 3，同时运行的 worker 总数 |
| `require_idle` | `False`，允许共享 GPU；`True` 要求空闲卡并使用独占锁 |
| `max_retries` | 3，可重试失败后的额外尝试次数 |
| `poll_interval_seconds` | 10，GPU 查询间隔 |
| `gpu_cooldown_seconds` | 60，GPU 普通失败后的冷却时间 |
| `task_timeout_seconds` | 0，关闭单次任务超时 |
| `wait_seconds` | 3600，无运行任务时等待 GPU 的时限 |

GPU 筛选同时遵守父进程 `CUDA_VISIBLE_DEVICES`。多卡可选时优先避开上次失败的卡，再选择剩余可调度容量最小的卡。查询失败时暂停启动新的 GPU 任务；设备连续两次查询消失后停止对应 worker 并隔离该卡。

可调度容量为实际空闲显存减去安全余量，再减去每个本实例运行任务尚未实际占用的预算。启动前预留完整预算，按 PID 查询实际显存以避免重复扣减。进程显存未知时保留完整预留。预算是准入策略，不是显存硬限制；多实例不共享预算，外部进程后续增长不受此预算约束。

## 进度

核心设置 `TASK_PROGRESS_FILE`。worker 可向对应路径原子写入 JSON，字段为 `current`、`total`、`phase`、`unit`。没有上报时显示初始化状态。TTY 且安装 `tqdm` 时使用交互式显示，其余情况输出状态和心跳日志。
