# 017 检查器：地图名、百分比口径、练习图白名单

状态：完成

## 目标

016 用本地千问 14B 生成的汇总报告，暴露了检查器的三个漏洞。DeepSeek 目前没出过这类错，但换成弱一些的模型就会出现：

| # | 现象（本地报告原文） | 漏洞 |
|---|---|---|
| 1 | "de_inferno 4回合（M12）"：M12 实际是 Dust2 | 文字里的地图名没有核对 |
| 2 | "问题集中在 de_dust2（68%）和 de_inferno（38%）"：68 和 38 是命中**次数** | 只核对"数字在数据里存在"，不核对它是不是百分比 |
| 3 | 推荐了"Deagle Duel""Inferno B""Spray Control"等练习图，很可能是编造的 | 训练建议没有白名单 |

另外，产品负责人已经确定了每类问题对应的练习方式（`docs/problem_catalog.md` 末尾的"训练方式"），本任务把它做成程序约束。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/problem_catalog.md`：末尾"训练方式"
- `docs/tasks/016-local-model-provider.md`：完成报告和审查意见
- 本地报告的提交记录：`C:\Users\David\AppData\Local\Temp\cs2coach-016-validation\妮妮孩孩major冠军_76561199703310441\汇总\submission.json`
- `src/cs2coach/tools/checker.py`：允许数值的收集、`_number_violations`、敏感说法
- `src/cs2coach/tools/submission.py`：`Finding`、`submit_tool`
- `src/cs2coach/ai/prompts.py`、`summary/prompts.py`
- `src/cs2coach/report/render.py`、`summary/render_report.py`
- `config/default.toml` 的 `[checker]`

## 要做的事

### 1. 地图名检查

- **配置**：`[checker.map_aliases]`，每张图的各种写法，例如：
  - `de_dust2`：`de_dust2`、`dust2`、`Dust2`、`沙二`、`沙2`；
  - `de_mirage`：`de_mirage`、`mirage`、`Mirage`、`米拉日`；
  - `de_inferno`、`de_ancient`、`de_anubis`、`de_nuke`、`de_cache`、`de_train`、`de_overpass`、`de_vertigo` 同样处理。
  
  匹配时不区分大小写。
- **单场**：分析文字中提到的地图必须是本场的地图。
- **多场**：
  - 每条 finding、pattern、highlight 的分析中提到的地图，必须是该条某个被引用目标所在场次的地图；
  - 总览中提到的地图，必须在这批比赛的地图之中；
  - 如果文字中写了"M12"之类的场次编号，后面紧跟的地图名必须是那一场的地图。例如"de_inferno 4回合（M12）"要报错。
- 违规代码：`map_mismatch`。说明里写明实际是哪张图。

### 2. 百分比口径

- **允许数值按类型区分**：收集允许数值时，标记哪些是**百分比类**：
  - 字段名以 `_pct` 结尾的数值；
  - 比例类数值（0 到 1 之间的比率、偶然概率等）× 100；
  - `compare()` 的 `better_than_pro_pct`。
- 文字中后面紧跟 `%` 或 `％`、或者前面是"百分之"的数字，**只能**匹配百分比类数值。命中次数、回合数、距离等不能拿来当百分比。
- 违规代码沿用 `number_not_found`，说明写成"`68%` 不是数据中的百分比；数据中的 68 是……"。如果能找到同值的非百分比数值，就指出它是什么。

### 3. 练习图白名单

- **配置**：`[[training.drills]]`，内容按 `docs/problem_catalog.md` 末尾的"训练方式"：
  - `id`、`label`（显示名称）、`problems`（适用的问题编号）；
  - 可选 `min_aim_problems`：用于"5E 的练枪图"。只有本报告的 findings 和 patterns 中出现两类及以上枪法问题时，才能使用。枪法问题指 `slow_reaction`、`poor_crosshair_placement`、`shot_while_moving`、`spray_control`。
- **提交格式**：`Finding` 新增 `drills: list[str]`，最多 2 项，可以为空。工具 schema 中用 enum 限定为白名单中的 `id`。
- **检查器**：
  - 每个 drill 的 `problems` 必须包含该 finding 的 `problem`，否则报 `drill_not_allowed`；
  - `min_aim_problems` 不满足时，同样报 `drill_not_allowed`；
  - 没有指定练习图的问题（武器专项、道具与决策类），`drills` 必须为空。
- **文字检查**（`training` 和 `training_plan`）：防止在文字里绕过白名单编造地图名。
  - 拉丁字母组成的词组，例如 `Deagle Duel`、`Inferno B`，必须在允许清单中。允许清单包括：白名单的 `label`、武器显示名、指标英文名（Time to Damage 等）、常见缩写（AK、M4、AWP、TTD、CT、T、ms 等）、地图别名。允许清单放进配置；
  - 书名号《》中的内容必须是白名单的 `label`；
  - 违规代码：`unknown_drill`。
- **排版**："怎么练"一节先列出所选练习图的 `label`，再接 AI 写的训练说明。

### 4. 提示词

单场和多场的提示词中补充：

- 训练建议只能使用 `drills` 中的练习图，以及摘要中列出的练习方式；
- 武器专项、道具与决策类问题暂不指定练习图，只描述练习方法，不写具体地图名；
- 摘要中列出白名单，以及每类问题可用的练习图。

## 验收标准

- [ ] 测试：
  - 地图名：
    - 单场写错地图时报错；
    - 多场的"de_inferno 4回合（M12）"报错，写对时通过；
    - 中文别名（沙二）能识别；
    - 总览提到不在这批比赛中的地图时报错；
  - 百分比：
    - "68%"对应命中次数时报错；
    - "25.7%"对应扫射命中率时通过；
    - "偶然概率 0.2%"通过；
    - 不带 % 的同一个数字，仍然按原规则匹配；
  - 练习图：
    - 用于不适用的问题时报错；
    - "5E 的练枪图"在只有一类枪法问题时报错，两类及以上时通过；
    - 道具类问题带了 drills 时报错；
    - `training` 中写了 "Deagle Duel" 或《Spray Control》时报错，写 "Aim Botz" 时通过；
    - schema 的 enum 正确；
  - 排版中出现练习图名称。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] **回归**：用 016 本地报告的 `submission.json` 中最终保留的提交，跑一遍新检查器，应报出上面三类问题。把这份提交复制到 `tests/fixtures/`，作为回归测试，在完成报告中贴出违规列表。
- [ ] DeepSeek 的现有回归样例（`tests/fixtures/` 中已有的合格提交）在新规则下：如果因为新增的 `drills` 字段或新规则而不合格，要按新规则更新样例，并在完成报告中说明改了什么。
- [ ] **不要调用 DeepSeek 或 Claude**。本地 Ollama 可以调用，但本任务不需要。

## 范围外（不要做）

- 不规定训练强度（组数、次数、时长）。
- 不为武器专项、道具与决策类问题指定练习图。
- 不改问题检测和频率规则。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：`config/default.toml`、`src/cs2coach/config.py` 新增并校验地图别名、练习图和训练英文允许清单；`tools/checker.py`、`tools/digest.py`、`summary/digest.py` 区分百分比和普通数值，核对地图与场次绑定，限制练习图及训练文字；`tools/submission.py` 增加 `drills` 字段和动态 enum（旧提交缺该字段时按空数组解析）；`tools/drills.py`、单/多场提示词与摘要列出各类问题可用练习图；单/多场报告渲染在“怎么练”先显示选中名称。更新 `tests/fixtures/` 中三份历史样例以显式包含空 `drills`，新增 016 原始提交、实测汇总及 `tests/test_checker_017.py` 回归测试；`tests/test_ai.py`、`tests/test_render.py`、`tests/test_summary_ai.py` 同步字段与排版断言。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**553 passed, 3 skipped**；`git diff --check` 通过。016 留存提交的关键违规：`overview` 的 `68%`、`38%` 均为地图命中次数，报 `number_not_found`；`findings[0].analysis` 把 M12 写成 de_inferno（实际 de_dust2），`highlights[0].analysis` 把 M1 写成 de_dust2（实际 de_inferno），均报 `map_mismatch`；`findings[0].training` 的《Deagle Duel》，以及 `training_plan[0]` 的《Deagle Duel》《Inferno B》《Spray Control》均报 `unknown_drill`。
- 真实 demo 验证（如有）：未重新解析 demo、未调用任何模型；回归使用 016 实际生成的 `submission.json` 与 `summary.json`，保存在 `tests/fixtures/016_local_submission.json`、`016_local_summary.json`。
- 遗留问题 / 需要确认的地方：016 输出未保存全部 21 场的 `MatchMoments` / `ProblemReport`，回归测试对这些详情使用空壳，仅核对依赖原始提交和实测汇总的三类新规则；因此旧规则还会产生无目标、无命中等附加违规。训练方式“平台（5E / 完美）的预瞄训练图”作为一个白名单项登记；如产品希望拆分为两个独立练习图，可在后续任务明确。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 三类规则都已实现，并有测试覆盖：地图名（含中文别名、场次绑定、总览）、百分比口径、练习图（适用问题、5E 练枪图的条件、文字中编造的地图名）。016 本地报告的回归测试抓到了全部错误，还多找出一处 Claude 漏掉的地图错误（M1 实际是 Inferno）。553 个测试通过。

提醒：`tests/fixtures/016_local_*.json` 含真实玩家名和 steamid。公开仓库前需要匿名化，已记入 `docs/product.md` 的"数据与隐私"。
