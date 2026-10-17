# 007 结构化结论：AI 的提交格式与检查器

状态：完成

## 目标

AI 不再直接写报告，而是通过 `submit_report` 工具提交**结构化结论**。程序逐条检查，不通过的返回具体原因，让 AI 修改。

本任务只做**格式定义**和**检查器**两部分，都是纯函数，不调用 AI。接入调用流程、提示词和排版是任务 008。

背景：用户发现 AI 报告有准确性问题，例如回合数对不上、漏掉细节、说了数据里没有的东西。现在程序只检查"引用的编号存在"，不检查 AI 说的内容对不对。设计见 `docs/problem_catalog.md`"用途与原则"。

## 背景（必须读）

- `CLAUDE.md`（架构原则 2–4）、`AGENTS.md`
- `docs/problem_catalog.md`：问题的分类、"只作背景"（1.4 节）、"其他"（第三部分）
- `src/cs2coach/features/problems.py`：`PROBLEMS`、`CONTEXT_ONLY`、`ProblemReport`、`ProblemHit`、`detect_problems`
- `src/cs2coach/tools/queries.py`：`TOOLS` 的写法（`name`、`description`、`input_schema`）。`submit_report` 照同样的格式写
- `src/cs2coach/tools/citations.py`：现有引用检查，可以复用其中的编号正则
- `src/cs2coach/features/labels.py`：`WEAPON_LABELS`（武器名里含数字，提取数字前要先去掉）
- `src/cs2coach/features/models.py`：`Moment`、`MatchMoments`

## 要做的事

### 1. 提交格式：新建 `src/cs2coach/tools/submission.py`

```python
class Finding(BaseModel):          # 一条问题结论
    problem: str                   # 问题编号（PROBLEMS 中已启用的非亮点问题）或 "other"
    targets: list[str]             # 1–5 个：瞬间编号 "R5-death"、回合 "R5"、整场 "match"
    severity: Literal["high", "medium", "low"]
    analysis: str                  # 分析：为什么是问题、在这个情境下有多严重
    training: str                  # 针对这一条怎么练

class Pattern(BaseModel):          # 反复出现的问题
    problem: str
    targets: list[str]             # ≥ 2 个
    analysis: str

class Highlight(BaseModel):        # 亮点
    problem: str                   # 必须是 category == "highlight" 的编号
    targets: list[str]             # 1–3 个
    analysis: str

class ReportSubmission(BaseModel):
    overview: str                  # 整场总览，3–5 句
    findings: list[Finding]        # 1 – max_findings 条
    patterns: list[Pattern]        # 0 – max_patterns 条
    highlights: list[Highlight]    # 0 – max_highlights 条
    training_plan: list[str]       # 1–5 条
```

**`submit_tool(report: ProblemReport, cfg) -> dict`**：生成工具定义，格式与 `TOOLS` 一致。

- `input_schema` 是严格的 JSON Schema：每个 object 都写 `additionalProperties: false`，字段都是 required。
- `problem` 用 `enum` 限定为当前可用的编号：
  - `findings`、`patterns`：`PROBLEMS` 中非 highlight、**未被停用**的编号，加上 `"other"`；
  - `highlights`：highlight 类编号。

  这样便宜的模型没法编造编号。
- `targets` 的元素用 `pattern` 限定为 `^(R\d+(-(kill\d+|death|assist\d+))?|match)$`。
- 数量上限、文字长度上限用 `maxItems`、`maxLength` 写进 schema，数值来自配置。
- `description` 用中文写清楚每个字段怎么填，并说明"分析文字里不要写编号，编号只放在 targets 中"。

**`parse_submission(args: dict) -> ReportSubmission`**：校验失败时抛出带中文说明的异常。

### 2. 检查器：新建 `src/cs2coach/tools/checker.py`

```python
class Violation(BaseModel):
    path: str          # 如 "findings[2].analysis"
    code: str          # 见下表
    message: str       # 中文，具体说明错在哪、数据实际是什么，让 AI 能据此修改

def check_submission(sub: ReportSubmission, mm: MatchMoments, report: ProblemReport,
                     digest: str, cfg: dict) -> list[Violation]: ...
```

`digest` 是给 AI 的整场摘要原文，用来确定总览里可以出现哪些数字（见第 5 项）。

**检查项**：

| code | 检查 | `message` 举例 |
|---|---|---|
| `unknown_target` | `targets` 中的编号不存在：瞬间编号不在 `mm.moments` 中；回合超出 `1..mm.rounds`；`match` 只能用于 `spray_control` | `R40-death 不存在，本场共 28 回合` |
| `unknown_problem` | 编号不在 `PROBLEMS` 中，也不是 `other` | |
| `context_only` | 编号属于 `CONTEXT_ONLY` | 附上 `CONTEXT_ONLY` 中记录的原因 |
| `disabled_problem` | 编号被配置停用 | |
| `wrong_category` | `highlights` 中用了非亮点编号，或 `findings`、`patterns` 中用了亮点编号 | |
| `precondition_failed` | 除 `other` 外，每个 target 在 `report.hits` 中都必须有同编号、同 target 的命中 | `R11-death 不满足"主动拉出撞上对手架点"：该瞬间检测到的问题只有 turned_into_flash、weapon_not_ready`。要列出该 target 实际命中的编号；没有任何命中时也要写明 |
| `too_many_other` | `other` 的条数超过 `max_other`（配置，默认 3） | |
| `pattern_too_few` | `patterns` 中 target 少于 2 个 | |
| `number_not_found` | 文字中的数字在允许的数值中找不到（见第 3 项） | `"反应慢了 650 毫秒"中的 650 在 R3-kill1 的数据中找不到；该瞬间的 time_to_damage_s 为 0.812` |
| `phrase_unsupported` | 敏感说法没有数据支撑（见第 4 项） | `"没看到对手"需要 R9-death 的 visibility_note 为 not_seen，实际为 seen_since_previous_kill` |
| `round_mismatch` | 文字中提到的"第 N 回合"或"R数字"不是本条 targets 所在的回合 | |
| `id_in_text` | 分析文字中出现了瞬间编号（如 `R5-death`、`[[R5]]`）。编号只能放在 targets 里，由程序排版 | |

### 3. 数字检查（`number_not_found`）

适用于 `overview`，以及 `findings`、`patterns`、`highlights` 中的 `analysis`。**不适用于** `training` 和 `training_plan`，训练建议里的"练 100 次"不是数据。

1. **提取数字**：
   - 先去掉武器显示名（`WEAPON_LABELS` 的值，如 AK-47、M4A1-S、SSG 08）和 `1v2` 这类人数写法中的数字；人数单独校验，要等于该瞬间的 `alive_own` 和 `alive_enemy`；
   - 再用正则提取 `\d+(?:\.\d+)?`。
2. **允许的数值集合**，按条目分别计算：
   - 该条每个 target 的完整数据：瞬间用 `Moment.model_dump()` 的所有数值叶子；回合 target 用该回合的 `missed_trades` 记录；
   - 该条每个 target 在 `report.hits` 中的 `evidence` 和 `thresholds`，JSON 字符串形式的列表要解析出来；
   - target 的回合号，以及 target 的个数；
   - **单位换算**：每个数值 v，另外允许 v × 1000（秒 → 毫秒）、v / 1000、v × 100（比例 → 百分比）；`recoil_index` 另外允许 v + 1（"第 N 发"从 1 开始数）；
   - **总览**额外允许 `digest` 原文中出现的所有数字。
3. **匹配**：文字中的数 x 有 d 位小数时，只要某个允许值四舍五入到 d 位后等于 x，就算匹配。如果数字前面有"约、大约、接近、左右"，放宽到 ±10%。
4. 首版允许一定比例的误判。008 实测后再调整，所以容差放进配置。

### 4. 敏感说法检查（`phrase_unsupported`）

在配置中维护对照表：

```toml
[[checker.phrases]]
pattern = "没看到|没看见|没发现"
require = "not_seen"
```

`require` 是代码中实现的谓词名。规则：**本条至少有一个 target 满足该谓词**。

首版实现下列谓词，每个都要测试：

| 谓词 | 条件（任一 target 满足即可） | 对应说法（首版对照表） |
|---|---|---|
| `not_seen` | `visibility_note == "not_seen"` | 没看到、没看见、没发现 |
| `blind` | `player_blind` 或 `opponent_blind` 不为 None，或 `death.blinded` | 被闪、吃闪、闪白、致盲 |
| `peek` | `contact_type == "peek"` | 干拉、主动拉出、拉出来 |
| `held` | `contact_type` 为 `"held"` 或 `"peek"`（目标玩家拉出时，对手就是在架点） | 架点、架枪、卡点 |
| `seen_order` | `seen_first_lead_s` 不为 None | 先看见、先发现、后看见 |
| `shot_order` | `first_shot_lead_s` 不为 None | 先开枪、后开枪 |
| `trade` | 有 `trade` 数据，或 target 是漏补枪的回合 | 补枪、补回、没人补 |
| `stopped` | `first_shot_stopped` 不为 None，或交火中有 `stopped` 不为 None 的子弹 | 急停、停稳、没停 |
| `spray` | 交火中有 `recoil_index ≥ 1` 的子弹 | 压枪、扫射、连射 |
| `weapon_not_ready` | `death.weapon_ready is False` | 拉栓、还不能开枪 |

对照表放进 `config/default.toml` 的 `[checker]` 段，以后加说法只改配置。谓词名在配置里写错时，配置校验要报错。

### 5. 配置

新增 `[checker]` 段：`max_findings`（6）、`max_patterns`（3）、`max_highlights`（3）、`max_other`（3）、各文字字段的长度上限、数字容差、`phrases` 对照表。每项带中文注释。

## 验收标准

- [x] 新建 `tests/test_submission.py` 和 `tests/test_checker.py`：
  - `submit_tool` 的 schema：
    - 所有 object 都有 `additionalProperties: false`；
    - 被停用的编号不在 enum 中；
    - `highlights` 的 enum 只有亮点编号；
    - 用 `jsonschema` 校验一份合法提交和几份非法提交。如果项目里没有 `jsonschema`，**不要新增依赖**，改为手工检查 schema 结构。
  - 上表**每个** `code` 至少一个触发的测试，并验证 `message` 中含有关键数据（例如 `precondition_failed` 列出了实际命中的编号）；
  - 一份完全合法的提交：返回空列表；
  - 数字检查：
    - 秒 → 毫秒、比例 → 百分比、`recoil_index + 1`；
    - 四舍五入匹配（0.79 → "0.8 秒"）；
    - "约"的放宽；
    - 武器名中的数字不算；
    - `training` 中的数字不检查；
    - 总览可以使用摘要中的数字。
  - 每个谓词至少一个满足、一个不满足的测试。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实数据演练（**不调用 AI**）：
  1. 用 5E Mirage（玩家 `76561190000001002`）的 `MatchMoments` 和 `ProblemReport`，**手工写两份提交**：
     - 一份全部基于 `_problems.md` 中真实的命中，应该零违规；
     - 一份故意犯错：编错 target、给没有命中的瞬间安问题、写错数字、在没有被闪的瞬间写"吃闪"。
  2. 两份都放在 `tests/fixtures/` 中作为回归测试，在完成报告中贴出第二份的违规列表。
  3. 摘要用 `build_digest` 生成：需要 `MatchData` 时可以解析这场 demo，也可以在测试中只用摘要中的一小段文字。

## 范围外（不要做）

- 不改 `diagnose()` 的调用流程，不改提示词，不做排版（任务 008）。
- 不删除现有的 `citations.py`（008 决定去留）。
- 不改问题检测和指标。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `src/cs2coach/tools/submission.py`：定义 Finding、Pattern、Highlight、ReportSubmission；拒绝额外字段及类型转换，所有字段必填。`parse_submission` 将结构错误转换为包含字段路径的中文 SubmissionError。
  - `submit_tool` 生成与现有 TOOLS 相同格式的工具定义：每个 object 都有 additionalProperties=false，动态 enum 排除停用编号并区分亮点，targets 使用指定正则，数量与各类文字长度上限从配置读取。未注册进现有工具或诊断流程。
  - `src/cs2coach/tools/checker.py`：实现任务表的全部 12 类违规；逐 target 核对同问题命中，错误消息包含实际命中编号或实际字段值。other 免前提检查但仍检查数字、敏感说法、类别与数量；规律要求至少两个不同 target。
  - 数字检查展开 Moment 数值叶子和命中证据/阈值中的 JSON 列表，处理秒/毫秒、比例/百分比及 recoil_index+1；用十进制四舍五入匹配文字精度。先去掉武器显示名，人数按完整配对单独核对；近似表达的容差可配置，训练文字不参与数据检查。
  - `config/default.toml` 新增 checker 配置、数量/长度上限、容差和十组敏感说法；`config.py` 使用检查器谓词注册表校验名称，同时拒绝无效正则和缺失/额外的对照表字段。未新增依赖。
  - `tests/test_submission.py`、`tests/test_checker.py`：共 81 项测试，覆盖严格 schema、结构错误、全部违规类型、合法提交、数字换算与容差、十个谓词的正反情况、配置校验和真实数据回归。
  - `tests/fixtures/mirage_submission_valid.json`、`mirage_submission_invalid.json` 是手工编写的两份提交；`mirage_checker_data.json` 保存真实数据快照，含从原始 37 个瞬间中选取的 7 个相关瞬间、原回合数与漏补记录、这些数据的 ProblemReport，以及完整摘要。纯函数回归测试无需本地 demo。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**443 passed, 3 skipped**。专项测试 81 项通过；`git diff --check` 通过。环境中未安装 jsonschema，按任务要求手工检查 schema 结构、合法提交的 enum/数量/长度/编号约束和非法值对应的限制，未新增依赖。
- 真实 demo 验证：
  - 仅解析 `g173-n-20261014223216271995270_de_mirage.dem`，玩家 `76561190000001002`；复用 005 生成的真实 MatchMoments，使用新解析的 MatchData 调用 `build_digest` 生成摘要（不传职业基准也可生成完整摘要）。未调用 AI，原 demo 只读。
  - 摘要保存于 `data/output/submission_mirage_digest.md`，与真实数据快照一同用于演练。合法提交使用 slow_reaction、shot_while_moving、weapon_not_ready 的真实命中、两个不同回合的同类规律和真实先发现/先开枪亮点，返回 **0 条违规**。
  - 错误提交返回 **4 条违规**，完整列表如下（与回归测试实际输出一致）：

  ```json
[
  {
    "path": "findings[0].targets[0]",
    "code": "unknown_target",
    "message": "R40-death 不存在，本场共 28 回合；瞬间编号须来自 mm.moments。"
  },
  {
    "path": "findings[1].targets[0]",
    "code": "precondition_failed",
    "message": "R11-death 不满足“主动拉出撞上对手架点”（peeked_into_held_angle）：该 target 实际检测到的问题为 turned_into_flash。"
  },
  {
    "path": "findings[2].analysis",
    "code": "number_not_found",
    "message": "文字中的数字 650 在允许数据中找不到。R3-kill1 实际数据：{\"alive_own\": 2, \"alive_enemy\": 1, \"trade\": {\"traded\": null, \"traded_by\": null, \"trade_delay_s\": null, \"nearest_teammate_distance\": null, \"is_trade_kill\": false, \"avenged_teammate\": null, \"avenge_delay_s\": null}, \"death.blinded\": null, \"death.weapon_ready\": null, \"time_to_damage_s\": 0.812, \"crosshair_placement_deg\": 3.33, \"visibility_note\": null, \"contact_type\": \"unclear\", \"seen_first_lead_s\": 0.375, \"first_shot_lead_s\": null, \"first_shot_stopped\": false, \"player_blind\": false, \"opponent_blind\": false, \"shot_stopped\": [false, false, true, true], \"recoil_index\": [0.0, 1.0, 2.0, 3.0]}"
  },
  {
    "path": "findings[2].analysis",
    "code": "phrase_unsupported",
    "message": "“吃闪”需要至少一个 target 满足 blind，实际均不满足。R3-kill1 实际数据：{\"alive_own\": 2, \"alive_enemy\": 1, \"trade\": {\"traded\": null, \"traded_by\": null, \"trade_delay_s\": null, \"nearest_teammate_distance\": null, \"is_trade_kill\": false, \"avenged_teammate\": null, \"avenge_delay_s\": null}, \"death.blinded\": null, \"death.weapon_ready\": null, \"time_to_damage_s\": 0.812, \"crosshair_placement_deg\": 3.33, \"visibility_note\": null, \"contact_type\": \"unclear\", \"seen_first_lead_s\": 0.375, \"first_shot_lead_s\": null, \"first_shot_stopped\": false, \"player_blind\": false, \"opponent_blind\": false, \"shot_stopped\": [false, false, true, true], \"recoil_index\": [0.0, 1.0, 2.0, 3.0]}"
  }
]
  ```

- 遗留问题 / 需要确认的地方：
  - 用户已确认接口划分：`parse_submission(args)` 只校验结构，动态数量/长度由工具 schema 约束，内容按任务表由检查器核对；没有添加 cfg 参数或隐式读取配置。008 接入时应保留 schema 约束。
  - 用户已确认：总览的数字仅来自 digest，人数配对必须在 digest 出现；条目人数须与 targets 中至少一个瞬间完整匹配。总览无 targets，不对其套用逐 target 的敏感说法谓词；训练建议不作比赛数据检查。
  - 用户已确认：spray_control 的 match target 可用整场 mm.moments 核对敏感说法，但不能据此把任意瞬间数值加入整场允许数字集合。
  - 规律 targets 的配置上限首版设为 10，其余默认数量按任务单；各类文字长度上限均可配置。总览“三到五句”在工具说明中要求，未增加句子语义判断。
  - 数字仅核对允许集合，不保证数字与分析中的具体指标语义对应；敏感说法只保证至少一个 target 有所需字段，不判断方向或措辞的所有语义。这是任务规定的首版口径，008 实测后再调整。
  - 开始时工作区干净，004、005 均已标记完成。此次只改 007 所列文件及本任务单，未改 diagnose、提示词、排版、citations.py、问题检测或指标；未提交、未调用真实 AI。

## 审查意见（Claude 填写）

**审查通过（2026-10-15）。** 12 类违规都已实现，退回消息附带实际数据，可以据此修改；四舍五入用 Decimal 处理，避开了浮点误差；两份真实数据演练已保存为回归测试。443 个测试通过。

记下来留到 009 实测后再看：
- 允许的数值集合很宽（瞬间的所有数值叶子及其换算），错误的数字有可能碰巧匹配上。首版可以接受，看实际误放和误杀的情况再收紧。
- 退回消息中的数据 JSON 较长，会增加 token 消耗。

