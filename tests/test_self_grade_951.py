"""리포트 자기 채점 (docs/reports.md 3.4, docs/infra.md 25.951) — 지난 리포트 1부 종목이 그 뒤 어땠나를 머리에."""

from __future__ import annotations

from batch.services import report_picks as rp
from batch.services import self_grade as sg


def row(sid: int, d: str, r5: float | None, r20: float | None = None) -> dict:
    return {"stock_id": sid, "trade_date": d, "ret_5d": r5, "ret_20d": r20}


class Test채점:
    def test_같은_종목_같은_날은_한_건이고_창마다_센다(self) -> None:
        rows = [row(1, "2026-09-17", 0.02), row(1, "2026-09-17", 0.02), row(1, "2026-09-17", 0.02)]  # 기간별 셋 — 한 건
        rows += [row(2, "2026-09-17", -0.01), row(1, "2026-09-18", 0.01), row(3, "2026-09-18", 0.0), row(4, "2026-09-19", 0.03, 0.05)]
        g = sg.grade(rows)
        오 = g.windows[0]
        assert (오.window, 오.n) == (5, 5) and abs(오.avg_ret - 0.01) < 1e-9 and abs(오.win_rate - 0.6) < 1e-9  # 0 은 이긴 것이 아니다
        이십 = g.windows[1]
        assert (이십.n, 이십.avg_ret) == (1, None)  # 20일 창은 한 건 — 평균 안 냄
        assert (g.first_date, g.last_date) == ("2026-09-17", "2026-09-19")

    def test_창이_안_찬_건은_빠진다(self) -> None:
        g = sg.grade([row(i, "2026-10-01", None) for i in range(10)])
        assert g.windows[0].n == 0 and g.windows[0].avg_ret is None

    def test_빈_행(self) -> None:
        g = sg.grade([])
        assert g.first_date is None and sg.line(g) is None


class Test글:
    def test_한_줄(self) -> None:
        g = sg.SelfGrade((sg.WindowGrade(5, 31, 0.0081, 0.5416), sg.WindowGrade(20, 0, None, None)), "2026-09-17", "2026-09-26")
        assert sg.line(g) == "지난 추천 자기 채점: 5일 뒤 평균 +0.8% · 이긴 54% (31건) · 20일 뒤 아직 셀 수 없음 (0건) — 09-17~09-26 리포트"
        # 같은 날 하나면 날짜를 한 번만 (10-06 리포트 "10-01~10-01")
        assert sg.line(sg.SelfGrade((sg.WindowGrade(5, 0, None, None),), "2026-10-01", "2026-10-01")).endswith("— 10-01 리포트")
        # 반올림해 0 이면 "-0.0%" 가 아니다
        assert "+0.0%" in sg.line(sg.SelfGrade((sg.WindowGrade(5, 5, -0.0003, 0.4),), "2026-09-17", "2026-09-17"))

    def test_compose_가_받으면_머리에_붙는다(self) -> None:
        from tests.test_report_picks import row as srow

        글 = "지난 추천 자기 채점: 5일 뒤 평균 +0.8% · 이긴 54% (31건)"
        composed = rp.compose([srow(1)], 0.0, "2026-10-02", self_grade_line=글)
        머리 = composed.text.split("\n\n")[0]
        assert 글 in 머리 and 머리.startswith("추천 (신호 기준일 2026-10-02")
        assert "자기 채점" not in rp.compose([srow(1)], 0.0, "2026-10-02").text
