"""종목에 잇지 못하고 남은 공시를 고유번호로 다시 잇는다 (docs/infra.md 25.1395, 공시 감사 ⑤)."""

from __future__ import annotations

from datetime import date

from batch.jobs import disclosure_reaction as job
from tests.test_cb_overhang import 응답


def _mem():  # noqa: ANN202
    from tests.test_cb_overhang import _mem as 기본

    mem = 기본()
    c = mem.conn
    # 보통주·우선주가 같은 고유번호 — 우선주를 나중에(큰 id) 넣는다
    c.execute("UPDATE stocks SET dart_corp_code = '00999' WHERE id = 1")
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, dart_corp_code, source, fetched_at)"
              " VALUES (2, '000005', 'KOSDAQ', 'KR', 'KRW', 'active', '00999', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, dart_corp_code, source, fetched_at)"
              " VALUES (3, '000000', 'KOSDAQ', 'KR', 'KRW', 'active', '00777', 't', 't')")  # fmt: skip
    # 넣을 때 종목이 없던 신규 상장 공시 — stock_id 없이 남았다
    c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, url, is_material,"
              " source, fetched_at) VALUES (NULL, '00777', '20260305000555', '주요사항보고서(전환사채권발행결정)',"
              " '2026-03-05', 'u', 1, 't', 't')")  # fmt: skip
    c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, url, is_material,"
              " source, fetched_at) VALUES (NULL, '00999', '20260306000666', '조회공시요구(현저한시황변동)',"
              " '2026-03-06', 'u', 0, 't', 't')")  # fmt: skip
    return mem


def test_반응_통계가_다시_이은_공시를_읽는다() -> None:
    mem = _mem()
    줄 = {str(r[4]): int(r[0]) for r in mem.execute(job.REACTION_EVENTS_SQL, ["2026-01-01"]).rows}
    assert 줄["20260305000555"] == 3
    # 같은 고유번호의 보통·우선주 — 하나에만(티커가 작은 쪽). 둘에 세면 같은 공시가 두 사건이 된다
    assert 줄["20260306000666"] == 1
    assert len(줄) == 6  # 원래 넷 + 다시 이은 둘, 중복 없음


def test_전환사채_대기열도_다시_잇는다(monkeypatch) -> None:  # noqa: ANN001
    mem = _mem()
    monkeypatch.setattr(job.dd, "fetch_cb_decision", lambda corp, day: (응답, None))
    결과 = job.collect_cb(mem, date(2026, 10, 12))  # type: ignore[arg-type]
    assert 결과["calls"] == 3
    assert mem.conn.execute("SELECT stock_id FROM cb_issuances WHERE receipt_no = '20260305000555'").fetchone() == (3,)
