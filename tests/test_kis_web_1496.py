"""KIS모의투자 웹 보기 — 배치가 적는 보유·계좌·성과 (docs/paper.md 15.7, docs/infra.md 25.1496)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from batch.sources import kis_vts as kv
from tests.test_kis_vts_1475 import _at, _Sess, c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_perf_by_hand() -> None:
    """총액 100 → 110 → 99 → 120: 수익률 +20%, 손익 +20, MDD = 99 ÷ 110 − 1 = −10%. 총액 없는 날은 뺀다.
    H 는 (지수, 손익) — 1.0·50 → 1.05·62 면 H 손익 +12, H +5%"""
    p = bm.perf([("2026-10-19", 100.0), ("2026-10-20", 110.0), ("2026-10-21", None), ("2026-10-22", 99.0),
                 ("2026-10-23", 120.0)], (1.0, 50.0), (1.05, 62.0))  # fmt: skip
    assert p is not None
    assert (p["start"], p["start_total"], p["last"], p["last_total"], p["days"]) == ("2026-10-19", 100.0, "2026-10-23",
                                                                                   120.0, 4)  # fmt: skip
    assert p["return_pct"] == pytest.approx(20.0) and p["mdd_pct"] == pytest.approx(-10.0)
    assert p["pnl_krw"] == pytest.approx(20.0) and p["h_pnl_krw"] == pytest.approx(12.0)
    assert p["h_return_pct"] == pytest.approx(5.0)
    assert bm.perf([("2026-10-19", None)]) is None
    q = bm.perf([("2026-10-19", 100.0)])
    assert q is not None and q["h_return_pct"] is None and q["h_pnl_krw"] is None


def test_us_totals_from_present_balance() -> None:
    """체결기준현재잔고(외화) → 보유 평가 합·달러 예수금·KIS 고시환율. 평가 없는 보유가 있으면 합은 모름 (25.1497)."""
    p = {"output1": [{"pdno": "AAPL", "cblc_qty13": "3", "frcr_evlu_amt2": "630.5"}, {"pdno": "X", "cblc_qty13": "0"}],
         "output2": [{"crcy_cd": "JPY", "frst_bltn_exrt": "8.5"}, {"crcy_cd": "USD", "frcr_dncl_amt_2": "0.000000",
                                                                  "frst_bltn_exrt": "1348.50000000"}]}  # fmt: skip
    assert {k: kv.us_totals(p)[k] for k in ("value_usd", "cash_usd", "usd_krw")} == {"value_usd": 630.5, "cash_usd": 0.0,
                                                                                    "usd_krw": 1348.5}  # fmt: skip
    p["output1"].append({"pdno": "IBM", "cblc_qty13": "1"})
    assert kv.us_totals(p)["value_usd"] is None


class _가격세션(_Sess):
    """`us_assets` = KIS 해외 총자산 원화(`output3.tot_asst_amt`). 보유 줄은 모의처럼 수량 0 (25.1527)"""

    def __init__(self, total: float, us_assets: str = "100000", **kw: Any) -> None:
        super().__init__(**kw)
        self.total, self.us_assets = total, us_assets

    def balance(self) -> tuple[list[kv.Holding], dict[str, float | None]]:
        return self.holdings, {"cash": 900_000.0, "total": self.total, "holdings_value": self.total - 899_000.0,
                               "cash_d2": 899_000.0}  # fmt: skip

    def present_us(self, won: bool = False) -> dict:
        return {"output1": [{"pdno": "AAPL", "cblc_qty13": "0.00000000", "frcr_evlu_amt2": "0.000000"}],
                "output2": [{"crcy_cd": "USD", "frcr_dncl_amt_2": "0", "frst_bltn_exrt": "1000"}],
                "output3": {"tot_asst_amt": self.us_assets}}


def test_job_stores_holdings_account_and_account_level_perf(c: Client) -> None:  # noqa: F811
    """국내 배치가 계좌 전체 원화 총액(국내 총평가 + KIS 해외 총자산 원화)으로 성과를 낸다 (25.1497·25.1527)."""
    c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw, pnl_krw,"
                   " twr_index, positions_json, recon_ok, updated_at) VALUES"
                   " ('B', '2026-10-15', 1, 1, 0, 1, 1000, 1.0, '[]', 1, 't'), ('B', '2026-10-19', 1, 1, 0, 1, 31000, 1.02, '[]', 1, 't')")  # fmt: skip
    보유 = [kv.Holding("005930", 3, 3, 9_800.0, 10_000.0, 30_000.0, 600.0)]
    job.run(c, "KR", _at("08:40"), session=_가격세션(1_000_000.0, holdings=보유), sleep=lambda _x: None,
            clock=lambda: _at("08:40"))  # fmt: skip
    job.run(c, "KR", _at("08:40", "2026-10-19"), session=_가격세션(1_050_000.0, "110000", holdings=보유),
            sleep=lambda _x: None, clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    줄 = c.q("SELECT as_of, holdings_json, summary_json, total_krw FROM paper_broker_daily WHERE broker = 'kis_vts'"
             " ORDER BY as_of")
    assert [r[0] for r in 줄] == ["2026-10-16", "2026-10-19"] and 줄[1][3] == 1_050_000.0
    assert json.loads(줄[1][1]) == [{"id": 1, "code": "005930", "qty": 3, "avg": 9_800.0, "price": 10_000.0,
                                    "value": 30_000.0, "pnl": 600.0}]  # fmt: skip
    s = json.loads(줄[1][2])
    # 국내 칸 현금은 총평가와 같은 D+2
    assert s["account"] == {"currency": "KRW", "cash": 899_000.0, "holdings_value": 151_000.0, "total": 1_050_000.0}
    # 계좌 전체 원화 = 국내 1,050,000 + 해외 총자산 110,000 = 1,160,000 · 첫날 1,000,000 + 100,000 = 1,100,000.
    # 체결기준현재잔고 보유 줄(수량 0)은 총액에 쓰지 않는다(25.1527)
    assert s["account_krw"]["total_krw"] == pytest.approx(1_160_000.0) and s["account_krw"]["basis"] == job.TOTAL_BASIS
    assert s["perf"]["start"] == "2026-10-16" and s["perf"]["pnl_krw"] == pytest.approx(60_000.0)
    # 같은 기간 H(이 시험은 B 를 베낀다) — 10-16 이하 마지막 (1.0, 1000) → 10-19 (1.02, 31000)
    assert s["perf"]["h_pnl_krw"] == pytest.approx(30_000.0) and s["perf"]["h_return_pct"] == pytest.approx(2.0)
    assert "n" in s and "match_pct" in s  # 15.3 비교 요약은 그대로 같은 칸에


def test_us_row_has_no_perf_and_us_query_failure_means_unknown(c: Client) -> None:  # noqa: F811
    """미국 배치 줄은 성과를 내지 않는다(시장별 총액 비는 수익률이 아니다). 국내 배치에서 미국 조회가 실패하면 그날 총액은 모름."""
    from batch.sources.kis import KisFailed

    class _미국실패(_가격세션):
        def present_us(self, won: bool = False) -> dict:
            raise KisFailed("모의 미국 체결기준현재잔고 실패 EGW00201")

    job.run(c, "KR", _at("08:40"), session=_미국실패(1_000_000.0), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    s = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE broker = 'kis_vts'")[0][0])
    assert s["account_krw"]["total_krw"] is None and s["perf"] is None


def test_fx_zero_and_perf_failure_do_not_block(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """08:30 KST 에 KIS 고시환율이 0 이어도 총액은 원화 해외 총자산이라 환율이 필요 없다(25.1527).
    해외 총자산 칸이 없으면 그날 총액은 모름. 성과 계산이 깨져도 그날 베낀 주문은 나간다(검토 [권장] 5)"""

    class _환율0(_가격세션):
        def present_us(self, won: bool = False) -> dict:
            return super().present_us(won) | {"output2": [{"crcy_cd": "USD", "frcr_dncl_amt_2": "0", "frst_bltn_exrt": "0"}]}

    job.run(c, "KR", _at("08:40"), session=_환율0(1_000_000.0, "0"), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    s = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = '2026-10-16'")[0][0])
    assert s["account_krw"]["total_krw"] == pytest.approx(1_000_000.0)

    class _해외모름(_가격세션):
        def present_us(self, won: bool = False) -> dict:
            return super().present_us(won) | {"output3": {}}

    job.run(c, "KR", _at("08:40", "2026-10-19"), session=_해외모름(1_000_000.0), sleep=lambda _x: None,
            clock=lambda: _at("08:40", "2026-10-19"))  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = '2026-10-19'")[0][0])
    assert s["account_krw"]["total_krw"] is None

    # 성과 계산이 깨진 날 — 주문은 그대로 나간다
    monkeypatch.setattr(job, "TOTALS_SQL", "SELECT 깨진 질의")
    c.conn.execute("DELETE FROM paper_broker_orders")
    s2 = _가격세션(1_000_000.0)
    기록 = job.run(c, "KR", _at("08:40"), session=s2, sleep=lambda _x: None, clock=lambda: _at("08:40"))
    assert 기록["perf_error"] and 기록["sent"] == {"accepted": 2} and len(s2.orders) == 2


def test_old_basis_rows_are_not_linked(c: Client) -> None:  # noqa: F811
    """기준이 바뀐 줄(25.1497 식, `basis` 없음)은 계열에 잇지 않는다 — 이으면 해외 자산만큼 총액이 뛰어 가짜 수익이 남는다 (25.1527)"""
    c.conn.execute("INSERT INTO paper_broker_daily (broker, as_of, account_id, holdings_json, diff_json, source, fetched_at,"
                   " summary_json) VALUES ('kis_vts', '2026-10-15', 'B', '[]', '{}', 'kis_vts', 't', ?)",
                   [json.dumps({"account_krw": {"total_krw": 500_000.0}})])  # fmt: skip
    job.run(c, "KR", _at("08:40"), session=_가격세션(1_000_000.0), sleep=lambda _x: None, clock=lambda: _at("08:40"))
    s = json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = '2026-10-16'")[0][0])
    assert s["perf"]["start"] == "2026-10-16" and s["perf"]["pnl_krw"] == pytest.approx(0.0)
