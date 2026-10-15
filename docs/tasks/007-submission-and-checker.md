# 007 结构化结论：AI 的提交格式与检查器

状态：待做（在 004 的修改审查通过并提交之后再做，避免改动混在一起）

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

- [ ] 新建 `tests/test_submission.py` 和 `tests/test_checker.py`：
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
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 真实数据演练（**不调用 AI**）：
  1. 用 5E Mirage（玩家 `76561199711881069`）的 `MatchMoments` 和 `ProblemReport`，**手工写两份提交**：
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
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
