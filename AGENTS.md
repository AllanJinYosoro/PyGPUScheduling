# 项目约定

PyGPUScheduling 是独立的单机 CPU/NVIDIA GPU 调度库，运行时核心只依赖 Python 标准库。

## 开始工作

- 先读 `README.md`、`docs/api.md` 和 `docs/maintaining.md`。
- 环境：`uv sync --locked`。
- 检查：`uv run python scripts/check_project.py`、`uv run pytest`。
- 构建：`uv build --no-sources`。
- 安装包验证：按 `docs/maintaining.md` 运行 wheel 和 sdist 冒烟测试。

## 代码边界

- 不在代码中留下注释；文档使用清楚、简洁的中文。
- 保持高可读、高复用、高维护、低耦合和高内聚。
- 核心在 `src/task_scheduling`；公共导出在 `__init__.py`。
- 不引入 Torch、Optuna、模型注册表、训练配置或具体产物协议。
- 业务规则由外部 `TaskHandler` 和事件订阅者实现。
- 保持核心运行时依赖为空，进度显示依赖只能作为可选 extra。
- 任务定义、处理器名称、订阅者名称和事件 ID 是恢复契约。
- 更新状态文件格式时必须说明旧状态的兼容与迁移方式。
- 示例放 `examples`，通用测试放 `tests`，维护工具放 `scripts`。
- 不把业务仓库的模型测试、数据或账号配置复制进来。

## 文档与版本

- README 保留定位、安装和最小示例，API 细节只在 `docs/api.md` 维护。
- 开发、发布与剩余账号配置只在 `docs/maintaining.md` 维护。
- 版本唯一来源是 `pyproject.toml`；发布 tag 必须等于 `v<version>`。
- 开发未发布的变化记录在 `CHANGELOG.md` 的 `Unreleased`。
- 不自动移动已发布 tag，不把本地路径依赖作为正式发布来源。
- 当前交接状态及下一步见 `docs/maintaining.md#当前交接状态`。
