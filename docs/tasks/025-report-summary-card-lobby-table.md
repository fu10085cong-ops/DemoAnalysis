# 025 报告开头的"本场体检"和同场对比表

状态：待做

## 目标

现在的单场报告（免费版 `basic_report.md` 和 AI 版 `report.md`）适合职业和硬核玩家，普通玩家读不下去：一上来就是毫秒、75 分位、偶然概率、倍数。产品负责人 2026-10-17 确定：

- **形式**：报告最前面加一页"本场体检"，包括各维度的等级和一句话、最该练的一件事、本场高光，再加一张精简的同场对比表。原来的内容整体放在后面，作为详细分析。
- **参照**：等级和排名**以同场 10 人为主**，职业对比作为补充。问题检测仍然对照职业基准，不改。
- **载体**：先放进 Markdown 报告。以后做本地网页时复用同一份数据，所以计算和渲染要分开。

参考对象是完美平台的局内数据页：按维度分表，10 人每人一行，标出同场最好和最差。2026-10-17 用一场完美 demo 对比过（见下方"已验证的口径"）：事件类指标和完美完全一致，"看见"相关的指标口径不同。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `src/cs2coach/features/moments.py`：`find_moments`；首杀、补枪、闪光助攻的标签（`opening_kill`、`opening_death`、`trade_kill`、`flash_assist`）
- `src/cs2coach/features/aim_stats.py`：`aim_stats`，整场枪法 6 项，样本不足时为 None
- `src/cs2coach/features/models.py`：`AimStats`、`MatchMoments`
- `src/cs2coach/features/visibility.py`：`Sight`。构造时要加载地图网格，10 个人共用一个
- `src/cs2coach/features/ranking.py`：`candidate_targets`，高光的排序复用它
- `src/cs2coach/report/basic.py`：`render_basic_report`、`choose_problems`、`build_l0`
- `src/cs2coach/report/render.py`：`render_diagnosis`，AI 版报告开头
- `src/cs2coach/baseline.py`：`compare`，用来给出"优于 X% 职业样本"
- `src/cs2coach/cli.py`：`_analyze`，报告怎么生成

## 已验证的口径（2026-10-17，Claude 用完美 demo `9220525551542050188_0` 和平台截图对比）

- **与平台一致，可以放心使用**：
  - 首杀成功次数和比例（10/10）；
  - 闪光助攻（10/10）；
  - 死亡时浪费道具的平均价值（10/10）；
  - 命中率：枪械命中敌人的伤害事件数 ÷ 枪械开枪数，不含刀和电击枪（9/10 误差在 1 个百分点以内）；
  - 爆头率（排序相关 0.99）。
- **道具伤害**：完美用的是 damages 表里的 `dmg_effective`，即扣除溢出血量的有效伤害，按此计算 10/10 一致。用 `dmg_health` 会偏多。本任务用 `dmg_effective`，只算对敌人的 `hegrenade`、`inferno`、`molotov`、`incgrenade`。
- **口径与平台不同，但同场排名仍然可比**：出枪速度（TTD）、预瞄、急停、看见时命中率、压枪。我们用地图几何判断"看见"，比引擎的"被发现"标记平均早 188 毫秒，所以这几项的数值会比平台看起来慢、差。报告里要加一行口径说明，见第 4 步。
- **补枪**：平台的窗口约 5 秒，我们的配置是 3 秒（`moments.trade_window_seconds`），所以我们的补枪数偏少。本任务沿用配置值，**不改窗口**；改不改由产品负责人另定。

## 要做的事

### 1. 同场数据：新模块 `src/cs2coach/features/lobby.py`

```python
class LobbyLine(BaseModel):
    steamid: int
    name: str
    team: int                      # 2 = T 方起手，3 = CT 方起手，按上半场阵营
    is_target: bool
    kills: int
    deaths: int
    accuracy_pct: float | None     # 命中率，口径见上
    aim: AimStats | None           # 6 项整场枪法，与目标玩家同一口径
    opening_kills: int
    opening_deaths: int
    trade_kills: int
    flash_assists: int
    utility_damage: int            # dmg_effective 合计

def lobby_lines(match: MatchData, target: int, cfg: dict, sight: Sight) -> list[LobbyLine]: ...
```

- 每名真实玩家（有 steamid、不是 bot）一行，包括已经断线的玩家。
- **性能**：只解析一次 demo，`Sight` 只构造一次，10 个人共用。在完成报告里写明，加入同场计算后单场 `analyze` 多花了多少秒。
  - 可以直接对每个人调用 `find_moments`；如果太慢，可以抽出只算上面这些字段的轻量路径，但口径必须和 `find_moments` 一致，并用测试证明。
- `MatchMoments` 新增字段 `lobby: list[LobbyLine] = []`。目标玩家那一行的数值必须和 `MatchMoments.aim_stats` 等完全相同。

### 2. 体检卡：新模块 `src/cs2coach/report/card.py`

**计算和渲染分开**：先算出数据结构，再渲染成 Markdown。以后做网页时直接用数据结构。

```python
class CardRow(BaseModel):
    key: str                       # 维度编号，例如 "headshot"
    label: str                     # 显示名，例如 "爆头"
    value_text: str | None         # 本人数值，按显示格式写好
    rank: int | None               # 同场名次，1 = 最好；样本不足为 None
    ranked_count: int              # 参与排名的人数
    grade: int | None              # 1–5，对应 ●○ 的个数
    grade_label: str | None        # 很好 / 好 / 一般 / 偏弱 / 需加强
    pro_better_than_pct: float | None
    problem_note: str | None       # 该维度在下文有检出的问题时的提示

class SummaryCard(BaseModel):
    rows: list[CardRow]
    focus: CardRow | None          # 最该练的一件事
    focus_drills: list[str]
    highlight: str | None          # 本场高光，一句话

def build_card(mm: MatchMoments, problems: ProblemReport, frequency: list[FrequencyRow],
               cfg: dict, baseline: dict | None) -> SummaryCard: ...
def render_card(card: SummaryCard, mm: MatchMoments) -> list[str]: ...          # 体检卡的 Markdown 行
def render_lobby_tables(mm: MatchMoments, cfg: dict) -> list[str]: ...         # 同场对比表的 Markdown 行
```

**维度**写进配置 `[report.card]`，每个维度包括：编号、显示名、取值字段、方向（越大越好或越小越好）、对应的问题编号（可以为空）。第一版 7 个维度：

| 编号 | 显示名 | 取值 | 方向 | 对应问题 |
|---|---|---|---|---|
| `headshot` | 爆头 | `aim.headshot_accuracy_pct` | 越大越好 | — |
| `counter_strafing` | 急停 | `aim.counter_strafing_pct` | 越大越好 | `shot_while_moving` |
| `crosshair` | 预瞄 | `aim.crosshair_placement_deg` | 越小越好 | `poor_crosshair_placement` |
| `ttd` | 出枪速度 | `aim.time_to_damage_ms` | 越小越好 | `slow_reaction` |
| `spotted` | 看到人后的命中 | `aim.spotted_accuracy_pct` | 越大越好 | — |
| `spray` | 压枪 | `aim.spray_accuracy_pct` | 越大越好 | `spray_control` |
| `utility` | 道具 | `utility_damage` | 越大越好 | `dry_peek_with_utility` |

**规则**：

- **同场名次**：只在该维度有数值的玩家中排名，并列时名次相同。本人没有数值时，这一行显示"样本不足"，不给等级。
- **等级**：按名次百分位分 5 档，切分点写进配置。默认：前 20% 很好、前 40% 好、前 60% 一般、前 80% 偏弱，其余需加强。参与排名少于 `min_ranked`（默认 6）人时，不给等级，只显示名次。
- **一句话**：`同场第 N（共 M 人），优于 X% 职业样本`。没有职业基准时省略后半句。
- **问题提示** `problem_note`：该维度对应的问题出现在 L0 选出的问题中（`choose_problems`）时，写成"但有 N 次{问题名}，见下文"。
- **最该练的一件事** `focus`：
  - 候选是等级为"偏弱"或"需加强"、并且对应问题在本场有命中的维度；
  - 选同场名次最差的一个，并列时选该问题多出次数更多的；
  - 没有候选时，取 L0 选出的第一个问题对应的维度；还没有就不显示这一行。
  - `focus_drills` 照 `build_l0` 的方式取该问题的练习图。
- **本场高光** `highlight`：
  - 从 highlight 类问题的命中中选一个，优先级写进配置，默认先发现先开枪赢下对枪 → 及时补枪 → 闪光助攻，同类之间用 `candidate_targets` 的第一名；
  - 用模板写成一句话，只用事实卡中已有的数据，例如"R12 架点交火，0.13 秒打中并爆头击杀，收下 3v1"；
  - 没有就不显示。

**同场对比表**，两张：

- **枪法**：玩家、K-D、命中率、爆头率、出枪速度、预瞄、急停、压枪；
- **对局**：玩家、首杀（成功-失败）、补枪击杀、闪光助攻、道具伤害。

排版要求：

- 本人那一行以"▶你"开头；
- 每列同场前 `mark_top` 名标 ▲、后 `mark_top` 名标 ▼，默认 3；
- 先列本人所在队伍，再列对手，队内按击杀降序；
- 数值格式和事实卡一致，毫秒取整，百分比取整，角度保留 1 位。

### 3. 放进报告

在**免费版和 AI 版**报告的标题、比分行之后、原有第一节之前，依次插入：

1. `## 本场体检`：体检卡，含最该练的一件事和本场高光；
2. `## 同场对比`：两张表；
3. 口径说明，见第 4 步；
4. 一行"下面是详细分析"，之后原样接原有内容。

两个版本的体检卡和同场表完全相同，都由程序生成，不经过 AI。AI 版其余部分不变。

### 4. 口径说明

固定文字，放进配置 `report.card.note`，例如："出枪速度、预瞄、急停、压枪按地图几何判断'看见'，数值和对战平台不同；同场排名可以直接比较。补枪按 3 秒窗口统计。"

其中补枪窗口的秒数从配置读取，不要写死。

## 接口与数据

- **新增配置 `[report.card]`**：维度表、等级切分点、`min_ranked`、`mark_top`、高光优先级、口径说明。都要在默认配置中声明，并通过配置校验。
- **`MatchMoments` 新增 `lobby` 字段**：分析缓存的键已经包含特征层代码的哈希，代码一改旧缓存自动失效，不用额外处理。
- 多场汇总不在本任务范围内，见"范围外"。

## 验收标准

- [ ] 测试，用合成数据（`tests/helpers/synthetic.py`）：
  - `lobby_lines`：
    - 命中率、首杀、补枪、闪光助攻、道具伤害（用 `dmg_effective`）各有用例；
    - bot 不进表；
    - 目标玩家那一行和 `MatchMoments` 的数值一致；
  - 排名和等级：
    - "越小越好"的方向；
    - 并列；
    - 样本不足；
    - 少于 `min_ranked` 人时不给等级；
    - 等级切分点读配置；
  - 问题提示：维度对应的问题在 L0 选中时出现"但有 N 次……"；
  - 最该练的一件事：
    - 按"偏弱或需加强 + 有问题命中 + 名次最差"选出；
    - 并列时按多出次数选；
    - 没有候选时退回 L0 第一个问题；
  - 高光：按优先级选，没有高光命中时不显示；
  - 同场表：
    - ▲▼ 标记正确（含"越小越好"的列）；
    - 本人一行带"▶你"；
    - 队伍顺序和队内排序正确；
  - 两个版本的报告开头都有"本场体检""同场对比"和口径说明，内容逐字相同；
  - 报告中不出现占位符 `{`、`}`。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **真实数据**：
  - 运行 `.venv/bin/python -m cs2coach analyze data/demos/perfectworld_demo/9220525551542050188_0.zip --player <data/eval/eval_set.toml 第一个 case 的 player> --out "$TMPDIR/cs2coach-025"`，**不要加 `--ai`**；
  - 在完成报告中贴出 `basic_report.md` 从开头到"下面是详细分析"的部分，玩家名按 024 的要求匿名；
  - 目标玩家的这几项必须是：命中率约 13%、同场最低；爆头率约 33%、同场最高；首杀 2-0；闪光助攻 1；道具伤害 163；
  - "最该练的一件事"应该是压枪；
  - 写明加入同场计算后 `analyze` 多花了多少秒。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改问题检测、职业基准、频率规则，也不改补枪窗口。
- 不改 AI 的提示词和检查器。AI 不参与体检卡。
- 不做多场汇总的体检卡，以后单独开任务。
- 不做网页。
- 不删除和改写原有的报告内容，只在前面插入。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
