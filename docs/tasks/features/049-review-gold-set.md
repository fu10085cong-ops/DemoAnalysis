# 049 标准答案集：复盘包支持分层抽样、盲审和"值不值得提醒"

状态：完成
难度：B（只改 `scripts/review_pack.py` 和配置；已预写测试）
执行：Codex（medium）。原定 Kimi，但 5 小时额度用完；改派 MiMo，被它的网关以"high risk"拒绝；最后由 Codex 完成
分支：`task/049-review-gold`（工作目录 `C:\Users\David\DemoAnalysis-049`）

## 目标

到目前为止，"规则判得对不对、值不值得提醒"几乎都是 Claude 读数据判出来的，产品负责人在游戏里亲自确认的只有 8 条，其中 5 条判错了。现在要建一套**由产品负责人在游戏里确认的标准答案集**：每条在用规则各抽 5 条，共约 30 条。用它算出真实的准确率和有用率，并检验 Claude 的判定和产品负责人的一致率。

为此复盘包需要三项新能力：
1. **分层抽样**：按规则各抽 N 条，用固定的随机种子，同一个种子每次抽到的都一样；
2. **盲审**：清单里不显示 Claude 的原判和备注，避免影响产品负责人的判断；
3. **回填"值不值得提醒"**：和"对不对"一起写回判定文件，判定文件里原来没有的条目要新建。

## 玩家看到什么（这里的"玩家"是做复盘的产品负责人）

- 清单每条都有一行 `- 值得提醒吗：……`，下面附填写说明。
- `review.toml` 每条有 `result` 和 `worth` 两个字段。`worth` 填"值得"或"不值得"，判断标准是：如果你是教练，会不会因为这一条提醒玩家。
- 加 `--blind` 时，清单里不出现 `Claude 原判`、`原备注` 这两行及其内容。

## 背景（必须读）

- `tests/test_review_gold.py`：**预写测试，不要修改**。实现的目标是让它全部通过；认为测试有错时，停下来写进完成报告。
- `tests/test_review_pack.py`：已有测试，必须继续通过。
- `scripts/review_pack.py`：`_selected`、`build`、`apply`、`main`（全文约 300 行）
- `config/default.toml` 的 `[review]` 和 `[review.questions]`

## 要做的事

1. **`build` 新增参数**：
   - `--pick stratified`：和现有的 `ingame` 并列；要同时给出 `--audit`、`--rules 规则1,规则2,...`、`--per-rule N`，`--seed` 默认 0。
   - 抽样方式：对每条规则，从审计目录 `hits.jsonl` 中这条规则的全部命中里，用 `random.Random(seed)` 抽 N 条，不足 N 条就全取。抽取前先按 `hit_key` 排序，保证结果可复现。抽到的条目按 `--rules` 的顺序排列。
   - `--blind`：清单不写 `Claude 原判` 和 `原备注` 两行。
   - 选 `stratified` 时，审计目录可以没有 `labels.toml`，命中也可以没有判定，原判显示为"未判"。现有的 `--keys` 和 `--pick ingame` 行为不变。
2. **清单和 `review.toml`**：
   - 每条加 `worth = ""` 字段；
   - 清单每条加 `- 值得提醒吗：如果你是教练，会因为这一条提醒玩家吗？在 review.toml 的 worth 填‘值得’或‘不值得’。`
   - 清单开头的说明里补一句 worth 的填法。
3. **`apply`**：
   - `worth` 的取值："值得" → `yes`，"不值得" → `no`，空表示不写；其他值报错。
   - 填了 `worth` 却没填 `result`，报错。
   - 判定文件里**已有**这条：覆盖 `verdict`、`reason`、`note`（与现在相同），以及 `worth`。原来没有 `worth` 字段的，在 `verdict` 那一行后面插入。
   - 判定文件里**没有**这条，或者 `labels.toml` 根本不存在：在文件末尾追加一个新条目，包含 `verdict`、`reason = "游戏内确认"`、`needs_ingame = false`、`note = "游戏内确认（日期）：对/不对/看不出[，备注]"`、`selection = "ingame-gold"`，以及填了的话 `worth`。新建的文件用 LF 换行。
   - 旧复盘包没有 `worth` 字段，照常回填。
   - 修改前照旧先备份。
4. 在 `docs/features.md` 或 `scripts/review_pack.py` 的模块说明里，写清标准答案集的生成命令示例：
   ```
   python scripts/review_pack.py build --audit 2026-10-18-after046 --pick stratified \
     --rules poor_crosshair_placement,slow_reaction,shot_while_moving,missed_trade,died_without_seeing,saw_but_no_shot \
     --per-rule 5 --seed 20261018 --blind --name 2026-10-18-gold
   ```

## 运行方式

在独立工作目录中执行；测试用主目录的 venv：

```
$env:PYTHONPATH = "C:\Users\David\DemoAnalysis-049\src;C:\Users\David\DemoAnalysis-049"
C:\Users\David\DemoAnalysis\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_review_gold.py tests/test_review_pack.py
```

工作目录里没有 `data/`，**不要访问主目录的 `data/`**（里面有真实玩家信息和共享的判定文件）。真实复盘包由 Claude 合并后生成。

## 验收标准

- [x] `tests/test_review_gold.py` 全部通过（不得修改这个文件）；`tests/test_review_pack.py` 全部通过。
- [x] 全部测试通过（命令同上，去掉文件名），报告只贴最后一行。
- [x] 修改的文件为 LF 换行，不含真实玩家名或 SteamID。

## 范围外（不要做）

- 不做"Claude 判定与产品负责人判定的一致率"统计（Claude 之后另做）。
- 不改审计收集、规则和报告。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：`scripts/review_pack.py` 增加固定种子的分层抽样、盲审和 worth 填写/校验/回填；支持追加缺失判定和新建判定文件，保留旧包兼容、原备注、备份及已有文件换行符；模块说明补生成命令示例。本任务单更新状态和完成报告；未修改测试或新增依赖。
- 测试结果（最后一行）：相关测试 `16 passed in 0.77s`；全量测试 `1037 passed, 4 skipped in 30.68s`。
- 遗留问题 / 需要确认的地方：无。默认临时目录权限不足，测试使用 `--basetemp` 指向工作目录外 `%TEMP%` 下独立目录；未访问主目录 data 或使用真实 demo。未提交、未切换分支。

## 审查意见（Claude 填写）

（2026-10-18，Windows）**通过**。分层抽样先按 `hit_key` 排序，再用同一个 `Random(seed)` 依次抽取，可以复现；`--blind` 只去掉两行；`apply` 兼容旧复盘包、保留原有换行符、追加缺失的条目，判定文件不存在时会新建。全部测试 1037 通过。
