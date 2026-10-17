# 026 补枪窗口改为 5 秒、"压枪差"只算看见对手时的子弹、距离说法中间夹武器名

状态：完成

## 目标

三处改动，都来自 2026-10-17 用完美 demo `9220525551542050188_0` 做的对比和 AI 报告核对。前两处会改变问题检测结果，所以职业基准要重建一次。

| # | 现象 | 改动 |
|---|---|---|
| 1 | 补枪：完美平台的窗口约 5 秒，按 5 秒算时 10 人中 9 人和平台一致；我们是 3 秒，补枪数只有平台的一半左右 | 产品负责人确定：**补枪窗口改为 5 秒** |
| 2 | 这场的 R4-death 被判为"压枪差"，但 13 枪里有 11 枪是看见对手**之前**打的（预瞄穿点或隔着掩体扫射），不是压枪失误。它还成了体检卡"最该练的一件事"的依据 | "压枪差"只统计**看见对手时**打出的子弹，职业基准的分段命中率也按同一口径重算 |
| 3 | AI 报告写了"近距离 AK 对拼"，实际是 19 米，检查器没拦住：规则只认"近距离对拼"这种紧挨着的写法 | 距离说法和交火名词之间允许夹武器名 |

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `config/default.toml`：`[moments]` 的 `trade_window_seconds`、`[problems]` 中 spray 相关参数、`[[checker.phrases]]` 中 `close_range` / `long_range` 两条
- `src/cs2coach/features/moments.py`：`_Params.trade`、`trade_info`、`find_missed_trades`、`_shot`
- `src/cs2coach/features/models.py`：`Shot`、`Engagement`
- `src/cs2coach/features/problems.py`：单次交火的 `spray_control`，以及停用中的 `spray_while_moving`
- `src/cs2coach/baseline.py`：`spray_stats`（职业分段命中率，也用于整场 `match` 目标）、`problem_rates`
- `src/cs2coach/features/aim_stats.py`：整场 Spray Accuracy 只算"看见敌人时"的子弹，判定方法是 `sight.enemies_seen(sid, 开枪 tick - 1)`
- `src/cs2coach/features/visibility.py`：`Sight` 的视线判定接口
- `src/cs2coach/tools/checker.py`：022 实现的距离说法核对（按句子定位）
- `scripts/build_baseline.py`、`src/cs2coach/baselines/pro_demos.toml`：重建职业基准
- `docs/features.md`：补枪、漏补、压枪的口径说明

## 要做的事

### 1. 补枪窗口改为 5 秒

- `moments.trade_window_seconds` 改为 `5.0`。补枪击杀、被队友补回、漏补、及时补枪高光都读这一个值，不要在别处另设窗口。
- 025 体检卡的口径说明从配置读秒数，会自动变成"5 秒"。确认一下即可。
- `docs/features.md` 中写到 3 秒的地方同步改掉，并注明依据：和完美平台对比，5 秒时 10 人中 9 人一致。

### 2. "压枪差"只统计看见对手时的子弹

- **`Shot` 新增 `seen_opponent: bool | None`**：开枪前一 tick，目标玩家能不能看见**这次交火的对手**。
  - 判定方法和 `aim_stats` 的 `enemies_seen` 相同：同样的地图几何视线、同样的"前一 tick"，只是对象从"任意敌人"换成"这名对手"；
  - 视野不可用时为 None。
- **单次交火的 `spray_control`**：参与计算的 `late` 子弹只取 `seen_opponent is True` 的。
  - 过滤后不足 `spray_min_late_shots` 发时，这次交火不评价，和现在发数不够时的处理一样；
  - 证据里增加 `excluded_unseen_shots`，记下被排除的发数。
- **停用中的 `spray_while_moving`** 用同样的过滤，保持一致。
- **`baseline.spray_stats`** 也只统计 `seen_opponent is True` 的子弹。职业分段命中率、单场整场 `match` 目标的压枪统计都用这个函数，口径自动一致。
- `seen_opponent` 为 None 的子弹不计入，也就是视野不可用时不评价压枪。

### 3. 距离说法中间夹武器名

- `close_range` / `long_range` 两条规则，在距离词和交火名词之间，允许出现一个武器名，武器名前面可以带"用 / 拿 / 持"，前后可以有空格或"的"。
- 交火名词补上"对狙"。
- **武器名清单**放进配置 `checker.range_weapon_words`，包括：
  - `WEAPON_LABELS` 的显示名，例如 AK-47、M4A1-S、Desert Eagle；
  - 常见简称：AK、M4、AWP、沙鹰、大狙、步枪、手枪、冲锋枪。
- **022 的按句子定位必须继续正确**。如果实现时要先替换武器名再匹配，替换后的文字位置要和原文对得上，或者改用不移动位置的写法。
- **按结构写正则**，不要只照着下面的例句拼（参考 022 第一轮审查意见）。
  - 必须匹配：
    - "近距离 AK 对拼"：评测原文；
    - "近距离的 AWP 对狙"
    - "远距离用 AK 对枪"
    - "短距离拿沙鹰对枪"
    - "中远距离的步枪交火"
  - 不能匹配：
    - "近距离和对枪节奏一快就更容易吃亏"：评测原文，属于建议；
    - "避免在中远距离与步枪正面对枪"：评测原文，属于建议；
    - "近距离"后面没有交火名词的句子。

### 4. 重建职业基准

- 前两处改动会改变职业选手的补枪、漏补、压枪检测和分段命中率。用 `.venv/bin/python scripts/build_baseline.py` 按 `pro_demos.toml` 重建 `src/cs2coach/baselines/pro_sample.json`。职业 demo 在 `data/demos/pro_demo/`，耗时可能几十分钟。
- **在完成报告里列出前后对比**：
  - `problem_rates` 中 `missed_trade`、`spray_control`、`good_trade`（如有）的频率；
  - AK-47、M4A4、M4A1-S 三把枪各分段命中率的变化；
  - 其他指标（TTD、准星等）应该不变；有变化要说明原因。

## 接口与数据

- `Shot.seen_opponent: bool | None = None`。旧缓存会因为特征层代码哈希变化而自动失效。
- 新增配置键 `checker.range_weapon_words`，在默认配置中声明，并通过配置校验。
- `spray_control` 证据新增 `excluded_unseen_shots: int`。
- 不新增违规代码，沿用 `phrase_unsupported`。

## 验收标准

- [ ] 测试：
  - **补枪窗口**：合成数据中队友阵亡后 4 秒补枪，算作补枪击杀；6 秒不算。漏补也按 5 秒判断。
  - **`seen_opponent`**：看见、没看见、视野不可用三种情况。
  - **`spray_control`**：
    - 看见前打的子弹被排除；
    - 排除后不够发数时不评价；
    - 证据中的 `excluded_unseen_shots` 正确；
    - 全部在看见后打的情况，结果和改动前相同。
  - **`spray_stats`**：没看见的子弹不计入分段。
  - **距离说法**：上面全部正反例都有测试；"近距离 AK 对拼"用在 19 米的交火上要报错，用在 8 米的交火上要通过；022 的按句子定位测试全部仍然通过。
- [ ] 全部测试通过：`.venv/bin/python -m pytest -q`。
- [ ] **真实数据**：运行 `.venv/bin/python -m cs2coach analyze data/demos/perfectworld_demo/9220525551542050188_0.zip --player <data/eval/eval_set.toml 第一个 case 的 player> --out "$TMPDIR/cs2coach-026"`，**不要加 `--ai`**，然后：
  - **同场表的补枪击杀**应该和完美平台对上。下面按同场表的 K-D 标识每名玩家，括号里是完美平台的数字：

    | K-D | 补枪击杀 | 被队友补回 |
    |---|---:|---:|
    | 20-12 | 1（1） | 2（2） |
    | 5-11 | 1（1） | 3（3） |
    | 8-14 | 1（1） | 2（2） |
    | 9-17 | 1（1） | 3（3） |
    | 8-16 | 2（2） | 3（2） |
    | 20-9 | 5（5） | 2（2） |
    | 19-7 | 2（2） | 1（1） |
    | 11-14 | 4（3） | 1（1） |
    | 12-9 | 1（1） | 2（2） |
    | 12-15 | 3（3） | 2（2） |

    第一个数是 Claude 按"阵亡后 5 秒内击杀击杀者"算出的预期值。补枪击杀要逐行一致；"被队友补回"在本任务的同场表里没有，但如果 `MatchMoments` 中目标玩家的 `traded` 标签与上表 19-7 那一行不一致，要说明原因。
  - **R4-death 不再被判为 `spray_control`**；
  - 体检卡里的"最该练的一件事"可能随之改变，把新的结果贴进完成报告；
  - `problems.md` 中漏补次数的变化也要写明。
- [ ] 职业基准已重建，前后对比写进完成报告。
- [ ] 不调用 DeepSeek、Claude 或本地模型。

## 范围外（不要做）

- 不改其他问题的检测规则和阈值参数，例如 `spray_margin_pct`、`spray_min_late_shots`。
- 不改 `aim_stats` 的整场 Spray Accuracy：它本来就只算看见敌人时的子弹。
- 不改 AI 提示词。
- 不重跑 AI 评测：会花钱，由 Claude 在审查通过后，经产品负责人同意再跑。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `config/default.toml`：统一补枪窗口设为 5 秒；增加距离说法可夹的武器词表和结构化正则。`src/cs2coach/config.py`、`src/cs2coach/tools/checker.py`：校验词表，在原文中展开武器名并匹配，保持句子位置。
  - `src/cs2coach/features/models.py`、`src/cs2coach/features/moments.py`：每发子弹记录开枪前一 tick 对本次交火对手的可见性；视野不可用为 None。补枪窗口沿用 `_Params.trade`。按用户确认的口径，击杀记录增加 `avenged_teammate_count`，同一击杀替两名队友报仇时记两人。
  - `src/cs2coach/features/problems.py`、`src/cs2coach/baseline.py`：单次压枪、停用中的移动扫射和职业分段压枪只算 `seen_opponent is True` 的子弹；单次压枪命中证据增加 `excluded_unseen_shots`。
  - `src/cs2coach/features/lobby.py`：同场表“补枪击杀”按被补队友人数累计，旧的仅有 `trade_kill` 标签的数据仍按一次计。`docs/features.md` 同步补枪、压枪口径及基准生成日期；`tests/` 增补边界、过滤、词组和真实口径对应的合成测试。
  - `src/cs2coach/baselines/pro_sample.json`：执行 `.venv/bin/python scripts/build_baseline.py` 重建，26 份 demo 全部成功、0 失败、260 人次。未新增依赖。
- 职业基准前后对比（每十回合次数；括号内为命中数）：

  | 问题 | 旧 | 新 |
  |---|---:|---:|
  | `missed_trade` | 0.84（471） | 1.00（563） |
  | `spray_control` | 0.21（116） | 0.16（92） |
  | `good_trade` | 0.99（558） | 1.24（697） |

  三把枪的连射分段命中率，旧 → 新：

  | 连射序号 | AK-47 | M4A4 | M4A1-S |
  |---|---:|---:|---:|
  | 0 | 35.86% → 36.82% | 38.68% → 39.80% | 37.94% → 39.04% |
  | 1–2 | 47.09% → 49.30% | 47.39% → 49.75% | 50.24% → 52.14% |
  | 3–5 | 48.18% → 52.07% | 52.42% → 56.65% | 57.63% → 60.53% |
  | 6–9 | 37.77% → 42.38% | 41.48% → 45.65% | 46.39% → 48.66% |
  | 10–14 | 27.68% → 30.46% | 35.94% → 36.75% | 40.28% → 40.00% |
  | 15+ | 45.61% → 51.22% | 44.30% → 43.59% | 33.33% → 33.33% |

  `metrics` 全部与旧基准相同，`weapons` 全部相同，包括 TTD、准星等分布。`spray` 有效发数减少：AK-47 各段从 1910/2788/1978/707/224/57 变为 1847/2647/1811/623/197/41；M4A4 从 517/842/660/311/128/79 变为 500/796/609/276/117/78；M4A1-S 从 738/1256/944/360/72/9 变为 707/1193/874/335/70/9。
- 测试结果：`.venv/bin/python -m pytest -q`：675 passed、3 skipped；`git diff --check` 通过。覆盖 4/6 秒补枪与漏补、三态视野及“看见其他敌人但没看见交火对手”、压枪排除和最低发数、职业分段、距离说法正反例及按句子定位。
- 真实 demo 验证：无 `--ai` 分析 `data/demos/perfectworld_demo/9220525551542050188_0.zip`，目标玩家取 `data/eval/eval_set.toml` 第一个 case，输出到 `$TMPDIR/cs2coach-026`。同场表十行按 K-D 的补枪击杀为 20-12:1、5-11:1、8-14:1、9-17:1、8-16:2、20-9:5、19-7:2、11-14:4、12-9:1、12-15:3，逐行符合任务表。目标玩家 19-7 的 `traded` 死亡为 1 次，与表中“被队友补回”一致。R4-death 的后段可见子弹仅 3 发，不再命中 `spray_control`；体检卡“最该练的一件事”从压枪变为预瞄；`problems.md` 漏补枪从 1 次变为 3 次。体检卡口径显示“补枪按 5 秒窗口统计”。
- 遗留问题 / 需要确认的地方：按用户确认的“被补队友人数”口径，一次击杀可以记为两次补枪，因此本次任务表十行全部对齐；完美平台对 12-15 行记 2 次，我们记 3 次，仍为 10 人中 9 人与平台一致。15+ 连射段样本较少，M4A1-S 仍只有 9 发。

## 审查意见（Claude 填写）

**审查通过（2026-10-17）。**

- **补枪窗口**：统一读 `trade_window_seconds = 5.0`。按产品负责人与 Codex 确认的口径，同一次击杀替两名队友报仇时记两次（`avenged_teammate_count`），这和完美平台的计数方式一致。
- **子弹是否看见对手**（`Shot.seen_opponent`）：用 `sight.sees(自己, 对手)` 在开枪前一 tick 判定。`aim_stats` 的 `enemies_seen` 内部也是对每个敌人调用 `sees`，口径一致。
- **"压枪差"和职业分段**：都只用看见对手时的子弹。职业基准重建后，`metrics`、`weapons` 不变，只有 spray 分段和 problem_rates 变化，符合预期。
- **距离说法**：正则改为在原文上展开 `{weapon}` 后匹配，不影响按句子定位。任务单里的全部正反例都正确；Claude 另加的"贴脸 M4A1-S 对枪""近距离用MP9交火""远距离 AWP 对狙"也能匹配。

Claude 自己验证：675 个测试通过。用真实数据重跑：
- 补枪击杀逐行和验收表一致；口径说明显示"5 秒"；
- R4-death 的 13 枪里只有 7 枪看见对手时打出，后段可见子弹不够发数，不再判为 `spray_control`；
- "最该练的一件事"从压枪变为预瞄；漏补从 1 次变为 3 次。

**更正完成报告的一处笔误**：和平台不一致的是 11-14 那一行（我们 4 次，完美 3 次），不是 12-15。

不阻塞的小缺口：
- 武器名大小写敏感，"近距离 ak 对拼"不匹配；
- 武器词表里有"手雷""燃烧""刀"，可以留着，但它们不是对枪武器；
- 15+ 连射段职业样本很少，M4A1-S 只有 9 发。

