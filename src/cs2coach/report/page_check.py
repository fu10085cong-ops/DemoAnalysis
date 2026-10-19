"""检查每个分段内的统计说明和区块是否一致，不中断报告输出。"""

from cs2coach.features.radar import minimum_samples
from cs2coach.report.page_data import ReportPage


def check_page(page: ReportPage, cfg: dict) -> list[str]:
    """返回可按规则汇总的违规说明；只使用页面保留的统计依据。"""
    issues = []
    radar = cfg["radar"]

    def add(rule, tier, detail):
        issues.append(f"{rule}：{tier}：{detail}")

    if len(page.highlights) > radar["max_highlights"]:
        add("数量超限", "页面", "高光")
    for tier in page.tiers:
        flagged = {row.axis for row in tier.problems if row.severity in ("bad", "warn") and row.axis}
        for row in tier.strengths:
            if row.axis and row.axis in flagged:
                add("维度冲突", tier.id, row.axis)
            if page.kind == "summary" and page.ladder_levels:
                if (row.ladder is None or row.ladder.reached is None or tier.level is None or
                        row.ladder.reached.level < tier.level + 1):
                    add("优点阶梯不足", tier.id, row.id)
            elif row.percentile is None or row.percentile < radar["strength_min_percentile"]:
                add("优点百分位不足", tier.id, row.id)
            if row.samples < minimum_samples(row.id, cfg):
                add("优点样本不足", tier.id, row.id)
        bad = [row for row in tier.problems if row.severity == "bad"]
        if bad:
            top = max(bad, key=lambda row: row.count - row.expected if row.expected is not None else float("-inf"))
            if not tier.verdict.startswith(f"最该练的是{top.name}："):
                add("结论选题不符", tier.id, top.id)
        elif "最该练" in tier.verdict:
            add("结论选题不符", tier.id, "没有 bad 问题")
        for rows, key in ((tier.strengths, "max_strengths"), (tier.problems, "max_problems")):
            if len(rows) > radar[key]:
                add("数量超限", tier.id, key)
            for row in rows:
                if not row.measure.strip() or not row.unit.strip():
                    add("口径或单位为空", tier.id, row.id)
                if len(row.examples) > radar["max_examples"]:
                    add("数量超限", tier.id, f"{row.id} 例子")
                if row.id == "missed_trade":
                    for example in row.examples:
                        if example.tick not in row.evidence_ticks.get(example.match_label, []):
                            add("漏补例子无命中依据", tier.id, f"{example.match_label} R{example.round} tick={example.tick}")
    return issues
