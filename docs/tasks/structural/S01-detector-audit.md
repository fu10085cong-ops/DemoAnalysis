# S01 检测规则审计：抽样核对准确率、全部 demo 的可疑组合扫描

状态：待做（结构性任务，说明见 `docs/tasks/README.md` 的"三类任务"）

## 目标

免费版和 AI 版都建立在程序检测出的问题上，但到现在为止，我们只测量过 AI 叙述的错误率，**从来没测量过"程序判定你有这个问题"本身对不对**。026 修的 R4 是偶然看到事实卡才发现的：一次误判让 AI 写错了分析，体检卡也推荐错了练习。

本任务做两个工具：

1. **抽样核对**：把全部业余 demo 中每类问题的命中随机抽一批，连同事实卡、程序依据、跳转命令写成核对清单。Claude 逐条判断对错，涉及"看见"的由产品负责人进游戏确认。另一个脚本读取判定结果，算出每类问题的准确率。
2. **可疑组合扫描**：在全部 demo 的全部命中上自动检查一组"不应该同时出现"的条件，例如"判了出枪慢，但看见对手之前就已经在开枪"，把可疑的命中列出来。

本任务只做工具和第一批数据，**不改任何检测规则**。要改哪些规则，等准确率出来后另开任务。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`、`docs/tasks/README.md`
- `docs/problem_catalog.md`：每类问题的定义和前提条件
- `src/cs2coach/features/problems.py`：`detect_problems`、`PROBLEMS`、`ProblemHit`
- `src/cs2coach/baseline.py`：`problem_rates`。它在校准时会把停用的规则也打开（`disabled = []`、`spray_match_enabled = True`），本任务照同样的做法
- `src/cs2coach/features/lobby.py`：一次解析、共用 `Sight`、对每名玩家调用 `find_moments` 的写法
- `src/cs2coach/summary/discover.py`：怎么找到 demo 和压缩包成员
- `src/cs2coach/report/facts.py`：`render_moment_card`、`missed_trade_facts`；`src/cs2coach/report/evidence.py`：`evidence_sentence`
- `src/cs2coach/features/models.py`：`Engagement` 中可用于扫描的字段，例如 `shots_before_seen`、`seen_to_first_shot_s`、`visibility_note`、`player_seen_while_blind`、`player_speed_at_contact`、`opponent_speed_at_contact`

## 要做的事

### 1. 收集全部命中：`scripts/audit_collect.py`

- **范围**：`data/demos/5e_demo` 和 `data/demos/perfectworld_demo` 下的全部 demo，每场的**每一名真实玩家**都分析，不只是评测清单里的目标玩家。职业 demo 不在本任务范围内。
- 每场 demo 只解析一次，`Sight` 共用，对每名玩家调用 `find_moments` 和 `detect_problems`。
- 检测时**打开全部规则**，做法同 `problem_rates` 的校准；每条命中都要记下这条规则当前是否停用。
- **结果写到 `data/audit/<日期>/hits.jsonl`**，每条命中一行，包括：
  - `hit_key`：稳定的唯一编号，由 demo 文件名、成员、steamid、问题编号、target 拼成，重复运行时不变；
  - demo 路径、成员、地图、玩家 steamid 和名字；
  - 问题编号、是否停用、target、证据、阈值；
  - 渲染好的事实卡文字和程序依据句子（`evidence_sentence`）；
  - 跳转命令；
  - 可疑组合扫描的结果（第 3 步）。
- 23 场左右、每场 10 人，在完成报告里写明总耗时和命中总数。

### 2. 抽样核对清单：`scripts/audit_sample.py`

- 从 `hits.jsonl` 里，每类问题随机抽 `--per-problem` 条（默认 20），随机种子固定（`--seed`，默认 20261017），保证重复运行结果相同。
- **分层**：同一场 demo 最多抽 `--per-demo` 条（默认 3），避免某一场占满样本；不够时有多少抽多少。
- **可疑命中优先**：每类问题先把可疑组合扫描标出的命中全部放进样本，最多占一半，剩下的再随机抽，并在清单中注明哪些是"可疑优先"、哪些是"随机"。算准确率时，两部分**分开统计**，见第 4 步。
- **输出**：
  - `data/audit/<日期>/review/<问题编号>.md`：每条一节，包括 `hit_key`、事实卡、程序依据、证据 JSON、跳转命令、是否停用、可疑组合，以及留给人填的判定行；
  - `data/audit/<日期>/labels.toml`：每个抽中的 `hit_key` 一条，字段为 `verdict`（留空，可填 `correct` / `wrong` / `unsure`）、`reason`（留空）、`needs_ingame`（涉及"看见"的规则默认为 true）、`note`。
- **重跑时不能覆盖**已经填好的 `labels.toml`：已有的条目保留原样，只追加新条目。

### 3. 可疑组合扫描（在第 1 步收集时一并完成）

规则写进配置 `[audit]`，每条规则包括：编号、适用的问题、条件、说明。第一批：

| 编号 | 适用问题 | 条件 | 为什么可疑 |
|---|---|---|---|
| `fired_before_seen` | `slow_reaction`、`poor_crosshair_placement` | `shots_before_seen > 0` | 看见前就在开枪，"看见"的起点可能不准 |
| `negative_segment` | `slow_reaction` | `seen_to_first_shot_s < 0` | 分段为负，说明时间点有问题 |
| `blind_at_seen` | 依赖视野的全部规则 | `player_seen_while_blind is True` | 被闪白时不应该算看见 |
| `visibility_note_set` | 依赖视野的全部规则 | `visibility_note` 不为空 | 视野数据不完整却判了问题 |
| `contact_speed_borderline` | `peeked_into_held_angle`、`dry_peek_with_utility`、`overpeek_in_advantage` | 任一方的接触速度落在 `contact_still_speed` 或 `contact_moving_speed` 的 ±15% 以内 | 主动拉出、架点的判断贴着阈值 |
| `stop_borderline` | `shot_while_moving` | 第一枪的 `speed_ratio` 落在 `accurate_speed_ratio` 的 ±15% 以内 | "没停稳"贴着阈值 |
| `spray_mostly_unseen` | `spray_control` | `excluded_unseen_shots` 大于等于 `shots` | 026 之后不该出现，留作回归检查 |
| `crosshair_extreme` | `poor_crosshair_placement` | 角度大于 90° | 很可能是转身或对象搞错，不是预瞄问题 |

- "依赖视野的全部规则"列成配置里的问题清单，不要写死在代码里。
- `±15%` 等数值也放进配置。
- 扫描结果写进 `hits.jsonl`，并在 `data/audit/<日期>/suspicious.md` 汇总：每条规则命中多少条、占该问题全部命中的比例，再各列出前 10 条的 `hit_key` 和一句话说明。

### 4. 统计准确率：`scripts/audit_report.py`

- 读 `labels.toml` 和 `hits.jsonl`，输出 `data/audit/<日期>/precision.md`：每类问题一行，列出已判定数、正确、错误、不确定、准确率，以及 95% 置信区间。准确率不计"不确定"，置信区间用 Wilson 区间。
- **"随机抽样"和"可疑优先"两部分分开列**。只有随机部分能代表这条规则的真实准确率；可疑部分用来验证扫描规则准不准。
- 错误条目按 `reason` 汇总，便于看出同一类原因。
- 判定还没填完也能运行，只统计已经填了的条目。

## 接口与数据

- 三个脚本都放在 `scripts/` 下，可以复用 `src/` 的函数，但**不要改动检测、特征、报告的行为**。
- 新增配置 `[audit]`：可疑组合规则、依赖视野的问题清单、贴阈值的比例。要在默认配置中声明并通过配置校验。
- 输出全部写在 `data/audit/` 下，已被 git 忽略，里面有真实玩家信息，**不要复制进仓库**。
- 测试 fixture 不能带真实信息，见 024。

## 验收标准

- [ ] 测试，用合成数据：
  - `hit_key` 稳定、唯一；
  - 每条可疑组合规则各有一个触发、一个不触发的用例；
  - 抽样：固定种子结果相同，同一场不超过上限，可疑部分不超过一半；
  - 重跑抽样时，已经填好的 `labels.toml` 条目不被覆盖；
  - 准确率计算：不计"不确定"、Wilson 区间、随机部分和可疑部分分开统计。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **真实数据**：
  - 跑完 `audit_collect.py` 和 `audit_sample.py`。
  - 在完成报告里贴出：每类问题的命中总数（标明是否停用）、`suspicious.md` 的汇总表、抽样清单的条数、总耗时。**不贴玩家名和 steamid**。
  - `spray_mostly_unseen` 应为 0，这是对 026 的回归检查。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改任何检测规则、阈值和停用清单。
- 不填写判定：由 Claude 和产品负责人完成。
- 不处理职业 demo。
- 不做网页界面。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
