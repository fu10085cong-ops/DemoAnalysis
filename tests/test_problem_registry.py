"""问题登记完整性（S08）：每个已实现的问题都要在所有该登记的地方登记。

起因：033、039 新增问题时都漏改过地方；030 启用"没看见对手就被打死"后，免费版说明文字一直缺失。
"""

import re
import tomllib
from pathlib import Path

import pytest

from cs2coach.config import load_config
from cs2coach.features.problems import DETECTED_PROBLEMS, PROBLEMS

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config()
TEXT = tomllib.loads((ROOT / "config/problem_text.toml").read_text(encoding="utf-8"))
EVIDENCE = (ROOT / "src/cs2coach/report/evidence.py").read_text(encoding="utf-8")
CATALOG = (ROOT / "docs/problem_catalog.md").read_text(encoding="utf-8")
ENABLED = [p for p in DETECTED_PROBLEMS if p not in CFG["problems"]["disabled"] and PROBLEMS[p].category != "highlight"]


@pytest.mark.parametrize("pid", DETECTED_PROBLEMS)
def test_has_dedicated_evidence_sentence(pid):
    """没有专属分支时会退化成"证据：{json}"，报告里给人看的依据就成了一串 JSON。"""
    assert re.search(rf'pid == "{pid}"', EVIDENCE), f"report/evidence.py 缺少 {pid} 的依据句"


@pytest.mark.parametrize("pid", DETECTED_PROBLEMS)
def test_listed_in_problem_catalog(pid):
    assert f"`{pid}`" in CATALOG, f"docs/problem_catalog.md 缺少 {pid}"


@pytest.mark.parametrize("pid", ENABLED)
def test_enabled_problem_has_free_text(pid):
    """默认启用的问题会出现在免费版报告里，必须有"为什么是问题"和"怎么改"。"""
    entry = TEXT.get(pid)
    assert entry and entry.get("why", "").strip() and entry.get("fix", "").strip(), f"config/problem_text.toml 缺少 [{pid}]"


def test_registry_has_no_duplicates_and_detected_are_registered():
    """替代原来写死的问题总数：只检查一致性，新增问题时不用改这里。"""
    assert set(DETECTED_PROBLEMS) <= set(PROBLEMS)
    assert len(DETECTED_PROBLEMS) == len(set(DETECTED_PROBLEMS))
