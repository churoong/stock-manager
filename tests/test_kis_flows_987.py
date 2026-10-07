"""국내 수급 일별 수집 (docs/infra.md 25.987). 응답 모양은 2026-10-07 실측."""

from __future__ import annotations

import pytest

from batch.jobs import kis_flows as job
from batch.sources import kis

투자자 = {"rt_cd": "0", "output": [
    {"stck_bsop_date": "20261007", "frgn_ntby_qty": "-108414", "orgn_ntby_qty": "-758859", "prsn_ntby_qty": "892207",
     "frgn_ntby_tr_pbmn": "-23956", "orgn_ntby_tr_pbmn": "-203472", "prsn_ntby_tr_pbmn": "234406"},
    {"stck_bsop_date": "20261006", "frgn_ntby_qty": "", "orgn_ntby_qty": ""},  # 아직 안 나온 날
]}  # fmt: skip
공매도 = {"rt_cd": "0", "output1": {"stck_prpr": "268500"}, "output2": [
    {"stck_bsop_date": "20261007", "ssts_cntg_qty": "705995", "ssts_vol_rlim": "4.43"},
    {"stck_bsop_date": "20261006", "ssts_cntg_qty": "600000", "ssts_vol_rlim": "3.10"},
]}  # fmt: skip
신용 = {"rt_cd": "0", "output": [
    {"deal_date": "20261001", "stlm_date": "20261006", "whol_loan_rmnd_stcn": "22025431", "whol_loan_rmnd_rate": "0.36"},
]}  # fmt: skip


def test_응답을_날짜별_칸으로() -> None:
    inv = kis.parse_investor(투자자)
    assert inv == {"2026-10-07": {"frgn_net_qty": -108414, "orgn_net_qty": -758859, "prsn_net_qty": 892207,
                                  "frgn_net_amt": -23956, "orgn_net_amt": -203472, "prsn_net_amt": 234406}}
    assert kis.parse_short(공매도)["2026-10-07"] == {"short_qty": 705995, "short_vol_pct": 4.43}
    # 신용은 결제일이 아니라 매매일로 적는다
    assert kis.parse_credit(신용) == {"2026-10-01": {"credit_rmnd_qty": 22025431, "credit_rmnd_pct": 0.36}}


def test_실패_응답은_못_받음() -> None:
    with pytest.raises(kis.KisFailed):
        kis.parse_investor({"rt_cd": "1", "msg_cd": "EGW00201"})


def test_합치면_한_출처가_비어도_다른_칸은_남는다() -> None:
    rows = job.merge(kis.parse_investor(투자자), kis.parse_short(공매도), {})
    assert rows["2026-10-07"]["short_qty"] == 705995 and rows["2026-10-07"]["frgn_net_qty"] == -108414
    assert "credit_rmnd_pct" not in rows["2026-10-07"]


def test_받은_칸만_덮는다(tmp_path) -> None:
    """어제 받은 신용 칸을 오늘 신용 실패로 지우지 않는다 — 실제 SQLite 로 돌린다."""
    import sqlite3
    from pathlib import Path

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0046_kr_flows.sql").read_text())
    con.execute("INSERT INTO stocks (id) VALUES (1)")
    for sql, args in job.upserts(1, {"2026-10-01": {"credit_rmnd_pct": 0.36, "frgn_net_qty": 5}}, "t1"):
        con.execute(sql, args)
    for sql, args in job.upserts(1, {"2026-10-01": {"frgn_net_qty": 7}}, "t2"):
        con.execute(sql, args)
    assert con.execute("SELECT frgn_net_qty, credit_rmnd_pct, fetched_at FROM kr_flows").fetchone() == (7, 0.36, "t2")


def test_고정_질의의_칸_순서가_COLUMNS_와_같다() -> None:
    """질의를 손으로 적었다(동적 SQL 상한). 칸 순서가 어긋나면 값이 엉뚱한 칸에 들어간다."""
    head = job.UPSERT.split("(", 1)[1].split(")", 1)[0]
    assert [c.strip() for c in head.split(",")] == ["stock_id", "date", *job.COLUMNS, "source", "fetched_at"]
