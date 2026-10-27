"""미국 재무 월 수집 — 후보에 여유를 두고, SEC 차단으로 도중에 멈추면 실패로 닫는다 (docs/infra.md 25.1826).

1. 후보를 유니버스 문턱 그대로 골라, 거래대금이 막 오른 유니버스 종목이 새 10-K 를 못 받고 지난해 보고서로 점수·중기
   신호를 받았다(시총·상장일 수집은 25.530 에서 여유 0.5 를 두었다).
2. SEC 403·429 로 도중에 멈춰도 partial·종료코드 0 이라 CIK 큰 쪽(최근 상장 회사)이 다음 달까지 빈 것을 몰랐다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from batch.core import db
from batch.jobs import us_financials as job
from batch.jobs import us_shares
from batch.sources import sec_edgar
from tests.test_metrics_beta import MemClient


def _준비(monkeypatch: pytest.MonkeyPatch, 막힘: str | None) -> tuple[MemClient, list[float]]:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    문턱: list[float] = []
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job, "_us_symbol_ids", lambda c: {"AAA": 1, "BBB": 2, "CCC": 3})

    def 후보(c, t):  # noqa: ANN001, ANN202
        문턱.append(t)
        return {"AAA", "BBB", "CCC"}

    monkeypatch.setattr(job, "candidate_symbols", 후보)

    class 가짜SEC:
        calls = 0

        def ticker_map(self) -> dict[str, str]:
            return {"AAA": "0000000001", "BBB": "0000000002", "CCC": "0001900000"}

        def company_facts(self, cik: str) -> dict:
            if cik == 막힘:
                raise sec_edgar.SecBlocked("HTTP 429")
            return {"facts": {}}

    monkeypatch.setattr(job.sec_edgar, "SecClient", 가짜SEC)
    monkeypatch.setattr(
        job.sf, "parse_annual_reports", lambda p: [SimpleNamespace(fiscal_year=2025, values={"revenue": 1})]
    )
    monkeypatch.setattr(job, "build_rows", lambda *a, **k: ([], []))
    monkeypatch.setattr(job, "build_dividend_rows", lambda *a, **k: [])
    monkeypatch.setattr(job, "payload_fill_statements", lambda *a, **k: [])
    return m, 문턱


def test_SEC_차단으로_도중에_멈추면_실패로_닫는다(monkeypatch: pytest.MonkeyPatch) -> None:
    m, _ = _준비(monkeypatch, 막힘="0000000002")
    assert job.run(5_000_000, None) == 1
    assert m.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC").fetchone()[0] == "failed"


def test_끝까지_받으면_성공이고_후보는_여유를_둔다(monkeypatch: pytest.MonkeyPatch) -> None:
    m, 문턱 = _준비(monkeypatch, 막힘=None)
    assert job.run(5_000_000, None) == 0
    assert m.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC").fetchone()[0] == "success"
    assert 문턱 == [5_000_000 * us_shares.CANDIDATE_MARGIN]
