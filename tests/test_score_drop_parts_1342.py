"""매도 플래그 ② "종합 점수 하락" 근거에 팩터별 기여 — 사실만, 합이 맞을 때만 (docs/infra.md 25.1342, 21회차 P3 1단계)."""

from __future__ import annotations

import json

from batch.jobs import sell_flags as job
from batch.services import sell_flags as sf
from tests.test_sell_flags import TODAY, holding

가중 = json.dumps({"value": 20, "quality": 20, "growth": 20, "momentum": 20, "risk": 20})


def _행(total: float, f: dict, sent: float | None = None, sw: float = 0) -> dict:
    return {"total_score": total, "factor_scores": json.dumps(f), "weights_json": 가중, "sentiment_score": sent,
            "sentiment_weight_used": sw}  # fmt: skip


def test_모멘텀·리스크로_빠진_하락을_팩터별로() -> None:
    전 = _행(70.0, {"value": 60, "quality": 70, "growth": 70, "momentum": 80, "risk": 70})
    지금 = _행(50.0, {"value": 75, "quality": 68, "growth": 67, "momentum": 20, "risk": 20})
    부분 = job._하락_기여(전, 지금)
    assert 부분 is not None and abs(sum(부분.values()) - (-20.0)) < 1e-9
    h = holding(score_at_buy=70.0, score_now=50.0, score_drop_parts=부분)
    [f] = sf.evaluate(h, TODAY)
    글 = f.criteria[0]["display"]
    assert "팩터별 기여: 모멘텀 -12.0 · 리스크 -10.0 · 성장 -0.6 · 퀄리티 -0.4 · 밸류 +3.0" in 글
    assert "재무" not in 글.split("팩터별 기여")[1]  # 인과 문장·"재무" 묶음 없음
    assert f.criteria[0]["label"] == "종합 점수 하락"  # 이름표 그대로 — [확인] 유지(25.564)


def test_합이_실제_차와_어긋나면_분해를_적지_않는다() -> None:
    전 = _행(70.0, {"value": 60, "quality": 70, "growth": 70, "momentum": 80, "risk": 70})
    지금 = _행(50.0, {"value": 60, "quality": 70, "growth": 70, "momentum": 80, "risk": 70})  # 팩터는 그대로(자르기 등)
    assert job._하락_기여(전, 지금) is None
    assert job._하락_기여(None, 지금) is None


def test_분해가_없으면_예전_글() -> None:
    [f] = sf.evaluate(holding(score_at_buy=70.0, score_now=50.0), TODAY)
    assert "팩터별 기여" not in f.criteria[0]["display"]


def test_잡이_두_점수_행의_분해를_넘긴다() -> None:
    """배선 — 질의에 팩터·감성 칸이 빠지면 운영에서는 아무것도 적지 않는다."""
    from batch.core import db
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
              " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
              " horizon, price_date, close, market_value, updated_at)"
              " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-09-16', 100, 1000, 't')")  # fmt: skip
    c.execute("INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
              " snapshot_as_of, score_at_trade, created_at, updated_at)"
              " VALUES (1, 'buy', '2026-09-01', 100, 10, 'KRW', 1, 'none', '2026-08-31', 70, 't', 't')")  # fmt: skip
    for day, total, f in (("2026-08-31", 70.0, {"value": 60, "quality": 70, "growth": 70, "momentum": 80, "risk": 70}),
                          ("2026-09-15", 50.0, {"value": 75, "quality": 68, "growth": 67, "momentum": 20, "risk": 20})):
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                  " weights_json, calc_version, created_at) VALUES (1, ?, ?, ?, 0, ?, 1, 't')",
                  [day, total, json.dumps(f), 가중])  # fmt: skip
    [h], _ = job.load_holdings(mem)  # type: ignore[arg-type]
    assert h.score_drop_parts is not None and h.score_drop_parts["momentum"] == -12.0
