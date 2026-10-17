"""将程序命中的证据写成中文句子，供摘要和最终报告共用。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from cs2coach.features.labels import GRENADE_LABELS, weapon_label
from cs2coach.features.problems import ProblemHit


def _number(value) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if isinstance(value, (int, float)) else str(value)


def _grenades(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return "、".join(GRENADE_LABELS.get(v, v) for v in value) if isinstance(value, list) else str(value)


def missed_trade_context(data: Mapping[str, Any]) -> str:
    """只拼接已有的漏补局势字段，旧数据缺字段时保持可读。"""
    parts = []
    if data.get("round_time_s") is not None:
        parts.append(f"第 {data['round_time_s']:.0f} 秒")
    if data.get("side") is not None:
        parts.append(f"{data['side']} 方")
    if data.get("bomb_planted") is not None:
        parts.append("已下包" if data["bomb_planted"] else "下包前")
    if data.get("alive_own") is not None and data.get("alive_enemy") is not None:
        parts.append(f"队友阵亡后 {data['alive_own']}v{data['alive_enemy']}")
    return "，".join(parts) + "。" if parts else ""


def evidence_sentence(hit: ProblemHit) -> str:
    """只复述命中记录里的数值、布尔值和阈值，不补充新的判断。"""
    e, t, pid = hit.evidence, hit.thresholds, hit.problem
    if pid == "slow_reaction":
        if "seen_to_first_shot_ms" in e:  # 032 起比较"看见 → 开枪"
            text = (f"看见对手到开枪 {_number(e.get('seen_to_first_shot_ms'))} 毫秒，高于职业同武器 75 分位 "
                    f"{_number(t.get('seen_to_first_shot_ms'))} 毫秒（TTD {_number(e.get('ttd_ms'))} 毫秒）")
        else:
            text = f"TTD {_number(e.get('ttd_ms'))} 毫秒，高于职业同武器 75 分位 {_number(t.get('ttd_ms'))} 毫秒"
        if e.get("kind") == "death":
            text += f"；对手 TTD 为 {_number(e.get('opponent_ttd_ms'))} 毫秒，比自己短"
        return text + "。"
    if pid == "poor_crosshair_placement":
        if "crosshair_initial_offset_m" in e:  # 031 起比较首次看见时准星离对手头部的距离
            return (f"刚看见对手时准星离他头部 {e['crosshair_initial_offset_m']:.1f} 米"
                    f"（{_number(e.get('crosshair_initial_deg'))}°），高于职业同武器 75 分位 "
                    f"{t.get('crosshair_initial_offset_m', 0):.1f} 米乘以 {1 + t.get('margin_ratio', 0):.2f}。")
        return (f"准星从看见到首次命中转过 {_number(e.get('crosshair_placement_deg'))}°，"
                f"高于职业同武器 75 分位 {_number(t.get('crosshair_placement_deg'))}° "
                f"乘以 {1 + t.get('margin_ratio', 0):.2f}。")
    if pid == "shot_while_moving":
        if e.get("first_seen_shot_speed_ratio") is None or t.get("min_speed_ratio") is None:
            return "交火第一枪未停稳，且没有命中。"
        return (f"看见对手后的第一枪速度比 {e['first_seen_shot_speed_ratio']:.3f}，"
                f"不低于 {t.get('weapon_name', weapon_label(hit.weapon))} 的阈值 {t['min_speed_ratio']:.3f}；这一枪未命中。")
    if pid == "peeked_into_held_angle":
        return "接触类型检测为主动拉出、对手架点（近似判断）。"
    if pid == "dry_peek_with_utility":
        return f"接触类型为主动拉出（近似）；剩余道具：{_grenades(e.get('unused_grenades'))}；对手没有交火致盲记录。"
    if pid == "overpeek_in_advantage":
        return "有人数优势标签，接触类型为主动拉出（近似），且死亡后未被补回。"
    if pid == "died_without_seeing":
        return "有未看到对手标签，且不是穿墙、穿烟、道具击杀，死亡时没有被闪。"
    if pid == "turned_into_flash":
        return "记录显示死亡时仍处于致盲中，投掷来源满足对手闪光的检测条件。"
    if pid == "missed_trade":
        context = missed_trade_context(e)
        return ("漏补枪记录：" + context
                + f"离阵亡队友 {_number(e.get('distance'))} 单位；"
                + ("其他队友随后补上。" if e.get("killer_traded_by_other") else "窗口内未由其他队友补上。")
                + (f"击杀者在阵亡 {e['killer_first_in_view_s']:.1f} 秒后出现在你的屏幕里，累计 {e['killer_in_view_s']:.1f} 秒。"
                   if e.get("killer_in_view_s") and e.get("killer_first_in_view_s") is not None else ""))
    if pid == "post_plant_utility_unused":
        since = f"下包后 {_number(e['seconds_since_plant'])} 秒阵亡，" if e.get("seconds_since_plant") is not None else ""
        return f"T 方已下包，{since}死亡时还携带：{_grenades(e.get('unused_grenades'))}。"
    if pid == "died_while_reloading":
        return (f"阵亡时正在换弹，换弹前弹匣还有 {_number(e.get('ammo_before_reload'))} / "
                f"{_number(e.get('magazine_size'))} 发，开始换弹 {_number(e.get('reload_started_s'))} 秒后阵亡。")
    if pid == "saw_but_no_shot":
        return f"看见对手 {_number(e.get('seen_to_death_s'))} 秒后阵亡，期间一枪没开。"
    if pid == "weapon_not_ready":
        return "有阵亡时武器还不能开枪的检测标签。"
    if pid == "awp_unscoped_shot":
        return f"AWP 第一枪未开镜，距离 {_number(e.get('distance_m'))} 米，大于配置阈值 {_number(t.get('distance_m'))} 米。"
    if pid == "deagle_moving_shot":
        return (f"看见对手后的第一枪速度比 {e['first_seen_shot_speed_ratio']:.3f}，"
                f"不低于 {t.get('weapon_name', weapon_label(hit.weapon))} 的阈值 {t['min_speed_ratio']:.3f}。")
    if pid == "glock_long_range":
        return f"Glock 交火距离 {_number(e.get('distance_m'))} 米，高于职业距离 90 分位 {_number(t.get('distance_m'))} 米。"
    if pid == "spray_control":
        scope = "整场该发数段" if hit.target == "match" else "本次交火后段"
        return (f"{scope} {_number(e.get('shots'))} 发，命中率 {_number(e.get('hit_pct'))}%；"
                f"低于职业期望 {_number(t.get('expected_hit_pct'))}% 减去 {_number(t.get('margin_pct'))} 个百分点。")
    if pid == "spray_while_moving":
        return (f"连射中的 {_number(e.get('shots'))} 发，未停稳比例 {_number(100 * e['moving_ratio'])}%，"
                f"超过配置阈值 {_number(100 * t['moving_ratio'])}%。")
    if pid == "good_trade":
        return "有及时补枪的检测标签。"
    if pid == "flash_assist":
        return "该助攻记录为闪光助攻。"
    if pid == "won_duel_seen_first":
        return (f"击杀时先看见 {_number(e.get('seen_first_lead_s'))} 秒、先开枪 "
                f"{_number(e.get('first_shot_lead_s'))} 秒，均不低于 {_number(t.get('min_lead_s'))} 秒。")
    return f"证据：{json.dumps(e, ensure_ascii=False)}；阈值：{json.dumps(t, ensure_ascii=False)}。"


def match_fact(hit: ProblemHit) -> str:
    e = hit.evidence
    start, end = e.get("from"), e.get("to")
    segment = (f"第 {start + 1} 发起" if end is None else f"第 {start + 1}–{end + 1} 发") if start is not None else "该发数段"
    return (f"本场 {weapon_label(hit.weapon) if hit.weapon else '自动武器'} 的{segment}共 {_number(e.get('shots'))} 发，"
            f"命中率 {_number(e.get('hit_pct'))}%。")
