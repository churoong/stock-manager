"""매매 복기가 포트폴리오 배치 안에서 끝까지 만들어지는지. 실제 마이그레이션을 적용한 메모리 SQLite, 네트워크 없음.

복기 계산 자체는 tests/test_review.py 가 검증한다. 여기서는 배치가
  - trades 스냅샷 열을 읽어 trade_reviews 에 옮기고
  - 설정 horizon_targets 로 판정하고
  - review_stats 를 집단별로 만들고
  - 다시 돌려도 같은 결과를 내는지
만 본다.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from batch.core.turso import ResultSet
from batch.jobs import portfolio as job
from batch.services import review as rv


class MemClient:
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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> MemClient:
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    from batch.core import db

    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
        " VALUES (2, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't')"
    )
    for day, kr, us, fx in (("2026-09-14", 70_000, 200.0, 1380.0), ("2026-09-16", 75_000, 210.0, 1400.0)):
        c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, ?, 'KRW', 't', 't')", [day, kr])  # fmt: skip
        c.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (2, ?, ?, 'USD', 't', 't')", [day, us])  # fmt: skip
        c.execute("INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 't', 't')", [day, fx])  # fmt: skip

    def trade(tid, sid, side, day, price, qty, cur, fx, horizon, snapshot=None):
        snap = snapshot or {}
        c.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
            " fee, tax, horizon, snapshot_as_of, score_at_trade, signal_type_at_trade, sentiment_at_trade,"
            " factor_scores_at_trade, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', 0, 0, ?, ?, ?, ?, ?, ?, 't', ?)",
            [tid, sid, side, day, price, qty, cur, fx, horizon, snap.get("as_of"), snap.get("score"),
             snap.get("signal"), snap.get("sentiment"), snap.get("factors"), f"2026-09-17T00:00:0{tid}"],
        )  # fmt: skip

    # 삼성전자: 중기, 스냅샷 있음. 10주 사서 전량 매도 (+10%, 30일)
    trade(1, 1, "buy", "2026-06-01", 70_000, 10, "KRW", 1.0, "mid",
          {"as_of": "2026-05-29", "score": 78.0, "signal": "실적 모멘텀", "sentiment": 12.0,
           "factors": json.dumps({"value": 60, "growth": 80})})  # fmt: skip
    trade(2, 1, "sell", "2026-07-01", 77_000, 10, "KRW", 1.0, "mid")
    # 애플: 기간 없음, 스냅샷 없음. 5주 중 2주만 매도 (-5%)
    trade(3, 2, "buy", "2026-06-01", 200.0, 5, "USD", 1380.0, None)
    trade(4, 2, "sell", "2026-06-11", 190.0, 2, "USD", 1400.0, None)
    return mem


def test_복기_행이_스냅샷과_판정을_담는다(client: MemClient) -> None:
    assert job.run() == 0
    rows = {
        r[0]: r
        for r in client.conn.execute(
            "SELECT buy_trade_id, horizon, has_snapshot, score_at_trade, signal_type_at_trade, return_pct,"
            " holding_days, outcome, target_pct, stop_pct, partial, verdict_text, last_sell_date FROM trade_reviews"
        ).fetchall()
    }
    assert set(rows) == {1, 3}

    samsung = rows[1]
    assert samsung[1] == "mid" and samsung[2] == 1 and samsung[3] == 78.0 and samsung[4] == "실적 모멘텀"
    assert samsung[5] == pytest.approx(0.10)
    assert samsung[6] == pytest.approx(30.0)
    assert samsung[7] == "gain"  # 중기 목표 +25% 미달, 손절 -15% 유지
    assert (samsung[8], samsung[9]) == (25.0, -15.0)  # 설정이 없으면 signals.DEFAULT_TARGETS
    assert samsung[10] == 0
    assert "실적 모멘텀" in samsung[11] and "+10.0%" in samsung[11] and "목표 +25% 미달" in samsung[11]
    assert samsung[12] == "2026-07-01"

    apple = rows[3]
    assert apple[1] is None and apple[2] == 0 and apple[7] == "none"
    assert apple[5] == pytest.approx(-0.05)
    assert apple[10] == 1  # 5주 중 2주
    assert "저장된 근거 없음" in apple[11]


def test_설정_목표가_판정을_바꾼다(client: MemClient) -> None:
    # 중기 목표를 +8% 로 낮추면 +10% 는 도달이다
    client.conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('horizon_targets', ?, 't')",
        [json.dumps({"mid": {"target_pct": 8.0, "stop_pct": -15.0}})],
    )
    job.run()
    outcome, target = client.conn.execute(
        "SELECT outcome, target_pct FROM trade_reviews WHERE buy_trade_id = 1"
    ).fetchone()
    assert (outcome, target) == ("target", 8.0)


def test_집단_통계가_전체_기간_신호로_나뉜다(client: MemClient) -> None:
    job.run()
    stats = {
        r[0]: r
        for r in client.conn.execute(
            "SELECT group_key, group_kind, n, sample_ok, win_rate, target_rate, calc_version FROM review_stats"
        ).fetchall()
    }
    # 신호별 집단은 스냅샷이 있는 행만 센다. 애플은 기간도 스냅샷도 없어 전체에만 들어간다
    assert set(stats) == {"all", "horizon:mid", "signal:실적 모멘텀"}
    # 애플은 일부만 팔아 승률·표본에서 빠진다 (25.694) — 남은 1건, 표본 < MIN_SAMPLE → 단정하지 않음
    assert stats["all"][2] == 1 and stats["all"][3] == 0
    assert stats["all"][4] == pytest.approx(1.0)
    assert stats["all"][5] == pytest.approx(0.0)  # 기간이 있는 1건 중 목표 도달 0
    assert stats["horizon:mid"][2] == 1
    assert stats["signal:실적 모멘텀"][2] == 1
    assert stats["all"][6] == rv.CALC_VERSION


def test_다시_돌려도_같은_결과(client: MemClient) -> None:
    job.run()
    first = client.conn.execute("SELECT * FROM trade_reviews ORDER BY buy_trade_id").fetchall()
    first_stats = client.conn.execute("SELECT * FROM review_stats ORDER BY group_key").fetchall()
    job.run()
    second = client.conn.execute("SELECT * FROM trade_reviews ORDER BY buy_trade_id").fetchall()
    second_stats = client.conn.execute("SELECT * FROM review_stats ORDER BY group_key").fetchall()
    assert [r[:-1] for r in first] == [r[:-1] for r in second]  # created_at 제외
    assert [r[:-1] for r in first_stats] == [r[:-1] for r in second_stats]
    assert len(second) == 2


def test_매도가_없으면_복기가_비고_전체_집단만_남는다(client: MemClient) -> None:
    client.conn.execute("DELETE FROM trades WHERE side = 'sell'")
    job.run()
    assert client.conn.execute("SELECT COUNT(*) FROM trade_reviews").fetchone()[0] == 0
    stats = client.conn.execute("SELECT group_key, n, sample_ok FROM review_stats").fetchall()
    assert stats == [("all", 0, 0)]
