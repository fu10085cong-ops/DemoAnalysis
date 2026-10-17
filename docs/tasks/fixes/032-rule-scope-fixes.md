# 032 规则口径修正：出枪慢只看反应段，拉出撞架点降为背景，另外四条加情境条件

状态：待做（031 已合并，可以开始）
难度：B（照现有写法改条件）
执行：Kimi
分支：`task/032-rule-scope`（独立工作目录 `/Users/parsival/Desktop/democheck-032`）

## 目标

2026-10-17 复审规则时发现，有几条规则的事实没错，但教练不会把它当成失误。本任务给这几条规则补上情境条件，让报告只提醒真正值得提醒的情况。产品负责人已确认全部六项。

## 背景（必须读）

- `src/cs2coach/features/problems.py::detect_problems`：`slow_reaction`、`peeked_into_held_angle`、`dry_peek_with_utility`、`overpeek_in_advantage`、`post_plant_utility_unused`、`glock_long_range` 几段
- `src/cs2coach/features/models.py`：`Context`、`Engagement.seen_to_first_shot_s`
- `src/cs2coach/features/moments.py::bomb_planted`，以及 `Context` 的构造（搜索 `opponent_weapon=`）
- `src/cs2coach/baseline.py::weapon_stats`
- `src/cs2coach/parsing/weapons.py`：`RIFLES`
- `tests/test_problems.py`：`CASES` 表的写法
- `config/default.toml`：`[problems]`

## 要做的事

### 1. 出枪慢：只看"看见 → 开枪"

- **现在**：比较的是 Time to Damage（看见 → 第一次造成伤害）。如果慢在"开枪 → 命中"这一段，其实是开枪没打中，属于枪法问题，不是反应慢。
- **改为**：比较 `seen_to_first_shot_s` 和职业同武器的 p75。
  - 新增基准分布 `seen_to_first_shot_ms`，放在 `weapon_stats`，筛选条件和 `time_to_damage_ms` 相同；
  - 死亡时的附加条件"对手的 TTD 比自己短"保留不变；
  - 原有的排除条件全部保留。
- **证据**：同时给出 `seen_to_first_shot_ms`、`first_shot_to_damage_ms`、`ttd_ms`；阈值写 `source = "pro_p75"`。
- 程序占位符：在 `tools/slots.py` 的问题字段映射中，把 `slow_reaction` 指向新字段。只补映射，不改结构。
- 规则名"出枪慢"不变。

### 2. 主动拉出撞上对手架点：降为背景

- 加进 `[problems] disabled`，在配置注释里写明原因：拉出是常规动作，单独出现属于零和背景，只有加重版（有道具直接拉、优势冒进）才算问题。
- `dry_peek_with_utility`、`overpeek_in_advantage` 不受影响，照常检测。用测试保证这一点。

### 3. 身上有道具却直接拉出：只算闪光

- 判定条件里的道具从 `{flashbang, smokegrenade}` 改为配置项 `dry_peek_grenades = ["flashbang"]`。

### 4. 人数优势时冒进：排除 CT 方已下包

- `c.side == "CT"` 且 `c.bomb_planted` 时不判。CT 必须去拆包，这时拉出是被迫的。

### 5. 下包后带着火没用：要求下包后存活够久

- `Context` 新增 `seconds_since_plant: float | None`：本回合最后一次下包到这个瞬间的秒数，没下包时为 `None`。实现照 `bomb_planted` 的写法。
- 只在 `seconds_since_plant ≥ post_plant_min_alive_seconds` 时判。默认 10 秒，待审计校准。
- 证据里加上 `seconds_since_plant`。

### 6. 用 Glock 远距离对枪：只算对手拿步枪或狙

- 死亡瞬间的 `m.weapon` 是击杀者用的武器。只在它属于 `glock_long_range_opponent_weapons` 时判。
- 配置默认值：`RIFLES` 加上 `awp`、`ssg08`、`scar20`、`g3sg1`，在配置里写成列表。

### 配置（新键）

```toml
[problems]
dry_peek_grenades = ["flashbang"]
post_plant_min_alive_seconds = 10.0
glock_long_range_opponent_weapons = ["ak47", "m4a1", "m4a1_silencer", "galilar", "famas", "aug", "sg556", "awp", "ssg08", "scar20", "g3sg1"]
```

## 预写测试

Claude 已写好 `tests/test_rule_scope_032.py`，覆盖下面六项改动和 `seconds_since_plant`。实现的目标是让它们通过；**不要修改这个文件**，认为测试有错时停下来写进完成报告。

`tests/test_problems.py` 的 `CASES` 中，`slow_reaction`、`post_plant_utility_unused`、`glock_long_range` 三条的正样本会因为新条件失效，**允许并且需要**按新口径更新这三条，其他条目不要动。

## 验收标准

- [ ] `tests/test_rule_scope_032.py` 全部通过。
- [ ] 所有命令都必须带 `PYTHONPATH=src`，否则导入的是主目录里的代码：
  - 开发时：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q tests/test_rule_scope_032.py tests/test_problems.py tests/test_problem_frequency.py tests/test_baseline*.py`
  - 完成前跑一次全部测试：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不要**运行 `build_baseline.py` 和 `audit_collect.py`：工作目录里没有 demo，而且它们会写共享目录。这两步由 Claude 在审查时运行。
- [ ] 更新 `docs/problem_catalog.md` 中这六条的前提条件，以及 `docs/features.md` 中出枪慢的口径。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改 031 涉及的准星和漏补。
- 不新增问题，比如换弹时机、闪到队友，这些另开任务单。
- 不改检查器的整体结构和 AI 提示词。
- 不读、不改 `data/` 下的任何文件。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
