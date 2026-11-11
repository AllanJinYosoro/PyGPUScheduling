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
| `ExecutionPolicy` | 部分覆盖队列的重试次数和超时 |
| `ArtifactHandler` | 带可选执行后、恢复时产物检查的命令处理器模板 |
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

同一批任务可向前引用依赖；依赖必须存在且不能形成环。批量校验失败时不提交这一批任务。相同 ID 和业务定义可重复提交；改变处理器、参数或依赖会报错。显示名、优先级、资源和执行策略可以在任务未执行时调整，执行中的任务不能更换定义。

同层任务按优先级降序、显存预算降序及提交顺序选择。显存不足时继续尝试后续可放入的小任务。依赖全部 `complete` 后任务才会启动，前置任务最终 `failed` 或 `blocked` 时，下游成为 `blocked`。严格先后关系使用依赖表达。

## 队列默认与任务覆盖

```python
from task_scheduling import ExecutionPolicy, Scheduling, TaskSpec

defaults = Scheduling(max_retries=3, task_timeout_seconds=3600)
task = TaskSpec("evaluate", "command", payload={"args": ["python", "evaluate.py"]},
                execution=ExecutionPolicy(task_timeout_seconds=600))
```

此任务继承 3 次额外重试，将每次尝试的超时改为 600 秒。两个任务级字段独立覆盖；`None` 表示继承，`0` 分别表示不重试、关闭超时。使用 `ExecutionPolicy(**settings)` 可从配置字典构造。恢复及进度显示使用相同的有效策略；提高预算后可继续此前因预算耗尽而停止的可重试任务。

## 生命周期处理器

自定义处理器实现 `TaskHandler` 协议，也可继承 `CommandHandler` 覆盖部分方法：

| 方法 | 契约 |
| --- | --- |
| `prepare(context)` | 检查输入、准备尝试目录，返回 `Command` |
| `assess(context, outcome)` | 检查退出码与业务产物，返回 `Assessment` |
| `commit(context)` | 幂等发布产物，返回可序列化的 JSON 对象 |
| `recover(context)` | 检查旧产物及旧状态，决定恢复或重新执行 |

`context.attempts` 是完整历史快照，`context.current_attempts` 只包含当前修订。`context.directory` 返回当前修订最近一次尝试的目录，没有时返回调度根目录；`context.gpu` 返回 GPU UUID 或 `None`。`context.result` 是已保存业务结果的快照，`context.revision` 是当前修订号。

设备丢失和超时由核心直接判定为可重试失败；普通退出会交给 `assess`，内置处理器以退出码 0 为成功。处理器自身抛错会停止调度并保留现场，不会当成业务失败自动消耗重试预算。

业务产物完整示例见 [`examples/artifacts.py`](../examples/artifacts.py)。

## 可选产物检查

```python
from task_scheduling import ArtifactHandler

handler = ArtifactHandler(check_attempt=check_worker_files, check_result=check_published_files)
scheduler.register_handler("checked", handler)
```

两个函数均接收 `TaskContext` 并返回布尔值，可以使用同一个函数，也可以分别省略。继承 `ArtifactHandler` 可定制命令和幂等发布。

- `check_attempt`：worker 退出码为 0 后、发布前运行。返回 `False` 时任务以不可重试的 `artifact` 错误结束；需要其他重试规则时覆盖现有 `assess`。
- `check_result`：每次持锁恢复时，检查已保存的 `complete` 任务。返回 `False` 时核心将该任务及所有依赖后继重新排队，独立分支继续复用。原有处理器也可自行实现同名方法，无需继承模板。

恢复检查按依赖顺序、在启动 worker 和交付事件之前完成；上游失效后不检查后继旧产物，也不通过 `recover` 复用这些旧结果。重新排队先原子保存，再启动新的尝试，因此中断后仍会继续重算。核心保留历史、增加修订号、清空当前结果和事件确认，并为新修订重新计算重试预算；不会删除业务文件或改变任务定义。业务的 `commit` 应能幂等替换失效产物。

检查函数应简短；耗时的质量检验放在 worker 中。函数抛错会停止恢复并保留原状态，不能用异常代表产物不合格。没有注册恢复检查时，核心信任保存的完成状态。

## 状态与恢复

成功流程为 `pending → running → committing → complete`。核心先保存 `committing`，再调用业务发布方法。失败根据 `Assessment.retryable` 和剩余预算回到 `pending` 或进入 `failed`，中断尝试记录为 `interrupted`，不消耗失败重试次数。

`recover` 可返回：

- `Recovery("complete", result)`：业务确认已经完成，复用结果。
- `Recovery("committing")`：计算已结束，继续调用幂等 `commit`。
- `Recovery("failed")`：业务确认永久失败。
- `None`：按已保存的失败次数及可重试标记重新排队。

未被产物检查判为失效的 `complete` 或 `committing` 状态无法恢复时仍会报错。`recover` 负责中断现场的恢复；已发布结果失效由 `check_result` 表达。业务自己定义数据身份、文件校验及发布流程。

`scheduler.json` 当前格式版本为 2，新增任务执行策略及任务、尝试的修订号。可直接读取版本 1，缺失策略按继承处理，旧任务及尝试属于修订 0；下次保存写为版本 2。修订 0 保持旧事件 ID 和确认记录，避免重复交付。0.1.x 不能读取版本 2，回退需恢复升级前的状态备份。

`scheduler.lock` 防止同一运行目录被重复调度。每次尝试目录包含 `worker.log`，以及 worker 选择上报的 `progress.json`。保留完整运行目录，并在恢复时重新注册同名处理器和订阅者。Python 函数、对象和闭包不写入状态文件。

`with Scheduler(...) as scheduler` 可显式控制锁持有范围；`reconcile()` 在持锁会话中恢复任务。`import_records()` 用于调用方迁入已有通用记录，只允许在尚无状态的新调度器中导入。状态包含绝对尝试路径，当前不承诺运行目录搬迁后仍可恢复。

## 完成事件与动态任务

`subscribe(name, listener)` 接收 `complete`、`failed`、`blocked` 终态事件。订阅回调可继续提交任务；新任务必须使用稳定 ID。

事件按至少一次语义交付：回调成功返回后才保存确认信息，期间中断会重复投递相同事件 ID。调用方按事件 ID 幂等更新业务状态。`observe(callback)` 提供通用状态快照，适合导出报告，不具有终态事件的确认语义。

重算后的事件包含新的 `revision`，事件 ID 也随之变化；同一修订内重放保持 ID 不变。计算正常完成但业务不接受结果时，可在 `commit` 返回接受标记，由订阅者注册稳定 ID 的新任务；不需要增加业务专用失败类型。

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
