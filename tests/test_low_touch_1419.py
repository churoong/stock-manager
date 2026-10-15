"""장중 감시가 꺼진 동안 전일 저가의 손절선 터치를 센다 — 기록만 (docs/infra.md 25.1419, docs/factors.md 12.30 P1)."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import portfolio as pf_job
from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_report_picks import SqliteClient
from tests.test_sell_flags import holding

뿌리 = Path(__file__).resolve().parent.parent


def test_스위치가_웹과_같다() -> None:
    글 = (뿌리 / "web/lib/intradaySwitch.ts").read_text(encoding="utf-8")
    값 = re.search(r"export const INTRADAY_ENABLED = (true|false);", 글)
    assert 값 is not None and (값.group(1) == "true") is job.INTRADAY_ENABLED


def test_판정식과_같은_잣대로_저가만_닿은_날() -> None:
    # 단기 −7%: 원가 1000, 평가 950(−5%) — 저가 90 / 종가 95 → 900(−10%) 닿음
    h = holding(cost=1000.0, market_value=950.0, horizon="short")
    assert sf.low_touch(h, 95.0, 90.0) is True
    assert sf.low_touch(h, 95.0, 94.0) is False  # 저가 −6.05%
    # 종가가 이미 손절선 아래면 플래그가 따로 있다 — 저가 터치로 세지 않는다
    assert sf.low_touch(holding(cost=1000.0, market_value=900.0, horizon="short"), 90.0, 85.0) is False
    # 권리락 계수를 원가에 곱한다(25.1197) — 0.9 면 원가 900, 저가 90/95 → 900 은 0%
    assert sf.low_touch(holding(cost=1000.0, market_value=950.0, horizon="short", rights_scale=0.9), 95.0, 90.0) is False
    # 모름: 저가 없음, 기업행위 20거래일
    assert sf.low_touch(h, 95.0, None) is None
    assert sf.low_touch(holding(cost=1000.0, market_value=950.0, corporate_action_recent=True), 95.0, 90.0) is None
    # 설정 문턱을 쓴다
    assert sf.low_touch(h, 95.0, 90.0, {"short": {"stop_pct": -12.0}}) is False


class _C(SqliteClient):
    def close(self) -> None:
        pass


def test_실행_기록에_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    c = _C()
    monkeypatch.setattr(db, "apply_migrations", lambda client: None)
    monkeypatch.setattr(job, "TursoClient", lambda: c)
    monkeypatch.setattr(pf_job, "쓰는_중", lambda cl: False)
    k = c.conn
    k.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, asset_type)"
        " VALUES (1, 'A', 'NASDAQ', 'US', 'A', 'USD', 'active', 't', 't', 'stock')"
    )
    k.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, updated_at)"
        " VALUES (1, 10, 'USD', 100, 1, 1000, 1000, '2026-10-01', 'short', '2026-10-14', 95, 950, 't')"
    )
    k.execute(
        "INSERT INTO prices (stock_id, date, open, high, low, close, volume, currency, source, fetched_at)"
        " VALUES (1, '2026-10-14', 96, 97, 90, 95, 1000, 'USD', 't', 't')"
    )
    job.run(date(2026, 10, 15))
    로그 = json.loads(k.execute("SELECT step_log FROM batch_runs WHERE job_name = 'sell_flags'").fetchone()[0])
    assert 로그["low_touch"] == {"holdings": 1, "touch": 1, "unknown": 0}
    # 판정은 종가 그대로 — 손절 플래그는 없다
    assert k.execute("SELECT COUNT(*) FROM sell_flags WHERE reason_code = '손절'").fetchone()[0] == 0
