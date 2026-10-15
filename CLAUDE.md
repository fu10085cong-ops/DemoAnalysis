# CS2 Demo 复盘分析工具（cs2coach）

## 项目背景
分析 CS2 比赛 demo：找出指定玩家的决定性瞬间，用数据加 AI 诊断讲清楚问题和改进方法。
**产品目标：帮助玩家科学、准确地找到自己的具体问题，并通过针对性训练解决问题、提升水平。** 质量标准是分析达到甚至超过主播帮人看 demo 的水准（"代替主播"是结果，不是目标市场）。完整的产品定位和待定事项见 `docs/product.md`，涉及产品方向的取舍以它为准。
**只做赛后分析**：不涉及任何局内实时功能，不读取游戏内存，不注入任何客户端。

## MVP 目标（证明分析是对的）
- 输入：demo 文件（.dem / .dem.bz2 / .dem.gz / .zip / .rar）+ 目标玩家 SteamID64；压缩包内有多个 demo 时用 `--demo-member` 指定
- .rar 解压依赖系统工具（unrar / 7z / bsdtar 任一；macOS、Windows 10+ 自带 bsdtar，Linux 需 `apt install libarchive-tools`）
- 输出：决定性瞬间列表（先做击杀、死亡、关键助攻），每个瞬间附带准确的指标数据和 CS2 游戏内跳转命令，再由 AI 生成中文诊断
- 形态：本地运行的简单网页（localhost）；demo 在本地解析
- 不包含：账号、计费、服务端、安装包
- **所有阈值放在 `config/` 下的配置文件里，不要写死在代码中**

## 架构原则（重要）
1. **分层**：解析层 `parsing/` → 特征层 `features/`（找出决定性瞬间、计算指标）→ 工具层（把查询能力包装成 AI 可调用的工具）→ AI 判断与表达 → 报告
2. **程序负责确定性的部分，AI 负责判断和表达**：解析、找出决定性瞬间、计算指标、与职业基准对比都由程序完成，结果可复核。AI 通过工具查数据，再判断轻重、做分析并写成报告。AI 的每个结论都必须引用具体的回合和 tick，程序会检查这些引用是否真实存在
3. **每条结论都必须能回溯到具体的回合和 tick**
4. **AI 模型可替换**：分析引擎与具体模型解耦（以后会按用户档位通过路由中转调用不同模型）。便宜的模型也要能用好，所以尽量事先算好，工具设计要简单、不容易用错
5. 为后续阶段预留扩展空间，但**现在不要实现**：回合胜率模型（用胜率变化 ΔWP 定位关键失误）、信息集重建、账号与计费服务端
6. **基于地图几何的视野计算已提前到 MVP**（2026-10-14 产品负责人决定）：Time to Damage、Crosshair Placement 等指标需要准确的"第一次看见敌人"时刻，雷达 spotted 精度不够（docs/fields.md §8、docs/metrics_research.md）
7. **枪法指标沿用 Leetify 的名称和口径**（Time to Damage、Crosshair Placement、Counter-Strafing、Spray Accuracy、Spotted Accuracy、Headshot Accuracy），定义见 docs/metrics_research.md；与 Leetify 不同的地方必须在 docs/features.md 中写明

## 技术约定
- Python 3.11+，demoparser2 解析，pydantic 定义数据结构，pytest 测试，**每个指标计算和决定性瞬间识别都要有单元测试**
- 依赖用 `pyproject.toml` 管理；本地开发用 uv：`uv venv && uv pip install -e ".[dev]"`，**然后必须执行 `python scripts/build_demoparser.py --install`** 覆盖安装修复版 demoparser2（需要 Rust 和 protoc；原因见下方陷阱）
- 跨平台（Mac / Windows / Linux）：路径一律用 `pathlib`，文本文件读写显式指定 `encoding="utf-8"`，不依赖 shell 特性
- CLI：`python -m cs2coach analyze <demo路径> --player <steamid64>`
- `data/`（demo 和输出）已加入 .gitignore

## 工作方式
- **不要凭记忆假设 demoparser2 的字段名和事件名**，以实际解析结果为准；不确定就先写脚本验证
- 字段调研结论（哪些数据准确、哪些只能近似）见 `docs/fields.md`；复现方法：`scripts/explore_demo.py`、`scripts/verify_semantics.py`
- 解析层（`parsing/match.py`）改动后，或拿到新来源的 demo 时，跑 `scripts/check_parsing.py` 与 demo 中的独立数据逐项核对
- 解析层输出格式见 `docs/parsing.md`；决定性瞬间与各指标的定义、准确度见 `docs/features.md`
- 工具层在 `src/cs2coach/tools/`（整场摘要、查询工具、引用检查），AI 层在 `src/cs2coach/ai/`（提示词、调用循环）。单元测试用假客户端，不调用真实 API；真实调用会产生费用，运行前先征得用户同意
- 分步推进，每一步完成后停下来等用户确认；遇到重要的设计取舍时，先列出选项和利弊，再动手

## demoparser2 关键陷阱（详见 docs/fields.md §0、§1）
- 不同来源（Valve / ESL / 完美 / 5E）的 demo 事件集合差异很大（ESL 赛事 demo 没有 `player_blind`、`buytime_ended`；完美、5E、ESL 的回合事件是 demoparser2 合成的）。**只依赖所有来源都有的数据**，替代方案见 docs/fields.md §2、§3
- `parse_ticks` 对不存在的字段**静默丢弃**，必须检查返回的列；`parse_event` 对不存在的事件返回空 `list`
- **不要用 demoparser2 的 `velocity*` 字段**（结果依赖请求的 tick，可能出错或缺列），速度自己用连续 tick 的 X/Y 差分计算
- 事件里的 steamid 是字符串，`parse_ticks` 返回的 steamid 是整数
- 死亡那一 tick 的 `inventory` 已经清空，要取 tick-1
- `parse_ticks` 每次调用都会扫描整个 demo，要合并调用
- **PyPI 上的 demoparser2 0.42.0 有已确认的 bug**：实体编号 > 2047 的角色（完美 demo 中常见）读不出数据，武器句柄也可能读错。上游已修复（issue #362）但未发布，所以用 `scripts/build_demoparser.py` 编译的修复版。解析层仍要逐个玩家检查数据完整性，目标玩家缺数据时明确报错
- 完美 demo 事件里的玩家名前面带空格：一律按 steamid 匹配
- 5E 有热身回合混在回合事件里：回合切分要过滤 `is_warmup_period`
