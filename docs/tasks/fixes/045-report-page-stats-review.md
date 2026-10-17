# 045 报告页统计口径修正（外部审查 14 条中的 12 条）+ 一致性检查

状态：完成
难度：A（改数据结构、统计判定和页面模板，跨 radar、page_data、lobby、分段基准）
执行：Codex（思考强度 high）
分支：`task/045-report-page-stats`

## 目标

2026-10-18 另一个 AI 审查了 043/044，Claude 逐条核实属实。共同的根源是：**页面上的说法超出了数据能支持的范围**，或者同一页的不同区块用了不同的口径。这次一并修掉，并加一个自动一致性检查，以后同类问题由程序在所有玩家上先找出来。

产品负责人已定（2026-10-18）：
- 多场统一用**典型单场**：逐场计算，再取各场中位数。
- 结论保留"最该练的是……"的说法。

## 背景（必须读）

- `src/cs2coach/report/page_data.py`（全文，约 300 行）
- `src/cs2coach/features/radar.py`
- `src/cs2coach/features/lobby.py::lobby_line`：`trade_kills` 的算法在约 85 行
- `src/cs2coach/features/problems.py::_poisson_tail`（约 461 行）以及它在频率表中的用法（约 508 行）：页面判定改用同一个函数
- `src/cs2coach/features/problems.py` 约 416–441 行：漏补命中的 `evidence` 是 `MissedTrade.model_dump()`，里面有 `tick`、`teammate`、`killer`
- `src/cs2coach/report/page_template.html`：切换分段的逻辑
- `docs/features.md` 的"分段雷达与报告页数据（043）"一节：改完要同步

## 要做的事

### 1. 指标名称和说明按实际口径重写（审查第 9 条）

当前说明是错的。比如准星写的是"看见对手时准星偏离他的角度"，实际算的是**从看见对手到第一次打中，准星转过的角度**（`aim_stats.py` 约 102 行）。

`METRIC_TEXT` 和雷达维度名统一改为下表：

| 指标 | 名称 | 说明（measure） |
|---|---|---|
| `time_to_damage_ms` | 首次命中用时 | 从看见对手到第一次打中他的时间（典型单场的中位数，1 秒以上的不算） |
| `crosshair_placement_deg` | 看见到命中的转角 | 从看见对手到第一次打中，准星转过的角度（越小说明预瞄越到位） |
| `counter_strafing_pct` | 急停 | 用步枪、看见敌人时开枪，已经停稳的比例 |
| `spray_accuracy_pct` | 压枪 | 用步枪连发 3 发以上时，子弹打中敌人的比例 |
| `trade_kill_events_per_round` | 补枪 | 每 10 回合补枪击杀的次数（一次击杀算一次） |
| `traded_death_pct` | 被队友补回 | 自己阵亡后被队友补枪的比例 |
| `utility_damage_per_round` | 道具伤害 | 每 10 回合手雷和火造成的伤害 |
| `enemies_flashed_per_flash` | 闪光效果 | 每颗闪光平均闪到的敌人数 |

`[[radar.axes]]` 的 `name` 改为：命中速度、预瞄、急停、压枪、补枪配合、道具使用。

### 2. 补枪按"击杀次数"计（第 11 条）

- `LobbyLine` 新增 `trade_kill_events: int = 0`，等于带 `trade_kill` 标签的击杀数，一次击杀算一次。
- 原有的 `trade_kills` 不改，Markdown 报告还在用它。
- 雷达改用新指标 `trade_kill_events_per_round`，删掉 `trade_kills_per_round`。分段基准要重新构建。

### 3. 去掉"阵亡时未用道具价值"（第 12 条）

从不买道具的人这一项也是 0，没法据此表扬"道具用得好"。从 `RADAR_METRICS`、道具维度和优点候选里都去掉。道具维度只留 `utility_damage_per_round`、`enemies_flashed_per_flash`。

### 4. 最低样本数（第 5 条）

新增配置项：

```toml
[radar.min_samples]
rounds = 10         # 按回合算的指标（补枪、道具伤害）至少要在场这么多回合
deaths = 5          # 被补回比例至少要死这么多次
flashes = 5         # 闪光效果至少要扔这么多颗
tier_values = 30    # 分段里某指标的有效人次少于这个数时，该指标不参与排名（百分位为 None）
```

- `LobbyLine` 新增 `flashes_thrown: int = 0`。样本不够的指标值为 None。
- 枪法四项沿用 `AimStats` 自己的样本门槛，不改。
- 页面每个优点行带上 `samples`：这一项的有效样本数（多场取各场合计）。

### 5. 多场统一用典型单场（第 2 条）

- 雷达维度：逐场计算维度值，再取各场中位数（现在就是这样）。
- 优点：先算每个指标逐场的**百分位**，再取各场中位数，用这个中位数筛选和排序。不再用"原始值的中位数再求百分位"。
- 优点行显示的数值 `you` 是各场原始值的中位数；measure 末尾加上"（典型单场）"，单场报告不加。
- 优点的 `text` 改为 `"在{分段名}里排在第 {p} 百分位。"`，不再写"优于多少人次"（第 8 条）。

### 6. 严重度和结论（第 4、6、13 条）

- **期望次数**用分段的完整精度：`expected = rounds × hits / tier_rounds`。不用舍入后的 `per_10_rounds`。分段命中数为 0 时，`hits` 按 0.5 计，不把它当成"概率为零"。
- **严重度**：
  - **bad**：`_poisson_tail(count, expected) < [problems].frequency_max_chance`，并且多出的次数 ≥ `problem_min_excess`；
  - **warn**：多出的次数 > 0；
  - 否则 **ok**。
  - 删掉 `problem_bad_ratio` 这个配置项。
- **结论**：
  - 有 bad 时，写 `"最该练的是{多出次数最多的 bad 问题}：{范围}出现 {count} 次，按{分段名}的频率约 {round(expected)} 次。"`；
  - 没有 bad 时，写 `"{这场/这段时间}没有检出明显多于{分段名}的问题。"`；
  - 雷达上可用维度 ≥ 4 时，在后面接 `"雷达上最弱的是{名称}（第 {p} 百分位）。"`；
  - 可用维度 < 4 时，改接 `"可用的指标不够多，暂不判断整体强弱。"`；
  - 不要写"最强的是"。
- 页面的 `problem_bad_ratio` 字段删除，改为下一节的按分段预先算好。

### 7. 每个分段完整计算（第 1 条）

切换分段时，选题、颜色、优点、结论都要跟着变。改成：

- `TierView` 新增 `verdict`、`strengths`、`problems`。每个分段各自用自己的期望次数选题、排序、判定颜色和写结论。
- `ReportPage` 顶层的 `verdict`、`strengths`、`problems` 删除。没有任何分段时，`tiers` 为空，页面显示 044 已有的那条提示。
- `page_template.html`：切换分段时，用该分段的 `verdict`、`strengths`、`problems` 重画对应区块。删掉前端的 `problemSeverity` 重算，颜色全部来自数据。
- 删除 `TierView.problem_targets`。每个问题行自带 `target`（分段每 10 回合次数，只用于显示）和 `expected`。

### 8. 例子必须对应被评价的指标（第 3、10 条）

- **漏补**：用命中 `evidence` 里的 `tick`、`teammate`、`killer`，精确对应到那一条 `MissedTrade`。被排除、没有命中的记录不能出现。
- **优点例子**：

  | 指标 | 例子 |
  |---|---|
  | 首次命中用时、转角 | 不变 |
  | 急停 | 不变 |
  | 压枪 | 连发 ≥ 3 发、这次连发命中率不低于本人整场 `spray_accuracy_pct`，并且击杀了对手 |
  | 补枪 | 带 `trade_kill` 的击杀 |
  | 被队友补回 | 本人带 `traded` 标签的死亡 |
  | 道具伤害 | 没有对应的瞬间，`examples` 留空 |
  | 闪光效果 | 带 `flash_assist` 的瞬间 |

- 没有对应事件时 `examples` 留空，不借用其他指标的事件。

### 9. 雷达参照线改名（第 8 条）

`tier_median` 的显示名改为 `"参考中线（50）"`；页面说明改为"50 = 分段里排在正中间的水平"。

### 10. 报告页一致性检查（新增）

新增 `src/cs2coach/report/page_check.py::check_page(page: ReportPage, cfg) -> list[str]`，返回违规说明，空列表表示通过。每个分段分别检查：

1. 同一个维度不能既在优点里，又出现在 bad 或 warn 的问题里；
2. 结论里点名的问题，必须是 bad 问题中多出次数最多的那个；没有 bad 时，结论不能出现"最该练"；
3. 每个优点行和问题行的 `measure`、`unit` 都不能为空；
4. 优点、问题、例子、高光的数量不超过配置的上限；
5. 每个优点的百分位，必须 ≥ `strength_min_percentile`；
6. 每个优点行的 `samples` 不低于对应的最低样本数；
7. 每个漏补例子的 tick，都必须能在命中的 evidence 里找到。

接入方式：
- `cli.py` 生成 `report_data.json` 后调用它，有违规时在终端打印 `报告页一致性检查：N 处` 和每一条，不中断输出。
- 新增 `scripts/page_check_all.py`：对 `config/tiers.toml` 中 `amateur_tmp` 的全部 demo、每名覆盖率合格的玩家，都生成单场页面数据并检查，最后打印每类违规的数量和前 5 个例子（玩家用"玩家01"这样的编号，不打印名字）。这个脚本不调用 AI。

## 验收标准

- [x] `tests/test_page_data.py`、`tests/test_page_check.py`、`tests/test_radar.py` 分别覆盖：
  - 审查报告的复现例子都改正了：
    - 两个分段 5 次和 1 次、本人 4 次：切换到 1 次的分段后会出现这个问题；
    - 两场 TTD 100 和 300 毫秒、基准 [201, 250, 299]：优点和雷达的口径一致；
    - 同一回合两条漏补记录、只有一条命中：只出现命中那一条；
    - 只有一个可用维度时，结论不说强弱；
    - 死一次、被补回一次：不列为优点；
    - 期望 6 次、实际 9 次：不是 bad，因为泊松尾概率约 15%；
    - 分段 17/4930、分析 1000 回合：期望 3.448，不是 3；
  - 一致性检查的 7 条规则，各有一个正例和一个反例；
  - 补枪按击杀次数计：一次击杀两名队友的凶手，只算 1 次。
- [x] 重建分段基准：`python scripts/build_tier_baseline.py --tier amateur_tmp`。贴出 `source` 一行和各指标的 n。
- [x] `python scripts/page_check_all.py` 的最终输出为 0 处违规；如果不是 0，贴出汇总并说明原因。
- [x] 用评测集第一个 case 和同一 player 跑 `analyze` 和 `summary`，贴出：
  - 每个分段的 `verdict`；
  - 每个分段 problems 的 id、severity、count、expected；
  - 每个分段 strengths 的 id、percentile、samples。
- [x] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。

## 范围外（不要做）

- 不改检测规则和职业阈值（046 做）。
- 不做按"机会数"归一化（审查第 7 条，以后另开任务）。
- 不改 Markdown 报告和 AI 报告。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
  - `features/models.py`、`lobby.py`、`radar.py`：新增补枪击杀事件数和闪光投掷数，补上样本门槛，移除未用道具价值；旧补枪人数口径保留。
  - `report/page_data.py`、`page_template.html`、`cli.py`：逐分段生成完整区块；统一典型单场百分位，使用完整精度期望和泊松尾概率，修正指标说明与例子来源。
  - `report/page_check.py`、`scripts/page_check_all.py`：实现七类一致性检查，CLI 提示不中断，批量结果仅用匿名编号。
  - `config/default.toml`、`config.py`、`scripts/build_tier_baseline.py`：声明并校验样本门槛，按新口径构建基准；整体判断的维度门槛也放入配置 `radar.verdict_min_axes = 4`。
  - `tests/test_page_data.py`、`test_page_check.py`、`test_radar.py`、`test_page_html.py`：覆盖指定复现、七条规则正反例及新数据结构；同步 `docs/features.md` 和任务索引。无新增依赖，修改文件均为 LF。
- 测试结果（最后一行）：
  - 相关三个测试文件：`87 passed in 2.17s`
  - 全量原命令遇到已知的 `test_platform_check.py` 共享目录收集错误；按用户说明排除此文件后：`994 passed, 3 skipped in 38.02s`
  - 隐私扫描：`身份信息命中：0 处。`；`git diff --check` 通过。
- 真实 demo 验证（全程未调用 AI）：
  - 已执行 `.venv/Scripts/python.exe scripts/build_tier_baseline.py --tier amateur_tmp`；已更新 `src/cs2coach/baselines/tiers/amateur_tmp.json`。
  - `source: 23 份 demo、230 人次`
  - 各指标 n：

```text
time_to_damage_ms: n=229
crosshair_placement_deg: n=229
counter_strafing_pct: n=230
spray_accuracy_pct: n=230
trade_kill_events_per_round: n=230
traded_death_pct: n=229
utility_damage_per_round: n=230
enemies_flashed_per_flash: n=184
```

  - `.venv/Scripts/python.exe scripts/page_check_all.py`：七类均为 0；`已检查 230 人次，无法检查 0 项`，`报告页一致性检查：0 处违规`。
  - 用 `data/eval/eval_set.toml` 第 1 个 case 的 demo 和目标玩家执行 `analyze`（30 回合），同一目标玩家执行 `summary`（自动扫描，21 场、438 回合、跳过 0 场），两者退出码均为 0。均使用重建后的基准；本机当前只有 `amateur_tmp` 分段，具体结果如下。输出保存在忽略目录 `data/tmp/task045-validation/`。

```text
analyze / amateur_tmp
verdict: 这场没有检出明显多于业余（临时）的问题。雷达上最弱的是补枪配合（第 9 百分位）。
problems（id / severity / count / expected）:
poor_crosshair_placement / warn / 6 / 4.089249492900609
spray_control / warn / 2 / 0.7849898580121704
overpeek_in_advantage / warn / 1 / 0.26774847870182555
dry_peek_with_utility / warn / 1 / 0.565922920892495
saw_but_no_shot / warn / 1 / 0.6937119675456389
strengths（id / percentile / samples）:
counter_strafing_pct / 83 / 170
time_to_damage_ms / 66 / 31
```

```text
summary / amateur_tmp
verdict: 这段时间没有检出明显多于业余（临时）的问题。雷达上最弱的是预瞄（第 51 百分位）。
problems（id / severity / count / expected）:
poor_crosshair_placement / warn / 68 / 59.70304259634889
died_without_seeing / warn / 18 / 16.7026369168357
overpeek_in_advantage / warn / 5 / 3.909127789046653
died_while_reloading / warn / 2 / 1.421501014198783
glock_long_range / warn / 1 / 0.44421906693711966
strengths（id / percentile / samples）:
counter_strafing_pct / 86 / 1888
utility_damage_per_round / 75 / 438
time_to_damage_ms / 66 / 489
```

- 遗留问题 / 需要确认的地方：
  - 已知共享目录测试收集问题未处理。
  - 本机业余目录实际发现 23 份 demo，与 `config/tiers.toml` 的“24 场”备注不同；按实际发现的全部 demo 完成重建和检查，未改范围外的备注。

## 审查意见（Claude 填写）

（2026-10-18，Windows）**通过**。全部测试 998 通过；`page_check_all.py` 检查 230 人次，0 处违规。按新的审查规则，用 3 名水平不同的玩家生成了真实页面：

- 产品负责人：单场的结论是最弱补枪配合，第 9 百分位；汇总没有 bad。
- 另一名业余玩家：汇总的结论是"最该练的是准星没提前放好：8 场里出现 34 次，按业余（临时）的频率约 23 次"，泊松尾概率 1.9%，与审查前人工判断一致。
- ZywOo：命中速度第 100 百分位，预瞄第 97 百分位，没有 bad，没有乱挑毛病。

审查时 Claude 改了一处：汇总里最低的维度是第 51 百分位，结论却写"最弱的是预瞄"。新增配置 `radar.verdict_weak_percentile = 40`，低于它才说"最弱"，否则写"没有明显偏弱的维度"。百分位改为四舍五入显示。

遗留问题：
- 职业分段还没有构建，审查时已在后台构建。
- `tiers.toml` 的备注写的是 24 场，实际是 23 场。
