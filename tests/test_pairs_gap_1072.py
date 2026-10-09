"""숨은 테마 안의 지각생 — 짝 거래 신호 (docs/factors.md 12.18, docs/analysis.md 53장, docs/infra.md 25.1072).

검증 둘의 조건을 묶는다:
- 기준일 뒤 가격을 한 줄이라도 보면 미래가 신호에 샌다 → 기준일까지 같은 두 세계는 같은 점수
- 짝 고르는 창은 신호 창(지난 21거래일)과 겹치지 않는다 → 형성 끝 = 축의 22번째 뒤 날
- 같은 날짜 축 — 멈춘 종목·국내 ±30% 날은 값이 없다
- 같은 발행사(보통주·우선주)는 짝이 아니다
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from batch.jobs import backtest as job
from batch.services import backtest as bt
from batch.services import candidate_factors as cf
from batch.services import history as hist


def test_같은_발행사_열쇠() -> None:
    assert cf.same_issuer_key("005930", kr=True) == cf.same_issuer_key("005935", kr=True)
    assert cf.same_issuer_key("005930", kr=True) != cf.same_issuer_key("000660", kr=True)
    assert cf.same_issuer_key("BRK-A", kr=False) == cf.same_issuer_key("BRK.B", kr=False)
    assert cf.same_issuer_key("GOOG", kr=False) == cf.same_issuer_key("GOOGL", kr=False)
    assert cf.same_issuer_key("F", kr=False) != cf.same_issuer_key("FOX", kr=False)


def test_같은_날짜_축으로_잰다() -> None:
    축 = [f"d{i:03d}" for i in range(30)]
    s = [(d, 100.0 + i) for i, d in enumerate(축)]
    assert cf.axis_return(s, 축, kr=False) == pytest.approx(129 / 108 - 1)
    assert cf.axis_return(s[:-1], 축, kr=False) is None  # 마지막 날 거래가 없다 — 멈춘 종목
    튐 = [(d, 100.0 * (2 if i >= 20 else 1)) for i, d in enumerate(축)]  # 창 안 하루 +100%
    assert cf.axis_return(튐, 축, kr=True) is None and cf.axis_return(튐, 축, kr=False) == pytest.approx(1.0)
    # 형성 창 수익은 그 날을 빈 날로
    assert "d020" not in cf.formation_returns(튐, kr=True, until="d029", days=29)


def test_짝_베타() -> None:
    random.seed(1)
    짝 = [{f"d{i}": random.gauss(0, 0.01) for i in range(100)} for _ in range(3)]
    평균 = {d: sum(p[d] for p in 짝) / 3 for d in 짝[0]}
    자기 = {d: 1.5 * v for d, v in 평균.items()}
    assert cf.pairs_beta(자기, 짝) == pytest.approx(1.5)
    assert cf.pairs_beta({"d1": 0.1}, 짝) is None


def test_같은_발행사는_짝이_아니다() -> None:
    random.seed(2)
    날 = [f"d{i:04d}" for i in range(260)]
    시장 = {d: random.gauss(0, 0.01) for d in 날}
    계열 = {sid: {d: 시장[d] + random.gauss(0, 0.01) for d in 날} for sid in range(1, 10)}
    계열[2] = {d: v + random.gauss(0, 0.001) for d, v in 계열[1].items()}  # 2번은 1번의 우선주
    짝 = hist.comovers(계열, 시장, {}, top=3, issuer_of={1: "00593", 2: "00593"})
    assert 2 not in {p["stock_id"] for p in 짝[1]}
    assert hist.comovers(계열, 시장, {}, top=3)[1][0]["stock_id"] == 2  # 빼지 않으면 기계적 짝


N = 70  # 짝 50 을 고를 수 있게 같은 시장 종목이 51 이상


def _세계(seed: int = 3, 뒤틀기: bool = False, n_days: int = 330) -> tuple[list[str], dict, dict]:
    random.seed(seed)
    d0 = date(2025, 1, 1)
    날 = [(d0 + timedelta(days=i)).isoformat() for i in range(n_days)]
    시장 = [random.gauss(0, 0.01) for _ in 날]
    테마 = [random.gauss(0, 0.012) for _ in 날]
    지수, x = {}, 1000.0
    for d, m in zip(날, 시장, strict=True):
        x *= math.exp(m)
        지수[d] = x
    prices: dict[int, dict[str, float]] = {}
    for sid in range(1, N + 1):
        p, prices[sid] = 100.0, {}
        for i, d in enumerate(날):
            r = 시장[i] + random.gauss(0, 0.01) + (테마[i] if sid <= 60 else 0.0)
            if n_days - 51 <= i < n_days - 30 and sid <= 60:
                # 기준일 앞 한 달: 테마(2~60번)는 하루 +1% 씩 오르는데 1번만 따라가지 못했다(지각)
                r += -테마[i] + (0.0 if sid == 1 else 0.01)
            if 뒤틀기 and i >= n_days - 30:
                r += 0.05 * (sid % 3)  # 기준일 뒤만 다른 세계
            p *= math.exp(r)
            prices[sid][d] = p
    return 날, prices, 지수


def _pool(prices: dict) -> list[dict]:
    return [{"stock_id": s, "market": "KOSPI", "ticker": f"{s:05d}0"} for s in prices]


def test_기준일까지_같으면_점수도_같다() -> None:
    날, a, 지수 = _세계()
    _, b, _ = _세계(뒤틀기=True)
    cutoff = 날[-31]
    sa = job.pairs_scores(bt.PriceView(a, cutoff), _pool(a), list(a), 지수, cutoff, set(a))
    sb = job.pairs_scores(bt.PriceView(b, cutoff), _pool(b), list(b), 지수, cutoff, set(b))
    assert sa == sb and sa[0][1] is not None


def test_테마에서_떨어진_종목의_폭이_크고_형성_창은_겹치지_않는다() -> None:
    날, prices, 지수 = _세계(seed=5)
    cutoff = 날[-31]
    v = bt.PriceView(prices, cutoff)
    점수, 통제 = job.pairs_scores(v, _pool(prices), list(prices), 지수, cutoff, set(prices))
    테마 = sorted(점수[k] for k in range(2, 61) if 점수[k] is not None)
    assert 점수[1] is not None and 점수[1] > 테마[-1]
    assert 통제[1] == pytest.approx(-cf.axis_return(v.closes(1), sorted(d for d in 지수 if d <= cutoff), True))
    결과 = cf.pairs_from_prices({s: v.closes(s) for s in prices}, dict.fromkeys(prices, "KOSPI"), {}, set(),
                               sorted((d, c) for d, c in 지수.items() if d <= cutoff), [1])  # fmt: skip
    축 = sorted(d for d in 지수 if d <= cutoff)
    assert 결과[1]["formation_until"] == 축[-1 - cf.PAIRS_DAYS] and 결과[1]["n"] >= cf.PAIRS_MIN_SHARE * cf.PAIRS_PEERS
    # 기준 지수가 없거나 짝이 50 이 안 되면 내지 않는다
    assert set(job.pairs_scores(v, _pool(prices), list(prices), {}, cutoff, set())[0].values()) == {None}
    적음 = {s: prices[s] for s in range(1, 40)}
    assert set(job.pairs_scores(bt.PriceView(적음, cutoff), _pool(적음), list(적음), 지수, cutoff, set())[0].values()) == {None}


def test_판정은_증분_IC_하나(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    날, prices, 지수 = _세계(seed=8)
    monkeypatch.setattr(job, "build_pit_inputs", lambda rows, *a, **k: [
        SimpleNamespace(stock_id=r["stock_id"], market="KOSPI", sector=None, metrics={}) for r in rows])
    monkeypatch.setattr(job.sc, "score_factors", lambda inputs: [
        SimpleNamespace(stock_id=i.stock_id, factor="value", score=50.0) for i in inputs])
    got = job.factor_ics(_pool(prices), {}, prices, 날, [날[-40], 날[-1]], {}, benchmark_closes=지수)
    assert got["pairs_gap"].months == 1 and got["pairs_gap_x_rev"].months in (0, 1)
    assert "pairs_gap_x_rev" in job.USER_IC_NAMES and "pairs_gap" not in job.USER_IC_NAMES
    assert "pairs_gap" in job.DIAG_IC_NAMES and len(job.LOOP_IC_NAMES) == 11


def test_주간_작업이_같은_함수로_쌓는다(client) -> None:  # noqa: ANN001
    import json

    from batch.jobs import metrics as metrics_job
    from tests.test_metrics_beta import 계열, 시세넣기
    from tests.test_metrics_beta import 기준일 as 지표_기준일

    random.seed(11)
    n = 400
    시장 = [random.gauss(0, 0.01) for _ in range(n - 1)]
    테마 = [random.gauss(0, 0.012) for _ in range(n - 1)]
    for d, c in 계열(1000.0, 시장):
        client.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
                            " VALUES ('KOSPI', ?, ?, 't', 't')", [d, c])  # fmt: skip
    for sid in range(1, 61):
        if sid > 1:
            client.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source,"
                                " fetched_at) VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't')",
                                [sid, f"{sid:05d}0", f"회사{sid}"])  # fmt: skip
        r = [m + random.gauss(0, 0.01) + t for m, t in zip(시장, 테마, strict=True)]
        시세넣기(client, sid, 계열(100.0, r))
    metrics_job.compute_all(client, [(sid, f"{sid:05d}0", "KR") for sid in range(1, 61)], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns WHERE stock_id = 2").fetchone()[0])
    폭 = 행["pairs_gap"]
    assert 폭["n"] >= cf.PAIRS_MIN_SHARE * cf.PAIRS_PEERS and 폭["gap"] == pytest.approx(
        폭["beta"] * 폭["peers"] - 폭["own"], abs=1e-3)


from tests.test_metrics_beta import client as client  # noqa: E402,F401 — 픽스처


def test_짝_후보는_점수_없는_종목까지() -> None:
    """점수(재무)가 없는 종목도 짝이 될 수 있다 — 운영 53장은 유니버스 전 종목에서 고른다(검증 A)."""
    날, prices, 지수 = _세계(seed=5)
    cutoff = 날[-31]
    점수, _ = job.pairs_scores(bt.PriceView(prices, cutoff), _pool(prices), [1], 지수, cutoff, set(prices))
    assert 점수 == {1: 점수[1]} and 점수[1] is not None


def test_베타로_배율을_맞춘다() -> None:
    """CCCL 2019 처럼 β·짝 수익 − 자기 수익. 짝보다 두 배로 흔들리는 종목은 짝이 +5% 면 +10% 가 '제자리' 다."""
    random.seed(9)
    d0 = date(2025, 1, 1)
    날 = [(d0 + timedelta(days=i)).isoformat() for i in range(330)]
    시장 = [random.gauss(0, 0.01) for _ in 날]
    테마 = [random.gauss(0, 0.012) for _ in 날]
    지수 = []
    x = 1000.0
    for d, m in zip(날, 시장, strict=True):
        x *= math.exp(m)
        지수.append((d, x))
    closes: dict[int, list[tuple[str, float]]] = {}
    for sid in range(1, 61):
        p, cl = 100.0, []
        for i, d in enumerate(날):
            p *= math.exp(시장[i] + (2.0 if sid == 1 else 1.0) * 테마[i] + random.gauss(0, 0.004))
            cl.append((d, p))
        closes[sid] = cl
    r = cf.pairs_from_prices(closes, dict.fromkeys(closes, "KOSPI"), {}, set(), 지수, [1])[1]
    # 기대 β = (σm² + 2σt²)/(σm² + σt²) ≈ 1.59 — 시장 몫이 둘 다에 있다
    assert r["beta"] > 1.4 and r["gap"] == pytest.approx(r["beta"] * r["peers"] - r["own"])
