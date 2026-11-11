# 开发与发布

## 开发验证

```bash
uv sync --locked
uv run python scripts/check_project.py
uv run pytest
uv run python examples/basic.py
uv run python examples/artifacts.py
uv build --no-sources
uv run --isolated --no-project --with dist/*.whl python scripts/smoke_test.py
uv run --isolated --no-project --with dist/*.tar.gz python scripts/smoke_test.py
```

`uv.lock` 固定开发及 CI 环境，业务仓库维护自己的锁文件。CI 在 Ubuntu 上测试 Python 3.11–3.14，并单独验证 wheel 和 sdist。`check_project.py` 检查语法、无代码注释、核心导入边界、元数据与文档本地链接；`smoke_test.py` 验证安装包的 CPU 依赖顺序、动态任务、任务级配置和产物失效重算。

## 版本与发布

版本唯一来源为 `pyproject.toml`，tag 必须为 `v<version>`。已发布 tag 不移动，同一版本不使用不同内容重新发布。

- `0.1.x` 保持首批接口和状态格式兼容，patch 用于兼容错误修复。
- 开发阶段的接口调整使用新的 minor，并在变更记录明确说明。
- 稳定后发布 `1.0.0`，此后遵循语义化版本。
- 恢复兼容范围包括任务定义、处理器和订阅者名称、事件语义及 `scheduler.json` 格式；格式变化必须说明旧状态的兼容与迁移方式并验证恢复。

仓库为 [AllanJinYosoro/PyGPUScheduling](https://github.com/AllanJinYosoro/PyGPUScheduling)。开发变化写入 `CHANGELOG.md` 的 `Unreleased`。每次发布按顺序执行：

1. 用 `uv version <version>` 更新版本及锁文件，将变化归入带实际日期的版本段。
2. 完成开发验证，提交并推送 `main`，确认该提交的 [CI](https://github.com/AllanJinYosoro/PyGPUScheduling/actions/workflows/ci.yml) 全部通过。
3. 检查 tag 与版本一致后创建并推送 tag，以 `0.2.0` 为例：

```bash
uv run python scripts/check_release.py v0.2.0
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin v0.2.0
```

4. 确认 [发布工作流](https://github.com/AllanJinYosoro/PyGPUScheduling/actions/workflows/release.yml) 成功，再从 PyPI 独立安装对应版本运行 `scripts/smoke_test.py`，核对 PyPI 的版本、文件及仓库链接。更新本页交接状态。

`release.yml` 验证 tag、运行测试、构建并验证分发包，再由独立 publish job 使用 OIDC 上传。只有 publish job 具有 `id-token: write`；无需长期 PyPI token。源码、wheel 与 tag 必须来自同一提交。

## PyPI 账号配置

发布使用以下绑定；迁移仓库或工作流时同步更新两端：

- [GitHub Environments](https://github.com/AllanJinYosoro/PyGPUScheduling/settings/environments)：`pypi`，仅允许 `v*` tag 部署。
- PyPI Trusted Publisher：绑定字段如下。

| 字段 | 值 |
| --- | --- |
| PyPI project name | `pygpu-scheduling` |
| GitHub owner | `AllanJinYosoro` |
| Repository | `PyGPUScheduling` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

新项目在 [PyPI Publishing](https://pypi.org/manage/account/publishing/) 添加 Pending Publisher，首次成功上传后自动转为普通绑定。已有项目在项目设置中管理 Trusted Publisher。不把凭证写入仓库。

参考：[PyPI 首次项目与 Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)、[uv 发布指南](https://docs.astral.sh/uv/guides/integration/github/#publishing-to-pypi)。

## 业务仓库接入

声明版本依赖：

```bash
uv add "pygpu-scheduling>=0.2.0,<0.3.0"
uv sync --locked
```

也可使用已发布的固定 GitHub tag：

```bash
uv add git+https://github.com/AllanJinYosoro/PyGPUScheduling.git --tag v0.2.0
```

联调时使用 `uv add --editable ../PyGPUScheduling`，提交业务仓库前恢复远程版本依赖，一同提交 `pyproject.toml` 与 `uv.lock`。迁移旧 `task-scheduling` 依赖时先移除旧分发包，避免两个包同时提供 `task_scheduling` 导入名；业务适配层留在调用方仓库。

## 当前交接状态

- 当前开发版本为 `0.2.0`，发布按上文执行；已发布的 `0.1.0` 见 [PyPI](https://pypi.org/project/pygpu-scheduling/0.1.0/)。
- 新版自动读取状态格式 1 并保存为格式 2，兼容边界见 [API 与恢复协议](api.md#状态与恢复)。
- 测试覆盖真实 CPU 子进程和模拟 GPU，尚未完成 GPU 实机验证。下游业务仓库迁移另行处理。
