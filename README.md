# cs2coach

CS2 demo 赛后复盘分析工具：找出玩家的决定性瞬间，用数据加 AI 诊断讲清楚问题和改进方法。

- 产品定位与待定事项：[docs/product.md](docs/product.md)
- demo 字段调研（哪些数据准确、哪些只能近似）：[docs/fields.md](docs/fields.md)
- CS2 引擎数据边界与分析上限：[docs/engine_limits.md](docs/engine_limits.md)
- 开发约定与架构原则：[CLAUDE.md](CLAUDE.md)

当前阶段：本地 CLI MVP。`analyze` 会按 SteamID64 解析 demo、生成事件时间线、确定性指标和 JSON/Markdown 报告；AI 主观诊断、地图视线重建和职业基准库尚未接入。

## 环境搭建

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。另外，由于 PyPI 上的 demoparser2 0.42.0 有已确认的 bug（[docs/fields.md](docs/fields.md) §0 陷阱 12），需要安装从上游修复版编译的 demoparser2。有两种方式：

- **方式一：下载编译好的 wheel（Windows 推荐）。** 在 GitHub 仓库的 Actions 页面，打开最近一次成功的 `ci` 运行，下载附件 `demoparser2-windows-wheel`，解压后安装（见下文）。
- **方式二：本地编译。** 需要额外安装 git、Rust 和 protoc，然后运行 `scripts/build_demoparser.py`。

### Windows（PowerShell）

```powershell
# 1. 基础工具
winget install astral-sh.uv
winget install Git.Git

# 2. 项目环境
git clone https://github.com/fu10085cong-ops/DemoAnalysis.git
cd DemoAnalysis
uv venv --python 3.12
.venv\Scripts\activate
uv pip install -e ".[dev]"

# 3a. 安装修复版 demoparser2（方式一：使用下载的 wheel）
uv pip install --reinstall --no-deps path\to\demoparser2-0.42.0-cp312-cp312-win_amd64.whl

# 3b. 或者（方式二：本地编译）
#   - Rust：安装 https://rustup.rs 的 rustup-init.exe；它提示安装 Visual Studio C++ Build Tools 时选择同意
#   - protoc：从 https://github.com/protocolbuffers/protobuf/releases 下载 protoc-*-win64.zip，
#     解压后把其中的 bin 目录加入 PATH
python scripts\build_demoparser.py --install

# 4. 验证
pytest
```

解压 .rar 格式的 demo 会用到 Windows 10/11 自带的 `tar.exe`，不需要额外安装。

### macOS

```bash
brew install uv protobuf
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
source "$HOME/.cargo/env"

uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
python scripts/build_demoparser.py --install
pytest
```

## 使用

把 demo 放进 `data/demos/`（该目录不会提交到仓库）。支持 `.dem`、`.dem.bz2`、`.dem.gz`、`.zip`、`.rar`。

```bash
# 探查 demo 里有哪些事件和字段（结果写入 data/output/explore/）
python scripts/explore_demo.py data/demos/xxx.dem

# 验证关键字段的含义与替代方案
python scripts/verify_semantics.py data/demos/xxx.dem

# 压缩包内有多个 demo 时，用 --demo-member 指定
python scripts/explore_demo.py data/demos/xxx.rar --demo-member some-map.dem
```

运行脚本时建议明确写出 demo 路径：不写路径时，脚本会取 `data/demos/` 下按文件名排序的第一个文件。

### 生成复盘报告

```powershell
python -m cs2coach analyze data/demos/match.dem --player 76561198000000000 --out data/output
```

报告包含击杀/阵亡/助攻、伤害、队友补枪时间线、闪光效果、移动开火样本和证据 tick；默认同时写出 JSON 与 Markdown。所有可调阈值位于 `config/default.toml`，可用 `--config` 指定另一份 TOML。

```powershell
python -m cs2coach analyze data/demos/match.rar --demo-member map.dem --player 76561198000000000
```

`demo_gototick` 仅作为回放跳转候选命令写入报告，仍需在目标 CS2 版本中实测。移动开火判断使用 tick 间隔平均速度，不能作为 sub-tick 瞬时速度或单独的枪法结论。引擎数据边界见 [docs/engine_limits.md](docs/engine_limits.md)。
