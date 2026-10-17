"""049 标准答案集：分层抽样、盲审（不显示 Claude 原判）、回填"值不值得提醒"。预写测试，实现方不要修改。"""

import json
import tomllib
from pathlib import Path

import pytest

from scripts import review_pack


def _fixture(tmp_path: Path, with_labels: bool = True) -> Path:
    """两条规则各 4 条命中、另一条规则 1 条；只有部分命中有 Claude 的判定。"""
    audit = tmp_path / "data/audit/gold"
    audit.mkdir(parents=True)
    rows = []
    for problem, count in (("slow_reaction", 4), ("missed_trade", 4), ("spray_control", 1)):
        for index in range(1, count + 1):
            rows.append({"hit_key": f"{problem}-{index}", "demo": "data/demos/game.dem", "member": None,
                         "map": "de_test", "steamid": 1, "player_name": "目标玩家", "problem": problem,
                         "target": f"R{index}-death", "evidence": {"round": index},
                         "jump_command": f"demo_gototick {1000 * index}"})
    (audit / "hits.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    if with_labels:
        (audit / "labels.toml").write_text(
            '[labels."slow_reaction-1"]\nverdict = "correct"\nreason = "Claude"\nneeds_ingame = false\n'
            'note = "Claude 认为慢了 200 毫秒"\nselection = "random"\nworth = "yes"\n\n', encoding="utf-8")
    demo = tmp_path / "data/demos/game.dem"
    demo.parent.mkdir(parents=True)
    demo.write_bytes(b"demo")
    return audit


def _build(tmp_path, *extra):
    review_pack.main(["build", "--audit", "gold", "--pick", "stratified", "--rules", "slow_reaction,missed_trade,spray_control",
                      "--per-rule", "2", "--seed", "7", "--name", "gold", *extra], tmp_path)
    return tmp_path / "data/review/gold"


def test_stratified_pick_takes_per_rule_in_rule_order_and_is_reproducible(tmp_path):
    _fixture(tmp_path)
    pack = tomllib.loads((_build(tmp_path) / "review.toml").read_text(encoding="utf-8"))
    keys = [item["hit_key"] for item in pack["items"]]
    problems = [key.rsplit("-", 1)[0] for key in keys]
    # 每条规则最多 2 条；不足 2 条的规则有几条取几条；按 --rules 的顺序排列
    assert problems == ["slow_reaction", "slow_reaction", "missed_trade", "missed_trade", "spray_control"]
    assert len(set(keys)) == 5
    # 同一个 seed 再生成一次，抽到的条目完全相同
    first = keys
    review_pack.main(["build", "--audit", "gold", "--pick", "stratified", "--rules", "slow_reaction,missed_trade,spray_control",
                      "--per-rule", "2", "--seed", "7", "--name", "gold2"], tmp_path)
    again = tomllib.loads((tmp_path / "data/review/gold2/review.toml").read_text(encoding="utf-8"))
    assert [item["hit_key"] for item in again["items"]] == first


def test_stratified_requires_rules_and_per_rule(tmp_path):
    _fixture(tmp_path)
    with pytest.raises((SystemExit, ValueError)):
        review_pack.main(["build", "--audit", "gold", "--pick", "stratified", "--name", "x"], tmp_path)


def test_items_have_worth_field_and_checklist_asks_it(tmp_path):
    _fixture(tmp_path)
    output = _build(tmp_path)
    pack = tomllib.loads((output / "review.toml").read_text(encoding="utf-8"))
    assert all(item["worth"] == "" and item["result"] == "" for item in pack["items"])
    md = (output / "checklist.md").read_text(encoding="utf-8")
    assert md.count("值得提醒吗") == len(pack["items"])
    assert "worth" in md and "值得" in md and "不值得" in md


def test_hits_without_claude_labels_can_be_picked(tmp_path):
    _fixture(tmp_path, with_labels=False)  # 新的审计目录还没有 labels.toml
    output = _build(tmp_path)
    md = (output / "checklist.md").read_text(encoding="utf-8")
    assert "未判" in md


def test_blind_hides_claude_judgement(tmp_path):
    _fixture(tmp_path)
    output = _build(tmp_path, "--blind")
    md = (output / "checklist.md").read_text(encoding="utf-8")
    assert "Claude 原判" not in md and "原备注" not in md and "慢了 200 毫秒" not in md
    not_blind = tmp_path / "data/review/open"
    review_pack.main(["build", "--audit", "gold", "--pick", "stratified", "--rules", "slow_reaction",
                      "--per-rule", "4", "--seed", "7", "--name", "open"], tmp_path)
    assert "Claude 原判" in (not_blind / "checklist.md").read_text(encoding="utf-8")


def _fill(output: Path, values: dict[str, tuple[str, str]]) -> None:
    """values：hit_key → (result, worth)。"""
    text = (output / "review.toml").read_text(encoding="utf-8")
    blocks = text.split("[[items]]")
    for i, block in enumerate(blocks):
        for key, (result, worth) in values.items():
            if f'hit_key = "{key}"' in block:
                block = block.replace('result = ""', f'result = "{result}"', 1).replace('worth = ""', f'worth = "{worth}"', 1)
        blocks[i] = block
    (output / "review.toml").write_text("[[items]]".join(blocks), encoding="utf-8")


def test_apply_writes_worth_and_creates_missing_labels(tmp_path):
    audit = _fixture(tmp_path)
    output = _build(tmp_path)
    pack = tomllib.loads((output / "review.toml").read_text(encoding="utf-8"))
    keys = [item["hit_key"] for item in pack["items"]]
    labelled = "slow_reaction-1" if "slow_reaction-1" in keys else None
    new_key = next(k for k in keys if k != "slow_reaction-1")
    values = {new_key: ("对", "不值得")}
    if labelled:
        values[labelled] = ("不对", "不值得")
    _fill(output, values)
    review_pack.main(["apply", "gold"], tmp_path)
    labels = tomllib.loads((audit / "labels.toml").read_text(encoding="utf-8"))["labels"]
    # 原来没有判定的命中：新建一条
    assert labels[new_key]["verdict"] == "correct" and labels[new_key]["worth"] == "no"
    assert labels[new_key]["reason"] == "游戏内确认"
    assert labels[new_key]["selection"] == "ingame-gold"
    if labelled:
        # 原来有判定的：verdict、worth 都被覆盖，原备注保留在前面
        assert labels[labelled]["verdict"] == "wrong" and labels[labelled]["worth"] == "no"
        assert labels[labelled]["note"].startswith("Claude 认为慢了 200 毫秒；游戏内确认（")


def test_apply_creates_labels_file_when_audit_has_none(tmp_path):
    audit = _fixture(tmp_path, with_labels=False)
    output = _build(tmp_path)
    key = tomllib.loads((output / "review.toml").read_text(encoding="utf-8"))["items"][0]["hit_key"]
    _fill(output, {key: ("对", "值得")})
    review_pack.main(["apply", "gold"], tmp_path)
    labels = tomllib.loads((audit / "labels.toml").read_text(encoding="utf-8"))["labels"]
    assert labels[key]["verdict"] == "correct" and labels[key]["worth"] == "yes"
    assert b"\r\n" not in (audit / "labels.toml").read_bytes()


def test_apply_rejects_invalid_worth_and_worth_without_result(tmp_path):
    _fixture(tmp_path)
    output = _build(tmp_path)
    key = tomllib.loads((output / "review.toml").read_text(encoding="utf-8"))["items"][0]["hit_key"]
    _fill(output, {key: ("对", "也许")})
    with pytest.raises((SystemExit, ValueError)):
        review_pack.main(["apply", "gold"], tmp_path)
    output2 = tmp_path / "data/review/gold-b"
    review_pack.main(["build", "--audit", "gold", "--pick", "stratified", "--rules", "missed_trade",
                      "--per-rule", "1", "--seed", "7", "--name", "gold-b"], tmp_path)
    key2 = tomllib.loads((output2 / "review.toml").read_text(encoding="utf-8"))["items"][0]["hit_key"]
    _fill(output2, {key2: ("", "值得")})  # 只填了 worth、没填 result
    with pytest.raises((SystemExit, ValueError)):
        review_pack.main(["apply", "gold-b"], tmp_path)


def test_old_packs_without_worth_still_apply(tmp_path):
    """旧复盘包（2026-10-17 的两个）没有 worth 字段，apply 照常工作。"""
    from tests.test_review_pack import _fixture as old_fixture
    audit, rows = old_fixture(tmp_path)
    keys = tmp_path / "keys.txt"
    keys.write_text(rows[0]["hit_key"] + "\n", encoding="utf-8")
    review_pack.main(["build", "--audit", "example", "--keys", str(keys), "--name", "old"], tmp_path)
    path = tmp_path / "data/review/old/review.toml"
    text = path.read_text(encoding="utf-8").replace('worth = ""\n', "").replace('result = ""', 'result = "对"', 1)
    path.write_text(text, encoding="utf-8")
    review_pack.main(["apply", "old"], tmp_path)
    labels = tomllib.loads((audit / "labels.toml").read_text(encoding="utf-8"))["labels"]
    assert labels[rows[0]["hit_key"]]["verdict"] == "correct"
