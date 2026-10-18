# 051 雷达"补枪"维度改为漏补率：有补枪机会时没补上的比例

状态：完成（漏补率已实现，但按产品负责人决定未上雷达）
难度：A（改补枪机会的识别逻辑，跨 moments / problems / lobby / radar / 报告页）
执行：Codex（思考强度 high）
分支：`task/051-trade-opportunity`（已建，基于 050；道具移出雷达、命中速度统一命名已在工作区，见下方"已完成的前置改动"）

## 目标

雷达"补枪配合"现在是"补枪击杀次数/回合"和"被队友补回率"的平均。两项都分不出段位（2026-10-18，5E B/A/S 36 场），而且"被补回率"主要取决于队友。产品负责人决定改成**漏补率**：在你本可以补枪的机会里，没有补上的比例，越低越好。现有"漏补"问题检测有段位区分度（全部地图 B 0.40、A 0.35、S 0.28 次/10 回合，p=0.028），但它只有分子；本任务补上分母"补枪机会"，并保证机会的判定与漏补检测是同一套条件。

## 背景（必须读）

- `src/cs2coach/features/moments.py::find_missed_trades`：现有漏补候选的生成（800 单位内、雷达发现过击杀者、视线时长、换弹、正和别人交火）。**它在第 680 行跳过了"自己补上了"的情况，这正是本任务要补回的分母另一半**
- `src/cs2coach/features/problems.py::detect_problems` 末尾 `for record in mm.missed_trades:` 一段：漏补的判定条件和 skipped 计数
- `src/cs2coach/features/models.py`：`MissedTrade`、`MatchMoments`、`LobbyLine`
- `src/cs2coach/features/lobby.py::lobby_line`
- `src/cs2coach/features/radar.py`：`RADAR_METRICS`、`sample_count`、`minimum_samples`、`player_metrics`
- `src/cs2coach/report/page_data.py`：`METRIC_TEXT`、`PROBLEM_METRICS`、`_strength_moments`
- `config/default.toml`：`[moments] trade_window_seconds`、`[problems] missed_trade_*`、`[radar.min_samples]`、`[[radar.axes]] id = "trade"`
- 参照 050 的做法：`src/cs2coach/features/crosshair.py`（雷达指标与问题检测共用同一个样本函数）

## 已完成的前置改动（Claude，未提交，在本分支工作区）

- 道具使用移出雷达，雷达剩 5 个维度；`config.py` 允许 3–8 个维度；优点只从雷达上画出来的维度里选
- 卡片、报告页的 TTD 统一叫"命中速度"
- 相应测试已改，全部通过。不要回退这些改动

## 要做的事

1. **补枪机会记录。** 新增模型 `TradeChance(MissedTrade)`，多两个字段：`traded_by_me: bool`、`my_trade_tick: int | None`（自己在窗口内击杀击杀者的 tick）。`MatchMoments` 新增 `trade_chances: list[TradeChance] = []`（有默认值，旧缓存可读）。

   把 `find_missed_trades` 改写成先生成全部补枪机会 `find_trade_chances(match, p, sid) -> list[TradeChance]`，条件与现在相同，只是**不再跳过自己补上的情况**，改为记 `traded_by_me=True`：
   - `killer_traded_by_other`：窗口内击杀者被**别的**队友击杀（自己补上的不算）。自己和别人都打了击杀者时，以第一个击杀他的人为准（击杀者只会死一次，实际只有一条）
   - 自己补上时，视线统计（`killer_in_view_s`、`killer_first_in_view_s`）的窗口截止到 `my_trade_tick`（击杀者死后不再计）
   - `mm.missed_trades` 保持不变：`[c for c in chances if not c.traded_by_me]` 转回 `MissedTrade`，**内容必须与改动前逐条相同**（验收见下）

2. **机会是否计入：一个共用函数。** 新增（放在 `features/problems.py` 或新文件 `features/trades.py`）：

   ```python
   def trade_chance_skip(record: TradeChance | MissedTrade, cfg: dict) -> str | None:
       """不计入的原因（与 missed_trade 的 skipped 键后缀一致），计入返回 None。"""
   ```

   顺序和原因与 `detect_problems` 里现有的漏补判定完全一致：`killer_traded_by_other`（受 `missed_trade_exclude_traded_by_other` 控制）→ `last_alive` → 视线缺失（返回 `"missing"`，调用方按原样计入 `skipped["missed_trade"]`）→ `killer_in_view_s` → `killer_first_in_view_s` → `reloading_at_death` → `other_fight_s`。唯一的例外：**`traded_by_me=True` 时不检查 `killer_in_view_s` 的最短时长**（补上了说明看得够久；最短时长是为了排除"一闪而过、来不及补"的漏补），其余条件照常检查。`detect_problems` 的漏补分支改为调用它，命中与 skipped 计数必须逐条不变。

3. **LobbyLine 新增**：`trade_chances: int = 0`（计入的机会数）、`missed_trade_chances: int = 0`（其中没补上的）。`lobby_line` 从 `mm.trade_chances` 用 `trade_chance_skip(...) is None` 统计。

4. **雷达**：`RADAR_METRICS` 新增 `"missed_trade_pct": True`（越低越好）= `100 * missed_trade_chances / trade_chances`，`trade_chances` 为 0 时为空。`sample_count` 返回 `line.trade_chances`；`minimum_samples` 读新配置 `[radar.min_samples] trade_chances = 5`。`config/default.toml` 的 id = "trade" 轴改为 `name = "补枪"`、`metrics = ["missed_trade_pct"]`。`trade_kill_events_per_round`、`traded_death_pct` 保留在 `RADAR_METRICS` 中（分段基准继续记录），只是不在任何轴上。

5. **报告页**（`page_data.py`）：
   - `METRIC_TEXT["missed_trade_pct"]`，见下方"玩家看到什么"
   - `PROBLEM_METRICS["missed_trade"] = "missed_trade_pct"`
   - `_strength_moments(mm, "missed_trade_pct", ...)`：返回计入的、`traded_by_me=True` 的机会所对应的补枪击杀瞬间（`kind == "kill"`、`opponent == killer`、`tick == my_trade_tick`），按时间顺序

6. **文档**：`docs/features.md` 写明 `trade_chances` 的定义、与漏补检测共用条件、自己补上时不查最短可见时长的原因；雷达一节把补枪维度改为漏补率。

**不要重建基准文件**，由 Claude 审查后重建。

## 接口与数据

- `TradeChance(MissedTrade)`：`traded_by_me: bool`、`my_trade_tick: int | None = None`
- `MatchMoments.trade_chances: list[TradeChance] = []`
- `find_trade_chances(match, p, sid) -> list[TradeChance]`；`find_missed_trades` 保留，由前者派生
- `trade_chance_skip(record, cfg) -> str | None`
- `LobbyLine.trade_chances: int = 0`、`LobbyLine.missed_trade_chances: int = 0`
- `[radar.min_samples] trade_chances = 5`（`config.py` 校验为正整数）

## 玩家看到什么

- 雷达轴名"补枪"；报告页指标：`METRIC_TEXT["missed_trade_pct"] = ("漏补率", "队友在你附近阵亡、你也看到了击杀者时，5 秒内没有补上的比例（越低越好；击杀者被其他队友补掉、只剩你一人、你正在换弹或正和别的对手交火的不算）", "%", 1)`
- 做得好的方向里的例子：自己补上的补枪击杀，最多 `max_examples` 条
- 问题汇总"漏补枪"现在挂在"补枪"维度下；045 的"同一维度不能既是优点又是突出问题"规则继续生效
- 机会少于 5 次：该轴为空，沿用现有空值显示

## 验收标准

- [ ] **回归：`mm.missed_trades` 不变。** 全量回归由 Claude 负责：开工前已在 `data/audit/2026-10-18-before051/` 跑好基线，审查时用 `scripts/rule_regression.py --compare-to 2026-10-18-before051` 对比，`missed_trade` 命中与各 skipped 计数必须不变。实现方不要跑全量回归；在单元测试中用合成数据验证"改动前后 `missed_trades` 相同"即可
- [x] 新增 `tests/test_trade_chance_051.py`，至少覆盖：
  - 自己 5 秒内补上 → 一条 `traded_by_me=True`、`my_trade_tick` 正确，不出现在 `missed_trades` 中
  - 别的队友补上 → `killer_traded_by_other=True`，`trade_chance_skip` 返回 `"killer_traded_by_other"`
  - 自己补上、`killer_in_view_s` 只有 0.2 秒 → 计入（不检查最短时长）；没补上、0.2 秒 → 不计入
  - 自己补上但正在换弹 / 正和别人交火 / 只剩自己 → 与没补上时一样不计入
  - 一致性：对一组机会，`detect_problems` 的 `missed_trade` 命中 ⇔ `traded_by_me is False and trade_chance_skip(...) is None`
  - `lobby_line` 的 `trade_chances` / `missed_trade_chances`；`player_metrics` 的 `missed_trade_pct`；机会 4 次为空、5 次有值
  - 报告页优点例子是对应的补枪击杀
- [x] 开发时只跑相关测试：`.venv/Scripts/python.exe -m pytest -q tests/test_trade_chance_051.py tests/test_moments.py tests/test_problems.py tests/test_radar.py tests/test_page_data.py tests/test_lobby.py`
- [x] 完成前跑一次全部测试：`.venv/Scripts/python.exe -m pytest -q`，报告只贴最后一行（`tests/test_platform_check.py` 在沙箱里读不到共享目录属已知问题，注明即可）
- [x] `python scripts/page_check_all.py` 违规为 0，或逐条说明原因

## 范围外（不要做）

- 不改漏补检测的判定结果和阈值（800 单位、5 秒窗口、0.4 秒、3 秒等）
- 不重建基准文件
- 不改 AI 提示词、占位符、检查器；不改 `missed_trades` 的现有消费方
- 不改其他雷达轴；不动急停和压枪（下一步与产品负责人另议）

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：`features/models.py`、`moments.py` 增加全部补枪候选并派生原漏补记录；新增 `features/trades.py`，由 `problems.py`、`lobby.py`、`report/page_data.py` 共用机会排除条件；`features/radar.py`、`config/default.toml`、`config.py` 接入漏补率及 5 次机会门槛；`docs/features.md` 更新口径；新增 `tests/test_trade_chance_051.py`，同步更新 `test_radar.py`、`test_page_data.py`、`test_page_check.py` 的旧指标夹具与断言；本任务单和任务索引更新状态。保留 Claude 的前置改动，无新增依赖。
- 测试结果（最后一行）：相关测试 `268 passed in 10.17s`；全量可运行部分 `1117 passed, 3 skipped in 56.05s`。完整命令先运行，因 `tests/test_platform_check.py` 读取 `data/eval/platform` 被沙箱拒绝而收集中断，随后仅用 `--ignore=tests/test_platform_check.py` 排除该已知项。测试临时目录与缓存通过 `--basetemp=data/task051-pytest-* -o cache_dir=data/task051-pytest-cache` 放在工作区内，避开默认临时目录权限问题。
- 回归对比结果：合成数据逐字段验证原 `missed_trades`，并验证命中 evidence、排除顺序及启用/停用时各 skipped 计数；均通过。未运行全量规则回归，留给 Claude 对比 `2026-10-18-before051`。
- 真实 demo 验证（如有）：`.venv/Scripts/python.exe scripts/page_check_all.py` 按 `config/tiers.toml` 的 `amateur_tmp` 清单检查 23 场、230 人次，无法检查 0 项；`报告页一致性检查：0 处违规`。没有调用真实 AI API。
- 遗留问题 / 需要确认的地方：上述共享目录测试需在沙箱外补跑；未重建任何基准文件，漏补率的真实分段百分位和补枪轴需 Claude 重建基准后复核（新指标及优点例子已用合成分段验证）。全量规则回归仍待 Claude 审查执行。

## 审查意见（Claude 填写）

2026-10-18 通过。

- `features/trades.py::trade_chance_skip` 与原漏补判定顺序、skipped 键逐一对应；自己补上时视线统计截止到补枪 tick、不查最短可见时长。
- 全量回归（`data/audit/2026-10-18-after051/` 对比 `before051`）：23 场 230 人次 3681 条命中，hit_key、evidence、collect_stats 逐条相同（仅耗时不同）。全部测试 `1120 passed, 4 skipped`。
- **漏补率没有上雷达**：重建基准后 `missed_trade_pct` 职业只有 12 个有效人次、业余 22 个（都不足 30）；5E B/A/S 36 场每人每场平均 1.8 次计入机会，≥5 次的只有 16/360；合并漏补率 B 41.1%、A 35.1%、S 37.5%，S 与 B 不显著（p=0.39）。产品负责人决定补枪移出雷达，雷达剩命中速度、预瞄、急停、压枪四个维度，`verdict_min_axes` 从 4 降到 3。漏补率代码与基准记录保留，留给多场汇总。
- 页面测试原本用补枪指标做优点样例：`tests/test_page_data.py`、`tests/test_page_check.py` 改用"生产配置 + 补枪轴"的测试配置，`test_trade_chance_051.py` 的报告页用例同样加回补枪轴；雷达实际配置由 `tests/test_radar.py::test_configured_axes` 检查。`page_check_all.py` 230 人次 0 处违规。
