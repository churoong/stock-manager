"""투자위원회 뉴스·감성부 근거의 날짜 = 실제 감성 행 날짜 (docs/infra.md 25.1730, 위원회 감사 [권고] 1)."""

from __future__ import annotations

from batch.jobs import company as cjob
from batch.services import company as co
from tests.test_paper_job_1445 import Client, _신호


def test_근거표의_감성_날짜는_그_행_날짜() -> None:
    """기준일 10-14, 감성 행은 10-12 하나(3일 신선도 안) — 근거표가 10-14 로 찍혀 "언제 기준" 이 이틀 틀렸다"""
    c = Client(accounts=None)
    c.conn.execute(
        "INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count, negative_count,"
        " negative_count_7d, decay_halflife_days, method, calc_version, created_at)"
        " VALUES (1, '2026-10-12', 45, 10, 6, 1, 0, 7, 't', 1, 't')"
    )  # fmt: skip
    신호 = _신호()
    [d] = cjob.load_dossiers(c, [신호], "KR", "2026-10-14")
    assert d.sentiment == 45 and d.sentiment_date == "2026-10-12"
    o = co.sentiment(d)
    assert o.stance == 1 and "2026-10-12" in str(o.evidence)
    assert cjob.dossier_json(d)["sentiment_date"] == "2026-10-12"
