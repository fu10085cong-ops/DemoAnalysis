# CS2 Demo 复盘分析工具（cs2coach）

## 项目背景
**2026-10-19 起，项目方向以 `docs/research_roadmap.md` 为准：基于职业选手 Demo 的可迁移行为规律发现。** 从职业比赛中识别有重复性、可解释、可训练的行为模式，在相似局势中与结果对比并人工核验，产出带真实 Demo 证据、适用条件和训练方法的**行为规律卡**。北极星指标是经人工核验的规律卡数量，不是指标数量。基本功指标（急停、预瞄等）保留但冻结扩展，只作为研究的特征和控制变量。**新增任何指标、模块之前，先回答路线图第 9 节的三个问题。**

- 代码盘点（哪些模块保留、冻结、暂停）：`docs/research_inventory.md`
- 原有的**玩家诊断程序**（报告页、段位阶梯、类别面板、问题检测、AI 报告）已冻结，小范围使用，只修 bug；它的定位与历史决定见 `docs/product.md`
- **当前进度、待办和工作约定见 `docs/status.md`（新会话、换机器后先读）**；评测结果见 `docs/eval_log.md`
- **只做赛后分析**：不涉及任何局内实时功能，不读取游戏内存，不注入任何客户端

## 当前阶段目标：第一个研究闭环（路线图第 5、7 节）
- 课题：Mirage，CT 首次从 5v5 变为 5v4 后 5 秒内的位置与接战调整
- 输入：职业 demo（.dem / .dem.bz2 / .dem.gz / .zip / .rar；.rar 解压依赖 unrar / 7z / bsdtar 任一）
- 输出：可追溯的行为窗口（`demo_id`、`round_id`、`player_id`、`start_tick`、`end_tick`）、行为标签（允许 `unknown`、多标签、置信度）、人工复核记录、一张行为规律卡（允许结论为"证据不足"）
- 分析单位是**局势中的某个玩家**，不是整场均值；区分比赛真实状态与玩家当时可能知道的信息（几何视野、雷达），不用结果反向定义行为
- 同一回合、同一场比赛的样本不独立，比较时按比赛分组
- **所有阈值放在 `config/` 下的配置文件里，不要写死在代码中**

## 架构原则（重要）
1. **分层**（路线图第 4 节）：解析层 `parsing/` → 特征层 `features/`（基本功指标、视野、局势上下文）→ **局势与行为建模层**（Situation、行为窗口、行为标签）→ **行为研究引擎**（局势分层与匹配、行为分布、结果对比、反例、置信度）→ **证据层**（`demo_id` + 回合 + tick 区间 + 人工复核记录，不可省略）→ 教学与大模型层（后续）。冻结的诊断程序仍是 解析 → 特征 → 工具 → AI → 报告
2. **程序负责确定性的部分，AI 负责判断和表达**：解析、找出决定性瞬间、计算指标、与职业基准对比都由程序完成，结果可复核。AI 通过工具查数据，再判断轻重、做分析并写成报告。AI 的每个结论都必须引用具体的回合和 tick，程序会检查这些引用是否真实存在
3. **每条结论都必须能回溯到具体的回合和 tick**
4. **AI 模型可替换**：分析引擎与具体模型解耦（以后会按用户档位通过路由中转调用不同模型）。便宜的模型也要能用好，所以尽量事先算好，工具设计要简单、不容易用错
5. 为后续阶段预留扩展空间，但**现在不要实现**：回合胜率模型（用胜率变化 ΔWP 定位关键失误）、账号与计费服务端。"玩家当时可能知道的信息"按路线图需要，先用几何视野 + 雷达 `spotted` 做简化版本，不做完整的信息集重建；观察性数据的因果解释必须保守（路线图第 2、5.6 节）
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
- **与 GPT（Codex）协作**：Claude 负责和用户沟通、定方案、写任务单、审查改动；GPT 按任务单写代码。规则见 `AGENTS.md`，任务单放在 `docs/tasks/`（格式见其中的 README.md）

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
