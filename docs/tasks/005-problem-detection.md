# 005 问题检测：实现问题目录的前提条件

状态：完成

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

- [x] 新建 `tests/test_problems.py`：
  - **每个**问题至少一个命中、一个不命中的测试；边界值（恰好等于阈值时**不**命中）至少覆盖 `slow_reaction` 和 `spray_control`；
  - 基准中没有该武器时计入 `skipped`，不报错；
  - 停用的问题不出现在 `hits` 中，并出现在 `disabled` 中；
  - 带排除标签的瞬间不参与检测；
  - `evidence` 和 `thresholds` 中包含规定的键。
  - 可以在 `tests/helpers/` 中新建一个构造 `Moment` 的辅助函数，用关键字参数覆盖默认值。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实 demo 验证（**不加 `--ai`**）：
  1. 5E Mirage（`g173-n-20261014223216271995270_de_mirage.dem`，玩家 `76561199711881069`）：生成 `_problems.md`。完成报告中列出每个问题的命中次数，并贴出 3 条命中（包含 `evidence`）。
  2. **基准自检**：任选一场职业 demo（例如 `vitality-vs-mouz-m3-dust2.dem`）中的一名选手，统计 `slow_reaction` 和 `poor_crosshair_placement` 的命中数占可判断交火的比例。职业 demo 本身就是基准的一部分，这个比例理论上应该接近 25%。报告实际数值。

## 范围外（不要做）

- 不改 AI 提示词和调用流程，不做 `submit_report` 和检查器（后续任务）。
- 不实现〔待开发〕的问题。
- 不改指标的计算口径和事实卡。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `features/problems.py`：新增 ProblemSpec / ProblemHit / ProblemReport，登记目录全部 25 个编号和 1.4 节背景项；实现明确检测表的 19 个编号、20 种检测（压枪含交火和整场）。交火武器直接复用 004 函数，不重复定义口径。
  - 枪法阈值按武器读取 p75，Glock 距离取 p90；无基准仍跑通用规则，缺少数据/阈值计入 skipped。默认停用 died_without_seeing，排除四类无效瞬间；致盲优先看 BlindInfo，未知投掷归属不猜。
  - 单次压枪按逐发所处分段求职业期望，只比较交火武器的子弹；整场复用 spray_stats，按实际武器与分段比较。证据完整保存所用子弹序号、命中/停稳序列、道具列表及阈值；列表以 JSON 字符串保存在标量字典里。
  - `config/default.toml`：新增 problems 段，所有阈值和停用列表均可严格配置覆盖，AWP 距离与移动比例注明待数据校准。
  - `report/problems.py` 与 `cli.py`：无需 AI 即生成按问题分组的 `_problems.md`，包含证据、阈值、职业来源、停用和 skipped。未改变 AI 调用流程。
  - `tests/test_problems.py`：新增 70 项测试，覆盖每条规则正反条件、严格边界、缺基准/缺武器/缺分段、停用与排除、致盲优先级、混合武器、配置覆盖、证据与 CLI 无 AI 输出。复用 004 的 `tests/helpers/moments.py` 及 synthetic 数据。
  - `docs/features.md` 说明检测口径；`docs/problem_catalog.md` 只补充顶部状态行。未新增依赖。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**362 passed, 3 skipped**（未设置真实 demo 测试环境变量）。`git diff --check` 通过。
- 真实 demo 验证：
  - 两场均运行 `python -m cs2coach analyze ... --player ...`，不加 `--ai`；JSON、事实卡、问题报告均成功生成，demo 原文件只读。最终补充缺少数据计数后，使用这些真实解析结果重新生成问题报告，命中数不变。核对统计保存于 `data/output/problem_validation.json`。
  - 5E Mirage：`g173-n-20261014223216271995270_de_mirage.dem`，玩家 `76561199711881069`，37 个瞬间，共 **24 条命中**。输出 `data/output/g173-n-20261014223216271995270_de_mirage_76561199711881069_problems.md`。

  | 问题编号 | 命中次数 |
  |---|---:|
  | slow_reaction | 7 |
  | poor_crosshair_placement | 7 |
  | shot_while_moving | 1 |
  | peeked_into_held_angle | 0 |
  | dry_peek_with_utility | 0 |
  | overpeek_in_advantage | 0 |
  | died_without_seeing（默认停用） | 0 |
  | turned_into_flash | 1 |
  | missed_trade | 2 |
  | post_plant_utility_unused | 0 |
  | weapon_not_ready | 3 |
  | awp_unscoped_shot | 0 |
  | deagle_moving_shot | 0 |
  | glock_long_range | 0 |
  | spray_control（单次交火 / 整场） | 1 / 0 |
  | spray_while_moving | 0 |
  | good_trade | 0 |
  | flash_assist | 0 |
  | won_duel_seen_first | 2 |

  仅登记的 overspray_at_range、awp_moving_shot、awp_repeek_same_angle、awp_close_range_duel、pistol_spam、other 均为 0。skipped：slow_reaction 14、poor_crosshair_placement 18、peeked_into_held_angle 5、dry_peek_with_utility 5、overpeek_in_advantage 1。

  三条实际命中（保留完整 evidence 与 thresholds）：

  ```json
  [
    {
      "problem": "slow_reaction", "target": "R3-kill1", "weapon": "m4a1_silencer",
      "evidence": {"weapon": "m4a1_silencer", "round": 3, "tick": 26836, "kind": "kill", "ttd_ms": 812.0, "opponent_ttd_ms": null, "visibility_note": null, "player_seen_while_blind": false},
      "thresholds": {"ttd_ms": 484.0, "source": "pro_p75", "ttd_max_seconds": 1.0, "ttd_max_source": "config"}
    },
    {
      "problem": "shot_while_moving", "target": "R1-death", "weapon": "usp_silencer",
      "evidence": {"weapon": "usp_silencer", "round": 1, "tick": 14820, "kind": "death", "first_shot_stopped": false, "first_shot_hit": false},
      "thresholds": {}
    },
    {
      "problem": "weapon_not_ready", "target": "R9-death", "weapon": "awp",
      "evidence": {"weapon": "awp", "round": 9, "tick": 59661, "kind": "death", "weapon_not_ready": true},
      "thresholds": {}
    }
  ]
  ```

  - 职业自检：`vitality-vs-mouz-m3-dust2.dem`，ZywOo `76561198113666193`，40 个瞬间。出枪慢 **4/15 = 26.7%**；准星没提前放好 **6/17 = 35.3%**。可判断分母分别要求有效指标、同武器阈值；TTD 另排除烟雾/被闪白和 ≥1秒，死亡要求对手 TTD 有值。分母没有提前筛掉“对手比自己快”为假的死亡，避免夸大命中率。
  - 职业 25% 是同武器合并分布高于 p75 的近似比例，不能要求单名选手单场严格等于 25%。该场分母仅 15/17，且 TTD 死亡有额外判定条件；如实记录准星 35.3%，未调阈值迎合预期。
- 遗留问题 / 需要确认的地方：
  - 用户明确选择“按 005 检测表实现”，因此 awp_close_range_duel 仅登记，不猜近距离阈值；awp_moving_shot 由通用 shot_while_moving 覆盖，未做独立命中。目录 AWP 未开镜适用栏包含击杀，但本任务表仅列死亡，实现遵循任务表，未改目录正文。
  - died_without_seeing 的模型部分可见误判仍在，默认停用；AWP 5 米、移动扫射 0.5 等阈值仍待校准。单次交火压枪噪声大，职业末段样本少，命中只是必要条件。
  - 未实现目录待开发项、训练、AI 选取与检查器；未改变原指标或事实卡。未调用真实 AI，未提交。

## 审查意见（Claude 填写）

**审查通过（2026-10-15）。** 规则和任务单一致，缺数据时计入 skipped，不去推断，`evidence` 完整。ZywOo 的自检结果（26.7% / 35.3%）在单场样本量下合理。

目录与任务单不一致的地方（`awp_unscoped_shot` 只适用于死亡）已由 Claude 更新目录。阈值的合理性等产品负责人看过实际命中结果后再回头审核。
