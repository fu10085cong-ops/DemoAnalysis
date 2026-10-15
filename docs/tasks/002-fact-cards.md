# 002 事实卡：由程序生成每个瞬间的中文事实描述

状态：待做（**依赖 001**：先完成 001，再做本任务）

## 目标

结构化报告中，"发生了什么"由程序写，不经过 AI 转述（设计见 `docs/problem_catalog.md`"用途"一节）。本任务实现事实卡：

- 把每个瞬间（`Moment`）和每次漏补枪（`MissedTrade`）的数据，用固定句式写成中文事实；
- 同时输出一个事实文件，供用户对照 demo 核对数据是否准确。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `src/cs2coach/features/models.py`：`Moment`、`Context`、`Engagement`、`Trade`、`DeathState`、`AssistInfo`、`MissedTrade`、`BlindInfo`（001 新增）
- `src/cs2coach/tools/digest.py`：
  - `moment_line()`：现有的单行摘要，给 AI 用。事实卡是它的"可读版"，取值逻辑可以参考，但不要改它；
  - `VISIBILITY_NOTES`、`CONTACT_LABELS`：可以复用。
- `src/cs2coach/features/labels.py`：`GRENADE_LABELS`、`KIND_LABELS`
- `docs/features.md`：各字段的定义
- `src/cs2coach/cli.py`：`analyze` 命令输出文件的方式

已确认的口径：

- `Context.distance` 的单位是**米**，以 `docs/parsing.md` 为准，`models.py` 里的注释已过时。
- `nearest_teammate_distance` 是游戏单位。
- 速度单位是"单位/秒"。

## 要做的事

1. **新建 `src/cs2coach/report/facts.py`**：
   - `moment_facts(m: Moment, names: Mapping[int, str]) -> list[str]`：按下方顺序返回句子，数据缺失的句子直接省略；
   - `render_moment_card(m: Moment, names: Mapping[int, str]) -> str`：标题行加上句子列表，Markdown 格式；
   - `missed_trade_facts(mt: MissedTrade, names: Mapping[int, str]) -> list[str]`；
   - `render_facts_file(mm: MatchMoments, names: Mapping[int, str]) -> str`：整场所有瞬间和漏补枪的事实卡；
   - `names` 是 steamid → 玩家名；找不到名字时用"队友"、"对手队友"代替，不报错。

2. **标题行**：
   `### 〔{id}〕{击杀/死亡/助攻}（{side}，第 {round_time_s:.0f} 秒，{alive_own}v{alive_enemy}{，已下包}）`

3. **句子的顺序和写法**（`{}` 为字段值，括号里的部分按数据有无增删；措辞可以微调，但信息和顺序必须保持一致）：
   1. 位置：`你在 {place}，对手 {opponent_name} 在 {opponent_place}，距离 {distance:.0f} 米。`
   2. 武器与装备：`你手持 {own_weapon}，对手手持 {opponent_weapon}；本回合装备价值 {own} 对 {opponent}。`
   3. 血量（任一方低于 100 时才写）：`交火前血量：你 {own}，对手 {opponent}。`
   4. 结果：
      - 击杀：`你用 {weapon}（爆头、穿烟、穿墙、盲狙）击杀了对手。`
      - 死亡：`对手用 {weapon}（……）击杀了你。`
      - 助攻：`你助攻击杀了 {opponent_name}：闪光助攻。` 或 `……：你对他造成了 {damage_to_victim} 点伤害。`
   5. 视野（`Engagement` 存在时）：
      - `seen_first_lead_s` 大于 0、小于 0、等于 0，分别写：`你先看见对手，领先 {x:.2f} 秒。` / `对手先看见你，领先 {x:.2f} 秒。` / `双方同时看见对方。`
      - `visibility_note` 不为空时，写对应说明：
        - `not_seen`：死亡写 `按视野计算，你被击杀前没有看见对手。`；击杀写 `按视野计算，你击杀前没有看见对手。`
        - `no_map_data`：`缺少地图数据，没有计算视野。`
        - 其余取值复用 `VISIBILITY_NOTES` 的文字。
   6. 开枪先后：`你先开枪，领先 {x:.2f} 秒。` / `对手先开枪，领先 {x:.2f} 秒。` / `双方同一 tick 开枪。`；目标玩家没开枪时写 `你没有朝对手开枪。`
   7. 接触：`接触时你的速度 {a:.0f}、对手 {b:.0f} 单位/秒：{CONTACT_LABELS[contact_type]}。`
   8. 枪法：
      - `你开了 {shots_fired} 枪，命中 {shots_hit} 枪；第一枪{命中/未命中}，开第一枪时{已停稳/未停稳}。`
      - `从看见对手到第一次造成伤害用了 {ttd} 毫秒（看见→开枪 {a} 毫秒，开枪→命中 {b} 毫秒）。`
      - `看见对手时，准星与对手的角度差 {cp:.1f}°。`
      - `看见对手之前，你已朝他开了 {shots_before_seen} 枪。`
   9. 对手出枪：`对手从看见你到第一次造成伤害用了 {opponent_ttd} 毫秒。`
   10. 闪光（001 的 `BlindInfo`）：
       - 对手：`对手在交火中被闪：{击杀或死亡时还剩 {x:.1f} 秒 / 击杀或死亡时已经恢复}，这次致盲最长 {y:.1f} 秒，{投掷者}。`
       - 自己：同样的写法，主语换成"你"。
       - 投掷者按 `thrower_role` 写：`self` → `是你自己扔的闪`；`teammate` → `是队友 {name} 扔的闪`；`opponent` → `是对手自己扔的闪`；`enemy` → `是对手的队友 {name} 扔的闪`；None → `投掷者无法确定`。
   11. 死亡状态：
       - `weapon_ready` 为 False：`你阵亡时武器还不能开枪：上一枪（{last_shot_weapon}）到阵亡只有 {gap:.2f} 秒，该武器两枪的最短间隔是 {interval:.2f} 秒。`
       - 死时被闪：**只有当 `engagement.player_blind` 为 None 时**才用 `DeathState.blinded` 写一句，避免重复。
       - `你阵亡时身上还有：{烟雾弹、闪光弹}。` 或 `你阵亡时身上没有道具。`
       - `本回合你在阵亡前投出了：{闪光 ×2、烟}。` 或 `本回合你在阵亡前没有投出道具。`
   12. 补枪：
       - 被补回：`你阵亡后 {trade_delay_s:.1f} 秒，队友 {name} 击杀了对手（补回）。`
       - 没被补回：`你阵亡后没有被补回；当时最近的存活队友距离你 {d:.0f} 单位。` 或 `……；当时附近没有存活的队友。`
       - 补枪击杀：`这是一次补枪：队友阵亡 {avenge_delay_s:.1f} 秒后，你击杀了击杀他的对手。`

4. **漏补枪**：
   `### 〔R{round}〕漏补枪`
   `队友 {name} 阵亡时离你 {distance:.0f} 单位，你在补枪时间窗口内没有击杀击杀他的对手{；对手随后被其他队友击杀}。`

5. **措辞要求**：事实卡只陈述事实，**不做评价**。不得出现"失误、应该、太慢、不够、错误、问题、建议"这类词，测试要检查这一点。

6. **CLI**：`analyze` 命令在写出 `_moments.json` 的同时，写出 `<stem>_<sid>_facts.md`（整场的事实卡）。玩家名取自 `match.players`。不需要新增命令行参数。

## 验收标准

- [ ] 新建 `tests/test_facts.py`，用 `tests/test_moments.py` 中的 `scenario()` 和 `tests/helpers/synthetic.py` 构造瞬间，覆盖：
  - 一次死亡：被闪、没开枪、身上有道具、没被补回。逐句断言**完整句子**，确定性输出用字符串完全相等来检查；
  - 一次击杀：对手被队友闪中（`thrower_role == "teammate"`），名字正确显示；
  - 一次助攻（没有 `Engagement`）：不报错，没有视野和枪法的句子；
  - 视野不可用（`visibility_note == "no_map_data"`）；
  - 名字缺失时用"队友"代替；
  - 所有测试场景生成的文本都不包含第 5 条列出的评价词；
  - 漏补枪的事实卡。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 真实 demo 验证：
  ```
  .venv/Scripts/python.exe -m cs2coach analyze data/demos/5e_demo/.extracted/g173-n-20261014223216271995270_de_mirage/g173-n-20261014223216271995270_de_mirage.dem --player 76561199711881069
  ```
  生成 `_facts.md`。完成报告中贴出其中 3 张卡（至少一次击杀、一次死亡），检查没有 `None`、`nan`、空括号、重复的句子。**不要加 `--ai`。**

## 范围外（不要做）

- 不改 `digest.py` 的 `moment_line()` 和给 AI 的摘要。
- 不改 AI 提示词和调用流程，不做结构化报告（后续任务）。
- 不改任何指标的计算。发现数据看起来不对时，写进完成报告的"遗留问题"。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
