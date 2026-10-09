"""재무 건전성 — Altman Z″ (docs/analysis.md 38장, docs/infra.md 25.1061).

계수 하나만 틀려도 "위험 구간" 이 "안전 구간" 으로 바뀐다. 그래서 손으로 계산한 값과 같은지, 기준(연결·최근 보고서)을 하나만
고르는지, 금융업·빈 재료에서 지어내지 않는지, 일일 의견에 실리는지를 묶는다.
"""

from __future__ import annotations

import json
import math
from datetime import date

from batch.core import db
from batch.jobs import verdicts as job
from batch.services import insights as ins
from tests.test_forecast_track_1037 import _시장
from tests.test_portfolio_job import MemClient


def _연간(**kw) -> dict:
    r = {"stock_id": 1, "fiscal_year": 2025, "report_code": "11011", "consolidated": 1, "report_date": "2026-03-15",
         "revenue": 1000, "operating_income": 80, "current_assets": 400, "current_liabilities": 250,
         "total_assets": 1000, "total_liabilities": 500, "retained_earnings": 300, "total_equity": 500}  # fmt: skip
    r.update(kw)
    return r


def test_손으로_계산한_Z() -> None:
    h = ins.health([_연간()], "KSIC 26")
    # 6.56·0.15 + 3.26·0.3 + 6.72·0.08 + 1.05·1.0 = 0.984 + 0.978 + 0.5376 + 1.05 = 3.5496
    assert math.isclose(h["z"], 3.550, abs_tol=1e-3) and h["zone"] == "safe" and h["debt_ratio"] == 1.0
    위험 = ins.health([_연간(retained_earnings=-300, operating_income=-50, current_assets=200)], "KSIC 26")
    assert 위험["zone"] == "distress" and 위험["z"] < ins.Z2_DISTRESS
    assert "Altman Z″ 3.55 — 안전 구간" in ins.health_line(h)


def test_기준을_하나만_고른다() -> None:
    행 = [_연간(consolidated=0, operating_income=-999), _연간(fiscal_year=2024, operating_income=999),
         _연간(report_date="2026-04-01", operating_income=90), _연간(report_code="11014", operating_income=-5)]  # fmt: skip
    h = ins.health(행, "KSIC 26")
    # 2025 연결 가운데 늦게 접수된 것(영업이익 90) — 별도·전년·분기 행은 보지 않는다
    assert h["fiscal_year"] == 2025 and h["consolidated"] and h["report_date"] == "2026-04-01"
    assert math.isclose(h["parts"][2], 0.09)


def test_금융업과_빈_재료는_지어내지_않는다() -> None:
    assert ins.health([_연간()], "KSIC 64")["z"] is None
    빈 = ins.health([_연간(retained_earnings=None)], "KSIC 26")
    assert 빈["z"] is None and "이익잉여금" in 빈["note"]
    assert ins.health([_연간(report_code="11012")], "KSIC 26") is None


def test_일일_의견에_실린다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    _시장(c, 기준가=100.0, 지금가=104.0)
    r = _연간()
    c.execute("INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated, report_date,"
              " receipt_no, currency, unit, revenue, operating_income, current_assets, current_liabilities,"
              " total_assets, total_liabilities, retained_earnings, total_equity, source, fetched_at)"
              " VALUES (1, 2025, '11011', 'A', 1, '2026-03-15', 'r1', 'KRW', 'KRW', ?, ?, ?, ?, ?, ?, ?, ?, 'dart', 't')",
              [r[k] for k in ("revenue", "operating_income", "current_assets", "current_liabilities", "total_assets",
                              "total_liabilities", "retained_earnings", "total_equity")])  # fmt: skip
    mem.batch(job.build_market(mem, "KR", date(2026, 10, 8), []))  # type: ignore[arg-type]
    d = json.loads(c.execute("SELECT detail_json FROM stock_verdicts").fetchone()[0])
    assert math.isclose(d["health"]["z"], 3.55, abs_tol=1e-3)
    assert any(x.startswith("재무 건전성(2025 사업보고서·연결)") for x in d["reasons"])
    # 같은 질의에 사업보고서 행이 섞여도 분기 추세는 그 행을 보지 않는다
    assert ins.quarter_trend([r]) is None
