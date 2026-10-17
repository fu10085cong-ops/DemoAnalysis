"""047 报告目录页：扫描输出目录下的 report.html，生成一个可点击的离线目录。预写测试，实现方不要修改。"""

import json
import os
from pathlib import Path

from cs2coach.report.index_page import collect_reports, render_index, write_index


def _report(root: Path, player_dir: str, match: str, kind: str, title: str, verdict: str | None,
            mtime: float, html: bool = True) -> Path:
    folder = root / player_dir / match
    folder.mkdir(parents=True)
    tiers = [] if verdict is None else [{"id": "amateur_tmp", "verdict": verdict}]
    (folder / "report_data.json").write_text(json.dumps(
        {"kind": kind, "title": title, "default_tier": "amateur_tmp", "tiers": tiers}, ensure_ascii=False),
        encoding="utf-8")
    if html:
        (folder / "report.html").write_text("<p>页面</p>", encoding="utf-8")
        os.utime(folder / "report.html", (mtime, mtime))
    return folder


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "output"
    _report(root, "玩家甲_111", "汇总", "summary", "玩家甲 · 多场汇总", "最该练的是急停。", 1_700_000_300)
    _report(root, "玩家甲_111", "match_a", "match", "玩家甲 · de_dust2", "这场没有检出明显多于业余的问题。", 1_700_000_100)
    _report(root, "玩家甲_111", "match_b", "match", "玩家甲 · de_mirage", None, 1_700_000_200)
    _report(root, "玩家乙_222", "match_c", "match", "玩家乙 · de_inferno", "最该练的是压枪差。", 1_700_000_050)
    # 不该出现的：没有 report.html、评测目录、历史版本、开发验证目录
    _report(root, "玩家乙_222", "no_html", "match", "不该出现", "x", 1_700_000_000, html=False)
    _report(root / "评测" / "20261018_x", "玩家丙_333", "m", "match", "评测里的", "x", 1_700_000_000)
    _report(root / "玩家甲_111" / "match_a", "历史版本", "old", "match", "旧版", "x", 1_700_000_000)
    _report(root / "_开发验证", "玩家丁_444", "m", "match", "开发验证", "x", 1_700_000_000)
    return root


def test_collect_reports_skips_eval_history_and_missing_html(tmp_path):
    entries = collect_reports(_fixture(tmp_path))
    assert [e.title for e in entries] == [
        "玩家甲 · 多场汇总", "玩家甲 · de_mirage", "玩家甲 · de_dust2", "玩家乙 · de_inferno"]
    first = entries[0]
    assert first.steamid == 111 and first.player_name == "玩家甲" and first.kind == "summary"
    assert first.href == "玩家甲_111/汇总/report.html"  # 相对目录页、用正斜杠
    assert first.verdict == "最该练的是急停。"
    assert entries[1].verdict == ""  # 没有分段数据时结论为空
    assert entries[2].updated < entries[1].updated  # 同一玩家内：汇总在前，单场按时间从新到旧


def test_players_ordered_by_latest_report(tmp_path):
    root = _fixture(tmp_path)
    _report(root, "玩家乙_222", "match_d", "match", "玩家乙 · de_nuke", "新的", 1_700_000_900)
    entries = collect_reports(root)
    assert entries[0].player_name == "玩家乙"  # 玩家乙有最新的报告，排在前面
    assert [e.title for e in entries[:2]] == ["玩家乙 · de_nuke", "玩家乙 · de_inferno"]


def test_render_index_is_offline_and_escaped(tmp_path):
    root = tmp_path / "output"
    _report(root, "玩家 &_111", "m", "match", "</script><b>标题</b>", "结论<i>x</i>", 1_700_000_000)
    html = render_index(collect_reports(root))
    assert "http://" not in html and "https://" not in html
    assert "<b>标题</b>" not in html and "&lt;b&gt;标题&lt;/b&gt;" in html
    assert "结论&lt;i&gt;x&lt;/i&gt;" in html
    assert 'href="%E7%8E%A9%E5%AE%B6%20%26_111/m/report.html"' in html  # href 按 URL 编码
    assert "玩家 &amp;" in html  # 玩家名做 HTML 转义
    assert "<title>" in html and "prefers-color-scheme" in html


def test_render_index_empty(tmp_path):
    html = render_index([])
    assert "还没有报告" in html


def test_write_index_writes_lf_file_at_root(tmp_path):
    root = _fixture(tmp_path)
    path = write_index(root)
    assert path == root / "index.html"
    data = path.read_bytes()
    assert b"\r\n" not in data
    text = data.decode("utf-8")
    assert text.count('class="report"') == 4
    assert "玩家甲 · 多场汇总" in text and "评测里的" not in text


def test_cli_analyze_and_summary_refresh_index(tmp_path, monkeypatch, capsys):
    """CLI 写完 report.html 后调用 write_index，并打印报告目录路径。"""
    from cs2coach import cli
    calls = []
    monkeypatch.setattr(cli, "write_index", lambda root: calls.append(root) or root / "index.html")
    cli._refresh_index(tmp_path)
    assert calls == [tmp_path]
    assert f"报告目录：{tmp_path / 'index.html'}" in capsys.readouterr().out
