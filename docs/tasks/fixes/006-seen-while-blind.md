# 006 被闪白时不算"看见"

状态：完成

## 目标

修复一个已确认的误判。视野计算只看几何上有没有被遮挡，没有考虑看的人当时正被闪白。

例子：5E Mirage 玩家 `76561190000001002` 的 `R11-death`。事实卡写着"你先看见对手，领先 0.06 秒"，但他在 tick 73089 被对手闪中 4.26 秒，50 个 tick 后就被击杀了。被闪白时实际上什么都看不见。

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

- `blinds` 表的列和剩余致盲时间的算法，见 `docs/tasks/features/001-flash-and-utility-data.md` 的"背景"一节；
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

- [x] 测试（`tests/test_visibility.py`，复用其中的 `ticks()`、`duel()` 和 `WallChecker` 场景）：
  - 目标玩家第一次看见时被闪白（剩余 ≥ 阈值）：`visibility_note == "blind"`，TTD 和准星为 None，`player_seen_while_blind` 为 True，`seen_first_lead_s` 为 None，没有 `saw_opponent_first` 标签；
  - 剩余时间低于阈值：不受影响；
  - 致盲时长低于 `min_blind_seconds`：不受影响；
  - 对手第一次看见时被闪白：只有 `opponent_time_to_damage_s` 为 None，目标玩家自己的指标不受影响；
  - `smoke` 的优先级高于 `blind`；
  - `aim_stats` 排除 `blind` 的交火。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实 demo 验证（**不加 `--ai`**）：重跑 5E Mirage，玩家 `76561190000001002`。
  - `R11-death` 的事实卡不再写"你先看见对手"，并出现被闪白的说明；
  - 完成报告中列出：整场有多少次交火因 `blind` 被排除；TTD 中位数在修改前后各是多少。

## 范围外（不要做）

- 不处理"看到部分模型却判为没看到"（还在等用户提供例子）。
- 不改 `BlindInfo`、`death_state` 的现有字段。
- 不重新生成职业基准（由紧接着的 004 生成）。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `config/default.toml` 新增暂定的剩余致盲阈值 1.0 秒，注明末段消退和待数据校准。
  - `features/visibility.py` 放置共用 `blinded_at(match, p, steamid, tick)`；`features/moments.py` 导入使用（也可从该模块导入此函数），`features/aim_stats.py` 对每次遭遇使用同一规则，避免循环导入和遗漏非致命遭遇。
  - `features/models.py` 新增双方可空的被闪白原因字段；`add_visibility` 保留几何首次可见 tick，过滤双方相应指标与先看见标签，烟雾原因优先。
  - `tools/digest.py`、`report/facts.py` 显示原因；`docs/features.md` 说明口径及 Leetify 未公开同类处理。
  - `tests/test_visibility.py` 新增 9 项测试，覆盖阈值边界、短闪、未来记录、双方独立过滤、烟雾优先、无起点、配置覆盖和整场统计。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：275 passed，3 skipped（未设置真实 demo 测试环境变量）。
- 真实 demo 验证：
  - 重跑指定 5E Mirage `g173-n-20261014223216271995270_de_mirage.dem`，目标 `76561190000001002`，正常生成 JSON 和事实卡，未加 `--ai`，原 demo 只读。
  - 37 个瞬间中 `visibility_note=blind` 共 **1** 次（R11-death）；本人被闪白标记 1 次、对手被闪白标记 1 次。
  - 整场 TTD 中位数：修改前 **500 ms（17 次遭遇）**，修改后 **500 ms（17 次遭遇）**。R11 本人未造成伤害，原本就不是整场 TTD 的样本，故中位数不变。
  - R11：几何首次可见 tick 73111、对手首次可见 tick 73115 均保留；`player_seen_while_blind=True`、`seen_first_lead_s=None`、`visibility_note=blind`。事实卡已不再出现“你先看见对手”，新增“第一次看见对手时你正被闪白，这次交火不计算反应时间和准星。”
- 遗留问题 / 需要确认的地方：阈值仍待数据校准；未处理部分模型的几何误判，未改 BlindInfo/死亡状态或其他枪法口径，未新增依赖、未重建职业基准。首次可见 tick 在烟雾排除时也保留作核对，烟雾的指标排除规则不变。真实 demo 中两名其他玩家原有约 93%、94% 覆盖率警告仍保留。用户已明确将执行顺序调整为 006 → 004 → 005。

## 审查意见（Claude 填写）

**审查通过（2026-10-15）。** R11-death 的处理正确，阈值边界和烟雾优先级的测试完整。

另有一处超出任务单的改动：`player_first_seen_tick` 和 `opponent_first_seen_tick` 现在无条件保留，在 `smoke` 时也写入，原来只在有效时才写入。保留下来方便核对，可以接受。但给 AI 的字段说明需要注明它们在 `smoke` 和 `blind` 时不可靠，这项并入 004 的第 1 轮修改。
