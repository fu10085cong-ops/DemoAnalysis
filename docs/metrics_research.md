# 主流平台的个人能力指标调研

> 2026-10-14 调研。目的：了解 Leetify、scope.gg、HLTV、完美、5E 等平台怎么定义和计算击杀时间、压枪、急停、准星位置等个人能力指标，判断我们能做到什么程度、该往哪个方向超过它们。
> 标"未公开"的是平台没有公布算法、只能从描述推测的部分。来源列在文末。

## 1. 结论先行

1. **公开定义最完整的是 Leetify**，其余平台（scope.gg、完美、5E、FACEIT）基本只给名字和用途，不给算法。业内对"枪法"的通用口径实际上就是 Leetify 那一套：Time to Damage、Crosshair Placement（Preaim）、Counter-Strafing、Spray Accuracy、Spotted Accuracy、Headshot Accuracy。
2. **这些指标的关键前提是"什么时候看见了敌人"。** Leetify 早期也用雷达的 spotted 状态，后来发现它有 0–500 ms 的随机延迟（与我们在 `docs/fields.md` §8 的结论一致），2020 年改为"重放所有玩家的位置，判断攻击者能否看到敌人身体或枪的任何部分"——具体方法未公开，但本质上是视线计算。**我们要做出同等或更准的 Time to Damage、Preaim，必须先有视野计算。**
3. **视野计算有现成的开源方案**：awpy 的 `VisibilityChecker` 用 CS2 地图三角网格做射线检测，网格数据按游戏版本发布。它不考虑烟雾等动态遮挡，但烟雾我们有爆开位置和持续时间，可以补上。这原本列在 CLAUDE.md 的"后续阶段、现在不要实现"中，**是否提前需要产品负责人决定**（见第 5 节）。
4. **"压枪"在所有平台上都只是命中率**（Leetify：连续 3 发以上的步枪射击的命中比例），没有一家衡量"压得对不对"。我们有每发子弹的实际方向（`fire_bullets`，已含后坐力）和连射序号，可以逐发计算偏离目标的角度，按第几发画出偏差曲线——这是可以明显超过现有平台的地方。
5. **综合评分（Leetify Rating、HLTV 3.0、完美 WE）都建立在回合胜率模型上**，计算每个行为让胜率变化了多少。这正是我们预留的 ΔWP 阶段，需要大量 demo 训练，不是 MVP 的事。
6. **平台给的都是平均数**，这正是 product.md 说的"笼统"。我们的差异化在于：每个指标都落到具体瞬间（回合 + tick），并且定义完全公开、可复核。

## 2. 各平台概况

| 平台 | 数据来源 | 公开程度 | 主要个人指标 |
|---|---|---|---|
| **Leetify** | 解析 demo（Premier、FACEIT 等） | 有术语表，给出大部分指标的定义和过滤条件；综合评分的权重部分公开 | Aim Rating（枪法综合）、Time to Damage、Crosshair Placement、Counter-Strafing、Spray / Spotted / Headshot Accuracy；道具、补枪类指标；Leetify Rating（胜率影响） |
| **scope.gg** | 解析 demo | 只有指标名称和用途，没有算法 | Time to Kill、First Bullet Accuracy、Spray Accuracy、Accuracy、HS% |
| **HLTV** | 职业比赛 demo | Rating 3.0 的思路公开，公式未公开 | Rating 3.0（经济调整 + Round Swing）、KAST、ADR、首杀、残局 |
| **完美世界** | 平台对局 | 只公开思路 | WE（致胜评分，基于胜率模型）、Rating Pro |
| **5E** | 平台对局 | 只有指标名 | Rating、RWS、首杀率、首杀后胜率等（官方称 50+ 项，没有定义） |
| **FACEIT** | 平台对局（Data API） | 基础统计 | K/D、HS%、ADR、残局、首杀等基础项 |

## 3. 逐项对比

### 3.1 击杀 / 伤害时间

| | Leetify：Time to Damage（TTD） | scope.gg：Time to Kill（TTK） |
|---|---|---|
| 定义 | 从**第一次看见**敌人到**第一次对他造成伤害**的时间 | "从接触敌人到完成击杀"有多快（具体起止点未公开） |
| 过滤 | ≥ 1 秒的样本排除（视为有意等待的情况）；取中位数 | 未公开 |
| 平台自己的提醒 | **不是反应时间**，而是反应、准星位置、命中率、武器射速的综合；400–500 ms 已经"非常快"；步枪两发间隔 90–100 ms，前几枪不中就会多出几百毫秒 | 不说明这次对枪该不该打 |

**我们的情况**：
- "第一次伤害 → 击杀"和"第一枪 → 击杀"现在就能准确算（伤害事件、开枪事件都是精确的）。
- "看见 → 第一次伤害"（真正的 TTD）**需要视野计算**。用雷达 spotted 精度不够（约 0.5 秒更新一次）。
- 建议同时给出拆分：看见 → 第一枪（反应 + 瞄准）、第一枪 → 第一次命中（命中率）、第一次命中 → 击杀（压枪 / 补枪）。平台只给一个总数，拆开后才能告诉玩家具体慢在哪一段。

### 3.2 准星位置（Preaim / Crosshair Placement）

| | Leetify |
|---|---|
| 定义 | 从第一次看见敌人到第一次造成伤害，准星移动的角度；越小越好；取中位数 |
| 参考值 | 第三方指南：个位数度数说明准星通常已经在敌人出现的位置；长期两位数说明在瞄地板和墙 |

**我们的情况**：每个 tick 都有视角（pitch / yaw）和双方坐标，**有了"第一次看见"的时刻就能准确计算**。现在 `aim_error_deg`（每发子弹偏离对手身体中心的角度）是同类信息，但只在开枪时刻有。

### 3.3 急停（Counter-Strafing）

| | Leetify |
|---|---|
| 定义 | 对已发现的敌人开枪、且没有下蹲时，速度低于该武器最高速度 34% 的射击占比 |
| 范围 | **只算步枪** |

**我们的情况**：已实现，口径基本一致（34%、排除下蹲）。差别：
- 我们统计所有枪械（包括手枪、冲锋枪）。为了和外部基准可比，**报告时应单独给出步枪口径**。
- 我们用子弹方向判断"是否对着对手开枪"；Leetify 用"敌人已被发现"。
- 各武器最高速度已用 24 个样本核对（`config/default.toml`）。

### 3.4 压枪（Spray）

| | Leetify：Spray Accuracy | scope.gg：Spray Accuracy |
|---|---|---|
| 定义 | 步枪，连续 3 发以上算一次扫射；扫射中命中敌人的子弹 ÷ 扫射子弹总数（只算敌人已被发现时） | "长连发时的控制能力"（算法未公开） |
| 数值感 | 职业选手中只有 ropz 超过 50%（第三方分析） | — |

**行业空白**：没有平台衡量"压得对不对"，只看命中率。命中率受距离、目标移动、对手是否蹲下影响很大，同一个命中率可能是完全不同的问题。网页上的压枪训练器（csdb.gg 等）能给出每一发与标准补偿路径的偏差，但只针对训练器内的输入，不分析实战 demo。

**我们能做的**（`docs/fields.md` §9 已验证数据）：
- `fire_bullets` 的方向是**每发子弹的实际方向**（已叠加后坐力、不含随机散布），配合连射序号 `recoil_index`。
- 逐发计算子弹方向与目标身体 / 头部的偏差角，按第几发聚合，就能看出"第 4–8 发压不住""横向漂移没跟""压过头"这类具体问题。
- 准确度：第几发、实际方向是精确的；目标点依赖估计的身体高度，所以偏差角是近似值（与 `features/aim.py` 同样的近似）。

### 3.5 命中率类

| 指标 | 定义（Leetify / scope.gg） | 我们 |
|---|---|---|
| Spotted Accuracy | 敌人已被发现时的命中 ÷ 开枪；打烟、打墙不算 | 可做；现在按子弹方向判断目标，有视野计算后可以换成"看得见"口径 |
| Headshot Accuracy | 命中头部 ÷ 命中总数，不含 AWP（不是爆头击杀率） | 可直接做（`player_hurt.hitgroup`） |
| First Bullet Accuracy（scope.gg） | 交火第一枪的命中率；反映准星位置和停稳再开枪 | 已有（`first_shot_hit`） |

### 3.6 道具（Leetify）

| 指标 | 定义 | 我们 |
|---|---|---|
| 闪光带来的击杀 | 每颗闪平均带来的敌方死亡（含自己的击杀）；被闪 < 1.1 秒不算 | 可直接做（致盲表 + 击杀表） |
| 每颗闪致盲的敌人 / 队友 | 致盲 > 1.1 秒的人数 ÷ 投出的闪 | 可直接做 |
| 致盲时长 | 每颗闪中被致盲最久的敌人的时长，取平均 | 可直接做 |
| 每颗雷的伤害 | 雷对敌人的总伤害 ÷ 投出的雷 | 可直接做 |
| 死亡时没用的道具价值 | 死亡时身上烟、雷、闪、火、诱饵的价值，所有死亡取平均 | 已有（列表），价值需要加价格表 |
| CT 烟阻止进攻 | CT 的烟展开点 800 单位内有敌人的比例 | 可直接做（投掷物表有展开位置） |

注意 Leetify 的致盲阈值是 1.1 秒，我们现在 `min_blind_seconds = 1.0`，可以对齐。

### 3.7 补枪（Leetify）

只给出"补枪机会 / 尝试 / 成功""被补的机会 / 尝试 / 成功"的名称，**"机会"和"尝试"的判定方法未公开**。我们现在的漏补用"直线距离 + 雷达发现过击杀者"近似；有视野计算后可以改成"窗口内能看到击杀者"，并且可以判断"尝试"（是否朝击杀者开过枪——用子弹方向判断目标）。

### 3.8 综合评分

| 评分 | 思路 | 公开程度 |
|---|---|---|
| **Leetify Rating** | 每回合按事件前后的胜率变化给分，零和。胜率模型输入：双方经济（分 4 档，CT / T 分开）、存活人数、上一个击杀是哪一方；数据来自 HLTV 比赛。击杀的分配：击杀者 35%、造成过伤害的人 30%、闪光助攻 15%、被补回的那名队友 20%；保枪、回合结束时存活也计分 | 思路和分配比例公开，胜率表未公开 |
| **HLTV Rating 3.0** | 2025-08 起替代 2.1：击杀、伤害、存活等按"这次对枪双方装备下的胜率"做经济调整；加入 Round Swing（行为对回合胜率的影响，分给击杀者、伤害者、闪光助攻、补枪）；去掉 Impact。2025-10 调整后 Round Swing 权重约 33% | 思路公开，公式未公开 |
| **完美 WE** | 建立比赛胜率走势模型，计算每名玩家每次行为对胜率的影响；越关键的时刻权重越大，道具、补枪、拉枪线、保枪都计入 | 只有思路 |
| **5E Rating / RWS** | Rating 社区普遍认为参照 HLTV；RWS：每回合胜方按有效伤害比例分 100 分，负方 0 分 | 只有思路 |

学术参考：Xenopoulos 等人的 "Valuing Player Actions in Counter-Strike"（Win Probability Added，用 XGBoost / CatBoost 预测回合胜率，训练数据 5500 万个比赛状态），以及基于 awpy 的 ESTA 数据集（1558 场职业比赛）。

**对我们的意义**：所有"这次击杀 / 死亡有多重要"的判断最终都要回到胜率模型，这与 CLAUDE.md 预留的 ΔWP 阶段一致。在那之前，我们用存活人数、经济、下包、残局等标签给 AI 提供局势，由 AI 判断轻重。

## 4. 平台指标的常见问题（第三方分析与平台自己的提醒）

- **TTD 不是反应时间**，却常被当成反应时间看；论坛上流传的 100–200 ms 不是合理参照。
- **不分武器**：AWP 和步枪的数据混在一起，很难比较（第三方对 Leetify 职业数据的批评）。
- **不分攻守、不分线上线下**（延迟没考虑）。
- **样本量**：30 场的样本对稳定的枪法指标可能不够。
- **综合评分权重不公开**：Aim Rating 的权重未公布，只知道是对各项做标准化后平均。
- **雷达 spotted 不可靠**：Leetify 自己的例子中延迟 421 ms；身体只露出脚或手臂时不触发；敌人离开视线后仍可能显示。
- **平均数说明不了"为什么"**：scope.gg 自己也承认，TTK、命中率不能说明这次对枪该不该打、站位对不对。

## 5. 对我们的建议

| 优先级 | 建议 | 依据 |
|---|---|---|
| 高 | **核心枪法指标沿用 Leetify 的通用口径**（TTD、Preaim、急停、扫射命中率、爆头命中率），并给出步枪口径，方便用户和外部数据对照；同时公开我们的完整定义 | 用户已经熟悉这套名词；公开定义是"科学、准确"的直接体现 |
| 高 | **提前做视野计算**（awpy 地图网格 + 我们的烟雾数据）。它决定 TTD、Preaim、"看见了却没开枪""没看见就被打死"、补枪机会这一整类指标能不能做准 | 平台的经验（Leetify 2020 年放弃雷达）和我们的验证（fields.md §8）一致 |
| 高 | **TTD 拆成三段**：看见 → 第一枪、第一枪 → 第一次命中、第一次命中 → 击杀 | 平台只给总数，拆开才能指出具体问题 |
| 中 | **压枪偏差曲线**：按连射第几发统计子弹偏离目标的角度 | 行业空白，数据已具备 |
| 中 | 补齐 Leetify 的道具指标（闪光致盲 / 带来击杀、雷的伤害、道具价值、CT 烟） | 数据都已在解析层，工作量小 |
| 中 | 致盲阈值对齐 1.1 秒 | 与外部口径一致 |
| 低（后续阶段） | 回合胜率模型（ΔWP），用于判断瞬间的重要性和综合评分 | 所有主流综合评分的基础；需要大量 demo |
| 低 | 职业基准：用同一套代码跑 HLTV demo，按武器、攻守、地图分别统计分位数 | Leetify 按段位分桶、取百分位；我们的定位要求同类比较 |

**产品负责人的决定（2026-10-14）**：视野计算提前到 MVP；枪法指标沿用 Leetify 的名称和口径。已实现，定义、核对结果和与 Leetify 的差别见 `docs/features.md`。

## 来源

- Leetify：[Stats Glossary](https://leetify.com/blog/leetify-stats-glossary/)、[Updated Benchmarks for CS2](https://leetify.com/blog/cs2-benchmarks/)、[Enemy (actually) spotted](https://leetify.com/blog/enemy-actually-spotted/)、[Leetify Rating explained](https://leetify.com/blog/leetify-rating-explained)、[Rating upgrade](https://leetify.com/blog/rating-upgrade/)、[Performance Metric Tool Guidelines](https://leetify.com/blog/performance-metric-tool-guidelines/)
- scope.gg：[CS2 aim guide](https://scope.gg/guides/cs2-aim-guide-how-to-improve-aim-and-track-progress/)、[Headshot stats](https://scope.gg/headshot-stats/)、[esports.gg 对 scope.gg 的评测](https://esports.gg/news/counter-strike-2/scope-gg-review)
- HLTV Rating 3.0：[pley.gg](https://pley.gg/cs2/hltv-introduces-rating-3-0-heres-the-changes/)、[pley.gg：公式调整](https://pley.gg/cs2/hltv-changes-rating-3-0-formula/)、[dust2.us](https://dust2.us/news/67183/hltv-releases-update-for-rating-30)、[Liquipedia](https://liquipedia.net/counterstrike/HLTV)
- 完美 WE：[新浪电竞](https://dj.sina.com.cn/article/kmxzfmk7169752.shtml)、[SGamer：S14 致胜评分](https://www.sgamer.com/news/202312/43012.html)、[虎扑讨论](https://bbs.hupu.com/630907745-2.html)
- 5E：[5EPlay 数据页](https://csgo.5eplay.com/data/team/ranks)、[虎扑：5E rating](https://bbs.hupu.com/36471969.html)、[ZOL：Rating / RWS / KAST](https://wap.zol.com.cn/ask/x_34761152.html)
- 第三方分析：[nomiun：which cs pros have the best aim](https://nomiun.substack.com/p/which-cs-pros-have-the-best-aim)、[CSRun：preaim 与反应时间](https://csrun.win/guides/crosshair-placement-and-preaim)、[Profilerr](https://profilerr.net/how-to-analyze-cs2-stats-for-winning/)、[csdb.gg 压枪训练器](https://csdb.gg/spray-trainer/)
- 视野计算：[awpy Visibility 文档](https://awpy.readthedocs.io/en/stable/visibility.html)、[awpy 示例](https://awpy.readthedocs.io/en/latest/examples/visibility.html)
- 学术：[Valuing Player Actions in CS:GO](https://arxiv.org/pdf/2011.01324)、[ESTA dataset](https://arxiv.org/pdf/2209.09861)
