# 008 问题频率对比，调整问题的启用状态

状态：待做（在 007 审查通过并提交之后再做）

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

- [ ] 测试：
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
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 重新生成 `pro_sample.json`，在完成报告中贴出 `problem_rates`。
- [ ] 真实 demo（**不加 `--ai`**）：5E Mirage，玩家 `76561199711881069`。完成报告中贴出 `_problems.md` 的频率对比表。

## 范围外（不要做）

- 不改 AI 提示词和调用流程（任务 009）。
- 不改各问题的检测条件和阈值，只改启用状态。
- 不改 007 的检查器。检查器读取 `disabled` 和 `CONTEXT_ONLY`，会自动生效；如果没有生效，写进遗留问题。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
