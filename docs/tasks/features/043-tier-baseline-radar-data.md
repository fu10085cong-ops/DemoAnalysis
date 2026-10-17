# 043 分段基准与报告页数据（雷达、做得好的方向、问题汇总、高光回合）

状态：待做
难度：A（新数据结构，跨基准、特征、汇总、CLI 多个模块）
执行：Codex（思考强度 high）
分支：`task/043-tier-radar-data`

## 目标

为新版报告页（044 渲染成 HTML）准备全部数据，由程序确定性地算出，写成 `report_data.json`：

- 雷达图：6 个维度，数值是"在目标分段里排到百分之几"；
- 做得好的方向、问题汇总：每项附具体回合和游戏内跳转命令；
- 高光回合：多杀、残局获胜、首杀并赢下回合、闪光助攻击杀。

目标分段由用户选（`--target`），每个分段有自己的基准文件。产品负责人会按分段收集大量 demo；在那之前，先用现有 24 场业余 demo 做一个临时分段。

版式草图：https://claude.ai/artifact/2SnHFwR2sf2eEiWnXgdLZ7（示意数据，只看结构）。

## 已验证的事实（不要重新验证）

- **demo 里读不到段位**：5E 和完美 demo 的 `rank` 列所有人都是 0（2026-10-18 Claude 实测）。所以分段只能按文件夹划分。
- `Round.winner`（`parsing/models.py::Round`）是每回合的胜方，类型是 `Team`。
- `Moment.tags` 里已经有 `opening_kill`、`clutch_1vN`（N 为对手存活数）、`flash_assist`、`teamkill`、`after_round_end`（`features/moments.py` 约 583–602 行）。
- `MatchMoments.lobby` 只在 `analyze` 中填写（`cli.py::_analyze` 调用 `lobby_lines`）。多场汇总的 `build_summary` 只算目标玩家的 `find_moments`，`lobby` 为空。

## 背景（必须读）

- `src/cs2coach/baseline.py`：`build_baseline`、`compare`（百分位口径照抄它的 `better_than_pro_pct`，并考虑 `LOWER_IS_BETTER` 的方向）、`problem_rates`。
- `scripts/build_baseline.py::main`：分段基准的构建流程照它写。
- `src/cs2coach/features/lobby.py::lobby_lines`：同场每人的统计。
- `src/cs2coach/features/models.py`：`MatchMoments`、`LobbyLine`、`AimStats`、`Context`。
- `src/cs2coach/summary/aggregate.py::build_summary`、`PlayerSummary`。
- `src/cs2coach/report/basic.py::choose_problems`、`problem_texts`：问题的中文名和固定说明从这里取。
- `config/default.toml` 的 `[review]`：跳转命令模板（`playdemo_template`、`jump_template`）。

## 要做的事

### 1. 分段基准

- 新增 `config/tiers.toml`，每个分段一项：
  ```toml
  [[tier]]
  id = "amateur_tmp"
  name = "业余（临时）"
  dirs = ["data/demos/5e_demo", "data/demos/perfectworld_demo"]   # 递归找 demo，支持现有全部格式
  note = "现有 24 场业余 demo，正式分段收集齐之前临时使用"

  [[tier]]
  id = "pro"
  name = "职业"
  manifest = "src/cs2coach/baselines/pro_demos.toml"   # 职业沿用现有清单
  ```
  以后的分段由产品负责人放到 `data/demos/tiers/<id>/`，再在这里加一项。
- 新增 `scripts/build_tier_baseline.py --tier <id>`（`--all` 构建全部分段），输出 `src/cs2coach/baselines/tiers/<id>.json`：
  ```json
  {"version": 1, "id": "amateur_tmp", "name": "业余（临时）", "source": "24 份 demo、238 人次",
   "metrics": {"<metric>": {"median": 0, "n": 0, "values": [/* 每人每场一个值，升序 */]}},
   "problem_rates": {/* 与 pro_sample.json 的 problem_rates 同格式 */}}
  ```
  - **不能写入 steamid、玩家名、demo 文件名**：这些文件要进仓库，提交钩子会扫描。
  - 每场所有覆盖率合格的玩家都计入，跳过规则和 `build_baseline.py` 相同。
  - `problem_rates` 用现有 `baseline.problem_rates(mms, pro_baseline, cfg)` 计算。**检测阈值始终用职业基准**（规则是按职业阈值审计过的），分段文件只提供对比用的分布和频率。
- 分段基准不参与问题检测，也**不改变** `pro_sample.json` 和任何检测结果。

### 2. 每人每场的雷达指标

新增 `src/cs2coach/features/radar.py`：

```python
RADAR_METRICS: dict[str, bool]  # 指标名 → 是否越小越好

def player_metrics(line: LobbyLine) -> dict[str, float | None]
def percentile(metric: str, value: float | None, tier: dict) -> float | None   # 0–100，口径同 baseline.compare
def axis_values(metrics: dict[str, float | None], tier: dict, axes: list[dict]) -> list[float | None]
```

指标（数据不足时为 None）：

| 指标 | 计算 | 方向 |
|---|---|---|
| `time_to_damage_ms` | `line.aim.time_to_damage_ms` | 越小越好 |
| `crosshair_placement_deg` | `line.aim.crosshair_placement_deg` | 越小越好 |
| `counter_strafing_pct` | `line.aim.counter_strafing_pct` | 越大越好 |
| `spray_accuracy_pct` | `line.aim.spray_accuracy_pct` | 越大越好 |
| `trade_kills_per_round` | `trade_kills / rounds_played` | 越大越好 |
| `traded_death_pct` | `100 * traded_deaths / deaths`（deaths 为 0 时 None） | 越大越好 |
| `utility_damage_per_round` | `utility_damage / rounds_played` | 越大越好 |
| `enemies_flashed_per_flash` | 原字段 | 越大越好 |
| `wasted_utility_avg` | 原字段 | 越小越好 |

- `LobbyLine` 新增 `rounds_played: int = 0`，在 `lobby_lines` 里填该玩家 `MatchMoments.rounds_played`。
- 维度的数值 = 它所含指标百分位的平均；全部为 None 时，这个维度为 None。

维度写在 `config/default.toml` 里，不写死在代码中：

```toml
[radar]
default_tier = "amateur_tmp"
strength_min_percentile = 65   # 做得好的方向：指标百分位至少这么高
max_strengths = 3
max_problems = 5
max_examples = 5               # 每项最多列几个具体回合
best_player_rule = "kills_minus_deaths"   # 本场最佳：击杀减死亡最大，相同时击杀多者；仍相同按 steamid

[[radar.axes]]
id = "ttd"
name = "出枪速度"
metrics = ["time_to_damage_ms"]
[[radar.axes]]
id = "crosshair"
name = "准星位置"
metrics = ["crosshair_placement_deg"]
[[radar.axes]]
id = "strafe"
name = "急停"
metrics = ["counter_strafing_pct"]
[[radar.axes]]
id = "spray"
name = "压枪"
metrics = ["spray_accuracy_pct"]
[[radar.axes]]
id = "trade"
name = "补枪配合"
metrics = ["trade_kills_per_round", "traded_death_pct"]
[[radar.axes]]
id = "utility"
name = "道具使用"
metrics = ["utility_damage_per_round", "enemies_flashed_per_flash", "wasted_utility_avg"]
```

### 3. 多场汇总也要有目标玩家的 LobbyLine

- 把 `lobby_lines` 里计算单个玩家的部分拆成 `lobby_line(match, sid, cfg, mm, ...) -> LobbyLine`，`lobby_lines` 改为循环调用它，输出不变。
- `build_summary` 的 `analyse()` 中，算完 `mm` 后补上 `mm.lobby = [lobby_line(match, steamid, cfg, mm, ...)]`。缓存键包含 features 代码哈希，旧缓存会自动失效。
- 多场汇总的雷达：每场算一次目标玩家的维度值，再对每个维度**取各场的中位数**（跳过 None）。

### 4. 高光回合

新增 `src/cs2coach/features/highlights.py::find_highlights(mm, rounds) -> list[Highlight]`。在 `find_moments` 末尾（或 `_analyze` / `analyse()` 里能拿到 `match.rounds` 的地方）算好，存入 `MatchMoments.highlights: list[Highlight] = []`。

计算时排除带 `teamkill`、`after_round_end`、`victim_is_bot` 标签的击杀。四类：

| kind | 条件 | 标题示例 |
|---|---|---|
| `multikill` | 同一回合目标击杀 ≥ 3 | `R16 四杀`（3/4/5 杀写成 三杀、四杀、五杀） |
| `clutch` | 该回合目标某次击杀带 `clutch_1vN` 标签，N ≥ 2，且目标一方赢下回合；N 取该回合第一次出现的值 | `R23 1v3 残局获胜` |
| `opening_win` | 该回合目标的击杀带 `opening_kill`，且目标一方赢下回合 | `R2 首杀并赢下` |
| `flash_assist` | 目标作为闪光助攻者的瞬间（`flash_assist` 标签） | `R27 闪光助攻` |

- 同一回合满足多类时，只保留排在最前的一类，顺序为 `clutch` > `multikill` > `opening_win` > `flash_assist`。
- 每条记录：`round`、`kind`、`title`、`time`（回合内时间，格式 `m:ss`）、`what`（一句话，见下）、`tick`（跳转 tick，用该回合第一个相关瞬间的 `jump_tick`）。
- `what` 由程序按模板拼写，例如 `"用 AK-47 击杀 3 人，其中 2 次爆头"`、`"1 打 3，击杀 3 人后赢下回合"`、`"首杀对手 甲，全队赢下回合"`、`"队友 乙 在对手被闪时击杀"`。名字取 `mm.player_names`。

### 5. 报告页数据

新增 `src/cs2coach/report/page_data.py`，定义 pydantic 模型并生成数据：

```python
class Command(BaseModel): playdemo: str; jump: str             # 用 [review] 模板生成
class Example(BaseModel):
    match_label: str     # 单场为 "本场"；汇总为 "Dust2 10-12"（地图 + 月-日）
    round: int; time: str; situation: str; what: str; tick: int; command: Command
class Row(BaseModel):      # 做得好的方向、问题汇总共用
    id: str; name: str; axis: str | None
    you: float; target: float | None; unit: str   # 问题：每 10 回合次数；做得好：指标值
    count: int; severity: Literal["bad", "warn", "ok"]
    text: str            # 问题：取 problem_text.toml 的第一句；做得好：程序模板句
    examples: list[Example]
class Highlight(BaseModel): kind: str; title: str; match_label: str; round: int; time: str; what: str; tick: int; command: Command
class Comparison(BaseModel): id: str; name: str; values: list[float | None]
class TierView(BaseModel):
    id: str; name: str; source: str
    you: list[float | None]
    comparisons: list[Comparison]
    problem_targets: dict[str, float]      # 问题 id → 该分段每 10 回合次数
class ReportPage(BaseModel):
    kind: Literal["match", "summary"]
    title: str; subtitle: str; verdict: str
    axes: list[dict]                       # [{"id": "ttd", "name": "出枪速度"}, ...]
    default_tier: str
    problem_bad_ratio: float               # 来自 [radar]；044 的页面切换分段时用它重算 severity
    tiers: list[TierView]                  # 所有已构建的分段，雷达随下拉切换
    strengths: list[Row]; problems: list[Row]; highlights: list[Highlight]

def build_match_page(mm: MatchMoments, report: ProblemReport, frequency, cfg, tiers: dict[str, dict]) -> ReportPage
def build_summary_page(summary: PlayerSummary, cfg, tiers: dict[str, dict]) -> ReportPage
def load_tiers(cfg) -> dict[str, dict]     # 读 baselines/tiers/*.json；一个都没有时返回空 dict
```

- **对比线**：
  - 单场：`match_avg`（本场平均：同场每名玩家维度值的平均，含目标）、`match_best`（本场最佳：按 `best_player_rule` 选出的那名玩家，名称写成 `"本场最佳（甲，22/14）"`）、`tier_median`（全部为 50）；
  - 汇总：只有 `tier_median`。
- **问题汇总**：沿用 `choose_problems` 的排序和范围，最多 `max_problems` 项。`you` 是目标每 10 回合的次数，`target` 取该分段的 `problem_rates[id].per_10_rounds`。severity：`you ≥ 1.5 × target` 为 bad，`you > target` 为 warn，否则 ok。没有分段数据时 `target` 为 None，severity 为 warn。1.5 写进 `[radar]` 配置，名为 `problem_bad_ratio`。
- **做得好的方向**：在 `RADAR_METRICS` 中找目标百分位 ≥ `strength_min_percentile` 的指标，按百分位降序，取前 `max_strengths` 个。`you` 是指标值，`target` 是该分段中位数。具体回合按下表挑选，每项最多 `max_examples` 条，按 tick 排序：

  | 指标 | 挑选的瞬间 |
  |---|---|
  | `time_to_damage_ms` | 击杀中 TTD 最短的 |
  | `crosshair_placement_deg` | 击杀中准星偏差最小的 |
  | `counter_strafing_pct` | 第一枪已停稳且击杀的 |
  | `spray_accuracy_pct` | 连射 ≥ 3 发且击杀的 |
  | `trade_kills_per_round`、`traded_death_pct` | 带 `trade_kill` 标签的击杀 |
  | 道具三项 | 带 `flash_assist` 标签的瞬间；没有就不列具体回合 |

- **verdict（一句话结论）**：程序模板，不调用 AI。有 severity 为 bad 的问题时写 `"最该练的是{问题名}：每 10 回合 {you} 次，{分段名}是 {target} 次。"`；否则写 `"这段时间没有明显落后于{分段名}的问题，最强的是{最高维度名}。"`。
- **situation**：复用已有的局面句函数 `tools/slots.py::situation_text(side, own, enemy, planted, round_time)`（参数取 `Moment.context`），不要另写。返回 None 时写空字符串。
- **跳转命令**：playdemo 用 `[review].playdemo_template`（`{subdir}`、`{demo_stem}`），jump 用 `jump_template`（`{tick}`），与 `scripts/review_pack.py` 保持一致。

### 6. CLI

- `analyze` 和 `summary` 新增 `--target <分段 id>`，默认 `[radar].default_tier`。分段文件不存在时打印提示，`tiers` 留空，其余照常输出。
- 两条命令都写出 `report_data.json`，放在 `basic_report.md` 旁边。`--target` 只决定 `default_tier`，`tiers` 里包含所有已构建的分段。
- 把 `report_data.json` 加进 `cli.py::_archive_reports` 的归档列表。

## 验收标准

- [ ] `tests/test_radar.py`：
  - 百分位方向：越小越好的指标，值越小百分位越高；与 `baseline.compare` 在相同输入下结果一致；
  - 维度平均时跳过 None；全部为 None 时维度为 None；
  - 汇总取各场中位数。
- [ ] `tests/test_highlights.py`：用构造的 `MatchMoments` 和 `Round` 覆盖：
  - 四类各一个正例；
  - 同回合多类时只留一类；
  - 输掉的回合不算残局获胜和首杀并赢下；
  - teamkill 不计入多杀。
- [ ] `tests/test_page_data.py`：
  - severity 三档；
  - 没有分段时 `target` 为 None；
  - 本场最佳的选择与平局规则；
  - verdict 的两种模板；
  - 跳转命令与 `review_pack` 的模板一致；
  - 分段基准文件里没有 steamid 和名字（对构建函数的输出断言）。
- [ ] `lobby_lines` 拆分后，现有 `tests/` 中涉及 lobby 的测试不改预期即可通过。
- [ ] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。
- [ ] 真实 demo：
  1. `python scripts/build_tier_baseline.py --tier amateur_tmp` 成功。贴出 `source` 一行和各指标的 `n`。
  2. 用评测集 `data/eval/eval_set.toml`（不入库）第一个 case 的 demo 和 player：`python -m cs2coach analyze <demo> --player <player>`。贴出 `report_data.json` 中的这几项：
     - `verdict`；
     - `tiers[0].you`；
     - `comparisons` 的名称；
     - `problems` 的 id 和 severity；
     - `highlights` 的 title。
  3. 同一个 player：`python -m cs2coach summary --player <player>`。贴出同样几项。贴结果时玩家名一律写"目标玩家""队友甲"。
- [ ] `python scripts/privacy_scan.py` 对新生成的 `baselines/tiers/*.json` 没有命中。

## 范围外（不要做）

- HTML 页面（044 做）；Markdown 报告和 AI 报告保持不变。
- 不改任何检测规则和阈值，不改 `pro_sample.json`。
- 不做维度总分或加权分（产品负责人还没定）。
- 不做"经济劣势获胜"等其他高光类型。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
