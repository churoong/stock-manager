"""`batch/jobs/backtest.run` 실행 틀을 넓힌다 — 미국·추세 필터·배당·정지·상폐·`pit_universe=False` (docs/infra.md 25.470).

`tests/test_backtest_run_harness.py`(25.460) 는 국내 한 경로만 돌았다. 여기서는 같은 가짜 클라이언트(메모리 SQLite +
실제 migrations/*.sql)에 **미국** 합성 데이터를 싣고 두 번 돌린다.

- 실행 A: `run("US", years=1, trend_filter=True)` — 수정종가(adj_close)와 가격 수준(close)이 다르고, SEC 연간 스냅샷
  (영업현금흐름·당기/전기 가중평균 주식수), 배당 이력(접수일 뒤 큰 배당·접수일 모르는 행 포함), SP500 지수
  (강세 → 약세 → 강세), 거래정지(두 달 가격 없음), 상장폐지(최신 스냅샷에 든 채 가격이 끊김), 거래대금 0 인 날
- 실행 B: 같은 데이터로 `pit_universe=False`, 추세 필터 끔

`bt.simulate` 와 `build_pit_inputs` 를 감싸 **전략별 리밸런스 비중**과 **기준일별 지표**를 엿본다(값은 바꾸지 않는다).
네트워크는 막고 시각은 2026-09-28 로 고정한다. 창은 1년이다 — 2년이면 지수 정렬(`aligned_returns`) 때문에
한 번에 11초가 걸려 두 파일 합 30초 예산을 넘는다.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

from batch.core import db
from batch.jobs import backtest as backtest_module
from batch.services import backtest as bt
from batch.services import trend
from tests.test_backtest_run_harness import (
    _마지막_실행,
    _영업일,
    가짜클라이언트,
    고정시각,
)

가격_시작 = date(2024, 8, 26)
가격_끝 = date(2026, 9, 25)
유니버스_기준일 = "2026-09-25"

종목수 = 35
#: SIC 60 — 발생액 IC 에서 빠지는 금융
금융들 = {34, 35}
#: 2025-10-01 ~ 2025-11-28 가격이 없다(거래정지). 풀린 뒤 25% 낮게 다시 선다
정지_종목 = 5
정지_구간 = ("2025-10-01", "2025-11-28")
#: status='delisted' 인데 최신 스냅샷에 든 채(스냅샷 뒤 폐지) 2026-02-27 에 가격이 끝난다
폐지_종목 = 6
폐지_마지막날 = "2026-02-27"
#: 닷새에 하루 거래량·거래대금이 0 이다
대금0_종목 = 7
#: FY2025 배당(시총의 약 30%)이 2026-06-15 에야 접수된다 — 그 전 기준일에 보이면 look-ahead
늦은배당_종목 = 8
늦은배당_접수일 = "2026-06-15"
#: 접수일(as_of_date)이 비어 있는 큰 배당 — 언제 알았는지 모르니 싣지 않아야 한다
접수일없는_종목 = 9
주식수 = 50_000_000


def _지수() -> list[tuple[str, float]]:
    """SP500: 2025-06 까지 오르고, 2025-12 까지 크게 내리고(약세), 다시 오른다."""
    out: list[tuple[str, float]] = []
    px = 4_000.0
    for d in _영업일(date(2023, 6, 1), 가격_끝):
        if d < "2025-06-01":
            px *= 1.0006
        elif d < "2025-12-01":
            px *= 0.996
        else:
            px *= 1.003
        out.append((d, px))
    return out


def _채우기(client: 가짜클라이언트) -> None:
    rng = random.Random(468)
    conn = client.conn
    t = "2026-09-25T00:00:00+00:00"
    for sid in range(1, 종목수 + 1):
        code = "SIC 6022" if sid in 금융들 else "SIC 3674"
        status = "delisted" if sid == 폐지_종목 else "active"
        conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at,"
            " sector, sector_code, listed_shares, listed_date)"
            " VALUES (?, ?, ?, 'US', 'USD', ?, 'test', ?, ?, ?, ?, '2015-01-02')",
            [sid, f"U{sid:03d}", "NASDAQ", status, t,  # 한 시장 — IC 는 시장 안에서 30종목이 있어야 난다 (25.810)
             "Finance" if sid in 금융들 else "Tech", code, 주식수],
        )  # fmt: skip
        conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES (?, ?, 1, 'USD', ?)",
            [유니버스_기준일, sid, t],
        )
    # 국내 종목 하나 — 나라 거르기가 새지 않는지
    conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, listed_shares)"
        " VALUES (500, '000500', 'KOSPI', 'KR', 'KRW', 'active', 'test', ?, 1000000)",
        [t],
    )

    days = _영업일(가격_시작, 가격_끝)
    rows: list[tuple[Any, ...]] = []
    for sid in range(1, 종목수 + 1):
        px = 100.0 + sid
        추세 = (sid % 7 - 3) * 0.0004
        for i, d in enumerate(days):
            px *= 1 + 추세 + rng.gauss(0, 0.015)
            if sid == 정지_종목 and 정지_구간[0] <= d <= 정지_구간[1]:
                continue
            if sid == 정지_종목 and d == "2025-12-01":
                px *= 0.75
            if sid == 폐지_종목 and d > 폐지_마지막날:
                continue
            vol = 200_000 + rng.randint(0, 50_000)
            if sid == 대금0_종목 and i % 5 == 0:
                vol = 0
            # 수정종가 = 원 종가 × (그 뒤 배당만큼 낮다). 끝으로 갈수록 1 에 가깝다
            조정 = 0.95 + 0.05 * i / len(days)
            rows.append((sid, d, px, px * 조정, vol, px * vol, "USD", "test", t))
    conn.executemany(
        "INSERT INTO prices (stock_id, date, close, adj_close, volume, value, currency, source, fetched_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.executemany(
        "INSERT INTO index_prices (index_code, date, close, source, fetched_at) VALUES ('SP500', ?, ?, 'test', ?)",
        [(d, c, t) for d, c in _지수()],
    )

    snaps: list[tuple[Any, ...]] = []
    divs: list[tuple[Any, ...]] = []
    for sid in range(1, 종목수 + 1):
        규모 = 1e9 * (1 + sid / 10)
        for 연도 in range(2019, 2026):
            성장 = 1.05 ** (연도 - 2019) * (1 + rng.uniform(-0.05, 0.05))
            ni = 규모 * 0.08 * 성장
            v = {
                "revenue": 규모 * 성장, "operating_income": 규모 * 0.1 * 성장, "net_income": ni,
                "total_assets": 규모 * 2 * 성장, "total_liabilities": 규모 * 성장, "total_equity": 규모 * 성장,
                "operating_cash_flow": ni * rng.uniform(0.6, 1.4),
                "shares_basic": 1e7 * (1 + rng.uniform(-0.02, 0.03)), "shares_basic_prev": 1e7,
            }  # fmt: skip
            접수 = f"{연도 + 1}-02-20"
            snaps.append((sid, 접수, f"A{sid}-{연도}", 연도, "11011", 1, json.dumps(v)))
            배당 = ni * 0.3
            if sid == 늦은배당_종목 and 연도 == 2025:
                접수, 배당 = 늦은배당_접수일, 0.3 * 주식수 * 100  # 시총(약 50억 달러)의 30%
            divs.append((sid, 연도, 연도, f"D{sid}-{연도}", 접수, 배당))
    divs.append((접수일없는_종목, 2026, 2026, "DX", None, 0.5 * 주식수 * 100))
    conn.executemany(
        "INSERT INTO financial_snapshots (stock_id, as_of_date, receipt_no, fiscal_year, report_code,"
        " consolidated, payload, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'test', '2026-09-25')",
        snaps,
    )
    conn.executemany(
        "INSERT INTO stock_dividends (stock_id, fiscal_year, report_year, receipt_no, as_of_date,"
        " cash_dividend_total, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, 'test', '2026-09-25')",
        divs,
    )
    conn.commit()


@dataclass
class 엿봄:
    """한 번의 run 이 남긴 것."""

    client: 가짜클라이언트
    돌림: int
    #: simulate 를 부른 순서대로 그 결과
    결과들: list[bt.BacktestResult] = field(default_factory=list)
    #: 기준일 → 종목 → 지표
    지표: dict[str, dict[int, dict[str, Any]]] = field(default_factory=dict)

    def 전략별(self, 이름들: list[str]) -> dict[str, bt.BacktestResult]:
        assert len(이름들) == len(self.결과들), (이름들, len(self.결과들))
        return dict(zip(이름들, self.결과들, strict=True))


def _돌리기(**kwargs: Any) -> Iterator[엿봄]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OFFLINE_MODE", "1")
        mp.setenv("DB_BACKEND", "turso")

        def _막기(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("백테스트가 네트워크를 불렀다")

        import requests

        mp.setattr(requests.sessions.Session, "request", _막기)
        client = 가짜클라이언트()
        db.apply_migrations(client)
        _채우기(client)
        client.기록.clear()
        mp.setattr(backtest_module, "TursoClient", lambda: client)
        mp.setattr(backtest_module, "datetime", 고정시각)

        본 = 엿봄(client, -1)
        원래_sim = bt.simulate
        원래_입력 = backtest_module.build_pit_inputs

        def 감싼_sim(*a: Any, **k: Any) -> bt.BacktestResult:
            r = 원래_sim(*a, **k)
            본.결과들.append(r)
            return r

        def 감싼_입력(universe: Any, snaps: Any, view: bt.PriceView, *a: Any, **k: Any) -> Any:
            out = 원래_입력(universe, snaps, view, *a, **k)
            칸 = 본.지표.setdefault(view.cutoff, {})
            for i in out:
                칸[i.stock_id] = i.metrics
            return out

        mp.setattr(bt, "simulate", 감싼_sim)
        mp.setattr(backtest_module, "build_pit_inputs", 감싼_입력)
        try:
            본.돌림 = backtest_module.run("US", years=1, top_n=10, **kwargs)
        finally:
            db._열린_실행.clear()
        yield 본


@pytest.fixture(scope="module")
def 실행A() -> Iterator[엿봄]:
    yield from _돌리기(trend_filter=True)


@pytest.fixture(scope="module")
def 실행B() -> Iterator[엿봄]:
    yield from _돌리기(pit_universe=False)


#: simulate 를 부르는 순서 — 전략들, (장기 문턱 없음), 비용 0
순서 = [*backtest_module.STRATEGIES, "composite_no_cost"]


# ----------------------------------------------------------------------
# 1. 미국
# ----------------------------------------------------------------------


def test_미국_실행이_성공하고_총수익_경고와_미국_비용을_남긴다(실행A: 엿봄) -> None:
    assert 실행A.돌림 == 0
    status, step_log = _마지막_실행(실행A.client)
    assert status == "success"
    assert step_log["stocks"] == 종목수  # 국내 종목이 섞이지 않는다
    rows = 실행A.client.행들(
        "SELECT strategy, market, warnings_json, costs_json FROM backtest_runs WHERE group_id = ?",
        [step_log["group_id"]],
    )
    assert sorted(r[0] for r in rows) == sorted(순서)
    for strategy, market, warnings_json, costs_json in rows:
        assert market == "US"
        경고 = json.loads(warnings_json)
        assert bt.WARN_TOTAL_RETURN in 경고 and bt.WARN_NO_DIVIDENDS not in 경고, strategy
        assert json.loads(costs_json)["tax_sell_pct"] == 0.0  # 미국은 거래세가 없다


def test_미국_시총은_수정종가가_아니라_가격_수준으로_잰다(실행A: 엿봄) -> None:
    """수정종가는 가격 수준의 0.95~1.0 배다. 시총으로 나누는 배당수익률이 수준 기준이어야 한다."""
    cutoff = max(c for c in 실행A.지표 if c < 늦은배당_접수일)
    dy = 실행A.지표[cutoff][1]["dividend_yield"]
    수준 = 실행A.client.행들("SELECT close FROM prices WHERE stock_id = 1 AND date = ?", [cutoff])[0][0]
    (payload,) = 실행A.client.행들(
        "SELECT cash_dividend_total FROM stock_dividends WHERE stock_id = 1 AND as_of_date <= ?"
        " ORDER BY fiscal_year DESC LIMIT 1",
        [cutoff],
    )[0]
    assert dy == pytest.approx(payload / (수준 * 주식수), rel=1e-6)


def test_미국_발생액과_순주식발행_IC_가_실제로_잰다(실행A: 엿봄) -> None:
    _, step_log = _마지막_실행(실행A.client)
    ic = step_log["factor_ic"]
    # 창이 1년이라(30초 예산) 12-1 모멘텀·3년 리스크가 비고, 두 팩터가 비면 종합도 빈다(`total_score`) — 재지 않는다
    for name in ("accrual", "net_issuance", "value", "quality"):
        assert ic[name]["months"] >= 1, f"{name} IC 가 한 달도 안 나왔다: {ic[name]}"
        assert ic[name]["mean"] is not None and -1 <= ic[name]["mean"] <= 1
    # 발생액 표본 비율은 금융을 뺀 후보 대비라 금융 둘이 있어도 1 에 가깝다
    assert ic["accrual"]["coverage"] > 0.9
    assert ic["sue"]["months"] == 0  # 미국은 분기 스냅샷이 없다 — 0 개월이 맞다


# ----------------------------------------------------------------------
# 2. 추세 필터
# ----------------------------------------------------------------------


def _국면(cutoff: str) -> str:
    return trend.regime_from_closes("SP500", _지수(), cutoff).state


def test_추세_필터는_약세_달의_비중을_배수만큼_줄이고_벤치마크는_그대로다(실행A: 엿봄) -> None:
    결과 = 실행A.전략별(순서)
    dates = sorted({d for r in 결과.values() for d, _ in r.curve})
    약세달 = 0
    for 이름, r in 결과.items():
        for rb in r.rebalances:
            if not rb.weights:
                continue
            합 = sum(rb.weights.values())
            cutoff = bt.previous_trading_day(dates, rb.date)
            if cutoff is None:
                # 첫 리밸런스는 워밍업 가격으로 판단한다(25.623) — 이 테스트의 날짜 축에는 그 직전 날이 없다
                continue
            if 이름 == "benchmark" or _국면(cutoff) != "bear":
                assert 합 == pytest.approx(1.0), (이름, rb.date, 합)
            else:
                assert 합 == pytest.approx(trend.DEFAULT_BEAR_FACTOR), (이름, rb.date, 합)
                약세달 += 1
    assert 약세달 >= 2 * 7, "약세 국면 리밸런스가 거의 없다 — 지수 합성이 틀렸다"
    _, step_log = _마지막_실행(실행A.client)
    assert step_log["trend_filter"]["on"] is True and step_log["trend_filter"]["index_days"]["SP500"] > 200


def test_추세_필터_켬_경고는_필터를_안_쓴_벤치마크_결과에_붙지_않는다(실행A: 엿봄) -> None:
    _, step_log = _마지막_실행(실행A.client)
    (경고,) = 실행A.client.행들(
        "SELECT warnings_json FROM backtest_runs WHERE group_id = ? AND strategy = 'benchmark'",
        [step_log["group_id"]],
    )[0]
    assert backtest_module.WARN_TREND_ON not in json.loads(경고)


def test_기본이_아닌_기간_상위N_실행은_결과_행에_비교용_표시가_붙는다(실행A: 엿봄) -> None:
    """웹 /backtest 는 이 표시로 대표 묶음을 고른다 — 없으면 기간 1년·상위 10 실행이 대표 결과가 됐다 (docs/infra.md 25.928)."""
    _, step_log = _마지막_실행(실행A.client)
    rows = 실행A.client.행들("SELECT warnings_json FROM backtest_runs WHERE group_id = ?", [step_log["group_id"]])
    assert rows and all(
        any(w.startswith(backtest_module.WARN_NOT_DEFAULT_PARAMS) for w in json.loads(r[0])) for r in rows
    )
    assert step_log["params"]["default"] is False


# ----------------------------------------------------------------------
# 3. 배당
# ----------------------------------------------------------------------


def test_배당수익률은_값이_나오고_접수일_전에는_새지_않는다(실행A: 엿봄) -> None:
    전 = [c for c in 실행A.지표 if c < 늦은배당_접수일]
    후 = [c for c in 실행A.지표 if c >= 늦은배당_접수일]
    assert 전 and 후
    값있는 = [m["dividend_yield"] for c in 전 for m in 실행A.지표[c].values() if m.get("dividend_yield") is not None]
    assert len(값있는) > 100
    for c in 전:
        m = 실행A.지표[c].get(늦은배당_종목)
        if m is not None:
            assert m["dividend_yield"] < 0.05, (c, m["dividend_yield"])  # 30% 배당이 미리 보이면 look-ahead
    assert any(실행A.지표[c][늦은배당_종목]["dividend_yield"] > 0.2 for c in 후 if 늦은배당_종목 in 실행A.지표[c])
    # 접수일 모르는 FY2026 배당(시총의 50%)은 어느 기준일에도 안 보인다
    for c, by in 실행A.지표.items():
        if 접수일없는_종목 in by and by[접수일없는_종목]["dividend_yield"] is not None:
            assert by[접수일없는_종목]["dividend_yield"] < 0.05, c


# ----------------------------------------------------------------------
# 4. 정지·상폐·거래대금 0
# ----------------------------------------------------------------------


def test_상장폐지_후보가_있으면_생존편향_경고가_꺼지고_폐지_뒤에는_사지_않는다(실행A: 엿봄) -> None:
    결과 = 실행A.전략별(순서)
    for 이름, r in 결과.items():
        assert bt.WARN_SURVIVORSHIP not in r.warnings, 이름
        for rb in r.rebalances:
            if rb.date > 폐지_마지막날:
                assert 폐지_종목 not in rb.weights, (이름, rb.date)
    # 벤치마크는 폐지 전에는 폐지 종목을 들고 있었다(후보였다)
    assert any(폐지_종목 in rb.weights for rb in 결과["benchmark"].rebalances if rb.date <= 폐지_마지막날)
    # 곡선은 끝까지 유한하고 양수다
    for 이름, r in 결과.items():
        assert r.curve[-1][0] == 가격_끝.isoformat() and all(0 < v < 10 for _, v in r.curve), 이름


def test_거래정지_중에는_들고_있던_종목을_팔_수_없다(실행A: 엿봄) -> None:
    """벤치마크는 정지 직전 리밸런스(2025-09-01)에 정지 종목을 산다. 정지 중 리밸런스(10-01·11-03)에는 팔 수 없으니
    비중이 남아 있어야 한다. 예전에는 정지 전 종가로 팔아 재개일(12-01) −25% 가 곡선에 안 들어갔다 (docs/infra.md 25.471)."""
    bench = 실행A.전략별(순서)["benchmark"]
    전 = [rb for rb in bench.rebalances if rb.date < 정지_구간[0]]
    assert 전 and 정지_종목 in 전[-1].weights  # 전제: 정지 직전에 들고 있었다
    중 = [rb for rb in bench.rebalances if 정지_구간[0] <= rb.date <= 정지_구간[1]]
    assert 중
    for rb in 중:
        assert 정지_종목 in rb.weights, f"{rb.date} 정지 중인데 정지 전 종가로 팔았다 (dropped={rb.dropped})"


def test_거래대금_0_인_날이_있어도_비유동성이_나온다(실행A: 엿봄) -> None:
    값 = [by[대금0_종목].get("amihud_illiquidity") for by in 실행A.지표.values() if 대금0_종목 in by]
    assert 값 and any(v is not None and v > 0 for v in 값)


def test_정지_종목은_정지_중_시점_유니버스에서_빠진다(실행A: 엿봄) -> None:
    """정지 중에는 20일 창의 거래대금이 0 이라 문턱 아래다. 풀리고 한 달 넘게 지나면 돌아온다."""
    정지중 = [c for c in 실행A.지표 if "2025-10-15" <= c <= 정지_구간[1]]
    assert 정지중
    for c in 정지중:
        assert 정지_종목 not in 실행A.지표[c], c
    assert any(정지_종목 in 실행A.지표[c] for c in 실행A.지표 if c >= "2026-01-15")


# ----------------------------------------------------------------------
# 5. pit_universe=False
# ----------------------------------------------------------------------


def test_시점_유니버스를_끄면_옛_경고와_빈_크기를_남긴다(실행B: 엿봄) -> None:
    assert 실행B.돌림 == 0
    status, step_log = _마지막_실행(실행B.client)
    assert status == "success"
    assert backtest_module.WARN_NO_HISTORICAL_UNIVERSE in step_log["warnings"]
    assert backtest_module.WARN_PIT_UNIVERSE not in step_log["warnings"]
    assert step_log["pit_universe"] == {"on": False, "sizes": {}, "excluded": {}}
    assert step_log["trend_filter"]["on"] is False
    for name in ("accrual", "net_issuance", "value"):
        assert step_log["factor_ic"][name]["months"] >= 1, name
    # 추세 필터가 꺼져 있으면 비중 합은 늘 1 이다
    for r in 실행B.결과들:
        for rb in r.rebalances:
            if rb.weights:
                assert sum(rb.weights.values()) == pytest.approx(1.0)


def test_시점_유니버스를_끄면_폐지_종목이_폐지_뒤에도_후보로_점수를_받는다(실행B: 엿봄) -> None:
    """옛 방식은 오늘 유니버스를 전 구간에 쓴다 — 가격이 끊긴 종목도 `PriceView` 에는 옛 종가가 남아 점수를 받는다.
    simulate 가 그날 체결가가 없어 빼므로 곡선은 안전하다(여기서는 그것만 확인)."""
    뒤 = [c for c in 실행B.지표 if c > 폐지_마지막날]
    assert 뒤 and all(폐지_종목 in 실행B.지표[c] for c in 뒤)
    for r in 실행B.결과들:
        for rb in r.rebalances:
            if rb.date > 폐지_마지막날:
                assert 폐지_종목 not in rb.weights
