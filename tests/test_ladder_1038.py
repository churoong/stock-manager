"""확률로 말하기·가격 사다리 (docs/analysis.md 12장, docs/infra.md 25.1038).

확률 식이 틀리면 "오를 확률 70%" 같은 문장이 자신 있게 틀린다. 그래서 **식이 알려진 특수한 경우와 같은가**
(표류 0 의 반사 원리, 거리 비)와 "사다리가 이미 있는 가격만 세우는가" 를 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date

from batch.core import db
from batch.jobs import signals as sig_job
from batch.jobs import verdicts as job
from batch.services import signals as sg
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient


def _er0(sigma: float) -> float:
    """로그 표류 ν 가 0 이 되는 기대수익 — ln(1+er) = σ²/2."""
    return math.exp(sigma * sigma / 2) - 1


def test_오를_확률은_범위와_같은_가정() -> None:
    f = vd.forecast(close=100.0, beta=1.0, sigma=0.3, market={"annual": 0.08, "years": 10, "since": "a", "until": "b"},
                    rf=0.0)  # fmt: skip
    for h in f["horizons"]:
        t = h["months"] / 12
        # 68% 범위 하단 아래일 확률 = 16% (같은 로그정규)
        assert math.isclose(1 - vd.prob_above(h["low68"] / 100, f["er"], 0.3, t), 0.1587, abs_tol=1e-3)
        assert 0 < h["p_drop"] < h["p_up"] < 1
    assert math.isclose(vd.prob_above(1.0, _er0(0.3), 0.3, 1.0), 0.5, abs_tol=1e-9)


def test_도달_확률은_표류_0_이면_반사_원리() -> None:
    """ν=0 이면 P(t 안에 닿음) = 2·P(t 뒤 그 너머) (반사 원리)."""
    er = _er0(0.25)
    for ratio in (1.15, 0.85):
        끝 = vd.prob_above(ratio, er, 0.25, 0.5) if ratio > 1 else 1 - vd.prob_above(ratio, er, 0.25, 0.5)
        assert math.isclose(vd.touch_prob(ratio, er, 0.25, 0.5), 2 * 끝, rel_tol=1e-9)
    # 표류가 있어도 "언젠가 닿음" 은 "끝에 그 너머" 보다 크거나 같다
    assert vd.touch_prob(1.2, 0.1, 0.3, 1.0) >= vd.prob_above(1.2, 0.1, 0.3, 1.0)


def test_아래_장벽은_표류를_뒤집은_위_장벽과_같다() -> None:
    """P(최저 ≤ b; 표류 ν) = P(최고 ≥ −b; 표류 −ν). 표류가 0 이 아닐 때 아래 장벽 식의 부호를 묶는다."""
    s = 0.3
    for nu in (0.15, -0.1):
        er = math.exp(nu + s * s / 2) - 1
        er_뒤집음 = math.exp(-nu + s * s / 2) - 1
        assert math.isclose(vd.touch_prob(0.8, er, s, 1.0), vd.touch_prob(1 / 0.8, er_뒤집음, s, 1.0), rel_tol=1e-9)


def test_목표_손절_경주() -> None:
    er = _er0(0.3)
    u, d = math.log(1.1), -math.log(0.93)
    assert math.isclose(vd.race_prob(1.1, 0.93, er, 0.3), d / (u + d), rel_tol=1e-6)
    # 기대수익이 클수록 목표가 먼저
    assert vd.race_prob(1.1, 0.93, 0.3, 0.3) > vd.race_prob(1.1, 0.93, er, 0.3) > vd.race_prob(1.1, 0.93, -0.2, 0.3)
    assert vd.race_prob(0.9, 0.8, 0.1, 0.3) is None  # 목표가가 지금 아래면 경주가 아니다


def test_판정표_기준을_가격으로_푼다() -> None:
    closes = [100.0] * 19 + [110.0]
    inp = sg.SignalInput(stock_id=1, ticker="A", name="A", market="KOSPI", closes=closes, pbr_now=0.8,
                         band=sg.Band(p20=0.5, p30=0.6, p50=0.9, p80=1.2, sample=700), band_close=110.0)  # fmt: skip
    lv = sg.price_levels(inp)
    ma20 = sum(closes) / 20
    assert [x["price"] for x in lv["short"]] == [ma20, ma20 * (1 + sg.MAX_EXTENSION)]
    assert [x["need"] for x in lv["short"]] == ["above", "below"]
    # PBR 0.6(30% 분위) × 주당순자산(110 / 0.8)
    assert math.isclose(lv["long"][0]["price"], 0.6 * 110 / 0.8) and lv["long"][0]["need"] == "below"
    assert lv["mid"] == []
    # 판정표 행에 함께 실린다
    rows = sig_job.check_rows(inp, "2026-10-07", "now")
    assert json.loads(next(r for r in rows if r[2] == "long")[8])[0]["price"] == lv["long"][0]["price"]


def test_사다리는_이미_있는_가격만_세운다() -> None:
    outlook = vd.outlook(close=100.0, close_date="2026-10-07", currency="KRW",
                         momentum={"high_52w_proximity": 0.8}, risk={"volatility_ann": 0.3, "beta": 1.0},
                         band={"current_value": 1.0, "band_close": 100.0, "p20": 0.7, "p50": 1.0, "p80": 1.4},
                         opinions=[{"date": "2026-10-01", "broker": "가", "target_price": 130}], today=date(2026, 10, 8),
                         market={"annual": 0.08, "years": 10, "since": "a", "until": "b"}, rf=0.03)  # fmt: skip
    signals = [{"horizon": "short", "as_of": "2026-10-07", "buy_zone_low": 97, "buy_zone_high": 101,
                "target_price": 110, "stop_price": 93}]  # fmt: skip
    checks = [{"horizon": "long", "passed": False, "failed_count": 1, "rows": [],
               "levels": [{"label": "장기: 밸류에이션 밴드 하단", "price": 80.0, "need": "below"}]}]  # fmt: skip
    b = vd.build(vd.Inputs(name="가", ticker="1", score={"as_of": "d", "total": 60}, signals=signals, checks=checks,
                           outlook=outlook))  # fmt: skip
    lad = b["outlook"]["ladder"]
    가격 = [it["price"] for it in lad["items"]]
    assert 가격 == sorted(가격, reverse=True)
    이름 = {it["label"] for it in lad["items"]}
    assert {"52주 고점(종가)", "증권사 목표가 중앙값(1곳)", "단기 신호 목표가", "단기 신호 손절가",
            "장기: 밸류에이션 밴드 하단"} <= 이름  # fmt: skip
    고점 = next(it for it in lad["items"] if it["kind"] == "high")
    assert math.isclose(고점["price"], 125.0) and 0 < 고점["touch"]["3"] < 고점["touch"]["12"] < 1
    기준 = next(it for it in lad["items"] if it["kind"] == "criterion")
    assert 기준["met"] is False and math.isclose(기준["dist"], -0.2)
    assert 0 < lad["race"]["p"] < 1 and any("먼저 닿을 확률" in r for r in b["reasons"])
    assert any(e["label"] == "가격 기준: 장기: 밸류에이션 밴드 하단" for e in b["evidence"])


def test_일일_의견이_판정표의_가격_기준을_읽는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
              " weights_json, rank_in_market, calc_version, created_at) VALUES (1, '2026-10-07', 60, '{}', 0, '{}', 1,"
              " 10, 't')")  # fmt: skip
    c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
              " VALUES (1, '2026-10-07', 100, 100, 'KRW', 't', 't')")  # fmt: skip
    c.execute("INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
              " calc_version, created_at, levels_json) VALUES (1, '2026-10-07', 'short', 0, 1, '[]', 1, 't', ?)",
              [json.dumps([{"label": "단기: 추세 위 (20일선)", "price": 104.0, "need": "above"}])])  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    it = d["outlook"]["ladder"]["items"][0]
    assert it["label"] == "단기: 추세 위 (20일선)" and it["met"] is False and math.isclose(it["dist"], 0.04)
