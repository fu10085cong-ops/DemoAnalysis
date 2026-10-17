# 004 职业基准 v2：15 张图，加入按武器的统计

状态：完成

## 目标

1. 职业基准从 1 场 BO3（30 人次）扩大到 5 场 BO3、15 张图（约 150 人次）。
2. 新增**按武器**的逐次交火分布和**按发数段**的压枪命中率。问题目录（`docs/problem_catalog.md`）的武器专项阈值都从这里读取。

## 背景（必须读）

- `CLAUDE.md`、`AGENTS.md`
- `docs/problem_catalog.md`：第一节"用途与原则"（交火武器的认定），第二部分各武器的表格。表中的数字就是本任务要用程序重新算出来的数，可以拿来对照
- `src/cs2coach/baseline.py`、`scripts/build_baseline.py`：现有基准的生成、读取和对比
- `src/cs2coach/features/models.py`：`Engagement`、`Shot`、`Moment`
- `src/cs2coach/features/labels.py`：`WEAPON_LABELS`。`Context.own_weapon` 是**显示名**（如 "AK-47"），要反查成规范名
- `config/default.toml` 的 `[aim_stats]`：`time_to_damage_max_seconds`

已验证的事实：

- **职业 demo**（都在 `data/demos/` 下）：

  | 压缩包 | 包内文件 |
  |---|---|
  | `esl-pro-league-season-24-vitality-vs-mouz-bo5-HHVdkDbJrwRclevPphxD2-.rar` | `vitality-vs-mouz-m1-cache.dem`、`-m2-inferno.dem`、`-m3-dust2.dem` |
  | `esl-pro-league-season-24-falcons-vs-natus-vincere-bo3-3Y4ykAiGXZrr2p0dvri2C9.rar` | `falcons-vs-natus-vincere-m1-inferno.dem`、`-m2-anubis.dem`、`-m3-mirage-p1.dem`、`-m3-mirage-p2.dem` |
  | `esl-pro-league-season-24-falcons-vs-spirit-bo3-7qoiS3mfVYinuAhEY9FtFF.rar` | `falcons-vs-spirit-m1-mirage.dem`、`-m2-anubis.dem` |
  | `esl-pro-league-season-24-mouz-vs-spirit-bo3-sqctTxs0ADRlg-g18Xysab.rar` | `mouz-vs-spirit-m1-dust2.dem`、`-m2-mirage.dem`、`-m3-nuke.dem` |
  | `esl-pro-league-season-24-vitality-vs-falcons-bo3-tjOq40YPwMI3QHOotPr9e9.rar` | `vitality-vs-falcons-m1-anubis.dem`、`-m2-dust2.dem`、`-m3-mirage.dem` |

  包内文件名都带有对局前缀，例如 `vitality-vs-mouz-m2-inferno.dem`。

- **mirage-p1、p2 是同一张图因服务器重启拆成的两段**（`docs/fields.md` 陷阱 22），按两场分别处理即可。
- **p1 中有一名覆盖率为 0% 的非选手**（@reLazffs，热身时进过队伍）。覆盖率低于 `[parsing].min_target_coverage` 的玩家一律跳过。
- 每场解析约 40 秒，15 场约 10 分钟。

## 要做的事

1. **新建清单 `src/cs2coach/baselines/pro_demos.toml`**，列出上面 15 场。每项包含：
   - `archive`：相对于仓库根目录的路径；
   - `member`：包内文件名；
   - `event`：如 "ESL Pro League S24"；
   - `teams`：如 "Vitality vs MOUZ"；
   - `map`。

   以后加新的职业 demo 只改这个清单。

2. **改写 `scripts/build_baseline.py`**：
   - 默认读清单（保留传 demo 路径的旧用法也可以）；
   - 逐场 `parse_match` → 对每名合格选手运行 `find_moments`；
   - 某场失败时打印原因并跳过，最后汇总成功和失败的场数。

3. **在 `baseline.py` 新增两个纯函数**，输入是 `MatchMoments` 列表，不依赖 demo，方便测试：

   - **`weapon_stats(mms, cfg) -> dict`**，按交火武器分组：
     - **交火武器**：交火中目标玩家第一枪的 `Shot.weapon`；没开枪时，用 `Context.own_weapon` 反查得到的规范名；查不到的跳过。
     - **样本范围**：击杀和死亡瞬间；排除带 `after_round_end`、`teamkill`、`victim_is_bot`、`world_damage` 标签的；排除没有 `engagement` 的。
     - 每把武器输出：
       - `engagements`：交火次数；
       - `time_to_damage_ms`：`{p50, p75, p90, n}`。只取 `time_to_damage_s` 有值、小于 `time_to_damage_max_seconds`、且 `visibility_note` 不是 `smoke` 的交火；
       - `crosshair_placement_deg`：`{p50, p75, p90, n}`；
       - `first_shot_stopped_pct`、`first_shot_hit_pct`：去掉 None 后的百分比，并给出各自的 n；
       - `distance_m`：`{p50, p75, p90, n}`，来自 `Context.distance`。
     - 交火次数少于 `min_engagements`（新配置键，默认 30）的武器不输出。

   - **`spray_stats(mms, cfg) -> dict`**：只统计配置中列出的自动武器（新配置键，默认 `["ak47", "m4a1", "m4a1_silencer"]`）。
     - 取上述交火中的每一发子弹，按 `recoil_index` 分段：`[0]`、`[1, 2]`、`[3, 5]`、`[6, 9]`、`[10, 14]`、`[15+]`。分段边界放进配置；
     - 每段输出：`{from, to, shots, hit_pct}`，`to` 为 None 表示不封顶；
     - `recoil_index` 为 None 的子弹跳过。

   - **新配置键**：放在 `config/default.toml` 新的 `[baseline]` 段下，带中文注释，配置校验要通过。

4. **`pro_sample.json` 的格式（v2）**：

   ```json
   {
     "version": 2,
     "source": "ESL Pro League S24：5 场 BO3、15 张图，150 人次",
     "demos": [{"event": "...", "teams": "...", "map": "...", "member": "..."}],
     "metrics": { ...与现在相同的整场枪法统计，compare() 照常使用... },
     "weapons": { "ak47": {...}, "awp": {...} },
     "spray": { "ak47": [{"from": 0, "to": 0, "shots": 980, "hit_pct": 36.0}, ...] }
   }
   ```

   `source` 中的人次按实际人数填写。

5. **在 `baseline.py` 新增读取接口**，给后续的问题目录用：
   - `weapon_threshold(baseline, weapon, metric, quantile) -> float | None`，例如 `weapon_threshold(b, "awp", "time_to_damage_ms", "p75")`；
   - `spray_segments(baseline, weapon) -> list[dict]`；
   - 查不到时返回 None 或空列表。

6. **重新生成 `src/cs2coach/baselines/pro_sample.json`**（提交进仓库的文件），并在完成报告中贴出生成时打印的汇总。

7. **文档**：更新 `docs/features.md` 中职业基准相关的说明（样本、按武器的口径，与 Leetify 不同的地方）。

## 验收标准

- [x] 测试（新建 `tests/test_baseline.py`，或加到现有测试文件中），用手工构造的 `MatchMoments` 和 `Moment`，不读 demo：
  - 交火武器的认定：用第一枪的武器；没开枪时用 `own_weapon` 反查；查不到时跳过；
  - TTD 的过滤：超过最大值、`smoke` 的不算；
  - 带排除标签的瞬间、没有 `engagement` 的瞬间不算；
  - 交火次数低于 `min_engagements` 的武器不输出；
  - 压枪分段：边界值归到正确的段，`recoil_index` 为 None 的子弹跳过，非自动武器不统计；
  - `weapon_threshold` 和 `spray_segments` 查不到时的返回值；
  - `compare()` 对 v2 文件照常工作。
- [x] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [x] 重新生成 `pro_sample.json`。完成报告中列出：
  - 成功的场数和人次；
  - 七把武器（ak47、m4a1、m4a1_silencer、awp、usp_silencer、glock、deagle）的交火次数、TTD 的 p50 和 p75、准星的 p75；
  - AK 的压枪分段。

  这些数字应该与 `docs/problem_catalog.md` 中的表格**大致吻合**。如果不一致，原因可能是 003 修复了 AWP 的开镜状态，或者口径有差异，在报告中说明，不要去改目录。
- [x] 用新基准跑一次 `analyze`（任选一场 5E demo，**不加 `--ai`**），确认摘要中的职业对比正常显示。

## 范围外（不要做）

- 不实现问题目录的检测（任务 005）。
- 不加路人分位（以后再做）。
- 不改指标的计算口径。发现数据异常时写进"遗留问题"。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
  - `baselines/pro_demos.toml` 登记 15 份 demo 的压缩包、成员、赛事、对阵与地图；Mirage p1/p2 分别处理。
  - `scripts/build_baseline.py` 默认读清单，逐图解析、按覆盖率筛选选手并共用地图视野；失败按图跳过、汇总原因，全部失败时保留原文件。成功结果原子写入 UTF-8 JSON。
  - `baseline.py` 新增共用的交火武器认定、逐武器分布、逐子弹连射分段和安全读取接口；保留原整场指标与 compare 接口。
  - `config/default.toml` 新增基准最小交火次数、自动武器和分段边界；`docs/features.md` 记录样本口径及与 Leetify 的差异。
  - `tests/helpers/moments.py` 提供手工瞬间；`tests/test_baseline.py` 覆盖过滤、缺失值、分位数、武器认定、连射边界、兼容接口和脚本失败处理。未新增依赖。
- 测试结果：`.venv/Scripts/python.exe -m pytest -q`：292 passed, 3 skipped。`git diff --check` 通过。
- 真实 demo 验证：运行 `scripts/build_baseline.py`，完整日志为 `data/output/pro_baseline_v2.log`。生成时汇总：

  ```text
  汇总：成功 15 场，失败 0 场，合格选手 150 人次
  跳过 @reLazffs：覆盖率 0.0%
  time_to_damage_ms: 中位数 375.0（328.0–406.0，n=146）
  crosshair_placement_deg: 中位数 3.83（2.86–5.04，n=146）
  counter_strafing_pct: 中位数 82.9（76.5–88.1，n=147）
  spray_accuracy_pct: 中位数 36.2（30.65–44.4，n=149）
  spotted_accuracy_pct: 中位数 35.55（31.08–40.2，n=150）
  headshot_accuracy_pct: 中位数 22.9（17.02–29.2，n=148）
  ```

  | 武器 | 交火次数 | TTD p50 ms | TTD p75 ms | 准星 p75 ° |
  |---|---:|---:|---:|---:|
  | ak47 | 1403 | 375 | 512 | 9.38 |
  | m4a1 | 450 | 375 | 484 | 10.38 |
  | m4a1_silencer | 474 | 359 | 484 | 6.91 |
  | awp | 359 | 312 | 391 | 3.08 |
  | usp_silencer | 192 | 375 | 547 | 8.59 |
  | glock | 237 | 359 | 531 | 11.39 |
  | deagle | 196 | 344 | 523.75 | 7.29 |

  | AK recoil_index 段 | 子弹数 | 命中率 % |
  |---|---:|---:|
  | 0 | 1102 | 36.12 |
  | 1–2 | 1579 | 46.04 |
  | 3–5 | 1118 | 49.28 |
  | 6–9 | 366 | 40.16 |
  | 10–14 | 94 | 30.85 |
  | 15+ | 15 | 46.67 |

  - 七把武器的 TTD 与目录大体吻合（例如 AWP 312/391 完全一致、AK p75 为 512 对目录 516）。USP 准星 p75 为 8.59 对目录 9.5，Deagle TTD p75 为 523.75 对目录 558。连射早中段接近目录；末段样本小，不能当稳定标准。
  - 当前交火次数含所有符合任务单的击杀/死亡，包括未开枪时反查手持武器；与目录部分 n 不一致（如 AWP 359 对目录 259）。目录未提供原始统计脚本，无法确证其样本过滤细节，未修改目录数字或指标口径，交由 Claude 审查。
  - 用新基准执行 5E Mirage analyze（玩家 76561190000001002，不加 --ai），成功输出 37 个瞬间与事实卡；再将生成的 MatchMoments 交给摘要的 `_aim_section`，六项职业对比均正常显示。TTD 500ms 对职业中位 375ms，优于 3%；准星 3.33° 对职业中位 3.83°，优于 58%。CLI 无 AI 模式本身只打印枪法原值，故单独验证摘要函数，未改变 CLI 展示范围。
- 遗留问题 / 需要确认的地方：
  - 目录 n 与当前完整样本数的差异见上；不对无法溯源的旧数字作强行解释。
  - 15 份 demo 是 5 组对局的可用文件，包含 BO5 的部分地图和服务器重启分段，因此 source 使用实际描述，避免称为“5 场 BO3、15 张不同地图”。
  - README、AI 提示词中的旧小样本说明属于范围外，未改；实际摘要来源读取新基准。
  - 按用户确认顺序 006 → 004 → 005 执行；未提交，未调用真实 AI。

### 第 1 轮修改（2026-10-15）

1. 已更新职业样本说明：
   - `src/cs2coach/ai/prompts.py` 规则 5 改为从摘要获取来源和规模，说明样本仅来自一个赛事的少数队伍，对比只能说明大致差距；不再写死比赛场数或人次。
   - `src/cs2coach/tools/digest.py` 仍使用 `baseline["source"]`，参考说明改为“样本只来自少数职业队伍，仅作参考”。
   - `README.md` 改为说明默认读取 `pro_demos.toml`、输出基准文件，并补充添加压缩包、登记清单字段、重新生成与核对汇总的步骤；规模以摘要中的 source 为准。
   - 检索相关测试，未发现断言旧说明文字的用例，因此无需修改测试。以上三处旧文字已移除。
2. 已在 `src/cs2coach/tools/queries.py` 的 get_moment 字段说明补充：visibility_note 为 smoke 或 blind 时，双方首次可见 tick 只作核对，不能据此判断谁先看见。

- 验证：`.venv/Scripts/python.exe -m pytest -q`：**362 passed, 3 skipped**；`git diff --check` 通过。复用已有 5E Mirage 玩家 76561190000001002 的真实解析 JSON，调用摘要函数核对，显示当前基准 source 和新的参考说明；未重新解析 demo，未重新生成基准。
- 本轮仅修改上述四个文件及本完成报告，未改变统计口径、查询返回值或 AI 调用流程；未新增依赖，未调用真实 AI，未提交。
- 遗留问题：本轮两条审查意见均已处理。初次报告中的样本数差异已由 Claude 审查确认为可行性验证未计入未开枪交火，本任务口径正确；旧样本说明也已在本轮更新。

## 审查意见（Claude 填写）

**第 1 轮（2026-10-15）：需修改。** 统计和接口都正确，与可行性验证的数字吻合。交火次数的差异（AWP 359 对 259）是因为可行性验证时没有计入未开枪的交火，以本任务的口径为准，目录已注明。

需要修改两处说明文字，避免 AI 说错：

1. **职业样本的说明已经过时**。基准扩大后，以下几处会让 AI 和用户以为样本还是一场比赛：
   - `src/cs2coach/ai/prompts.py` 规则 5（"职业样本只有一场比赛的 10 名职业选手"）；
   - `src/cs2coach/tools/digest.py` 第 76 行（"职业样本很小"）；
   - `README.md` 第 93 行。

   改法：**不要把场次和人次写死在提示词和代码里**，样本大小以摘要中显示的 `baseline["source"]` 为准。
   - 提示词规则 5 改为：职业样本的来源和规模见摘要；样本只来自一个赛事的少数几支队伍，对比只说明大致差距，不要说成"职业标准"。
   - 摘要改为：`职业样本：{source}。样本只来自少数职业队伍，仅作参考。`
   - README 说明新的生成方式：清单文件 `pro_demos.toml`，以及加新 demo 的步骤。
   - 相关测试中如果断言了旧文字，要同步修改。

2. **（来自 006）** `src/cs2coach/tools/queries.py` 中 `get_moment` 的字段说明，在 `player_first_seen_tick / opponent_first_seen_tick` 处补充：`visibility_note` 为 `smoke` 或 `blind` 时，这两个 tick 只作核对，不能据此判断谁先看见。

改完后跑全部测试，在完成报告中追加"第 1 轮修改"。

**第 2 轮（2026-10-15）：通过。** 已核对提示词规则 5、摘要、README 和 `get_moment` 字段说明，362 个测试通过。
