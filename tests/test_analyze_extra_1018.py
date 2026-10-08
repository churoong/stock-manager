"""유니버스 밖 종목 참고 분석 (docs/analysis.md 8장, docs/infra.md 25.1018)."""

from __future__ import annotations

import json

import pytest

from batch.core import db
from batch.jobs import analyze_extra as job
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient


def test_참고_분석은_유니버스_기준_순위로_말한다() -> None:
    sc = {"as_of": "2026-10-07", "total": 61.0, "rank": 300, "ranked": 870, "factors": {"value": 70, "risk": 40}}
    r = vd.build(vd.Inputs(name="가", ticker="000001", score=sc, excluded_reason="시총미달"))
    assert r["verdict"] == "reference"
    assert r["headline"] == ("참고 분석 — 유니버스 밖(시총미달)이라 추천·신호 대상이 아닙니다. "
                             "참고 점수 61.0(유니버스 기준 300위 상당/870)")  # fmt: skip
    assert r["reasons"][0] == "참고 점수(유니버스 + 이 종목으로 계산) 61.0 · 유니버스 기준 300위 상당/870 (2026-10-07)"
    # 보유 중이면 보유 유지가 먼저다
    h = vd.build(vd.Inputs(name="가", ticker="000001", score=sc, excluded_reason="시총미달",
                           position={"quantity": 1, "pnl_pct": 1.0}))  # fmt: skip
    assert h["verdict"] == "hold"


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, t, reason in ((1, "111111", "시총미달"), (2, "222222", "스팩")):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSDAQ', 'KR', ?, 'KRW', 't', 't')", [sid, t, f"종목{sid}"])  # fmt: skip
        c.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, exclude_reason, market_cap,"
                  " currency, created_at) VALUES ('2026-10-05', ?, 0, ?, 30000000000, 'KRW', 't')", [sid, reason])  # fmt: skip
    c.execute("INSERT INTO analysis_requests VALUES (1, 't', 'requested', NULL, NULL)")
    return mem


def test_성격상_빠진_종목은_점수를_내지_않고_나머지는_참고_점수(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    받은: list = []
    monkeypatch.setattr(job, "ensure_data", lambda c, country, rows: 받은.append([r["id"] for r in rows]) or [])
    monkeypatch.setattr(job, "score_extra", lambda c, country, as_of, extra: {
        1: {"total": 55.5, "factors": {"value": 60, "quality": 50}, "skip_reason": None, "rank": 400, "ranked": 870,
            "as_of": "2026-10-07"}})  # fmt: skip
    rows = mem.execute("SELECT id, ticker, country, name_ko AS name, asset_type FROM stocks ORDER BY id").dicts()
    warnings: list[str] = []
    out = job.analyze(mem, "KR", rows, warnings)  # type: ignore[arg-type]
    assert 받은 == [[1]]  # 스팩(2)은 재무를 받지도 않는다
    assert out[1]["verdict"] == "reference" and "시총미달" in out[1]["headline"]
    assert out[2]["verdict"] == "undecided" and "스팩" in out[2]["headline"]
    stored = {r[0]: r[1] for r in mem.conn.execute("SELECT stock_id, verdict FROM stock_verdicts")}
    assert stored == {1: "reference", 2: "undecided"}
    # 다시 돌려도 한 행 (덮어쓴다)
    job.analyze(mem, "KR", rows, warnings)  # type: ignore[arg-type]
    assert mem.conn.execute("SELECT COUNT(*) FROM stock_verdicts").fetchone()[0] == 2


def test_요청이면_끝나고_알리되_조용시간에는_저장만(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from batch.notify import telegram

    mem = _mem()
    보낸: list[str] = []
    monkeypatch.setattr(telegram, "send", 보낸.append)
    row = {"id": 1, "ticker": "111111", "name": "종목1"}
    res = {"verdict": "reference", "headline": "참고 분석 — x", "against": ["외국인 순매도"]}

    class 낮(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 10, 8, 3, 0, tzinfo=UTC)  # 12:00 KST

    monkeypatch.setattr(job, "datetime", 낮)
    assert job.notify(mem, "KR", row, res, None) is True  # type: ignore[arg-type]
    assert 보낸 == ["🔎 분석 완료 — 종목1(111111)\n참고 분석 — x\n반대 목소리: 외국인 순매도"]
    a = mem.conn.execute("SELECT trigger_type, sent_at FROM alerts").fetchone()
    assert a[0] == "analysis" and a[1] is not None

    class 새벽(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206
            return datetime(2026, 10, 8, 17, 0, tzinfo=UTC)  # 02:00 KST

    monkeypatch.setattr(job, "datetime", 새벽)
    보낸.clear()
    assert job.notify(mem, "KR", row, res, None) is False  # type: ignore[arg-type]
    assert 보낸 == []
    # 같은 날 같은 종목은 한 행을 덮고, 조용시간이라 웹이 해제 뒤 보낸다(sent_at 비움)
    rows = mem.conn.execute("SELECT sent_at FROM alerts").fetchall()
    assert len(rows) == 1 and rows[0][0] is None


def test_요청_실행은_상태를_남기고_실패도_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    mem.close = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(job, "TursoClient", lambda: mem)

    def 터짐(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("재무 표 잠김")

    monkeypatch.setattr(job, "analyze", 터짐)
    알림: list = []
    monkeypatch.setattr(job, "notify", lambda c, country, row, res, error: 알림.append((res, error)) or True)
    assert job.run(stock_id=1, send=True) == 1
    assert mem.conn.execute("SELECT status, note FROM analysis_requests").fetchone() == ("failed", "재무 표 잠김")
    assert 알림 == [(None, "재무 표 잠김")]
    assert json.loads(mem.conn.execute("SELECT step_log FROM batch_runs WHERE job_name = 'analyze_extra'")
                      .fetchone()[0])["stocks"] == 1  # fmt: skip


def test_유니버스와_함께_계산하고_이_종목_결과만_순위와_함께(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from batch.jobs import scores as sj
    from batch.services import scoring as sc

    uni = [{"stock_id": i, "ticker": f"{i}", "market": "KOSPI", "sector": "x", "market_cap": 1, "currency": "KRW",
            "snapshot_date": "d", "market_cap_date": "d"} for i in (1, 2, 3)]  # fmt: skip
    monkeypatch.setattr(sj, "load_universe", lambda *a: uni)
    monkeypatch.setattr(sj, "load_series", lambda *a, **k: {})
    monkeypatch.setattr(sj, "series_window_start", lambda *a: None)
    for name in ("load_financials", "load_metrics", "load_benchmark_closes"):
        monkeypatch.setattr(sj, name, lambda *a, **k: {})
    monkeypatch.setattr(sj, "load_dividends", lambda *a, **k: {})
    monkeypatch.setattr(sj, "drop_stale_annual", lambda *a: {})
    monkeypatch.setattr(sj, "attach_unrecovered_rows", lambda *a: None)
    monkeypatch.setattr(sj, "load_pending_adjust", lambda *a: set())
    monkeypatch.setattr(sj, "load_weights", lambda *a: ({}, 0.0, []))
    monkeypatch.setattr(sj, "load_sentiments", lambda *a: ({}, None))
    본입력: list = []

    def build_inputs(rows, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        본입력.extend(r["stock_id"] for r in rows)
        return rows

    monkeypatch.setattr(sj, "build_inputs", build_inputs)
    점수표 = {1: 80.0, 2: 60.0, 3: 40.0, 9: 70.0}
    monkeypatch.setattr(sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=r["stock_id"], factor="value", score=점수표[r["stock_id"]]) for r in inputs])
    monkeypatch.setattr(sc, "total_score", lambda s, w, sentiment=None, sentiment_weight=0: SimpleNamespace(
        total=s["value"], skip_reason=None))
    extra = [{**uni[0], "stock_id": 9, "ticker": "9", "included": 0, "exclude_reason": "시총미달"}]
    out = job.score_extra(object(), "KR", "2026-10-07", extra)  # type: ignore[arg-type]
    assert 본입력 == [1, 2, 3, 9]  # 유니버스 전부 + 이 종목
    assert list(out) == [9] and out[9]["total"] == 70.0
    assert out[9]["rank"] == 2 and out[9]["ranked"] == 3  # 80 하나만 위


def test_이미_유니버스_종목이면_덮지_않고_그렇다고_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    mem.close = lambda: None  # type: ignore[method-assign]
    mem.conn.execute("UPDATE universe_members SET included = 1, exclude_reason = NULL WHERE stock_id = 1")
    mem.conn.execute("INSERT INTO stock_verdicts VALUES (1, 'KR', 'waiting', '신호 대기 — x', '{}', '[]', NULL, NULL, 't')")
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "ensure_data", lambda *a: pytest.fail("유니버스 종목의 재무를 따로 받지 않는다"))
    알림: list = []
    monkeypatch.setattr(job, "notify", lambda c, country, row, res, error: 알림.append((res, error)) or True)
    assert job.run(stock_id=1, send=True) == 0
    assert mem.conn.execute("SELECT verdict FROM stock_verdicts WHERE stock_id = 1").fetchone()[0] == "waiting"
    assert mem.conn.execute("SELECT status, note FROM analysis_requests").fetchone() == ("done", job.IN_UNIVERSE_NOTE)
    assert 알림 == [(None, None)]
