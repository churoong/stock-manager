"""공시 위험 이력 1년 — 사실 줄 (docs/analysis.md 54장, docs/infra.md 25.1157)."""

from __future__ import annotations

from datetime import date, timedelta

from batch.core import db
from batch.jobs import disclosure_reaction as job
from batch.services import disclosure_reaction as dr
from tests.test_portfolio_job import MemClient

오늘 = date(2026, 10, 9)


def _날들(시작: date, 끝: date) -> list[str]:
    return [(시작 + timedelta(days=i)).isoformat() for i in range((끝 - 시작).days + 1)]


def test_덮은_날이_1년을_이어야_구간을_낸다() -> None:
    한해 = 오늘 - timedelta(days=dr.GOVERNANCE_DAYS)
    assert dr.covered_span(_날들(한해, 오늘), 오늘) == (한해.isoformat(), 오늘.isoformat())
    # 하루 빈틈 — "없음" 으로 읽히면 안 되니 쓰지 않는다
    빈틈 = [d for d in _날들(한해, 오늘) if d != "2026-03-01"]
    assert dr.covered_span(빈틈, 오늘) is None
    # 끝이 열흘 전이면 오래됐다 (7일까지 봐준다)
    assert dr.covered_span(_날들(한해, 오늘 - timedelta(days=10)), 오늘) is None
    assert dr.covered_span(_날들(한해, 오늘 - timedelta(days=7)), 오늘) is not None
    # 10-08 부터만 덮었으면(운영 처음 모양) 없음
    assert dr.covered_span(_날들(date(2026, 10, 8), 오늘), 오늘) is None


def test_사실_줄은_유형별로_날짜와_제목을_나열한다() -> None:
    e = [{"type": "fraud", "day": "2026-03-04", "title": "횡령ㆍ배임혐의발생", "covered_from": "2025-10-09",
          "covered_to": "2026-10-09"},
         {"type": "unfaithful", "day": "2026-05-01", "title": "불성실공시법인지정", "covered_from": "2025-10-09",
          "covered_to": "2026-10-09"}]  # fmt: skip
    줄 = dr.governance_line(e)
    assert 줄 == ('지난 1년 공시 위험 이력: 횡령·배임 1건(2026-03-04 "횡령ㆍ배임혐의발생") · 불성실공시 1건(2026-05-01 '
                 '"불성실공시법인지정") — 거래소 공시 2025-10-09~2026-10-09 기준, 점수·결론에 넣지 않음')
    assert dr.governance_line([]) is None


def _mem(덮은날: list[str]) -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (1, '000001', 'KOSDAQ', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    for no, title, day in (("r1", "횡령ㆍ배임혐의발생", "2026-03-04"), ("r2", "횡령ㆍ배임혐의발생", "2026-03-04"),
                           ("r3", "불성실공시법인지정", "2026-05-01"),
                           ("r4", "횡령ㆍ배임혐의발생(종속회사의주요경영사항)", "2026-06-01"),
                           ("r5", "최대주주변경", "2026-07-01")):  # fmt: skip
        c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
                  " VALUES (1, 'c', ?, ?, ?, 'dart_opendart', 't')", [no, title, day])  # fmt: skip
    for d in 덮은날:
        c.execute("INSERT INTO disclosure_coverage (kind, day, rows, calls, source, fetched_at)"
                  " VALUES ('I', ?, 1, 1, 't', 't')", [d])  # fmt: skip
    return mem


def test_금요일_계산이_표를_채우고_못_덮으면_비운다() -> None:
    한해 = 오늘 - timedelta(days=dr.GOVERNANCE_DAYS)
    mem = _mem(_날들(한해, 오늘))
    out = job.compute(mem, 오늘)  # type: ignore[arg-type]
    assert out["governance"]["rows"] == 2
    행 = mem.conn.execute("SELECT receipt_no, type, day FROM governance_events ORDER BY day").fetchall()
    # 같은 날 같은 유형은 한 건, 자회사 사항·최대주주 변경은 빠진다
    assert 행 == [("r1", "fraud", "2026-03-04"), ("r3", "unfaithful", "2026-05-01")]

    mem2 = _mem(_날들(date(2026, 10, 8), 오늘))
    mem2.conn.execute("INSERT INTO governance_events (receipt_no, stock_id, day, type, title, covered_from, covered_to,"
                      " source, computed_at) VALUES ('old', 1, '2026-01-01', 'fraud', 't', 'a', 'b', 's', 't')")  # fmt: skip
    out2 = job.compute(mem2, 오늘)  # type: ignore[arg-type]
    assert out2["governance"] == {"covered": None, "rows": 0}
    assert mem2.conn.execute("SELECT COUNT(*) FROM governance_events").fetchone()[0] == 0
