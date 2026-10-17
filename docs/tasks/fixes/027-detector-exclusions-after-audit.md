# 027 按审计结果修检测规则：补排除条件、看见后的第一枪、阈值余量

状态：待做

## 目标

S01 第一批审计（`docs/eval_log.md` 的"2026-10-17 检测规则审计"）判定了 353 条命中，wrong 89 条。**错误大多不是规则写错，而是少了排除条件**：穿烟、穿墙、交火中被闪、看见前已在开枪、已被队友补上。这些条件在数据里都有。

本任务按审计结果一次修好这些问题，再用**同一批判定**重新计算，验证修完后每条规则的准确率提高了多少，以及误删了多少原本判对的命中。

"准星初始夹角"和"漏补视线判断"改动较大，不在本任务范围内，另开结构性任务。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`、`docs/tasks/README.md`
- `docs/eval_log.md`："2026-10-17 检测规则审计"一节，里面有错误原因表和结论
- `data/audit/2026-10-17/labels.toml`：353 条判定，包括 `verdict`、`reason`、`note`，只读。**不要修改**。
- `data/audit/2026-10-17/hits.jsonl`：命中的完整记录
- `src/cs2coach/features/problems.py`：`detect_problems` 中各规则的前提条件
- `src/cs2coach/features/models.py`：
  - `Moment.through_smoke`、`Moment.wallbang`、`Moment.weapon`；
  - `Engagement.player_blind`、`Engagement.opponent_blind`（`BlindInfo.thrower_role`）、`shots_before_seen`、`first_shot_stopped`、`first_shot_hit`；
  - `Shot.seen_opponent`（026 新增）；
  - `MissedTrade.killer_traded_by_other`
- `src/cs2coach/report/evidence.py`：程序依据的句子
- `src/cs2coach/audit.py`、`scripts/audit_*.py`：S01 的审计工具
- `scripts/build_baseline.py`：检测规则一改，职业频率就要重算

## 要做的事

### 1. 统一的排除条件

新增配置 `[problems.exclusions]`：**每个条件对应一组不适用的问题**，检测时满足任一条件就不判这条问题。规则写进配置，不要散落在各条规则的代码里。

| 条件 | 判断方法 | 默认适用的问题 | 审计中的依据 |
|---|---|---|---|
| `through_smoke` | `Moment.through_smoke` 为真 | 出枪慢、准星、压枪、扫射时移动、拉出撞架点、有道具直接拉出、人数优势冒进、先发现先开枪。**不包括**开枪时没停稳、沙鹰移动开枪：移动开枪不受烟影响，审计中一条穿烟的沙鹰样本判为 correct | 33 条 wrong |
| `wallbang` | `Moment.wallbang` 为真 | 拉出撞架点、有道具直接拉出、人数优势冒进 | 3 条 |
| `player_blinded_in_fight` | `Engagement.player_blind` 不为空。BlindInfo 只记录 ≥ `min_blind_seconds` 的致盲 | 出枪慢、准星、压枪、扫射时移动、开枪时没停稳、拉出撞架点、有道具直接拉出 | 8 条 wrong + 2 条 unsure |
| `opponent_blinded_by_own_side` | `Engagement.opponent_blind` 不为空，且 `thrower_role` 是 `self` 或 `teammate` | 拉出撞架点、人数优势冒进 | 2 条：对手正被你方闪白时拉出是正确打法 |
| `killed_by_utility` | 死亡瞬间的击杀武器是 `hegrenade` / `inferno` / `molotov` / `incgrenade` | 没看见就被打死、拉出撞架点 | 2 条 |
| `fired_before_seen` | `shots_before_seen` ≥ `problems.max_shots_before_seen`，新增配置，默认 2 | 出枪慢、准星 | 4 条 wrong；1 发的留着，审计中判为 unsure |

- **排除也要留下记录**：`ProblemReport.skipped` 中按"问题、条件"计数，例如 `slow_reaction/through_smoke: 12`，`problems.md` 里显示这些计数，方便复查。
- `died_without_seeing` 原本就排除穿墙、穿烟、被闪，保持不变，只加上 `killed_by_utility`。

### 2. "第一枪"改为看见对手之后的第一枪

审计中有 4 条 wrong：程序判断的"第一枪"，其实是看见对手之前打出的预瞄或乱扫子弹。

- `Engagement` 新增 `first_seen_shot_stopped: bool | None`、`first_seen_shot_hit: bool | None`：取这次交火中 `seen_opponent is True` 的第一发子弹。没有这样的子弹时为 None。
- `shot_while_moving`、`deagle_moving_shot` 改用这两个字段；为 None 时不判。
- 程序依据的句子写成"看见对手后的第一枪未停稳……"。
- 事实卡中原有的"第一枪……"不改，那是对整次交火的描述。如果这次交火有看见前的子弹，并且看见后第一枪的停稳情况和第一枪不同，就在事实卡里加一句"看见对手后的第一枪：……"。

### 3. 阈值余量

- **开枪时没停稳**：新增 `problems.moving_shot_margin`，默认 0.10。看见后第一枪的 `speed_ratio` 必须**不低于** `accurate_speed_ratio × (1 + 余量)`，即 0.374，才判为没停稳。审计中速度比在 0.34–0.36 之间的 5 条全部是 wrong。
  - 这个余量只用于问题检测；事实卡里"已停稳 / 未停稳"仍按 0.34 显示。
  - 这需要 `Shot.speed_ratio`，已有。
- **准星没提前放好**：新增 `problems.crosshair_margin_ratio`，默认 0.10。转角至少超过职业 75 分位的 10% 才判。审计中有 2 条只超 0.5° 的 wrong。

### 4. 先发现、先开枪：领先要够

`won_duel_seen_first` 要求 `seen_first_lead_s` 和 `first_shot_lead_s` 都不少于 `problems.highlight_min_lead_s`，新增配置，默认 0.05 秒，约 3 tick。审计中有 2 条只领先 1–2 tick 的 wrong。

### 5. 漏补枪排除"已被其他队友补上"

新增 `problems.missed_trade_exclude_traded_by_other`，默认 true。`killer_traded_by_other` 为真的漏补记录不算命中。

- 全部漏补命中中有 207/683（30%）属于这种情况。
- 审计中 6 条全部是 wrong。
- **注意**：这会让同场表、体检卡里的漏补次数也随之减少。漏补记录本身照样保留在 `MatchMoments.missed_trades` 里，只是不再作为问题命中。

### 6. 审计统计的置信区间

现在某一层准确率为 0% 或 100% 时，分层方差为 0，区间退化成一个点，例如出枪慢显示 91.9%–91.9%。改为**用两层各自的 Wilson 区间按权重合成**：下界为 `Σ W·下界`，上界为 `Σ W·上界`。这种合成偏保守，在报告开头注明。补测试：两层分别为 0% 和 100% 时，区间不退化。

### 7. 用同一批判定验证改进

- `audit_collect.py` 和 `audit_report.py` 新增 `--name`，用来指定输出目录名，代替默认的日期。这样改动后的结果可以写到 `data/audit/2026-10-17-after027/`，不覆盖改动前的结果。
- `audit_report.py` 新增 `--labels-from <目录名>`：把改动前的判定套到改动后的命中上，按 `hit_key` 匹配。`hit_key` 由 demo、成员、玩家、问题、目标组成，不受规则改动影响。每类问题输出：
  - 改动前后各有多少条已判定命中、加权准确率各是多少；
  - 被移除的已判定命中，按判定分开计数：wrong 被移除是好事，**correct 被移除是误删**，逐条列出 `hit_key` 和当时的备注；
  - 改动后新出现的命中，审计时没有判定，单独计数，不计入准确率。

### 8. 重建职业基准

检测规则改了，`problem_rates` 就要重算：运行 `scripts/build_baseline.py`。在完成报告里列出各问题职业频率的前后对比，并说明 `metrics`、`weapons` 是否不变（应该不变）。

## 接口与数据

- 新增配置：
  - `[problems.exclusions]`：条件 → 问题清单；
  - `problems.max_shots_before_seen`、`problems.moving_shot_margin`、`problems.crosshair_margin_ratio`、`problems.highlight_min_lead_s`、`problems.missed_trade_exclude_traded_by_other`。

  全部在默认配置中声明，并通过配置校验：条件名和问题编号都必须存在。
- `Engagement` 新增 `first_seen_shot_stopped`、`first_seen_shot_hit`。
- 审计脚本新增 `--name`、`--labels-from`。
- `docs/features.md`、`docs/problem_catalog.md` 同步各规则的前提条件。

## 验收标准

- [ ] 测试，用合成数据：
  - 每个排除条件各有一个"排除"和一个"不排除"的用例，并且排除计数正确；
  - 看见前有子弹时，用的是看见后的第一枪；全部是看见前的子弹时不判；
  - 速度比 0.36 不判、0.38 判；准星只超 5% 不判、超 15% 判；
  - 领先 0.03 秒不算先发现、0.06 秒算；
  - 被其他队友补上的漏补不算命中，但记录仍在；
  - 置信区间在两层分别为 0% 和 100% 时不退化；
  - `--labels-from` 的匹配，以及误删列表。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **真实数据验证**：
  1. `.venv/bin/python scripts/audit_collect.py --name 2026-10-17-after027`
  2. `.venv/bin/python scripts/audit_report.py --name 2026-10-17-after027 --labels-from 2026-10-17`
  3. 在完成报告中贴出每类问题改动前后的加权准确率、移除的 wrong 和 correct 数量、误删的 correct 清单（`hit_key` 和备注），**不贴玩家名**。
  4. **预期**：
     - 原因为穿烟、交火中被闪、已被其他队友补上、穿墙、被燃烧瓶烧死、贴阈值、噪声领先的 wrong 应该大部分被移除；
     - "压枪差""开枪时没停稳""漏补枪"的准确率应该明显提高。
     - 如果有 correct 被移除，逐条说明是哪个条件导致的。误删超过 5 条时停下来，写进完成报告，等 Claude 判断。
- [ ] 职业基准已重建，前后对比写进完成报告。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不修改 `data/audit/2026-10-17/` 下的任何文件，特别是 `labels.toml`。
- 不做"准星初始夹角"和"漏补时能否看到击杀者"，另开结构性任务。
- 不改 `weapon_not_ready` 的定义，继续停用。
- 不改停用清单。要不要启用停用的规则，等产品负责人决定。
- 不改 AI 提示词和检查器。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
