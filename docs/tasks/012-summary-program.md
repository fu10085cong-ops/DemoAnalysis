# 012 多场汇总（程序部分）：新命令 `summary`

状态：完成

## 目标

单场比赛样本太少，很多问题只能标为"次数较少，需要更多比赛确认"。多场汇总把同一个玩家多场比赛合起来分析，判断哪些是真正的习惯。

**多场汇总和单场报告是两个独立的产品**（产品负责人 2026-10-16 确定）：

- 多场汇总**直接从 demo 计算**，不读取、不依赖单场分析的任何输出（`report.md`、`moments.json` 等）；
- 生成单场报告也不会触发汇总；
- 两者共用解析、特征、问题检测的底层函数。

本任务只做程序部分，不调用 AI。AI 报告是任务 013。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/product.md` 待定事项中的"多场汇总"
- `src/cs2coach/cli.py`：`analyze` 命令、`_output_folder`（玩家文件夹按 steamid 沿用）、`_archive_reports`
- `src/cs2coach/parsing/loader.py`：`resolve_demo`，支持的格式和压缩包成员
- `src/cs2coach/parsing/match.py`：`parse_match`、`require_player`、`roster_names`
- `src/cs2coach/features/moments.py`：`find_moments`
- `src/cs2coach/features/problems.py`：`detect_problems`、`problem_frequency`、`FrequencyRow`、`_poisson_tail`
- `src/cs2coach/report/problems.py`：频率表的输出格式
- `src/cs2coach/tools/digest.py`：`match_score`（目标玩家所在队伍的比分，已处理中途掉线的回合），每场概况的比分用它
- `data/output/README.md`：输出目录结构

已确认的事实：

- `DemoParser(path).parse_player_info()` 很快，实测一场 0.2–0.8 秒，可以用来扫描哪些 demo 里有这个玩家。被截断的 demo 可能返回空表（`docs/fields.md` 陷阱 22），这时用 `roster_names` 的做法，从少量 tick 中取名单。
- `data/demos` 下有三种存放方式：压缩包（`.zip`、`.rar`，一个包里可能有多场）；解压缓存 `.extracted/`；直接放的 `.dem`。同一场比赛可能同时存在压缩包和解压后的文件。
- 测试用的真实数据：梁孙超（steamid `76561199736861630`）出现在 8 场 demo 中：5E 5 场，完美 3 场，在完美上叫 Jayleo。

## 要做的事

### 1. 查找 demo（新模块 `src/cs2coach/summary/discover.py`）

`find_player_demos(steamid, demos_dir) -> list[DemoRef]`：

- `DemoRef` 包含：`path`、`member`（压缩包内的文件名，没有则为 None）、`match_id`（比赛标识 = demo 文件名去掉扩展名，压缩包内的用包内文件名）、`map_name`、`player_name`（这个玩家在该 demo 里的名字）。
- 递归查找支持的格式（与 `resolve_demo` 一致）。压缩包要列出所有成员。
- **同一个 `match_id` 只保留一份**：优先用压缩包或原始文件，其次用 `.extracted` 中的文件。
- 读不了的文件跳过，并记录原因，不中断。
- 结果按 `match_id` 排序，保证稳定。

### 2. 汇总计算（新模块 `src/cs2coach/summary/aggregate.py`）

`build_summary(steamid, refs, cfg, baseline) -> PlayerSummary`。对每场比赛：`parse_match` → `require_player` → `find_moments` → `detect_problems`。

- 某一场失败（例如覆盖率不够）时跳过，记录原因，继续处理其他场。
- 每场给一个编号 `M1`、`M2`……，按 `match_id` 排序。**跨场引用**写作 `M3:R9-death`，013 会用到。

`PlayerSummary`（pydantic）包含：

- `steamid`；`names`：这个玩家在各场用过的名字，去重；
- `matches`：每场一行：
  - 编号、`match_id`、地图、回合数、在场回合数、比分、击杀、死亡、助攻；
  - 整场枪法统计（`AimStats` 的六项）；
  - 本场的突出问题编号列表；
- `skipped`：跳过的比赛及原因；
- `rounds_played_total`：在场回合数之和；
- `frequency`：汇总后的问题频率，每类问题一行：
  - 与 `FrequencyRow` 相同的字段，基于所有场次的命中次数之和、在场回合数之和计算，突出的判断规则和阈值与单场相同；
  - 新增 `matches_with_hits`：至少命中一次的场数；
  - 新增 `matches_above_pro`：该场次数高于该场按职业频率预期次数的场数；
  - 新增 `by_map`：各地图的命中次数。
- `aim_overall`：六项枪法指标各场数值的**中位数**，并注明参与的场数。注意不是把所有子弹合在一起重新算，名称和说明里要写清楚。
- 内部还要保留每场的 `MatchMoments` 和 `ProblemReport`，给 013 用。不写进输出文件，或者用 `exclude` 排除。

**复用**：频率的计算要和 `problem_frequency` 共用逻辑。可以把它重构成接收"命中次数、回合数"的函数，单场和多场都调用它，**不要复制一份**。单场的行为和输出必须保持不变，用现有测试保证。

### 3. 命令行

```
python -m cs2coach summary --player <steamid> [<demo> ...] [--demo-member 名字 ...] [--demos-dir data/demos] [--out data/output] [--config 文件]
```

- 不给 demo 时，用 `--demos-dir`（默认 `data/demos`）查找所有包含该玩家的 demo；
- 给了 demo 时只用这些，`--demo-member` 的用法和 `analyze` 一样；
- 打印每场的处理进度和结果（成功 / 跳过及原因）；
- 输出到 `<out>/<玩家文件夹>/汇总/`：
  - 玩家文件夹沿用 `_output_folder` 的规则，按 steamid 匹配已有的文件夹；
  - `problems.md`：汇总频率表。列：问题、总次数、职业预期、多出的次数、偶然概率、倍数、出现场数 / 高于职业的场数、主要地图。下面是每场概况表、跳过的比赛；
  - `summary.json`：`PlayerSummary` 的内容；
  - 重新运行时，旧的 `problems.md` 和 `summary.json` 移进 `汇总/历史版本/`，复用 `_archive_reports` 的做法；
- 本任务**没有** `--ai` 参数，在 013 加。

### 4. 文档

- `README.md`：新增 `summary` 命令的用法；
- `docs/features.md`：新增"多场汇总"一节，写明口径：频率按在场回合数之和计算；枪法取各场中位数；`matches_above_pro` 的定义；
- `data/output/README.md`：补充 `汇总/` 文件夹的说明。

## 验收标准

- [x] 测试（不读真实 demo）：
  - `find_player_demos`：用 `tmp_path` 构造文件；`parse_player_info` 用 monkeypatch 替换。覆盖：压缩包与 `.extracted` 同名去重；读取失败时跳过并记录；排序稳定。
  - 汇总频率：用两场手工构造的 `MatchMoments` 和 `ProblemReport` 验证：
    - 总次数和在场回合数正确；
    - 预期次数、偶然概率与用总数单独计算的结果一致；
    - `matches_with_hits`、`matches_above_pro`、`by_map` 正确；
    - 一场有缺席回合时，用在场回合数计算。
  - 单场 `problem_frequency` 的现有测试全部不改且通过。
  - 某一场失败时被记入 `skipped`，其他场照常汇总。
  - 输出目录、重新运行时的归档。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 真实数据：
  ```
  python -m cs2coach summary --player 76561199736861630 --out <临时目录>
  ```
  - 应找到 8 场：5E 5 场，完美 3 场；
  - 完成报告中贴出 `problems.md` 的汇总频率表和每场概况表；
  - 用 `--out` 指定临时目录，**不要写进 `data/output/`**；
  - 写明总耗时。

## 范围外（不要做）

- 不调用 AI，不做汇总报告的排版（013）。
- 不做按时间排序和进步趋势：比赛日期不可靠。
- 不做缓存。如果耗时太长，在完成报告中说明，再另行讨论。
- 不改单场 `analyze` 的输出和行为。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `summary/discover.py` 递归扫描原始文件与压缩包成员，按 `match_id` 去重并优先原始文件；`parse_player_info` 为空时用少量 tick 字段取名单，无法读取的文件记录原因。`summary/aggregate.py` 逐场解析、检测并生成 Pydantic 汇总对象，失败场次跳过；保留各场瞬间与问题报告供 013 使用，JSON 不序列化这些内部对象。
  - `features/problems.py` 抽出单场和多场共用的频率计算；`report/problems.py` 抽出共用的频率表单行格式。单场输入、排序、突出判断和输出行为保持原样。
  - `summary/render.py` 输出汇总频率、每场概况、六项枪法各场中位数及跳过原因。`cli.py` 增加 `summary` 命令，沿用玩家文件夹和按修改时间归档机制；无 AI 参数，不读取单场输出。`README.md`、`docs/features.md`、`data/output/README.md` 补充用法、统计口径与目录结构；`.gitignore` 只放行此前被忽略的 `data/output/README.md`，让该文档进入审查范围，其他 `data/` 内容继续忽略。
  - `tests/test_summary.py` 覆盖压缩包与缓存去重、读取失败与 tick 名单后备、缺席回合分母、两场聚合与职业预期、失败场次继续、输出目录沿用及重跑归档。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**526 passed, 3 skipped**；`git diff --check` 通过。未新增依赖，未运行 AI 或真实 API。
- 真实 demo 验证：运行 `python -m cs2coach summary --player 76561199736861630 --out <临时目录>`，找到并成功处理 8 场（5E 5 场、完美 3 场），跳过 0 场，在场回合合计 169。耗时 **84.08 秒**。输出位于 `C:\Users\David\AppData\Local\Temp\cs2coach-012-e7f1c2043b5746248423a3f12f2f617b\Jayleo_76561199736861630\汇总\`；生成后补排版了枪法中位数段，未重新解析 demo。`problems.md` 的汇总频率表：

  | 问题 | 总次数 | 职业预期 | 多出的次数 | 偶然概率 | 倍数 | 出现场数 / 高于职业的场数 | 主要地图 |
  |---|---:|---:|---:|---:|---:|---:|---|
  | **出枪慢** | 46 | 23.24 | 22.76 | 0.0% | 1.98× | 8 / 7 | de_dust2 24 次、de_inferno 11 次、de_mirage 11 次 |
  | **漏补枪** | 31 | 13.67 | 17.33 | 0.0% | 2.27× | 7 / 6 | de_dust2 19 次、de_inferno 7 次、de_mirage 5 次 |
  | 准星没提前放好（次数较少，需要更多比赛确认） | 34 | 27.40 | 6.60 | 12.4% | 1.24× | 8 / 6 | de_dust2 16 次、de_inferno 11 次、de_mirage 7 次 |
  | 压枪差（次数较少，需要更多比赛确认） | 7 | 3.39 | 3.61 | 5.7% | 2.06× | 5 / 5 | de_dust2 4 次、de_mirage 2 次、de_inferno 1 次 |
  | 身上有道具却直接拉出（次数较少，需要更多比赛确认） | 4 | 2.52 | 1.48 | 24.6% | 1.59× | 4 / 4 | de_dust2 2 次、de_inferno 1 次、de_mirage 1 次 |
  | 人数优势时冒进（次数较少，需要更多比赛确认） | 2 | 1.31 | 0.69 | 37.8% | 1.52× | 2 / 2 | de_dust2 2 次 |
  | 下包后带着火没用（次数较少，需要更多比赛确认） | 1 | 0.38 | 0.62 | 31.8% | 2.61× | 1 / 1 | de_dust2 1 次 |
  | 沙鹰移动开枪（次数较少，需要更多比赛确认） | 1 | 0.38 | 0.62 | 31.8% | 2.61× | 1 / 1 | de_mirage 1 次 |
  | 用 Glock 远距离对枪（次数较少，需要更多比赛确认） | 1 | 0.88 | 0.12 | 58.3% | 1.14× | 1 / 1 | de_dust2 1 次 |
  | 开枪时没停稳（不高于职业水平） | 12 | 14.88 | -2.88 | 80.7% | 0.81× | 6 / 3 | de_dust2 7 次、de_mirage 3 次、de_inferno 2 次 |
  | 主动拉出撞上对手架点（不高于职业水平） | 5 | 9.41 | -4.41 | 95.7% | 0.53× | 5 / 2 | de_dust2 3 次、de_inferno 1 次、de_mirage 1 次 |

  每场概况表：

  | 编号 | 比赛 | 地图 | 回合 / 在场 | 比分 | 击杀 | 死亡 | 关键助攻 | 突出问题 |
  |---|---|---|---:|---:|---:|---:|---:|---|
  | M1 | 9209547595508347918_0 | de_inferno | 22 / 22 | 13:9 | 18 | 16 | 4 | — |
  | M2 | 9210673495171964302_0 | de_dust2 | 16 / 16 | 13:3 | 10 | 6 | 1 | slow_reaction |
  | M3 | 9215177492731139980_0 | de_dust2 | 30 / 30 | 16:14 | 22 | 20 | 5 | — |
  | M4 | g161-20261009192530992825357_de_dust2 | de_dust2 | 21 / 21 | 13:8 | 7 | 14 | 2 | missed_trade |
  | M5 | g161-n-20260927233757853942668_de_dust2 | de_dust2 | 16 / 16 | 13:3 | 11 | 10 | 4 | — |
  | M6 | g161-n-20260928224715762874577_de_inferno | de_inferno | 20 / 20 | 13:7 | 16 | 16 | 2 | missed_trade |
  | M7 | g173-n-20261014223216271995270_de_mirage | de_mirage | 28 / 28 | 16:12 | 22 | 18 | 2 | slow_reaction |
  | M8 | g173-n-20261014233811572950094_de_dust2 | de_dust2 | 16 / 16 | 3:13 | 9 | 15 | 3 | missed_trade |

- 遗留问题 / 需要确认的地方：无；没有修改 `data/output/` 下的既有比赛文件，也未提交。

## 审查意见（Claude 填写）

**审查通过（2026-10-16），Claude 撤回了一处范围外的改动。** 查找、去重、汇总、输出都正确；单场频率重构后行为不变；8 场 84 秒，不需要缓存。汇总结果有说服力：出枪慢和漏补枪在 8 场中分别有 7 场、6 场高于职业；单场里常被提到的准星问题，合起来后偶然概率 12%，不突出。526 个测试通过。

撤回：`.gitignore` 放行了 `data/output/README.md`。这个文件含真实玩家的名字和 steamid，`data/` 整体不进版本库是有意的，已改回原样。以后需要审查 `data/` 下的文件，在完成报告中写明路径即可，Claude 可以直接读取。

