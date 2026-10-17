"""离线报告的数据嵌入、脚本边界与模板资源。"""

import json
from html.parser import HTMLParser

import pytest

from cs2coach.report.page_data import ReportPage
from cs2coach.report.page_html import render_page


class PageDataParser(HTMLParser):
    """按浏览器的 script 边界读取数据，捕获提前闭合问题。"""

    def __init__(self):
        super().__init__()
        self.in_data = False
        self.data = ""

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "page-data":
            self.in_data = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.data += data


def sample_page(kind="match", tiers=True):
    """只用匿名构造数据，覆盖空值、命令与两类报告。"""
    command = {"playdemo": "playdemo replays/example", "jump": "demo_gototick 123; demo_pause"}
    example = dict(match_label="本场" if kind == "match" else "Dust2 M1", round=2,
                   time="0:42", situation="T 方，2v2，未下包", what="与对手甲交火后阵亡",
                   tick=123, command=command)
    row = dict(id="slow_reaction", name="出枪慢", axis="speed", you=3, target=2,
               unit="次/10回合", count=3, severity="bad", text="需要练习。", examples=[example])
    comparison = dict(id="tier_median", name="分段中位数", values=[50] * 6)
    return ReportPage(kind=kind, title="目标玩家", subtitle="本场" if kind == "match" else "2 场",
        axes=[dict(id="speed" if i == 0 else str(i), name=name)
            for i, name in enumerate(["出枪速度", "准星位置", "急停", "压枪", "补枪配合", "道具使用"])],
        default_tier="amateur",
        tiers=[dict(id="amateur", name="业余", source="合成数据", you=[None, 20, 40, 60, 80, 100],
                    comparisons=[comparison], verdict="业余分段结论", strengths=[], problems=[row]),
               dict(id="pro", name="职业", source="合成数据", you=[10] * 6,
                    comparisons=[comparison], verdict="职业分段结论", strengths=[], problems=[row])] if tiers else [],
        highlights=[dict(kind="clutch", title="R2 残局获胜", **{k: v for k, v in example.items()
                                                              if k != "situation"})])


@pytest.mark.parametrize("kind", ["match", "summary"])
def test_embedded_json_round_trip_and_no_external_resources(kind):
    page = sample_page(kind)
    html = render_page(page)
    assert 'id="page-data"' in html
    parser = PageDataParser()
    parser.feed(html)
    assert json.loads(parser.data) == page.model_dump(mode="json")
    assert "http://" not in html and "https://" not in html


def test_script_boundary_in_names_and_other_text():
    page = sample_page()
    dangerous = '</script><b> & " __PAGE_DATA__'
    page.title = dangerous
    page.tiers[0].problems[0].text = dangerous
    page.tiers[0].problems[0].examples[0].command.playdemo = dangerous
    html = render_page(page)
    assert "</script><b>" not in html
    parser = PageDataParser()
    parser.feed(html)
    assert json.loads(parser.data) == page.model_dump(mode="json")


def test_missing_tiers_keeps_instruction_and_page_sections():
    html = render_page(sample_page(tiers=False))
    assert "还没有构建分段基准：" in html
    assert "build_tier_baseline.py --all" in html
    for section in ("做得好的方向", "问题汇总", "高光回合"):
        assert section in html
