"""021 评测脚本：清单解析、单个 case 失败不中断、汇总列、输出位置与确认；不调用真实模型。"""

import json

import pytest

from scripts import eval_batch as eb


def write_set(tmp_path, body):
    path = tmp_path / "eval_set.toml"
    path.write_text(body, encoding="utf-8")
    return path


SET = '''
[[case]]
name = "单场 A"
kind = "analyze"
demo = "a.zip"
player = 76561190000000000

[[case]]
name = "多场 B"
kind = "summary"
player = 76561190000000001
'''


def fake_runner(calls):
    """模拟 CLI：单场写出报告和审计；多场失败并留下 submission_failed.json。"""
    def run(argv):
        calls.append(argv)
        out = eb.Path(argv[argv.index("--out") + 1]) / "玩家_1" / "比赛"
        out.mkdir(parents=True)
        if argv[0] == "analyze":
            (out / "report.md").write_text("# 报告\n", encoding="utf-8")
            (out / "submission.json").write_text(json.dumps({
                "violations_history": [[{"code": "raw_number"}, {"code": "unknown_slot"}], []],
                "dropped": [{"action": "replaced"}],
                "run": {"provider": "local", "model": "m", "tool_calls": 3, "repairs": 1,
                        "input_tokens": 100, "cache_read_tokens": 40, "output_tokens": 20}}), encoding="utf-8")
            return 0
        (out / "submission_failed.json").write_text(json.dumps({
            "error": "删除违规结论后没有任何 finding", "violations_history": [[{"code": "raw_number"}]] * 3}),
            encoding="utf-8")
        return 1
    return run


def test_load_cases_validates(tmp_path):
    assert [c["name"] for c in eb.load_cases(write_set(tmp_path, SET))] == ["单场 A", "多场 B"]
    with pytest.raises(eb.EvalSetError):
        eb.load_cases(write_set(tmp_path, '[[case]]\nname = "x"\nkind = "analyze"\nplayer = 1\n'))  # 缺 demo
    with pytest.raises(eb.EvalSetError):
        eb.load_cases(tmp_path / "missing.toml")


def test_argv_matches_cli():
    case = {"name": "x", "kind": "analyze", "demo": "a.zip", "player": 7, "demo_member": "m.dem"}
    assert eb.build_argv(case, eb.Path("out"), "local", None) == [
        "analyze", "a.zip", "--demo-member", "m.dem", "--player", "7", "--ai", "--out", "out", "--provider", "local"]
    assert eb.build_argv({"name": "y", "kind": "summary", "player": 7}, eb.Path("o"), None, "deepseek-v4-pro")[-2:] == \
        ["--model", "deepseek-v4-pro"]


def test_batch_continues_after_failure_and_summarises(tmp_path):
    calls = []
    cases = eb.load_cases(write_set(tmp_path, SET))
    ticks = iter([0.0, 2.5, 10.0, 11.0])
    rows = eb.run_batch(cases, tmp_path / "run", None, None, fake_runner(calls), clock=lambda: next(ticks))
    assert len(calls) == 2  # 第二个失败也跑到了
    ok, bad = rows
    assert ok["status"] == "成功" and ok["model"] == "local / m" and ok["rounds"] == "2→0"
    assert ok["codes"] == "raw_number 1、unknown_slot 1" and (ok["removed"], ok["replaced"]) == (0, 1)
    assert ok["tool_calls"] == 3 and ok["tokens"] == "100（40）/ 20" and ok["seconds"] == 2.5
    assert ok["report"].endswith("report.md")
    assert bad["status"].startswith("失败：删除违规结论后没有任何 finding") and bad["rounds"] == "1→1→1"
    assert bad["report"].endswith("submission_failed.json")
    text = eb.render_summary(rows, "测试", "20261016-000000")
    assert "| 单场 A | analyze | 成功 |" in text and "人工核对：错误数" in text


def test_runner_exception_is_recorded(tmp_path):
    cases = eb.load_cases(write_set(tmp_path, SET))[:1]
    def boom(argv):
        raise RuntimeError("网络中断")
    row = eb.run_batch(cases, tmp_path / "run", None, None, boom)[0]
    assert row["status"] == "失败：RuntimeError: 网络中断"


def test_main_requires_confirmation_and_writes_under_eval_root(tmp_path):
    path = write_set(tmp_path, SET)
    calls = []
    assert eb.main(["--set", str(path), "--out", str(tmp_path / "评测")], fake_runner(calls), ask=lambda _: "n") == 1
    assert calls == []  # 没确认就不跑
    code = eb.main(["--set", str(path), "--out", str(tmp_path / "评测"), "--label", "试 跑", "--yes"], fake_runner(calls))
    assert code == 2  # 有一个 case 失败
    [run_dir] = list((tmp_path / "评测").iterdir())
    assert run_dir.name.endswith("_试_跑") and (run_dir / "summary.md").is_file()
    assert all(eb.Path(c[c.index("--out") + 1]).is_relative_to(run_dir) for c in calls)  # 不写正式报告目录


def test_failure_reason_stays_on_one_line(tmp_path):
    case = {"name": "x", "kind": "analyze", "demo": "a.zip", "player": 1}
    out = tmp_path / "x" / "p" / "m"
    out.mkdir(parents=True)
    (out / "submission_failed.json").write_text(json.dumps({
        "error": "删除违规结论后仍未通过检查：提交未通过数据检查：\ntraining_plan[2]：\n- [unknown_slot]",
        "violations_history": [[]], "run": {"provider": "local", "model": "m"}}), encoding="utf-8")
    row = eb.case_row(case, tmp_path / "x", 1, 1.0)
    assert row["status"] == "失败：删除违规结论后仍未通过检查：提交未通过数据检查"
    assert row["model"] == "local / m" and row["tokens"] == "-"
    table = [line for line in eb.render_summary([row], "t", "0").splitlines() if line.startswith("|")]
    assert len(table) == 3  # 表头、分隔行、一行数据：失败原因没有把表格撑开
