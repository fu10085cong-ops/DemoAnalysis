# 033 新增问题"换弹时被打死"（子弹没打空就主动换弹）

状态：待做
难度：B（照现有规则的写法新增一条；解析层只加一个字段）
执行：Codex，思考强度 medium（改解析层，所以不交给 Kimi）
分支：`task/033-died-while-reloading`

## 目标

教练常说的一种失误：杀完人或交火间隙，子弹还没打空就换弹，结果换弹时被打死。

Claude 已在 24 场业余 demo 和 26 份职业 demo 上统计过（2026-10-17）：

- 阵亡时正在换弹：业余占全部阵亡的 2.8%，职业 0.7%。
- 其中换弹前弹匣**不是空的**（主动换弹）：业余每 10 回合 0.09 次（24 场共 46 次），职业 0.018 次，约 5 倍。
- 弹匣打空后被迫换弹的不算失误，不判。

## 背景（必须读）

- `src/cs2coach/parsing/match.py`：`PLAYER_PROPS`；031 加 `is_in_reload` 的写法（搜索 `is_in_reload`）
- `src/cs2coach/features/models.py::DeathState`
- `src/cs2coach/features/moments.py`：`DeathState` 的构造（搜索 `DeathState(`）；031 里 `reloading`（`find_missed_trades`）的写法
- `src/cs2coach/features/problems.py`：`_SPECS`、`DETECTED_PROBLEMS`、`post_plant_utility_unused` 一段的写法
- `config/default.toml`：`[problems]`、`[problems.exclusions]`
- `config/problem_text.toml`：每个问题的免费版文字，参照 `[post_plant_utility_unused]` 的写法

**已验证的事实**（不必重新验证）：

- `active_weapon_ammo` 是逐 tick 数值字段，完美、5E、ESL 三种来源都能返回，表示弹匣里剩余的子弹，拿刀和道具时为空。
- `is_in_reload` 已经在解析层，031 加的。
- 换弹开始时刻：从阵亡前一 tick 往回找，`is_in_reload` 连续为真的第一个 tick。最多回看 5 秒。换弹前的弹匣剩余，取这个 tick 的前一 tick 的 `active_weapon_ammo`。

## 要做的事

1. **解析层**：`active_weapon_ammo` 加进 `PLAYER_PROPS`，数值类型，缺失保留 NaN，**不要填 0**。在 `docs/fields.md` §5 注明三种来源已验证。跑一次 `scripts/check_parsing.py`，用一场完美 demo 即可，确认结论仍然是"全部一致"。
2. **`DeathState` 新增三个字段**，只在死亡瞬间计算：
   - `reloading_at_death: bool`：阵亡 tick − 1 或阵亡 tick，`is_in_reload` 为真；
   - `reload_started_s: float | None`：换弹开始到阵亡的秒数，不在换弹时为 None；
   - `ammo_before_reload: int | None`：换弹开始前一 tick 的弹匣剩余，取不到为 None。
3. **新问题 `died_while_reloading`**（换弹时被打死）：
   - 登记进 `_SPECS`（类别 `duel`，适用 `death`，可靠度 `high`）和 `DETECTED_PROBLEMS`；
   - 判定条件：`reloading_at_death` 为真，并且 `ammo_before_reload ≥ reload_min_ammo_left`（默认 1，也就是弹匣没打空）；
   - `ammo_before_reload` 为 None 时计入 `missing`，不判；
   - 证据：`reloading_at_death`、`ammo_before_reload`、`reload_started_s`；阈值写 `{"min_ammo_left": 1, "source": "config"}`；
   - 排除：在 `[problems.exclusions]` 的 `killed_by_utility` 里加上这条，被道具炸死、烧死时不判。
4. **配置**：`[problems]` 新增 `reload_min_ammo_left = 1`，加注释，并加进配置校验（正整数）。
5. **问题文字与文档**：
   - `config/problem_text.toml` 照 `[post_plant_utility_unused]` 的写法加一节，大意是"子弹没打空就换弹，换弹时被打死"；
   - `docs/problem_catalog.md` §1.2 加一行；
   - `docs/features.md` 写明新字段的口径；
   - 训练方式暂不指定练习图，和其他决策类问题一样。
6. **报告证据句**：在 `src/cs2coach/report/evidence.py` 里照其他问题的写法加一句，例如"阵亡时正在换弹，换弹前弹匣还有 N 发，开始换弹 X 秒后阵亡"。
7. **基准**：运行 `scripts/build_baseline.py` 重建职业基准，确认 `problem_rates` 里出现 `died_while_reloading`，其他问题的频率不变。
8. **全量收集**：运行 `scripts/audit_collect.py --name 2026-10-17-after033`，贴出 `died_while_reloading` 的命中数，预期约 46 次，相差很多时说明原因；同时确认其他问题的命中数与 `2026-10-17-after032` 完全相同。

## 验收标准

- [ ] 单元测试：
  - **`DeathState` 新字段**：用 `tests/helpers/synthetic.py` 构造三种情况——不在换弹；换弹中、换弹前弹匣还有 12 发；换弹中、换弹前弹匣为 0。分别检查 `reload_started_s` 和 `ammo_before_reload`；
  - **规则**：弹匣还有 1 发时判，为 0 时不判，为 None 时计入 missing；被燃烧瓶烧死时不判；
  - **配置校验**：`reload_min_ammo_left = 0` 或填小数时报错；
  - **证据句**：包含弹匣剩余和秒数。
- [ ] 开发时只跑相关测试文件，完成前跑一次全部测试：`.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] 第 7、8 步的数字写进完成报告。
- [ ] 完成报告里**玩家名一律替换成"（已匿名）"**；运行 `scripts/privacy_scan.py`，退出码为 0。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不做"闪到队友"：数据显示职业和业余一样多，每 10 回合 2.06 次对 2.14 次，不是业余特有的问题。
- 不改其他规则。
- 不改检查器的整体结构和 AI 提示词。
- 不修改 `data/audit/*/labels.toml`。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
- 测试结果（最后一行）：
- 真实 demo 验证：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
