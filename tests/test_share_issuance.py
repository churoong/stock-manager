"""순주식발행 — 기법 발굴 루프 1회차 B (batch/services/share_issuance.py, docs/infra.md 25.446)."""

from __future__ import annotations

import math

import pytest

from batch.services import share_issuance as si
from batch.sources import sec_facts as sf


def test_손으로_센_값() -> None:
    assert si.net_issuance(110, 100) == pytest.approx(math.log(1.1))
    assert si.net_issuance(95, 100) == pytest.approx(math.log(0.95))  # 소각


def test_분할_병합은_발행이_아니다() -> None:
    assert si.net_issuance(200, 100) is None  # 2:1 분할
    assert si.net_issuance(50, 100) is None  # 1:2 병합
    assert si.net_issuance(180, 100) == pytest.approx(math.log(1.8))  # 문턱 아래는 발행으로 본다


def test_없거나_0_이하면_없다() -> None:
    assert si.net_issuance(None, 100) is None and si.net_issuance(100, 0) is None


def _fact(val: int, accn: str, end: str, form: str = "10-K") -> dict:
    return {"end": end, "val": val, "accn": accn, "form": form, "filed": "2025-11-01"}


def test_10K_표지의_dei_주식수를_공시마다_싣는다() -> None:
    """dei 는 표지 날짜라 결산일과 다르다 — accn 으로 고른다 (docs/infra.md 25.446)."""
    ni = {"start": "2024-10-01", "end": "2025-09-30", "val": 100, "accn": "a", "form": "10-K", "filed": "2025-11-01"}
    payload = {"facts": {
        "us-gaap": {"NetIncomeLoss": {"units": {"USD": [ni]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _fact(1_000, "a", "2025-10-20"),
            _fact(900, "q", "2025-07-20", form="10-Q"),  # 다른 공시
        ]}}},
    }}  # fmt: skip
    r = sf.parse_annual_reports(payload)[0]
    assert r.values["shares_outstanding"] == 1_000
    assert r.concepts["shares_outstanding"] == "dei:EntityCommonStockSharesOutstanding"


def test_dei_가_없으면_결산일의_us_gaap() -> None:
    ni = {"start": "2024-10-01", "end": "2025-09-30", "val": 100, "accn": "a", "form": "10-K", "filed": "2025-11-01"}
    payload = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [ni]}},
        "CommonStockSharesOutstanding": {"units": {"shares": [_fact(700, "a", "2024-09-30"), _fact(800, "a", "2025-09-30")]}},
    }}}  # fmt: skip
    r = sf.parse_annual_reports(payload)[0]
    assert r.values["shares_outstanding"] == 800  # 비교 기간(전년)이 아니라 결산일 값


def test_옛_스냅샷에도_채우고_백테스트가_IC_를_잰다() -> None:
    from batch.jobs import backtest as job
    from batch.jobs import us_financials as usf

    assert "shares_outstanding" in usf.ADDED_PAYLOAD_KEYS and "shares_outstanding" in usf._FILL_SQL
    assert "net_issuance" in job.IC_NAMES


def test_백테스트_IC_는_적게_찍을수록_높게(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from batch.jobs import backtest as job

    ids = list(range(1, 41))
    dates = ["2026-01-30", "2026-02-02", "2026-03-02"]
    prices = {sid: {"2026-01-30": 100.0, "2026-02-02": 100.0, "2026-03-02": 200.0 - sid} for sid in ids}
    # 종목 번호가 클수록 많이 찍었고(발행 +) 다음 달 덜 올랐다 → −발행 과 수익의 순위가 같다
    # 같은 10-K 의 당기·전기 가중평균 주식수 (25.450)
    snaps = {sid: [
        {"as_of_date": "2026-01-15", "fiscal_year": 2025, "values": {"shares_basic": 1000 + sid, "shares_basic_prev": 1000}},
    ] for sid in ids}  # fmt: skip
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="NASDAQ", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="momentum", score=0.0) for i in inputs])
    got = job.factor_ics([{"stock_id": s} for s in ids], snaps, prices, dates, ["2026-02-02", "2026-03-02"], {})
    assert got["net_issuance"].mean == pytest.approx(1.0)


def _wa(val: int, accn: str, start: str, end: str) -> dict:
    return {"start": start, "end": end, "val": val, "accn": accn, "form": "10-K", "filed": "2025-11-01"}


def test_같은_10K_의_당기_전기_가중평균_주식수를_싣는다_3대2_분할도_발행이_아니다() -> None:
    """3:2 분할(1.5배)이 1.9배 문턱을 빠져나가 "대량 발행" 이 됐다. 같은 공시는 전기를 소급 조정해 적는다
    (docs/infra.md 25.450, 교차검증)."""
    ni = {"start": "2024-10-01", "end": "2025-09-30", "val": 100, "accn": "a", "form": "10-K", "filed": "2025-11-01"}
    payload = {"facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [ni]}},
        "WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": [
            _wa(1_530, "a", "2024-10-01", "2025-09-30"),  # 분할 뒤 당기
            _wa(1_500, "a", "2023-10-01", "2024-09-30"),  # 같은 공시의 전기 — 분할 소급 조정됨(1000 × 1.5)
            _wa(1_000, "old", "2023-10-01", "2024-09-30"),  # 작년 공시의 조정 전 값 — 쓰지 않는다
        ]}},
    }}}  # fmt: skip
    r = sf.parse_annual_reports(payload)[0]
    assert (r.values["shares_basic"], r.values["shares_basic_prev"]) == (1_530, 1_500)
    got = si.net_issuance(r.values["shares_basic"], r.values["shares_basic_prev"], split_adjusted=True)
    assert got == pytest.approx(math.log(1.02))  # 2% 발행이지 50% 가 아니다


def test_조정된_쌍은_두_배_증자도_자르지_않는다() -> None:
    assert si.net_issuance(200, 100, split_adjusted=True) == pytest.approx(math.log(2))
    assert si.net_issuance(150, 100) == pytest.approx(math.log(1.5))  # 조정 안 된 쌍은 3:2 분할을 못 거른다 — 그래서 안 쓴다


def test_같은_날짜에_다른_주식수가_여럿이면_비운다() -> None:
    """(frame, 값) 중복 제거는 frame 유무에 따라 결과가 멋대로 바뀌었다 (25.450)."""
    ni = {"start": "2024-10-01", "end": "2025-09-30", "val": 100, "accn": "a", "form": "10-K", "filed": "2025-11-01"}
    payload = {"facts": {
        "us-gaap": {"NetIncomeLoss": {"units": {"USD": [ni]}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            _fact(500, "a", "2025-10-20"), _fact(700, "a", "2025-10-20")]}}},
    }}  # fmt: skip
    assert sf.parse_annual_reports(payload)[0].values["shares_outstanding"] is None
