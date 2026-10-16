# cs2coach

CS2 demo 赛后复盘分析工具：找出玩家的决定性瞬间，用数据加 AI 诊断讲清楚问题和改进方法。

- 产品定位与待定事项：[docs/product.md](docs/product.md)
- demo 字段调研（哪些数据准确、哪些只能近似）：[docs/fields.md](docs/fields.md)
- 解析层的输出格式：[docs/parsing.md](docs/parsing.md)
- 开发约定与架构原则：[CLAUDE.md](CLAUDE.md)

当前阶段：解析层完成（回合、击杀、伤害、开枪、致盲、投掷物、经济与背包都已与 demo 中的独立数据核对，覆盖 ESL、5E、完美共 24 场）；决定性瞬间与指标已实现，包括基于地图几何的视野计算和 Leetify 口径的枪法统计（见 [docs/features.md](docs/features.md)）；AI 诊断已接入（DeepSeek、Claude、本机 Ollama 可切换）。

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

# 3b. 或者（方式二：本地编译）。用 winget 安装工具链（Build Tools 有几个 GB，需要较长时间）：
winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
winget install --id Rustlang.Rustup --exact
winget install --id Google.Protobuf --exact
#   安装后重新打开 PowerShell（让 PATH 生效），再编译：
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

# 核对解析结果（回合、记分板、致盲、开枪）。新来源的 demo 先跑这个
python scripts/check_parsing.py data/demos/xxx.dem

# 找出目标玩家的决定性瞬间及指标，结果写入 data/output/<玩家名>_<SteamID64>/<比赛名>/（同一 steamid 沿用已有的玩家文件夹）
python -m cs2coach analyze data/demos/xxx.dem --player <SteamID64>

# 加上 --ai：调用 AI 生成中文复盘报告（同一目录下的 report.md 和 submission.json）
python -m cs2coach analyze data/demos/xxx.dem --player <SteamID64> --ai

# 汇总同一玩家在 data/demos 中的全部比赛，不调用 AI
python -m cs2coach summary --player <SteamID64>

# 仅汇总指定比赛；压缩包含多个 demo 时用 --demo-member 指定
python -m cs2coach summary data/demos/xxx.zip --demo-member some-map.dem --player <SteamID64> --out <临时目录>
```

不加 `--ai` 时生成 `moments.json`、`facts.md`、`problems.md`。重新分析同一玩家时，原有的 `report.md` 和 `submission.json` 会按修改时间移入同目录的 `历史版本/`。

`summary` 直接从 demo 生成多场程序汇总，不读取单场输出；结果放在 `<输出根目录>/<玩家名>_<SteamID64>/汇总/`，包含 `problems.md` 和 `summary.json`。加 `--ai` 后还会生成 `report.md` 和 `submission.json`。重复运行会归档旧版文件。

### 选择模型

默认用 DeepSeek 的 `deepseek-flash`（便宜，约为 v4-pro 的十分之一）。重要比赛想要更细的分析时，用 `--model` 临时换成 `deepseek-v4-pro`，不用改配置：

```bash
python -m cs2coach summary --player <SteamID64> --ai --model deepseek-v4-pro
```

`--provider` 选模型服务（deepseek / claude / local），`--model` 覆盖该服务的模型名，都只对本次运行有效。

### 本地 Ollama 模型

先安装并启动 Ollama，拉取基础模型，再创建一个上下文足够大的模型。新建 `Modelfile`：

```text
FROM qwen3:14b
PARAMETER num_ctx 40960
```

```bash
ollama pull qwen3:14b
ollama create qwen3-14b-40k -f Modelfile
python -m cs2coach analyze data/demos/xxx.dem --player <SteamID64> --ai --provider local
python -m cs2coach summary --player <SteamID64> --ai --provider local
```

建议在启动 Ollama 前设置 `OLLAMA_FLASH_ATTENTION=1` 和 `OLLAMA_KV_CACHE_TYPE=q8_0`，并在跑报告时空出约 12–14 GB 显存。Windows PowerShell 可用 `$env:OLLAMA_FLASH_ATTENTION="1"` 和 `$env:OLLAMA_KV_CACHE_TYPE="q8_0"`，然后在同一环境启动 Ollama。`--provider local` 会覆盖配置里的 `[ai].provider`，不需要 API 密钥。默认访问 `http://localhost:11434/v1`；可在 `config/default.toml` 的 `[ai.local]` 中修改模型或地址。程序会在生成前查询模型的 `num_ctx`，并在每轮核对实际输入 token，避免 Ollama 静默截断历史。若使用其他 OpenAI 兼容的本地服务，可把 `context_check` 设为 `"none"`，此时不进行 Ollama 上下文核对。

`--ai` 用哪个模型由 `config/default.toml` 的 `[ai] provider` 决定（默认 `deepseek`），各模型的设置在 `[ai.deepseek]`、`[ai.claude]`。凭据：
- DeepSeek：环境变量 `DEEPSEEK_API_KEY`（Windows PowerShell：`$env:DEEPSEEK_API_KEY = "sk-..."`）
- Claude：环境变量 `ANTHROPIC_API_KEY`，或用 Anthropic CLI 执行 `ant auth login`

也可以把 key 写在项目根目录的 `.env` 文件里（一行一个，如 `DEEPSEEK_API_KEY=sk-...`）；`.env` 已在 `.gitignore` 中，不会提交。

职业基准（报告中"与职业样本对比"）由 `scripts/build_baseline.py` 默认读取清单 `src/cs2coach/baselines/pro_demos.toml`，从职业比赛 demo 生成 `src/cs2coach/baselines/pro_sample.json`。样本来源和实际规模以摘要显示的基准 `source` 为准；样本只来自少数职业队伍，仅作参考。

添加新的职业 demo：

1. 把 demo 压缩包放到 `data/demos/`，确认包内 demo 的完整文件名。
2. 在 `pro_demos.toml` 追加一项 `[[demos]]`，填写 `archive`（相对仓库根目录的路径）、`member`（包内文件名）、`event`（赛事）、`teams`（对阵）和 `map`（地图）；同图的服务器重启分段分别登记。
3. 在仓库根目录运行 `python scripts/build_baseline.py`。脚本按清单重新统计，跳过覆盖率不足的玩家和失败的 demo，汇总成功/失败情况，并将实际来源与人次写入基准文件。

视野计算首次运行时会从 GitHub 下载 awpy-data 的地图网格（约 150 MB，缓存在 `~/.awpy`）；下载不了时视野相关指标留空，其余分析照常。

运行 `pytest` 时设置环境变量 `CS2COACH_TEST_DEMO=<.dem 路径>`，会额外用这个 demo 跑端到端测试。

运行脚本时建议明确写出 demo 路径：不写路径时，脚本会取 `data/demos/` 下按文件名排序的第一个文件。
