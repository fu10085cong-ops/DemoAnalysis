# 031 准星改看初始偏差（米）并排除空中；漏补加视线、换弹、正在交火三个条件

状态：待做
难度：A（新算法、跨解析层 / 特征层 / 基准 / 检测）
执行：Codex，思考强度 high
分支：`task/031-crosshair-trade`

## 目标

审计中准确率最低的两条规则改算法：

- **准星没提前放好**：第一轮准确率 37%。错判来自两点：一是跟移动目标转过的角度被算进去，二是跳跃中看见对手。
- **漏补枪**：判对的部分是 100%，但有一半判不了，因为现在只看直线距离。游戏内确认的 2 条错判，原因分别是"正在换弹"和"阵亡时看不到击杀者"。

Claude 已经在 24 场业余 demo 和 26 份职业 demo 上手工验证过新口径，数字见"背景"。

## 背景（必须读）

- `src/cs2coach/features/moments.py::add_visibility`（准星现在的算法：看见时视角 vs 第一次造成伤害时的视角）、`::find_missed_trades`
- `src/cs2coach/features/visibility.py::Sight`：`eye`、`sees(need_view=...)`、`states`、`view_vector`；`angle_between`
- `src/cs2coach/features/models.py`：`Engagement`、`MissedTrade`
- `src/cs2coach/features/problems.py::detect_problems`：`poor_crosshair_placement`、`missed_trade` 两段
- `src/cs2coach/baseline.py::weapon_stats`
- `src/cs2coach/parsing/match.py`：`PLAYER_PROPS`、`_BOOL_PROPS`
- `config/default.toml`：`[moments]`、`[problems]`

**已验证的事实**（Claude 2026-10-17，不必重新验证）：

- `is_in_reload` 是逐 tick 字段，完美、5E、ESL 三种来源的 `parse_ticks` 都能返回，值为布尔。ESL 没有 `weapon_reload` 事件，所以**不要用事件，用这个字段**。
- `is_airborne` 已经在 `PLAYER_PROPS` 里。
- 游戏单位换算成米：除以 39.37（见 `docs/parsing.md`）。
- **初始夹角跟距离强相关**：职业选手的 p75 在 5 米内是 28°，10–20 米是 7°，30 米外是 2.5°。**换算成"准星离头多少米"后，职业 p75 在各个距离都在 1.7–1.9 米左右**，业余大约 2.3–2.7 米。所以规则用米，不按距离分档。
- 垂直偏差业余和职业差不多，差距主要在水平方向。所以只记录水平和垂直两个分量供报告说明，不单独设规则。
- 看见对手前 0.25 秒到第一次造成伤害之间人在空中的交火：职业占 4%，业余占 5%。
- 漏补中"阵亡后 2 秒内和别的敌人互有伤害"的：业余占 18%，职业占 27%。

## 要做的事

### 1. 解析层：读取 `is_in_reload`

- 加进 `PLAYER_PROPS` 和 `_BOOL_PROPS`。
- 跑一遍 `scripts/check_parsing.py`，确认解析结果没有变化，只多了一列。
- 在 `docs/fields.md` §5 注明：三种来源都已验证，ESL 没有 `weapon_reload` 事件。

### 2. 准星：初始偏差

在 `Engagement` 上新增以下字段，只在现在 `crosshair_placement_deg` 有值的条件下计算：

| 字段 | 含义 |
|---|---|
| `crosshair_initial_deg` | 在 `player_first_seen_tick` 那一刻，本人视角向量与"本人眼睛 → 对手眼睛（头部）"方向的夹角。眼睛位置用 `Sight.eye`，已经考虑下蹲 |
| `crosshair_initial_offset_m` | `距离（米）× sin(min(夹角, 90°))`，即准星离对手头部有多少米 |
| `crosshair_initial_yaw_deg` | 水平分量，取绝对值 |
| `crosshair_initial_pitch_deg` | 垂直分量，带符号，**正数表示准星在头下方**（Source 引擎中 pitch 为正表示向下看） |
| `airborne_near_seen` | 在 `[player_first_seen_tick − airborne_lookback, 第一次造成伤害的 tick]` 区间内，本人 `is_airborne` 任一 tick 为真。没有造成伤害时，区间终点取锚点 |

`crosshair_placement_deg`（Leetify 口径）**保持不变**，报告和指标页照常显示它。

### 3. 准星规则改口径

`poor_crosshair_placement` 的判定改为：

- `crosshair_initial_offset_m` > 职业同武器 p75 ×（1 + `crosshair_margin_ratio`）；
- 并且 `airborne_near_seen` 不为真。
- 原有的排除条件全部保留：穿烟、被闪、看见前开过枪等。

其他要求：

- **新增排除项** `airborne_near_seen`。像其他排除条件一样，在 `[problems.exclusions]` 中登记它适用于哪些规则，本任务只用于 `poor_crosshair_placement`。
- 证据 `evidence` 里给出：`crosshair_initial_offset_m`、`crosshair_initial_deg`、`crosshair_initial_yaw_deg`、`crosshair_initial_pitch_deg`、`distance_m`。阈值写 `source = "pro_p75"`。
- 程序占位符：在 `tools/slots.py` 的问题字段映射中，把 `poor_crosshair_placement` 改成指向 `crosshair_initial_offset_m`，单位为米。新单位按现有占位符的写法补齐，**不要改检查器的整体结构**。
- `weapon_stats` 新增按武器的分布 `crosshair_initial_offset_m`，筛选条件和 `crosshair_placement_deg` 相同，另外排除 `airborne_near_seen`。

### 4. 漏补：三个新条件

在 `MissedTrade` 上新增以下字段：

| 字段 | 含义 |
|---|---|
| `killer_in_view_s` | `[阵亡 tick, 阵亡 tick + 补枪窗口]` 内，本人能看到击杀者（`Sight.sees`，需要在屏幕内）的累计秒数 |
| `killer_first_in_view_s` | 第一次看到的时刻，从阵亡起算的秒数；没看到为 `None` |
| `reloading_at_death` | 阵亡 tick − 1 或阵亡 tick，本人 `is_in_reload` 为真 |
| `other_fight_s` | `[阵亡 − 0.5 秒, 阵亡 + other_fight_window]` 内，本人和击杀者以外的敌人互有伤害（`match.damages`，谁打谁都算）时，最早一次的秒数，从阵亡起算；没有为 `None` |

`missed_trade` 规则在现有"已被其他队友补上"的基础上，下面几种情况也不判：

- `killer_in_view_s` < `missed_trade_min_view_seconds`；
- `killer_first_in_view_s` 为空，或者大于"补枪窗口 − `missed_trade_min_react_seconds`"；
- `reloading_at_death` 为真；
- `other_fight_s` 不为空。

其他要求：

- 原始记录照常保留，排除原因按现有 `skipped["missed_trade/..."]` 的写法分别计数。
- 地图视野不可用时，前两个条件无法计算：**不判**，计入 `missing`，不要当作"能看到"。

### 5. 配置（新键）

```toml
[problems]
crosshair_airborne_lookback_seconds = 0.25
missed_trade_min_view_seconds = 0.25
missed_trade_min_react_seconds = 1.0
missed_trade_other_fight_seconds = 2.0
```

`crosshair_margin_ratio` 沿用 0.10。如果放在 `[moments]` 更符合现有分组，可以调整位置，但要在完成报告中说明。

### 6. 基准与文档

- 运行 `scripts/build_baseline.py`，重建 `src/cs2coach/baselines/pro_sample.json`。完成报告里列出：
  - AK-47、M4A1-S、M4A4、AWP、USP-S、Glock-18、Desert Eagle 的 `crosshair_initial_offset_m` p75；
  - `poor_crosshair_placement` 和 `missed_trade` 在 `problem_rates` 中的新旧值。
- `docs/features.md`：
  - 准星一节写明两个口径：显示用的 Leetify 口径，判定用的初始偏差（米）；说明为什么判定改用初始偏差，以及空中排除；
  - 漏补一节写明三个新条件。
- `docs/problem_catalog.md`：更新这两条的前提条件。

## 验收标准

- [ ] 单元测试，用 `tests/helpers/` 构造数据：
  - **初始偏差**：对手正前方 10 米、准星偏 0° 时为 0；水平偏 10° 时，`offset_m` ≈ 10 × sin 10° ≈ 1.74，`yaw` ≈ 10，`pitch` ≈ 0；准星低于头 5° 时 `pitch` ≈ +5；
  - **空中排除**：只有区间内一个 tick 在空中，也排除；空中的 tick 在区间外不排除；
  - **准星规则**：`offset_m` 刚好在阈值 ×1.1 上下各一个样本；旧字段 `crosshair_placement_deg` 很大、但 `offset_m` 小时**不判**；
  - **漏补**：
    - 每个新条件各有一对样本，只改这一个条件，一个判、一个不判；
    - `killer_first_in_view_s` = 4.1 时不判（窗口 5 秒、反应时间 1 秒），= 3.9 时判；
    - 地图视野不可用时计入 `missing`；
  - **解析层**：`is_in_reload` 列存在，类型为布尔。
- [ ] 开发时只跑相关测试：`tests/test_problems.py tests/test_problem_frequency.py tests/test_moments*.py tests/test_baseline*.py`，以实际存在的文件为准。
- [ ] 完成前跑一次全部测试：`.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **真实 demo 回归**：按 `data/audit/2026-10-17/hits.jsonl` 里下列 `hit_key`（按末尾 8 位匹配）找到 demo 和瞬间，逐条输出新字段：
  - `…262fffc2`、`…bd19c252`：准星，游戏内确认是跳跃中看见对手。预期 `airborne_near_seen = true`，不判；
  - `…2d56e57c`：漏补，游戏内确认正在换弹。预期 `reloading_at_death = true`，不判；
  - `…e792061e`：漏补，阵亡后看不到击杀者。预期 `killer_in_view_s` < 0.25，不判。
- [ ] **全量对比**：运行 `scripts/audit_collect.py --name 2026-10-17-after031`，贴出两条规则的命中数，和 `data/audit/2026-10-17-after029/collect_stats.json` 对比。Claude 手工估算的结果：业余比赛中准星约 1,029 → 约 990（换了约三分之一的条目），漏补 476 → 约 350–430。和这个估算差得多的，在报告里说明原因。
- [ ] 完成报告里**玩家名一律替换成"（已匿名）"**；提交前运行 `scripts/privacy_scan.py`，退出码为 0。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改其他规则。出枪慢、拉出类、Glock 等的口径修改是 032。
- 不新增"准星高度"规则。
- 不改 `crosshair_placement_deg` 的计算和显示。
- 不改 AI 提示词和检查器的整体结构；`slots.py` 只按第 3 步补映射。
- 不修改 `data/audit/*/labels.toml`；审计判定由 Claude 做。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
