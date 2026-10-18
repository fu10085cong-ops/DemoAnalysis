# 050 雷达"预瞄"改用初始偏差米数，与"准星没放好"同一把尺子

状态：待做
难度：B（照现有写法扩展，跨 radar / 报告页 / 卡片 / 汇总，但每处改动都是替换一个指标键）
执行：Codex（思考强度 medium）
分支：`task/050-radar-crosshair-offset`

## 目标

雷达"预瞄"现在用 Leetify 的 Crosshair Placement（看见 → 首次命中准星**转过的度数**），问题检测"准星没放好"用**刚看见时准星离头部的米数**。两者测的不是同一件事，会出现"预瞄是优点、准星没放好又是问题"的矛盾（045 已遇到过）。2026-10-18 产品负责人决定：**雷达、总结卡片、报告页的"预瞄"一律改用初始偏差米数**，样本口径与问题检测完全一致。Leetify 口径的 `crosshair_placement_deg` 保留计算（AI 摘要和控制台仍可用），但不再出现在雷达、卡片和报告页上。

段位数据参考（2026-10-18，5E B/A/S 共 36 场，仅 Dust2，每人每场中位数）：初始偏差 B 1.19 m、A 1.21 m、S 1.03 m。

## 背景（必须读）

- `src/cs2coach/features/problems.py::detect_problems`：内部函数 `exclusions()` 和 `poor_crosshair_placement` 分支（武器白名单、`crosshair_min_distance_m`、`p["exclusions"]`），新指标的样本口径照它来
- `src/cs2coach/features/aim_stats.py::aim_stats`：已接收 `moments` 参数，新指标在这里算
- `src/cs2coach/features/radar.py`：`RADAR_METRICS`、`sample_count`、`player_metrics`
- `src/cs2coach/report/page_data.py`：`METRIC_TEXT`、`PROBLEM_METRICS`、`_strength_moments`、做得好的方向里对 `time_to_damage_ms` / `crosshair_placement_deg` 的排序特判
- `config/default.toml`：`[[radar.axes]]` id = "crosshair"、`[[report.card.dimensions]]` key = "crosshair"
- `src/cs2coach/config.py`：`report.card.dimensions` 的 `allowed_fields`
- `src/cs2coach/baseline.py`：`LOWER_IS_BETTER`、`METRICS`
- `src/cs2coach/summary/aggregate.py::_AIM_FIELDS`、`src/cs2coach/tools/digest.py::METRIC_LABELS`
- `docs/features.md#整场枪法统计 aim_stats（Leetify 口径）`

## 要做的事

1. **样本口径抽成共用函数。** 把 `detect_problems` 里的 `exclusions(moment)` 提到模块级 `active_exclusions(moment, p) -> set[str]`（行为不变，`detect_problems` 改为调用它）。新增 `src/cs2coach/features/crosshair.py`：

   ```python
   def crosshair_offset_sample(moment: Moment, cfg: dict) -> float | None:
       """可参与"预瞄"统计的初始偏差（米）；与 poor_crosshair_placement 的判定前提完全一致，不含阈值和 no_cost。"""
   ```

   返回非空的条件（全部满足）：
   - `moment.kind in ("kill", "death")`，`moment.tags` 与 `baseline.EXCLUDED_TAGS` 无交集，`moment.engagement` 不为空
   - `engagement_weapon(moment)` 在 `cfg["aim_stats"]["weapons"]` 中
   - `engagement.crosshair_initial_offset_m` 是有限数
   - 不是近身：`context.distance` 缺失时照常计入，有值且 `< cfg["problems"]["crosshair_min_distance_m"]` 时不计入
   - `active_exclusions(moment, cfg["problems"])` 中没有任何一个条件在 `cfg["problems"]["exclusions"]` 里列了 `poor_crosshair_placement`

   注意循环导入：`aim_stats.py` 要能导入 `crosshair.py`。如有循环，把 `active_exclusions` 放进 `crosshair.py`，由 `problems.py` 反过来导入。

2. **`AimStats` 新增两个字段**（`features/models.py`）：`crosshair_offset_m: float | None`（中位数，保留 2 位小数）、`crosshair_offset_samples: int`。`aim_stats()` 中对 `moments` 逐个调用 `crosshair_offset_sample`，样本数 ≥ `aim_stats.min_samples` 时给中位数，否则为空。`crosshair_placement_deg` 及其样本数保持原样。

3. **雷达**：`RADAR_METRICS` 中用 `"crosshair_offset_m": True`（越低越好）替换 `crosshair_placement_deg`，位置不变（仍是第 2 个，`minimum_samples` 用前 4 个判断枪法指标）；`sample_count` 映射到 `crosshair_offset_samples`；`player_metrics` 读新字段。`config/default.toml` 中 id = "crosshair" 的轴 `metrics = ["crosshair_offset_m"]`，名称"预瞄"不变。

4. **总结卡片**：key = "crosshair" 的 `field` 改为 `aim.crosshair_offset_m`；`config.py` 的 `allowed_fields` 加入 `aim.crosshair_offset_m`（保留旧值，旧配置不报错）。

5. **报告页**（`page_data.py`）：
   - `METRIC_TEXT` 用 `crosshair_offset_m` 替换 `crosshair_placement_deg`，见下方"玩家看到什么"
   - `PROBLEM_METRICS["poor_crosshair_placement"] = "crosshair_offset_m"`
   - `_strength_moments` 和做得好的方向里按数值排序的特判：`crosshair_offset_m` 用 `crosshair_offset_sample(m, cfg)` 取值，只选返回非空的击杀，按偏差从小到大
   - `card.py` 同场对比表里 `aim.crosshair_placement_deg` 一列改为 `aim.crosshair_offset_m`

6. **基准与汇总**：`baseline.py` 的 `LOWER_IS_BETTER` 与 `METRICS` 加入 `crosshair_offset_m`（保留 `crosshair_placement_deg`）；`summary/aggregate.py::_AIM_FIELDS` 加入；`tools/digest.py::METRIC_LABELS` 加入 `("预瞄（初始偏差）", "米", "crosshair_offset_samples", "次交火")`。**不要重建基准文件**，由 Claude 在审查后重建（需要解析职业 demo，沙箱外运行）。

7. **文档**：`docs/features.md` 的 aim_stats 表格加一行 `crosshair_offset_m`，说明它是我们自己的口径（Leetify 无此指标）、样本范围与"准星没放好"相同；雷达一节如有提到预瞄用 Crosshair Placement，同步改掉。

## 接口与数据

- `crosshair_offset_sample(moment, cfg) -> float | None`（`features/crosshair.py`）
- `active_exclusions(moment, p) -> set[str]`（`features/problems.py` 或 `features/crosshair.py`）
- `AimStats.crosshair_offset_m: float | None`、`AimStats.crosshair_offset_samples: int`（给默认值 `None` / `0`，旧缓存与旧 JSON 能读）
- 分段基准 JSON 中新指标键为 `crosshair_offset_m`；旧基准文件里没有这个键时，雷达该轴为空（现有逻辑已如此处理，确认即可）

## 玩家看到什么

- 雷达"预瞄"轴、总结卡片"预瞄"、做得好的方向：
  - `METRIC_TEXT["crosshair_offset_m"] = ("预瞄", "刚看见对手那一刻，准星离他头部的距离（典型单场的中位数，越小越好；5 米内的近身交火、跳跃中和转身才看到的对手不算）", "米", 1)`
  - 数值显示保留 2 位小数，例如"1.03 米"。如果现有数字格式化只能按单位统一处理，在完成报告里说明实际显示效果
- 与问题汇总"准星没放好"现在是同一把尺子：雷达是整场中位数，问题是单次超过职业同武器 p75 × 1.25。中位数好、个别交火很差时两边可能同时出现，这是合理的，不额外处理；045 已有的"同一方向不能既是优点又是突出问题"规则继续生效
- 例子：做得好的方向下列出的击杀必须是参与了这个指标统计的击杀（`crosshair_offset_sample` 非空），按偏差从小到大
- 样本不足（< `min_samples`）：沿用现有的空值显示

## 验收标准

- [ ] 新增 `tests/test_crosshair_offset_050.py`，至少覆盖：
  - `crosshair_offset_sample`：白名单武器正常返回；`mp9` 返回 None；距离 4.9 返回 None、5.0 返回值、距离缺失返回值；`airborne_near_seen=True`、`opponent_in_sight_before_view_ticks` ≥ `crosshair_turn_min_ticks`、`through_smoke=True`、`shots_before_seen` ≥ `max_shots_before_seen` 各返回 None；`assist` 返回 None
  - **一致性**：对上面每个样本，`detect_problems` 中 `poor_crosshair_placement` 被判或因 `no_cost` 跳过 ⇔ `crosshair_offset_sample` 非空（构造偏差远超阈值的样本，再验证两边结论一致）
  - `aim_stats` 的 `crosshair_offset_m` 等于合格样本的中位数；样本数 4 时为 None、5 时有值（可复用 `tests/test_visibility.py` 的构造方式，或直接对 `moments` 调用聚合的那段逻辑）
  - `player_metrics` / `sample_count` 读到新字段；`RADAR_METRICS["crosshair_offset_m"] is True`
- [ ] 现有测试中写死 `crosshair_placement_deg` 作为雷达或卡片指标的，改为新键；改动的测试文件在完成报告中逐个列出并说明原因
- [ ] 开发时只跑相关测试：`.venv/Scripts/python.exe -m pytest -q tests/test_crosshair_offset_050.py tests/test_radar.py tests/test_page_data.py tests/test_card.py`
- [ ] 完成前跑一次全部测试：`.venv/Scripts/python.exe -m pytest -q`，报告只贴最后一行
- [ ] `python scripts/page_check_all.py` 违规为 0，或逐条说明原因

## 范围外（不要做）

- 不重建 `baselines/pro_sample.json`、`baselines/tiers/*.json`
- 不删除 `crosshair_placement_deg`（交火字段、AimStats 字段、职业基准中的分布都保留）
- 不改 AI 提示词、占位符（`tools/slots.py`）和检查器
- 不改 `poor_crosshair_placement` 的判定结果（只允许把排除条件的计算挪成共用函数，判定与 skipped 计数必须逐条不变）
- 不改其他雷达轴

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
