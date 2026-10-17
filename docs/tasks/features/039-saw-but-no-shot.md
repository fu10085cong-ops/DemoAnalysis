# 039 新问题"看见对手却没开枪"

状态：完成（审计待做）
难度：B（照现有规则的写法新增一条，加两个字段）
执行：Codex medium（Kimi 额度用完，按规则改由 Codex）
分支：`task/039-saw-but-no-shot`（独立工作目录 `/Users/parsival/Desktop/democheck-039`）

## 目标

2026-10-17 Claude 统计了 24 场业余比赛：只有 25% 的阵亡被已启用的规则解释了。没被解释的阵亡里，最大的一类是**看见了对手却没开枪就被打死**（514 次）。按"看见对手到阵亡隔了多久"分段，和 26 份职业 demo 对比（每 10 回合的次数）：

| 看见到阵亡 | 职业 | 业余 |
|---|---:|---:|
| 0.3 秒以内（来不及反应） | 0.73 | 0.39 |
| 0.6 秒以上 | 0.21 | 0.43（2.1 倍） |
| 0.6 秒以上，且阵亡前 2 秒没朝任何人开过枪 | 0.11 | 0.24（2.2 倍） |

0.6 秒以上一枪不开，说明在犹豫或者没反应过来，业余是职业的 2 倍多。本任务把它立为新问题。

## 背景（只读这些）

- `src/cs2coach/features/models.py`：`Engagement`、`DeathState`
- `src/cs2coach/features/moments.py`：`add_visibility`（`player_first_seen_tick` 在这里算），`death_state`（033 加换弹字段的写法），`p.shots`
- `src/cs2coach/features/problems.py`：`_SPECS`、`DETECTED_PROBLEMS`、`died_while_reloading` 一段的写法
- `config/default.toml` 的 `[problems]`、`[problems.exclusions]`；`src/cs2coach/config.py::_validate_problems`
- `config/problem_text.toml`、`src/cs2coach/report/evidence.py`

## 预写测试

Claude 已写好 `tests/test_saw_but_no_shot_039.py`，目标是让它全部通过，**不要修改它**。如果其他测试失效，先停下来，把测试名和原因写进完成报告，不要自行修改。唯一的例外：`tests/test_problem_frequency.py` 中列出全部已实现问题、或者默认启用规则的断言，可以加上新问题。

## 要做的事

1. **字段**：
   - `Engagement.seen_to_death_s: float | None`：只在死亡瞬间、`player_first_seen_tick` 有值时计算，等于（阵亡 tick − `player_first_seen_tick`）÷ tick 率，保留三位小数；
   - `DeathState.shots_last_2s: int`：`[阵亡 tick − no_shot_recent_seconds, 阵亡 tick]` 内，本人的枪械开枪次数，朝任何人都算，用 `p.shots`；不含投掷物、不含接管 bot 时。
2. **新问题 `saw_but_no_shot`**（看见对手却没开枪）：
   - 登记进 `_SPECS`（类别 `duel`，适用 `death`，可靠度 `medium`）和 `DETECTED_PROBLEMS`，默认启用；
   - 判定条件，全部满足才判：
     - 本次交火里本人没有开枪（`engagement.shots` 为空）；
     - `visibility_note` 为空，也就是视野正常；
     - `seen_to_death_s ≥ no_shot_min_seen_seconds`（0.6）；
     - `shots_last_2s == 0`。
   - `seen_to_death_s` 为空时不判，**不计入** missing：这种情况很常见，大多是根本没看见；
   - 排除条件：在 `[problems.exclusions]` 的 `through_smoke`、`wallbang`、`player_blinded_in_fight`、`killed_by_utility` 中都加上这条；
   - 证据：`seen_to_death_s`、`opponent_ttd_ms`（取 `opponent_time_to_damage_s`，没有时为 None）；阈值 `{"min_seen_seconds": 0.6, "source": "config"}`。
3. **配置**：`no_shot_min_seen_seconds = 0.6`、`no_shot_recent_seconds = 2.0`，写注释说明依据（职业和业余的对比），并加进校验。
4. **文字**：
   - `problem_text.toml` 加一节；
   - `evidence.py` 加一句证据，例如"看见对手 0.75 秒后阵亡，期间一枪没开"；
   - `docs/problem_catalog.md` 加一行；`docs/features.md` 写明两个字段的口径。

## 验收标准

- [ ] `tests/test_saw_but_no_shot_039.py` 全部通过。
- [ ] 所有命令带 `PYTHONPATH=src`：
  - 开发时：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q tests/test_saw_but_no_shot_039.py tests/test_problems.py tests/test_moments.py tests/test_problem_frequency.py`
  - 完成前跑一次全部测试：`PYTHONPATH=src /Users/parsival/Desktop/democheck/.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不要**运行 `build_baseline.py`、`audit_collect.py`，不读、不改 `data/`。这些由 Claude 做。

## 范围外（不要做）

- 不改其他规则、检查器、提示词。
- 不 commit、push，不切换分支。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：`models.py`、`moments.py`、`problems.py` 增加两个字段与检测规则；`config.py`、两个 TOML 文件补阈值、排除和文字；`evidence.py`、`docs/features.md`、`docs/problem_catalog.md` 补证据与口径；`tests/test_problem_frequency.py` 更新已实现规则数；`docs/tasks/README.md` 更新任务索引。
- 测试结果（最后一行）：`2 failed, 196 passed in 3.31s`（相关测试；依任务单要求，发现其他测试失效后未运行全量测试）。
- 遗留问题 / 需要确认的地方：`tests/test_saw_but_no_shot_039.py::test_exclusions`：死亡时被闪白的样例仍命中新规则，现有 `player_blinded_in_fight` 条件未覆盖此样例。`tests/test_problems.py::test_registry_context_and_markdown`：固定断言 `len(PROBLEMS) == 26`，新增规则后为 27；任务单仅允许修改 `tests/test_problem_frequency.py` 中的问题列表/数量断言，故未修改该测试。请确认是否允许调整 `tests/test_problems.py` 的该断言；确认后再修复致盲排除并运行全量测试。

## 审查意见（Claude 填写）

**审查通过（2026-10-17，Claude）。** Codex 按规则在两处测试失败时停下，没有自行修改测试。两处原因都是 Claude 写任务单时的疏漏，由 Claude 补上：
1. **被闪白**：`player_blinded_in_fight` 看的是交火过程中被闪，预写测试构造的是死的那一刻正被闪。规则里补上了 `d.blinded is not True`；
2. **问题总数**：`test_problems.py` 里写死了 26，改为 27。

Claude 复跑：829 passed、4 skipped。

