"""국내 중기 신호 — 사업보고서 뒤에 나온 분기 영업이익 참고 행 (docs/infra.md 25.1352, 22회차 P3)."""

from __future__ import annotations

import json

from batch.jobs import signals as job
from batch.services import signals as sg
from tests.test_signals import mid_input

A = "11011"


def _r(fy: int, code: str, op: float, date: str, cons: int = 1, ccy: str = "KRW", days: int | None = None) -> dict:
    return {"stock_id": 1, "fiscal_year": fy, "report_code": code, "consolidated": cons, "report_date": date,
            "operating_income": op, "currency": ccy, "period_days": days}  # fmt: skip


기본 = [
    _r(2025, A, 1000e8, "2026-03-15"),
    _r(2025, "11012", 300e8, "2025-08-14"),  # 전년 같은 분기(반기)
    _r(2026, "11013", 200e8, "2026-05-15"),
    _r(2026, "11012", 250e8, "2026-08-14"),  # 사업보고서 뒤 가장 새 분기
]


def _nq(rows: list[dict], as_of: str = "2026-10-14", **kw) -> dict | None:
    return sg.newer_quarter(rows, fiscal_year=kw.get("fy", 2025), consolidated=kw.get("cons", True),
                            annual_code=A, as_of=as_of)  # fmt: skip


def test_사업보고서_뒤_가장_새_분기와_전년_같은_분기() -> None:
    nq = _nq(기본)
    assert (nq["fy"], nq["q"], nq["op"], nq["prev_op"], nq["lower"]) == (2026, 2, 250e8, 300e8, True)
    행 = sg.newer_quarter_criterion(nq, True)
    assert 행["passed"] is None  # 참고 행 — 판정에 쓰지 않는다
    assert 행["display"] == "2026 2분기 250.0억원 — 전년 같은 분기 300.0억원보다 작음"
    assert "SUE 와 별개" in 행["threshold"] and 행["as_of"] == "2026-08-14"


def test_기준일_뒤_접수는_보지_않는다() -> None:
    # 9월 기준이면 반기(8-14)까지, 5월 1일 기준이면 1분기(5-15)도 아직 없다
    assert _nq(기본, "2026-09-01")["q"] == 2
    assert _nq(기본, "2026-05-01") is None


def test_기준_통화_짧은해가_다르면_없다() -> None:
    # 별도 행만 있으면 연결 사업보고서와 견주지 않는다
    별도 = [기본[0], *[{**r, "consolidated": 0} for r in 기본[1:]]]
    assert _nq(별도) is None
    # 전년 같은 분기의 통화가 다르면 견주지 않는다
    assert _nq([기본[0], {**기본[1], "currency": "USD"}, *기본[2:]]) is None
    # 사업보고서가 짧은 사업연도면 없다
    assert _nq([{**기본[0], "period_days": 200}, *기본[1:]]) is None
    # 전년 같은 분기가 없으면 없다
    assert _nq([기본[0], *기본[2:]]) is None


def test_사업보고서_앞_분기는_뒤가_아니다() -> None:
    # 2025 사업보고서보다 먼저 접수된 2025 분기만 있으면 없다
    assert _nq([기본[0], 기본[1], _r(2024, "11012", 1.0, "2024-08-14")]) is None


def test_정정은_기준일까지_가장_늦은_것() -> None:
    정정 = [*기본, _r(2026, "11012", 350e8, "2026-09-30")]
    assert _nq(정정)["lower"] is False
    assert _nq(정정, "2026-09-01")["lower"] is True


class _Client:
    def __init__(self, rows: list[dict]) -> None:
        self.rows, self.calls = rows, []

    def execute(self, sql: str, params: list):  # noqa: ANN201
        self.calls.append((sql, params))
        rows = self.rows

        class R:
            def dicts(self):  # noqa: ANN202
                return rows

        return R()


def test_작업은_중기_신호에_참고_행을_붙이고_센다() -> None:
    inp = mid_input()
    신호 = sg.evaluate(inp, max_weight_per_stock=10.0, total_investable=1e8, min_order_amount=0, targets=None,
                     fx=None)  # fmt: skip
    mid = next(s for s in 신호 if s.horizon == "mid")
    행들 = [job.to_row(mid, "2026-10-14", "2026-10-14T00:00:00")]
    판정 = [c.get("passed") for c in mid.rationale_data["criteria"]]
    client = _Client(기본)
    기록 = job.add_newer_quarter_rows(client, [(0, mid, inp)], 행들, "2026-10-14", "2026-10-14T00:00:00")
    assert 기록 == {"signals": 1, "with_quarter": 1, "lower": 1}
    assert len(client.calls) == 1 and json.loads(client.calls[0][1][0]) == [1]  # 한 질의, 신호 낸 종목만
    붙은 = json.dumps(행들[0], ensure_ascii=False, default=str)
    assert sg.NEWER_QUARTER_LABEL in 붙은
    # 판정 행은 그대로 — 참고 행 하나만 늘었다
    assert [c.get("passed") for c in mid.rationale_data["criteria"]] == [*판정, None]
    assert job.add_newer_quarter_rows(client, [], 행들, "2026-10-14", "x") is None
