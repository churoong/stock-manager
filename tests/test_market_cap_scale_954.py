"""밸류 분모 시점 정합 (docs/factors.md 3.1 "분모 시점 규칙", docs/infra.md 25.954, 13회차) — 스냅샷 시총을 기준일 가격으로."""

from __future__ import annotations

from batch.jobs import scores as job
from batch.services import scoring as sc

DATES = ["2026-09-26", "2026-09-29", "2026-09-30", "2026-10-01"]


class Test국내배수:
    def test_등락률_곱으로_낸다(self) -> None:
        # 26일 스냅샷 → 10-01: 29일 +10%, 30일 −5%, 1일 +2% → 1.1 × 0.95 × 1.02 = 1.0659
        pct = {"2026-09-26": 1.0, "2026-09-29": 10.0, "2026-09-30": -5.0, "2026-10-01": 2.0}
        assert abs(sc.price_factor_kr(DATES, pct, "2026-09-26", "2026-10-01") - 1.0659) < 1e-9
        assert sc.price_factor_kr(DATES, pct, "2026-10-01", "2026-10-01") == 1.0  # 같은 날이면 1

    def test_분할이_낀_주도_등락률은_건넌다(self) -> None:
        # 1:10 액면분할 — 종가는 1/10 이지만 KRX 등락률은 기준가 조정 뒤 값이라 평범한 숫자다. 배수는 가격 흐름만
        # 반영한다
        pct = {"2026-09-26": 0.0, "2026-09-29": 1.0, "2026-09-30": 1.0, "2026-10-01": 1.0}
        f = sc.price_factor_kr(DATES, pct, "2026-09-26", "2026-10-01")
        assert abs(f - 1.01**3) < 1e-9

    def test_빈_날이_있거나_시작일이_없으면_모름(self) -> None:
        pct = {"2026-09-29": 10.0, "2026-09-30": None, "2026-10-01": 2.0}
        assert sc.price_factor_kr(DATES, pct, "2026-09-26", "2026-10-01") is None
        assert sc.price_factor_kr(DATES, {}, "2026-09-25", "2026-10-01") is None  # 시작일이 계열에 없다
        assert sc.price_factor_kr(DATES, {}, "2026-10-01", "2026-09-26") is None  # 거꾸로


class Test미국배수:
    def test_수정종가_비율(self) -> None:
        closes = [100.0, 110.0, 104.5, 106.59]
        assert abs(sc.price_factor_us(DATES, closes, "2026-09-26", "2026-10-01") - 1.0659) < 1e-9
        assert sc.price_factor_us(DATES, [100.0, 0.0, 1.0, 1.0], "2026-09-29", "2026-10-01") is None
        assert sc.price_factor_us(DATES, closes, "2026-09-27", "2026-10-01") is None  # 시작일 없음


class Test옮기기:
    def test_날짜가_같고_띠_안이면_옮기고_기록한다(self) -> None:
        cap, note = sc.scale_market_cap(1_000.0, 1.0659, cap_date="2026-09-26", snapshot_date="2026-09-26")
        assert abs(cap - 1065.9) < 1e-9 and note["scaled"] is True and note["factor"] == 1.0659 and note["reason"] is None

    def test_묵은_시총이면_그대로_두고_사유를_적는다(self) -> None:
        # KRX 응답이 비어 지난 시총을 복사한 주(2026-10-04 실측: 시총 날짜 09-16, 스냅샷 10-02 — 16일)
        cap, note = sc.scale_market_cap(1_000.0, 1.3, cap_date="2026-09-16", snapshot_date="2026-10-02")
        assert cap == 1_000.0 and note["scaled"] is False and "묵은 시총" in note["reason"]
        # 시총 날짜가 스냅샷보다 늦으면(옛 스냅샷 재계산·스냅샷 뒤 덮어씀) 이 스냅샷의 값이 아니다
        cap, note = sc.scale_market_cap(1_000.0, 1.1, cap_date="2026-10-09", snapshot_date="2026-10-02")
        assert cap == 1_000.0 and "늦음" in note["reason"]
        assert sc.scale_market_cap(1_000.0, 1.1, cap_date=None, snapshot_date="d")[1]["reason"] == "시총 날짜 모름"

    def test_미국처럼_하루_앞선_시총은_옮긴다(self) -> None:
        # 25.960 — 토요일 us_shares 는 목요일 종가로 시총을 내고 일요일 스냅샷은 금요일 날짜다(10-05 실측 1,860종목
        # 전부)
        cap, note = sc.scale_market_cap(1_000.0, 1.05, cap_date="2026-10-01", snapshot_date="2026-10-02")
        assert abs(cap - 1050.0) < 1e-9 and note["scaled"] is True

    def test_띠_밖이면_그대로(self) -> None:
        cap, note = sc.scale_market_cap(1_000.0, 0.1, cap_date="2026-10-02", snapshot_date="2026-10-02")  # 1:10 분할을 종가 비율로 잘못 냈다면
        assert cap == 1_000.0 and note["scaled"] is False and "띠" in note["reason"]
        assert sc.scale_market_cap(1_000.0, 2.5, cap_date="2026-10-02", snapshot_date="2026-10-02")[0] == 1_000.0

    def test_배수를_못_내거나_시총이_없으면(self) -> None:
        cap, note = sc.scale_market_cap(1_000.0, None, cap_date="2026-10-02", snapshot_date="2026-10-02")
        assert cap == 1_000.0 and note["reason"] == "배수 못 냄"
        cap, note = sc.scale_market_cap(None, 1.1, cap_date="2026-10-02", snapshot_date="2026-10-02")
        assert cap is None and note["reason"] == "시총 없음"
        cap, note = sc.scale_market_cap(1_000.0, 1.1, cap_date="2026-10-02", snapshot_date="2026-10-02", reason="수정주가 재수집 대기 중")
        assert cap == 1_000.0 and note["reason"] == "수정주가 재수집 대기 중"


def _row(stock_id: int, market: str, cap_date: str | None, snapshot: str) -> dict:
    return {"stock_id": stock_id, "ticker": "T", "market": market, "sector": None, "market_cap": 1_000.0,
            "currency": "KRW" if market in ("KOSPI", "KOSDAQ") else "USD", "market_cap_date": cap_date,
            "snapshot_date": snapshot}  # fmt: skip


class Test조립:
    def test_국내는_등락률_미국은_수정종가_as_of_없으면_그대로(self) -> None:
        series = {1: (DATES, [1.0, 1.0, 1.0, 1.0], [None] * 4), 2: (DATES, [100.0, 110.0, 104.5, 106.59], [None] * 4)}
        moves = {1: {"2026-09-26": 0.0, "2026-09-29": 10.0, "2026-09-30": -5.0, "2026-10-01": 2.0}}
        kr, note_kr = job.scaled_market_cap(_row(1, "KOSPI", "2026-09-26", "2026-09-26"), 1, "2026-10-01", series, moves, set())
        us, note_us = job.scaled_market_cap(_row(2, "NASDAQ", "2026-09-26", "2026-09-26"), 2, "2026-10-01", series, moves, set())
        assert abs(kr - 1065.9) < 1e-6 and note_kr["scaled"] and abs(us - 1065.9) < 1e-6 and note_us["scaled"]
        # 미국: 재수집 대기 중이면 옮기지 않는다
        us2, n2 = job.scaled_market_cap(_row(2, "NASDAQ", "2026-09-26", "2026-09-26"), 2, "2026-10-01", series, moves, {2})
        assert us2 == 1_000.0 and n2["reason"] == "수정주가 재수집 대기 중"
        # 미국: 시총 날짜가 재수집 창(10일) 밖이면 옮기지 않는다
        us3, n3 = job.scaled_market_cap(_row(2, "NASDAQ", "2026-09-15", "2026-09-15"), 2, "2026-10-01", series, moves, set())
        assert us3 == 1_000.0 and "재수집 창" in n3["reason"]
        # 미국: 시총 날짜(목)가 스냅샷(금)보다 하루 앞서도 그 날짜부터 옮긴다 (25.960)
        us4, n4 = job.scaled_market_cap(_row(2, "NASDAQ", "2026-09-29", "2026-09-30"), 2, "2026-10-01", series, moves, set())
        assert abs(us4 - 1000.0 * 106.59 / 110.0) < 1e-6 and n4["scaled"]
        # as_of 가 없으면(옛 호출부) 기록 없이 그대로
        assert job.scaled_market_cap(_row(1, "KOSPI", "2026-09-26", "2026-09-26"), 1, None, series, moves, set()) == (1_000.0, None)

    def test_밸류_raw_json_에_기록이_실린다(self) -> None:
        from tests.test_scores_job import universe_row

        rows = [{**universe_row(i, "KOSPI", 1_000), "market_cap_date": "2026-09-26", "snapshot_date": "2026-09-26",
                 "currency": "KRW"} for i in range(1, 4)]  # fmt: skip
        fin = {i: {2025: {"net_income": 100.0, "total_equity": 500.0, "revenue": 900.0, "currency": "KRW"}} for i in range(1, 4)}
        series = {i: (DATES, [1.0] * 4, [None] * 4) for i in range(1, 4)}
        moves = {i: {"2026-09-29": 10.0, "2026-09-30": 0.0, "2026-10-01": 0.0} for i in range(1, 4)}
        inputs = job.build_inputs(rows, fin, {}, series, as_of="2026-10-01", moves=moves)
        assert all(i.market_cap_note and i.market_cap_note["scaled"] for i in inputs)
        # E/P = 100 / 1100 — 분모가 옮겨졌다
        assert abs(inputs[0].metrics["ep"] - 100 / 1100) < 1e-9
        results = sc.score_factors(inputs, min_size=1)
        value = next(r for r in results if r.factor == "value" and r.stock_id == 1)
        assert value.raw[sc.MARKET_CAP_KEY]["factor"] == 1.1 and value.raw[sc.MARKET_CAP_KEY]["scaled"] is True
        assert sc.MARKET_CAP_KEY not in next(r for r in results if r.factor == "quality").raw
