"""포트폴리오 주의는 오늘 상태가 앞, 늘 같은 안내가 뒤 (docs/infra.md 25.1820).

일일 리포트는 하위 작업마다 앞 3줄만 싣는다(`daily._하위_경고`). ETF 세금 칸·수수료율 미설정 같은 지난 매매·설정의 안내가
앞자리를 차지해 오늘의 환율 13일 묵음이 "… 외 N건" 으로만 보였다(결함 회차 재현을 옮김).
"""

import json
from datetime import date

from batch.core import db
from batch.jobs import daily
from batch.jobs import portfolio as job
from tests.test_portfolio_job import MemClient


def test_환율_묵음이_리포트_세_줄_안에_든다(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(MemClient, "__enter__", lambda self: self, raising=False)
    monkeypatch.setattr(MemClient, "__exit__", lambda self, *a: None, raising=False)
    m = MemClient()
    db.apply_migrations(m)  # type: ignore[arg-type]
    monkeypatch.setattr(job, "TursoClient", lambda: m)
    monkeypatch.setattr(daily, "TursoClient", lambda: m)
    monkeypatch.setattr(job.cal, "user_today", lambda: date(2026, 10, 12))
    c = m.conn
    ins = "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, asset_type) VALUES (?,?,?,?,?,?, 'active','t','t',?)"
    c.execute(ins, [1, "005930", "KOSPI", "KR", "주식", "KRW", "stock"])
    c.execute(ins, [2, "AAPL", "NASDAQ", "US", "Apple", "USD", "stock"])
    c.execute(ins, [3, "069500", "KOSPI", "KR", "ETF", "KRW", "etf"])

    def px(s, d, v, cur):
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?,?,?,?, 't','t')",
            [s, d, v, cur],
        )

    for d in ("2026-09-01", "2026-09-10", "2026-09-22", "2026-09-23"):
        px(1, d, 70000, "KRW")
    for d in ("2026-09-01", "2026-09-10"):
        px(3, d, 30000, "KRW")
    for d in ("2026-09-20", "2026-10-08", "2026-10-09"):
        px(2, d, 200.0, "USD")
    for d in ("2026-09-20", "2026-09-29"):
        c.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, 1390, 't','t')", [d]
        )
    tid = [0]

    def tr(s, side, d, p, q, cur, fee, tax, fx=None):
        tid[0] += 1
        c.execute(
            "INSERT INTO trades (id, stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source, fee, tax, horizon, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'long','t','2026-10-12T00:00:00')",
            [tid[0], s, side, d, p, q, cur, fx or 1.0, "manual", fee, tax],
        )

    tr(1, "buy", "2026-09-01", 70000, 10, "KRW", 0, None)
    tr(3, "buy", "2026-09-01", 30000, 10, "KRW", 0, None)
    tr(3, "sell", "2026-09-10", 30000, 5, "KRW", 0, None)
    tr(2, "buy", "2026-09-21", 200.0, 10, "USD", None, None, 1390.0)
    since = db.now_iso()
    assert job.run() == 0
    기록 = json.loads(
        c.execute("SELECT step_log FROM batch_runs WHERE job_name = ? ORDER BY id DESC", [job.JOB_NAME]).fetchone()[0]
    )
    줄 = 기록["warnings"]
    assert "환율이 13일 전" in 줄[1] and 줄[2] == job.ETF_GAIN_TAX_NOTE and "수수료율이 비어" in 줄[3]
    올라감 = daily._하위_경고([job.JOB_NAME], since)
    assert any("USD 환율이 13일 전" in w for w in 올라감[:3])
    assert 올라감[-1] == "portfolio: … 외 1건 (배치 기록 참고)"


def test_지표_없는_종목은_리포트_저하_줄로(monkeypatch) -> None:  # noqa: ANN001
    """새 편입 지표 단계(25.1819)가 못 돈 날 점수가 리스크 팩터 없이 나간 것을 리포트가 말한다 (25.1820)"""
    from tests.test_scores_degraded_1220 import _기록, _문맥
    from tests.test_signal_outcomes_version import 메모리DB

    mem = 메모리DB()
    _기록(mem, "scores", {"missing_metrics": 12})
    _기록(mem, "signals", {})
    _문맥(monkeypatch, mem)
    got = daily._계산_저하("scores", "signals", "2026-10-08T00:00:00+00:00")
    assert got == ["점수: 성과 지표가 없는 12종목은 리스크 팩터 없이 계산했습니다 (scores 배치 기록 참고)"]
