"""审计判定的第二项"值不值得提醒"（任务 S04，Claude 预写，实现方不得修改）。"""

import tomllib

import pytest

from cs2coach.audit import precision_report, review_page, update_labels, wilson
from tests.test_audit import record


def _labels():
    return {"key-0": {"verdict": "correct", "worth": "yes"},
            "key-1": {"verdict": "wrong", "reason": "视野不准"},
            "key-2": {"verdict": "correct", "worth": "no"},
            "key-3": {"verdict": "correct", "worth": "yes"},
            "key-4": {"verdict": "correct", "worth": ""},
            "key-5": {"verdict": "unsure"}}


def _rows():
    return [record(f"key-{i}", suspicious=i < 2) for i in range(6)]


def test_worth_table_counts_and_weights_by_stratum():
    report = precision_report(_rows(), _labels())
    assert "## 有用率（事实成立且值得提醒）" in report
    section = report.split("## 有用率（事实成立且值得提醒）")[1]
    # 有用 = correct 且 worth=yes；无用 = wrong，或 correct 且 worth=no；未填 = correct 但 worth 为空，不计入有用率
    assert "| slow_reaction | 可疑层 | 2 | 1 | 1 | 0 | 50.0% |" in section
    assert "| slow_reaction | 不可疑层 | 3 | 1 | 1 | 1 | 50.0% |" in section
    assert "| slow_reaction | 整体（加权） | 5 | 2 | 2 | 1 | 50.0% |" in section
    a, b = wilson(1, 1), wilson(1, 1)
    interval = f"{(a[0] / 3 + 2 * b[0] / 3):.1%}–{(a[1] / 3 + 2 * b[1] / 3):.1%}"
    assert f"| slow_reaction | 整体（加权） | 5 | 2 | 2 | 1 | 50.0% | {interval} |" in section


def test_precision_table_is_unchanged_by_worth():
    with_worth = precision_report(_rows(), _labels())
    without = {k: {kk: vv for kk, vv in v.items() if kk != "worth"} for k, v in _labels().items()}
    plain = precision_report(_rows(), without)
    assert with_worth.split("## 有用率（事实成立且值得提醒）")[0].split("## 错误原因")[0] == \
        plain.split("## 有用率（事实成立且值得提醒）")[0].split("## 错误原因")[0]


def test_old_labels_without_worth_still_report():
    without = {k: {kk: vv for kk, vv in v.items() if kk != "worth"} for k, v in _labels().items()}
    report = precision_report(_rows(), without)
    assert "暂无“值不值得提醒”判定。" in report


@pytest.mark.parametrize("entry", [{"verdict": "correct", "worth": "maybe"},
                                   {"verdict": "wrong", "worth": "yes"},
                                   {"verdict": "unsure", "worth": "no"}])
def test_invalid_worth_raises(entry):
    with pytest.raises(ValueError):
        precision_report([record("key-0")], {"key-0": entry})


def test_new_label_entries_have_empty_worth(tmp_path):
    path = tmp_path / "labels.toml"
    update_labels(path, [(record("key-1"), "random")], {"slow_reaction"})
    labels = tomllib.loads(path.read_text(encoding="utf-8"))["labels"]
    assert labels["key-1"]["worth"] == ""


def test_review_page_asks_for_worth():
    page = review_page("slow_reaction", [(record("key-1"), "random")])
    assert "值得提醒：" in page and "yes / no" in page
