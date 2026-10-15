"""多场程序汇总的 Markdown 表格。"""

from __future__ import annotations

from cs2coach.report.problems import frequency_cells
from cs2coach.summary.aggregate import PlayerSummary
from cs2coach.tools.digest import METRIC_LABELS


def summary_frequency_table(summary: PlayerSummary, rows=None) -> list[str]:
    lines = ["| 问题 | 总次数 | 职业预期 | 多出的次数 | 偶然概率 | 倍数 | 出现场数 / 高于职业的场数 | 主要地图 |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in summary.frequency if rows is None else rows:
        maps = "、".join(f"{name} {count} 次" for name, count in
                        sorted(row.by_map.items(), key=lambda pair: (-pair[1], pair[0]))) or "—"
        cells = frequency_cells(row) + [f"{row.matches_with_hits} / {row.matches_above_pro}", maps]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def summary_match_table(summary: PlayerSummary) -> list[str]:
    lines = ["| 编号 | 比赛 | 地图 | 回合 / 在场 | 比分 | 击杀 | 死亡 | 关键助攻 | 突出问题 |",
             "|---|---|---|---:|---:|---:|---:|---:|---|"]
    for match in summary.matches:
        problems = "、".join(match.prominent_problems) or "—"
        lines.append(f"| {match.number} | {match.match_id} | {match.map_name} | "
                     f"{match.rounds} / {match.rounds_played} | {match.score[0]}:{match.score[1]} | "
                     f"{match.kills} | {match.deaths} | {match.assists} | {problems} |")
    return lines


def render_summary_problems(summary: PlayerSummary) -> str:
    """频率表、每场概况及跳过原因；不生成 AI 诊断。"""
    lines = [f"# 多场问题汇总：{', '.join(summary.names) or summary.steamid}", "",
             f"共 {len(summary.matches)} 场，在场 {summary.rounds_played_total} 回合。"
             "频率按在场回合计算；加粗表示多出次数及偶然概率均达到突出阈值。", "",
             "## 汇总频率", "", *summary_frequency_table(summary),
             "", "## 每场概况", "", *summary_match_table(summary)]
    lines.extend(["", "## 整场枪法各场中位数", "",
                  "各指标取有数值比赛的中位数，不合并各场子弹重新计算。", "",
                  "| 指标 | 中位数 | 参与场数 |", "|---|---:|---:|"])
    for field, item in summary.aim_overall.items():
        label, unit, _, _ = METRIC_LABELS[field]
        value = f"{item.median:.1f}{unit}" if item.median is not None else "—"
        lines.append(f"| {label} | {value} | {item.matches} |")
    lines.extend(["", "## 跳过的比赛", ""])
    lines.extend(f"- {item.match_id}（{item.path}） ：{item.reason}" for item in summary.skipped)
    if not summary.skipped:
        lines.append("无。")
    return "\n".join(lines) + "\n"
