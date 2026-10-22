# DemoAnalysis：基于 MiMo 的 CS2 战术决策 AI 技术路线

> 版本：v0.2｜日期：2026-10-22｜定位：**2026-10-22 起项目方向以本文为准**（产品负责人采纳）。方案中的参数、工期和预期都是研究起点，不是已验证的结论。
> 之前的方向（职业 Demo 行为规律研究）及其规则归档在 `docs/history/`；其成果如何衔接见第 13 节。进度与待办见 `docs/status.md`。

## 摘要

本方案面向 DemoAnalysis 的下一阶段建设：在保留现有 Demo 解析、操作指标和赛后复盘能力的基础上，增加一个以 MiMo 等通用大模型为推理核心的 CS2 战术决策研究子系统。目标不是让 AI 自动瞄准或操作，而是在严格限定为玩家当时合法可获得的信息下，提出可执行战术建议、解释依据，并通过独立评测验证质量。核心原则：先解决信息边界，再做大模型决策，最后研究最优性。

## 1. 项目定位与边界

现有方向：Demo 解析 → 操作指标 → 职业选手基准 → AI 赛后复盘。新增方向：Demo 真实状态 → 合法可观察信息重建 → 战术局势快照 → MiMo 决策 → 结构化评测。两条能力线共享底层解析数据，但应采用独立模块，避免机械指标和战术结论混杂。研究阶段优先离线处理 Demo；任何未来实时使用必须遵守游戏和赛事规则。

## 2. 核心研究问题

（1）通用大模型在只使用合法已知信息时，能否提出接近高水平选手的战术判断？（2）结构化状态、事件记忆、地图工具各自能带来多大增益？（3）加入敌方位置/意图信念模型后，决策是否真正改善？（4）如何防止模型“分析很合理”但实际上事实错误、建议不可行？（5）在无法直接观察反事实的前提下，怎样稳健地比较战术优劣？

## 3. 数据契约：三层隔离

Ground Truth：Demo 中所有玩家的位置、真实结果、道具等完整信息，仅供标签和评测。Observation：目标玩家到决策时刻已通过可用视觉、声音、雷达或可靠队友报点获得的事实；不可将 Demo 中记录的事件直接等同于目标选手听到或看到的事件。Belief：根据 Observation 推断的未知敌人位置和意图，必须连同置信度、证据与不确定性输出。强制满足所有观测时间不晚于决策时刻；此外还必须验证信息实际可达。对无法准确重建的观测，明确标记 unknown，不要默认为可见。

## 4. 核心数据结构 TacticalSnapshot

建议字段：match_id、round_id、decision_tick、player_id、map_name、side、time_remaining、self_state、known_teammates、observations（tick、source、kind、area、confidence）、bomb_status、unknown_information、schema_version。所有模型只能通过受控查询访问 TacticalSnapshot，不直接接触全图 Demo 对象。初版最好使用 Pydantic 对字段及来源做校验，保留原始观测引用以便审计。

## 5. 模块化架构

A. parsing：保留当前 Demo 解压和解析能力。B. tactical/observations.py：合法信息重建与可见性/可听性边界。C. tactical/snapshot.py：决策时点的结构化快照。D. tactical/map_graph.py：地图区域连接、可达性与路径时间。E. tactical/decision_points.py：抽取残局、首杀、爆弹、阵亡、发现 C4 等决策节点。F. llm/client.py：MiMo API 适配，兼容更换模型。G. llm/tools.py：仅允许查询经过权限过滤的状态。H. llm/validator.py：验证 JSON、目标区域和动作合法性。I. evaluation：数据集划分、指标、对照实验。

## 6. MiMo 推理策略

首版使用强通用模型 API + 固定提示词 + JSON 输出，不做微调。请求包含局势、历史事件摘要、地图查询结果与允许行动集合；模型需区分事实、推测及未知信息，比较至少三个可执行动作，输出 recommended_action、target_area、evidence、alternatives、risks、missing_information。不要把模型随口给出的概率当作已校准胜率。长时记忆放在外部状态存储，不通过无限增长的对话历史维持。建议按关键事件触发调用，避免逐 tick 调用大模型。

## 7. 战术动作与信息工具

初始行动集合：HOLD、FALLBACK、ROTATE_A、ROTATE_B、PUSH、REPOSITION、GATHER_INFO、SUPPORT、DELAY、RETAKE、SAVE、COMMIT。动作应携带目标区域和可行性约束。推荐只开放五个受控工具：get_tactical_snapshot、get_observation_history、get_map_routes、get_team_status、validate_tactical_action。若目标玩家不知道 B 二楼有没有人，工具必须返回 unknown，而不是 Demo 的真实坐标。

## 8. 数据与评测路线

先抽取 10–30 个典型 Mirage 决策场景进行人工检查，扩大到约 100 个来自未见过比赛的残局。按比赛/时间/战队划分训练与测试，避免相邻 tick 泄漏。测试四方面：事实一致性与信息泄漏率；战术动作合法率与时间路径约束；与职业选择的 Top-k 一致性（仅参考，不代表最优）；教练或高水平选手盲评。后续加入位置预测的 Log Loss、胜率 Brier Score 与校准指标。不同动作的历史胜率只是相关性，不能直接当反事实因果结论。

## 9. 对照实验

A：基础 MiMo + 简单文本问题。B：MiMo + 标准 TacticalSnapshot + 专业系统提示词。C：B + 地图/路径工具 + 外部历史记忆。D：C + 显式敌方位置/意图信念分布（第二阶段）。在相同冻结测试集、同等信息边界下比较合法率、虚构率、盲评得分、响应延迟和成本。只有 D 在可靠评测上取得增益，才值得投入更复杂的信念模型。

## 10. 阶段实施计划

阶段 0：明确观察信息标准和不支持场景，冻结数据契约。阶段 1：单张 Mirage Demo 的合法信息过滤与单回合时序快照。阶段 2：抽取决策节点，生成可人工检查的场景集。阶段 3：接入 MiMo 并规范输出与工具调用。阶段 4：建立独立评测及错误分类，完成 A/B/C 对照。阶段 5：如确有收益，再加入信念模型、职业案例检索及 SFT/LoRA；最后才研究模拟反事实和离线强化学习。原型可按 6–8 周规划，但职业级决策和高保真模拟器不是这一阶段交付目标。

## 11. 风险、缓解与验收标准

风险：Demo 信息泄漏；脚步、雷达和队友报点重建误差；模型幻觉；行动标签含糊；历史结果的混杂偏差；调用延迟和成本；跨队伍泛化不足。缓解：字段来源追踪、unknown 优先、黄金测试场景、工具白名单、结构化输出校验、盲评、按比赛隔离测试。MVP 验收：能从 Demo 重建一名选手在指定 tick 的受控快照；能人工验证观测来源；MiMo 能输出可追溯的合法候选战术；评测结果可复现。不能以“回答像教练”作为验收标准。

## 12. 推荐的下一次工程提交

第一提交：新增 docs/tactical-roadmap.md，写明目标、非目标、信息边界、数据契约、测试口径（2026-10-22 已完成：即本文）。第二提交：新增 tactical/snapshot.py 与 observations.py，附最小单元测试，包括未来事件泄漏、未看见敌人、未知脚步来源等情况。第三提交：编写单 Demo → TacticalSnapshot 导出命令。第四提交：接入可替换的大模型客户端与严格 JSON 校验。第五提交：增加 10–30 个冻结战术场景和基线测试。这条路线成本较低、反馈清晰、允许随时暂停或调整。

## 参考与说明

项目地址：https://github.com/fu10085cong-ops/DemoAnalysis 。本文件整理此前对该仓库的公开文档、产品方向和 MiMo 方案的讨论，不声称本轮重新完成完整源码审计或已实际运行仓库测试。相关工具可参考 demoparser2：https://github.com/LaihoE/demoparser ；MiMo API、模型名称及支持能力以官方最新文档为准。所有示例参数与阶段工期均为研究起点，不是已证实的性能结论。

## 13. 与现有成果的衔接（2026-10-22 对照代码整理）

### 13.1 已有的零件

| 本文的模块 | 现有对应 | 差距 |
|---|---|---|
| A. parsing | `src/cs2coach/parsing/`、修复版 demoparser2、`scripts/check_parsing.py` | 无 |
| B. observations | `src/cs2coach/research/threat.py`：雷达（`spotted_by_mask`）、听得到的声音与枪声、接火点；`features/visibility.py` 几何视线；`threat.py::pre_danger` 只用决策时刻之前的信息 | 声音、枪声按"任一活着的 CT 听到即全队知道"处理，相当于假设队友报点完美；没有逐条来源与置信度；没有统一的 unknown |
| Belief（第二阶段） | `src/cs2coach/research/potential.py`：同方向、同回合阶段的历史 T 分布，扣掉本队 5 秒内看过的格子 | 没有逐条证据与置信度输出 |
| C. snapshot | `scripts/research_text_ai.py::timeline()`：每 0.5 秒一行（位置、移动、准星、血量、武器、已知敌人、烟及扔烟者、队友、剩几个 T），到截止时刻为止 | 是拼好的文字，不是带校验的数据对象 |
| D. map_graph | awpy 导航网格、`windows.py::_nav_distance`、`potential.py` 地面格子与邻接、`config/research.toml` 报点分层 | 没有"路径时间"与区域连接的查询接口 |
| E. decision_points | `research/triggers.py`（CT 5v4）、首杀窗口（`research/stance.py`） | 残局、爆弹、阵亡、发现 C4 等都没有 |
| F. llm/client | `scripts/research_vision.py::_clients()`、`research_text_ai.py::_clients()`：MiMo 多账号轮流、DeepSeek | 在脚本里，不是独立模块 |
| H. validator | 各脚本的 `parse()`：只检查标签在不在选项里 | 很弱 |
| I. evaluation | 盲审、留出集、两人一致率、按比赛分两半确认、程序与 AI 交叉检验（R03） | 只针对"行为标签"，没有针对"战术建议" |

### 13.2 已知的信息边界问题

- 动画回放页（`scripts/research_replay.html`）按真实位置画出全部 10 名选手。MiMo 看图版截图和此前的人工盲审都看得到玩家当时看不到的 T。全量 AI 标签用的是文字版（只含玩家可知信息），规律卡 01 的结论不受影响
- **复核方式改为**：产品负责人用与 demo 版本匹配的历史版本 CS 在游戏内看 demo（复盘包里有 `demo_gototick` 跳转命令）；动画回放页只作参考

### 13.3 规律卡 = 职业案例检索的证据

R03 的成果（`docs/tasks/research/R03-ai-post-fight-reposition.md`、`docs/cards/01-first-kill-aggression.md`）在本方向里的用途：阶段 5 的"职业案例检索"。

- 检索的钥匙：决策时刻的 TacticalSnapshot（局势字段）
- 检索到的线索：规律卡（职业选手在这种局势下倾向怎么做、代价是什么），附效应大小、区间和可回放片段
- 不能越界：卡里写明"能说 / 不能说"，validator 据此拦截编造（例如 01 号卡的"回合胜率无差别"不能被说成"应该激进"）
- 待做：给规律卡加机器可读的元数据（适用局势用 TacticalSnapshot 的字段名、行为定义、效应与区间、状态、能说 / 不能说、片段 ID）

### 13.4 暂停的工作

- R03 规律卡 01：结论已完成，典型片段 10/12 已确认，替换的 2 段（`card-1b`）待确认后即可收尾
- 研究分支 R06（四类行为 +"能不能跑"）：暂停；其中"能不能跑"的信号（退路是否被看着、敌我人数、离掩体的距离）可以作为 TacticalSnapshot 的派生字段再利用
- 冻结的玩家诊断程序：保持冻结，只修 bug
