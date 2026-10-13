"""엇갈림 절은 최근 5거래일 안의 수급만 더한다 (docs/infra.md 25.1276, 리포트 감사 재현)."""

from __future__ import annotations

from types import SimpleNamespace

from batch.jobs import daily
from tests.test_report_picks import SqliteClient


def _줄(날들: list[str]) -> list[str]:
    client = SqliteClient()
    c = client.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, name_ko)"
              " VALUES (1, '000001', 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '가상종목')")  # fmt: skip
    for d in 날들:
        c.execute("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
                  " VALUES (1, ?, -2000000000, -1500000000, 'kis_openapi', 't')", [d])  # fmt: skip
    return daily._divergence_lines(client, "2026-10-12", [SimpleNamespace(stock_id=1, name="가상종목", ticker="000001")], [])


def test_수집이_멈춰_묵은_수급은_싣지_않는다() -> None:
    # 마지막 수급 10-01, 리포트 10-12 — 그 사이 국내 거래일 10-02·10-06·10-07·10-08·10-12 (5거래일 창 밖)
    assert not any("외국인" in x for x in _줄(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"]))


def test_최근_거래일_수급은_그대로() -> None:
    assert any("외국인" in x for x in _줄(["2026-10-07", "2026-10-08", "2026-10-12"]))
