"""将确定性分析结果写成机器可读 JSON 和中文 Markdown 报告。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _file_stem(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return clean or "demo"


def render_markdown(report: dict[str, Any]) -> str:
    player = report["target_player"]
    metrics = report["metrics"]
    timebase = report["timebase"]
    demo = report["demo"]
    lines = [
        f"# CS2 复盘：{player['name']}",
        "",
        f"- Demo：`{demo['file']}`",
        f"- SteamID64：`{player['steamid']}`",
        f"- 地图：`{demo.get('header', {}).get('map_name', '未知')}`",
        f"- Tick rate：{timebase['tick_rate']}（{timebase['source']}）",
        f"- 回合：{len(report['rounds'])}",
        "",
        "> 本报告由确定性规则生成，不包含 AI 主观诊断。候选项需结合回放和战术情境复核。",
        "",
        "## 比赛汇总",
        "",
        f"- 击杀 / 阵亡 / 助攻：{metrics['kills']} / {metrics['deaths']} / {metrics['assists']}；队友误伤击杀 {metrics['teamkills']}；阵营无法确认的击杀事件 {metrics['unclassified_attacker_events']}",
        f"- 造成伤害 / 承受伤害：{metrics['damage_dealt']} / {metrics['damage_received']}",
        f"- 道具伤害：{metrics['utility_damage_dealt']}",
        f"- 枪械开火事件：{metrics['weapon_fire_events']}",
        f"- 补枪：队友阵亡后 {metrics['team_kills_traded']}/{metrics['team_kill_opportunities']}；本人阵亡后队友补回 {metrics['target_deaths_traded']}/{metrics['target_death_opportunities']}",
        f"- 闪光：目标被闪 {report['flash_summary']['target_blinded_events']} 次（达到时长门槛 {report['flash_summary']['target_blinded_events_ge_threshold']} 次）；目标致队友 {report['flash_summary']['teammates_blinded_ge_threshold']} 次，致敌人 {report['flash_summary']['enemies_blinded_ge_threshold']} 次",
        "",
        "## 购买窗口经济快照",
        "",
    ]
    if report["economy_snapshots"]:
        lines.extend(["| 回合 | tick | 余额 | 本回合花费 | 装备价值 | 与队伍装备中位数差 |", "|---:|---:|---:|---:|---:|---:|"])
        for row in report["economy_snapshots"]:
            lines.append(f"| {row.get('round') or '—'} | {row['tick']} | {row.get('balance')} | {row.get('cash_spent_this_round')} | {row.get('current_equip_value')} | {row.get('difference_from_team_median')} |")
    else:
        lines.append("无可用购买窗口快照（需有效的 `mp_buytime` 和相关 tick 字段）。")
    lines.extend([
        "",
        "## 复核建议",
        "",
    ])
    if report["findings"]:
        for finding in report["findings"]:
            refs = ", ".join(f"tick {tick}" for tick in finding["evidence_ticks"][:8]) or "无可用 tick"
            lines.append(f"- **{finding['text']}** 证据：{refs}。方法：{finding['method']}")
    else:
        lines.append("当前样本未达到配置中的重复模式门槛，或所需字段缺失；不据此判断玩家表现正常。")
    lines.extend(["", "## 决定性瞬间", ""])
    if report["moments"]:
        lines.extend(["| 回合 | 时间 | 类型 | 事件 | 证据跳转 |", "|---:|---:|---|---|---|"])
        for moment in report["moments"]:
            facts = moment["event"]
            if moment["kind"] == "kill":
                detail = f"击杀 `{facts.get('victim_name') or facts.get('victim_steamid')}`，武器 `{facts.get('weapon')}`"
            elif moment["kind"] == "teamkill":
                detail = f"队友误伤击杀 `{facts.get('victim_name') or facts.get('victim_steamid')}`，武器 `{facts.get('weapon')}`"
            elif moment["kind"] == "unclassified_attacker_event":
                detail = f"攻击者事件（阵营无法确认）`{facts.get('victim_name') or facts.get('victim_steamid')}`，武器 `{facts.get('weapon')}`"
            elif moment["kind"] == "death":
                detail = f"被 `{facts.get('attacker_name') or facts.get('attacker_steamid')}` 击杀，武器 `{facts.get('weapon')}`；死前装备价值 `{facts.get('equipment_value')}`"
            else:
                detail = f"助攻，武器 `{facts.get('weapon')}`"
            evidence = moment["evidence"]
            jump = f"`{evidence['jump_command']}`（目标 tick {evidence['jump_tick']}）"
            lines.append(f"| {moment.get('round') or '—'} | {moment['time_seconds']:.2f}s / {moment['tick']} | {moment['kind']} | {detail} | {jump} |")
    else:
        lines.append("没有匹配到目标玩家的击杀、阵亡或助攻事件。")
    lines.extend(["", "## 数据完整性与限制", ""])
    quality = report["data_quality"]
    lines.append(f"- 解析到 {quality['queried_event_counts'].get('player_death', 0)} 条死亡事件；回合结束状态缺失 {quality['rounds_without_end_state']} 个。")
    if quality["missing_tick_properties"]:
        lines.append(f"- 未返回字段：{', '.join(quality['missing_tick_properties'])}。")
    for limitation in quality["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(["- `demo_gototick` 命令按当前 CS2 控制台命令格式生成；使用前请在目标游戏版本中实测。", ""])
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"{_file_stem(Path(report['demo']['file']).stem)}_{report['target_player']['steamid']}"
    json_path = output_dir / f"{base}.json"
    markdown_path = output_dir / f"{base}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
