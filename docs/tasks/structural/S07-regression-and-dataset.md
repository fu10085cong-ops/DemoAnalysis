# S07 规则变更回归工具 + 交火数据集

状态：完成
难度：B（把已经反复手工做过的流程写成脚本，不改检测逻辑）
执行：Codex，思考强度 medium
分支：`task/S07-regression-dataset`

## 目标

2026-10-17 这一天里，有两件事被反复手工做了很多遍：

1. **每次改规则后**（031–040 一共 8 次左右）：重建职业基准 → 全量收集业余命中 → 和上一次对比命中数 → 把上一轮审计判定中仍然命中的条目找出来，估算新的准确率和有用率。每次都是现写脚本。
2. **每做一项数据研究**（先手转化、看见却没开枪、死亡覆盖率、位置汇总等，至少 7 次）：都要重写一个脚本，把 50 份 demo 重新解析一遍，每次 10 分钟以上。

本任务把它们做成两个可以反复运行的工具。

## 背景（必须读）

- `scripts/audit_collect.py`（全量收集）、`scripts/build_baseline.py`（职业基准）、`src/cs2coach/baselines/pro_demos.toml`（职业 demo 清单）
- `src/cs2coach/audit.py`：`read_hits`、`precision_report`。worth 字段见 S04
- `src/cs2coach/features/models.py`：`Moment`、`Engagement`、`DeathState`、`Context`、`MissedTrade`
- `src/cs2coach/cache.py`：`code_hash`

## 要做的事

### 1. `scripts/dataset_export.py`：交火数据集

- **输入**：
  - 职业 demo：`pro_demos.toml`；
  - 业余 demo：`data/audit/<最近一次收集>/hits.jsonl` 里出现过的所有 demo，或者用 `--demos-from <审计目录>` 指定。
- **输出**：`data/dataset/<code_hash 前 12 位>/`，每份 demo 一个 parquet 文件。按行分为三类：
  - **`moments`**：每名合格玩家的每个瞬间一行。包括 group（pro / amateur）、demo、member、地图、steamid、`Moment` 的标量字段，以及 `Context`、`Engagement`、`DeathState`、`Trade` 中所有标量字段，展开成列，列名加前缀（`ctx_`、`eng_`、`death_`、`trade_`）。列表字段（如 tags、unused_grenades）存成 JSON 字符串。另加两列：该瞬间命中的问题列表 `hits` 和停用问题列表 `disabled_hits`；
  - **`missed_trades`**：`MissedTrade` 每条一行；
  - **`players`**：每名合格玩家一行，包括 rounds_played、名字的哈希（**不存真实名字**）和整场枪法统计。
- **缓存**：目标文件已存在就跳过，因为目录名里带 code_hash，代码变了会自动换目录；`--jobs N` 并行，默认 3。
- **读取接口**：`cs2coach.dataset.load(kind="moments", group=None) -> pandas.DataFrame`，自动读取当前 code_hash 的目录；目录不存在时报错，并提示先运行导出。
- **不存储真实玩家名**：数据集供分析用，会长期保存在 `data/` 下。
- **依赖**：环境里已有 `pyarrow` 26.0.0（随其他依赖装进来），但 `pyproject.toml` 没有声明。使用 parquet 就要把它写进依赖，并在完成报告中说明。

### 2. `scripts/rule_regression.py`：规则变更回归

```
python scripts/rule_regression.py --name 2026-10-18-afterXXX --compare-to 2026-10-17-after039 [--skip-baseline]
```

依次做下面几件事：
1. **重建职业基准**（除非 `--skip-baseline`）：运行前备份旧的 `pro_sample.json`，记下每个问题的 `problem_rates` 新旧值；
2. **全量收集业余命中**：调用 `audit_collect`，输出到 `data/audit/<name>/`；
3. **对比命中数**：和 `--compare-to` 目录按问题对比；
4. **沿用判定估算**：在 `--compare-to` 及更早的审计目录中，找出已有判定、并且在新收集中仍然命中的条目（按 hit_key 匹配）。按问题统计：
   - 仍命中的已判定条数；
   - 其中 correct、wrong、worth=yes、worth=no 各多少；
   - 估算的准确率和有用率（只是参考，样本是沿用的）；
   - 被新规则移除的已判定条目里，wrong 和 worth=no 各占多少，用来判断这次改动去掉的是不是不该判的。
5. **写报告**：结果写到 `data/audit/<name>/regression.md`，终端打印摘要。

**判定来源**：用 `--labels-from` 指定一个或多个审计目录，默认取 `--compare-to` 加上所有更早的同日目录。同一 hit_key 有多份判定时，取最新目录里的那份。

## 验收标准

- [ ] **单元测试**，用临时目录和合成数据：
  - **数据集**：一份合成比赛导出后，三类行都存在，列名带前缀，`hits` 列和 `detect_problems` 的结果一致，不含真实名字；
  - **数据集缓存**：再次运行时跳过；
  - **回归对比**：给两份合成的 hits.jsonl 和一份 labels.toml，命中数的变化、沿用判定的统计、"被移除条目的构成"都正确；
  - **判定来源**：多份判定冲突时，取最新的那份。
- [ ] **真实运行**：
  - `dataset_export.py` 导出全部 50 份 demo，贴出总行数和耗时；
  - 用 `cs2coach.dataset.load` 复现一个已知数字：业余"没朝击杀者开枪的阵亡"每 10 回合 2.37 次、职业 2.17 次。允许有小差异，在报告中说明原因；
  - `rule_regression.py --name S07-check --compare-to 2026-10-17-after039 --skip-baseline`：代码没变，应该显示所有命中数不变。
- [ ] 全部测试通过，报告只贴最后一行。
- [ ] 完成报告中玩家名一律替换成"（已匿名）"。仓库带有提交钩子，但你不提交，所以要自己注意。
- [ ] 不调用任何付费 API。

## 范围外（不要做）

- 不改检测规则、基准算法、审计抽样。
- 不做图表和网页。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：新增 `scripts/dataset_export.py`、`src/cs2coach/dataset.py`，导出并按代码哈希读取三类 parquet 行，玩家名仅存 SHA-256；新增 `scripts/rule_regression.py`，备份基准、收集命中并比较历史判定；新增 `tests/test_dataset_regression.py` 覆盖三类行、命中、缓存、回归统计和最新判定覆盖；`pyproject.toml` 声明环境已有的 `pyarrow>=26.0.0`，`docs/tasks/README.md` 同步状态。
- 测试结果（最后一行）：`833 passed, 3 skipped in 81.99s (0:01:21)`
- 真实运行：数据集导出 50/50 份（职业 26、业余 24），17,440 行（moments 15,694、missed_trades 1,246、players 500），耗时 1059.7 秒。`load` 读取后按 `moment_kind == "death"` 且 `eng_shots_fired == 0` / `rounds_played` 计算，业余 1531/5110 回合 = 3.00 次/10 回合，职业 1640/5620 回合 = 2.92 次/10 回合。`rule_regression.py --name S07-check --compare-to 2026-10-17-after039 --skip-baseline` 收集 24/24 份，旧 3977 条、新 3962 条，减少 15 条，全部为 `saw_but_no_shot`；报告在 `data/audit/S07-check/regression.md`。
- 遗留问题 / 需要确认的地方：任务单给出的 2.37/2.17 与现有 `Engagement.shots_fired` 口径不符，需确认原数字是否统计整回合朝击杀者的射击；任务单预期 S07-check 命中数不变，但当前比较基准多出 15 条 `saw_but_no_shot`，没有新增命中，需确认是否由 040 的规则改动造成。两项差异均未通过改检测规则消除。

## 审查意见（Claude 填写）

**审查通过（2026-10-18，Claude）。** Codex 报告的两处数字差异都已核实：
1. **"没朝击杀者开枪的阵亡"是 3.00 / 2.92，不是 2.37 / 2.17**：Claude 之前的统计排除了穿烟、穿墙、被闪、被道具打死的阵亡，但任务单里没写这些条件。用数据集加上同样的条件后，职业 2.17、业余 2.37，**完全一致**。
2. **S07-check 少了 15 条"看见却没开枪"**：对比用的 after039 是在 040 之前收集的，040 排除了手持道具的情况。这是预期内的变化。

**以后补**：数据集的列名需要一份说明。比如 `kind` 表示行的类型，瞬间本身的类型在 `moment_kind`。Claude 审查时就因此写错过两次查询。

导出 50 份 demo 共 17,440 行，耗时 1,060 秒。每个代码版本只需要导出一次，之后的分析都是几秒钟的查询。

