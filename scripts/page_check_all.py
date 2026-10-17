"""逐场逐人检查临时业余分段的报告页，不调用 AI，不输出玩家身份。"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cs2coach.baseline import load_baseline
from cs2coach.config import load_config
from cs2coach.console import ensure_utf8_stdio
from cs2coach.features.aim import shot_targets
from cs2coach.features.lobby import _lobby_inputs, lobby_line
from cs2coach.features.moments import find_moments
from cs2coach.features.problems import detect_problems, problem_frequency
from cs2coach.features.visibility import Sight
from cs2coach.parsing.loader import resolve_demo
from cs2coach.parsing.match import parse_match
from cs2coach.report.page_check import check_page
from cs2coach.report.page_data import build_match_page, load_tiers
from scripts.build_tier_baseline import TIER_CONFIG, demo_entries


def main() -> int:
    ensure_utf8_stdio()
    cfg, pro = load_config(), load_baseline()
    tiers = load_tiers(cfg)
    if pro is None or "amateur_tmp" not in tiers:
        print("缺少职业基准或临时业余分段基准，无法检查")
        return 1
    tier = next(t for t in tomllib.loads(TIER_CONFIG.read_text(encoding="utf-8"))["tier"]
                if t["id"] == "amateur_tmp")
    try:
        entries = demo_entries(tier)
    except OSError as exc:
        print(f"无法读取分段 demo 目录：{type(exc).__name__}")
        return 1
    rules = ("维度冲突", "结论选题不符", "口径或单位为空", "数量超限", "优点百分位不足", "优点样本不足", "漏补例子无命中依据")
    counts, examples = Counter(dict.fromkeys(rules, 0)), defaultdict(list)
    players = checked = failed = 0
    for index, (path, member) in enumerate(entries, 1):
        print(f"比赛{index:02d} [{index}/{len(entries)}] 解析中", flush=True)
        try:
            match = parse_match(resolve_demo(path, member=member), cfg)
            sight = Sight(match, cfg)
            if not sight.available:
                raise ValueError("视野不可用")
            shots = match.shots.join(shot_targets(match, cfg)[["target", "target_angle"]])
            inputs = _lobby_inputs(match, cfg)
        except Exception as exc:
            failed += 1
            print(f"  无法检查：{type(exc).__name__}", flush=True)
            continue
        game = []
        for sid, player in match.players.items():
            if sid <= 0 or player.coverage < cfg["parsing"]["min_target_coverage"]:
                continue
            players += 1
            label = f"玩家{players:02d}"
            try:
                mm = find_moments(match, sid, cfg, sight=sight, shots_with_targets=shots)
                mm.lobby = [lobby_line(match, sid, cfg, mm, inputs=inputs)]
                report = detect_problems(mm, pro, cfg)
                game.append((mm, report, label))
            except Exception as exc:
                failed += 1
                print(f"  {label} 无法检查：{type(exc).__name__}", flush=True)
        # 先备齐同场统计，让平均线与最佳玩家也和 CLI 的单场报告一致。
        lobby = [mm.lobby[0] for mm, _, _ in game]
        for mm, report, label in game:
            mm.lobby = lobby
            page = build_match_page(mm, report, problem_frequency(report, mm, pro, cfg), cfg, tiers)
            checked += 1
            for issue in check_page(page, cfg):
                rule = issue.split("：", 1)[0]
                counts[rule] += 1
                if len(examples[rule]) < 5:
                    examples[rule].append(f"比赛{index:02d} {label} {issue}")
    for rule, count in sorted(counts.items()):
        print(f"{rule}：{count} 处")
        for example in examples[rule]:
            print(f"  {example}")
    print(f"已检查 {checked} 人次，无法检查 {failed} 项")
    print(f"报告页一致性检查：{sum(counts.values())} 处违规")
    return int(sum(counts.values()) > 0 or failed > 0 or checked == 0)


if __name__ == "__main__":
    sys.exit(main())
