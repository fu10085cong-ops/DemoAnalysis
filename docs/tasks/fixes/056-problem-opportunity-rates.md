# 056 问题按机会衡量：出现率 = 命中 / 可判断次数，跨场重复参与排序

状态：待做
难度：A（改问题检测的统计口径，跨问题检测、基准、多场汇总、报告页）
执行：Codex（思考强度 high）
分支：`task/056-problem-opportunity-rates`（基于 055 合并后的 `task/053-tier-ladder`，起点 e859afc 之后）

## 目标

外部审查（2026-10-19）指出：问题现在按"每 10 回合出现几次"和分段比，没有考虑**机会**。打得少、交火少的场次，问题自然少；预瞄问题在 21 场里 20 场出现，按"比 A 段多几次"排序却显得不重要。产品负责人确认：

1. 每条规则的出现率改为 **命中次数 / 可判断次数**（规则走到了判定这一步的次数），与分段同口径比较；"每 10 回合几次"保留，作为出现规模
2. 多场汇总显示 **跨场重复**："21 场里 20 场出现"，并参与"最该练"的选择

## 关键设计：可判断次数由规则自己记录

`detect_problems` 中每条规则对一个对象（交火、漏补机会等）有三种结局：**命中**、**判定后不算**（未超阈值，或 `no_cost`、`close_range` 这类判定后的豁免）、**跳过不判**（排除条件、缺数据、缺阈值）。新增计数 `evaluated[pid]`：前两种都 +1，第三种不计。这样分母由规则自己的前提条件决定，不需要为每个问题另写一套"机会"定义，也自动与命中保持一致。

- 判定后的豁免（如准星的 `no_cost`、`close_range`）**算可判断**：这次交火是能判断的，只是不值得提醒
- 排除条件（`p["exclusions"]`、`missing`、缺阈值）**不算**
- 整场目标（`target == "match"`）与亮点类（`category == "highlight"`）不参与本任务

## 背景（必须读）

- `src/cs2coach/features/problems.py`：`detect_problems`（每条规则的分支、`emit`、`missing`、`skipped`）、`ProblemReport`、`FrequencyRow`、`problem_frequency`、`frequency_from_counts`
- `src/cs2coach/baseline.py::problem_rates`（分段与职业的问题频率）
- `scripts/build_tier_baseline.py`、`scripts/build_baseline.py`（写出 `problem_rates`）
- `src/cs2coach/summary/aggregate.py`（多场合并命中次数）
- `src/cs2coach/report/page_data.py`：`_rows`（问题候选、预期、`severity`）、`_verdict`、`_ladder_verdict`（055 后的"相对谁"句式）
- `src/cs2coach/report/page_template.html`：问题汇总的显示

## 要做的事

1. **`ProblemReport.evaluated: dict[str, int] = {}`**（有默认值，旧缓存可读）。`detect_problems` 中每条已实现的规则在"命中"与"判定后不算"两种结局下 +1。实现时逐条规则检查分支，**命中与 `skipped` 计数必须逐条不变**（Claude 用全量规则回归核对）。
2. **基准**：`problem_rates` 每个问题增加 `evaluated`（合并后的可判断次数）与 `per_100_evaluated`（命中 × 100 / 可判断次数）。旧基准没有这两个键时，退回按回合的现有算法。
3. **频率行**：`FrequencyRow` 新增 `evaluated: int | None`、`per_100_evaluated: float | None`、`matches_with_hits: int | None`、`matches: int | None`。多场汇总合并 `evaluated`，并统计有命中的场次。
4. **预期与显著性**：分段有 `evaluated` 时，预期次数 = 玩家可判断次数 × 分段命中率（`hits / evaluated`），显著性仍用现有泊松尾概率；没有时沿用现有按回合的预期。
5. **"最该练"的选择**：在 `bad` 问题中，先排除跨场重复率 < `[problems] focus_min_match_share`（建议 0.3）的（集中在个别场次，结论句改写为"集中在少数几场"）；其余按多出次数排序，并列时重复率高的优先。单场报告不看重复率。
6. **报告页问题汇总**：每行显示"每 100 次可判断的交火出现 X 次 · 分段 Y 次"，下方小字"每 10 回合 Z 次"；多场再显示"{n} 场里 {m} 场出现"。漏补的分母文字为"补枪机会"（其余为"可判断的交火"；按规则设置文字，写在 `config/problem_text.toml` 或 `PROBLEMS` 中均可，说明理由）。
7. **文档**：`docs/features.md#问题检测` 写明可判断次数的定义、出现率、跨场重复与"最该练"的规则。

**不要重建基准文件**，由 Claude 重建。

## 接口与数据

- `ProblemReport.evaluated: dict[str, int]`
- `problem_rates[pid]`：新增 `evaluated`、`per_100_evaluated`
- `FrequencyRow`：新增 `evaluated`、`per_100_evaluated`、`matches_with_hits`、`matches`
- 配置：`[problems] focus_min_match_share = 0.3`

## 玩家看到什么

- 问题汇总每行："准星没提前放好 · 每 100 次可判断的交火出现 18 次 · 5E A 段 17 次"，小字"每 10 回合 1.23 次 · 21 场里 20 场出现"
- 结论"最该练的是 X"只在 X 跨场重复率达到门槛时出现；否则写"X 偏多，但集中在少数几场"
- 单场报告：只显示出现率与每 10 回合次数，没有跨场重复

## 验收标准

- [ ] 新增 `tests/test_problem_opportunity_056.py`，至少覆盖：每条已实现规则的"命中 / 判定后不算 / 跳过"三种结局对 `evaluated` 的影响（至少准星、出枪慢、漏补、没看见就被打死、压枪各一组）；多场合并；按可判断次数算预期；旧基准退回；跨场重复率门槛对"最该练"的影响
- [ ] 全部测试通过，报告只贴最后一行
- [ ] 不调用真实 AI；不重建基准
- [ ] 真实数据验证由 Claude 完成：全量规则回归命中与 `skipped` 逐条不变；重建基准后生成多场汇总并截图

## 范围外（不要做）

- 不改任何问题的判定阈值、排除条件
- 不做"回合损失"（需要回合胜率模型，`CLAUDE.md` 规定暂不实现）
- 不改段位阶梯、类别面板（055 已处理）

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
