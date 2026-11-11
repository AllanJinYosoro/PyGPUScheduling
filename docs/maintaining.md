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

`uv.lock` 用于开发及 CI 的可复现环境，各业务仓库仍维护自己的锁文件。CI 在 Ubuntu 上配置 Python 3.11、3.12、3.13、3.14 的测试矩阵，并单独验证安装包。

`scripts/check_project.py` 检查 Python 语法、无代码注释、核心导入边界、包元数据和文档本地链接。`scripts/smoke_test.py` 应在独立安装环境中运行，检查包版本、CPU 依赖顺序、动态任务和恢复。

## 目录

| 目录或文件 | 维护内容 |
| --- | --- |
| `src/task_scheduling` | 通用调度核心，不能导入业务框架 |
| `tests` | 调度、恢复、GPU 预算与失败行为测试 |
| `examples` | CPU 流程和外部产物处理示例 |
| `docs/api.md` | 公共 API 与恢复契约的唯一详细说明 |
| `scripts` | 项目检查、安装包验证、发布版本检查 |
| `.github/workflows/ci.yml` | 测试矩阵及安装包检查 |
| `.github/workflows/release.yml` | tag 验证、构建及 PyPI Trusted Publishing |

## 版本约定

版本唯一来源为 `pyproject.toml`。Git tag 格式为 `v<version>`，发布工作流通过 `scripts/check_release.py` 验证两者一致。

- `0.1.x` 保持首批接口和状态格式兼容，patch 用于兼容错误修复。
- 开发阶段的接口调整使用新的 minor，并在变更记录明确说明。
- 达到稳定接口后发布 `1.0.0`，后续 major 表示不兼容变化、minor 表示兼容功能、patch 表示兼容修复。
- 已发布 tag 不移动，同一版本不使用不同内容重新发布。
- 兼容范围包含任务定义、处理器与订阅者名称、事件语义及 `scheduler.json` 格式。状态格式变化需要迁移说明和恢复测试。

开发变化写在 `CHANGELOG.md` 的 `Unreleased`。发布时为实际版本建立日期段，并检查源码分发包和 wheel。

## 首次连接 GitHub

在自己的 GitHub 账号下建立空的 public 仓库 `PyGPUScheduling`，然后在本地添加实际远程地址并推送 `main`。当前没有预设 GitHub 所有者，也没有填写虚构的远程 URL。

远程地址确定后，补充 `pyproject.toml` 的 `[project.urls]`，提供真实的 Source、Issues 和 Changelog 链接；同步 README 中需要的远程接入说明。`OWNER` 在以下命令中仅表示实际 GitHub 所有者。

```bash
git remote add origin https://github.com/OWNER/PyGPUScheduling.git
git push -u origin main
```

先确认 GitHub CI 全部通过，再建立公开发布 tag。仓库默认不保存密钥或账号 token。

## 首次发布 PyPI

1. 登录 PyPI，确认并选择最终分发名称 `pygpu-scheduling`。首次发布前检查名称可用性，HTTP 404 也不代表已为本项目保留名称。
2. 在 GitHub 仓库建立 `pypi` environment。
3. 在 PyPI 的 Publishing 设置中配置 Pending Trusted Publisher：实际 GitHub owner、仓库 `PyGPUScheduling`、工作流文件 `release.yml`、environment `pypi`。首次项目由成功上传建立。
4. 核对 MIT 许可证、版权署名、README、公共接口和版本号，补齐实际仓库 URL；将 README 的相对文档链接补为实际 GitHub 链接，以便 PyPI 页面跳转。
5. 完成开发验证，确保源码包和 wheel 都能独立安装运行。
6. 将变更记录标记为实际发布版本和日期，提交后创建并推送对应 tag。

```bash
uv run python scripts/check_release.py v0.1.0
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

`release.yml` 会重新运行测试、核对 tag 与包版本、构建及验证分发包，然后在独立 publish job 使用 OIDC 上传。构建 job 没有 PyPI 发布权限，publish job 只有 `id-token: write`，不需要保存长期 PyPI token。workflow 已准备，账号侧绑定仍需在自己的账号中完成。

参考：[uv 发布指南](https://docs.astral.sh/uv/guides/integration/github/#publishing-to-pypi)、[PyPI 首次项目与 Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)。

## 后续发布

通过 `uv version 0.1.1` 更新版本及锁文件，记录变化，完成上述验证后提交并建立 `v0.1.1`。发布前可以执行 `uv run python scripts/check_release.py v0.1.1`。源码、wheel 与 tag 必须来自同一提交。

## 业务仓库接入

GitHub 首次 tag 发布后，业务项目直接声明依赖，无需把源码 clone 到业务仓库里：

```bash
uv add git+https://github.com/OWNER/PyGPUScheduling.git --tag v0.1.0
uv sync --locked
```

PyPI 发布后可以改用版本范围：

```bash
uv add "pygpu-scheduling>=0.1.0,<0.2.0"
```

联调时可临时使用同级 checkout：`uv add --editable ../PyGPUScheduling`。提交业务仓库前恢复远程版本依赖，并一同提交 `pyproject.toml` 与 `uv.lock`。

分发包名从提取前的 `task-scheduling` 改为 `pygpu-scheduling`，Python 导入名仍为 `task_scheduling`。DMMBaseline 当前仍使用自己的本地包副本；后续迁移时移除旧分发依赖再添加新依赖，避免两个发行包同时提供相同 Python 导入名。模型、Optuna 与产物协议保留在业务仓库的适配层中。

## 当前交接状态

初始核心来自 DMMBaseline 提交 `2781c6a`。本次提取保持 `src/task_scheduling` 源码不变，独立迁入通用测试，新增资源与故障测试、示例、元数据和维护设施。

| 事实面 | 状态 |
| --- | --- |
| 本地代码与包元数据 | Python 3.11 下 29 项测试、项目检查、版本检查、两个示例及 wheel/sdist 独立安装验证通过；核心 9 个源码文件与提取来源逐文件一致 |
| 本地 Git | 以 `main` 初始化，版本文件为 `0.1.0`，未将初始提交等同于正式发布 |
| 文档与 Agent 规则 | 使用本仓库 README、docs 和 AGENTS.md，后续会话从这里继续 |
| GitHub 远程与 CI | 待账号侧建立仓库、推送并验证；工作流 YAML 已本地解析检查，Python 3.12–3.14 矩阵仍待远程运行 |
| PyPI | 待确认名称、账号绑定、首次 tag 与实际发布 |
| GPU 实机 | 尚未验证；CPU 子进程与模拟 GPU 行为属于本地验证范围 |
| DMMBaseline 接入 | 待独立仓库发布后单独迁移；原仓库本次不做改动 |
| 平台长期记忆 | 本项目不维护独立的 Agent 记忆文件 |

下一次 Agent 优先读取本节，核对当前 Git 状态和 CI，再处理远程、发布及下游迁移。不要把工作流文件存在视为已在 GitHub 执行，也不要把本地构建成功视为已发布 PyPI。
