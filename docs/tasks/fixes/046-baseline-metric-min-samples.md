# 046 职业阈值按指标检查样本数，分位数不外推（外部审查第 14 条）

状态：待做
难度：B（改 `baseline.py` 两处，并重建基准、跑规则回归）
执行：Codex（思考强度 medium）
分支：`task/046-baseline-min-samples`

## 目标

现在只检查"某武器的交火总数 ≥ `min_engagements`（30）"。只要总数够，各指标即使只有很少的有效值，也会生成正式阈值供检测规则使用。真实的职业基准里：

| 武器 | 指标 | 样本数 |
|---|---|---:|
| SSG 08 | `time_to_damage_ms`、`seen_to_first_shot_ms` | 12 |
| SSG 08 | `crosshair_placement_deg`、`crosshair_initial_offset_m` | 13 |
| MP7 | `time_to_damage_ms`、`seen_to_first_shot_ms` | 19 |
| MP7 | `crosshair_initial_offset_m` | 18 |

另外，`build_baseline` 用的 `statistics.quantiles` 默认是 exclusive 方法，小样本时会外推出界：对 `[0, 100]` 算出的 p25 = −25、p75 = 125。

## 背景（必须读）

- `src/cs2coach/baseline.py`：`_distribution`、`weapon_stats`、`build_baseline`、`weapon_threshold`
- `config/default.toml` 的 `[baseline]`
- `scripts/rule_regression.py --help`：重建基准、收集全部命中，并沿用历史判定估算有用率

## 要做的事

1. **按指标检查样本数**
   - `[baseline]` 新增 `min_metric_samples = 20`，并在 `config.py` 里校验它是正整数。
   - `_distribution` 增加参数 `min_n`：有效值少于 `min_n` 时，p50、p75、p90 都为 None，`n` 照常记录。
   - `weapon_stats` 里所有 `_distribution(...)` 调用都传入 `min_metric_samples`。
   - `weapon_threshold` 不用改：值为 None 时它本来就返回 None，检测规则会记为缺阈值、跳过这把武器。确认现有逻辑确实是这样，如果不是，停下来写进完成报告。
2. **分位数不外推**：`build_baseline` 改用 `statistics.quantiles(v, n=4, method="inclusive")`。
3. **重建并跑回归**：
   ```
   python scripts/rule_regression.py --name 2026-10-18-after046 --compare-to 2026-10-17-after039 --labels-from 2026-10-17-after037
   ```
   这一步会重建 `pro_sample.json`。之后再跑 `python scripts/build_tier_baseline.py --all`：分段基准的问题频率依赖职业阈值，要一起重建。
4. 在 `docs/features.md` 的职业基准一节写明样本门槛。

## 验收标准

- [ ] `tests/test_baseline.py`（或现有的基准测试文件）新增用例：
  - 30 次交火里只有 1 个有效 TTD，阈值为 None，`n` 为 1；
  - 有效值达到 20 个时正常生成阈值；
  - `build_baseline` 对 `[0, 100]` 算出的 p25、p75 落在 [0, 100] 内。
- [ ] 重建后，贴出 `pro_sample.json` 中 SSG 08、MP7 各指标的 n 和 p75。
- [ ] 贴出 `rule_regression.py` 输出的命中数变化表；变化大于 5% 的规则，说明原因。
- [ ] 跑 `python scripts/page_check_all.py`，结果为 0 处违规。
- [ ] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。

## 范围外（不要做）

- 不改其他检测规则和阈值分位。
- 不改报告页（045 已完成）。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
