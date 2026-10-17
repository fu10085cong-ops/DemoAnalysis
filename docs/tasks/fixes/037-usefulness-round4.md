# 037 按第四轮审计补的条件：出枪慢、漏补、优势冒进、准星、换弹时被打死

状态：完成
难度：B（改判定条件，并给 `DeathState` 加一个字段）
执行：Kimi（代码和测试；额度在改旧测试时用完），Claude 补完文档并审查
分支：`task/037-usefulness-round4`（独立工作目录 `/Users/parsival/Desktop/democheck-037`）

## 目标

第四轮审计（`data/audit/2026-10-17-after036`）中，五条规则的准确率都是 100%，但还有一部分命中不值得提醒：

| 规则 | 有用率 | 不值得提醒的主要原因 | 本任务的改法 |
|---|---:|---|---|
| 出枪慢 | 85% | 击杀时虽然慢，但仍然比对手先打中 | 击杀时也要求对手的 TTD 比自己短，和死亡时的条件一样 |
| 漏补 | 80% | 击杀者只在屏幕里一闪而过（0.27–0.34 秒） | 屏幕内累计时间 0.25 秒 → 0.4 秒 |
| 人数优势冒进 | 90% | T 方未下包、只剩 1 名对手时，T 方本来就要主动找人 | 这种情况不判 |
| 准星 | 65% | 准星偏了，但"看见→开枪"不慢，没有代价 | 击杀时如果"看见→开枪"不超过职业 p75，不判 |
| 换弹时被打死 | 45% | 弹匣只剩不到三成，换弹是必要的；换弹刚开始就阵亡 | 弹匣至少剩三成，且换弹开始至少 0.3 秒后才阵亡，才判 |

## 背景（只读这些）

- `src/cs2coach/features/problems.py::detect_problems`：`slow_reaction`、`poor_crosshair_placement`、`overpeek_in_advantage`、`died_while_reloading` 几段，以及 `missed_trade` 循环
- `src/cs2coach/features/moments.py::death_state`：033 计算 `ammo_before_reload` 的那一段
- `src/cs2coach/features/models.py::DeathState`
- `config/default.toml` 的 `[problems]`；`src/cs2coach/config.py::_validate_problems`
- `src/cs2coach/report/evidence.py`：`died_while_reloading` 的证据句

## 预写测试

Claude 已写好 `tests/test_rule_usefulness_037.py`，目标是让它全部通过，**不要修改它**。

以下旧测试会因为新条件失效，**允许并且需要**按新口径更新：
- `tests/test_problems.py` 中 `died_while_reloading` 的样本，例如"弹匣还有 1 发时判"，按"三成"和"0.3 秒"更新；
- 033 加的、和 `died_while_reloading` 判定或证据句有关的测试，所在文件以实际为准。

如果还有其他测试失效，**先停下**，把失败的测试名和原因写进完成报告，不要自行修改。

## 要做的事

1. **出枪慢**：击杀瞬间要求 `opponent_time_to_damage_s < time_to_damage_s`；对手更慢时不判，计入 `skipped["slow_reaction/opponent_slower"]`。035 的 `opponent_no_damage` 照旧保留。
2. **漏补**：`missed_trade_min_view_seconds` 从 0.25 改为 0.4，只改配置值和注释。
3. **人数优势冒进**：T 方、未下包、`alive_enemy == 1` 时不判。
4. **准星**：击杀瞬间，如果 `seen_to_first_shot_s × 1000 ≤` 职业同武器的 `seen_to_first_shot_ms` p75，不判，计入 `skipped["poor_crosshair_placement/no_cost"]`。阈值取不到时照常判。死亡瞬间不受影响。
5. **换弹时被打死**：
   - `DeathState` 新增 `magazine_size: int | None`：**本场比赛**中，换弹开始前一 tick 本人手持的那把武器（按 `active_weapon_name` 匹配），在所有玩家、所有 tick 的 `active_weapon_ammo` 里出现过的最大值。没有数据时为 None。**不要写死各武器的弹匣容量**。只在 `reloading_at_death` 为真时计算；同一场比赛的最大值算一次后缓存，不要每次阵亡都扫描全部 tick。
   - 判定条件：`ammo_before_reload ≥ magazine_size × reload_min_ammo_fraction`，并且 `reload_started_s ≥ reload_min_seconds`；`reload_min_ammo_left` 保留。`magazine_size` 为 None 时计入 `missing`。
   - 证据里加上 `magazine_size`；证据句改为"换弹前弹匣还有 N / M 发"。
6. **配置**：
   - 新键：`reload_min_ammo_fraction = 0.30`、`reload_min_seconds = 0.3`；
   - 改值：`missed_trade_min_view_seconds = 0.4`；
   - 每项写注释，说明依据是第四轮审计（2026-10-17），并加进校验。
7. **文档**：`docs/problem_catalog.md` 中这五条的前提条件。

## 验收标准

- [ ] `tests/test_rule_usefulness_037.py` 全部通过。
- [ ] 所有命令带 `PYTHONPATH=src`：
  - 开发时：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q tests/test_rule_usefulness_037.py tests/test_problems.py tests/test_moments.py`
  - 完成前跑一次全部测试：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不要**运行 `build_baseline.py`、`audit_collect.py`，也不读、不改 `data/`。

## 范围外（不要做）

- 不改其他规则、检查器、提示词。
- 不 commit、push，不切换分支。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：Kimi 按任务单改了 `problems.py`、`moments.py`（`_magazine_size`，按武器缓存）、`models.py`、`evidence.py`、`config/default.toml`、`config.py`，以及 `test_problems.py` 中允许的两处换弹样本。额度用完后，Claude 补了 `docs/problem_catalog.md`。
- 测试结果（最后一行）：814 passed, 4 skipped。
- 遗留问题 / 需要确认的地方：无。

## 审查意见（Claude 填写）

**审查通过（2026-10-17）。** 五项条件都和任务单一致；弹匣容量从本场数据里取最大值，按武器缓存，没有写死；预写测试没有改动，旧测试只改了允许的范围。
