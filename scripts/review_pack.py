"""生成、安装和回填游戏内复盘包。"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import os
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
    if args.keys:
        keys = [line.strip() for line in Path(args.keys).read_text(encoding="utf-8").splitlines() if line.strip()]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("--keys 文件不能为空或含重复 hit_key")
        audit = args.audit or _find_audit(root, keys)
    else:
        if not args.audit or args.pick != "ingame":
            raise ValueError("请选择 --keys，或同时指定 --audit 和 --pick ingame")
        audit = args.audit
        keys = None
    directory = _audit_dir(root, audit)
    rows = read_hits(directory / "hits.jsonl")
    labels = _load_labels(directory / "labels.toml")
    if keys is None:
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


def build(args: argparse.Namespace, root: Path, cfg: dict) -> Path:
    audit, rows, labels = _selected(args, root)
    name = _name(args.name or (Path(args.keys).stem if args.keys else audit))
    output = root / "data" / "review" / name
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"复盘包目录已有内容：{output}")
    stems = _demo_stems(rows)
    review = cfg["review"]
    md = ["# 游戏内复盘清单", "", "先把 review.toml 的 result 填成‘对’、‘不对’或‘看不出’，可在 comment 写备注。",
          "在 CS2 控制台先输入 `exec cs2coach_review` 一次；每条再输入下面两条命令。", ""]
    toml = [f"audit = {_string(audit)}", f"demo_subdir = {_string(review['demo_subdir'])}",
            f"playdemo_template = {_string(review['playdemo_template'])}", ""]
    aliases = ["// 游戏内复盘包：先 exec cs2coach_review，再按清单加载 demo、输入 r1 等短命令。"]
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
        aliases.append(f'alias {ident} "{jump}{"; " + follow if follow else ""}"')
        label = labels[row["hit_key"]]
        question = review["questions"].get(row["problem"], review["questions"]["generic"])
        md += [f"## {ident} · {row['map']}", "", f"- Demo：`{stem}.dem`（来源：`{Path(row['demo']).name}`）",
               f"- 目标玩家：{row['player_name']}；回合：{row['evidence'].get('round', row['target'])}",
               f"- 要确认：{question}", f"- Claude 原判：{label.get('verdict') or '未判'}",
               f"- 原备注：{label.get('note') or '无'}", f"- 加载：`{play}`", f"- 跳转：`{ident}`（{jump}）", ""]
        toml += ["[[items]]", f"id = {_string(ident)}", f"hit_key = {_string(row['hit_key'])}",
                 'result = ""', 'comment = ""', ""]
    output.mkdir(parents=True, exist_ok=True)
    (output / "checklist.md").write_text("\n".join(md), encoding="utf-8")
    (output / "review.toml").write_text("\n".join(toml), encoding="utf-8")
    (output / "cs2coach_review.cfg").write_text("\n".join(aliases) + "\n", encoding="utf-8")
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
    with path.open(encoding="utf-8", newline="") as f:
        original = f.read()
    labels = tomllib.loads(original).get("labels", {})
    updates = {}
    for item in pack["items"]:
        result = item["result"].strip()
        if not result:
            print(f"跳过未填写：{item['id']}")
            continue
        if result not in RESULTS:
            raise ValueError(f"{item['id']} 的 result 无效：{result}")
        key = item["hit_key"]
        if key not in labels:
            raise ValueError(f"labels.toml 缺少 {key}")
        note = labels[key].get("note", "")
        addition = f"游戏内确认（{datetime.now().date().isoformat()}）：{result}"
        if item.get("comment", "").strip():
            addition += f"，{item['comment'].strip()}"
        updates[key] = (RESULTS[result], note + ("；" if note else "") + addition)
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
            verdict, note = updates[key]
            block = _replace_field(block, "verdict", verdict)
            block = _replace_field(block, "reason", "游戏内确认")
            block = _replace_field(block, "note", note)
            found.add(key)
        chunks.append(block)
    if found != set(updates):
        raise ValueError(f"labels.toml 无法定位条目：{set(updates) - found}")
    backup = path.with_name(f"labels.toml.bak-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}")
    with backup.open("w", encoding="utf-8", newline="") as f:
        f.write(original)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("".join(chunks))
    print(f"已备份：{backup}")
    for key, (verdict, _) in updates.items():
        print(f"{key}：{labels[key].get('verdict', '')} → {verdict}")


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="覆盖默认配置的 TOML 文件")
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build", help="生成复盘包")
    builder.add_argument("--audit", help="审计目录名")
    builder.add_argument("--pick", choices=["ingame"])
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
