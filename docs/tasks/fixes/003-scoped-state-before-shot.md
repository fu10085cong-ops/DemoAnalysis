# 003 开镜状态改读开枪前一 tick

状态：完成

## 目标

修复一个已确认的数据错误：所有 AWP 开枪都被判为"没开镜"。这个错误也让 AWP 的急停判断用错了最高速度。

## 背景（必须读）

- `docs/fields.md` 陷阱 23：开枪那一 tick 的 `is_scoped` 已经是开枪后的状态。
  - 已核实：AWP 开枪后自动退镜，`weapon_fire` 所在 tick 为 False，前 1～3 个 tick 为 True；
  - 在 ESL Vitality vs MOUZ Dust2 中，40 发 AWP 全部如此。
- `src/cs2coach/features/moments.py`：`_shot()`（第 282 行附近），以及 `engagement()` 中 `states = match.player_ticks(sid, start, tick)` 的取值范围。
- `src/cs2coach/features/aim_stats.py`：第 36–46 行附近，急停统计中的 `is_scoped`。
- `config/default.toml` 的 `[counter_strafe]`：`max_speed_scoped` 表。

## 要做的事

1. **`_shot()` 中的 `scoped`**：
   - 改为读开枪前一 tick（`tick - 1`）的 `is_scoped`；
   - 前一 tick 没有数据时，退回读开枪那一 tick；
   - `engagement()` 里 `states` 的取值范围要多包含一个 tick，否则交火第一枪读不到前一 tick；
   - 速度、下蹲、空中等其他字段**保持原样**，仍读开枪那一 tick。
2. **`aim_stats.py`** 的急停统计做同样的修改。
3. **最好抽成一个小函数**，例如 `scoped_before_shot(states, tick) -> bool`，两处共用。
4. **`docs/features.md`**：在急停（Counter-Strafing）和 `Shot.scoped` 的说明中写明"开镜状态取开枪前一 tick"，注明出处是陷阱 23。

## 验收标准

- [x] 新增测试（放在 `tests/test_moments.py` 或 `tests/test_visibility.py`，用 `tests/helpers/synthetic.py` 构造）：
  - 一发 AWP：开枪前一 tick `is_scoped=True`，开枪 tick 为 False → `Shot.scoped` 为 True，`max_speed` 取 `max_speed_scoped` 中 awp 的值；
  - 开枪前一 tick 没开镜 → `scoped` 为 False；
  - 交火第一枪恰好在交火窗口的第一个 tick → 仍然能读到前一 tick；
  - `aim_stats` 的急停统计对开镜的 AWP 使用开镜最高速度。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实 demo 验证：在 `data/demos/esl-pro-league-season-24-vitality-vs-mouz-bo5-HHVdkDbJrwRclevPphxD2-.rar`（`--demo-member vitality-vs-mouz-m3-dust2.dem`）上运行 `analyze`，分析一名用 AWP 的选手。
  - 可以从 `_moments.json` 中找 `weapon` 为 `awp` 的击杀者；
  - 完成报告中写明：该选手所有交火中，AWP 子弹 `scoped == True` 的比例。预期大多数为 True。
  - **不要加 `--ai`。**

## 范围外（不要做）

- 不重新生成 `src/cs2coach/baselines/pro_sample.json`（后续任务统一重建）。
- 不改视野计算（开镜后视野变窄的问题另行处理）。
- 不新增 AWP 相关的问题检测。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `features/aim.py` 新增共用函数 `scoped_before_shot`，优先读 tick-1，缺数据时读开枪 tick，均不可用时返回 False。
  - `features/moments.py` 的交火状态窗口多读前一 tick，`_shot` 使用共用函数；其他状态仍读开枪 tick。
  - `features/aim_stats.py` 同样改读开枪前的开镜状态；`features/models.py`、`docs/features.md` 同步说明并引用陷阱 23。
  - `tests/test_moments.py`、`tests/test_visibility.py`、`tests/test_aim.py` 新增 9 项测试，覆盖 AWP 退镜、未开镜、交火第一枪、缺失回退、其他状态取值及整场开镜速度选择。没有改写现有测试。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：183 passed，3 skipped（未设置真实 demo 测试环境变量）。
- 真实 demo 验证：
  - 运行 `.venv/Scripts/python.exe -m cs2coach analyze data/demos/esl-pro-league-season-24-vitality-vs-mouz-bo5-HHVdkDbJrwRclevPphxD2-.rar --demo-member vitality-vs-mouz-m3-dust2.dem --player 76561198113666193`，正常退出，未加 `--ai`。
  - ZywOo：28 回合、40 个瞬间；所有交火共 17 发 AWP，17 发 `scoped=True`，比例 **100%**，`max_speed` 均为开镜的 100 u/s。
  - 输出 `data/output/vitality-vs-mouz-m3-dust2_76561198113666193_moments.json`。原 demo 只读，未重新生成职业基准。
- 遗留问题 / 需要确认的地方：整场 Counter-Strafing 按原口径只含步枪，AWP 只参与逐枪速度选择、不加入该百分比；测试同时验证 AWP 选用开镜速度以及仍被排除在步枪统计外。未修改开镜视野及其他审查中发现的范围外问题；没有新增依赖。

## 审查意见（Claude 填写）

**审查通过（2026-10-15）。**开镜状态和速度选择正确，ZywOo 17 发 AWP 全部为开镜，符合预期。
