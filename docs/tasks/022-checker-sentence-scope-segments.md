# 022 检查器：说法按句子核对对应的交火、"慢在哪一段"、残局与回防

状态：待做

## 目标

评测发现检查器有三个漏洞，本任务一起修。它们改的都是 `_check_entries` 里核对说法的那段循环，所以合成一个任务。

| # | 现象（评测原文） | 漏洞 |
|---|---|---|
| 1 | "慢主要出在看见到开枪的这段"。引用的 R8-death 里，看见→开枪只有 375 毫秒，整个 TTD 是 781 毫秒（`20261016-225457_mac首次_梁孙超重跑`）。019/020 基线和新基准评测里也各出现过一次 | 没有核对"慢在哪一段"这类说法 |
| 2 | "短距离交火"：说的那次是 28 米，同一条 finding 引用的另一次交火是 4.8 米（`docs/eval_log.md` 的"flash_新基准26份"） | 距离类说法只要求本条**任意一个** target 满足，没有核对这句话说的是哪一次 |
| 3 | 漏补 pattern 写"回防或残局里确实来不及"，R16 实际是 CT 方下包前 4v4 | 没有核对"残局""回防"这两个说法 |

另外还有一个顺带要修的问题：现在每条说法规则只用 `re.search` 查第一次出现，同一段文字里第二次出现的同一说法不会被核对。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/eval_log.md`："flash_新基准26份"和"mac首次"两节，里面有这些错误的原文
- `src/cs2coach/tools/checker.py`：
  - `PREDICATES`、`RECORD_PREDICATES`；
  - `_check_entries` 中 `for phrase in cfg["checker"]["phrases"]` 这段循环，包括距离类、`scope = "all"`、默认 any 三个分支
- `src/cs2coach/tools/slots.py`：
  - `SLOT` 正则；
  - `situation_text()`：程序对"残局""回防"的定义；
  - 占位符对象的写法：单场 `R8-kill1`、`R10`、`R11[2]`（第 2 条漏补记录，从 1 开始计数），多场 `M1:R7-death`、`M14:R11[2]`；
  - `R11[2]` 的解析见 `make_slot_resolver` 中处理 `round_match` 的部分
- `config/default.toml` 的 `[checker]` 和 `[[checker.phrases]]`
- `src/cs2coach/features/models.py`：
  - `Engagement` 的 `time_to_damage_s`、`seen_to_first_shot_s`、`first_shot_to_damage_s`；
  - `MissedTrade` 的 `side`、`bomb_planted`、`alive_own`、`alive_enemy`
- `scripts/eval_recheck.py`：用当前检查器重查已保存的提交，不调用模型

## 要做的事

### 1. 说法按句子核对对应的交火（只用于 any 规则和距离规则）

适用范围：默认 `scope = "any"` 的规则，以及距离类规则（`close_range`、`long_range`、`teammate_far`、`teammate_near`）。

- **每次出现都核对**：用 `finditer` 找出每一处匹配，不再只看第一处。同一条目里同一条规则报出的违规按句子去重，同一句话里出现两次只报一条。
- **确定这句话说的是哪些交火**：
  - 先把分析文字按 `。！？；` 和换行切成句子，找到匹配所在的句子；
  - 收集这句话里占位符 `{对象.字段}` 的对象，只保留本条 targets 中的对象；
  - 对应关系：瞬间编号对应该瞬间，`R10` 对应该回合的全部漏补记录，`R11[2]` 只对应第 2 条漏补记录（多场同理，带 `M序号:` 前缀）。
- **核对**：
  - 这句话里有本条 targets 的对象时，只用这些对象对应的瞬间和记录来判断；
  - 没有时，和现在一样用本条全部 targets。
- **违规说明**：要写出这句话核对的是哪些 target，便于模型修改。

### 2. "慢在哪一段"（scope = "all"，用本条全部 targets 核对）

- **新增谓词**：
  - `seen_to_shot_major`：`seen_to_first_shot_s / time_to_damage_s >= checker.segment_major_ratio`；
  - `shot_to_hit_major`：`first_shot_to_damage_s / time_to_damage_s >= checker.segment_major_ratio`；
  - 任一值缺失或 `time_to_damage_s` 不大于 0 时为 False。
- **新增配置**：`checker.segment_major_ratio = 0.5`。
- **新增两条说法规则**，`scope = "all"`。这类说法是对整个问题的概括，**不按句子定位**，用本条全部 targets 核对：现在 all 规则就是这样。
  - 下面的写法必须匹配 `seen_to_shot_major`（都是评测原文）：
    - "慢主要出在看见到开枪的这段"
    - "其中大半时间花在看见到开枪之间"
    - "共同点是从看见对手到打出第一枪的这一段偏长"
    - "慢的主要是看见→开枪这一段"
    - "都集中在开枪前"
  - 下面的写法必须匹配 `shot_to_hit_major`："主要慢在开枪到命中""大半时间花在开枪到命中"。
  - 下面的写法**不能**匹配，因为它们只是陈述分段数值，没有说"主要""大半"：
    - "其中看见到开枪占了{R27-kill3.seen_to_shot}"
    - "其中看见到开枪就占了 {R4-death.seen_to_shot}"
    - "看见→开枪 594 毫秒"
- **违规说明**：列出每个 target 的两段时间和占比，并建议改用 `{瞬间.seen_to_shot}`、`{瞬间.shot_to_hit}` 分别写出各次交火。

### 3. "残局"和"回防"（默认 any，按第 1 点按句子定位）

口径和 `situation_text()` 一致：

- `clutch`："残局"，自己这边只剩 1 人：`alive_own == 1 and alive_enemy >= 1`。
- `ct_retake`："回防"，CT 方并且已下包：`side == "CT" and bomb_planted is True`。

两个谓词都要同时加进 `PREDICATES` 和 `RECORD_PREDICATES`，漏补记录也能支持。

正则要只匹配**描述这次交火局面**的说法，不匹配建议和泛指：

- 必须匹配（评测原文）：
  - "回防或残局里确实来不及"："回防"和"残局"各匹配一次；
  - "另一次回防中也是拉出撞上守点的 AWP"
  - "残局对枪中"
  - "这次残局击杀里"
  - "一次残局里你主动拉出时"
- 不能匹配：
  - "先用燃烧瓶封住对手可能回防的通道"：说的是对手回防；
  - "可以作为后续处理残局的参考"：泛指；
  - "局面从开局对枪到残局"：泛指，句末没有接交火。

## 接口与数据

- 新增配置键：`[checker]` 下的 `segment_major_ratio = 0.5`，要在默认配置中声明并通过配置校验。
- 新增 4 个谓词：`seen_to_shot_major`、`shot_to_hit_major`、`clutch`、`ct_retake`。
- 新增 4 条 `[[checker.phrases]]`，每条都写上注释，说明来自哪次评测。
- 违规代码沿用 `phrase_unsupported`，不新增代码。

## 验收标准

- [ ] 测试，在 `tests/test_situation_claims.py` 或新文件中：
  - 按句子定位：
    - 一条 finding 有两个 target，分别是 4.8 米和 28 米。"28 米那次"的句子只含 28 米那个 target 的占位符，写"短距离交火"要报错；
    - 句子里没有占位符时，仍按现在的"任意一个"规则通过；
    - `R11[2]` 只对应第 2 条记录：第 1 条满足、第 2 条不满足时报错；
    - 同一段文字里第二次出现的说法也会被核对；
  - 分段：
    - 上面列出的必须匹配的写法，在任一 target 占比低于 0.5 时报错，全部不低于 0.5 时通过；
    - 必须不匹配的写法不报错；
    - TTD 缺失时报错；
  - 残局与回防：
    - 必须匹配的写法在数据不满足时报错、满足时通过；
    - 必须不匹配的写法不报错；
    - 漏补记录满足 `clutch` / `ct_retake` 时通过；
  - 多场的 `M1:R7-death`、`M14:R11[2]` 对象也能定位。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **回归**：用 `eval_recheck.py` 重查下面两次评测（不调用模型），在完成报告里贴出**新增**的违规：
  - `.venv/bin/python scripts/eval_recheck.py "data/output/评测/20261016-225457_mac首次_梁孙超重跑"`，应新增：
    - `findings[1]`（slow_reaction）："慢主要出在看见到开枪"，R8-death 占比不足；
    - `patterns[1]`（slow_reaction）："从看见对手到打出第一枪的这一段偏长"，R8-death、R19-kill1 占比不足；
    - `patterns[0]`（missed_trade）："回防""残局"各一条，这句话里定位到的是 R9、R10、R11[2]、R16，全部不满足。
  - `.venv/bin/python scripts/eval_recheck.py "data/output/评测/20261016-222717_mac首次" --only 完美,卢文笙,ZywOo,多场`：
    - 这几份里"残局""回防""近距离""看见到开枪"的写法都是对的，**不应新增违规**；
    - 如有新增，逐条说明是误伤还是确实写错了。
    - 不要重查这一次的梁孙超：那份是在视野计算失败的数据上生成的。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改 `scope = "all"` 规则的核对范围：仍用本条全部 targets，不按句子定位。
- 不改总览（`_check_overview`）和训练文字的检查。
- 不核对"身边没有其他队友"这类说法：没有可靠数据，以后另议。
- 不改提示词。如果认为提示词也需要补充，写进完成报告。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
