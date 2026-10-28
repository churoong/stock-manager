"""그림자 장부 — 신호 성적표 실행이 단기 신호마다 단타·스윙을 가상 체결해 집계한다 (docs/paper.md 16장, docs/infra.md 25.1862)."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from batch.core import db
from batch.jobs import signal_outcomes as job
from batch.services import shadow as sh
from tests.test_portfolio_job import MemClient


def _장부() -> MemClient:
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    c = m.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    for 기간 in ("short", "mid"):
        c.execute(
            "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
            " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at)"
            " VALUES (1, '2026-10-01', ?, 'buy', 95, 100, 'KRW', '[]', 0, 'r', '{}', 1, 't')",
            [기간],
        )
    # 10-01 신호 날 종가 100, 다음 장 10-02 시가 100 → 고가 106(목표 105 닿음), 종가 104
    d = date(2026, 10, 1)
    for i, (o, h, lo, cl) in enumerate([(99, 101, 98, 100), (100, 106, 99, 104), (104, 105, 103, 104)]):
        c.execute(
            "INSERT INTO prices (stock_id, date, open, high, low, close, adj_close, currency, source, fetched_at)"
            " VALUES (1, ?, ?, ?, ?, ?, ?, 'KRW', 't', 't')",
            [(d + timedelta(days=i)).isoformat(), o, h, lo, cl, cl],
        )
    return m


def test_단기_신호만_두_규칙으로_가상_체결한다() -> None:
    m = _장부()
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert sorted((t.rule, t.status, t.reason) for t in 그림자) == [("day_v1", "closed", "close"),
                                                                    ("swing_v1", "closed", "target")]  # fmt: skip
    단타 = next(t for t in 그림자 if t.rule == "day_v1")
    assert (단타.entry_date, 단타.entry_price, 단타.exit_price, 단타.net) == ("2026-10-02", 100.0, 104.0, pytest.approx(0.04))


def test_실행이_집계를_설정과_실행_기록에_남기고_출력한다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    assert job.run("KR") == 0
    집계 = db.get_setting(m, f"{job.SHADOW_KEY}_kr")  # type: ignore[arg-type]
    assert 집계["stats"]["day_v1"]["n"] == 1 and 집계["stats"]["swing_v1"]["reasons"] == {"target": 1}
    assert 집계["costs"]["sell_tax_pct"] > 0  # 국내 매도세가 들어간다(기본값)
    기록 = json.loads(m.conn.execute("SELECT step_log FROM batch_runs ORDER BY id DESC").fetchone()[0])
    assert 기록["shadow"]["stats"]["swing_v1"]["n"] == 1
    out = capsys.readouterr().out
    assert "그림자 장부 day_v1: 끝 1건(1종목) · 승률 100%" in out and "그림자 장부 swing_v1:" in out


def test_분할_계수를_시가_고가_저가에도_곱한다() -> None:
    m = _장부()
    # 수정주가가 원 종가의 절반인 날(분할 전 구간) — 시가·고가·저가도 절반이어야 한다
    m.conn.execute("UPDATE prices SET adj_close = close / 2 WHERE date = '2026-10-02'")
    줄 = job.load_bars(m, [1], "2026-10-01")  # type: ignore[arg-type]
    둘째 = next(b for b in 줄[1] if b.date == "2026-10-02")
    assert (둘째.open, 둘째.high, 둘째.low, 둘째.close) == (50.0, 53.0, 49.5, 52.0)


def test_그림자_장부가_실패해도_성적표는_저장된다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.1863 (25.1862 교차검증 [필수]) — 시세 읽기 예외가 성적표 저장 전에 실행을 failed 로 닫았다"""
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job, "load_bars", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("읽기 실패")))
    assert job.run("KR") == 0
    상태, 기록 = m.conn.execute("SELECT status, step_log FROM batch_runs ORDER BY id DESC").fetchone()
    기록 = json.loads(기록)
    assert 상태 == "success" and m.conn.execute("SELECT COUNT(*) FROM signal_outcomes").fetchone()[0] >= 1
    assert "RuntimeError" in 기록["shadow"]["error"] and any("그림자 장부" in w for w in 기록["warnings"])


def test_설정_쓰기가_실패해도_실행은_성공으로_닫힌다(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    진짜 = db.set_setting
    monkeypatch.setattr(job.db, "set_setting", lambda c, k, v: (_ for _ in ()).throw(RuntimeError("x"))
                        if k.startswith(job.SHADOW_KEY) else 진짜(c, k, v))  # fmt: skip
    assert job.run("KR") == 0
    assert m.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC").fetchone()[0] == "success"


def test_국내_상한가_시가는_잡_경로에서도_체결_못_함() -> None:
    m = _장부()
    m.conn.execute("UPDATE prices SET open = 130, high = 130, low = 130, close = 130, adj_close = 130"
                   " WHERE date = '2026-10-02'")  # fmt: skip
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert {(t.rule, t.status, t.reason) for t in 그림자} == {("day_v1", "no_entry", "상한가 시가"),
                                                            ("swing_v1", "no_entry", "상한가 시가")}  # fmt: skip


def test_분할_재수집_대기_종목은_빼고_중기_신호는_넣지_않는다() -> None:
    m = _장부()
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", held={1}, shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert 그림자 == []
    # 중기 신호를 직접 넘기면 들어간다 — 단기 거르개는 compute 한 곳(25.1863)
    줄 = job.load_bars(m, [1], "2026-10-01")  # type: ignore[arg-type]
    중기 = [{"stock_id": 1, "as_of_date": "2026-10-01", "horizon": "mid", "market": "KOSPI"}]
    assert len(job.shadow_trades(중기, 줄, sh.Costs(0, 0, 0, 0), "KR")) == 2


def test_시가가_빈_날은_빼고_다음_날로_넘어간다() -> None:
    m = _장부()
    m.conn.execute("UPDATE prices SET open = NULL WHERE date = '2026-10-02'")
    줄 = job.load_bars(m, [1], "2026-10-01")  # type: ignore[arg-type]
    assert [b.date for b in 줄[1]] == ["2026-10-01", "2026-10-03"]
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert next(t for t in 그림자 if t.rule == "day_v1").entry_date == "2026-10-03"


def test_시장보다_먼저_끊긴_종목은_끊김으로_닫는다() -> None:
    줄 = {1: [sh.Bar("2026-10-01", 99, 101, 98, 100), sh.Bar("2026-10-02", 100, 101, 98.5, 99)]}
    신호 = [{"stock_id": 1, "as_of_date": "2026-10-01", "horizon": "short", "market": "KOSPI"}]
    ts = job.shadow_trades(신호, 줄, sh.Costs(0, 0, 0, 0), "KR", {"KOSPI": "2026-10-27"})
    assert next(t for t in ts if t.rule == "swing_v1").reason == "끊김"
    ts = job.shadow_trades(신호, 줄, sh.Costs(0, 0, 0, 0), "KR", {"KOSPI": "2026-10-08"})  # 아직 끊긴 게 아니다
    assert next(t for t in ts if t.rule == "swing_v1").status == "open"
