# 019 数字由程序填写：分析文字中的占位符

状态：待做

## 目标

AI 写的总览、分析、亮点中不再出现任何数字，需要数字的地方写占位符，例如 `{R5-kill2.ttd}`，排版时由程序换成真实数值，例如"781 毫秒"。这样能整类消除"数字写错、含义写错、张冠李戴"的错误。

**背景**：2026-10-16 核对了 3 份报告，12 处错误中有 6 处属于这一类，现有的"数字是否存在于数据中"核对拦不住：

- "7 次先发现对手的击杀"：7 其实是首杀次数；
- "3 次漏补"：实际只有 1 次；
- "命中率 100%"：实际是 67%，而整场数据里恰好有别的 100.0% 可以对上；
- 把 AK 的职业阈值 9.7° 配给了 Five-SeveN 的交火；
- "整段九发"：9 发其实是后段的发数；
- "其中两次随后阵亡"：实际三次都是阵亡，用的是中文数字。

产品负责人已确认以下规则：

- 总览、分析、亮点中**完全禁止阿拉伯数字**，只能用占位符；
- 禁止"两、三……"等中文数字加量词的写法，"一次……另一次……"仍然允许；
- 训练文字（`training`、`training_plan`）不受这两条限制。"3 组 30 次"是训练计划，不是比赛事实。

## 背景（必须读）

- `CLAUDE.md`（架构原则 2、4）、`AGENTS.md`
- `src/cs2coach/tools/submission.py`：`Finding`、`submit_tool`
- `src/cs2coach/tools/checker.py`：`_check_entries`、`_check_overview`、`_number_violations`、`_training_violations`、`_WEAPONS`、`PREDICATES`
- `src/cs2coach/tools/digest.py`：`build_digest`、`whole_match_numbers`、`METRIC_LABELS`
- `src/cs2coach/summary/digest.py`：`build_summary_digest`、`summary_numbers`
- `src/cs2coach/report/facts.py`（事实卡的取值与单位，占位符的显示应与之一致）、`report/evidence.py`
- `src/cs2coach/report/render.py`、`summary/render_report.py`
- `src/cs2coach/ai/prompts.py`、`summary/prompts.py`
- `src/cs2coach/ai/diagnose.py`、`summary/diagnose.py`：修正循环和审计记录
- `config/default.toml`：`[checker]`、`[training] allowed_latin`

## 要做的事

### 1. 占位符语法和解析：新建 `src/cs2coach/tools/slots.py`

- 语法：`{对象.字段}`，例如 `{R5-kill2.ttd}`、`{match.ttd}`、`{freq.slow_reaction.hits}`。多场时，对象可以带场次前缀：`{M3:R9-death.ttd}`、`{M3.score}`。
- 写一个解析函数，单场和多场共用：
  ```python
  def render_slots(text, resolver) -> tuple[str, list[SlotError]]
  ```
  `resolver` 由单场或多场分别提供，与检查器的 `_single_resolver`、`_summary_resolver` 同样处理。
- 显示格式由程序决定，与事实卡保持一致：
  - 毫秒取整，"781 毫秒"；秒保留一位小数，"1.3 秒"；
  - 角度保留一位小数，"21.7°"；百分比去掉多余的 0，"33%""12.5%"；
  - 距离用米取整，"19 米"；队友距离用游戏单位取整，"480 单位"；速度"209 单位/秒"；
  - 人数"5v4"；回合时间"24 秒"，AI 写成"回合第{…round_time}"；回合"第 5 回合"。

**可用的对象和字段**（GPT 按实际数据结构实现。字段名可以调整，但必须在摘要中列出完整清单）：

| 对象 | 字段 |
|---|---|
| 瞬间 `R5-kill2` | `round`、`side`、`people`、`round_time`、`distance`、`health`、`opponent_health`、`equip`、`opponent_equip`、`ttd`、`seen_to_shot`、`shot_to_hit`、`opponent_ttd`、`crosshair`、`shots`、`hits`、`hit_rate`（程序计算）、`speed`、`opponent_speed`（接触时）、`trade_delay`、`teammate_distance` |
| 瞬间的**完整句**（避免方向写反） | `shot_order` → "你先开枪，领先 0.27 秒" / "对手先开枪……"；`seen_order` 同理；`contact` → 与事实卡相同的接触类型说明 |
| 瞬间 + 本条问题的命中 | `value`、`threshold`、`gap`（value − threshold）。按本条 `problem` 取该 target 的 `ProblemHit`，每个问题用哪个 evidence 键和 thresholds 键，在代码里写成一张表；例如 slow_reaction：`ttd_ms` / `ttd_ms`；spray_control：`hit_pct` / `expected_hit_pct` |
| 漏补回合 `R9` | `people`、`round_time`、`distance`、`count`（本回合的漏补次数）。一个回合有多条记录时，用 `R9[1]`、`R9[2]`（从 1 开始）；不加序号且有多条时报错 |
| 整场 `match`（多场为整批） | `score`、`rounds`、`rounds_played`、`kills`、`deaths`、`assists`，以及六项枪法指标（`ttd`、`crosshair`、`counter_strafing`、`spray`、`spotted`、`headshot`）及其样本数 |
| 职业 `pro.<指标>` | `median`、`p25`、`p75`、`better_than`（"优于 N% 的职业样本"中的 N%） |
| 频率 `freq.<问题编号>` | `hits`、`expected`、`excess`、`chance`、`ratio`；多场另有 `matches_with_hits`、`matches_above_pro` |
| 计数 `count.<名称>` | 程序按标签统计：`kills`、`deaths`、`assists`、`flash_assists`、`opening_kills`、`opening_deaths`、`saw_first_kills`、`shot_first_kills`、`trade_kills`、`traded_deaths`、`missed_trades`、`no_shot_deaths`、`unused_utility_deaths`。标签名以 `features/moments.py` 为准；每个计数在摘要中写明中文含义 |
| 多场的场次 `M3` | `score`、`kills`、`deaths`、`rounds_played` |

### 2. 检查器

新增违规代码：

| 代码 | 含义 | 说明里写什么 |
|---|---|---|
| `raw_number` | 总览、分析、亮点中出现阿拉伯数字 | 指出这个数字，并建议对应的占位符 |
| `chinese_number` | 出现中文数字加量词：`[两二三四五六七八九十百几]+` 后接 次、发、枪、秒、米、回合、人、场、处、名 | 指出原文；"一次""另一次""这次"不报错 |
| `unknown_slot` | 对象或字段不存在 | 列出该对象可用的字段 |
| `slot_target_not_cited` | 瞬间或回合对象不在本条的 targets 中 | — |
| `slot_unavailable` | 字段值为空，例如没有 TTD | 写明原因（`visibility_note` 等） |

要求：

- 检查数字之前，先去掉占位符、武器显示名、地图别名、练习图名称、`allowed_latin`（如"5E"）。"AK-47""M4A1-S""de_dust2"不能误报。
- 总览只允许 `match`、`pro`、`freq`、`count`、多场的 `M3` 这几类对象。
- **删除**原来对总览和分析的数字允许集合核对（`_number_violations` 等），有了占位符就不再需要。训练文字原来就不核对数字，保持不变。
- 敏感说法、地图名、回合与场次引用、`id_in_text` 等其余检查保留。"第 N 回合"的核对改为核对占位符 `{…round}` 的对象是否在 targets 中。

### 3. 排版与审计

- `report.md` 中的占位符一律换成数值。原始提交和替换后的文字都写入 `submission.json`。
- 最终提交里如果还有无法解析的占位符：按现有规则删除该条，替换总览，并记录下来。不要把 `{…}` 原样印进报告。

### 4. 摘要与提示词

- 单场和多场摘要中新增"占位符"一节，写明：
  - 语法；
  - 每类对象可用的字段，以及显示示例；
  - `count.*` 各自的含义和本场数值，方便 AI 判断要不要提。
- 提示词：
  - 把"比赛数字只能取自摘要……"改为"文字里不写数字，用占位符"；
  - 写明中文数字的规则；
  - 涉及谁先开枪、谁先看见、谁在架点的方向时，优先用 `shot_order`、`seen_order`、`contact` 这三个完整句；
  - 把 finding 示例改成占位符写法。
- 摘要里逐次瞬间仍然显示数字，供 AI 阅读判断。只是 AI 写文字时不能抄这些数字。

### 5. 现有样例

`tests/fixtures/` 中历史真实提交的回归测试，按新规则更新断言：旧提交现在应报 `raw_number`。保留它们原本要验证的其他规则，例如地图名、练习图。在完成报告中逐个列出改了哪些。

## 验收标准

- [ ] 测试：
  - 每类对象和字段都能正确解析和显示，单位、小数位与事实卡一致；
  - `shot_order`、`seen_order` 在正负领先时，句子方向正确；
  - `value`、`threshold`、`gap` 按问题取对键：slow_reaction、poor_crosshair_placement、spray_control 至少各一例；
  - `R9[2]` 序号正确；多条记录不加序号时报错；
  - 各违规代码的触发和不触发：
    - "AK-47""M4A1-S""de_dust2""5E 的练枪图"不报 `raw_number`；
    - "其中两次随后阵亡"报 `chinese_number`，"一次……另一次……"不报；
  - 总览中使用瞬间对象时报错；
  - 排版后报告中没有 `{`；无法解析的占位符按规则删除；
  - 2026-10-16 真实错误的回归：用占位符写法改写 flash 汇总、本地 M7、卢文笙报告中被标出的句子，渲染出来是正确的数值。具体句子见各报告 `report.md` 中的"⚠️ 核对"。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] **真实本地运行**（Ollama 免费，可以调用；**不要调用 DeepSeek 或 Claude**）：
  ```
  python -m cs2coach analyze data/demos/perfectworld_demo/9215177492731139980_0.zip --player 76561199703310441 --ai --provider local --out <临时目录>
  ```
  完成报告中写明：
  - 各轮的违规代码分布、修正次数、删除的条目；
  - 本地模型能不能正确使用占位符；
  - 贴出第一条 finding 的原始提交和渲染后的文字。

  失败也如实报告，不要为了通过而放宽规则。
- [ ] `git diff --check` 通过。

## 范围外（不要做）

- 不改问题检测、频率规则和事实卡内容。
- 不做"程序挑代表例子"（任务 020）。
- 不改训练文字的检查规则。
- 不调用 DeepSeek 或 Claude。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
