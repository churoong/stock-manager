"""관문 R 대비 — 원가·묵은 평가 비중이 크면 믿기 어렵다 (docs/desk.md 6장, docs/infra.md 25.1709, H·R 감사 [권고] 3)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from batch.services import desk


def _날(n: int) -> list[str]:
    return [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


def test_valuation_gaps_by_hand() -> None:
    """평가 1,000 가운데 원가 평가 30(멈춤) + 마지막 종가 10일 전 40 = 7%. 이레 전 종가·0 이하 줄은 안 센다"""
    줄 = [{"value_krw": 30, "unknown_why": "기업행위(멈춤)"}, {"value_krw": 40, "price_date": "2026-10-05"},
          {"value_krw": 500, "price_date": "2026-10-15"}, {"value_krw": 20, "price_date": "2026-10-08"},
          {"value_krw": 0, "unknown_why": "종가 없음"}]  # fmt: skip
    rows = [{"account_id": "H", "as_of": "2026-10-15", "equity_krw": 1000, "positions_json": json.dumps(줄)},
            {"account_id": "H", "as_of": "2026-10-16", "equity_krw": 0, "positions_json": "[]"}]  # fmt: skip
    assert desk.valuation_gaps(rows) == {"H": [("2026-10-15", pytest.approx(7.0))]}


def test_gate_vs_r_unreliable_when_gap_over_limit() -> None:
    """R 대비가 + 여도 R 쪽 원가·묵은 평가 평균이 5% 를 넘으면 불통 — 다른 칸은 그대로"""
    날 = _날(130)
    h = [(d, 1 + 0.001 * i) for i, d in enumerate(날)]
    r = [(d, 1.0) for d in 날]
    def 관문(r_gap: float) -> dict:  # noqa: E306
        v = {"H": [(d, 3.0) for d in 날], "R1": [(d, r_gap) for d in 날], "R2": [(d, r_gap) for d in 날]}
        g = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis=None, valuation=v)
        return {i["key"]: i for i in g["items"]}["vs_r"]

    assert 관문(4.0)["ok"] and 관문(4.0)["valuation_gap"] == {"h_pct": pytest.approx(3.0), "r_pct": pytest.approx(4.0),
                                                           "limit": 5.0, "over": False}  # fmt: skip
    칸 = 관문(8.0)
    assert not 칸["ok"] and 칸["now"] > 0 and 칸["valuation_gap"]["over"]
    # 계열을 안 주면(못 읽음) 판정은 예전대로
    g = desk.gate({"H": h, "R1": r, "R2": r}, "H", recon_bad_days=0, batch_failed=0, kis=None)
    assert {i["key"]: i for i in g["items"]}["vs_r"]["ok"]


def test_job_reads_valuation(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from batch.jobs import desk as job
    from tests.test_paper_job_1445 import Client

    c = Client(accounts=None)
    monkeypatch.setattr(job.bmj, "MIRROR_ACCOUNT", "B")
    멈춤 = json.dumps([{"value_krw": 100, "unknown_why": "기업행위(멈춤)"}])
    for aid in ("B", "R1", "R2"):
        for i, as_of in enumerate(("2026-10-14", "2026-10-15", "2026-10-16")):
            c.conn.execute("INSERT INTO paper_daily (account_id, as_of, invested_krw, cash_krw, holdings_krw, equity_krw,"
                           " pnl_krw, twr_index, positions_json, recon_ok, updated_at) VALUES (?, ?, 1000, 0, 1000, 1000, 0,"
                           " ?, ?, 1, 't')", [aid, as_of, 1 + 0.01 * i, 멈춤 if aid == "R1" else "[]"])  # fmt: skip
    job.write(c, "KR", datetime(2026, 10, 16, 23, 40, tzinfo=UTC), trade_date="2026-10-16", session_date="2026-10-19",
              step_log={}, warnings=[])  # fmt: skip
    s = json.loads(c.q("SELECT summary_json FROM paper_desk")[0][0])
    v = {i["key"]: i for i in s["gate"]["items"]}["vs_r"]["valuation_gap"]
    assert v["h_pct"] == pytest.approx(0.0) and v["r_pct"] == pytest.approx(5.0) and not v["over"]
