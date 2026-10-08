"""종목 분석 탭 레이더 (docs/analysis.md 20장, docs/infra.md 25.1043)."""

from __future__ import annotations

import json
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import insights as ins
from tests.test_portfolio_job import MemClient


def _e(sid: int, **kw) -> dict:  # noqa: ANN003
    return {"stock_id": sid, "ticker": f"T{sid}", "name": f"종목{sid}", "verdict": "waiting", **kw}


def test_세_목록의_순서() -> None:
    기준 = lambda d, met=False: {"items": [{"kind": "criterion", "label": "장기: 밴드", "price": 1, "dist": d,  # noqa: E731
                                         "need": "below", "met": met}]}  # fmt: skip
    r = ins.radar([
        _e(1, ladder=기준(-0.05)), _e(2, ladder=기준(0.01)), _e(3, ladder=기준(0.001, met=True)),
        _e(4, score_change={"delta": 3.0, "up": "growth"}), _e(5, score_change={"delta": 8.0}),
        _e(6, score_change={"delta": -2.0}),
        _e(7, agreement={"views": {"a": 0.1, "b": 0.2, "c": 0.05}, "up": 3, "n": 3}),
        _e(8, agreement={"views": {"a": 0.1, "b": 0.2, "c": 0.15}, "up": 3, "n": 3}),
        _e(9, agreement={"views": {"a": 0.1, "b": -0.2, "c": 0.15}, "up": 2, "n": 3}),
        _e(10, agreement={"views": {"a": 0.1, "b": 0.2}, "up": 2, "n": 2}),
    ])  # fmt: skip
    assert [x["stock_id"] for x in r["near"]] == [2, 1]  # 충족한 기준은 뺀다
    assert [x["stock_id"] for x in r["rising"]] == [5, 4]  # 내린 종목은 뺀다
    assert [x["stock_id"] for x in r["eyes"]] == [8, 7]  # 하나라도 내림이거나 눈이 둘이면 뺀다


def test_일일_의견이_시장마다_레이더를_둔다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    for d, total in (("2026-10-07", 70), ("2026-09-08", 60)):
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                  " weights_json, rank_in_market, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, '{}', 1, 10, 't')",
                  [d, total])  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    r = json.loads(c.execute("SELECT value FROM settings WHERE key = ?", [job.radar_key("KR")]).fetchone()[0])
    assert r["as_of"] == "2026-10-07" and r["rising"][0]["stock_id"] == 1 and r["rising"][0]["delta"] == 10.0
