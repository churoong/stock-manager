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


def test_단기_신호만_규칙마다_가상_체결한다() -> None:
    m = _장부()
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    # 종가 판정 스윙(25.1867)은 고가가 목표에 닿아도 종가(104)가 아래라 아직 진행 중
    assert sorted((t.rule, t.status, t.reason) for t in 그림자) == [("day_v1", "closed", "close"),
                                                                    ("swing_close_v1", "open", None),
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
    assert 집계["stats"]["swing_close_v1"]["open"] == 1 and "그림자 장부 swing_close_v1:" in out  # 25.1867


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


def test_설정_쓰기가_실패해도_실행은_성공으로_닫힌다(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    진짜 = db.set_setting
    monkeypatch.setattr(job.db, "set_setting", lambda c, k, v: (_ for _ in ()).throw(RuntimeError("설정쓰기본문"))
                        if k.startswith(job.SHADOW_KEY) else 진짜(c, k, v))  # fmt: skip
    assert job.run("KR") == 0
    assert m.conn.execute("SELECT status FROM batch_runs ORDER BY id DESC").fetchone()[0] == "success"
    # 공개 로그 — 예외 종류만(25.1866, 25.1865 교차검증 [권고])
    assert "설정쓰기본문" not in caplog.text and "적지 못함: RuntimeError" in caplog.text


def test_점수_보정표_경고는_공개_로그에_종류만_실행_기록에_본문(caplog: pytest.LogCaptureFixture) -> None:
    """25.1866 (25.1865 교차검증 [참고]) — 점수 보정표 경고가 예외 본문(질의·인자가 섞일 수 있다)을 공개 로그에 찍었다"""
    class 깨짐:
        def execute(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("보정본문")

    o = [job.oc.Outcome(**{**{f: None for f in job.oc.Outcome.__dataclass_fields__}, "as_of_date": "2026-10-01"})]
    표, 말 = job.calibrations(깨짐(), "KR", o)  # type: ignore[arg-type]
    assert 표 == [] and 말 is not None and "보정본문" in 말  # 실행 기록 warnings(DB)에는 본문
    assert "보정본문" not in caplog.text and "읽지 못해 뺐다: RuntimeError" in caplog.text


def test_점수_보정표_경고_본문은_실행_경로로_실행_기록에_든다(monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    """25.1868 (25.1866 교차검증 [권고]) — 직접 부르는 시험만 있어, run 이 그 말을 warnings 에 싣는지는 묶이지 않았다"""
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(job, "load_scores", lambda *a: (_ for _ in ()).throw(RuntimeError("보정본문" + "x" * 300)))
    assert job.run("KR") == 0
    기록 = json.loads(m.conn.execute("SELECT step_log FROM batch_runs ORDER BY id DESC").fetchone()[0])
    말 = [w for w in 기록["warnings"] if w.startswith("점수 보정표")]
    assert len(말) == 1 and "보정본문" in 말[0] and len(말[0]) < 250  # 본문은 DB 에 — 200자로 자른다
    assert "보정본문" not in caplog.text


def test_국내_상한가_시가는_잡_경로에서도_체결_못_함() -> None:
    m = _장부()
    m.conn.execute("UPDATE prices SET open = 130, high = 130, low = 130, close = 130, adj_close = 130"
                   " WHERE date = '2026-10-02'")  # fmt: skip
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert {(t.rule, t.status, t.reason) for t in 그림자} == {("day_v1", "no_entry", "상한가 시가"),
                                                            ("swing_v1", "no_entry", "상한가 시가"),
                                                            ("swing_close_v1", "no_entry", "상한가 시가")}  # fmt: skip


def test_분할_재수집_대기_종목은_빼고_중기_신호는_넣지_않는다() -> None:
    m = _장부()
    그림자: list[sh.Trade] = []
    job.compute(m, "KR", "2026-10-28", held={1}, shadow=(그림자, sh.Costs(0, 0, 0, 0), {}))  # type: ignore[arg-type]
    assert 그림자 == []
    # 중기 신호를 직접 넘기면 들어간다 — 단기 거르개는 compute 한 곳(25.1863)
    줄 = job.load_bars(m, [1], "2026-10-01")  # type: ignore[arg-type]
    중기 = [{"stock_id": 1, "as_of_date": "2026-10-01", "horizon": "mid", "market": "KOSPI"}]
    assert len(job.shadow_trades(중기, 줄, sh.Costs(0, 0, 0, 0), "KR")) == len(sh.RULES)


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


def _둘째_종목(m: MemClient) -> None:
    """끝까지 이어지는 같은 시장 종목 — 시장 마지막 날을 10-27 로 만든다"""
    m.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                   " VALUES (2, '000660', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')")  # fmt: skip
    d = date(2026, 10, 1)
    while d <= date(2026, 10, 27):
        if d.weekday() < 5:
            m.conn.execute(
                "INSERT INTO prices (stock_id, date, open, high, low, close, adj_close, currency, source, fetched_at)"
                " VALUES (2, ?, 50, 51, 49, 50, 50, 'KRW', 't', 't')",
                [d.isoformat()],
            )
        d += timedelta(days=1)


def test_잡_경로에서_시장보다_먼저_끊긴_종목은_끊김으로_닫는다() -> None:
    """25.1864 (25.1863 교차검증 [권고]) — compute 가 시장 마지막 날을 넘기지 않아도 시험이 통과했다(배선)"""
    m = _장부()
    m.conn.execute("UPDATE prices SET high = 103, low = 99 WHERE stock_id = 1 AND date >= '2026-10-02'")  # 스윙이 열린 채
    _둘째_종목(m)
    그림자: list[sh.Trade] = []
    메모: dict = {}
    job.compute(m, "KR", "2026-10-28", shadow=(그림자, sh.Costs(0, 0, 0, 0), 메모))  # type: ignore[arg-type]
    스윙 = next(t for t in 그림자 if t.rule == "swing_v1")
    assert (스윙.status, 스윙.reason, 스윙.exit_date) == ("closed", "끊김", "2026-10-03")
    assert 메모["bars_rows"] == 3  # 단기 신호 종목(1)만 읽는다


def test_운영_비용은_매도세_일정을_싣는다() -> None:
    """25.1864 — `shadow_costs` 가 일정을 빼도 시험이 통과했다(오늘 세율 하나로 되돌아감)"""
    from batch.services import backtest as bt

    m = _장부()
    c = job.shadow_costs(m, "KR", "2026-10-28")  # type: ignore[arg-type]
    assert c.tax_schedule == tuple(bt.KR_SELL_TAX_SCHEDULE)
    db.set_setting(m, "taxes", {"kr_transaction_pct": 0.2})  # type: ignore[arg-type]
    c2 = job.shadow_costs(m, "KR", "2026-10-28")  # type: ignore[arg-type]
    assert c2.tax_schedule[:-1] == tuple(bt.KR_SELL_TAX_SCHEDULE[:-1]) and c2.tax_schedule[-1][1] == 0.2


def test_실패한_회차는_지난_집계를_덮지_않고_실패라고_찍는다(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str],
                                                         caplog: pytest.LogCaptureFixture) -> None:  # fmt: skip
    m = _장부()
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    assert job.run("KR") == 0
    capsys.readouterr()
    monkeypatch.setattr(job, "load_bars", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("읽기 실패")))
    assert job.run("KR") == 0
    assert db.get_setting(m, f"{job.SHADOW_KEY}_kr")["stats"]["day_v1"]["n"] == 1  # type: ignore[arg-type]
    out = capsys.readouterr().out
    assert "그림자 장부: 실패 — RuntimeError" in out and "끝난 거래 없음" not in out
    # 공개 Actions 로그(ops_tee 안 거침)라 예외 본문은 찍지 않는다 — 종류만 (25.1865, 25.1864 교차검증 [권고])
    assert "읽기 실패" not in out and "읽기 실패" not in caplog.text and "그림자 장부 실패: RuntimeError" in caplog.text


def test_끊김_경계는_14일을_넘을_때() -> None:
    줄 = {1: [sh.Bar("2026-10-01", 99, 101, 98, 100), sh.Bar("2026-10-02", 100, 101, 98.5, 99)]}
    신호 = [{"stock_id": 1, "as_of_date": "2026-10-01", "horizon": "short", "market": "KOSPI"}]
    딱 = job.shadow_trades(신호, 줄, sh.Costs(0, 0, 0, 0), "KR", {"KOSPI": "2026-10-16"})  # 14일
    넘음 = job.shadow_trades(신호, 줄, sh.Costs(0, 0, 0, 0), "KR", {"KOSPI": "2026-10-17"})  # 15일
    assert next(t for t in 딱 if t.rule == "swing_v1").status == "open"
    assert next(t for t in 넘음 if t.rule == "swing_v1").reason == "끊김"
