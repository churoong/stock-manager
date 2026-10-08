"""종목 분석 의견 (docs/analysis.md, docs/infra.md 25.1016)."""

from __future__ import annotations

import json
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient

SCORE = {"as_of": "2026-10-07", "total": 72.5, "rank": 12, "ranked": 870,
         "factors": {"value": 60, "quality": 80, "growth": None, "momentum": 70, "risk": 45}, "skip_reason": None}
CHECKS = [
    {"horizon": "short", "as_of": "2026-10-07", "passed": False, "failed_count": 3, "rows": []},
    {"horizon": "mid", "as_of": "2026-10-07", "passed": False, "failed_count": 1, "rows": [
        {"label": "영업이익 증가율", "display": "+4.0%", "threshold": "≥ 10%", "source": "financials",
         "as_of": "2026-08-14", "passed": False},
        {"label": "점수", "display": "72.5", "threshold": "≥ 60", "source": "scores", "as_of": "2026-10-07",
         "passed": True}]},
    {"horizon": "long", "as_of": "2026-10-07", "passed": False, "failed_count": 1, "rows": []},
]  # fmt: skip
SIGNAL = {"horizon": "short", "as_of": "2026-10-07", "buy_zone_low": 60000, "buy_zone_high": 62000,
          "target_price": 66000, "stop_price": 55800}  # fmt: skip


def test_결론은_규칙의_사실로만_위에서부터() -> None:
    기본 = dict(name="가", ticker="000001", score=SCORE, checks=CHECKS)
    # 종합 분석(예전 "신호 대기", 25.1024) — 사실 먼저, 가장 가까운 기간(탈락 1개, 단기→중기→장기 순으로 중기)은 끝에
    w = vd.build(vd.Inputs(**기본))
    assert w["verdict"] == "waiting" and w["headline"] == (
        "종합 분석 — 점수 72.5(시장 12위/870, 상위 1%) · 매수 신호까지 중기 기준 1개 남음 (2026-10-07)")
    assert "빠진 기준: 영업이익 증가율 — 지금 +4.0% · 문턱 ≥ 10%" in w["reasons"]
    assert "가장 강한 팩터 퀄리티 80 · 가장 약한 팩터 리스크 45" in w["reasons"]
    # 매수 검토
    b = vd.build(vd.Inputs(**기본, signals=[SIGNAL]))
    assert b["verdict"] == "consider_buy"
    assert b["headline"] == "매수 검토 — 단기 매수 신호 (2026-10-07). 권장 매수 구간 60,000~62,000 · 목표 66,000 · 손절 55,800"
    # 보유 + 플래그 → 보유 점검이 신호보다 먼저
    flag = {"level": "red", "rationale_text": "손절선 -7% 터치", "as_of": "2026-10-07"}
    c = vd.build(vd.Inputs(**기본, signals=[SIGNAL], position={"quantity": 1, "pnl_pct": -8.0}, flags=[flag]))
    assert c["verdict"] == "check_holding" and c["headline"] == "보유 점검 — 매도 플래그 적색: 손절선 -7% 터치"
    # 녹색 플래그는 점검이 아니다
    h = vd.build(vd.Inputs(**기본, position={"quantity": 1, "pnl_pct": 3.25, "price_date": "2026-10-07"},
                           flags=[{**flag, "level": "green"}]))  # fmt: skip
    assert h["verdict"] == "hold" and h["headline"] == "보유 유지 — 매도 플래그 없음 · 평가손익률 +3.2% (2026-10-07)"
    # 점수가 없으면 보류
    u = vd.build(vd.Inputs(name="나", ticker="000002", score={**SCORE, "total": None, "skip_reason": "재무 없음"}))
    assert u["verdict"] == "undecided" and u["headline"] == "판단 보류 — 재무 없음"


def test_반대_목소리는_결론을_바꾸지_않고_붙는다() -> None:
    a = [{"text": "외국인·기관 5거래일 순매도", "evidence": {"label": "x", "display": "-1억", "threshold": "<0",
                                                        "source": "kr_flows", "as_of": "2026-10-07"}}]  # fmt: skip
    b = vd.build(vd.Inputs(name="가", ticker="000001", score=SCORE, checks=CHECKS, signals=[SIGNAL], against=a))
    assert b["verdict"] == "consider_buy" and b["headline"].endswith(" · 반대 목소리 1개")
    assert b["against"] == ["외국인·기관 5거래일 순매도"] and b["evidence"][-1]["source"] == "kr_flows"


def test_실제_스키마로_시장_전체() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    for sid, t in ((1, "005930"), (2, "000660"), (3, "035420")):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 't', 't')", [sid, t, f"종목{sid}"])  # fmt: skip
        c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                  " weights_json, rank_in_market, calc_version, created_at) VALUES (?, '2026-10-07', ?, ?, 0, '{}', ?,"
                  " 10, 't')", [sid, 80 - sid, json.dumps({"value": 50, "quality": 60}), sid])  # fmt: skip
    c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
              " tranche_plan, size_reduction, rationale_text, rationale_data, calc_version, created_at, target_price,"
              " stop_price) VALUES (1, '2026-10-07', 'short', 'buy', 100, 110, 'KRW', '[]', 0, 'r', '{}', 'v', 't',"
              " 120, 93)")  # fmt: skip
    c.execute("INSERT INTO signal_checks (stock_id, as_of_date, horizon, passed, failed_count, checks_json,"
              " calc_version, created_at) VALUES (3, '2026-10-07', 'mid', 0, 2, '[]', 1, 't')")  # fmt: skip
    c.execute("INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
              " unrealized_pnl_krw, price_date, updated_at) VALUES (2, 10, 'KRW', 1, 1, 100, 100, '2026-01-02', 5,"
              " '2026-10-07', 't')")  # fmt: skip
    for d in ("2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"):
        c.execute("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, source, fetched_at)"
                  " VALUES (3, ?, -100, -50, 'kis', 't')", [d])  # fmt: skip
    c.execute("INSERT INTO disclosure_reaction (type, label, keywords, priority, n, mean_pct, median_pct, pos_pct,"
              " window_days, since, as_of, computed_at) VALUES ('rights', '유상증자', '[]', 2, 120, -2.0, -1.5, 40,"
              " 5, 's', 'a', 't')")  # fmt: skip
    c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, is_material, source,"
              " fetched_at) VALUES (3, 'c3', 'r9', '주요사항보고서(유상증자결정)', '2026-10-05', 1, 't', 't')")  # fmt: skip
    warnings: list[str] = []
    rows = job.build_market(mem, "KR", date(2026, 10, 8), warnings)  # type: ignore[arg-type]
    assert warnings == []
    mem.batch(rows)  # type: ignore[arg-type]
    got = {r[0]: r for r in c.execute("SELECT stock_id, verdict, headline, detail_json FROM stock_verdicts")}
    assert got[1][1] == "consider_buy" and got[2][1] == "hold" and got[3][1] == "waiting"
    d3 = json.loads(got[3][3])
    assert d3["nearest"] == {"horizon": "mid", "failed_count": 2, "as_of": "2026-10-07"}
    assert any(t.startswith("외국인·기관 5거래일 순매도") for t in d3["against"])
    assert any("유상증자" in t and "공시일 반응 포함" in t for t in d3["against"])
    # 다시 돌려도 시장 행을 통째로 바꾼다(겹치지 않는다)
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), warnings))  # type: ignore[arg-type]
    assert c.execute("SELECT COUNT(*) FROM stock_verdicts").fetchone()[0] == 3
