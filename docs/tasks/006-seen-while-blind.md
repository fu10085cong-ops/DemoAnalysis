# 006 被闪白时不算"看见"

状态：待做（**请在 004 之前做**：它改变 TTD 和准星的统计口径，004 生成职业基准时要用新口径）

## 目标

修复一个已确认的误判。视野计算只看几何上有没有被遮挡，没有考虑看的人当时正被闪白。

例子：5E Mirage 玩家 `76561199711881069` 的 `R11-death`。事实卡写着"你先看见对手，领先 0.06 秒"，但他在 tick 73089 被对手闪中 4.26 秒，50 个 tick 后就被击杀了。被闪白时实际上什么都看不见。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `config/default.toml` 的 `[visibility]`：烟雾排除的做法（第 82–86 行附近的注释）。**本任务照烟雾的思路处理**：第一次看见的那一刻，看的人正被闪白 → 这一方的视野数据不可靠
- `src/cs2coach/features/moments.py`：
  - `add_visibility()`：`visibility_note`、`seen_first_lead_s`、TTD 和准星在这里计算；
  - `blind_info()`：001 新增，致盲重叠的算法可以参考。
- `src/cs2coach/features/aim_stats.py`：整场 TTD 和准星统计如何排除 `smoke`
- `src/cs2coach/report/facts.py`、`src/cs2coach/tools/digest.py`：`VISIBILITY_NOTES`
- `docs/features.md`：视野相关指标的定义

已确认的数据事实：

- `blinds` 表的列和剩余致盲时间的算法，见 `docs/tasks/001-flash-and-utility-data.md` 的"背景"一节；
- 时长低于 `[moments].min_blind_seconds` 的致盲不算。

## 要做的事

1. **新增配置** `[visibility].blind_min_remaining_seconds`，暂定 1.0。注释写明：
   - 闪光效果在最后一段逐渐消退，剩余时间较短时已经能看清部分画面；
   - 这个值是暂定的，待用数据校准。

2. **新增函数** `blinded_at(match, p, steamid, tick) -> bool`：该 tick 是否处于剩余时间 ≥ `blind_min_remaining_seconds` 的致盲中，致盲时长要 ≥ `min_blind_seconds`。

3. **在 `add_visibility()` 中**：
   - 目标玩家第一次看见对手的那一 tick（`player_first_seen_tick`）如果他正被闪白：
     - `visibility_note` 设为新值 `"blind"`；
     - 目标玩家的 TTD、`seen_to_first_shot_s`、`first_shot_to_damage_s`、`crosshair_placement_deg` 置为 None，和 `smoke` 的处理一致。
   - 对手第一次看见目标玩家的那一 tick 如果对手正被闪白：`opponent_time_to_damage_s` 置为 None。
   - 任一方在自己第一次看见时被闪白：`seen_first_lead_s` 置为 None，相关的 `saw_opponent_first` / `opponent_saw_first` 标签也就不会产生。
   - **新增两个字段**记录原因：`Engagement.player_seen_while_blind: bool | None = None`、`opponent_seen_while_blind: bool | None = None`。没有对应的 first_seen tick 时为 None。
   - `visibility_note` 的优先级：已经是 `smoke` 时保持 `smoke`。
   - 第一次看见的 tick 本身仍然保留，不要置空，方便核对。

4. **整场统计**：`aim_stats.py` 把 `visibility_note == "blind"` 的交火从 TTD 和准星统计中排除，和 `smoke` 一样。

5. **显示**：
   - `VISIBILITY_NOTES` 新增 `"blind"`：`第一次看见对手时你正被闪白，这次交火不计算反应时间和准星`；
   - 事实卡中，对手被闪白时也加一句：`对手第一次看见你时正被闪白，不计算对手的反应时间。`

6. **文档**：
   - `docs/features.md`：视野一节补充这条规则，并写明与 Leetify 的不同（Leetify 没有公开是否这样处理）；
   - `config/default.toml`：注释写清楚。

## 验收标准

- [ ] 测试（`tests/test_visibility.py`，复用其中的 `ticks()`、`duel()` 和 `WallChecker` 场景）：
  - 目标玩家第一次看见时被闪白（剩余 ≥ 阈值）：`visibility_note == "blind"`，TTD 和准星为 None，`player_seen_while_blind` 为 True，`seen_first_lead_s` 为 None，没有 `saw_opponent_first` 标签；
  - 剩余时间低于阈值：不受影响；
  - 致盲时长低于 `min_blind_seconds`：不受影响；
  - 对手第一次看见时被闪白：只有 `opponent_time_to_damage_s` 为 None，目标玩家自己的指标不受影响；
  - `smoke` 的优先级高于 `blind`；
  - `aim_stats` 排除 `blind` 的交火。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 真实 demo 验证（**不加 `--ai`**）：重跑 5E Mirage，玩家 `76561199711881069`。
  - `R11-death` 的事实卡不再写"你先看见对手"，并出现被闪白的说明；
  - 完成报告中列出：整场有多少次交火因 `blind` 被排除；TTD 中位数在修改前后各是多少。

## 范围外（不要做）

- 不处理"看到部分模型却判为没看到"（还在等用户提供例子）。
- 不改 `BlindInfo`、`death_state` 的现有字段。
- 不重新生成职业基准（由紧接着的 004 生成）。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
