"""游戏内复盘包使用临时审计目录和虚构 demo。"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from cs2coach.config import ConfigError, load_config
from scripts import review_pack


def _fixture(tmp_path: Path) -> tuple[Path, list[dict]]:
    audit = tmp_path / "data/audit/example"
    audit.mkdir(parents=True)
    rows = []
    for index, problem in enumerate(("died_without_seeing", "missed_trade", "slow_reaction"), 1):
        rows.append({"hit_key": f"{problem}-{index}", "demo": "data/demos/game.dem", "member": None,
                     "map": "de_test", "steamid": 1, "player_name": "测试玩家", "problem": problem,
                     "target": f"R{index}-death", "evidence": {"round": index},
                     "jump_command": f"demo_gototick {100 * index}"})
    (audit / "hits.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    labels = ""
    for index, row in enumerate(rows):
        labels += (f'[labels."{row["hit_key"]}"]\nverdict = "{["unsure", "correct", "wrong"][index]}"\n'
                   f'reason = "原原因"\nneeds_ingame = true\nnote = "{["原备注", "推断需要看", "保持原样"][index]}"\n'
                   f'selection = "random"\n\n')
    (audit / "labels.toml").write_text(labels, encoding="utf-8")
    demo = tmp_path / "data/demos/game.dem"
    demo.parent.mkdir(parents=True)
    demo.write_bytes(b"demo")
    return audit, rows


def test_build_pick_and_keys(tmp_path: Path) -> None:
    _, rows = _fixture(tmp_path)
    review_pack.main(["build", "--audit", "example", "--pick", "ingame", "--name", "picked"], tmp_path)
    output = tmp_path / "data/review/picked"
    pack = tomllib.loads((output / "review.toml").read_text(encoding="utf-8"))
    assert [item["hit_key"] for item in pack["items"]] == [row["hit_key"] for row in rows[:2]]
    md = (output / "checklist.md").read_text(encoding="utf-8")
    assert md.count("## r") == len(pack["items"])
    assert "测试玩家" in md and "原备注" in md and "de_test" in md
    cfg = (output / "cs2coach_review.cfg").read_text(encoding="utf-8")
    assert 'alias r1 "demo_gototick 100"' in cfg
    assert 'alias r2 "demo_gototick 200"' in cfg
    keys = tmp_path / "keys.txt"
    keys.write_text(rows[2]["hit_key"] + "\n" + rows[0]["hit_key"] + "\n", encoding="utf-8")
    review_pack.main(["build", "--keys", str(keys), "--name", "chosen"], tmp_path)
    chosen = tomllib.loads((tmp_path / "data/review/chosen/review.toml").read_text(encoding="utf-8"))
    assert [item["hit_key"] for item in chosen["items"]] == [rows[2]["hit_key"], rows[0]["hit_key"]]


def test_install_deduplicates_and_checks_conflicts(tmp_path: Path, capsys) -> None:
    _fixture(tmp_path)
    review_pack.main(["build", "--audit", "example", "--pick", "ingame", "--name", "picked"], tmp_path)
    csgo = tmp_path / "CS 2/game/csgo"
    csgo.mkdir(parents=True)
    args = ["install", "picked", "--csgo", str(csgo)]
    review_pack.main(args, tmp_path)
    assert list((csgo / "replays").glob("*.dem")) == [csgo / "replays/game.dem"]
    assert (csgo / "cfg/cs2coach_review.cfg").exists()
    review_pack.main(args, tmp_path)
    assert "相同，跳过" in capsys.readouterr().out
    target = csgo / "replays/game.dem"
    target.write_bytes(b"different")
    with pytest.raises(SystemExit):
        review_pack.main(args, tmp_path)
    assert target.read_bytes() == b"different"


def test_apply_preserves_other_entries_and_backs_up(tmp_path: Path, capsys) -> None:
    audit, rows = _fixture(tmp_path)
    keys = tmp_path / "keys.txt"
    keys.write_text("\n".join(row["hit_key"] for row in rows[:2]) + "\n", encoding="utf-8")
    review_pack.main(["build", "--audit", "example", "--keys", str(keys), "--name", "chosen"], tmp_path)
    path = tmp_path / "data/review/chosen/review.toml"
    original_pack = path.read_text(encoding="utf-8")
    path.write_text(original_pack.replace('result = ""', 'result = "不对"', 1).replace('comment = ""', 'comment = "看到了"', 1), encoding="utf-8")
    original = (audit / "labels.toml").read_text(encoding="utf-8")
    review_pack.main(["apply", "chosen"], tmp_path)
    updated = (audit / "labels.toml").read_text(encoding="utf-8")
    assert list(audit.glob("labels.toml.bak-*"))[0].read_text(encoding="utf-8") == original
    assert original.split(f'[labels."{rows[2]["hit_key"]}"]')[1] == updated.split(f'[labels."{rows[2]["hit_key"]}"]')[1]
    labels = tomllib.loads(updated)["labels"]
    assert labels[rows[0]["hit_key"]]["verdict"] == "wrong"
    assert labels[rows[0]["hit_key"]]["reason"] == "游戏内确认"
    assert labels[rows[0]["hit_key"]]["note"].startswith("原备注；游戏内确认（")
    assert "不对，看到 了" not in labels[rows[0]["hit_key"]]["note"]
    assert "不对，看到了" in labels[rows[0]["hit_key"]]["note"]
    assert labels[rows[1]["hit_key"]]["verdict"] == "correct"
    assert "跳过未填写：r2" in capsys.readouterr().out


def test_templates_and_validation(tmp_path: Path) -> None:
    _fixture(tmp_path)
    custom = tmp_path / "custom.toml"
    custom.write_text('[review]\ndemo_subdir = "watch"\nplaydemo_template = "playdemo {demo_stem}"\n'
                      'jump_template = "demo_gototick {tick} exact"\nfollow_template = "spec_player {steamid}"\n', encoding="utf-8")
    review_pack.main(["--config", str(custom), "build", "--audit", "example", "--pick", "ingame", "--name", "custom"], tmp_path)
    output = tmp_path / "data/review/custom"
    assert "playdemo game" in (output / "checklist.md").read_text(encoding="utf-8")
    assert 'demo_gototick 100 exact; spec_player 1' in (output / "cs2coach_review.cfg").read_text(encoding="utf-8")
    csgo = tmp_path / "CS 2/game/csgo"
    csgo.mkdir(parents=True)
    review_pack.main(["install", "custom", "--csgo", str(csgo)], tmp_path)
    assert (csgo / "watch/game.dem").read_bytes() == b"demo"
    custom.write_text('[review]\njump_template = "demo_gototick {bad}"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(custom)


def test_apply_all_result_values(tmp_path: Path) -> None:
    audit, rows = _fixture(tmp_path)
    keys = tmp_path / "all.txt"
    keys.write_text("\n".join(row["hit_key"] for row in rows) + "\n", encoding="utf-8")
    review_pack.main(["build", "--audit", "example", "--keys", str(keys), "--name", "all"], tmp_path)
    path = tmp_path / "data/review/all/review.toml"
    content = path.read_text(encoding="utf-8")
    for value in ("对", "不对", "看不出"):
        content = content.replace('result = ""', f'result = "{value}"', 1)
    path.write_text(content, encoding="utf-8")
    review_pack.main(["apply", "all"], tmp_path)
    labels = tomllib.loads((audit / "labels.toml").read_text(encoding="utf-8"))["labels"]
    assert [labels[row["hit_key"]]["verdict"] for row in rows] == ["correct", "wrong", "unsure"]


def test_apply_keeps_original_line_endings(tmp_path: Path) -> None:
    """判定文件在 Mac 与 Windows 间共享：写回时不能把整份文件的换行符改掉。"""
    audit, rows = _fixture(tmp_path)
    path = audit / "labels.toml"
    # 先统一成 LF 再转 CRLF：Windows 上 write_text 写出的已经是 CRLF，直接替换会变成 \r\r\n
    crlf = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(crlf)
    keys = tmp_path / "keys.txt"
    keys.write_text(rows[0]["hit_key"] + "\n", encoding="utf-8")
    review_pack.main(["build", "--audit", "example", "--keys", str(keys), "--name", "eol"], tmp_path)
    pack = tmp_path / "data/review/eol/review.toml"
    pack.write_text(pack.read_text(encoding="utf-8").replace('result = ""', 'result = "对"', 1), encoding="utf-8")
    review_pack.main(["apply", "eol"], tmp_path)
    updated = path.read_bytes()
    assert b"\n" not in updated.replace(b"\r\n", b"")  # 没有混入单独的 LF
    other = f'[labels."{rows[2]["hit_key"]}"]'.encode()
    assert crlf.split(other)[1] == updated.split(other)[1]


def test_checklist_names_opponent_and_load_alias(tmp_path: Path) -> None:
    """产品负责人 2026-10-17 实测：清单要写明对手是谁，并能用短命令加载 demo；共享目录里的文件一律 LF。"""
    audit, rows = _fixture(tmp_path)
    rows[0]["fact_card"] = ("### 〔R1-death〕死亡（CT，第 109 秒，4v3，已下包）\n\n"
                            "- 你在 BombsiteB，对手 某对手 在 BombsiteB，距离 8 米。\n"
                            "- 你手持 M4A1-S，对手手持 AK-47；本回合装备价值 5700 对 4800。\n"
                            "- 交火前血量：你 75，对手 21。\n- 对手用 AK-47（爆头）击杀了你。")
    rows[1]["fact_card"] = ("### 〔R2〕漏补枪\n\n- 第 20 秒，T 方，下包前，队友阵亡后 4v4。\n"
                            "- 队友 甲 被对手 乙 击杀，阵亡时离你 335 单位，你在补枪时间窗口内没有击杀击杀他的对手。")
    (audit / "hits.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    review_pack.main(["build", "--audit", "example", "--pick", "ingame", "--name", "scene"], tmp_path)
    output = tmp_path / "data/review/scene"
    md = (output / "checklist.md").read_text(encoding="utf-8")
    assert "时间与局面：死亡（CT，第 109 秒，4v3，已下包）" in md
    assert "对手 某对手 在 BombsiteB，距离 8 米" in md and "对手用 AK-47（爆头）击杀了你" in md
    assert "被对手 乙 击杀" in md and "交火前血量" not in md
    cfg = (output / "cs2coach_review.cfg").read_text(encoding="utf-8")
    assert 'alias p1 "playdemo ' in cfg and 'alias r1 "demo_gototick 100' in cfg
    assert "加载：`p1`" in md
    for name in ("checklist.md", "review.toml", "cs2coach_review.cfg"):
        assert b"\r\n" not in (output / name).read_bytes()
