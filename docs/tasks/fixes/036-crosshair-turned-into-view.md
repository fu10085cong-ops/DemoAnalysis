# 036 准星：转身转进屏幕的对手不判

状态：完成
难度：A（视野几何）
执行：Codex，思考强度 high
分支：`task/036-crosshair-turn`

## 目标

第三轮审计中，"准星没提前放好"的 20 条样本有 3 条判错（`data/audit/2026-10-17-after032`，hit_key 末尾 8 位为 `8ae70698`、`1e363942`、`5cbc93dc`）。

这 3 条的对手早就在几何视线里，只是在玩家屏幕外。玩家转身时，对手才进入屏幕边缘，所以"初始偏差"约等于视野边缘的角度，分别是 41.6°、46.2°、49.3°。这不是预瞄位置的问题。

Claude 核对过的数据：在"第一次看见"之前，连续有几何视线（不要求在屏幕内）的 tick 数：

- 判错的 3 条分别是 58、128（回看上限）、110；
- 其余 17 条中，16 条是 0，1 条是 3。那条是 4 米内的拐角遭遇。

## 背景（必须读）

- `src/cs2coach/features/moments.py::add_visibility`：031 计算 `crosshair_initial_*` 和 `airborne_near_seen` 的那一段
- `src/cs2coach/features/visibility.py::Sight`：`sees(viewer, target, need_view=False)`、`states`
- `src/cs2coach/features/problems.py`：`active_conditions` 里 `airborne_near_seen` 的写法、`poor_crosshair_placement` 一段
- `src/cs2coach/baseline.py::weapon_stats` 中 `crosshair_initial_offset_m` 的筛选
- `config/default.toml` 的 `[problems]`、`[problems.exclusions]`

## 要做的事

1. **`Engagement` 新增字段 `opponent_in_sight_before_view_ticks: int | None`**：
   - 从 `player_first_seen_tick − 1` 往前数，本人对对手连续有几何视线（`need_view=False`）的 tick 数，最多数 `crosshair_turn_lookback_ticks` 个（默认 64）；
   - 只在 `crosshair_initial_offset_m` 有值时计算；
   - 用 `Sight.states` 一次取出这一段的状态，不要逐 tick 调用 `state_at`。
2. **新排除条件 `turned_into_view`**：
   - 该字段 ≥ `crosshair_turn_min_ticks`（默认 8，即 0.125 秒）时成立；
   - 在 `[problems.exclusions]` 登记，只用于 `poor_crosshair_placement`；
   - 加进 `config.py` 的排除条件名校验。
3. **基准**：`weapon_stats` 的 `crosshair_initial_offset_m` 分布另外排除 `turned_into_view`。
4. **配置**：`crosshair_turn_lookback_ticks = 64`、`crosshair_turn_min_ticks = 8`，写注释，并加进正整数校验。
5. **文档**：`docs/features.md` 准星一节、`docs/problem_catalog.md` 准星一行。

## 验收标准

- [ ] 单元测试，用 `tests/helpers/synthetic.py` 和 `WallChecker` 构造：
  - **对手从墙后走出**：字段为 0，照常判；
  - **对手一直在视线里，玩家转身后才进入屏幕**：字段 ≥ 8，不判，计入 `skipped["poor_crosshair_placement/turned_into_view"]`；
  - **边界**：7 不排除，8 排除；回看上限生效。
- [ ] 真实 demo：对上面 20 条样本，按 `labels.toml` 中 `poor_crosshair_placement` 的条目，逐条输出新字段。预期只有那 3 条 ≥ 8。
- [ ] 运行 `scripts/build_baseline.py` 和 `scripts/audit_collect.py --name 2026-10-17-after036`，贴出准星的职业 p75（7 把武器）新旧值，以及业余命中数新旧值。
- [ ] 全部测试通过，报告只贴最后一行；`privacy_scan.py` 退出码为 0；完成报告中玩家名替换成"（已匿名）"。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改其他规则。
- 不改"第一次看见"本身的算法：它是 TTD 等多个指标共用的。
- 不修改 `data/audit/*/labels.toml`。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：`features/models.py`、`moments.py` 新增首次进屏前连续几何视线 tick 数；`problems.py`、`config.py`、`config/default.toml` 登记准星专用排除及阈值；`baseline.py` 和 `baselines/pro_sample.json` 重算初始偏差基准；`docs/features.md`、`docs/problem_catalog.md` 更新口径；`tests/test_visibility.py`、`test_baseline.py`、`test_config.py` 覆盖墙后出现、转身、7/8 tick、64 tick 上限、基准和配置。
- 测试结果（最后一行）：`809 passed, 3 skipped in 20.30s`。`privacy_scan.py` 退出码 0，身份信息命中 0 处。
- 真实 demo 验证：`data/audit/2026-10-17-after032/labels.toml` 的 20 条准星样本来自 13 份 demo：5E 的 `g161-20260929135849884675929_de_dust2.dem`、`g161-20260929221613531950574_de_inferno.dem`、`g161-20261009192530992825357_de_dust2.dem`、`g161-b-20260919143305694257572_de_dust2.dem`、`g161-n-20260928224715762874577_de_inferno.dem`、`g201-20260920174509013415128_de_dust2.dem`、`g201-20260927213614106266461_de_dust2.dem`；完美的 `9210321637397607564_0.dem`、`9210673495171964302_0.dem`、`9212010755172502668_0.dem`、`9213629233256574988_0.dem`、`9215177492731139980_0.dem`、`9216021770063315084_0.dem`。逐条新字段（hit_key 末 8 位 → tick 数）：`0752b1cf=0, 1e16d1e5=0, 1e363942=64, 22fa8c7d=0, 354a4aea=0, 4197cb7d=0, 4a8fae4e=3, 4ac267b6=0, 5cbc93dc=64, 67edaf22=0, 7b15a55b=0, 7d3f2152=0, 7d5afb2a=0, 8ae70698=58, 9438c71b=0, c5a8a87c=0, d53beed6=0, de0b2038=0, e447bcb2=0, fdb765d5=0`；只有指定 3 条 ≥ 8。职业初始偏差 p75（米，旧→新）：AK-47 `1.85→1.63`、M4A4 `1.99→1.81`、M4A1-S `1.74→1.56`、AWP `1.18→1.17`、Glock `1.84→1.56`、USP-S `1.43→1.40`、沙鹰 `1.33→1.28`。`build_baseline.py` 26/26 场成功；035 规则配旧基准重算的业余准星命中 825 条，`audit_collect.py --name 2026-10-17-after036` 为 814 条（24/24 场成功，0 失败）。
- 遗留问题 / 需要确认的地方：无。

## 审查意见（Claude 填写）

**审查通过（2026-10-17，Claude）。** 几何计数用 `Sight.states` 一次取出整段状态，排除条件、基准筛选和配置校验都和任务单一致；20 条样本中只有指定的 3 条被排除。

排除转身的交火后，职业准星 p75 变严了，例如 AK 从 1.85 米降到 1.63 米：之前混在职业样本里的转身情况把阈值拉高了。所以业余命中只从 825 条降到 814 条。新阈值下的有用率留到下一轮审计复测。

