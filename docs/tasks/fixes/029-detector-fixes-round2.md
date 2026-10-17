# 029 审计第二轮的修正：按武器设移动开枪阈值、看见时隔烟、压枪距离上限

状态：待做
执行机器：Mac，分支 `task/029-detector-fixes-round2`

## 目标

027 之后做了第二轮审计（`docs/eval_log.md` 的"检测规则审计第二轮"），又发现几处可以修的问题。最大的一处来自一次数据统计：

**移动开枪对命中率的影响因武器差别很大**。统计范围：24 场业余比赛、30 米以内、排除隔烟，看见对手后的第一枪，2026-10-17：

| 武器 | 停稳（速度比 < 0.34） | 0.34–0.45 | 0.45–0.70 | > 0.70 |
|---|---:|---:|---:|---:|
| AK-47 | 41% | **27%** | 18% | 10% |
| M4A4 | 46% | 42% | **22%** | 18% |
| M4A1-S | 39% | 35% | **19%** | 16% |
| 沙鹰 | 50% | 56% | **37%** | 25% |
| Glock / USP-S | 35% | 28–33% | 35% | 32–35% |
| MAC-10 / MP9 | 21–36% | 24–29% | 32–37% | 19–27% |

- AK 稍微没停稳就明显变差；
- M4、沙鹰要到 0.45 以上才明显变差；
- **手枪和冲锋枪移动开枪并不比停稳差**。

可是"开枪时没停稳"现在对所有武器都用同一个阈值 0.374，所以第二轮按这个口径重判后只有 40% 准确：20 条里有 9 条是手枪或冲锋枪，3 条是 M4 在 0.38 左右。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`、`docs/tasks/README.md`
- `docs/eval_log.md`："检测规则审计第二轮（027 之后）"一节
- `docs/tasks/fixes/027-detector-exclusions-after-audit.md`：排除条件的配置方式 `[problems.exclusions]`、`max_shots_before_seen`、`moving_shot_margin`
- `src/cs2coach/features/problems.py`：`shot_while_moving`、`deagle_moving_shot`、`spray_control`、`slow_reaction` 的判断
- `src/cs2coach/features/models.py`：`Engagement.visibility_note`、`first_seen_shot_*`、`Shot.speed_ratio`、`Shot.seen_opponent`、`Context.distance`
- `scripts/audit_collect.py`：遍历业余 demo、每场只解析一次的写法，校准脚本照着写
- `data/audit/2026-10-17-after027/labels.toml`：第二轮判定，只读

## 要做的事

### 1. 移动开枪按武器设阈值

- **新增配置** `[problems.moving_shot_thresholds]`：武器 → 速度比阈值。速度比**不低于**阈值才判为"没停稳"。默认值按上表：
  - `ak47 = 0.374`、`galilar = 0.374`。Galil 样本少，按和 AK 同类处理，注释里写明待校准。
  - `m4a1 = 0.45`、`m4a1_silencer = 0.45`、`famas = 0.45`、`aug = 0.45`、`sg556 = 0.45`。后三把样本少，注释里写明待校准。
  - `deagle = 0.45`
  - **不在表里的武器（手枪、冲锋枪、霰弹枪）不判。**
- `shot_while_moving` 和 `deagle_moving_shot` 都改用这张表，取代 027 的 `moving_shot_margin`。`moving_shot_margin` 删掉，配置校验同步更新。
- 程序依据写成"看见对手后的第一枪速度比 X，不低于 Y 的阈值 Z"，其中 Y 是武器显示名。

### 2. 速度校准工具 `scripts/calibrate_moving_shots.py`

- 按 `audit_collect.py` 的方式遍历 demo，统计"看见对手后的第一枪"：
  - 排除隔烟：击杀或死亡穿烟，或者 `visibility_note == "smoke"`；
  - 排除蹲下和空中的射击；
  - 距离上限用参数 `--max-distance`，默认 30 米。
- 按武器、速度比分段输出命中率、样本数和 Wilson 95% 区间。分段边界用参数 `--edges`，默认 `0,0.34,0.45,0.70,1.01`。
- 参数 `--demos` 选择 `amateur`（默认，即 5E 和完美）、`pro` 或 `all`。
- 输出 Markdown 到终端，或者用 `--out` 写成文件。
- **用这个工具重现上面的表格**，并对职业 demo（`--demos pro`）跑一遍，结果贴进完成报告。如果职业数据和业余数据的拐点明显不同，在完成报告里说明，**不要自己改默认阈值**。

### 3. 出枪慢：看见前开过一枪就不判

`problems.max_shots_before_seen` 从 2 改成 1。第二轮中，看见前开过 1 枪的 10 条：5 条 wrong（看见后几十毫秒就开枪，慢在命中，或者是 AWP 受拉栓限制），5 条 unsure。

### 4. 新排除条件：看见时隔烟

- 在 `[problems.exclusions]` 中新增条件 `first_seen_through_smoke`，判断为 `Engagement.visibility_note == "smoke"`；
- 默认适用于：压枪差、扫射时移动、先发现先开枪。
- 第二轮中有 8 条压枪差 wrong 属于这种情况：027 只排除了击杀本身穿烟，没有排除一开始就隔着烟看到的情况。

### 5. 压枪差：距离上限

- 新增 `problems.spray_max_distance_m = 30`，超过这个距离的交火不判压枪差；
- 30 米以上的长扫属于问题目录里待开发的"远距离长扫"，本任务不做那条规则；
- 第二轮中有 2 条 wrong，分别是 32 米和 33 米。

### 6. 沙鹰移动开枪：加上交火中被闪

把 `deagle_moving_shot` 加进 `player_blinded_in_fight` 的适用清单。第二轮中有 2 条 wrong。

### 7. 重建职业基准、用第二轮判定验证

- `scripts/build_baseline.py` 重建，完成报告列出职业频率的前后对比；
- `audit_collect.py --name 2026-10-17-after029`，然后 `audit_report.py --name 2026-10-17-after029 --labels-from 2026-10-17-after027`；
- **预期**：
  - 第二轮判为 wrong 的条目中，原因是"口径不符"（手枪、冲锋枪）、"贴阈值"（M4 在 0.38 左右）、"首次看见隔烟"、"远距离扫射"、"交火中被闪"的，以及出枪慢里"看见前已开枪"的，应该大部分被移除。
  - 列出被移除的 correct。出枪慢里有 5 条 unsure 也会被移除，这是预期的。误删的 correct 超过 3 条时，停下来写进完成报告。

## 接口与数据

- 新增配置：`[problems.moving_shot_thresholds]`、`problems.spray_max_distance_m`，排除条件 `first_seen_through_smoke`。删除 `problems.moving_shot_margin`。全部通过配置校验。
- 新增 `scripts/calibrate_moving_shots.py`。
- `docs/features.md`、`docs/problem_catalog.md` 同步：移动开枪按武器的阈值和依据，以及压枪差的距离上限。

## 验收标准

- [ ] 测试，用合成数据：
  - 按武器的阈值：AK 0.38 判、M4 0.38 不判、M4 0.50 判、Glock 0.90 不判、沙鹰 0.40 不判、沙鹰 0.50 判；
  - `first_seen_through_smoke` 排除，并计数；
  - 压枪差 31 米不判、29 米判；
  - 看见前开过 1 枪时，出枪慢不判；
  - 沙鹰移动开枪在交火中被闪时排除；
  - 校准工具的分段、Wilson 区间、距离和隔烟过滤。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] 真实数据：校准工具重现上表（业余）并跑一遍职业 demo；第 7 步的审计对比；职业基准重建。完成报告里**玩家名一律替换成"（已匿名）"**，只用 `hit_key` 和统计数字。
- [ ] 不调用 DeepSeek、Claude 或本地模型；不修改 `data/audit/2026-10-17*/labels.toml`。

## 范围外（不要做）

- 不做"远距离长扫"规则。
- 不改"准星没提前放好"和"漏补枪"的算法，留给 S03。
- 不改停用清单。
- 不改 AI 提示词和检查器。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
