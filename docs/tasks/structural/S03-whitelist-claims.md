# S03 事实性说法改由程序提供：对比句、准星两种口径、训练目标

状态：完成（改动后的评测待运行）
难度：A（改检查器、占位符和提示词的分工）
执行：Codex，思考强度 medium（产品负责人指定）
分支：`task/S03-whitelist-claims`

## 目标

改动前的基线见 `docs/eval_log.md` 的"S03 改动前基线"一节。检查器没拦住、但写错的说法里，有两类每份报告都会出现：

1. **口径错配**：4 份单场报告里有 3 份出现，共 4 处。
   - AI 用占位符自己拼对比句，可数值和阈值不是同一口径。例如"从看见到第一次命中 594 毫秒，高于 75 分位 391 毫秒"，但 391 是"看见→开枪"的阈值。
   - 准星有两种口径，AI 会混用：判定用"刚看见时离头部多少米"，显示用 Leetify 的"转过的角度"。总览经常先说"准星角度不差"，后面又说"准星没提前放好最突出"。
2. **AI 自己定的训练目标**：4 份全有。例如"前 10 发全部落在靶心""首枪命中率低于九成就重做"。

做法是"白名单"：**事实性的对比由程序提供，AI 只能引用，不能自己组合**。

## 背景（必须读）

- `src/cs2coach/report/evidence.py::evidence_sentence`：程序写的"程序检测依据"句子，口径一定正确
- `src/cs2coach/report/render.py`：报告里显示"程序检测依据"的位置（搜索 `程序检测依据`）
- `src/cs2coach/tools/slots.py`：`PROBLEM_VALUES`、`LEGACY_PROBLEM_VALUES`、`problem_values`，`{对象.value}`、`{对象.threshold}`、`{对象.gap}` 的取值，`{对象.crosshair}` 等字段表，`slot_guidance_fixed`
- `src/cs2coach/tools/checker.py`：`_training_violations`、说法检查（`[[checker.phrases]]`）、`raw_number` / `chinese_number` 检查
- `src/cs2coach/ai/prompts.py`：`SYSTEM_PROMPT`、`system_prompt`；`src/cs2coach/summary/prompts.py`
- `config/default.toml` 的 `[checker]`
- 改动前基线的报告：`data/output/评测/20261017-200017_S03基线_deepseek/`（只读）

## 要做的事

### 1. 对比句只由程序提供

- **分析文字里不再允许使用 `{对象.value}`、`{对象.threshold}`、`{对象.gap}`**。检查器报 `comparison_slot_removed`，提示改为引用依据句。
- **新增占位符 `{对象.basis}`**：渲染为该条问题的 `evidence_sentence` 全文，去掉句末句号以便嵌入句子。它只能在问题条目的分析里使用，用法和现在的 value 一样，按条目的问题取。
- 提示词里说明：需要说明"超过职业多少"时，引用 `{对象.basis}`，或者直接不写，因为报告里已经有"程序检测依据"一节。
- 多场汇总同样处理，跨场编号的写法照现有规则。
- **兼容**：旧的提交和回归样例里还有 value/threshold/gap。检查器对它们报新的违规代码，不要让旧测试崩溃。测试预期按新口径更新，在完成报告里列出改了哪些。

### 2. 准星的两种口径分开

- 占位符字段表中：
  - Leetify 的转角改名为 `{对象.crosshair_turn}`，显示名"准星转过的角度（Leetify 口径）"；
  - 新增 `{对象.crosshair_offset}`，取 `crosshair_initial_offset_m`，单位用 S03 之前已有的 `offset_m`，显示名"刚看见对手时准星离头部的距离"；
  - 旧名 `{对象.crosshair}` 报 `unknown_slot`，提示改用这两个之一。
- **说法检查**，在 `[[checker.phrases]]` 里配置：
  - 句子里出现"刚看见""看见对手时""初始""离头""偏差"这类描述准星位置的词，又引用了 `crosshair_turn` 时，报 `metric_mismatch`；
  - 反过来，"转过""甩枪幅度"这类描述转动幅度的词搭配 `crosshair_offset` 时，同样报 `metric_mismatch`。
- **总览**：本场命中了 `poor_crosshair_placement` 时，总览里不允许出现整场 Crosshair Placement 的数值或"准星不差 / 不落后 / 优于"这类评价，报 `overview_contradiction`。规则写进配置，不写死在代码里。

### 3. 训练目标的数字只能来自程序

- `training` 和 `training_plan` 中：
  - **允许**练习量：几组、几次、几遍、几分钟、每周几次；
  - **不允许**成功率或达标标准：百分比、"几成"、"X 发中 Y 发""至少中""命中率低于 / 不低于 / 达到"等；
  - 不允许的写法报 `training_target_number`。正则放进配置 `checker.training_forbidden_patterns`。
- 提示词里说明：达标标准用"直到动作稳定""下次复盘对比"这类说法，不写具体的命中率。

### 4. 提示词

- 在 `SYSTEM_PROMPT` 和多场汇总的提示词里，按上面三点更新写法要求。改动保持在固定部分，不要破坏 S05 的"系统提示词逐字固定"测试。

## 验收标准

- [ ] **单元测试**：
  - **`{对象.basis}`**：渲染结果和 `evidence_sentence` 相同（去掉句末句号）；在训练计划或总览里使用时报错；
  - **对比占位符**：分析里写 value/threshold/gap 时报 `comparison_slot_removed`；
  - **准星口径**：搭配正确时不报，搭配错误时报 `metric_mismatch`，正反两个方向各一对样本；
  - **总览矛盾**：命中准星问题时说"准星不差"报 `overview_contradiction`；没有命中时照常允许；
  - **训练目标**：练习量通过（"每组 20 次，共 3 组"），达标标准不通过（"命中率达到 90%""10 发至少中 8 发""九成"）；
  - **回归**：用改动前基线报告中的原句做样例，至少各一句：
    - "从看见到第一次命中一共用了{…value}，职业同武器 75 分位是{…threshold}"；
    - "准星已经转过{…crosshair}"出现在"刚看见对手时准星离他很远"的句子里；
    - "前 10 发全部落在靶心区域"。
    - 这些在改动后都必须被拦下。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`，报告只贴最后一行。
- [ ] **不调用任何付费 API**。改动后的评测由 Claude 运行，对比基线的人工核对错误数、违规数和轮数。
- [ ] `privacy_scan.py` 退出码为 0；完成报告中玩家名替换成"（已匿名）"。

## 范围外（不要做）

- 不改检测规则、事实卡内容、`evidence_sentence` 的措辞。
- 不改提交格式的整体结构，只调整占位符和检查。
- 不减少 AI 的分析篇幅要求。

---

## 完成报告（实现方填写，尽量简短）

- 改动的文件及原因：`slots.py`、`checker.py`、`config.py`、`default.toml`、`ai/prompts.py`：依据句占位符、准星双口径、总览和训练目标检查及固定提示词；`docs/eval_log.md`：匿名化基线中的玩家名以通过隐私扫描；相关测试覆盖单场、多场及基线错句。
- 测试结果（最后一行）：`825 passed, 3 skipped in 19.67s`。`privacy_scan.py` 退出码 0（身份信息命中 0 处）；未调用付费 API。
- 更新了哪些旧测试的预期：`test_slots.py` 的 crosshair 与 value/threshold/gap 渲染预期改为双口径和 basis/停用违规；`test_checker.py` 的 match.value 改为 match.basis；`test_situation_claims.py` 的旧对比句改为停用违规与 basis 通过。
- 遗留问题 / 需要确认的地方：无。

## 审查意见（Claude 填写）

**审查通过（2026-10-17，Claude）。** 新的违规代码 `comparison_slot_removed`、`metric_mismatch`、`overview_contradiction`、`training_target_number` 和 `{对象.basis}` 都和任务单一致，正则都放在配置里；基线报告中的错句改动后都会被拦下。

Codex 还发现 Claude 写进 `eval_log.md` 的基线记录里有真实玩家名，已经匿名化。

改动后的效果，要用评测集测量。

