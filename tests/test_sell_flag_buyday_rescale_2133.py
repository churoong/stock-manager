"""매수일을 새 판으로 다시 낸 점수도 잣대 이어 더하기에 넣는다 (docs/infra.md 25.2133, 11-04 결함 회차 [권고]).

예전 `CHAIN_HISTORY_SQL` 은 `as_of_date > 매수일` 이라 매수일 행이 하나도 들어오지 않았다. 매수일에 판이 둘(매매 점수를 낸
옛 판·그날을 다시 낸 새 판)이어도 매수일에 새 판으로 갈아타지 못해, 첫 새 판 점수 날이 잣대 바뀜 걸음이 되고 그 사이
하락이 영영 빠졌다 — 재무악화가 서지 않고 "같은 잣대의 점수가 아직 없음" 이라는 거짓 경고가 났다.
"""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from tests.test_sell_flag_scale_1405 import W, _C


def _준비(monkeypatch: pytest.MonkeyPatch) -> _C:
    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', 'A', 'KRW', 'active', 't', 't')")  # fmt: skip
    k.execute("INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
              " horizon, price_date, close, market_value, updated_at)"
              " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-07-01', 'long', '2026-10-14', 100, 1000, 't')")  # fmt: skip
    k.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " horizon, score_at_trade, snapshot_as_of, created_at, updated_at)"
              " VALUES (1, 'buy', '2026-07-01', 100, 10, 'KRW', 1, 'none', 'long', 72.0, '2026-07-01', 't', 't')")  # fmt: skip
    return c


def _점수(c: _C, d: str, v: float, ver: int) -> None:
    c.conn.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                   " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, ?, ?, 't')", [d, v, W, ver])  # fmt: skip


def test_매수일_새_판_점수로_갈아타_하락을_잇는다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    c = _준비(monkeypatch)
    _점수(c, "2026-07-01", 72.0, 10)  # 매매 점수를 낸 옛 판
    _점수(c, "2026-07-01", 60.0, 11)  # 판을 올리며 매수일을 새 판으로 다시 냄
    _점수(c, "2026-10-13", 40.0, 11)  # 같은 새 판으로 60 → 40, 20점 하락
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-13T23:27:00+00:00")
    job.run(date(2026, 10, 14))
    assert c.conn.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]
    assert "같은 잣대의 점수가 아직 없음" not in capsys.readouterr().out
    _점수(c, "2026-10-14", 40.0, 11)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-14T23:27:00+00:00")
    job.run(date(2026, 10, 15))  # 다음 날 — 예전엔 (0.0, 1) 로 판정을 마쳐 영영 서지 않았다
    assert c.conn.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]


def test_매수일에_판이_하나면_예전과_같다(monkeypatch: pytest.MonkeyPatch) -> None:
    """매수일 시작 행과 같은 잣대 행은 하락 0 으로 지나간다 — 매수일 행을 읽어도 이어 더한 값이 같다"""
    c = _준비(monkeypatch)
    _점수(c, "2026-07-01", 72.0, 10)
    _점수(c, "2026-10-13", 48.0, 10)
    _점수(c, "2026-10-14", 47.0, 11)
    _점수(c, "2026-10-15", 46.0, 11)
    monkeypatch.setattr(job.db, "now_iso", lambda: "2026-10-15T23:27:00+00:00")
    job.run(date(2026, 10, 16))  # 72→48(판 10) 24점 + 47→46(판 11) 1점 = 25점
    assert c.conn.execute("SELECT reason_code FROM sell_flags WHERE is_active = 1").fetchall() == [("재무악화",)]
