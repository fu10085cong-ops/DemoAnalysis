# 005 问题检测：实现问题目录的前提条件

状态：待做（**依赖 004**：需要 `weapon_threshold`、`spray_segments` 和 v2 基准文件）

## 目标

把 `docs/problem_catalog.md` 中的问题实现成程序：对每个瞬间判断满足哪些问题的前提条件，并记下判断用到的数据和阈值。

后续结构化报告中，AI 只能从这里检测出的问题里挑选、判断轻重，检查器也用这里的结果核对 AI 的结论（后续任务）。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/problem_catalog.md`：全文。本任务实现其中**没有〔待开发〕标记**的问题
- `src/cs2coach/features/models.py`：`Moment`、`Engagement`（含 `player_blind`、`opponent_blind`）、`Shot`、`DeathState`、`Trade`、`MatchMoments`、`MissedTrade`
- `src/cs2coach/baseline.py`：`weapon_threshold`、`spray_segments`（004 新增）
- `src/cs2coach/features/labels.py`：`TAG_LABELS`、`WEAPON_LABELS`
- `src/cs2coach/report/facts.py`：参考它如何输出 Markdown

## 产品负责人尚未审核目录，先按以下默认方案实现

- 枪法类阈值取职业同武器的 **p75**；
- `died_without_seeing` 实现但**默认停用**（视野有已知误判）；
- 训练方法先不做；
- `awp_moving_shot` 不单独实现，由通用的 `shot_while_moving` 覆盖（所有武器都适用）。

## 要做的事

1. **新建 `src/cs2coach/features/problems.py`**，包含：

   ```python
   class ProblemSpec(BaseModel):       # 一类问题
       id: str
       label: str                      # 中文名，与目录一致
       category: Literal["aim", "duel", "utility_team", "weapon", "highlight"]
       applies_to: list[Literal["kill", "death", "assist", "round", "match"]]
       reliability: Literal["high", "medium", "low"]

   class ProblemHit(BaseModel):        # 一次命中
       problem: str
       target: str                     # 瞬间编号（"R5-death"）、回合（"R5"）或 "match"
       weapon: str | None              # 交火武器（规范名）
       evidence: dict[str, float | int | str | bool | None]   # 判断用到的数据值
       thresholds: dict[str, float | int | str | None]        # 比较用的阈值及来源（如 "pro_p75"）

   class ProblemReport(BaseModel):
       hits: list[ProblemHit]
       disabled: list[str]             # 被配置停用的问题
       skipped: dict[str, int]         # 每类问题因缺少数据（如基准中没有该武器）而无法判断的次数
       baseline_source: str | None

   PROBLEMS: dict[str, ProblemSpec]    # 目录中的全部问题，包括待开发的（便于检查器认出编号）
   CONTEXT_ONLY: dict[str, str]        # 目录 1.4 节"只作背景"的内容 → 原因，给后续检查器用

   def engagement_weapon(m: Moment) -> str | None: ...   # 与 004 的交火武器认定完全一致，最好直接复用 004 的函数
   def detect_problems(mm: MatchMoments, baseline: dict | None, cfg: dict) -> ProblemReport: ...
   ```

2. **要实现的问题及前提条件**：都是必要条件，每条都要有正反两面的测试。跳过带 `after_round_end`、`teamkill`、`victim_is_bot`、`world_damage` 标签的瞬间。

   | 编号 | 适用 | 前提条件 |
   |---|---|---|
   | `slow_reaction` | 击杀、死亡 | `time_to_damage_s` 有值且小于 `[aim_stats].time_to_damage_max_seconds`，`visibility_note` 不是 `smoke`；TTD（毫秒）> 该武器的 `time_to_damage_ms` p75。死亡时还要求 `opponent_time_to_damage_s` 有值，且比自己短 |
   | `poor_crosshair_placement` | 击杀、死亡 | `crosshair_placement_deg` > 该武器的 p75 |
   | `shot_while_moving` | 死亡 | `first_shot_stopped is False` 且 `first_shot_hit is False` |
   | `peeked_into_held_angle` | 死亡 | `contact_type == "peek"` |
   | `dry_peek_with_utility` | 死亡 | `contact_type == "peek"`；`unused_grenades` 中有 `flashbang` 或 `smokegrenade`；`opponent_blind` 为 None |
   | `overpeek_in_advantage` | 死亡 | 有 `man_advantage` 标签；`contact_type == "peek"`；`trade.traded is False` |
   | `died_without_seeing` | 死亡 | 有 `died_without_seeing` 标签；不是 `wallbang`、不是 `through_smoke`；`death.blinded` 为 False。**默认停用** |
   | `turned_into_flash` | 死亡 | 优先看 `engagement.player_blind`：事件时仍被闪，且 `thrower_role` 为 `opponent` 或 `enemy`。没有 `player_blind` 时，退回看 `death.blinded` 为 True 且 `blinded_by_teammate is False` |
   | `missed_trade` | 回合 | `mm.missed_trades` 的每条记录，`target` 为 `R{round}` |
   | `post_plant_utility_unused` | 死亡 | `side == "T"`；`bomb_planted`；`unused_grenades` 中有 `molotov` 或 `incgrenade` |
   | `weapon_not_ready` | 死亡 | 有 `weapon_not_ready` 标签 |
   | `awp_unscoped_shot` | 死亡 | 交火武器为 `awp`；交火第一枪的 `Shot.scoped is False`；距离 > `awp_noscope_max_distance_m`（配置，暂定 5，注明"待用数据校准"） |
   | `deagle_moving_shot` | 死亡 | 交火武器为 `deagle`；`first_shot_stopped is False`（不要求没打中） |
   | `glock_long_range` | 死亡 | 交火武器为 `glock`；`distance` > 基准中 glock 的 `distance_m` p90 |
   | `spray_control`（单次交火） | 击杀、死亡 | 交火武器属于自动武器；本次交火中 `recoil_index ≥ 3` 的子弹 ≥ `spray_min_late_shots`（配置，默认 6）。这些子弹的命中率 < 职业期望命中率 − `spray_margin_pct`（配置，默认 15 个百分点）。**职业期望命中率** = 对每发子弹，取它所在发数段的职业命中率，再求平均 |
   | `spray_control`（整场） | 整场 | 对每把自动武器的每个发数段：本场的子弹数 ≥ `spray_min_segment_shots`（配置，默认 30），且命中率 < 职业该段命中率 − `spray_margin_pct`。`target` 为 `"match"`，`evidence` 记下武器、段、子弹数、命中率 |
   | `spray_while_moving` | 死亡 | 交火武器属于自动武器；`recoil_index ≥ 1` 的子弹 ≥ 4 发，其中 `stopped is False` 的比例 > `spray_moving_ratio`（配置，暂定 0.5，注明"待用数据校准"） |
   | `good_trade` | 击杀 | 有 `trade_kill` 标签 |
   | `flash_assist` | 助攻 | `assist.flash_assist` 为 True |
   | `won_duel_seen_first` | 击杀 | 有 `saw_opponent_first` 和 `shot_first` 标签 |

   **说明**：
   - 交火武器在基准里查不到（例如 SMG），或者需要的阈值缺失时，不要报错，计入 `skipped`。
   - `evidence` 要包含判断用到的**每一个**数据值，例如 `slow_reaction` 要包含 `ttd_ms`、`opponent_ttd_ms`、`weapon`；`thresholds` 要写明阈值的数值和来源（`"pro_p75"`、`"config"`）。这两项以后会显示给用户，也会交给检查器。

3. **配置**：在 `config/default.toml` 新增 `[problems]` 段：
   - `disabled = ["died_without_seeing"]`；
   - 上面提到的各个阈值；
   - 每个键都带中文注释。

4. **CLI**：`analyze` 额外写出 `<stem>_<sid>_problems.md`，按问题分组列出命中的瞬间编号、`evidence` 和阈值，末尾列出停用的问题和 `skipped` 统计。基准用 `load_baseline()`；没有基准时，只跑不需要基准的问题。

5. **文档**：`docs/features.md` 新增"问题检测"一节，说明与目录的对应关系；在 `docs/problem_catalog.md` 顶部的状态行补充"已实现的问题见 features/problems.py"。不要改目录的其他内容。

## 验收标准

- [ ] 新建 `tests/test_problems.py`：
  - **每个**问题至少一个命中、一个不命中的测试；边界值（恰好等于阈值时**不**命中）至少覆盖 `slow_reaction` 和 `spray_control`；
  - 基准中没有该武器时计入 `skipped`，不报错；
  - 停用的问题不出现在 `hits` 中，并出现在 `disabled` 中；
  - 带排除标签的瞬间不参与检测；
  - `evidence` 和 `thresholds` 中包含规定的键。
  - 可以在 `tests/helpers/` 中新建一个构造 `Moment` 的辅助函数，用关键字参数覆盖默认值。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 真实 demo 验证（**不加 `--ai`**）：
  1. 5E Mirage（`g173-n-20261014223216271995270_de_mirage.dem`，玩家 `76561199711881069`）：生成 `_problems.md`。完成报告中列出每个问题的命中次数，并贴出 3 条命中（包含 `evidence`）。
  2. **基准自检**：任选一场职业 demo（例如 `vitality-vs-mouz-m3-dust2.dem`）中的一名选手，统计 `slow_reaction` 和 `poor_crosshair_placement` 的命中数占可判断交火的比例。职业 demo 本身就是基准的一部分，这个比例理论上应该接近 25%。报告实际数值。

## 范围外（不要做）

- 不改 AI 提示词和调用流程，不做 `submit_report` 和检查器（后续任务）。
- 不实现〔待开发〕的问题。
- 不改指标的计算口径和事实卡。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
