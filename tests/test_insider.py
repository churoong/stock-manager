"""내부자 매매 참고 행 (docs/signals.md 8장). 집계는 손으로 맞춘 값, 시점 규칙은 접수일."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.services import insider
from batch.services import signals as sg

T = insider.Trade


class Test집계:
    TRADES = [
        T("2026-07-01", "김임원", "buy", 1000, price=50_000.0),
        T("2026-08-15", "김임원", "buy", 500),
        T("2026-08-20", "박주주", "sell", 300),
        T("2026-09-01", "이임원", "other", 10_000),  # 증여 등. 매수·매도에 세지 않는다
    ]

    def test_순매수와_사람_수(self) -> None:
        s = insider.summarize(self.TRADES, "2026-09-15")
        assert s is not None
        assert s.buy_shares == 1500 and s.sell_shares == 300 and s.net_shares == 1200
        assert s.buyers == ["김임원"] and s.sellers == ["박주주"]  # 같은 사람은 한 번
        assert s.reports == 4 and s.latest_filed == "2026-09-01"
        assert s.since == "2026-06-17"

    def test_창_밖은_세지_않는다(self) -> None:
        # 7/1 은 2026-10-15 기준 90일(7/17) 밖
        s = insider.summarize(self.TRADES, "2026-10-15")
        assert s is not None and s.buy_shares == 500

    def test_접수일이_기준일_뒤면_모른다(self) -> None:
        # look-ahead 방지: 8/15 이후 접수분은 8/10 기준에 없다
        s = insider.summarize(self.TRADES, "2026-08-10")
        assert s is not None and s.buy_shares == 1000 and s.sell_shares == 0

    def test_보고가_없으면_None(self) -> None:
        assert insider.summarize(self.TRADES, "2026-06-01") is None
        assert insider.summarize([], "2026-09-15") is None

    def test_순매수_비율(self) -> None:
        s = insider.summarize(self.TRADES, "2026-09-15")
        assert s is not None
        assert s.net_ratio(120_000) == pytest.approx(0.01)
        assert s.net_ratio(None) is None and s.net_ratio(0) is None


class Test근거행:
    def test_참고_행이다(self) -> None:
        s = insider.summarize(Test집계.TRADES, "2026-09-15")
        rows = insider.criteria_rows(s, 120_000)
        assert len(rows) == 1
        row = rows[0]
        assert row["label"] == "내부자 매매 (참고)" and row["passed"] is None
        assert row["display"] == "순매수 1,200주 (상장주식수의 +1.000%) · 매수 1명 · 매도 1명 · 보고 4건"
        assert row["as_of"] == "2026-09-01"
        assert "2026-06-17~2026-09-15" in row["threshold"]

    def test_주식수를_모르면_비율이_빠진다(self) -> None:
        s = insider.summarize(Test집계.TRADES, "2026-09-15")
        assert "상장주식수" not in insider.criteria_rows(s, None)[0]["display"]

    def test_없으면_행이_없다(self) -> None:
        # "내부자 거래 없음" 은 "수집 안 됨" 과 구분되지 않아 적지 않는다
        assert insider.criteria_rows(None, 100) == []

    def test_신호_근거표_끝에_붙는다(self) -> None:
        inp = sg.SignalInput(
            stock_id=1, ticker="A", name="A", market="KOSPI",
            closes=[100.0] * 70, turnovers=[1e9] * 70,
            factor_scores={"momentum": 80.0},
            insider=insider.summarize(Test집계.TRADES, "2026-09-15"), listed_shares=120_000,
        )
        # 단기 규칙이 통과하도록 오르는 계열
        inp.closes = [100.0 + i for i in range(70)]
        signals = sg.evaluate(inp)
        if signals:  # 규칙이 바뀌어 신호가 안 나도 이 테스트가 규칙을 고정하진 않는다
            labels = [c["label"] for c in signals[0].rationale_data["criteria"]]
            assert labels[-1] == "내부자 매매 (참고)"
        # 요약이 없으면 행도 없다
        inp.insider = None
        for s in sg.evaluate(inp):
            assert all(c["label"] != "내부자 매매 (참고)" for c in s.rationale_data["criteria"])


class Test읽기:
    def _client(self):
        import sqlite3
        from typing import Any

        from batch.core.turso import ResultSet

        class Sqlite:
            def __init__(self) -> None:
                self.conn = sqlite3.connect(":memory:")

            def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
                cur = self.conn.execute(sql, args or [])
                cols = [d[0] for d in cur.description or []]
                return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

            def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
                return [self.execute(sql, args) for sql, args in statements]

            def close(self) -> None:
                pass

        client = Sqlite()
        db.apply_migrations(client)  # type: ignore[arg-type]
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        for filed, who, action, shares in [
            ("2026-08-01", "김임원", "buy", 100), ("2026-09-01", "박주주", "sell", 40), ("2026-09-20", "김임원", "buy", 999),
        ]:
            client.conn.execute(
                "INSERT INTO insider_trades (stock_id, filed_date, insider, action, shares, currency, source, receipt_no, fetched_at)"
                " VALUES (1, ?, ?, ?, ?, 'KRW', 'dart_opendart', ?, 't')",
                [filed, who, action, shares, f"r{filed}{who}"],
            )
        return client

    def test_표에서_읽어_집계한다(self) -> None:
        summaries = insider.load_summaries(self._client(), "KR", "2026-09-15")  # type: ignore[arg-type]
        assert summaries[1].buy_shares == 100 and summaries[1].sell_shares == 40  # 9/20 접수분은 아직 모른다

    def test_표가_비면_빈_dict(self) -> None:
        assert insider.load_summaries(self._client(), "US", "2026-09-15") == {}  # type: ignore[arg-type]
