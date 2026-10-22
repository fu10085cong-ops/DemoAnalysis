# CS2 战术决策 AI（cs2coach / DemoAnalysis）

## 项目定位
**以 MiMo 等通用大模型为推理核心的 CS2 战术决策研究**（2026-10-22 起，方向与阶段以 `docs/tactical-roadmap.md` 为准）。

在严格限定为玩家当时合法可获得的信息下，提出可执行的战术建议、解释依据，并用独立评测检验质量。核心顺序：**先解决信息边界，再做大模型决策，最后研究最优性**。

- **当前进度、待办和工作约定见 `docs/status.md`（新会话、换机器后先读）**
- 之前的方向（2026-10-19 至 10-22，职业 Demo 行为规律研究）及其规则归档在 `docs/history/`；其成果（R03、规律卡）作为职业案例检索的证据保留，见路线图第 13 节
- **现阶段只做离线 Demo 分析**：不涉及任何局内实时功能，不读取游戏内存，不注入任何客户端；未来任何实时使用必须遵守游戏和赛事规则
- **验收不能以"回答像教练"为标准**：要可追溯、可复现、经独立评测

## 原则（路线图第 3、6、8、11 节）
1. **三层隔离**：
   - **Ground Truth**：Demo 里的全部真实状态，只用于打标签和评测
   - **Observation**：目标玩家到决策时刻为止，通过视觉、声音、雷达或可靠的队友报点已经获得的事实
   - **Belief**：由 Observation 推断的未知敌人位置和意图，必须带置信度、证据和不确定性
2. **信息不越界**：
   - 所有观测的时间都不晚于决策时刻，并且要验证这条信息玩家确实能拿到
   - 不能把 Demo 里记录的事件直接当作目标选手看到或听到的事件
   - 无法准确重建的观测标 `unknown`，不能默认为可见
   - 工具查询只能返回经过权限过滤的状态。玩家不知道某处有没有人时，工具返回 `unknown`，不能返回 Demo 的真实坐标
3. **模型输出要区分事实、推测和未知**：
   - 至少比较三个可执行动作
   - 按 JSON 输出 `recommended_action`、`target_area`、`evidence`、`alternatives`、`risks`、`missing_information`
   - 不把模型随口给出的概率当作已校准的胜率
4. **每条记录都可追溯**：`match_id`、`round_id`、`decision_tick`、`player_id`；每条观测保留来源（tick、source、kind、area、confidence），以便审计
5. **评测按比赛、时间、战队划分**，避免相邻 tick 泄漏。测四个方面：事实一致性与信息泄漏率、动作合法率、与职业选择的 Top-k 一致性（只作参考，不代表最优）、教练或高水平选手盲评
6. **历史胜率只是相关性**，不当作反事实因果结论
7. **缺数据记 `null` / `unknown`**，不编造
8. **所有阈值放在 `config/` 下的配置文件里，不要写死在代码中**

## 架构（路线图第 5 节）
```
A. parsing/                    Demo 解压与解析（已有）
B. tactical/observations.py    合法信息重建，可见性 / 可听性边界
C. tactical/snapshot.py        决策时点的结构化快照 TacticalSnapshot（pydantic）
D. tactical/map_graph.py       地图区域连接、可达性、路径时间
E. tactical/decision_points.py 残局、首杀、爆弹、阵亡、发现 C4 等决策节点
F. llm/client.py               MiMo API 适配（可更换模型）
G. llm/tools.py                只允许查询经过权限过滤的状态
H. llm/validator.py            校验 JSON、目标区域、动作合法性
I. evaluation/                 数据集划分、指标、对照实验
```
- 模型只能通过受控查询访问 TacticalSnapshot，不直接接触全图 Demo 对象
- **可复用的现有零件**（对照表见路线图 13.1）：
  - `research/threat.py`：雷达、声音、枪声、接火点，以及只用决策前信息的 `pre_danger`
  - `research/potential.py`：历史 T 分布，可作为 Belief 的起点
  - `features/visibility.py`：几何视野
  - awpy 导航网格与 `research/windows.py::_nav_distance`
  - `scripts/research_text_ai.py::timeline()`：快照雏形
  - `scripts/research_vision.py::_clients()`：MiMo 多账号
- 初始动作集合：HOLD、FALLBACK、ROTATE_A、ROTATE_B、PUSH、REPOSITION、GATHER_INFO、SUPPORT、DELAY、RETAKE、SAVE、COMMIT。动作带目标区域和可行性约束
- 规律卡（`docs/cards/`）用于阶段 5 的"职业案例检索"：快照的局势字段是检索的钥匙；卡里的"能说 / 不能说"由 validator 执行
- **先不要做**：微调（SFT / LoRA）、信念模型（阶段 5 之前）、模拟反事实与离线强化学习、账号与计费服务端

## 人工复核
- **以游戏内看 demo 为主**：产品负责人用与 demo 版本匹配的历史版本 CS 在游戏里看；复盘包要给出 `demo_gototick` 跳转命令
- 动画回放页（`scripts/research_replay.py` / `.html`）**只作参考**：它画出了全部选手的真实位置，超出玩家当时可知的信息
- 盲审时先判断、再看 AI 或程序的答案；人的判断也会变，结论要看多种来源是否一致

## 冻结的诊断程序
原有的**玩家诊断程序**（单场 / 多场报告页、问题检测、段位阶梯、类别面板、AI 结构化报告）2026-10-19 起**冻结**，小范围使用，**只修 bug，不再扩展**。
- 定位与历史决定：`docs/product.md`；口径：`docs/features.md`；任务记录：`docs/tasks/`（001–056）
- CLI：`python -m cs2coach analyze <demo> --player <steamid64>`、`python -m cs2coach summary --player <steamid64>`；默认与入门（5E B 段，8 名代表玩家）比较
- 枪法指标沿用 Leetify 的名称和口径（`docs/metrics_research.md`），与 Leetify 不同之处写在 `docs/features.md`

## 大模型调用
- **真实 API 调用会产生费用，运行前先征得用户同意并报预估费用**；默认用 MiMo（`.env` 里的 `MIMO_API_KEY`、`MIMO_API_KEY_2`…，多个账号轮流并发）；单元测试用假客户端
- 按关键事件触发调用，不逐 tick 调用；长期记忆放外部存储，不靠无限增长的对话历史
- 推理模型要给足 `max_tokens`，否则推理没写完就没有 JSON

## 数据约定
- `data/` 不进仓库（demo、研究表、输出、代表玩家名单都在这里）
- 职业 Mirage demo 放在 `data/demos/pro_mirage/`，`manifest.json` 记录来源（HLTV 比赛编号与链接、赛事、战队）；早期职业样本清单在 `src/cs2coach/baselines/pro_demos.toml`
- 同一场比赛被拆成多段的 demo（如 `-p1`、`-p2`）共用同一个 `match_id`
- 研究表中可以出现职业选手名（公开比赛）；非职业玩家的真实姓名和 SteamID 不进仓库（提交前有隐私扫描钩子）
- **HLTV 有机器人防护，不要自动抓取或绕过**；新 demo 由产品负责人下载或提供
- 研究数据目录的分类与按名字查找：`docs/research.md` 末节（`src/cs2coach/research/paths.py`）

## 技术约定
- Python 3.11+，demoparser2 解析，pydantic 定义数据结构，pytest 测试。**信息过滤、快照字段、决策节点抽取、动作校验和指标计算都要有单元测试**（用合成比赛，`tests/helpers/synthetic.py`），信息过滤必须覆盖：未来事件泄漏、没看见的敌人、来源不明的脚步
- 依赖用 `pyproject.toml` 管理；本地开发用 uv：`uv venv && uv pip install -e ".[dev]"`，**然后必须执行 `python scripts/build_demoparser.py --install`** 覆盖安装修复版 demoparser2（需要 Rust 和 protoc；原因见下方陷阱）
- 跨平台（Mac / Windows / Linux）：路径一律用 `pathlib`，文本文件读写显式指定 `encoding="utf-8"`，不依赖 shell 特性
- 支持的 demo 格式：.dem / .dem.bz2 / .dem.gz / .zip / .rar（.rar 依赖 unrar / 7z / bsdtar 任一）
- 逐场处理的脚本支持 `--jobs`，默认取 `[tools] jobs`（2）；每个进程约占 1.3–1.5 GB 内存，进程数受空闲内存限制

## 工作方式
- **不要凭记忆假设 demoparser2、awpy 的字段名、事件名和接口**，以实际结果为准；不确定就先写脚本验证，结论写进 `docs/fields.md`
- 字段调研结论见 `docs/fields.md`；复现方法：`scripts/explore_demo.py`、`scripts/verify_semantics.py`。解析层（`parsing/match.py`）改动后，或拿到新来源的 demo 时，跑 `scripts/check_parsing.py` 逐项核对
- **占用 CPU 的任务（解析 demo、导出研究表、重建基准、回归、全量页面检查、批量截图）开始前先告诉产品负责人：跑什么、用几个进程、大约多久**；默认少占 CPU，产品负责人要求快时再加大进程数
- 分步推进，每一步完成后停下来等用户确认；遇到重要的设计取舍（信息边界、快照字段、动作定义、评测口径），先列出选项和利弊，再动手
- **与编码代理协作**：Claude 负责和产品负责人沟通、定方案、写任务单、审查改动并用真实数据验证；Codex / Kimi / MiMo 按任务单写代码。规则见 `AGENTS.md`；任务单放在 `docs/tasks/`，格式见 `docs/tasks/README.md`

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
