"""건너뛴 추천 영수증 (docs/reports.md 3.3, docs/infra.md 25.950) — 몇 번째 추천이고 처음 봤을 때보다 얼마나 움직였나."""

from __future__ import annotations

from typing import Any

from batch.notify import report_sections as rs
from batch.services import pick_history as ph
from batch.services import report_picks as rp


def row(stock_id: int, trade_date: str, close: float | None = 70000.0) -> dict[str, Any]:
    return {"stock_id": stock_id, "trade_date": trade_date, "close": close}


class Test이력:
    def test_횟수_첫날_종가_연속(self) -> None:
        rows = [
            row(1, "2026-09-29", 70000), row(1, "2026-09-29", 70000),  # 같은 날 기간별 두 항목 — 한 날
            row(1, "2026-09-30", 71000), row(1, "2026-10-01", 72000),
            row(2, "2026-09-29", 500), row(2, "2026-10-01", 520),  # 09-30 에 빠졌다 — 연속은 1
            row(3, "2026-09-26", 10),  # 옛날 한 번
        ]
        h = ph.summarize(rows)
        assert h[1] == ph.PickHistory(times=3, first_date="2026-09-29", first_close=70000.0, streak=3)
        assert h[2].times == 2 and h[2].streak == 1
        assert h[3].times == 1 and h[3].streak == 0  # 가장 최근 리포트(10-01)에 없다

    def test_첫날_종가가_비면_뒤의_같은_날_값으로(self) -> None:
        h = ph.summarize([row(1, "2026-09-29", None), row(1, "2026-09-29", 70000)])
        assert h[1].first_close == 70000.0
        assert ph.summarize([row(1, "2026-09-29", None)])[1].first_close is None

    def test_빈_값은_건너뛴다(self) -> None:
        assert ph.summarize([{"stock_id": None, "trade_date": "2026-09-29"}, {"stock_id": 1, "trade_date": None}]) == {}


class Test글:
    def test_첫_추천과_n번째(self) -> None:
        원 = lambda v: rs._money(v, "KRW")  # noqa: E731
        assert ph.history_line(None, 72000, 원) == "첫 추천"
        h = ph.PickHistory(times=3, first_date="2026-09-29", first_close=70000.0, streak=3)
        assert ph.history_line(h, 72170, 원) == "4번째 추천 (연속 4일) — 첫 추천 09-29 종가 70,000원 대비 +3.1%"
        # 연속이 끊겼으면 연속을 적지 않는다. 오늘 종가가 없으면 대비를 적지 않는다
        assert ph.history_line(ph.PickHistory(2, "2026-09-29", 70000.0, 0), None, 원) == "3번째 추천 — 첫 추천 09-29 종가 70,000원"
        # payload(dict) 도 같은 말. 첫 종가가 없으면 날짜까지만
        assert ph.history_line({"times": 1, "first_date": "2026-09-29", "first_close": None, "streak": 1}, 1.0, 원) == (
            "2번째 추천 (연속 2일) — 첫 추천 09-29"
        )

    def test_compose_가_이력을_받으면_종목_줄에_붙고_payload_에_남는다(self) -> None:
        from tests.test_report_picks import row as srow

        rows = [srow(1, close=72170.0), srow(2, close=1000.0)]
        hist = {1: ph.PickHistory(times=3, first_date="2026-09-29", first_close=70000.0, streak=3)}
        composed = rp.compose(rows, 0.0, "2026-10-02", pick_history=hist)
        assert "  4번째 추천 (연속 4일) — 첫 추천 09-29 종가 70,000원 대비 +3.1%" in composed.text
        assert "  첫 추천" in composed.text  # 2 번 종목은 지난 리포트에 없었다
        하나 = next(p for p in composed.picks if p.ticker == "000001")
        assert 하나.history == {"times": 3, "first_date": "2026-09-29", "first_close": 70000.0, "streak": 3}
        둘 = next(p for p in composed.picks if p.ticker == "000002")
        assert 둘.history == {"times": 0, "first_date": None, "first_close": None, "streak": 0}
        # 이력을 안 넘기면(옛 호출부·읽기 실패) 아무 줄도 없다 — "첫 추천" 이라 지어내지 않는다
        전 = rp.compose(rows, 0.0, "2026-10-02")
        assert "번째 추천" not in 전.text and "첫 추천" not in 전.text and all(p.history is None for p in 전.picks)


class Test분할_띠:
    """25.963 — 첫 추천과 오늘 사이에 분할·병합이 끼면(수정 전 종가끼리) 대비를 적지 않는다."""

    def test_띠_밖이면_대비를_적지_않는다(self) -> None:
        원 = lambda v: f"{v:,.0f}원"  # noqa: E731
        h = ph.PickHistory(3, "2026-09-29", 70000.0, 3)
        # 1:5 액면분할 — 수정 전 종가 70,000 → 14,280 이면 "대비 −79.6%" 가 찍혔다
        assert ph.history_line(h, 14280, 원).endswith("종가 70,000원 (가격 단위가 바뀐 듯해 대비는 적지 않음 — 분할·병합)")
        assert "대비 +" not in ph.history_line(h, 350000, 원)  # 5:1 병합
        # 띠 끝은 적는다
        assert ph.history_line(h, 35000, 원).endswith("대비 -50.0%")
        assert ph.history_line(h, 140000, 원).endswith("대비 +100.0%")
