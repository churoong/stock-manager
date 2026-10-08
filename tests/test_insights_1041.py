"""역DCF·수급 흐름·점수 변화·닮은 종목·네 눈 (docs/analysis.md 15~19장, docs/infra.md 25.1041)."""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import insights as ins
from tests.test_portfolio_job import MemClient


def test_역DCF_는_고든_모형을_거꾸로_푼_것() -> None:
    r = ins.reverse_dcf(ep=0.08, discount=0.10)
    g = r["implied_growth"]
    # 가격 1 에 이익 0.08 — E(1+g)/(r−g) 가 다시 1 이 된다
    assert math.isclose(0.08 * (1 + g) / (0.10 - g), 1.0)
    assert math.isclose(r["per"], 12.5)
    assert ins.reverse_dcf(ep=-0.02, discount=0.1) is None  # 적자면 풀지 않는다


def _흐름(n: int, frgn: int, orgn: int, prsn: int) -> list[dict]:
    끝 = date(2026, 10, 7)
    return [{"date": (끝 - timedelta(days=i)).isoformat(), "frgn_net_amt": frgn, "orgn_net_amt": orgn,
             "prsn_net_amt": prsn, "short_vol_pct": 2.0 if i < 20 else 1.0, "credit_rmnd_pct": 3.0 - i * 0.01}
            for i in range(n)]  # fmt: skip


def test_수급_흐름() -> None:
    c = ins.flow_card(_흐름(60, -100, -50, 150))
    assert c["windows"]["20"] == {"days": 20, "frgn": -20.0, "orgn": -10.0, "prsn": 30.0}
    assert c["windows"]["60"]["frgn"] == -60.0
    assert c["frgn_streak"] == -60
    assert c["short"] == {"recent": 2.0, "before": 1.0}
    assert math.isclose(c["credit"]["before"], 2.8)
    줄 = ins.flow_lines(c)
    assert "20거래일 동안 개인만 순매수 — 외국인·기관은 순매도" in 줄 and "외국인 60거래일 연속 순매도" in 줄


def test_점수_변화와_닮은_종목() -> None:
    v = ins.score_change({"as_of": "b", "total": 70, "factors": {"growth": 80, "momentum": 40}},
                         {"as_of": "a", "total": 64, "factors": {"growth": 68, "momentum": 48}})  # fmt: skip
    assert v["delta"] == 6 and v["up"] == "growth" and v["down"] == "momentum"
    base = dict.fromkeys(ins.FACTORS, 50.0)
    profiles = {1: base, 2: {**base, "value": 52.0}, 3: {**base, "value": 80.0}, 4: {**base, "value": 49.0}}
    assert [s for s, _ in ins.twins(1, profiles)] == [4, 2, 3]
    assert ins.profile({"value": 1}) is None  # 다섯이 모두 있어야 한다


def test_네_눈() -> None:
    o = {"close": 100.0, "forecast": {"horizons": [{"months": 12, "expected": 108.0}]},
         "scenario": {"base": 90.0}, "consensus": {"median": 130.0}}  # fmt: skip
    a = ins.agreement(o)
    assert a["n"] == 3 and a["up"] == 2 and math.isclose(a["spread"], 0.40)
    assert ins.agreement({"close": 100.0, "consensus": {"median": 120.0}}) is None  # 하나로는 합의가 아니다


def test_일일_의견에_실린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    f = {"value": 50, "quality": 50, "growth": 50, "momentum": 50, "risk": 50}
    for sid in (1, 2, 3):
        c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 't', 't')", [sid, f"00000{sid}", f"종목{sid}"])  # fmt: skip
        for d, total, ff in (("2026-10-07", 60 + sid, {**f, "value": 50 + sid * 5}), ("2026-09-08", 55, f)):
            c.execute("INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                      " weights_json, rank_in_market, calc_version, created_at) VALUES (?, ?, ?, ?, 0, '{}', 1, 10, 't')",
                      [sid, d, total, json.dumps(ff)])  # fmt: skip
    for r in _흐름(60, -100, -50, 150):
        c.execute("INSERT INTO kr_flows (stock_id, date, frgn_net_amt, orgn_net_amt, prsn_net_amt, short_vol_pct,"
                  " credit_rmnd_pct, source, fetched_at) VALUES (1, ?, ?, ?, ?, ?, ?, 'kis', 't')",
                  [r["date"], r["frgn_net_amt"], r["orgn_net_amt"], r["prsn_net_amt"], r["short_vol_pct"],
                   r["credit_rmnd_pct"]])  # fmt: skip
    warnings: list[str] = []
    읽은: list[str] = []
    원래 = mem.execute

    def 기록(sql, args=None):  # noqa: ANN001, ANN202
        읽은.append(sql)
        return 원래(sql, args)

    mem.execute = 기록  # type: ignore[method-assign]
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), warnings))  # type: ignore[arg-type]
    assert warnings == []
    d1 = json.loads(c.execute("SELECT detail_json FROM stock_verdicts WHERE stock_id = 1").fetchone()[0])
    assert d1["outlook"]["flows"]["windows"]["20"]["frgn"] == -20.0
    assert [t["stock_id"] for t in d1["twins"]] == [2, 3]
    assert d1["score_change"]["delta"] == 6 and d1["score_change"]["since"] == "2026-09-08"
    assert any(r.startswith("점수 변화: 2026-09-08보다 +6.0") for r in d1["reasons"])
    # 반대 목소리의 5거래일 엇갈림도 같은 흐름으로 (따로 읽지 않는다)
    assert any(t.startswith("외국인·기관 5거래일 순매도") for t in d1["against"])
    from batch.jobs import daily

    assert daily.DIVERGENCE_FLOWS_SQL not in 읽은 and job.FLOWS_SQL in 읽은


def test_수급_창은_실제로_센_거래일로_말한다() -> None:
    """수집 초기엔 60거래일 창이 덜 찼다 — "60거래일" 이라 적으면 틀린다 (25.1042, 교차검증 감사)."""
    rows = _흐름(31, -100, -50, 150) + [{"date": "2026-08-01", "frgn_net_amt": None, "orgn_net_amt": None,
                                         "prsn_net_amt": None, "short_vol_pct": 1.0}]  # fmt: skip
    c = ins.flow_card(rows)
    assert c["windows"]["60"]["days"] == 31  # 공매도만 있는 행은 세지 않는다
    줄 = ins.flow_lines(c)
    assert any(x.startswith("31거래일 순매수") for x in 줄) and not any(x.startswith("60거래일") for x in 줄)
