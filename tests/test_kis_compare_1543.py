"""KIS모의투자 대 가상모의투자 H 견주기 — 같은 창·쉬는 해외 현금 환율 몫 뺌·해외 총자산 0 막이
(docs/paper.md 15.7, docs/desk.md 8장, docs/infra.md 25.1543). 손으로 셀 수 있는 값."""

from __future__ import annotations

import json
from typing import Any

import pytest

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from tests.test_kis_vts_1475 import _at, c  # noqa: F401 — 픽스처
from tests.test_kis_web_1496 import _가격세션
from tests.test_paper_job_1445 import Client


def test_환율_몫을_뺀_총액() -> None:
    # 해외 현금 102,000원(= 100 달러 × 1,020) — 시작 환율 1,000 에 묶으면 100,000 → 총액에서 2,000 빠진다
    assert bm.fx_neutral_total(1_102_000.0, 102_000.0, 1000.0, 1020.0) == pytest.approx(1_100_000.0)
    assert bm.fx_neutral_total(1_102_000.0, 102_000.0, 1000.0, 1000.0) == pytest.approx(1_102_000.0)
    for 빈 in ((None, 1.0, 1.0, 1.0), (1.0, None, 1.0, 1.0), (1.0, 1.0, None, 1.0), (1.0, 1.0, 1.0, 0.0)):
        assert bm.fx_neutral_total(*빈) is None


def test_같은_창_견주기_손계산() -> None:
    rows = {"2026-10-15": (1_100_000.0, 100_000.0, 1000.0), "2026-10-16": (1_105_000.0, 102_000.0, 1020.0)}
    h = bm.h_compare(rows, "2026-10-15", "2026-10-16", (1.0, 1000.0), (1.01, 4000.0))
    # 원래 +5,000, 그 가운데 쉬는 현금 환율 몫 +2,000 → 견줄 손익 +3,000, H +3,000
    assert h["kis_pnl_krw"] == pytest.approx(5_000.0) and h["fx_cash_krw"] == pytest.approx(2_000.0)
    assert h["kis_cmp_pnl_krw"] == pytest.approx(3_000.0) and h["h_pnl_krw"] == pytest.approx(3_000.0)
    assert h["h_return_pct"] == pytest.approx(1.0) and (h["h_from"], h["h_to"]) == ("2026-10-15", "2026-10-16")
    # 환율을 모르면 견줄 손익은 모름 — 원래 손익은 그대로
    빈 = bm.h_compare({**rows, "2026-10-16": (1_105_000.0, 102_000.0, None)}, "2026-10-15", "2026-10-16", None, None)
    assert 빈["kis_pnl_krw"] == pytest.approx(5_000.0) and 빈["kis_cmp_pnl_krw"] is None and 빈["h_pnl_krw"] is None


class _환세션(_가격세션):
    """해외 현금(`frcr_evlu_tota`)·고시환율을 정하는 세션 — 해외 총자산 = 해외 현금(보유 없음)"""

    def __init__(self, total: float, cash: str, fx: str, **kw: Any) -> None:
        super().__init__(total, cash, **kw)
        self.cash, self.fx = cash, fx

    def present_us(self, won: bool = False) -> dict:
        return {"output1": [], "output2": [{"crcy_cd": "USD", "frcr_dncl_amt_2": "0", "frst_bltn_exrt": self.fx}],
                "output3": {"tot_asst_amt": self.us_assets, "evlu_amt_smtl": "0", "frcr_evlu_tota": self.cash}}


def _돌림(c: Client, day: str, total: float, cash: str, fx: str) -> dict:  # noqa: F811
    job.run(c, "KR", _at("08:40", day), session=_환세션(total, cash, fx), sleep=lambda _x: None,
            clock=lambda: _at("08:40", day))  # fmt: skip
    return json.loads(c.q("SELECT summary_json FROM paper_broker_daily WHERE as_of = ?", [day])[0][0])


def test_작업은_같은_창으로_환율_몫을_빼고_견준다(c: Client) -> None:  # noqa: F811
    # H(이 시험은 B 를 베낀다) — 10-15 (1.0, 1000), 10-16 (1.01, 4000) · 10-16 밤 미국 배치가 끝났다
    for d, twr, pnl in (("2026-10-15", 1.0, 1000.0), ("2026-10-16", 1.01, 4000.0), ("2026-10-19", 1.05, 99_000.0)):
        c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                       " pnl_krw, twr_index, positions_json, recon_ok, updated_at)"
                       " VALUES ('B', ?, 1, 1, 0, 1, ?, ?, '[]', 1, 't')", [d, pnl, twr])  # fmt: skip
    c.conn.execute("INSERT INTO batch_runs (job_name, market, started_at, status) VALUES ('daily_us', 'US',"
                   " '2026-10-16T13:30:00+00:00', 'success')")  # fmt: skip
    _돌림(c, "2026-10-15", 1_000_000.0, "100000", "1000")
    _돌림(c, "2026-10-16", 1_003_000.0, "102000", "1020")
    s = _돌림(c, "2026-10-19", 1_010_000.0, "103000", "1030")
    p = s["perf"]
    # 성과(계좌 그대로)는 10-15 ~ 10-19 — 1,100,000 → 1,113,000
    assert p["start"] == "2026-10-15" and p["pnl_krw"] == pytest.approx(13_000.0)
    # 견주기는 10-15 ~ 10-16(오늘 아침 H 행은 미국이 한 장 늦다 — 끝에 쓰지 않는다): KIS 1,100,000 → 1,105,000,
    # 쉬는 현금 환율 몫 +2,000 → +3,000 · H +3,000 (10-19 행 99,000 은 쓰지 않는다)
    assert (p["h_from"], p["h_to"]) == ("2026-10-15", "2026-10-16")
    assert p["kis_pnl_krw"] == pytest.approx(5_000.0) and p["kis_cmp_pnl_krw"] == pytest.approx(3_000.0)
    assert p["fx_cash_krw"] == pytest.approx(2_000.0) and p["h_pnl_krw"] == pytest.approx(3_000.0)


def test_해외_자산이_있던_계좌에_0_이_오면_그날_총액은_모름(c: Client) -> None:  # noqa: F811
    앞 = _돌림(c, "2026-10-15", 1_000_000.0, "100000", "1000")
    assert 앞["account_krw"]["total_krw"] == pytest.approx(1_100_000.0)
    s = _돌림(c, "2026-10-16", 1_000_000.0, "0", "1000")
    assert s["account_krw"]["total_krw"] is None and s["account_krw"]["assets_zero"] is True
    assert s["perf"]["mdd_pct"] == pytest.approx(0.0)  # 가짜 −9% 가 없다
