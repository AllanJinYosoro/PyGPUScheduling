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

`uv.lock` 固定开发及 CI 环境，业务仓库维护自己的锁文件。CI 在 Ubuntu 上测试 Python 3.11–3.14，并单独验证 wheel 和 sdist。`check_project.py` 检查语法、无代码注释、核心导入边界、元数据与文档本地链接；`smoke_test.py` 验证安装包的版本、CPU 依赖顺序、动态任务和恢复。

## 版本与发布

版本唯一来源为 `pyproject.toml`，tag 必须为 `v<version>`。已发布 tag 不移动，同一版本不使用不同内容重新发布。

- `0.1.x` 保持首批接口和状态格式兼容，patch 用于兼容错误修复。
- 开发阶段的接口调整使用新的 minor，并在变更记录明确说明。
- 稳定后发布 `1.0.0`，此后遵循语义化版本。
- 恢复兼容范围包括任务定义、处理器和订阅者名称、事件语义及 `scheduler.json` 格式；格式变化必须说明旧状态的兼容与迁移方式并验证恢复。

仓库为 [AllanJinYosoro/PyGPUScheduling](https://github.com/AllanJinYosoro/PyGPUScheduling)。开发变化写入 `CHANGELOG.md` 的 `Unreleased`。每次发布按顺序执行：

1. 用 `uv version <version>` 更新版本及锁文件，将变化归入带实际日期的版本段。
2. 完成开发验证，提交并推送 `main`，确认该提交的 [CI](https://github.com/AllanJinYosoro/PyGPUScheduling/actions/workflows/ci.yml) 全部通过。
3. 检查 tag 与版本一致后创建并推送 tag，以 `0.1.0` 为例：

```bash
uv run python scripts/check_release.py v0.1.0
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
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
uv add "pygpu-scheduling>=0.1.0,<0.2.0"
uv sync --locked
```

也可使用已发布的固定 GitHub tag：

```bash
uv add git+https://github.com/AllanJinYosoro/PyGPUScheduling.git --tag v0.1.0
```

联调时使用 `uv add --editable ../PyGPUScheduling`，提交业务仓库前恢复远程版本依赖，一同提交 `pyproject.toml` 与 `uv.lock`。迁移旧 `task-scheduling` 依赖时先移除旧分发包，避免两个包同时提供 `task_scheduling` 导入名；业务适配层留在调用方仓库。

## 当前交接状态

- `0.1.0` 已于 2026-11-11 发布：[PyPI](https://pypi.org/project/pygpu-scheduling/0.1.0/)、[GitHub Release](https://github.com/AllanJinYosoro/PyGPUScheduling/releases/tag/v0.1.0)。tag 固定于 `26f770f`；初始核心来源见 `CHANGELOG.md`。
- 发布提交的 Python 3.11–3.14 及安装包 [CI 已通过](https://github.com/AllanJinYosoro/PyGPUScheduling/actions/runs/42970852316)，[OIDC 发布成功](https://github.com/AllanJinYosoro/PyGPUScheduling/actions/runs/42970989855)。`pypi` environment 仅允许 `v*` tag，Trusted Publisher 已经实际上传验证，后续无需重建 Pending Publisher。
- 本地 29 项测试、项目与版本检查、两个示例、wheel/sdist 独立安装验证通过；从正式 PyPI 隔离安装的依赖顺序、动态任务和恢复验证通过。PyPI 两个分发文件的 SHA256 均与 CI 产物一致。
- 发布 job 的 setup-uv 空目录提示及 GitHub Ubuntu 运行器迁移公告均未影响此次结果；后续改动仍写入 `Unreleased`。
- GPU 实机尚未验证；现有测试覆盖 CPU 子进程和模拟 GPU 行为。下游业务仓库迁移另行处理；本项目不维护独立 Agent 记忆。
