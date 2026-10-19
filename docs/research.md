# R01：Mirage CT 首次 5v4 行为表

## 触发器与分析单位

每回合冻结期结束时建立双方实际存活名单，按死亡事件推进。回合判定前首次敌对击杀必须击杀 T，且击杀前恰好 5 CT 对 5 T，才记录一个 `ct_first_5v4`。冻结期死亡、掉线、先失去 CT 或先失去 T 都不会被当作这个触发器。每名触发时存活的 CT 是一行；同场拆分 demo 用同一 `match_id`，各行仍保留 `demo_id`。

## 五秒窗口与特征

窗口含触发 tick 和结束 tick，通常为触发后五秒，若玩家阵亡或回合先结束则截断。位置和地点取两端逐 tick 状态；`displacement` 是两端三维直线距离。`yaw_change` 累计相邻 tick 最短角度差的绝对值。`utility_types` 使用 `weapon_fire` 中的投掷物类型，表示投出，不以爆炸事件代替。枪数为枪械 `weapon_fire` 次数。伤害为解析层的 `dmg_effective`，击杀、死亡由事件表确定。最近队友距离为当时存活 CT 的三维直线距离，不能等同于可补枪距离。

导航目标是冻结期结束时五名 T 位置中心所在网格区域。`nav_start_distance` 与 `nav_end_distance` 沿 awpy `find_path` 返回的区域链，按起点、区域中心点、终点折线累计；这是导航路径的近似长度，不是玩家真实走过的轨迹。`nav_delta = nav_end_distance - nav_start_distance`，负数表示更靠近 T 出生区。任一位置或导航区域缺失、没有路径时，导航字段为空，主标签为 `unknown`。区域标签由 `[research.regions.de_mirage]` 的地点名表提供；空字符串暂记为未知，第一版不参与主标签。

`visible_enemies_trigger` 是触发 tick 本人在几何视野、视角内，且未被烟雾和闪白遮挡的 T 人数。`visible_enemies_window` 是窗口内曾被本人如此看到的不同 T 人数。`radar_enemies_window` 是窗口内曾被任一 CT 的 `spotted_by_mask` 标记的不同 T 人数；它只是团队雷达发现的近似值，不代表目标玩家确实留意到雷达。雷达位掩码随解析层的采样 tick 更新，可能晚于真实发现时刻。无法加载视野数据时几何可见人数为空，不用敌人真实坐标推测玩家知识。

`advantage_held` 比较窗口结束 tick 的 CT 与 T 存活人数；窗口因该玩家阵亡而截断时，比较阵亡 tick。`ct_won` 来自回合胜负。`time_remaining_s` 暂为空：现有解析层只有已过回合时间，不能可靠推算回合剩余时长。`jump_command` 跳至触发前两秒，最小为 tick 0。

`first_killer_id`（情境）是拿下触发击杀的 CT；`first_kill_by`（窗口）为 `self`（这名玩家自己拿的第一个人头）或 `teammate`（队友拿的），击杀者缺失时为空。两者的处境不同：自己拿人头的人刚交过火、位置可能已暴露，常需要换位；队友拿人头时他只是得知人数优势。研究时按它分层（产品负责人 2026-10-19 提出）。`killed_enemy` 统计窗口内是否有击杀，**包含触发击杀本身**，不能代替这一列。

## 初版标签

全部阈值在 `config/default.toml`，单位为游戏单位，是待人工回放校准的工程假设。按下列顺序判定：

1. 玩家在窗口一秒内阵亡：`unknown`。
2. 地点分组（`[research.zones.<地图>]`，R02 第 1 轮后加入）：起点和终点报点在同一防守区组内为 `hold`（主防在自己区域内换角度、补位仍算保持）；从某防守区组或纵深（`deep`）进入另一个防守区组为 `rotate`（转点）。按高度拆分的报点见 `[research.floors.<地图>]`（如 Palace 楼下 / 楼上）。不满足时继续往下判。
3. 导航变化或位移缺失：`unknown`。
4. 直线位移 < 150 且导航变化绝对值 < 100：`hold`。
5. 导航变化 ≤ −250：`advance`；≥ 250：`retreat`。
6. 直线位移 ≥ 300 且导航变化绝对值 < 250：`lateral`。
7. 其余：`unknown`。

只改标签规则或阈值时，用 `python scripts/research_export.py --relabel <来源导出名> --name <新名>` 重打标签，不需要重新解析 demo。

辅助标签可叠加：投出投掷物为 `utility`；到最近队友距离缩短 > 300 且结束距离 < 600 为 `regroup`；开枪或受到伤害为 `engaged`。`label_confidence` 是规则离边界的归一化余量，截断在 0–1：`hold` 取两个上限剩余比例的较小值，`advance` / `retreat` 取超出 250 的比例，`lateral` 取位移超出 300 的比例与方向离 250 上限的剩余比例之较小值。按地点分组判出的 `hold` / `rotate` 记 1，`unknown` 为 0。该数只是规则边界距离，不是统计概率。

## 研究表

`situations.csv` 每个触发回合一行，存 demo、比赛、回合、地图、触发 tick、触发后人数、已过时间、下包状态。`windows.csv` 每名 CT 一行，复制局势字段，增加 SteamID 字符串、选手名、tick rate、tick 区间、上述行为特征、初版标签、短期与回合结果、跳转命令、`review_status`。列表与坐标在 CSV 中编码为 JSON 字符串，缺数据留空。`summary.md` 列样本数量、主标签分布与跳过原因。结果字段仅用于后续研究，不参与行为标签判定。
