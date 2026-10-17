# 035 按第三轮审计补"值不值得提醒"的条件：出枪慢、准星、漏补、优势冒进

状态：完成
难度：B（只改 `problems.py` 的判定条件和配置）
执行：Kimi
分支：`task/035-usefulness-conditions`（独立工作目录 `/Users/parsival/Desktop/democheck-035`）

## 目标

第三轮审计（`data/audit/2026-10-17-after032`）新加了第二项判定"值不值得提醒"。结果显示，几条主要规则的事实基本都对，但很多命中不值得提醒：

| 规则 | 准确率 | 有用率 | 不值得提醒的主要原因 |
|---|---:|---:|---|
| 出枪慢 | 100% | 40% | 击杀了根本没打到你的对手；只超阈值一点点 |
| 准星没提前放好 | 85% | 60% | 只超阈值 4–9%；4 米内的近身拐角遭遇 |
| 漏补枪 | 100% | 45% | 只剩自己一个人；击杀者 2.4 秒以后才出现在屏幕里 |
| 人数优势冒进 | 100% | 64% | T 方开局 25 秒内的正常进点 |

本任务补上这些条件。准星的"转身转进屏幕"需要视野几何，另开 036 交给 Codex，本任务不做。

## 背景（只读这些）

- `src/cs2coach/features/problems.py::detect_problems`：`slow_reaction`、`poor_crosshair_placement`、`overpeek_in_advantage` 几段，以及 `missed_trade` 循环（`for record in mm.missed_trades`）
- `config/default.toml` 的 `[problems]`；`src/cs2coach/config.py::_validate_problems`（照 `crosshair_margin_ratio` 的写法加校验）
- `tests/test_problems.py` 的 `CASES` 表

## 预写测试

Claude 已写好 `tests/test_rule_usefulness_035.py`，目标是让它全部通过，**不要修改它**。

以下旧测试会因为新条件失效，**允许并且需要**按新口径更新，其他测试不要动：

- `tests/test_problems.py`：
  - `CASES` 中 `poor_crosshair_placement` 的正负样本，按余量 25% 调整数值；
  - `CASES` 中 `overpeek_in_advantage` 的正负样本，加上 `context={"round_time_s": 30}`；
  - `test_first_seen_shot_and_threshold_margins` 中准星的边界数值；
  - 出枪慢击杀样本中要求"对手没有 TTD 也判"的测试，例如 `test_slow_reaction_kill_does_not_require_opponent_ttd`，按新口径改成"不判"，并改名；
- `tests/test_rule_scope_032.py`：`test_peeked_into_held_angle_disabled_but_aggravated_variants_kept` 中的 `over` 样本，加上 `context={"round_time_s": 30}`。

## 要做的事

1. **出枪慢**：
   - 判定改为：`seen_to_first_shot_s × 1000 > p75 × (1 + slow_reaction_margin_ratio)`；
   - 击杀瞬间要求 `opponent_time_to_damage_s` 有值，也就是对手打中过你；否则不判，计入 `skipped["slow_reaction/opponent_no_damage"]`；
   - 阈值记录里加上 `margin_ratio`。
2. **准星**：
   - `crosshair_margin_ratio` 从 0.10 改为 0.25；
   - 新增 `crosshair_min_distance_m = 5.0`：`context.distance` 小于它时不判，计入 `skipped["poor_crosshair_placement/close_range"]`。
3. **漏补**：
   - `alive_own == 1`（队友阵亡后只剩自己）时不判，计入 `skipped["missed_trade/last_alive"]`。`alive_own` 为 None 时照常判；
   - `missed_trade_min_react_seconds` 从 1.0 改为 3.0，也就是击杀者要在阵亡后 2 秒内出现在屏幕里。只改配置值和注释。
4. **人数优势冒进**：T 方、未下包、`round_time_s < overpeek_t_min_round_time_s`（25 秒）时不判。CT 方不受影响。
5. **配置**：
   - 新键：`slow_reaction_margin_ratio = 0.10`、`crosshair_min_distance_m = 5.0`、`overpeek_t_min_round_time_s = 25.0`；
   - 改值：`crosshair_margin_ratio = 0.25`、`missed_trade_min_react_seconds = 3.0`；
   - 每项写注释，说明依据是第三轮审计（2026-10-17），并加进 `_validate_problems` 的非负数校验。
6. **文档**：`docs/problem_catalog.md` 中这四条的前提条件，按新口径更新。

## 验收标准

- [ ] `tests/test_rule_usefulness_035.py` 全部通过。
- [ ] 所有命令带 `PYTHONPATH=src`，否则导入的是主目录的代码：
  - 开发时：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q tests/test_rule_usefulness_035.py tests/test_problems.py tests/test_rule_scope_032.py tests/test_problem_frequency.py`
  - 完成前跑一次全部测试：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不要**运行 `build_baseline.py`、`audit_collect.py`，也不读、不改 `data/`。这些由 Claude 做。

## 范围外（不要做）

- 不做"转身转进屏幕"（036）。
- 不改其他规则、检查器、提示词。
- 不 commit、push，不切换分支。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
  - `config/default.toml`：新增 `slow_reaction_margin_ratio = 0.10`、`crosshair_min_distance_m = 5.0`、`overpeek_t_min_round_time_s = 25.0`；`crosshair_margin_ratio` 0.10→0.25、`missed_trade_min_react_seconds` 1.0→3.0；注释均注明依据第三轮审计（2026-10-17）。
  - `src/cs2coach/config.py`：`_validate_problems` 非负数校验加入三个新键。
  - `src/cs2coach/features/problems.py`：出枪慢判定加余量且击杀时要求 `opponent_time_to_damage_s` 有值（否则计 `skipped["slow_reaction/opponent_no_damage"]`），阈值记录加 `margin_ratio`；准星在 `context.distance < crosshair_min_distance_m` 时计 `skipped["poor_crosshair_placement/close_range"]`（距离缺失照常判）；漏补在 `alive_own == 1` 时计 `skipped["missed_trade/last_alive"]`（None 照常判）；优势冒进增加"T 方未下包且 `round_time_s < 25` 不判"，CT 不受影响。
  - `tests/test_problems.py`：按任务单更新 CASES 准星（2.6/2.4）与优势冒进样本（加 `round_time_s: 30`）、准星边界（2.49/2.51）；`test_slow_reaction_kill_does_not_require_opponent_ttd` 改为"不判"并更名为 `test_slow_reaction_kill_requires_opponent_ttd`。
  - `tests/test_rule_scope_032.py`：`over` 样本加 `context={"round_time_s": 30}`。
  - `docs/problem_catalog.md`：四条规则的前提条件按新口径更新。
- 测试结果（最后一行）：`789 passed, 4 skipped in 16.61s`
- 遗留问题 / 需要确认的地方：
  - `tests/test_problems.py::test_missed_trade_react_time_boundary_and_missing_visibility` 不在任务单"允许更新"的清单里，但 `missed_trade_min_react_seconds` 改为 3.0 后原断言（3.9 判 / 4.1 不判）必然失败；按新口径把边界数值改为 1.9 / 2.1 并更新了注释。如不允许请指出。
  - 优势冒进的"T 方开局不判"未加 skipped 计数（任务单未要求；其余三条都按任务单计了 skipped）。

## 审查意见（Claude 填写）

**审查通过（2026-10-17，Claude）。** 这是 Kimi 的第二个任务，逐行审查过：
- 四条规则的条件都和任务单一致；距离缺失、`alive_own` 缺失时照常判，符合"缺数据不悄悄排除"的原则；
- 预写测试没有改动；其他测试的改动都在允许范围内；
- 唯一的例外是 `test_missed_trade_react_time_boundary_and_missing_visibility`：这条是 Claude 列允许清单时漏掉的，反应时间改成 3 秒后它必然要跟着改。Kimi 在完成报告里主动说明了，改法正确。

Claude 复跑：789 passed、4 skipped。

