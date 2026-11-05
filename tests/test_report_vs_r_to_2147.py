"""25.2147 — 리포트 순위 줄의 R 대비가 대조 R 기록이 멈춘 날까지 잰 값이면 그 끝을 적는다(25.2145 `vs_r_to`)."""

from __future__ import annotations

from batch.services import desk
from tests.test_desk_report_1510 import _칸


def test_순위_줄은_R_대비를_잰_끝을_적는다() -> None:
    board = {"window_start": "2026-10-16", "rows": [
        {"id": "A", "window_pct": 2.0, "vs_r_pp": 1.5, "vs_r_to": "2026-10-30"},
        {"id": "H", "window_pct": 1.0, "vs_r_pp": 0.5, "vs_r_to": None},
    ]}  # fmt: skip
    글 = "\n".join(desk.report_lines(_칸(), board, "H"))
    assert "(R 대비 +1.50%p, 2026-10-30까지)" in 글
    assert "(R 대비 +0.50%p)" in 글  # 끝이 없으면 예전 그대로
