"""研究目录分类后的路径查找。"""

import pytest

from cs2coach.research.paths import research_dir


def _pack(root, kind, category, name, marker):
    path = root / "data" / "research" / kind / category / name if category else root / "data" / "research" / kind / name
    path.mkdir(parents=True)
    (path / marker).write_text("", encoding="utf-8")
    return path


@pytest.mark.parametrize("kind,marker", [("review", "review.toml"), ("stance", "windows.csv"), ("stance", "facts.csv")])
def test_top_level_and_category(tmp_path, kind, marker):
    top = _pack(tmp_path, kind, "", "top", marker)
    nested = _pack(tmp_path, kind, "old", "nested", marker)
    assert research_dir(kind, "top", tmp_path) == top
    assert research_dir(kind, "nested", tmp_path) == nested
    assert research_dir(kind, "old/nested", tmp_path) == nested
    assert research_dir(kind, "new", tmp_path) == top.parent / "new"


def test_duplicate_category_names_raise(tmp_path):
    first = _pack(tmp_path, "review", "gold", "same", "review.toml")
    second = _pack(tmp_path, "review", "old", "same", "review.toml")
    with pytest.raises(ValueError) as error:
        research_dir("review", "same", tmp_path)
    assert str(first) in str(error.value) and str(second) in str(error.value)


def test_category_name_does_not_mask_pack(tmp_path):
    nested = _pack(tmp_path, "review", "old", "gold", "review.toml")
    (tmp_path / "data" / "research" / "review" / "gold").mkdir(exist_ok=True)
    assert research_dir("review", "gold", tmp_path) == nested


def test_hidden_and_private_categories_are_skipped(tmp_path):
    _pack(tmp_path, "stance", ".hidden", "same", "windows.csv")
    _pack(tmp_path, "stance", "_private", "same", "facts.csv")
    expected = tmp_path / "data" / "research" / "stance" / "same"
    assert research_dir("stance", "same", tmp_path) == expected


def test_stance_score_finds_categorized_inputs(tmp_path):
    """score 命令按原名字读取分类后的复盘包和判定结果。"""
    import pandas as pd

    from scripts import research_stance
    from scripts.research_review import _write_review

    pack = _pack(tmp_path, "review", "gold", "x", "review.toml")
    _write_review(pack, {"source": "stance/source"}, [
        {"id": "g1", "demo_id": "m.dem", "round_id": 1, "player_id": "7", "result": "保守", "comment": ""},
    ])
    pd.DataFrame([{"id": "g1", "split": "dev"}]).to_csv(pack / "answer_key.csv", index=False)
    source = _pack(tmp_path, "stance", "old", "source", "windows.csv")
    pd.DataFrame([{"demo_id": "m.dem", "round_id": 1, "player_id": "7", "behaviour": "保守"}]).to_csv(
        source / "windows.csv", index=False)

    assert "细类：一致 1/1" in research_stance.score("x", "source", root=tmp_path)
