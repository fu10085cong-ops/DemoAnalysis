"""训练练习图白名单及摘要说明。"""

from __future__ import annotations


AIM_PROBLEMS = frozenset({"slow_reaction", "poor_crosshair_placement",
                          "shot_while_moving", "spray_control"})


def drill_labels(cfg: dict) -> dict[str, str]:
    return {item["id"]: item["label"] for item in cfg["training"]["drills"]}


def drill_guidance(cfg: dict) -> list[str]:
    """在摘要中列出白名单和各问题的适用方式。"""
    from cs2coach.features.problems import PROBLEMS

    lines = ["", "## 训练练习图白名单", "训练建议只能使用以下练习图；其他问题只描述练习方法，不指定地图名。"]
    for item in cfg["training"]["drills"]:
        problems = "、".join(f"{PROBLEMS[pid].label}（{pid}）" for pid in item["problems"])
        condition = (f"；报告中至少有 {item['min_aim_problems']} 类不同枪法问题"
                     if item.get("min_aim_problems") else "")
        lines.append(f"- {item['label']}（{item['id']}）：{problems}{condition}。")
    return lines
