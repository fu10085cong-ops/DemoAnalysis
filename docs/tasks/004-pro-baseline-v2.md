# 004 职业基准 v2：15 张图，加入按武器的统计

状态：待做（**在 006 之后做**）

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

- [ ] 测试（新建 `tests/test_baseline.py`，或加到现有测试文件中），用手工构造的 `MatchMoments` 和 `Moment`，不读 demo：
  - 交火武器的认定：用第一枪的武器；没开枪时用 `own_weapon` 反查；查不到时跳过；
  - TTD 的过滤：超过最大值、`smoke` 的不算；
  - 带排除标签的瞬间、没有 `engagement` 的瞬间不算；
  - 交火次数低于 `min_engagements` 的武器不输出；
  - 压枪分段：边界值归到正确的段，`recoil_index` 为 None 的子弹跳过，非自动武器不统计；
  - `weapon_threshold` 和 `spray_segments` 查不到时的返回值；
  - `compare()` 对 v2 文件照常工作。
- [ ] 全部测试通过：`.venv/Scripts/python.exe -m pytest -q`
- [ ] 重新生成 `pro_sample.json`。完成报告中列出：
  - 成功的场数和人次；
  - 七把武器（ak47、m4a1、m4a1_silencer、awp、usp_silencer、glock、deagle）的交火次数、TTD 的 p50 和 p75、准星的 p75；
  - AK 的压枪分段。

  这些数字应该与 `docs/problem_catalog.md` 中的表格**大致吻合**。如果不一致，原因可能是 003 修复了 AWP 的开镜状态，或者口径有差异，在报告中说明，不要去改目录。
- [ ] 用新基准跑一次 `analyze`（任选一场 5E demo，**不加 `--ai`**），确认摘要中的职业对比正常显示。

## 范围外（不要做）

- 不实现问题目录的检测（任务 005）。
- 不加路人分位（以后再做）。
- 不改指标的计算口径。发现数据异常时写进"遗留问题"。

---

## 完成报告（GPT 填写）

- 改动的文件及原因：
- 测试结果：
- 真实 demo 验证（如有）：
- 遗留问题 / 需要确认的地方：

## 审查意见（Claude 填写）
