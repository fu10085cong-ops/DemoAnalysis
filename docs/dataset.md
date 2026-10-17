# 交火数据集（S07）

供数据研究用：50 份 demo（26 份职业、24 场业余）的全部瞬间、漏补记录和玩家，按代码版本缓存。

- **导出**：`python scripts/dataset_export.py [--jobs 3]`。输出到 `data/dataset/<code_hash 前 12 位>/`，每份 demo 一个 parquet 文件；代码变了会自动换目录。全部导出约 18 分钟。
- **读取**：`from cs2coach import dataset; dataset.load(kind="moments" | "missed_trades" | "players", group=None | "pro" | "amateur")`
- **不含真实玩家名**：玩家只存 `steamid` 和 `name_hash`。

## 容易弄错的列名

| 列 | 含义 |
|---|---|
| `kind` | **行的类型**：`moments`、`missed_trades`、`players` |
| `moment_kind` | **瞬间本身的类型**：`kill`、`death`、`assist` |
| `id` | 瞬间编号，如 `R5-death`；同一场同一名玩家内唯一 |
| `hits` / `disabled_hits` | 该瞬间命中的问题编号列表（JSON 字符串），前者是启用的规则，后者是停用的规则 |
| `group` | `pro` 或 `amateur` |
| `rounds_played` | 只在 `players` 行有值，算"每 10 回合"时用它作分母 |

## 列名前缀

| 前缀 | 来源 | 例子 |
|---|---|---|
| `eng_` | `Engagement`（交火） | `eng_shots_fired`、`eng_seen_first_lead_s`、`eng_crosshair_initial_offset_m` |
| `ctx_` | `Context`（局势） | `ctx_distance`、`ctx_own_weapon`、`ctx_alive_enemy` |
| `death_` | `DeathState`（只有死亡有） | `death_blinded`、`death_shots_last_2s`、`death_reloading_at_death` |
| `trade_` | `Trade` | `trade_traded` |
| `aim_` | 整场枪法统计，只在 `players` 行 | `aim_time_to_damage_ms` |
| 无前缀 | `Moment` 本身，或 `MissedTrade` 的字段 | `weapon`（死亡瞬间是击杀者的武器）、`through_smoke`、`killer_in_view_s` |

## 常用的排除条件

研究"失误"时，通常先排除这些没法从枪法上解释的阵亡（2026-10-17 的统计都用了这组条件）：

```python
d = m[m["moment_kind"] == "death"]
d = d[~d["through_smoke"].astype(bool) & ~d["wallbang"].astype(bool)
      & ~d["weapon"].isin(["hegrenade", "inferno", "molotov", "incgrenade"])
      & ~d["death_blinded"].fillna(False).astype(bool)]
```

不加这组条件时，结果会明显不同。例如"没朝击杀者开枪的阵亡"：加条件后职业 2.17、业余 2.37（每 10 回合），不加时是 2.92、3.00。
