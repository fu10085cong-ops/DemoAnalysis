# 046 职业阈值按指标检查样本数，分位数不外推（外部审查第 14 条）

状态：完成
难度：B（改 `baseline.py` 两处，并重建基准、跑规则回归）
执行：Codex（思考强度 medium）
分支：`task/046-baseline-min-samples`

## 目标

现在只检查"某武器的交火总数 ≥ `min_engagements`（30）"。只要总数够，各指标即使只有很少的有效值，也会生成正式阈值供检测规则使用。真实的职业基准里：

| 武器 | 指标 | 样本数 |
|---|---|---:|
| SSG 08 | `time_to_damage_ms`、`seen_to_first_shot_ms` | 12 |
| SSG 08 | `crosshair_placement_deg`、`crosshair_initial_offset_m` | 13 |
| MP7 | `time_to_damage_ms`、`seen_to_first_shot_ms` | 19 |
| MP7 | `crosshair_initial_offset_m` | 18 |

另外，`build_baseline` 用的 `statistics.quantiles` 默认是 exclusive 方法，小样本时会外推出界：对 `[0, 100]` 算出的 p25 = −25、p75 = 125。

## 背景（必须读）

- `src/cs2coach/baseline.py`：`_distribution`、`weapon_stats`、`build_baseline`、`weapon_threshold`
- `config/default.toml` 的 `[baseline]`
- `scripts/rule_regression.py --help`：重建基准、收集全部命中，并沿用历史判定估算有用率

## 要做的事

1. **按指标检查样本数**
   - `[baseline]` 新增 `min_metric_samples = 20`，并在 `config.py` 里校验它是正整数。
   - `_distribution` 增加参数 `min_n`：有效值少于 `min_n` 时，p50、p75、p90 都为 None，`n` 照常记录。
   - `weapon_stats` 里所有 `_distribution(...)` 调用都传入 `min_metric_samples`。
   - `weapon_threshold` 不用改：值为 None 时它本来就返回 None，检测规则会记为缺阈值、跳过这把武器。确认现有逻辑确实是这样，如果不是，停下来写进完成报告。
2. **分位数不外推**：`build_baseline` 改用 `statistics.quantiles(v, n=4, method="inclusive")`。
3. **重建并跑回归**：
   ```
   python scripts/rule_regression.py --name 2026-10-18-after046 --compare-to 2026-10-17-after039 --labels-from 2026-10-17-after037
   ```
   这一步会重建 `pro_sample.json`。之后再跑 `python scripts/build_tier_baseline.py --all`：分段基准的问题频率依赖职业阈值，要一起重建。
4. 在 `docs/features.md` 的职业基准一节写明样本门槛。

## 验收标准

- [x] `tests/test_baseline.py`（或现有的基准测试文件）新增用例：
  - 30 次交火里只有 1 个有效 TTD，阈值为 None，`n` 为 1；
  - 有效值达到 20 个时正常生成阈值；
  - `build_baseline` 对 `[0, 100]` 算出的 p25、p75 落在 [0, 100] 内。
- [ ] 重建后，贴出 `pro_sample.json` 中 SSG 08、MP7 各指标的 n 和 p75。
- [ ] 贴出 `rule_regression.py` 输出的命中数变化表；变化大于 5% 的规则，说明原因。
- [x] 跑 `python scripts/page_check_all.py`，结果为 0 处违规（使用未重建的现有基准，见完成报告）。
- [ ] 开发时只跑相关测试文件；完成前跑一次全部测试，报告只贴最后一行。

## 范围外（不要做）

- 不改其他检测规则和阈值分位。
- 不改报告页（045 已完成）。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：
  - `config/default.toml`、`src/cs2coach/config.py`：新增 `baseline.min_metric_samples = 20`，严格校验正整数（拒绝布尔值）。
  - `src/cs2coach/baseline.py`：各武器指标独立检查有效样本数，不足时保留 n、三个分位数均置 None；整场四分位数改用 inclusive。已确认 `weapon_threshold` 和检测器会将 None 视为缺阈值并跳过对应判断，未改规则。
  - `tests/test_baseline.py`、`tests/test_config.py`：覆盖 0/1/19/20 个有效值、不同指标独立计数、门槛覆盖、配置非法值和两点分位数；原小样本统计测试显式设置门槛为 1。
  - `tests/test_problem_frequency.py`：已有 4 样本的新阈值集成测试显式配置 `min_metric_samples = 1`，原断言不变。
  - `docs/features.md`：说明样本门槛和不外推口径；任务单及索引更新为待审查。所有修改文件使用 LF，无新增依赖，未提交或切换分支，未修改任务 047 的两个指定文件。
- 测试结果（最后一行）：
  - 相关测试：`77 passed in 0.99s`
  - 全量（加 `--continue-on-collection-errors`，继续验证可收集测试）：`1007 passed, 3 skipped, 2 errors in 43.91s`
  - pytest 使用工作区 `data/tmp/` 下的 `--basetemp` 和缓存目录，避开沙箱不可访问的默认临时目录。
- 真实 demo 验证（如有）：`scripts/page_check_all.py` 使用 `config/tiers.toml` 临时业余分段清单的 23 份 demo（`data/demos/5e_demo`、`data/demos/perfectworld_demo`），基准仍为未重建的现有文件：已检查 230 人次，无法检查 0 项；`报告页一致性检查：0 处违规`（退出码 0）。
- 遗留问题 / 需要确认的地方：
  - 全量收集错误：`tests/test_platform_check.py` 无权读取共享目录（用户已说明的已知情况）；`tests/test_index_page.py` 依赖任务 047 尚不存在的 `cs2coach.report.index_page`。均未修改。
  - 指定规则回归在读取 `data/audit/2026-10-17-after039/hits.jsonl` 时即因 PermissionError 失败，尚未创建输出目录；换到 `data/tmp/` 输出也无法解决历史输入不可读。因此没有可报告的命中变化表或超过 5% 的变化原因。
  - 职业基准重建脚本会将真实玩家名和 SteamID 写入 `pro_sample.json`，与本次用户要求存在冲突，已中断并提问；等待确认是否允许基准文件沿用现有身份字段。分段重建亦中断，未将基于旧阈值的产物作为验收结果；本次出现的未跟踪 `tiers/pro.json` 暂存于 `data/tmp/046-interrupted-tier-pro.json`，供审查。
  - `pro_sample.json` 未更新，不能把现有 SSG 08、MP7 的 n/p75 冒充重建结果；重建后的指标表和回归验收仍待完成。

## 审查意见（Claude 填写）

（2026-10-18，Windows）**通过**。代码改动只有 `baseline.py` 两处，测试覆盖 0、1、19、20 个有效值。Codex 的沙箱读不到共享审计目录，规则回归由 Claude 在沙箱外运行（`data/audit/2026-10-18-after046/`），23 场用时 615 秒。

- 职业基准重建后，阈值被关掉的有：SSG 08 的 TTD、看见→开枪、准星转角、初始偏移（n = 12–13）；MP7 的 TTD、看见→开枪、初始偏移（n = 18–19）。MP7 的准星转角 n = 20，保留，p75 = 11.9。
- 回归报告里所有规则都变少了，原因有两个，都和 046 无关：
  1. 本机只有 23 场。上一轮在 Mac 上跑的是 24 场，本机缺 `9220525551542050188_0.zip`，少了 167 条；
  2. `--labels-from 2026-10-17-after037` 被当成相对当前目录的路径，一条判定都没读到，也没有报错。已修：相对路径改按审计目录解析，指定的目录里都没有 labels.toml 时报错。
- 去掉缺的那场、正确读取 124 条判定后重算，**只有 3 条规则有变化**：
  - 准星 672 → 655（−17：MP7 14 条、SSG 08 3 条），沿用判定的有用率 72%；
  - 出枪慢 356 → 345（−11：MP7 9 条、SSG 08 2 条），沿用判定的有用率 100%；
  - 看见却没开枪 129 → 114（−15），这条规则不用职业阈值，减少来自 040（手持道具不判）。
- 046 的真实影响：去掉 28 条"依据样本太少的阈值"做出的判断。
