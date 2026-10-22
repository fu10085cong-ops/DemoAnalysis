# 代码盘点（研究路线图 2026-10-19 初版第 3.1 节）

> 2026-10-19 按当时的研究路线图（已归档 `docs/history/2026-10-research-roadmap.md`）盘点；2026-10-22 起方向见 `docs/tactical-roadmap.md`。产品负责人决定：现有诊断程序做少量完善、保证基本参考价值后**冻结**，小范围使用；其余工作**全面转向路线图**。
> 本表是第一周"盘点现有指标"的交付物。"可作 L2/L3 上下文"指能否直接作为局势建模、行为窗口、结果比较的输入。

## 一、数据基础层（保留，保持稳定）

| 模块 | 实现 | 输入 | 输出粒度 | 测试 | 可作 L2/L3 上下文 | 结论 |
|---|---|---|---|---|---|---|
| Demo 读取与解压 | `parsing/loader.py`、`summary/discover.py` | .dem / .zip / .rar / .gz / .bz2 | 文件 | 有 | 是（Demo 定位、`demo_fingerprint` 识别同一场比赛） | 保留 |
| 逐 tick 玩家状态 | `parsing/match.py`（`MatchData.ticks`、`state_at`、`player_ticks`） | demoparser2 修复版 | 每 tick × 每人：坐标、视角、速度（自算）、血量、护甲、武器、弹药、下蹲、空中、致盲、`last_place_name`、`spotted_by_mask` | 有（17 个测试文件） | **是，行为窗口的直接数据源** | 保留 |
| 回合与胜负 | `parsing/rounds.py`、`Round` | 回合事件（部分平台为合成） | 回合：冻结期结束、胜负判定 tick、胜方、原因 | 有 | 是（时间、回合结果） | 保留 |
| 击杀、伤害、开枪、致盲、投掷物、炸弹、经济 | `parsing/match.py` | 事件 | 事件 | 有 | 是（接战、道具时机、经济） | 保留 |
| 平台差异与数据核对 | `scripts/check_parsing.py`、`docs/fields.md` | 各平台 demo | — | 脚本 | 是（只依赖所有平台都有的数据） | 保留 |

## 二、可直接服务研究的特征（保留，作为 L2 的基础）

| 模块 | 实现 | 输入 | 输出粒度 | 测试 | 可作 L2/L3 上下文 | 结论 |
|---|---|---|---|---|---|---|
| **地图几何视野** | `features/visibility.py`（`Sight.sees`、`first_seen`、`last_seen`） | 逐 tick 坐标与视角、awpy 地图网格、烟雾 | 任意 tick 的"A 能否看见 B"（视线 + 视野 + 烟雾 + 闪白） | 有 | **是**。路线图第 2 节第 3 条"区分比赛真实状态与玩家当时可能知道的信息"的核心工具；与雷达 `spotted_by_mask`（队友共享信息）一起可构成 `known_enemy_information` | 保留 |
| 局势上下文 | `features/moments.py`（`Context`） | 击杀 / 死亡前一 tick | 瞬间：阵营、双方存活人数、回合时间、下包、装备价值、手持武器、血量、距离、地点名 | 有 | 是，但只在击杀 / 死亡时刻计算；缺队友位置、剩余时间、区域语义 | 保留，扩展为通用 `Situation` |
| 交火窗口 | `features/moments.py`（`Engagement`） | 击杀 / 死亡前的开枪与伤害 | 交火：谁先看见、谁先开枪、接触类型（拉出 / 架点）、各段用时 | 有 | 是（"发生接战"的辅助标签、短期结果） | 保留 |
| 补枪机会 | `features/moments.py::find_trade_chances`、`features/trades.py` | 队友阵亡、距离、视线、换弹、其他交火 | 每次机会 | 有 | 是（"与队友配合"类课题的现成样本） | 保留 |
| 存活人数推演 | `features/panel.py::_clutch` | 冻结期名单 + 击杀顺序 | 回合内人数变化 | 有 | **是**。改造后即可做"CT 首次 5v5 → 5v4"触发器 | 保留，抽成通用函数 |
| 交火数据集 | `dataset.py`、`scripts/dataset_export.py`（S07） | 职业 + 业余 demo | 每行一次交火 / 漏补 / 玩家 | 未找到直接测试 | 是（研究表的雏形；分析单位要改为局势窗口） | 保留 |

## 三、人工复核与证据（保留，契合度高）

| 模块 | 实现 | 用途 | 结论 |
|---|---|---|---|
| 规则审计 | `audit.py`、`scripts/audit_collect.py`、`audit_report.py`（S01） | 抽样判定"对不对 / 值不值得"，可疑组合扫描 | 保留，改造为行为标签复核 |
| 标准答案集 | `scripts/review_pack.py`（049：分层抽样、盲审、回填） | 盲审、混淆统计 | **保留，直接用于路线图第 3.1 节"人工复核标签"** |
| 游戏内复盘包 | `scripts/review_pack.py`（028） | `exec` 后按编号跳转到 tick | 保留（`EvidenceClip` 的回放手段） |
| 规则回归 | `scripts/rule_regression.py`（S07） | 规则改动前后命中逐条对比 | 保留（标签规则改动的回归工具） |
| 隐私扫描 | `scripts/privacy_scan.py`、git 钩子（S06） | 防止真实玩家名进仓库 | 保留 |

## 四、基本功指标（保留但冻结扩展：作为研究的控制变量）

| 模块 | 实现 | 输出粒度 | 测试 | 结论 |
|---|---|---|---|---|
| 枪法指标（命中速度、预瞄、急停、压枪、爆头、命中率） | `features/aim_stats.py`、`aim.py`、`crosshair.py` | 交火 / 整场 | 有 | 冻结，可作控制变量 |
| 问题检测 | `features/problems.py`（30 条规则，`evaluated` 可判断次数） | 瞬间 / 回合 | 有（34 个测试文件） | 冻结。其中**局势类规则**（`overpeek_in_advantage` 人数优势时冒进、`peeked_into_held_angle`、`dry_peek_with_utility`、`missed_trade`、`died_without_seeing`）只是"认定这样是问题"，没有与结果比较过，在研究线中应**视为待检验的假设**；`overpeek_in_advantage` 与第一个课题（5v4）直接相关 |
| 职业武器阈值 | `baseline.py`、`baselines/pro_sample.json` | 武器 × 指标分位 | 有 | 冻结 |

## 五、评分与报告（暂停：路线图"总评分、排行榜、精美报告"）

| 模块 | 实现 | 结论 |
|---|---|---|
| 分段雷达、段位阶梯、类别面板 | `features/radar.py`、`ladder.py`、`panel.py`；`config/tiers.toml`；`baselines/tiers/`（052–055） | 暂停。保持现状供小范围使用，不再扩展 |
| 报告页、体检卡、基础报告 | `report/`（`page_data.py`、`page_template.html`、`card.py`、`basic.py`） | 暂停；只修 bug |
| 多场汇总 | `summary/` | 暂停；只修 bug |
| 5E 代表玩家分段 | `data/tiers/`、`data/demos/tiers/` | 暂停收集 A、S 段 |

## 六、AI 层（后置）

| 模块 | 实现 | 结论 |
|---|---|---|
| 结构化诊断、检查器、占位符 | `ai/`、`tools/checker.py`、`tools/slots.py`、`tools/digest.py` | 后置。设计原则（AI 只解释结构化证据、每条结论引用 tick、数字由程序填写）与路线图第 6 节第 5 条一致，冻结保留，待有已验证的规律卡后再接 |

## 七、第一个研究闭环（Mirage CT 5v4）的缺口

| 需要的能力 | 现状 | 难度 |
|---|---|---|
| 触发器：CT 首次 5v5 → 5v4 | 无；`panel._clutch` 的人数推演可改造 | 小 |
| 5 秒行为窗口（位置、视角、道具、开枪、接战） | 数据都有，缺抽取模块和 `BehaviourWindow` / `EvidenceClip` 模型 | 中 |
| Mirage 区域与前压 / 后撤方向 | 有 `last_place_name`；"纵深 / 接触区"需人工定义并校准 | **大**（需要人工判断） |
| 行为标签（含 `unknown`、多标签、置信度） | 无 | 中 |
| 玩家可能掌握的敌方信息 | 几何视野 + 雷达 `spotted` 已有 | 小 |
| 短期结果（5 秒内死亡、是否失去人数优势）、回合结果 | 数据都有 | 小 |
| 人工复核记录（混淆矩阵、错误类型） | `review_pack.py` 可改造 | 中 |
| 行为规律卡模板 | 无 | 小 |
| **Mirage 职业 demo 20–30 场** | **只有 6 场**（职业样本共 26 场：Mirage 6、Dust2 6、Nuke 4、Ancient 3、Anubis 3、Inferno 2、Cache 2） | 需要补数据 |
