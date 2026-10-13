"""짧은 사업연도(분할 첫해·결산월 변경)는 12개월 해와 견주지 않는다 (docs/infra.md 25.1286, 재무 감사 재현)."""

from __future__ import annotations

from batch.jobs import backtest as bj
from batch.services import scoring as sc
from batch.sources import dart


def test_손익_행의_기간을_읽는다() -> None:
    rows = [{"sj_div": "BS", "thstrm_dt": "2021.12.31 현재"},
            {"sj_div": "IS", "thstrm_dt": "2021.11.02 ~ 2021.12.31"}]  # fmt: skip
    assert dart.period_days_of(rows) == 60
    assert dart.period_days_of([{"sj_div": "CIS", "thstrm_dt": "2025.01.01 ~ 2025.12.31"}]) == 365
    assert dart.period_days_of([{"sj_div": "IS", "thstrm_dt": ""}]) is None


def test_짧은_해_판정과_손익_비우기() -> None:
    assert sc.short_year({"period_days": 60}) and not sc.short_year({"period_days": 365})
    assert not sc.short_year({"period_days": None}) and not sc.short_year(None)  # 모르면 예전처럼
    빈 = sc.flow_blanked({"net_income": 10, "revenue": 100, "total_equity": 500})
    assert 빈["net_income"] is None and 빈["revenue"] is None and 빈["total_equity"] == 500


def _snap(year: int, as_of: str, days: int | None, ni: float, rev: float) -> dict:
    return {"as_of_date": as_of, "fiscal_year": year, "consolidated": 1,
            "values": {"net_income": ni, "revenue": rev, "total_equity": 500.0, "total_assets": 900.0,
                       "operating_income": ni, "period_days": days, "currency": "KRW"}}  # fmt: skip


def test_백테스트_시점_재무도_같은_규칙() -> None:
    짧은해 = _snap(2021, "2022-03-20", 60, 20.0, 200.0)
    온해 = _snap(2022, "2023-03-20", 365, 120.0, 1200.0)
    # 2022-06 기준: 지금 해가 짧다 — 손익을 비우고 전년 비교 없음
    지금, 전년, _ = bj.pit_financials([짧은해], "2022-06-30")
    assert 지금 is not None and 지금["values"]["net_income"] is None and 지금["values"]["total_equity"] == 500.0
    assert 전년 is None
    # 2023-06 기준: 전년이 짧다 — 성장 비교에 쓰지 않는다
    지금, 전년, _ = bj.pit_financials([짧은해, 온해], "2023-06-30")
    assert 지금 is not None and 지금["values"]["net_income"] == 120.0 and 전년 is None


def test_운영_점수도_짧은_해를_12개월로_쓰지_않는다() -> None:
    from batch.jobs import scores as job
    from tests.test_scores_job import universe_row

    def 해(ni: float, rev: float, days: int | None) -> dict:
        return {"net_income": ni, "total_equity": 500, "revenue": rev, "operating_income": ni, "total_assets": 1000,
                "total_liabilities": 500, "currency": "KRW", "period_days": days}  # fmt: skip

    행 = {**universe_row(1), "currency": "KRW"}
    # 지금 해가 2개월 — 손익 비율은 비고 B/P 는 산다
    m = job.build_inputs([행], {1: {2021: 해(20, 200, 60)}}, {})[0].metrics
    assert m["ep"] is None and m["sp"] is None and m["roe"] is None and m["bp"] is not None
    # 전년이 2개월 — 성장 비교를 하지 않는다 (예전엔 +500%)
    m = job.build_inputs([행], {1: {2021: 해(20, 200, 60), 2022: 해(120, 1200, 365)}}, {})[0].metrics
    assert m.get("revenue_growth") is None and m["ep"] is not None
