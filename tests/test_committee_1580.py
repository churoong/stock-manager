"""25.1577 교차검증 권고 반영 (docs/company.md 3장, docs/infra.md 25.1580).

지키는 것
- 덮개 끝이 기준일 그날(월요일 배치)이어도 그날 공시를 보충한다
- 상세에 행이 없던(`no_row`) 전환사채 결정은 받은 것으로 치지 않고, 수집이 14일 안에서 다시 부른다
- 실행 품질 — 매도 때 아직 서 있던 발생부터 지연을 센다(60일 안에 끝난 옛 발생이 아니라)
"""

from __future__ import annotations

from datetime import date

from batch.jobs import company as cjob
from batch.jobs import disclosure_reaction as djob
from batch.services import company as co
from batch.services import execution_quality as eq
from tests.test_cb_overhang import _mem, 응답
from tests.test_committee_1577 import _공시, _덮개
from tests.test_company_dossier_1463 import _행, c, 기준  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_덮개_끝이_기준일이어도_그날_공시를_보충한다(c: Client) -> None:  # noqa: F811
    c.conn.execute("DELETE FROM governance_events WHERE day = '2026-05-01'")
    c.conn.execute("DELETE FROM cb_issuances")
    assert 기준 == "2026-10-15" and c.q("SELECT MAX(covered_to) FROM governance_events") == [("2026-10-15",)]
    _공시(c, "f1", "횡령ㆍ배임혐의발생", 기준)  # 주간 표가 지어진 뒤 그날 저녁
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert d.gov_covered and d.gov_events == [(기준, "fraud")] and co.governance(d).veto


def test_상세에_행이_없던_전환사채는_받은_것이_아니다(c: Client) -> None:  # noqa: F811
    _덮개(c)
    _공시(c, "cb1", "주요사항보고서(전환사채권발행결정)", "2026-10-14")
    c.conn.execute("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, source, fetched_at)"
                   " VALUES ('cb1', 1, '2026-10-14', 'no_row', 't', 't')")  # fmt: skip
    [d] = cjob.load_dossiers(c, [_행(1, "005930", "KOSPI", 80)], "KR", 기준)
    assert not d.gov_covered


def test_최근_no_row_는_다시_불러_덮는다(monkeypatch) -> None:  # noqa: ANN001
    mem = _mem()
    monkeypatch.setattr(djob.dd, "fetch_cb_decision", lambda corp, day: ([], None))  # 그날은 상세에 행이 없었다
    djob.collect_cb(mem, date(2026, 3, 5))  # type: ignore[arg-type]
    assert mem.conn.execute("SELECT status FROM cb_issuances WHERE receipt_no = '20260301000111'").fetchall() == [
        ("no_row",)]
    불림: list[str] = []

    def 가짜(corp: str, day: str):  # noqa: ANN202
        불림.append(day)
        return 응답, None

    monkeypatch.setattr(djob.dd, "fetch_cb_decision", 가짜)
    결과 = djob.collect_cb(mem, date(2026, 3, 13))  # type: ignore[arg-type]
    assert 결과["ok"] == 1 and "20260301" in 불림
    줄 = mem.conn.execute("SELECT status, shares_pct FROM cb_issuances WHERE receipt_no = '20260301000111'").fetchall()
    assert 줄 == [("ok", 5.23)]
    # 기한(14일) 밖 no_row 는 다시 부르지 않는다
    불림.clear()
    djob.collect_cb(mem, date(2026, 3, 30))  # type: ignore[arg-type]
    assert 불림 == []


def test_매도_때_서_있던_발생부터_센다() -> None:
    플래그 = [{"stock_id": 1, "reason_code": "손절", "level": "red", "first_seen": "2026-08-10", "last_seen": "2026-08-12"},
              {"stock_id": 1, "reason_code": "손절", "level": "red", "first_seen": "2026-09-27", "last_seen": "2026-09-30"}]  # fmt: skip
    매매 = [{"stock_id": 1, "side": "sell", "trade_date": "2026-09-30", "price": 1}]
    m = eq.measure(매매, [], [], 플래그, "2026-09-01", "2026-09-30")
    assert (m["sells_after_flag"], m["sell_delay_avg"]) == (1, 3.0)


def test_서_있던_발생이_없으면_가장_최근_발생부터() -> None:
    """60일 안에 끝난 발생 둘 — 가장 이른 것(51일)이 아니라 최근 것(15일) (25.1583, 25.1580 반박 [권고] 2)."""
    플래그 = [{"stock_id": 1, "reason_code": "손절", "level": "red", "first_seen": "2026-08-10", "last_seen": "2026-08-12"},
              {"stock_id": 1, "reason_code": "손절", "level": "red", "first_seen": "2026-09-15", "last_seen": "2026-09-20"}]  # fmt: skip
    매매 = [{"stock_id": 1, "side": "sell", "trade_date": "2026-09-30", "price": 1}]
    assert eq.measure(매매, [], [], 플래그, "2026-09-01", "2026-09-30")["sell_delay_avg"] == 15.0


def test_받은_상세는_다시_부른_결과로_덮지_않는다() -> None:
    """`CB_INSERT` 의 덮기는 `no_row` 행만 (25.1583, 25.1580 반박 [권고] 3 — 대기 질의가 ok 를 안 골라 방어만 하던 조건)."""
    mem = _mem()
    mem.conn.execute("INSERT INTO cb_issuances (receipt_no, stock_id, decided_at, status, shares_pct, source, fetched_at)"
                     " VALUES ('r1', 1, '2026-03-01', 'ok', 5.0, 't', 't')")  # fmt: skip
    mem.conn.execute(djob.CB_INSERT, ["r1", 1, "2026-03-01", "no_row", *[None] * 7, "t", "t2"])
    assert mem.conn.execute("SELECT status, shares_pct FROM cb_issuances WHERE receipt_no = 'r1'").fetchall() == [("ok", 5.0)]
