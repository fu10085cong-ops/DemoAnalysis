"""复盘包的观察时段与程序一致：提前截止（阵亡、又一次交战）时只看到截止时刻；截图在这个时段里均分。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from research_stance import focus_fields  # noqa: E402
from research_vision import info_text, offsets  # noqa: E402


def test_focus_stops_at_program_cutoff():
    cut = focus_fields({"engagement_end_s": 0.2, "observed_s": 1.234, "stop_reason": "new_fight"}, 3.0)
    assert cut["focus"] == [0.2, 1.434] and cut["stop_reason"] == "new_fight"
    assert "又一次交战" in cut["focus_note"] and "之后的不算" in cut["focus_note"]
    full = focus_fields({"engagement_end_s": 0.0, "observed_s": 3.0, "stop_reason": "observe"}, 3.0)
    assert full["focus"] == [0.0, 3.0] and "截止" not in full["focus_note"]
    missing = focus_fields({"engagement_end_s": 0.5, "observed_s": float("nan"), "stop_reason": None}, 3.0)
    assert missing["focus"] == [0.5, 3.5] and missing["stop_reason"] == "observe"


def test_frames_and_ai_window_follow_cutoff():
    item = {"focus": [0.2, 1.4], "match_id": "m", "round_id": 1, "start_place": "Truck", "round_time_s": 40}
    assert offsets(item) == [-1.0, 0.0, 0.3, 0.6, 0.9, 1.2]
    assert info_text(item)[1] == "+0.2 到 +1.4 秒"
