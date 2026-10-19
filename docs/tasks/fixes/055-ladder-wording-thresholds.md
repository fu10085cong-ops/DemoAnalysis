# 055 页面口径统一、阶梯门槛与"相对谁"

状态：完成
难度：B（照现有写法修改，测试已预写）
执行：Kimi（额度不足时改 MiMo），在独立工作目录 `C:\Users\David\DemoAnalysis-055` 中
分支：`task/055-wording-thresholds`

## 目标

外部审查（2026-10-19）指出多场报告页有几处会让玩家误解，产品负责人确认按下面的口径修改：

1. **同一指标只有一个数**：类别面板"枪法"类里的命中速度、预瞄、急停、压枪，与段位阶梯显示同一个值（典型单场）。现在一个是 504 毫秒、一个是 484 毫秒。
2. **"达到某档" = 不差于该档的标准**，与阶梯上蓝点越过刻度一致。去掉"较弱一侧四分位"的宽松门槛（`reach_quantile`）。
3. **每档至少 8 名独立玩家才进入阶梯**（`[ladder] min_players = 8`）。不够的分段不画刻度，页脚注明"不足 8 名，暂不进入阶梯"。
4. **阶梯文字**：无法排序时写"现有基准无法稳定分级"（原来"段位间没有明显差别"说得过满）；达到时写"已达到{分段}标准（{标准值} {单位}）"。
5. **结论始终写明"相对谁"**：以"相对{分段}，"开头；当前对比分段没有明显偏多的问题、但和职业比有时，再加一句"相对职业，最该练的是……"。避免把"没有明显多于 A 段"读成"不用练"。单场和多场都改。
6. **小样本显示分子 / 分母**：比例类指标分母少于 `[panel] small_sample_below = 20` 时，显示"9/9（样本较少）"而不是 100%。
7. **比赛表现与技能分开**：面板指标新增类型"表现"（击杀、伤害、残局与首杀后的胜负结果），不进阶梯，名称后标"（比赛表现）"。

## 背景（必须读）

- `src/cs2coach/features/ladder.py`：`ladder_points`、`ladder_position`
- `src/cs2coach/report/page_data.py`：`_ladder_text`、`_verdict`、`_ladder_verdict`、`build_summary_page`（阶梯部分、`ladder_axes`、类别面板 `categories`、`view.panel_values`）
- `src/cs2coach/report/page_template.html`：`drawLadder`、`drawCategories`
- `src/cs2coach/features/panel.py`：`PANEL_METRICS`（`kind`）、`panel_sample`
- `src/cs2coach/features/radar.py::typical_player_metrics`
- `scripts/build_tier_baseline.py`：`per_player` 只为 `kind == "水平"` 的面板指标计算
- `config/default.toml` 的 `[ladder]`、`[panel]`；`src/cs2coach/config.py` 对 `ladder` 的校验

## 要做的事

1. **配置**：`[ladder]` 删除 `reach_quantile`，新增 `min_players = 8`（注释写明 2026-10-19 产品负责人确定）；`[panel]` 新增 `small_sample_below = 20`。`config.py` 相应修改校验。
2. **`ladder.py`**：
   - `ladder_points(metric, tiers, min_players=8)`：跳过 `per_player.n_players < min_players` 的分段；去掉 `reach_quantile` 参数和重算分位数的代码。`q25`/`q75` 仍原样带出（留给以后画参考范围）
   - `ladder_position`：`reached` = 数值不差于其标准的最高一档（越高越好：`value >= standard`；越低越好：`value <= standard`）
3. **`page_data.py`**：
   - 所有 `ladder_points(...)` 调用改传 `cfg["ladder"]["min_players"]`
   - `page.ladder_note`：在现有说明后，对有 `level` 但人数不足的分段加"{分段名}：{n} 名玩家，不足 {min_players} 名，暂不进入阶梯"
   - `_ladder_text`：`ordered = False` → "现有基准无法稳定分级"；达到 → "已达到{名称}标准（{标准值} {单位}）"，后面"· 下一档 …"保留；一档都没达到 → "未达到{最低一档名称}标准（…）"
   - 结论（`_verdict`、`_ladder_verdict`）：开头加"相对{分段名}，"，原句"这段时间没有检出明显多于{分段}的问题"改为"相对{分段名}，这段时间没有明显偏多的问题"（单场为"这场"）；当前分段没有 bad 问题、当前分段不是职业、且职业分段存在时，按职业分段的问题频率再判断一次，有 bad 就追加"相对职业，最该练的是{问题}：{N 场里 / 这场}出现 {次数} 次，按职业的频率约 {预期} 次。"（与现有"最该练的是"句式相同）。雷达 / 阶梯部分的句子不变，阶梯模式下"雷达上最弱的是"改为"阶梯上最弱的是"
   - 类别面板：枪法类这四项（`time_to_damage_ms`、`crosshair_offset_m`、`counter_strafing_pct`、`spray_accuracy_pct`）的"你"改用与 `ladder_axes` 相同的典型单场值；各分段的 `view.panel_values` 中这四项改用该分段 `per_player.metrics[...]["standard"]`（没有时退回原值）
   - 类别面板每项：比例类（有分母、单位为 %）且分母 < `small_sample_below` 时加 `count_text = "{分子}/{分母}（样本较少）"`；其他情况不加这个键
4. **`panel.py`**：以下指标的 `kind` 改为 `"表现"`：`kpr`、`kpr_won`、`adr`、`adr_won`、`kill_round_pct`、`multikill_round_pct`、`clutch_win_pct`、`clutch_1v1_win_pct`、`opening_round_win_pct`。"表现"类不算阶梯（`page_data` 只为"水平"类算 `ladder`，现有逻辑已经如此，确认即可）
5. **模板**：类别面板名称后缀——风格类"（风格）"、表现类"（比赛表现）"；有 `count_text` 时"你"一列显示它。阶梯区的说明文字如有"段位间没有明显差别"一并改掉
6. **文档**：`docs/features.md` 的"段位阶梯"与"类别面板"两节按上面更新（达到的判定、8 名门槛、表现类、小样本显示、结论写明相对谁）

## 预写测试（不得修改）

`tests/test_wording_thresholds_055.py`。Claude 已确认：改动前 9 项全部失败，原因都是功能未实现（`reached` 仍用四分位、没有 `min_players`、文字是旧说法、面板枪法四项不是典型单场、没有 `small_sample_below`、没有"表现"类、结论没有"相对…"）。认为测试有错时停下来写进完成报告，不要改测试。

**允许修改的旧测试**（会因新口径失败）：`tests/test_tier_ladder_053.py` 中按旧四分位门槛断言 `reached` 的那几行（例如 `ladder_position(17, points, True).reached.level == 2`），以及其他断言旧文字（"段位间没有明显差别"、"没有检出明显多于"）的测试。改了哪些、为什么，在完成报告中逐条列出。

## 运行测试的方法（独立工作目录专用）

本目录没有自己的 `.venv`，用主目录的 Python，并让它导入本目录的代码（PowerShell）：

```powershell
$env:PYTHONPATH = "C:\Users\David\DemoAnalysis-055\src"
C:\Users\David\DemoAnalysis\.venv\Scripts\python.exe -m pytest -q tests/test_wording_thresholds_055.py -p no:cacheprovider --basetemp C:\Users\David\DemoAnalysis-055\.pytest-tmp
```

完成前跑一次全部测试（去掉文件名）。本目录没有 `data/`，依赖真实 demo 或局域网共享的测试失败属已知情况，在报告中注明即可。

## 验收标准

- [ ] `tests/test_wording_thresholds_055.py` 全部通过
- [ ] 全部测试通过（除已知的环境类失败），报告只贴最后一行
- [ ] 真实数据验证由 Claude 在主目录完成（重建基准、生成多场汇总、截图核对页面）

## 范围外（不要做）

- 不改问题检测、问题频率的分母（另有任务 056）
- 不改基准文件；不重建基准
- 不做综合评分、分级
- 不访问本工作目录以外的路径（主目录的 `.venv` 只用来运行 Python）；不运行 `--ai`

---

## 完成报告（实现方填写，尽量简短）

> Kimi 完成了全部代码与大部分文档后触发 5 小时额度上限（403），未写完成报告；以下由 Claude 根据改动整理。

- 改动的文件及原因：`config/default.toml`、`config.py`（删 `reach_quantile`，加 `ladder.min_players`、`panel.small_sample_below`）；`features/ladder.py`（`ladder_points` 按人数门槛筛选，`reached` 改为不差于标准）；`features/panel.py`（9 项改为"表现"类）；`report/page_data.py`（阶梯文字、页脚人数不足说明、结论"相对谁"与职业补充句、面板枪法四项用典型单场、段位一列用人均标准、小样本 `count_text`）；`report/page_check.py`（一致性检查接受新结论句式）；`report/page_template.html`（后缀与 `count_text` 显示、文字）；`docs/features.md`
- 修改的旧测试及原因：`test_tier_ladder_053.py`（`reached` 新判定：17 → 第 1 档、25 → 第 1 档；`kpr` 改为表现类，不再进 `per_player`）；`test_page_data.py`、`test_page_check.py`（结论句式加"相对{分段}，"）
- 测试结果（最后一行）：预写测试 `9 passed`；全部测试 `1181 passed, 4 skipped`
- 遗留问题：无

## 审查意见（Claude 填写）

2026-10-19 通过。代码与任务单一致；Claude 补完 `docs/features.md` 中"雷达同心线"与旧结论句式两处，以及完成报告。真实数据验证（重建基准、生成多场汇总、截图）在合并后于主目录完成。
