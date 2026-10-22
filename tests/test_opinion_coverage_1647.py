"""37회차 C 전제 ① — 의견 호출이 덮은 종목·실패·잘림을 실행 기록에 (docs/factors.md 12.40, docs/infra.md 25.1647).

14회차(12.16) 다시 낼 조건: 실패·잘린 종목의 빈 창을 "의견 없음(0)" 으로 읽지 않으려면 그날 무엇을 받았는지가 남아야 한다.
"""

from __future__ import annotations

import inspect

from batch.jobs import kis_flows as job
from batch.sources import kis


def test_받은_종목_못_받은_종목_잘린_종목() -> None:
    c = job.opinion_coverage([5, 3, 9, 1], {3: 0, 9: kis.OPINION_PAGE_ROWS, 1: 12})
    assert c == {"covered_ids": [1, 3, 9], "failed_ids": [5], "truncated_ids": [9], "page_rows": 100}
    # 0건 응답도 받은 것 — 의견 없음(0)과 모름(NULL)을 가르는 열쇠
    assert 3 in c["covered_ids"] and 3 not in c["failed_ids"]
    assert job.opinion_coverage([], {}) == {"covered_ids": [], "failed_ids": [], "truncated_ids": [], "page_rows": 100}


def test_잘림은_걸러진_뒤가_아니라_응답의_원래_행_수로(monkeypatch) -> None:
    """날짜·증권사가 빈 행을 걸러 99건이 되어도 응답이 100행이면 잘렸을 수 있다."""
    행 = [{"stck_bsop_date": "20260923", "mbcr_name": "유안타", "hts_goal_prc": "1000"}] * 99 + [{"stck_bsop_date": ""}]
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    qc = kis.QuoteClient("t")
    monkeypatch.setattr(qc, "get", lambda *a, **k: {"rt_cd": "0", "output": 행})
    from datetime import date

    읽음, 원래 = qc.opinions_with_count("005930", date(2025, 10, 1), date(2026, 10, 1))
    assert len(읽음) == 99 and 원래 == 100 >= kis.OPINION_PAGE_ROWS
    assert qc.opinions("005930", date(2025, 10, 1), date(2026, 10, 1)) == 읽음


def test_수집_실행이_기록에_남긴다() -> None:
    본문 = inspect.getsource(job.run)
    # 25.1681 부터 모든 창을 `opinions_split`(안에서 `opinions_with_count`)으로 받고, 기록은 상태 칸을 더해 넣는다 —
    # 실제 기록은 tests/test_opinion_full_1676.py 의 실행 시험이 본다
    assert "opinions_split(qc, code," in 본문 and "덮음 = opinion_coverage(" in 본문
    assert 'step["opinion_coverage"] = 덮음' in 본문
    assert "qc.opinions_with_count(" in inspect.getsource(job.opinions_split)
