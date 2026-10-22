"""KIS모의투자 경로 감사 반영 — 잔고 잘림·정리 대상·잔고 합계 (docs/paper.md 15장, docs/infra.md 25.1712)."""

from __future__ import annotations

import json

import pytest

from batch.jobs import broker_mirror as job
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import (
    _at,
    _Sess,
    _세션,
    c,  # noqa: F401 — 픽스처
)
from tests.test_paper_job_1445 import Client


def _쪽(n0: int, n: int, cont: str) -> tuple[dict, str]:
    rows = [{"pdno": f"{900000 + i:06d}", "hldg_qty": "1", "ord_psbl_qty": "1", "pchs_avg_pric": "1000"}
            for i in range(n0, n0 + n)]  # fmt: skip
    return {"rt_cd": "0", "output1": rows, "output2": [{"dnca_tot_amt": "1", "tot_evlu_amt": "1"}],
            "ctx_area_fk100": "f", "ctx_area_nk100": "n"}, cont  # fmt: skip


def test_잔고가_쪽_상한에_닿으면_잘림을_표시한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """감사 R1 — 11쪽 모두 "다음 있음" 이면 10쪽에서 멈추고 잘림. 끝 쪽("D")까지 받으면 잘림 아님"""
    s, _h = _세션(monkeypatch, [_쪽(i * 20, 20, "M") for i in range(11)])
    보유, _합 = s.balance()
    assert len(보유) == 200 and s.balance_truncated
    s2, _h2 = _세션(monkeypatch, [_쪽(0, 20, "M"), _쪽(20, 3, "D")])
    보유2, _ = s2.balance()
    assert len(보유2) == 23 and not s2.balance_truncated


def test_잘린_잔고면_그날_주문을_내지_않고_기준점에서_뺀다(c: Client) -> None:  # noqa: F811
    """감사 R1b — 잘린 잔고에 H 가 파는 종목이 빠지면 "팔 수량 없음" 으로 매도를 버렸고, 그날 줄이 실현손익 기준점이
    되어 맞는 값을 모름으로 돌렸다"""
    s = _Sess(holdings=[])
    s.balance_truncated = True
    기록 = job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert s.orders == [] and 기록["balance_truncated"] and 기록["send"].startswith("증권사 잔고 조회가 쪽 상한")
    줄 = c.q("SELECT as_of, fetched_at, holdings_json, summary_json FROM paper_broker_daily")
    assert json.loads(줄[0][3])["balance_truncated"] is True
    cols = ["as_of", "fetched_at", "holdings_json", "summary_json"]
    assert job._개장전_잔고([dict(zip(cols, 줄[0], strict=True))], "KR") == []


def test_안_찬_매수만_있던_종목은_정리하지_않는다(c: Client) -> None:  # noqa: F811
    """감사 R2 — 베낀 매수가 안 찬(unfilled) 종목을 사람이 손으로 사면 정리가 그 주식을 팔았다. 찼거나 아직 맞추지 않은
    (`unknown` — 실제로 찼을 수 있음) 매수만 "산 적 있는 종목" 이다"""
    x = c.conn.execute
    x("UPDATE paper_orders SET status = 'filled'")  # 오늘 H 주문 없음
    x("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side, order_type,"
      " limit_price, quantity, trade_date, reason, status, filled_qty, source, created_at) VALUES ('kis_vts', 101,"
      " 'B', 'KR', 1, '005930', 'buy', 'limit', 9800, 3, '2026-10-09', '베낌', 'unfilled', 0, 't', 't')")  # fmt: skip
    s = _Sess(holdings=[kv.Holding("005930", 5, 5, 9_700.0)])
    job.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert s.orders == []
    # 같은 매수가 `unknown` 으로 닫혔다면(실제로 찼을 수 있다) 예전처럼 정리 대상
    x("UPDATE paper_broker_orders SET status = 'unknown' WHERE paper_order_id = 101")
    x("DELETE FROM paper_broker_daily")
    s2 = _Sess(holdings=[kv.Holding("005930", 5, 5, 9_700.0)])
    job.run(c, "KR", _at("08:41"), session=s2, sleep=lambda _x: None, clock=lambda: _at("08:41"))
    assert s2.orders == [("sell", "005930", 5, None)]


def test_잔고_합계는_값이_있는_마지막_쪽(monkeypatch: pytest.MonkeyPatch) -> None:
    """감사 [참고] 4 — 첫 쪽 합계가 비고 끝 쪽에만 오면 예전엔 합계가 모두 모름"""
    첫 = {"rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "1", "ord_psbl_qty": "1"}], "output2": [],
          "ctx_area_fk100": "f", "ctx_area_nk100": "n"}  # fmt: skip
    끝 = {"rt_cd": "0", "output1": [{"pdno": "000660", "hldg_qty": "1", "ord_psbl_qty": "1"}],
          "output2": [{"dnca_tot_amt": "1000", "tot_evlu_amt": "5000"}]}  # fmt: skip
    s, _h = _세션(monkeypatch, [(첫, "M"), (끝, "D")])
    _보유, 합 = s.balance()
    assert 합["total"] == 5000
