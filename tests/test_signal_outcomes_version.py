"""성적표는 그 기준일의 가장 새 판 신호만 센다 (docs/infra.md 25.464)."""

from __future__ import annotations

from typing import Any

from batch.jobs import signal_outcomes as so


class 가짜:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def execute(self, sql: str, args: list[Any]) -> Any:
        rows = self.rows

        class R:
            def dicts(self) -> list[dict[str, Any]]:
                return [dict(r) for r in rows]

        return R()


def 행(sid: int, d: str, h: str, v: int, target: float = 10.0) -> dict[str, Any]:
    return {"stock_id": sid, "as_of_date": d, "horizon": h, "target_price": target, "stop_price": 1.0,
            "market": "KOSPI", "calc_version": v, "rationale_data": "{}"}  # fmt: skip


def test_새_판에서_사라진_신호의_옛_판_행은_세지_않는다() -> None:
    rows = [행(1, "2026-09-10", "short", 1), 행(2, "2026-09-10", "short", 1),  # 옛 판: 1·2번
            행(1, "2026-09-10", "short", 2, target=11.0),  # 새 판: 1번만
            행(3, "2026-09-09", "mid", 1)]  # 새 판이 없던 날은 옛 판 그대로  # fmt: skip
    got = so.load_signals(가짜(rows), "KR", "2026-09-01")  # type: ignore[arg-type]
    키 = sorted((r["stock_id"], r["as_of_date"], r["calc_version"]) for r in got)
    assert 키 == [(1, "2026-09-10", 2), (3, "2026-09-09", 1)]
    assert next(r for r in got if r["stock_id"] == 1)["target_price"] == 11.0


class _결과:
    def __init__(self, cur: Any) -> None:
        cols = [c[0] for c in cur.description or []]
        self.rows = cur.fetchall()
        self._cols = cols

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self._cols, r, strict=True)) for r in self.rows]

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None


class 메모리DB:
    """실제 마이그레이션을 적용한 SQLite — 질의 글자를 그대로 돌린다."""

    def __init__(self) -> None:
        import sqlite3
        from pathlib import Path

        self.conn = sqlite3.connect(":memory:")
        for path in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
            self.conn.executescript(path.read_text(encoding="utf-8"))

    def execute(self, sql: str, args: list[Any] | None = None) -> _결과:
        return _결과(self.conn.execute(sql, args or []))

    def 종목(self, sid: int, country: str) -> None:
        self.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'M', ?, 'KRW', 'active', 't', 't')", [sid, f"T{sid}", country])

    def 신호(self, sid: int, d: str, h: str, v: int, w: float = 5.0) -> None:
        self.conn.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, suggested_weight_pct, size_reduction, rationale_text, rationale_data, calc_version,"
            " created_at) VALUES (?, ?, ?, 'x', 1, 2, 'KRW', '[]', ?, 0, 't', ?, ?, 't')",
            # 근거표가 있는 정상 신호 — 따라잡기 알림이 근거표 없는 신호를 빼므로(25.752)
            [sid, d, h, w, '{"criteria": [{"label": "x", "display": "1", "threshold": "> 0", "source": "t"}]}', v],
        )  # fmt: skip


def _옛판_섞인_DB(country: str, horizon: str) -> 메모리DB:
    db = 메모리DB()
    for sid in (1, 2):
        db.종목(sid, country)
    db.신호(1, "2026-09-25", horizon, 1)
    db.신호(2, "2026-09-25", horizon, 1)  # 옛 판에만 있다 — 새 판이 걸러 냈다
    db.신호(1, "2026-09-25", horizon, 2)
    return db


def test_ETF_추천_종목은_새_판만() -> None:
    from batch.jobs import etf

    got, as_of = etf.load_recommended_us(_옛판_섞인_DB("US", "short"))  # type: ignore[arg-type]
    assert as_of == "2026-09-25" and set(got) == {"T1"}


def test_장기_신호_종목은_새_판만() -> None:
    from batch.jobs import accumulation

    assert accumulation.long_signal_ids(_옛판_섞인_DB("KR", "long"), "KR") == {1}  # type: ignore[arg-type]


def test_스트레스_바스켓은_새_판만() -> None:
    from batch.jobs import stress

    weights, as_of = stress.load_basket(_옛판_섞인_DB("KR", "short"), "KR")  # type: ignore[arg-type]
    assert as_of == "2026-09-25" and set(weights) == {1}


def test_따라잡기_알림의_신호_수는_새_판만() -> None:
    import sys
    from datetime import UTC, datetime
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import catchup_report

    facts = catchup_report.collect(_옛판_섞인_DB("KR", "short"), datetime(2026, 9, 28, tzinfo=UTC))
    assert facts.signals_by_horizon == {"short": 1} and facts.signals_read_error is None


def test_따라잡기_알림의_신호는_점수_순_종목마다_한_줄() -> None:
    """`sg.id`(적재 순서)로 잘라 아침 리포트 상위와 다른 종목이 나갔다 (docs/infra.md 25.489, 텔레그램 감사)."""
    import sys
    from datetime import UTC, datetime
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import catchup_report

    db = 메모리DB()
    for sid in (1, 2, 3):
        db.종목(sid, "KR")
    for sid, h in ((1, "short"), (1, "mid"), (2, "short"), (3, "short")):
        db.신호(sid, "2026-09-25", h, 1)
    for sid, 점수 in ((1, 50.0), (2, 90.0), (3, 70.0)):
        db.conn.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used, weights_json,"
            " calc_version, created_at) VALUES (?, '2026-09-25', ?, '{}', 0, '{}', 1, 't')", [sid, 점수])
    facts = catchup_report.collect(db, datetime(2026, 9, 28, tzinfo=UTC))
    assert [s["ticker"] for s in facts.top_signals] == ["T2", "T3", "T1"]


def test_제외된_종목의_신호는_스트레스·적립·ETF_에_쓰지_않는다() -> None:
    """추천 화면·리포트와 같은 상태 조건 (docs/infra.md 25.802·25.824)."""
    from batch.jobs import accumulation, etf, stress

    for country, horizon in (("KR", "long"), ("KR", "short"), ("US", "short")):
        client = _옛판_섞인_DB(country, horizon)
        client.conn.execute("UPDATE stocks SET status = 'excluded'")
        if country == "US":
            assert etf.load_recommended_us(client)[0] == {}  # type: ignore[arg-type]
        elif horizon == "long":
            assert accumulation.long_signal_ids(client, "KR") == set()  # type: ignore[arg-type]
        else:
            assert stress.load_basket(client, "KR")[0] == {}  # type: ignore[arg-type]


def test_장기_신호_표시는_마지막으로_계산한_날로() -> None:
    """그날 걸린 신호가 0건이면 MAX 는 옛 날짜다 — 리포트·스트레스와 같은 잣대 (25.824)."""
    from batch.jobs import accumulation

    client = _옛판_섞인_DB("KR", "long")
    client.conn.execute(
        "INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
        " calc_version, created_at) VALUES (1, '2026-09-30', 'long', 0, 1, '[]', 1, 't')"
    )
    assert accumulation.long_signal_ids(client, "KR") == set()  # type: ignore[arg-type]


def test_따라잡기와_ETF_겹침은_마지막으로_계산한_날로() -> None:
    """오늘 계산했는데 0건이면 옛 신호를 지금 것처럼 내지 않는다 (docs/infra.md 25.827·25.831, 교차검증 — 되돌려도 통과하던 곳)."""
    import sys
    from datetime import UTC, datetime
    from pathlib import Path

    from batch.jobs import etf

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import catchup_report

    for country in ("US", "KR"):
        db = _옛판_섞인_DB(country, "short")
        db.conn.execute(
            "INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
            " calc_version, created_at) VALUES (1, '2026-09-30', 'short', 0, 1, '[]', 1, 't')"
        )
        if country == "US":
            got, as_of = etf.load_recommended_us(db)  # type: ignore[arg-type]
            assert got == {} and as_of == "2026-09-30"
        else:
            facts = catchup_report.collect(db, datetime(2026, 9, 30, 4, 0, tzinfo=UTC))
            assert facts.top_signals == []
