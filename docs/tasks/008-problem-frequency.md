# 008 问题频率对比，调整问题的启用状态

状态：完成

## 目标

1. 路人每人每场平均命中 21 条问题，而且职业也经常命中，所以**单条命中不说明问题**。程序要计算这名玩家每类问题的发生频率，与职业对比，找出"明显比职业多"的问题，作为报告的重点。
2. 按产品负责人确认的审核结论，调整几类问题的启用状态。

依据见 `docs/problem_catalog.md` 文末的"实测频率与审核结论"。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/problem_catalog.md`：文末"实测频率与审核结论"、1.4 节
- `src/cs2coach/features/problems.py`：`detect_problems`、`ProblemReport`、`CONTEXT_ONLY`、`PROBLEMS`
- `scripts/build_baseline.py`、`src/cs2coach/baseline.py`：基准的生成和读取
- `src/cs2coach/report/problems.py`：`_problems.md` 的输出
- `config/default.toml`：`[problems]`、`[baseline]`

## 要做的事

### 1. 调整启用状态（只改配置和 `CONTEXT_ONLY`）

- `[problems].disabled` 默认值改为：`died_without_seeing`、`turned_into_flash`、`weapon_not_ready`、`spray_while_moving`、`awp_unscoped_shot`。
- 新增配置 `[problems].spray_match_enabled = false`，用来关闭整场的 `spray_control`；单次交火的 `spray_control` 保留。
- `CONTEXT_ONLY` 新增两项，原因按目录写：
  - `turned_into_flash`：路人并不比职业多，主要反映对手的道具水平；
  - `weapon_not_ready`：路人与职业一样多，多数是被迫的。
- 配置注释中写明每项停用的原因，以及"待用数据校准"。

### 2. 职业的问题频率写进基准

`build_baseline.py` 生成基准时增加一步：

1. 先算出按武器的统计（现有逻辑）；
2. 用这份**新算出的**基准，对每名职业选手每张图运行 `detect_problems`；
3. 统计每类问题的频率，写入 `pro_sample.json` 的新键 `problem_rates`：

```json
"problem_rates": {
  "slow_reaction": {"hits": 427, "rounds": 3090, "per_10_rounds": 1.38, "player_share_pct": 91.3}
}
```

- `per_10_rounds` = 10 × 命中次数 ÷ 回合数；
- `player_share_pct` = 至少命中一次的人次占比；
- 回合数取每人 `MatchMoments.rounds` 之和；
- 只统计 `target` 不是 `"match"` 的命中；
- 被停用的问题也照常统计，因为停用只影响报告，频率数据以后校准要用。

计算时**必须**使用第 1 步新算出的阈值，不能用旧文件里的阈值。

### 3. 频率对比：新函数 `problem_frequency`

放在 `features/problems.py`：

```python
class FrequencyRow(BaseModel):
    problem: str
    label: str
    hits: int                   # 本场命中次数
    per_10_rounds: float        # 本场频率
    pro_per_10_rounds: float | None
    ratio: float | None         # 本场频率 ÷ 职业频率；职业频率为 0 或缺失时为 None
    prominent: bool             # 突出问题：hits ≥ frequency_min_hits 且 ratio ≥ frequency_prominent_ratio

def problem_frequency(report: ProblemReport, mm: MatchMoments, baseline: dict | None,
                      cfg: dict) -> list[FrequencyRow]: ...
```

- 只列**启用的、非亮点**问题；本场 0 次的也要列出，`hits = 0`。
- 排序：`prominent` 的在前，各组内按 `ratio` 从大到小，`ratio` 为 None 的排在最后。
- 新配置：`[problems].frequency_min_hits = 2`、`frequency_prominent_ratio = 1.5`，带中文注释。
- 回合数用 `mm.rounds`。

### 4. 输出

`_problems.md` 开头新增"频率对比"表：问题、本场次数、每 10 回合（本场 / 职业）、倍数，突出问题加粗。原有的逐条明细保留在后面。

### 5. 重新生成基准

运行 `scripts/build_baseline.py`。完成报告中贴出 `problem_rates`，与目录的实测频率表对照，数值应该接近。如果有差异，说明原因。

## 验收标准

- [x] 测试：
  - `problem_frequency`：
    - 频率、倍数计算正确；
    - `prominent` 的两个条件都要满足，边界值（恰好等于阈值时**算**突出）要测；
    - 职业频率为 0 或缺失时 `ratio` 为 None，且不算突出；
    - 停用的问题和亮点不在列表中；
    - 0 次命中的问题也在列表中；
    - 排序规则正确。
  - 基准中 `problem_rates` 的计算：用手工构造的 `MatchMoments` 测试，不读 demo；覆盖 `match` 目标被排除、`player_share_pct`。
  - 默认配置下，5 个停用的问题不出现在 `detect_problems` 的 `hits` 中；`spray_match_enabled = false` 时没有 `target == "match"` 的命中。
  - `CONTEXT_ONLY` 包含新增的两项。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 重新生成 `pro_sample.json`，在完成报告中贴出 `problem_rates`。
- [x] 真实 demo（**不加 `--ai`**）：5E Mirage，玩家 `76561190000001002`。完成报告中贴出 `_problems.md` 的频率对比表。

## 范围外（不要做）

- 不改 AI 提示词和调用流程（任务 009）。
- 不改各问题的检测条件和阈值，只改启用状态。
- 不改 007 的检查器。检查器读取 `disabled` 和 `CONTEXT_ONLY`，会自动生效；如果没有生效，写进遗留问题。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `config/default.toml`：默认停用任务指定的五个编号，增加 `spray_match_enabled=false`、频率最少命中次数 2 和突出倍数 1.5，并注明原因及待校准项。
  - `features/problems.py`：将 turned_into_flash、weapon_not_ready 加入 CONTEXT_ONLY；整场压枪增加配置开关，单次交火保留。新增 FrequencyRow、problem_frequency，零命中也输出，突出判断采用包含边界的 ≥，按突出优先/倍数降序/缺失最后排序；未改检测条件或原阈值。
  - 按用户确认，仅实际实现的检测编号参与频率统计，新增 DETECTED_PROBLEMS 元数据，排除待开发项、仅登记项及 other。职业统计保留其中 19 类（含停用项及亮点），用户频率表只列当前启用的 11 类非亮点。
  - `baseline.py`：新增纯函数 problem_rates，用配置副本临时启用所有规则，不修改调用方配置；过滤 match 命中，按各人次 rounds 之和计频率，并按至少命中一次的人次计算占比。
  - `scripts/build_baseline.py`：先生成本次 weapons/spray，再把新基准对象传给 problem_rates；不读取旧文件的阈值。生成的 `baselines/pro_sample.json` 新增 problem_rates；原有字段的内容逐项与生成前比较，全部一致。
  - `report/problems.py`、`cli.py`：报告开头增加频率表，突出项加粗，原逐条证据和停用/skipped 明细保留。CLI 共用同一份已加载的基准完成检测与频率比较；渲染函数的频率参数可选，兼容已有调用。
  - `docs/features.md`：同步启用状态、频率口径、校准统计和输出说明。
  - `tests/test_problem_frequency.py`：新增 19 项测试，覆盖频率和倍数、两个突出条件及边界、缺基准/零频率、零命中、排序、match 排除、人次占比、不修改配置、真实检测的校准启用，以及脚本使用新阈值而非旧文件。
  - `tests/test_problems.py`：原规则测试显式启用待校准规则，继续验证原条件；默认状态另行测试。CLI 断言增加频率表。
  - `tests/test_checker.py`、`tests/test_submission.py`：更新历史合法样例的当前状态断言，验证 weapon_not_ready 被现有检查器自动判为背景/停用，并从当前合法 schema 样例中移除此项；历史 fixture 保持原样，007 检查器和提交格式代码未改。
- 测试结果：重新生成基准后运行 `.venv/Scripts/python.exe -m pytest -q`：**462 passed, 3 skipped**。新增专项与原基准测试合计 36 项通过。`git diff --check` 通过。未新增依赖。
- 真实 demo 验证：
  - 运行 `scripts/build_baseline.py`，使用清单中全部 15 份职业 demo；**成功 15、失败 0、合格选手 150 人次，总计 3,090 个选手回合**。覆盖率 0 的热身非选手照常跳过。完整日志：`data/output/pro_baseline_frequency.log`。
  - 新生成的 problem_rates 如下：

  ```json
  {
    "slow_reaction": {
      "hits": 425,
      "rounds": 3090,
      "per_10_rounds": 1.38,
      "player_share_pct": 91.3
    },
    "poor_crosshair_placement": {
      "hits": 501,
      "rounds": 3090,
      "per_10_rounds": 1.62,
      "player_share_pct": 94.7
    },
    "shot_while_moving": {
      "hits": 272,
      "rounds": 3090,
      "per_10_rounds": 0.88,
      "player_share_pct": 83.3
    },
    "peeked_into_held_angle": {
      "hits": 172,
      "rounds": 3090,
      "per_10_rounds": 0.56,
      "player_share_pct": 64.7
    },
    "dry_peek_with_utility": {
      "hits": 46,
      "rounds": 3090,
      "per_10_rounds": 0.15,
      "player_share_pct": 30.7
    },
    "overpeek_in_advantage": {
      "hits": 24,
      "rounds": 3090,
      "per_10_rounds": 0.08,
      "player_share_pct": 14.0
    },
    "died_without_seeing": {
      "hits": 206,
      "rounds": 3090,
      "per_10_rounds": 0.67,
      "player_share_pct": 79.3
    },
    "turned_into_flash": {
      "hits": 126,
      "rounds": 3090,
      "per_10_rounds": 0.41,
      "player_share_pct": 52.0
    },
    "missed_trade": {
      "hits": 250,
      "rounds": 3090,
      "per_10_rounds": 0.81,
      "player_share_pct": 78.0
    },
    "post_plant_utility_unused": {
      "hits": 7,
      "rounds": 3090,
      "per_10_rounds": 0.02,
      "player_share_pct": 4.7
    },
    "weapon_not_ready": {
      "hits": 105,
      "rounds": 3090,
      "per_10_rounds": 0.34,
      "player_share_pct": 40.7
    },
    "awp_unscoped_shot": {
      "hits": 1,
      "rounds": 3090,
      "per_10_rounds": 0.0,
      "player_share_pct": 0.7
    },
    "deagle_moving_shot": {
      "hits": 7,
      "rounds": 3090,
      "per_10_rounds": 0.02,
      "player_share_pct": 4.7
    },
    "glock_long_range": {
      "hits": 16,
      "rounds": 3090,
      "per_10_rounds": 0.05,
      "player_share_pct": 10.0
    },
    "spray_control": {
      "hits": 62,
      "rounds": 3090,
      "per_10_rounds": 0.2,
      "player_share_pct": 33.3
    },
    "spray_while_moving": {
      "hits": 4,
      "rounds": 3090,
      "per_10_rounds": 0.01,
      "player_share_pct": 2.7
    },
    "good_trade": {
      "hits": 312,
      "rounds": 3090,
      "per_10_rounds": 1.01,
      "player_share_pct": 86.7
    },
    "flash_assist": {
      "hits": 115,
      "rounds": 3090,
      "per_10_rounds": 0.37,
      "player_share_pct": 51.3
    },
    "won_duel_seen_first": {
      "hits": 224,
      "rounds": 3090,
      "per_10_rounds": 0.72,
      "player_share_pct": 74.0
    }
  }
  ```

  - 与目录实测表对照：压枪差 0.20、出枪慢 1.38、人数优势时冒进 0.08、身上有道具却直接拉出 0.15、准星没提前放好 1.62、开枪时没停稳 0.88、漏补枪 0.81、主动拉出撞上架点 0.56、拉栓期间交火 0.34、被对手闪中后被击杀 0.41；**十项每十回合频率均与目录的两位小数值相同**，人次占比按目录整数显示也吻合。未改目录数字。
  - 运行 `python -m cs2coach analyze .../g173-n-20261014223216271995270_de_mirage.dem --player 76561190000001002`，不加 `--ai`，正常输出 28 回合、37 个瞬间及事实卡和问题报告。报告路径：`data/output/g173-n-20261014223216271995270_de_mirage_76561190000001002_problems.md`，频率表原文如下：

  | 问题 | 本场次数 | 每 10 回合（本场 / 职业） | 倍数 |
  |---|---:|---:|---:|
  | **出枪慢** | 7 | 2.50 / 1.38 | 1.81× |
  | **准星没提前放好** | 7 | 2.50 / 1.62 | 1.54× |
  | 压枪差 | 1 | 0.36 / 0.20 | 1.79× |
  | 漏补枪 | 2 | 0.71 / 0.81 | 0.88× |
  | 开枪时没停稳 | 1 | 0.36 / 0.88 | 0.41× |
  | 主动拉出撞上对手架点 | 0 | 0.00 / 0.56 | 0.00× |
  | 身上有道具却直接拉出 | 0 | 0.00 / 0.15 | 0.00× |
  | 人数优势时冒进 | 0 | 0.00 / 0.08 | 0.00× |
  | 下包后带着火没用 | 0 | 0.00 / 0.02 | 0.00× |
  | 沙鹰移动开枪 | 0 | 0.00 / 0.02 | 0.00× |
  | 用 Glock 远距离对枪 | 0 | 0.00 / 0.05 | 0.00× |

  - 本场突出项为出枪慢、准星没提前放好，各 7 次；压枪差虽为职业的 1.79 倍，但只有 1 次，没有达到最少 2 次的条件，因此未加粗。
- 遗留问题 / 需要确认的地方：
  - 用户已明确选择“只统计实际已实现的检测编号”，因此没有为待开发项及 other 写入零职业频率或显示比较行。
  - 007 检查器和 schema 读取现有 disabled / CONTEXT_ONLY 后自动生效，未发现需要改检查器的地方。旧样例中的 weapon_not_ready 已不再是当前可提交问题，测试记录了这一预期变化。
  - 频率是命中次数除以回合数，没有按可判断交火数校正；缺少职业频率或职业为 0 时不判为突出。基准频率保留两位小数、占比一位小数；例如职业 AWP 未开镜有 1 次，显示频率为 0.00，原始 hits 仍完整保留，该规则默认停用。
  - 五类停用项、整场压枪及突出阈值仍按配置说明等待校准。5E demo 中其他两名玩家原有的约 93%/94% 覆盖率警告仍保留。
  - 未改 AI 提示词、调用流程、007 检查器、原指标和事实卡代码；未调用真实 AI，demo 原文件只读，未提交。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 职业频率与目录实测表 10 项完全一致；计算频率时使用新阈值、临时启用全部规则，不修改原配置；突出问题的两个条件和边界正确。462 个测试通过。
