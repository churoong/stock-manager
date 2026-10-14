"""유니버스 밖 보유 종목의 참고 분석이 매도 플래그를 본다 (docs/infra.md 25.1335, 분석 화면 감사 #1).

예전: `analyze_extra` 가 판정 입력에 플래그를 넘기지 않아 손절(적색) 플래그가 떠 있어도 "보유 유지 — 매도 플래그 없음" 이었다.
"""

from __future__ import annotations

import pytest

from batch.jobs import analyze_extra as job
from tests.test_analyze_extra_1018 import _mem


def test_적색_플래그가_있으면_보유_점검(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    c = mem.conn
    c.execute(
        "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
        " horizon, price_date, close, market_value, unrealized_pnl_krw, updated_at)"
        " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-06-01', 'long', '2026-10-13', 72, 720, -280, 't')"
    )
    c.execute(
        "INSERT INTO sell_flags (stock_id, as_of_date, level, reason_code, rationale_text, rationale_data, is_active,"
        " first_seen_date, created_at) VALUES (1, '2026-10-13', 'red', '손절', '종목1: 장기 손절선 -25% 에 닿았습니다', '{}', 1,"
        " '2026-10-13', 't')"
    )
    monkeypatch.setattr(job, "ensure_data", lambda c, country, rows: [])
    monkeypatch.setattr(job, "score_extra", lambda c, country, as_of, extra: {
        1: {"total": 55.5, "factors": {"value": 60}, "skip_reason": None, "rank": 400, "ranked": 870,
            "as_of": "2026-10-07"}})  # fmt: skip
    rows = mem.execute("SELECT id, ticker, country, name_ko AS name, asset_type FROM stocks WHERE id = 1").dicts()
    out = job.analyze(mem, "KR", rows, [])  # type: ignore[arg-type]
    assert out[1]["verdict"] == "check_holding", out[1]["headline"]  # 예전: hold
    assert "매도 플래그 없음" not in out[1]["headline"]
