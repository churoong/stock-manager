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


# --- 투자의견 · 기업행위 일정 (25.988) -------------------------------------------------------------------------------

def test_투자의견_목표가_0_은_없음() -> None:
    got = kis.parse_opinions({"rt_cd": "0", "output": [
        {"stck_bsop_date": "20260923", "invt_opnn": "BUY", "invt_opnn_cls_code": "2", "rgbf_invt_opnn_cls_code": "3",
         "mbcr_name": "유안타", "hts_goal_prc": "630000"},
        {"stck_bsop_date": "20260907", "invt_opnn": "중립", "invt_opnn_cls_code": "3", "mbcr_name": "미래에셋",
         "hts_goal_prc": "0"},
    ]})  # fmt: skip
    assert got[0] == {"date": "2026-09-23", "broker": "유안타", "opinion": "BUY", "opinion_code": 2,
                      "prev_opinion_code": 3, "target_price": 630000.0}
    assert got[1]["target_price"] is None


def test_기업행위_일정은_네_종류_모두_output1() -> None:
    split = kis.parse_events("split", {"rt_cd": "0", "output1": [
        {"record_date": "20260930", "sht_cd": "028080", "opp_cust_nm": "휴맥스홀딩스", "merge_type": "흡수합병"}]})
    assert split[0]["code"] == "028080" and split[0]["kind"] == "split" and split[0]["record_date"] == "2026-09-30"
    # 2026-10-07 실측: 배당·무상·유상도 output1 이다 (25.994)
    assert kis.parse_events("bonus", {"rt_cd": "0", "output": [{"record_date": "20261030", "sht_cd": "052400"}]}) == []
    bonus = kis.parse_events("bonus", {"rt_cd": "0", "output1": [
        {"record_date": "20261030", "sht_cd": "052400", "isin_name": "코나아이", "fix_rate": "50.00"},
        {"record_date": "20261030", "sht_cd": "", "isin_name": "코드없음"}]})
    assert [e["name"] for e in bonus] == ["코나아이"]


def test_새_표의_질의가_실제_스키마에서_돈다() -> None:
    import sqlite3
    from pathlib import Path

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE stocks (id INTEGER PRIMARY KEY)")
    con.executescript((Path(__file__).resolve().parent.parent / "migrations" / "0047_kr_opinions_events.sql").read_text())
    con.execute("INSERT INTO stocks (id) VALUES (1)")
    for _ in range(2):  # 두 번 넣어도 한 줄 — 겹치는 창으로 매일 받는다
        con.execute(job.OPINION_UPSERT, [1, "2026-09-23", "유안타", "BUY", 2, 3, 630000.0, "kis_openapi", "t"])
        con.execute(job.EVENT_UPSERT, ["052400", "bonus", "2026-10-30", "코나아이", "{}", "kis_openapi", "t"])
    assert con.execute("SELECT COUNT(*) FROM kr_opinions").fetchone() == (1,)
    assert con.execute("SELECT COUNT(*) FROM kr_corp_events").fetchone() == (1,)


def test_초당_한도면_쉬었다_다시_묻는다(monkeypatch) -> None:
    """2026-10-07 첫 수집: 3,504회 중 80회가 EGW00201 (25.993)."""
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    qc = kis.QuoteClient("t")
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)

    class R:
        status_code = 200

        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    answers = [R({"rt_cd": "1", "msg_cd": "EGW00201"}), R({"rt_cd": "0", "output": []})]
    monkeypatch.setattr(qc.session, "get", lambda *a, **k: answers.pop(0))
    assert qc.get("/x", "TR", {}) == {"rt_cd": "0", "output": []}
    assert slept and qc.calls == 2
    # 끝까지 막히면 마지막 응답을 돌려 부른 쪽이 "못 받음" 으로 센다
    qc2 = kis.QuoteClient("t")
    monkeypatch.setattr(qc2.session, "get", lambda *a, **k: R({"rt_cd": "1", "msg_cd": "EGW00201"}))
    assert qc2.get("/x", "TR", {})["msg_cd"] == "EGW00201" and qc2.calls == 1 + kis.RATE_RETRY


def test_잘린_일정은_보유_종목마다_다시_받고_유상증자는_창이_길다() -> None:
    """25.1008: 배당이 시장 전체 100행에서 잘리면 보유 배당 기준일이 빠졌다. 유상증자는 청약일로 걸러 창이 길어야 한다."""
    from datetime import date

    from batch.jobs import kis_flows as job

    class Rs:
        rows = [("005930",), ("000660",)]

    class Client:
        def execute(self, sql, args=None):  # noqa: ANN001, ANN201
            assert sql == job.HELD_KR_TICKERS
            return Rs()

    부름: list[tuple] = []

    class QC:
        def events(self, kind, since, until, code=""):  # noqa: ANN001, ANN201
            부름.append((kind, (until - since).days, code))
            if kind == "dividend" and not code:
                return [{"code": f"{i:06d}", "kind": kind, "record_date": "2026-12-31"} for i in range(100)]
            return [{"code": code or "111111", "kind": kind, "record_date": "2026-12-31"}]

    failed: dict[str, str] = {}
    got = job.collect_events(Client(), QC(), date(2026, 10, 8), failed)  # type: ignore[arg-type]
    assert ("dividend", job.EVENT_BACK_DAYS + job.EVENT_AHEAD_DAYS, "005930") in 부름
    assert ("dividend", job.EVENT_BACK_DAYS + job.EVENT_AHEAD_DAYS, "000660") in 부름
    assert not any(k != "dividend" and c for k, _, c in 부름)  # 안 잘린 종류는 다시 받지 않는다
    assert ("rights", job.EVENT_BACK_DAYS + 120, "") in 부름
    assert "일정:dividend" in failed and len(got) == 100 + 3 + 2
