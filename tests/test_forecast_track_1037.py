"""예측 성적표 (docs/analysis.md 11장, docs/infra.md 25.1037).

과감한 예측은 **맞혔는지를 함께 보일 때만** 쓸 만하다. 그래서 "같은 날 두 번 돌아도 두 번 세지 않는가",
"수정주가가 다시 매겨져도 두 끝이 같은 잣대인가", "표본이 적으면 비율을 말하지 않는가" 를 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import forecast_track as ft
from tests.test_portfolio_job import MemClient

오늘 = date(2026, 10, 8)


def test_달력으로_몇_달_앞() -> None:
    assert ft.months_back(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert ft.months_back(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert ft.months_back(date(2026, 10, 8), 12) == date(2025, 10, 8)


def test_한_칸_채점() -> None:
    pred = {"exp": 0.05, "lo68": -0.05, "hi68": 0.15, "lo90": -0.12, "hi90": 0.25}
    n, nr, i68, i90, dn, dh, err = ft.score_one(pred, 0.20)
    assert (n, nr, i68, i90, dn, dh) == (1, 1, 0, 1, 1, 1)
    assert math.isclose(err, abs(math.log(1.2) - math.log(1.05)))
    # 범위가 없는 예측(증권사 목표가)은 범위 분모에 들지 않는다
    assert ft.score_one({"exp": 0.3}, -0.1)[:6] == [1, 0, 0, 0, 1, 0]


def test_오늘_예측을_수익률로_적는다() -> None:
    o = {"close": 100.0, "forecast": {"horizons": [{"months": 1, "expected": 101.0, "low68": 92.0, "high68": 110.0}]},
         "consensus": {"median": 130.0}}  # fmt: skip
    m = ft.log_models(o)
    assert math.isclose(m["capm"]["1"]["exp"], 0.01) and math.isclose(m["capm"]["1"]["lo68"], -0.08)
    assert math.isclose(m["consensus"]["12"]["exp"], 0.30)
    assert ft.log_models({"close": None}) == {}


def test_표본이_적으면_비율을_말하지_않는다() -> None:
    track: dict = {}
    for _ in range(ft.MIN_SAMPLE - 1):
        ft.add(track, "capm", 1, ft.score_one({"exp": 0.01, "lo68": -0.1, "hi68": 0.1}, 0.02))
    assert "비율을 말하지 않습니다" in str(ft.line(track, scope="이 종목"))
    ft.add(track, "capm", 1, ft.score_one({"exp": 0.01, "lo68": -0.1, "hi68": 0.1}, 0.5))
    줄 = str(ft.line(track, scope="이 종목"))
    assert f"{ft.MIN_SAMPLE}건" in 줄 and "68% 범위 안 95%" in 줄 and "방향 적중 100%" in 줄


def _시장(c, *, 기준가: float, 지금가: float) -> None:
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-07', 60, '{}', 0, '{}', 1,"
              " 10, 't')")  # fmt: skip
    for d, px in (("2026-09-07", 기준가), ("2026-10-07", 지금가)):
        c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (1, ?, ?, ?, 'KRW', 't', 't')", [d, px, px])  # fmt: skip
    for d, px in (("2016-10-10", 2000.0), ("2026-10-07", 2600.0)):
        c.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('KOSPI', ?, ?, 't', 't')",
                  [d, px])  # fmt: skip
    # 한 달 전에 낸 예측 — 1개월 기대 +1%, 68% 범위 −5%~+8%
    c.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
              " VALUES ('2026-09-07', 1, 'KR', 999, ?, 't')",
              [json.dumps({"capm": {"1": {"exp": 0.01, "lo68": -0.05, "hi68": 0.08}}})])  # fmt: skip


def test_기간이_찬_예측을_한_번만_센다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    warnings: list[str] = []
    mem.batch(job.build_market(mem, "KR", 오늘, warnings))  # type: ignore[arg-type]
    assert warnings == []
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    # 실제 +4% — 68% 범위 안, 방향 맞음. 기록의 close(999)가 아니라 **계열의 그날 종가(100)** 로 잰다
    assert d["track"]["stock"]["capm"]["1"][:6] == [1, 1, 1, 0, 1, 1]
    assert d["track"]["market"]["capm"]["1"][0] == 1
    # 오늘 예측도 쌓았다(1·3·6·12개월)
    오늘기록 = json.loads(c.execute("SELECT models_json FROM forecast_log WHERE as_of_date = '2026-10-07'").fetchone()[0])
    assert sorted(오늘기록["capm"]) == ["1", "12", "3", "6"]
    # 같은 날 다시 돌아도 두 번 세지 않는다
    mem.batch(job.build_market(mem, "KR", 오늘, warnings))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["track"]["stock"]["capm"]["1"][0] == 1
    assert json.loads(c.execute("SELECT value FROM settings WHERE key = 'forecast_track_KR'").fetchone()[0])[
        "through"]["1"] == "2026-09-07"


def test_범위_밖이면_범위_안으로_세지_않는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=80.0)
    mem.batch(job.build_market(mem, "KR", 오늘, []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["track"]["stock"]["capm"]["1"][:6] == [1, 1, 0, 0, 1, 0]


def test_주말을_건너도_기록일을_모두_평가한다() -> None:
    """가장 최근 기록일 하나만 보면 달력 차이(주말)로 기록일 약 30% 가 영영 빠졌다 (25.1042, 교차검증 감사)."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    for d in ("2026-09-04", "2026-09-03"):  # 9-07(월) 앞의 목·금 — 예전 방식이면 9-07 하나만 본다
        c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                  " VALUES (1, ?, 100, 100, 'KRW', 't', 't')", [d])  # fmt: skip
        c.execute("INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
                  " VALUES (?, 1, 'KR', 100, ?, 't')",
                  [d, json.dumps({"capm": {"1": {"exp": 0.01, "lo68": -0.05, "hi68": 0.08}}})])  # fmt: skip
    db.set_setting(mem, job.track_key("KR"), {"through": {"1": "2026-09-02"},  # type: ignore[arg-type]
                                              "logged": ["2026-09-03", "2026-09-04", "2026-09-07"]})  # fmt: skip
    mem.batch(job.build_market(mem, "KR", 오늘, []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["track"]["stock"]["capm"]["1"][0] == 3
    상태 = json.loads(c.execute("SELECT value FROM settings WHERE key = 'forecast_track_KR'").fetchone()[0])
    assert 상태["through"]["1"] == "2026-09-07" and "2026-10-07" in 상태["logged"] and "_changed" not in 상태


def test_종목_누계는_의견을_지워도_남는다() -> None:
    """의견 행을 지우고 다시 쓰는 길(유니버스에서 빠짐·뒤 묶음 실패)에서 누계가 사라졌다 (25.1042, 교차검증 감사)."""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    mem.batch(job.build_market(mem, "KR", 오늘, []))  # type: ignore[arg-type]
    c.execute("DELETE FROM stock_verdicts")
    assert json.loads(c.execute("SELECT track_json FROM forecast_track WHERE stock_id = 1").fetchone()[0])["capm"]["1"][0] == 1
    mem.batch(job.build_market(mem, "KR", 오늘, []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert d["track"]["stock"]["capm"]["1"][0] == 1  # 다시 세지 않고, 잃지도 않는다
