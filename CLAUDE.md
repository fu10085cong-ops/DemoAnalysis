# CS2 职业选手 Demo 行为研究（cs2coach）

## 项目定位
**基于职业选手 Demo 的可迁移行为规律发现系统**（2026-10-19 起，方向与优先级以 `docs/research_roadmap.md` 为准）。

从职业比赛中识别有重复性、可解释、可训练的行为模式，在相似局势中与结果对比并人工核验，最终输出带真实 Demo 证据、适用条件和训练方法的**行为规律卡**（路线图第 5 节）。项目追求的是持续累积可验证的 **情境—行为—结果—训练** 知识，不是职业选手的数据画像。

- **北极星指标**：经人工核验、带 Demo 证据的行为规律卡数量，不是实现了多少个指标。严谨的否定结果（"证据不足"、"不支持"）也是有效产出
- **当前进度、待办和工作约定见 `docs/status.md`（新会话、换机器后先读）**
- 代码盘点（哪些模块保留、冻结、暂停）：`docs/research_inventory.md`；研究数据层的定义：`docs/research.md`
- **只做赛后分析**：不涉及任何局内实时功能，不读取游戏内存，不注入任何客户端

## 研究原则（路线图第 2 节）
每项研究都要能回答四个问题：**发现了什么**（什么局势下倾向什么行为）、**证据是什么**（样本量、行为分布、结果对比、可复核片段和反例）、**为什么值得学**（控制局势差异后行为与有利结果是否稳定关联）、**普通玩家怎么练**（可执行、可衡量、可复盘验证）。

1. **分析单位是"局势中的某个玩家"**，不是整场均值
2. **每个样本都可追溯**：`demo_id`、`match_id`、`round_id`、`player_id`、`start_tick`、`end_tick`，并能生成游戏内跳转命令
3. **区分比赛真实状态与玩家当时可能知道的信息**：玩家知道的信息只用几何视野（`features/visibility.py`）和本队雷达发现（`spotted_by_mask`），不把 Demo 上帝视角当作玩家信息
4. **区分行为标签、后续结果和解释性假设**，不用结果反向定义行为
5. **缺数据记 `null` / `unknown`**，不编造；无法判断的行为标为 `unknown`，辅助标签可以多选
6. **样本不独立**：同一回合的 5 名玩家、同一场比赛、同一支战队都相关，比较和检验按比赛分组；结论要说明是否在多支队伍、多个赛事中都成立
7. **保留重要混杂变量并分层**：地图、阵营、时间、人数、经济、区域、队友支援、赛事级别（顶级 / 二三线）；**只用行为之前的状态分层和匹配**，不能用行为之后的变量
   - 后果分维度记录（局势优势、资源、信息、空间、协同），不打总分；当前假设用不到的维度记 `null`（路线图 3.2）
8. **因果解释必须保守**："职业选手经常这么做"不等于"这么做能提高胜率"；观察性数据不宣称因果
9. **自动标签必须经人工抽样复核**，记录错误类型和混淆矩阵；阈值是待校准的工程假设，经复核后才能用于结论
10. **所有阈值放在 `config/` 下的配置文件里，不要写死在代码中**

## 新增功能的门槛（路线图第 7 节）
新增任何指标、模块或可视化之前，必须回答：
1. 它具体回答哪个游戏行为问题？
2. 它验证哪一条可证伪的假设？
3. 研究得出结果之后，普通玩家能够采取什么行动？

三项答不清楚就先不开发；如果只是工程基础设施，要说明它对哪项已批准的研究是必要的。**不再新增基本功评分项、排行榜、综合评分和复杂前端。**

## 架构与模块
```
职业 Demo
  → 数据基础层      parsing/（解析、逐 tick 状态、事件、回合）
  → 特征层          features/（几何视野、局势上下文、交火、补枪机会；基本功指标只作控制变量）
  → 局势与行为建模层  research/（触发器、行为窗口、行为标签）
  → 行为研究引擎     局势分层与匹配、行为分布、结果对比、反例、置信度（待建）
  → 证据层          demo_id + 回合 + tick 区间 + 人工复核记录（不可省略）
  → 教学与大模型层   基于已验证证据的解释与训练建议（后置）
```
- `src/cs2coach/research/`：`models.py`（`Situation`、`BehaviourWindow`）、`triggers.py`、`windows.py`、`labels.py`；导出脚本 `scripts/research_export.py`，输出到 `data/research/<名称>/`
- 人工复核沿用并改造已有工具：`scripts/review_pack.py`（游戏内复盘包、分层抽样、盲审）、`scripts/rule_regression.py`（规则改动前后逐条对比）
- **基于地图几何的视野**（`features/visibility.py`，awpy 地图网格）是"玩家可能知道的信息"的基础；导航网格（awpy `NavMesh`，`~/.awpy/.../navs/`）用于前压 / 后撤等方向判断，接口见 `docs/fields.md`
- **现在不要实现**：回合胜率模型（ΔWP）、完整的信息集重建、账号与计费服务端

## 冻结的诊断程序
原有的**玩家诊断程序**（单场 / 多场报告页、问题检测、段位阶梯、类别面板、AI 结构化报告）2026-10-19 起**冻结**，小范围使用，**只修 bug，不再扩展**。
- 定位与历史决定：`docs/product.md`；口径：`docs/features.md`；任务记录：`docs/tasks/`（001–056）
- CLI：`python -m cs2coach analyze <demo> --player <steamid64>`、`python -m cs2coach summary --player <steamid64>`；默认与入门（5E B 段，8 名代表玩家）比较
- 枪法指标沿用 Leetify 的名称和口径（`docs/metrics_research.md`），与 Leetify 不同之处写在 `docs/features.md`
- AI 层（`src/cs2coach/ai/`、`src/cs2coach/tools/`）后置：只有当研究产出已验证的结构化证据后，才把它接到规律卡的解释上。**真实 API 调用会产生费用，运行前先征得用户同意**；单元测试用假客户端

## 数据约定
- `data/` 不进仓库（demo、研究表、输出、代表玩家名单都在这里）
- 职业 Mirage demo 放在 `data/demos/pro_mirage/`，`manifest.json` 记录来源（HLTV 比赛编号与链接、赛事、战队）；早期职业样本清单在 `src/cs2coach/baselines/pro_demos.toml`
- 同一场比赛被拆成多段的 demo（如 `-p1`、`-p2`）共用同一个 `match_id`
- 研究表中可以出现职业选手名（公开比赛）；非职业玩家的真实姓名和 SteamID 不进仓库（提交前有隐私扫描钩子）
- **HLTV 有机器人防护，不要自动抓取或绕过**；新 demo 由产品负责人下载或提供

## 技术约定
- Python 3.11+，demoparser2 解析，pydantic 定义数据结构，pytest 测试。**每个触发器、窗口特征、行为标签规则和指标计算都要有单元测试**（用合成比赛，`tests/helpers/synthetic.py`）
- 依赖用 `pyproject.toml` 管理；本地开发用 uv：`uv venv && uv pip install -e ".[dev]"`，**然后必须执行 `python scripts/build_demoparser.py --install`** 覆盖安装修复版 demoparser2（需要 Rust 和 protoc；原因见下方陷阱）
- 跨平台（Mac / Windows / Linux）：路径一律用 `pathlib`，文本文件读写显式指定 `encoding="utf-8"`，不依赖 shell 特性
- 支持的 demo 格式：.dem / .dem.bz2 / .dem.gz / .zip / .rar（.rar 依赖 unrar / 7z / bsdtar 任一）
- 逐场处理的脚本支持 `--jobs`，默认取 `[tools] jobs`（2）；每个进程约占 1.3–1.5 GB 内存，进程数受空闲内存限制

## 工作方式
- **不要凭记忆假设 demoparser2、awpy 的字段名、事件名和接口**，以实际结果为准；不确定就先写脚本验证，结论写进 `docs/fields.md`
- 字段调研结论见 `docs/fields.md`；复现方法：`scripts/explore_demo.py`、`scripts/verify_semantics.py`。解析层（`parsing/match.py`）改动后，或拿到新来源的 demo 时，跑 `scripts/check_parsing.py` 逐项核对
- **占用 CPU 的任务（解析 demo、导出研究表、重建基准、回归、全量页面检查）开始前先告诉产品负责人：跑什么、用几个进程、大约多久**；默认少占 CPU，产品负责人要求快时再加大进程数
- 分步推进，每一步完成后停下来等用户确认；遇到重要的设计取舍（尤其是行为的操作性定义、阈值、分层方式），先列出选项和利弊，再动手
- **与编码代理协作**：Claude 负责和产品负责人沟通、定方案、写任务单、审查改动并用真实数据验证；Codex / Kimi / MiMo 按任务单写代码。规则见 `AGENTS.md`；任务单放在 `docs/tasks/`，研究任务在 `docs/tasks/research/`（编号 `R01` 起），格式见 `docs/tasks/README.md`

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
