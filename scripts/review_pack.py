"""生成、安装和回填游戏内复盘包。

标准答案集生成示例（以下参数接在同一条命令后）：
    python scripts/review_pack.py build --audit 2026-10-18-after046 --pick stratified
      --rules poor_crosshair_placement,slow_reaction,shot_while_moving,missed_trade,died_without_seeing,saw_but_no_shot
      --per-rule 5 --seed 20261018 --blind --name 2026-10-18-gold
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
import random
import re
import shutil
import tomllib
from datetime import datetime
from pathlib import Path

from cs2coach.audit import read_hits
from cs2coach.config import load_config
from cs2coach.parsing.loader import resolve_demo

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
SECTION = re.compile(r'^\[labels\."((?:[^"\\]|\\.)*)"\][^\n]*$', re.M)
RESULTS = {"对": "correct", "不对": "wrong", "看不出": "unsure"}
WORTH = {"值得": "yes", "不值得": "no"}


def _string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _name(value: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError(f"目录名无效：{value}")
    return value


def _audit_dir(root: Path, name: str) -> Path:
    return root / "data" / "audit" / _name(name)


def _load_labels(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("labels", {})


def _read_pack(path: Path) -> dict:
    pack = tomllib.loads((path / "review.toml").read_text(encoding="utf-8"))
    _name(pack["audit"])
    subdir = pack["demo_subdir"]
    if (not isinstance(subdir, str) or not subdir or Path(subdir).is_absolute() or
            any(part in (".", "..") for part in subdir.replace("\\", "/").split("/")) or
            ":" in subdir or "\\" in subdir):
        raise ValueError("review.toml 的 demo_subdir 无效")
    ids = [item["id"] for item in pack["items"]]
    keys = [item["hit_key"] for item in pack["items"]]
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
        raise ValueError("review.toml 含重复条目")
    return pack


def _find_audit(root: Path, keys: list[str]) -> str:
    matches = []
    for directory in (root / "data" / "audit").iterdir():
        if directory.is_dir() and (directory / "hits.jsonl").is_file():
            rows = read_hits(directory / "hits.jsonl")
            if set(keys) <= {row["hit_key"] for row in rows}:
                matches.append(directory.name)
    if len(matches) != 1:
        raise ValueError(f"无法唯一确定审计目录（匹配：{matches}），请指定 --audit")
    return matches[0]


def _selected(args: argparse.Namespace, root: Path) -> tuple[str, list[dict], dict]:
    stratified = args.pick == "stratified" and not args.keys
    if stratified:
        rules = [rule.strip() for rule in (args.rules or "").split(",")]
        if (not args.audit or not all(rules) or len(rules) != len(set(rules)) or
                args.per_rule is None or args.per_rule <= 0):
            raise ValueError("分层抽样须指定 --audit、--rules（非空且不重复）和 --per-rule（正整数）")
    if args.keys:
        keys = [line.strip() for line in Path(args.keys).read_text(encoding="utf-8").splitlines() if line.strip()]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("--keys 文件不能为空或含重复 hit_key")
        audit = args.audit or _find_audit(root, keys)
    else:
        if not args.audit or args.pick not in ("ingame", "stratified"):
            raise ValueError("请选择 --keys，或同时指定 --audit 和 --pick ingame/stratified")
        audit = args.audit
        keys = None
    directory = _audit_dir(root, audit)
    rows = read_hits(directory / "hits.jsonl")
    label_path = directory / "labels.toml"
    labels = _load_labels(label_path) if label_path.exists() or not stratified else {}
    if stratified:
        rng = random.Random(args.seed)
        selected = []
        for rule in rules:
            candidates = sorted((row for row in rows if row["problem"] == rule), key=lambda row: row["hit_key"])
            selected.extend(rng.sample(candidates, args.per_rule) if len(candidates) > args.per_rule else candidates)
    elif keys is None:
        selected = [row for row in rows if (label := labels.get(row["hit_key"], {})).get("needs_ingame") is True
                    and (label.get("verdict") == "unsure" or "推断" in label.get("note", ""))]
    else:
        by_key = {row["hit_key"]: row for row in rows}
        missing = [key for key in keys if key not in by_key or key not in labels]
        if missing:
            raise ValueError(f"审计命中或标签里缺少 hit_key：{missing}")
        selected = [by_key[key] for key in keys]
    if not selected:
        raise ValueError("没有符合条件的条目")
    return audit, selected, labels


def _demo_stems(rows: list[dict]) -> dict[tuple[str, str | None], str]:
    stems = {}
    used = set()
    for row in rows:
        source = (row["demo"], row.get("member"))
        if source in stems:
            continue
        filename = Path(row.get("member") or row["demo"]).name
        stem = filename[:-4] if filename.lower().endswith(".dem") else Path(filename).stem
        if not re.fullmatch(r"[A-Za-z0-9_-]+", stem):
            raise ValueError(f"demo 文件名无法用于 CS2 控制台：{filename}")
        if stem in used:
            stem += "_" + hashlib.sha256(repr(source).encode("utf-8")).hexdigest()[:8]
        used.add(stem)
        stems[source] = stem
    return stems


def _scene(card: str) -> list[str]:
    """从事实卡取出看 demo 时需要的场景：时间与局面、双方位置与距离、双方武器、谁击杀谁；漏补取队友与击杀者。"""
    lines = card.splitlines()
    out = []
    if lines and lines[0].startswith("#"):
        head = re.sub(r"^#+\s*〔[^〕]*〕", "", lines[0]).strip()
        if head:
            out.append(f"时间与局面：{head}")
    keep = ("你在 ", "你手持 ", "对手用 ", "你用 ", "队友 ", "第 ")
    out += [line[2:] for line in lines if line.startswith("- ") and line[2:].startswith(keep)]
    return out


def build(args: argparse.Namespace, root: Path, cfg: dict) -> Path:
    audit, rows, labels = _selected(args, root)
    name = _name(args.name or (Path(args.keys).stem if args.keys else audit))
    output = root / "data" / "review" / name
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"复盘包目录已有内容：{output}")
    stems = _demo_stems(rows)
    review = cfg["review"]
    md = ["# 游戏内复盘清单", "", "先把 review.toml 的 result 填成‘对’、‘不对’或‘看不出’，可在 comment 写备注。",
          "worth 填‘值得’或‘不值得’，表示如果你是教练，会不会因为这一条提醒玩家。",
          "在 CS2 控制台先输入 `exec cs2coach_review` 一次；每条先输入 `p1` 等加载 demo，等加载完成后再输入 `r1` 等跳转。",
          "result 记的是“这条检测对不对”：问题只是帮你判断；如果认为这条不该算作问题（例如当时在换弹、在跳跃），"
          "填‘不对’并写明原因。", ""]
    toml = [f"audit = {_string(audit)}", f"demo_subdir = {_string(review['demo_subdir'])}",
            f"playdemo_template = {_string(review['playdemo_template'])}", ""]
    aliases = ["// 游戏内复盘包：先 exec cs2coach_review，再按清单输入 p1 加载 demo、r1 跳转。"]
    for index, row in enumerate(rows, 1):
        ident = f"r{index}"
        command = row.get("jump_command") or ""
        match = re.fullmatch(r"demo_gototick (\d+)", command)
        if not match:
            raise ValueError(f"{row['hit_key']} 没有可用的跳转 tick")
        tick = int(match.group(1))
        stem = stems[(row["demo"], row.get("member"))]
        play = review["playdemo_template"].format(subdir=review["demo_subdir"], demo_stem=stem)
        jump = review["jump_template"].format(tick=tick)
        follow = review["follow_template"].format(steamid=row["steamid"])
        if any(char in play + jump + follow for char in ('"', '\n', '\r')):
            raise ValueError("CS2 命令不能包含引号或换行")
        aliases.append(f'alias p{index} "{play}"')
        aliases.append(f'alias {ident} "{jump}{"; " + follow if follow else ""}"')
        label = labels.get(row["hit_key"], {})
        question = review["questions"].get(row["problem"], review["questions"]["generic"])
        md += [f"## {ident} · {row['map']}", "", f"- Demo：`{stem}.dem`（来源：`{Path(row['demo']).name}`）",
               f"- 看谁的视角：{row['player_name']}；回合：{row['evidence'].get('round', row['target'])}",
               *[f"- {line}" for line in _scene(row.get("fact_card", ""))],
               f"- 要确认：{question}",
               "- 值得提醒吗：如果你是教练，会因为这一条提醒玩家吗？在 review.toml 的 worth 填‘值得’或‘不值得’。"]
        if not args.blind:
            md += [f"- Claude 原判：{label.get('verdict') or '未判'}", f"- 原备注：{label.get('note') or '无'}"]
        md += [f"- 加载：`p{index}`（{play}）", f"- 跳转：`{ident}`（{jump}）", ""]
        toml += ["[[items]]", f"id = {_string(ident)}", f"hit_key = {_string(row['hit_key'])}",
                 'result = ""', 'worth = ""', 'comment = ""', ""]
    output.mkdir(parents=True, exist_ok=True)
    # 复盘包放在两台电脑共享的目录里：一律写 LF，避免 Windows 默认的 CRLF 让文件来回改换行符
    (output / "checklist.md").write_text("\n".join(md), encoding="utf-8", newline="\n")
    (output / "review.toml").write_text("\n".join(toml), encoding="utf-8", newline="\n")
    (output / "cs2coach_review.cfg").write_text("\n".join(aliases) + "\n", encoding="utf-8", newline="\n")
    print(f"生成 {len(rows)} 条：{output}")
    return output


def install(args: argparse.Namespace, root: Path, cfg: dict) -> None:
    pack_dir = root / "data" / "review" / _name(args.name)
    pack = _read_pack(pack_dir)
    csgo_value = args.csgo or cfg["review"]["csgo_dir"] or os.environ.get("CS2COACH_CSGO_DIR")
    if not csgo_value:
        raise ValueError("请用 --csgo、review.csgo_dir 或 CS2COACH_CSGO_DIR 指定 game/csgo 目录")
    csgo = Path(csgo_value)
    if not csgo.is_dir():
        raise ValueError(f"CS2 game/csgo 目录不存在：{csgo}")
    rows = read_hits(_audit_dir(root, pack["audit"]) / "hits.jsonl")
    by_key = {row["hit_key"]: row for row in rows}
    selected = [by_key[item["hit_key"]] for item in pack["items"]]
    stems = _demo_stems(selected)
    copies = {}
    subdir = pack["demo_subdir"]
    for row in selected:
        source = (row["demo"], row.get("member"))
        if source in copies:
            continue
        path = Path(row["demo"])
        if not path.is_absolute():
            path = root / path
        dem = resolve_demo(path, member=row.get("member"))
        copies[source] = (dem, csgo / subdir / (stems[source] + ".dem"))
    copies["cfg"] = (pack_dir / "cs2coach_review.cfg", csgo / "cfg" / "cs2coach_review.cfg")
    for source, target in copies.values():
        if target.exists() and not filecmp.cmp(source, target, shallow=False):
            raise ValueError(f"目标文件内容不同，未覆盖：{target}")
    for source, target in copies.values():
        if target.exists():
            print(f"相同，跳过：{target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            print(f"已复制：{target}")
    print("CS2 控制台先输入：exec cs2coach_review")
    for index, row in enumerate(selected, 1):
        stem = stems[(row["demo"], row.get("member"))]
        play = pack["playdemo_template"].format(subdir=subdir, demo_stem=stem)
        print(f"r{index}：{play}，然后 r{index}")


def _replace_field(block: str, field: str, value: str) -> str:
    pattern = re.compile(rf'^([ \t]*{field}[ \t]*=[ \t]*)[^\r\n]*(\r?\n|$)', re.M)
    if not pattern.search(block):
        raise ValueError(f"labels.toml 条目缺少 {field}")
    return pattern.sub(lambda match: match.group(1) + _string(value) + match.group(2), block, count=1)


def apply(args: argparse.Namespace, root: Path) -> None:
    pack = _read_pack(root / "data" / "review" / _name(args.name))
    path = _audit_dir(root, pack["audit"]) / "labels.toml"
    # 按原样读写换行符：判定文件在两台电脑间共享，Windows 默认写法会把整份文件改成 CRLF
    original = ""
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            original = f.read()
    labels = tomllib.loads(original).get("labels", {})
    updates = {}
    for item in pack["items"]:
        result = item["result"].strip()
        worth = item.get("worth", "").strip()
        if worth and worth not in WORTH:
            raise ValueError(f"{item['id']} 的 worth 无效：{worth}")
        if worth and not result:
            raise ValueError(f"{item['id']} 填写 worth 时必须同时填写 result")
        if not result:
            print(f"跳过未填写：{item['id']}")
            continue
        if result not in RESULTS:
            raise ValueError(f"{item['id']} 的 result 无效：{result}")
        key = item["hit_key"]
        note = labels.get(key, {}).get("note", "")
        addition = f"游戏内确认（{datetime.now().date().isoformat()}）：{result}"
        if item.get("comment", "").strip():
            addition += f"，{item['comment'].strip()}"
        updates[key] = (RESULTS[result], note + ("；" if note else "") + addition, WORTH.get(worth))
    if not updates:
        print("没有需要写回的结果")
        return
    matches = list(SECTION.finditer(original))
    chunks = [original[:matches[0].start()]] if matches else [original]
    found = set()
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(original)
        block = original[match.start():end]
        key = json.loads('"' + match.group(1) + '"')
        if key in updates:
            verdict, note, worth = updates[key]
            block = _replace_field(block, "verdict", verdict)
            block = _replace_field(block, "reason", "游戏内确认")
            block = _replace_field(block, "note", note)
            if worth is not None:
                if "worth" in labels[key]:
                    block = _replace_field(block, "worth", worth)
                else:
                    # 在 verdict 后插入，并沿用该条目的换行符。
                    pattern = re.compile(r'^([ \t]*verdict[^\r\n]*)(\r?\n|$)', re.M)
                    block = pattern.sub(lambda m: m.group(1) + (m.group(2) or "\n") +
                                        f"worth = {_string(worth)}" + (m.group(2) or "\n"), block, count=1)
            found.add(key)
        chunks.append(block)
    missing = (set(updates) & set(labels)) - found
    if missing:
        raise ValueError(f"labels.toml 无法定位条目：{missing}")
    newline = "\r\n" if "\r\n" in original else "\n"
    for key, (verdict, note, worth) in updates.items():
        if key in found:
            continue
        fields = [f"[labels.{_string(key)}]", f"verdict = {_string(verdict)}",
                  'reason = "游戏内确认"', "needs_ingame = false", f"note = {_string(note)}",
                  'selection = "ingame-gold"']
        if worth is not None:
            fields.append(f"worth = {_string(worth)}")
        chunks.append(newline * 2 + newline.join(fields) + newline)
    if path.exists():
        backup = path.with_name(f"labels.toml.bak-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}")
        with backup.open("w", encoding="utf-8", newline="") as f:
            f.write(original)
        print(f"已备份：{backup}")
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(chunks))
    for key, (verdict, _, _) in updates.items():
        print(f"{key}：{labels.get(key, {}).get('verdict', '')} → {verdict}")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="覆盖默认配置的 TOML 文件")
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="生成复盘包")
    builder.add_argument("--audit", help="审计目录名")
    builder.add_argument("--pick", choices=["ingame", "stratified"])
    builder.add_argument("--rules", help="分层抽样的规则，以逗号分隔")
    builder.add_argument("--per-rule", type=int, help="每条规则最多抽取的条数")
    builder.add_argument("--seed", type=int, default=0, help="分层抽样的随机种子，默认 0")
    builder.add_argument("--blind", action="store_true", help="清单隐藏 Claude 原判和原备注")
    builder.add_argument("--keys", type=Path, help="每行一个 hit_key")
    builder.add_argument("--name", help="复盘包目录名")
    installer = commands.add_parser("install", help="复制 demo 和 cfg 到 CS2")
    installer.add_argument("name", help="复盘包目录名")
    installer.add_argument("--csgo", type=Path, help="CS2 的 game/csgo 目录")
    applier = commands.add_parser("apply", help="回填审计判定")
    applier.add_argument("name", help="复盘包目录名")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.command == "build":
            build(args, root, cfg)
        elif args.command == "install":
            install(args, root, cfg)
        else:
            apply(args, root)
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
