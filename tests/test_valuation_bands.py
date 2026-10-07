"""밸류에이션 밴드 주간 계산 테스트. 손으로 셀 수 있는 계열과 실제 마이그레이션의 메모리 SQLite 로 본다."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from batch.jobs import valuation_bands as job
from batch.services import valuation_band as vb
from tests.test_portfolio_job import MemClient


def days(n: int, start: str = "2024-01-01") -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


class Test계산:
    def test_표본이_모자라면_분위를_비우고_사유를_남긴다(self) -> None:
        prices = [(d, 100.0) for d in days(100)]
        r = vb.compute(prices, [("2023-12-01", 1000.0)], 10)
        assert r.p20 is None and r.sample == 100
        assert r.current_value == pytest.approx(1.0)  # 100 / (1000/10)
        assert r.skip_reason == "PBR 표본 100일 (250일 미만)"

    def test_현재값의_밴드_위치는_계열_전체로(self) -> None:
        # 가격 1..300, BPS 1 → PBR 1..300. 마지막(300)보다 작은 값 299개 → 99.67%
        prices = [(d, float(i + 1)) for i, d in enumerate(days(300))]
        r = vb.compute(prices, [("2023-12-01", 10.0)], 10)
        assert r.sample == 300 and r.skip_reason is None
        assert r.current_value == pytest.approx(300.0)
        assert r.band_rank == pytest.approx(299 / 300 * 100)
        assert r.p50 == pytest.approx(150.5)
        assert r.price_date == "2024-10-26" and r.equity_report_date == "2023-12-01"

    def test_기준일_뒤에_접수된_자본은_쓰지_않는다(self) -> None:
        prices = [(d, 100.0) for d in days(300)]
        # 두 번째 보고서는 마지막 가격일보다 뒤에 접수 → 현재값은 첫 보고서로
        r = vb.compute(prices, [("2023-12-01", 1000.0), ("2030-01-01", 5000.0)], 10)
        assert r.current_value == pytest.approx(1.0)
        assert r.equity_report_date == "2023-12-01"

    @pytest.mark.parametrize(
        ("shares", "prices", "equities", "reason"),
        [
            (None, [("2024-01-01", 1.0)], [("2023-01-01", 1.0)], "상장주식수 없음"),
            (10, [], [("2023-01-01", 1.0)], "가격 없음"),
            (10, [("2024-01-01", 1.0)], [], "연간 연결 자본총계 없음"),
        ],
    )
    def test_입력이_없으면_지어내지_않는다(self, shares, prices, equities, reason) -> None:
        r = vb.compute(prices, equities, shares)
        assert r.current_value is None and r.p50 is None and r.skip_reason == reason


class Test업종_백분위:
    def test_업종이_5종목_이상이면_업종_안에서(self) -> None:
        rows = [(i, "금융", float(i)) for i in range(1, 6)] + [(9, "반도체", 0.1)]
        out = vb.peer_percentiles(rows, "KR")
        assert out[1] == (0.0, "sector:KR:금융", 5)
        assert out[5] == (80.0, "sector:KR:금융", 5)

    def test_작은_업종과_업종_없음은_시장_전체로(self) -> None:
        rows = [(i, "금융", float(i)) for i in range(1, 6)] + [(9, "반도체", 0.1), (10, None, 2.5), (11, "금융", None)]
        out = vb.peer_percentiles(rows, "KR")
        assert out[9] == (0.0, "market:KR", 7)
        assert out[10][1] == "market:KR" and out[10][0] == pytest.approx(3 / 7 * 100)
        assert 11 not in out  # 현재값이 없으면 백분위도 없다


def test_적재_끝까지(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, shares in ((1, 10), (2, None)):
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, sector,"
            " listed_shares) VALUES (?, ?, 'KOSPI', 'KR', '테스트', 'KRW', 'active', 't', 't', '금융', ?)",
            [sid, f"00000{sid}", shares],
        )
        c.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-09-14', ?, 1, 'KRW', 't')",
            [sid],
        )
    for d in days(300, "2025-11-01"):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, 100, 'KRW', 't', 't')",
            [d],
        )
    c.execute(
        "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
        " receipt_no, currency, unit, total_equity, source, fetched_at)"
        " VALUES (1, 2024, '11011', 'A', 1, '2025-03-10', 'r1', 'KRW', 'KRW', 1000, 't', 't')"
    )
    assert job.run(("KR",), "2026-09-17") == 0
    rows = c.execute(
        "SELECT stock_id, current_value, sample, skip_reason, peer_group FROM valuation_bands ORDER BY stock_id"
    ).fetchall()
    assert rows[0] == (1, pytest.approx(1.0), 300, None, "market:KR")
    assert rows[1] == (2, None, 0, "상장주식수 없음", None)

    # 같은 날 다시 돌리면 덮어쓰고, 유니버스에서 빠진 종목은 지운다
    c.execute("UPDATE universe_members SET included = 0 WHERE stock_id = 2")
    assert job.run(("KR",), "2026-09-17") == 0
    assert [r[0] for r in c.execute("SELECT stock_id FROM valuation_bands").fetchall()] == [1]


def test_열_개수() -> None:
    from batch.core import db

    row = job.build_rows([{"stock_id": 1, "listed_shares": None, "sector": None, "currency": "KRW"}], {}, {}, "KR",
                         "2026-09-17", "t")[0]  # fmt: skip
    assert len(row) == db.column_count(job._COLS) == 20
