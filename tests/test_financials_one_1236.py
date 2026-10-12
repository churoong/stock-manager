"""한 종목 재무 수집(참고 분석)이 주간 재무 수집의 화요일 재시도를 건너뛰게 하지 않는다 (docs/infra.md 25.1236, DART 감사 재현)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from batch.core import db
from batch.jobs import financials
from batch.sources import dart
from batch.sources.yfinance_src import FetchResult
from tests.test_portfolio_job import MemClient


def test_한_종목_성공이_재시도를_막지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, dart_corp_code)"
                     " VALUES (1, '123456', 'KOSPI', 'KR', 'KRW', 't', 't', '00999999')")  # fmt: skip
    월 = (datetime.now(UTC) - timedelta(hours=20)).isoformat()
    mem.conn.execute("INSERT INTO batch_runs (job_name, market, status, started_at, finished_at)"
                     " VALUES ('financials', 'KR', 'partial', ?, ?)", [월, 월])  # fmt: skip
    monkeypatch.setattr(financials, "TursoClient", lambda: mem)
    monkeypatch.setattr(financials.time, "sleep", lambda s: None)

    def 받기(chunk, fy, code):  # noqa: ANN001, ANN202
        item = dart.CompanyFinancials(corp_code=chunk[0], stock_code="123456", fiscal_year=fy, report_code=code,
                                      consolidated=True, receipt_no=f"{fy + 1}0310000001",
                                      report_date=f"{fy + 1}-03-10", currency="KRW", values={"revenue": 1})  # fmt: skip
        return FetchResult(ok=True, source=dart.SOURCE, data=[item], limit_state="ok")

    monkeypatch.setattr(financials.dart, "fetch_multi_financials", 받기)
    financials.run(financials.collection_plan(None, 1, "11011", datetime.now(UTC).date()),
                   universe_only=False, only_ids=[1])  # fmt: skip
    이름들 = [r[0] for r in mem.conn.execute("SELECT job_name FROM batch_runs ORDER BY id")]
    assert 이름들 == ["financials", financials.JOB_NAME_ONE]
    assert db.ran_within(mem, "financials", 3, "KR", include_partial=False) is False  # type: ignore[arg-type]


def test_미국_재무도_한_종목은_따로_적는다() -> None:
    from pathlib import Path

    from batch.jobs import us_financials

    글 = Path(us_financials.__file__).read_text(encoding="utf-8")
    assert us_financials.JOB_NAME_ONE != us_financials.JOB_NAME
    assert "job_name=JOB_NAME_ONE if only_ids else JOB_NAME" in 글
