# 018 视野：露出部分身体、只在一个时刻判断导致的"没看见"误判

状态：完成

## 目标

现在事实卡里的"按视野计算，你被击杀前没有看见对手"有一半以上是错的。产品负责人在游戏里核对了两例，确认当时都能看见对手。本任务修正视野判断，让 `visibility_note == "not_seen"` 只表示"死前（或击杀前）一段时间内确实没看见过对手"。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/features.md`："视野"一节（第 42 行起）、`visibility_note` 表格行、第 113 行的覆盖率统计
- `src/cs2coach/features/visibility.py`：`Sight.body_points`、`in_view`、`sees`、`first_seen`、`smoke_between`
- `src/cs2coach/features/moments.py`：约 235–276 行（锚点 `my_dmg` / `tick - 1`、`note` 的判定），约 493 行（`died_without_seeing` 标签）
- `src/cs2coach/features/models.py`：`Engagement.visibility_note` 及其注释
- `src/cs2coach/report/facts.py`：`_engagement_facts`
- `src/cs2coach/tools/digest.py`：`VISIBILITY_NOTES`
- `config/default.toml`：`[visibility]`

## Claude 已验证的事实（2026-10-16）

调查脚本在 `C:\Users\David\AppData\Local\Temp\claude\c--Users-David-DemoAnalysis\a6b2160f-ca86-4fdc-bd1d-cc716d5bd243\scratchpad\`：`vision_victim.py`（阵亡分类）、`vision_kills.py`（击杀者视角）、`vision_case.py`（逐 tick 查看）、`vision_miss.py`（扩展身体点的写法）。完整误判清单：同目录下的 `视野误判清单.md`，共 41 次。

**1. 误判比例。** 4 场 demo，按每名玩家分别计算，共 631 次阵亡（不含穿墙、穿烟）。其中 73 次判为 `not_seen`：

| 实际情况 | 次数 |
|---|---:|
| 死前 0.5 秒内看见过击杀者 | 20 |
| 死前 0.5–3 秒内看见过 | 16 |
| 对手只在屏幕边缘，或只露出部分身体（扩展检测点和屏幕判断后才可见） | 5 |
| 有视线，但对手不在屏幕内（侧面、背后） | 26 |
| 完全没有视线 | 6 |

前三类（41 次）属于误判。4 场 demo 是：
- `data/demos/5e_demo/g173-n-20261014223216271995270_de_mirage.zip`
- `data/demos/5e_demo/g173-n-20261014233811572950094_de_dust2.zip`
- `data/demos/perfectworld_demo/9215177492731139980_0.zip`
- `data/demos/5e_demo/g161-20260929221613531950574_de_inferno.zip`

**2. 原因一：露出部分身体时检测不到。**

例子：Mirage（`g173-n-20261014223216271995270`）R15，妮妮孩孩major冠军（`76561199703310441`）阵亡，tick 101902，击杀者是喑海。产品负责人截图确认，喑海蹲在柜台后，露出头和肩。

逐 tick 的结果：
- tick 101892–101901：现有 6 个点（头 46、胸 28、腰 14、脚 6、胸口两侧）全部被挡住，`sees` 为 False；
- 同一段时间，头两侧（高度 62/44，左右 ±6）和两肩（高度 54/38，左右 ±16）可见；
- 本人第一次造成伤害在 tick 101901，是锚点，于是判为 `not_seen`。

**3. 原因二：只在一个时刻判断。**

例子：同一场 R7，喑海（`76561199753730524`）阵亡，tick 50003，击杀者是妮妮孩孩major冠军。截图确认击杀者站在空地上，完全可见。

- 喑海第一次造成伤害在 tick 49924（胸口，23 点）。这一刻 15 个检测点全部被挡住，可能是穿透，也可能是地图网格误差；伤害事件没有穿墙标记。
- tick 49964–50000，全身可见；第二、三次伤害（49983、49989）都在可见时打出。
- 现有逻辑只看锚点（第一次伤害）前后 2 个 tick，于是判为 `not_seen`。

**4. 多加检测点不会把"看不到"误判成"看得到"。**

- 普通击杀前一 tick，击杀者看得到受害者的比例：现有点 99.5%，扩展到 15 个点后 99.8%（6 场 demo，920 次）。
- 穿墙击杀中被判为可见的比例：71% → 81%（21 次）。原本就偏高，因为门、薄墙不在网格里，已在 `docs/features.md` 的局限中注明。

## 要做的事

### 1. 身体检测点改为可配置的列表

在 `[visibility]` 中用一个新键替换 `body_points_standing`、`body_points_crouched`、`body_side_offset`：

```toml
# 每项 [站立高度, 下蹲高度, 横向偏移]；高度相对脚底，按 duck_amount 插值；
# 横向偏移沿"垂直于视线的水平方向"，正负各一项
body_points = [
  [64.0, 46.0, 0.0],   # 头
  [40.0, 28.0, 0.0],   # 胸口
  [20.0, 14.0, 0.0],   # 腰
  [6.0, 6.0, 0.0],     # 脚
  [40.0, 28.0, 14.0], [40.0, 28.0, -14.0],   # 胸口两侧
  [70.0, 52.0, 0.0],   # 头顶
  [62.0, 44.0, 6.0], [62.0, 44.0, -6.0],     # 头两侧
  [54.0, 38.0, 16.0], [54.0, 38.0, -16.0],   # 两肩
]
```

- 烟雾判断（`smoke_between`）仍用胸口高度。新增配置 `chest_height = [40.0, 28.0]`，不要依赖列表顺序。
- `config.py` 校验：每项是 3 个数，高度 ≥ 0。

### 2. 屏幕范围：任意一个点在屏幕内即可

`sees` 改为：存在某个点，既在屏幕内（`in_view`），又没被挡住。现在的写法只检查胸口在不在屏幕内。

### 3. 按时间段区分"没看见"

在 `moments.py` 判定 `note` 时，如果锚点附近看不到（现在的 `not valid(me)` 且不是烟雾、`seen_at_limit` 等情况），按下面的顺序细分：

1. **`first_hit_unseen`**：锚点是本人第一次造成伤害，且从锚点之后到事件前一 tick 之间看见过对手。
   - 事实卡："你第一次打中对手时看不到他（可能是穿透或掩体边缘），之后看见了。"
2. **`lost_sight`**：在锚点之前的回溯窗口内（与 `first_seen` 相同：`max(floor, 锚点 - lookback)` 到锚点）看见过对手。
   - 记录 `last_seen_before_s`：最后一次看见的 tick 到事件 tick 的秒数。
   - 事实卡："你在 X 秒前看见过对手，之后视线中断。"（X 保留一位小数）
3. **`not_seen`**：以上都不满足。
   - 新增 `opponent_off_screen: bool | None`：锚点前后 `gap` 个 tick 内，不考虑屏幕范围时有视线（`sees(..., need_view=False)`）。
   - 事实卡：
     - 为 True："对手在你的视野之外（侧面或背后），你被击杀前没有看见他。"击杀时写"……你击杀前没有看见他"。
     - 否则保留现有句子。

要求：

- 新的两种说法不计算 TTD、看见到开枪、开枪到命中、准星移动，与 `not_seen` 相同。这次不改这些指标的口径。
- `died_without_seeing` 标签只在 `not_seen` 时添加，不变。
- `VISIBILITY_NOTES`、`Engagement.visibility_note` 的注释、`docs/features.md` 同步更新。

### 4. 职业基准重算

检测点和屏幕判断改了，"第一次看见"的时刻会变，TTD、准星也会跟着变。职业基准必须用同一套算法重算，否则对比不公平。

- 运行 `python scripts/build_baseline.py`，覆盖 `src/cs2coach/baselines/pro_sample.json`。
- 完成报告中列出重算前后的对比：
  - 整体 TTD、Crosshair Placement 中位数；
  - 各武器的 p75；
  - `problem_rates` 中 `died_without_seeing`、`slow_reaction`、`poor_crosshair_placement` 的数值。
- 测试如果依赖旧基准的具体数值，按新值更新，并在完成报告中说明。

## 验收标准

- [ ] 测试（用 `Sight` 的可注入 checker 构造场景）：
  - 只露出肩或头侧时判为可见；现有 6 个点全被挡住时，旧写法判为不可见；
  - 胸口在屏幕外、头在屏幕内时判为可见；
  - 第一次伤害时看不到、之后看得到 → `first_hit_unseen`；
  - 锚点前看见过、锚点时看不到 → `lost_sight`，`last_seen_before_s` 正确；
  - 从未看见、有视线但在屏幕外 → `not_seen`，`opponent_off_screen=True`；
  - 从未看见、没有视线 → `not_seen`，`opponent_off_screen=False`；
  - 三种情况的事实卡句子；`died_without_seeing` 只在 `not_seen` 时出现；
  - 配置校验：`body_points` 格式错误时报错。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] **真实 demo 验证**（4 场 demo，同"已验证的事实"第 1 条），完成报告中写明：
  - Mirage R15（妮妮孩孩major冠军，tick 101902）不再是 `not_seen`；预期能算出 TTD，或为 `first_hit_unseen` / `lost_sight`；
  - Mirage R7（喑海，tick 50003）为 `first_hit_unseen`；
  - 631 次阵亡中 `not_seen` 的数量，预期从 73 降到约 30–35，以及其中 `opponent_off_screen` 为 True 的数量；
  - 用 `vision_kills.py` 的方法重跑：普通击杀"可见"比例不低于 99.5%；
  - `视野误判清单.md` 的 41 次中，仍为 `not_seen` 的逐条列出；
  - 单场分析耗时变化：射线数量大约翻倍，列出修改前后同一场 demo 的耗时。
- [ ] `scripts/check_parsing.py` 照常通过（解析层没改，确认没有误伤）。
- [ ] 不调用 DeepSeek 或 Claude。

## 范围外（不要做）

- 不改 TTD、准星的起算口径（例如"从最后一段连续可见开始算"），以后另议。
- 不启用 `died_without_seeing`。重算后由 Claude 和产品负责人根据新频率决定。
- 不处理门、可破坏物体、薄墙（网格缺失），也不改烟雾模型。
- 不改检查器和提示词。检查器的 `not_seen` 谓词会自动只对新的 `not_seen` 生效。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：`config/default.toml` 改为 11 个可配置身体点与独立胸口高度；`src/cs2coach/config.py` 校验其形状、数值和高度。`features/visibility.py` 逐个点同时检查屏幕范围与几何视线，并提供时间窗口内最后一次可见 tick 的查询；`features/moments.py` 将“首次伤害时不可见但之后可见”“此前看见但视线中断”“窗口内确实没看见”分开，记录最后可见时间与屏幕外标记。`features/models.py`、`report/facts.py`、`tools/digest.py`、`docs/features.md` 同步字段和文案。`src/cs2coach/baselines/pro_sample.json` 按新算法重算。`tests/test_visibility.py`、`tests/test_facts.py` 覆盖新采样点、屏幕边缘、时间窗口、事实卡和配置校验。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：**580 passed, 3 skipped**；`scripts/check_parsing.py data/demos/5e_demo/g173-n-20261014223216271995270_de_mirage.zip`：各对照项全部一致；`git diff --check` 通过。职业基准重算 15/15 场成功、0 失败、150 人次。整体中位数：TTD **375 → 391 ms**，Crosshair Placement **3.83 → 3.88°**。各武器 p75（TTD，单位 ms；准星，单位 °）：

  | 武器 | TTD 旧→新 | 准星旧→新 |
  |---|---:|---:|
  | AK-47 | 512→531 | 9.38→9.70 |
  | AWP | 391→406 | 3.08→3.17 |
  | Deagle | 523.75→516 | 7.29→7.15 |
  | Dual Berettas | 621.25→476.5 | 11.46→12.89 |
  | FAMAS | 438→453 | 8.15→8.15 |
  | Five-SeveN | 550.75→550.75 | 11.72→11.72 |
  | Galil AR | 453→476.5 | 8.65→9.13 |
  | Glock | 531→543 | 11.39→12.11 |
  | M4A4 | 484→500 | 10.38→11.40 |
  | M4A1-S | 484→519.75 | 6.91→7.73 |
  | MAC-10 | 488→515.5 | 19.38→20.78 |
  | MP9 | 516→516 | 15.98→16.21 |
  | Tec-9 | 484.5→508 | 17.60→18.83 |
  | USP-S | 547→562 | 8.59→9.74 |

  `problem_rates`（命中数；每 10 回合）：`died_without_seeing` **206→137；0.67→0.44**，`slow_reaction` **425→422；1.38→1.37**，`poor_crosshair_placement` **501→506；1.62→1.64**。未启用 `died_without_seeing`。
- 真实 demo 验证：任务单列出的 4 场 demo 全部解析，对所有玩家逐场运行新算法。排除穿墙、穿烟后共 **631 次阵亡**，`not_seen` **73→33**，其中 `opponent_off_screen=True` **26** 次；普通击杀前可见 **620/621（99.84%）**。Mirage R15（妮妮孩孩major冠军，tick 101902）不再是 `not_seen`，TTD 为 **0.781 秒**；Mirage R7（喑海，tick 50003）为 `first_hit_unseen`，TTD 留空。原误判清单 41 条中仍为 `not_seen` 的只有 **Dust2 R14，首席狙击手祁同伟，tick 97601**：同一玩家在 tick 97600 刚完成击杀，现有交火下限被推至 97601，排除了 97600 的可见记录。Mirage 同一玩家单场 `find_moments` 热运行耗时旧 **3.57 秒**、新 **3.78 秒**（约增加 6%）；数据保存在系统临时目录的 `cs2coach-018-validation.json`，没有写入 `data/`。
- 遗留问题 / 需要确认的地方：上述 Dust2 R14 是既有“上一次击杀后才算本次交火”下限造成的边界案例；本任务明确不改 TTD 起算口径，因此保留给后续口径讨论。门、薄墙、烟雾模型局限和 `died_without_seeing` 是否启用仍按任务单留待审查。

## 审查意见（Claude 填写）

**审查通过（2026-10-16）。** 检测点、屏幕判断、三种新说法、职业基准重算都符合任务单，580 个测试通过。

Claude 用旧代码（HEAD 的工作树）和新代码跑同样 4 场 demo 的全部玩家、1390 个瞬间对比：

- 视野说法：`not_seen` 108 → 43；新增 `lost_sight` 44、`first_hit_unseen` 7。
- TTD 样本数 773 → 772，基本不变；数值变化来自更早发现露出的部分身体，路人 TTD 中位数 516 → 562 ms，职业基准同算法重算 375 → 391 ms，对比仍然公平。
- 与任务单的一处偏差：`first_hit_unseen` 在 `first_seen` 有效（允许 2 tick 误差）、但第一次伤害那一 tick 本身看不到时也会触发，会去掉这类交火的 TTD。实测 7 例原本都是 `not_seen`、都没有 TTD，没有造成样本损失，接受。
- Claude 直接修改：`VISIBILITY_NOTES` 中的"回溯窗口""锚点"是内部术语，会进入给 AI 的摘要，改为"这次交火中没有看见对手""之前看见过对手，事件发生时视线已中断"。

以后可以校准：肩部 ±16、头侧 ±6 略宽于模型实际宽度，可能把"刚到掩体边缘"判为可见（约十几毫秒）；穿墙击杀判为可见的比例 71% → 81% 也说明这一点。`died_without_seeing` 是否启用，按新频率另议：职业 0.44 次 / 10 回合。
