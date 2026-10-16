# 026 补枪窗口改为 5 秒、"压枪差"只算看见对手时的子弹、距离说法中间夹武器名

状态：待做

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
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
