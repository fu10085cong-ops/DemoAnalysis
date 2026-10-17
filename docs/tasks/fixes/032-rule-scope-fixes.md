# 032 规则口径修正：出枪慢只看反应段，拉出撞架点降为背景，另外四条加情境条件

状态：待做（**031 合并之后再开始**：两者都改 `problems.py` 和 `config/default.toml`）
难度：B（照现有写法改条件）
执行：Codex，思考强度 medium（Kimi 开通后同类任务改由 Kimi 执行）
分支：`task/032-rule-scope`

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

## 验收标准

- [ ] 单元测试：在 `tests/test_problems.py` 的 `CASES` 中，每项改动有一对样本，只改这一个条件，一个判、一个不判。
  1. **出枪慢**：`seen_to_first_shot_s` 超过阈值、TTD 也超过时判；TTD 超过阈值但 `seen_to_first_shot_s` 不超过时**不判**；
  2. **拉出撞架点**：默认配置下不出现在报告里；`dry_peek_with_utility`、`overpeek_in_advantage` 仍然命中；
  3. **有道具拉出**：身上只有烟时不判，有闪光时判；
  4. **优势冒进**：CT、已下包时不判；T、已下包时判；
  5. **下包带火**：`seconds_since_plant` = 9.9 时不判，= 10.0 时判；
  6. **Glock**：远距离被 Glock 打死时不判，被 AK 打死时判。
- [ ] `seconds_since_plant` 的计算有一个用 `tests/helpers/synthetic.py` 构造的测试。
- [ ] 开发时只跑 `tests/test_problems.py tests/test_problem_frequency.py`，以及受影响的基准测试；完成前跑一次全部测试，报告只贴最后一行。
- [ ] 运行 `scripts/build_baseline.py` 重建基准，贴出 7 把武器的 `seen_to_first_shot_ms` p75，以及六条规则在 `problem_rates` 中的新旧值。
- [ ] 运行 `scripts/audit_collect.py --name 2026-10-17-after032`，贴出这六条规则的命中数，和 after031 对比。
- [ ] 更新 `docs/problem_catalog.md` 中这六条的前提条件，以及 `docs/features.md` 中出枪慢的口径。
- [ ] 完成报告里**玩家名一律替换成"（已匿名）"**；运行 `scripts/privacy_scan.py`，退出码为 0。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改 031 涉及的准星和漏补。
- 不新增问题，比如换弹时机、闪到队友，这些另开任务单。
- 不改检查器的整体结构和 AI 提示词。
- 不修改 `data/audit/*/labels.toml`。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
