"""매매 복기 계산 테스트. docs/review.md 7장을 옮겼다. 손으로 맞출 수 있는 값만 쓴다."""

from __future__ import annotations

import pytest

from batch.services import portfolio as pf
from batch.services import review as rv


def lot(buy_id: int, sell_id: int, qty: float, cost: float, proceeds: float, days: int, krw: float = 1.0) -> pf.Lot:
    pnl = proceeds - cost
    return pf.Lot(
        buy_trade_id=buy_id, sell_trade_id=sell_id, stock_id=1, quantity=qty,
        buy_price=cost / qty, sell_price=proceeds / qty, currency="KRW", buy_fx=krw, sell_fx=krw,
        cost=cost, proceeds=proceeds, realized_pnl=pnl, realized_pnl_krw=pnl * krw,
        price_pnl_krw=pnl * krw, fx_pnl_krw=0.0, fee_total=0.0, tax_total=0.0,
        cost_estimated=False, holding_days=days,
    )  # fmt: skip


def buy(tid: int, qty: float, horizon: str | None = "mid", snapshot: bool = True, signal: str | None = "실적 모멘텀",
        score: float | None = 78.0) -> rv.BuySnapshot:
    return rv.BuySnapshot(
        trade_id=tid, stock_id=1, trade_date="2026-01-05", quantity=qty, currency="KRW", horizon=horizon,
        snapshot_as_of="2026-01-02" if snapshot else None,
        score_at_trade=score if snapshot else None,
        signal_type_at_trade=signal if snapshot else None,
    )  # fmt: skip


SELLS = {10: "2026-02-15", 11: "2026-03-20"}


class Test판정:
    def test_경계는_포함이다(self) -> None:
        # 중기 목표 +25%, 손절 -15%
        assert rv.outcome_of(0.25, 25.0, -15.0) == "target"
        assert rv.outcome_of(-0.15, 25.0, -15.0) == "stop"

    def test_계산으로_나온_경계도_포함이다(self) -> None:
        """docs/infra.md 25.243 — 손으로 친 -0.15 가 아니라 `실수령 / 원가 − 1` 로 나온 값으로 본다."""
        # 단기 손절 -7%: 원가 100, 실수령 93 → -0.06999999999999995
        assert rv.outcome_of(93 / 100 - 1, 10.0, -7.0) == "stop"
        # 목표 +10%: 원가 100, 실수령 110 → 0.10000000000000009 (원래 통과) · 원가 3, 실수령 3.3 → 0.0999…
        assert rv.outcome_of(3.3 / 3 - 1, 10.0, -7.0) == "target"

    def test_사이는_방향으로_가른다(self) -> None:
        assert rv.outcome_of(0.10, 25.0, -15.0) == "gain"
        assert rv.outcome_of(-0.05, 25.0, -15.0) == "loss"
        assert rv.outcome_of(0.0, 25.0, -15.0) == "flat"

    def test_기간이_없으면_판정하지_않는다(self) -> None:
        assert rv.outcome_of(0.5, None, None) == "none"

    def test_설정이_없으면_기본값(self) -> None:
        assert rv.targets_for("mid", None) == (25.0, -15.0)
        assert rv.targets_for("short", {"short": {"target_pct": 8, "stop_pct": -5}}) == (8.0, -5.0)
        assert rv.targets_for(None, None) == (None, None)


class Test매수결정으로합치기:
    def test_lot_둘을_한_관찰로(self) -> None:
        """매수 하나가 두 번에 나뉘어 팔렸다. 관찰은 하나여야 한다."""
        lots = [lot(1, 10, 4, 400.0, 480.0, 41), lot(1, 11, 6, 600.0, 540.0, 74)]
        reviews = rv.build_reviews(lots, {1: buy(1, 10)}, SELLS)

        assert len(reviews) == 1
        r = reviews[0]
        # 원가 1000, 실수령 1020 → +2.0%. 보유일 (41×4 + 74×6)/10 = 60.8
        assert r.return_pct == pytest.approx(0.02)
        assert r.holding_days == pytest.approx(60.8)
        assert r.last_sell_date == "2026-03-20"
        assert r.partial is False

    def test_일부만_팔았으면_partial(self) -> None:
        reviews = rv.build_reviews([lot(1, 10, 4, 400.0, 480.0, 41)], {1: buy(1, 10)}, SELLS)
        assert reviews[0].partial is True
        assert reviews[0].quantity_sold == 4
        assert reviews[0].quantity_bought == 10

    def test_스냅샷이_없는_매수(self) -> None:
        reviews = rv.build_reviews([lot(1, 10, 1, 100.0, 90.0, 10)], {1: buy(1, 1, snapshot=False)}, SELLS)
        assert reviews[0].has_snapshot is False
        assert "저장된 근거 없음" in reviews[0].verdict_text

    def test_모르는_매수의_lot은_건너뛴다(self) -> None:
        assert rv.build_reviews([lot(99, 10, 1, 100.0, 110.0, 5)], {}, SELLS) == []


class Test근거문장:
    def test_있는_값만_들어간다(self) -> None:
        r = rv.build_reviews([lot(1, 10, 10, 1000.0, 1123.0, 41)], {1: buy(1, 10)}, SELLS)[0]
        assert r.verdict_text == "중기 · 실적 모멘텀 · 매수 시 점수 78 → +12.3% (41일). 목표 +25% 미달, 손절 -15% 유지"

    def test_소수_목표는_반올림하지_않고_그대로_적는다(self) -> None:
        """+7.5% 목표를 "+8%" 로 적으면 수익률 +7.6% 가 "목표 +8% 도달" 로 나간다 (docs/infra.md 25.367)."""
        설정 = {"mid": {"target_pct": 7.5, "stop_pct": -12.5}}
        r = rv.build_reviews([lot(1, 10, 1, 100.0, 107.6, 5)], {1: buy(1, 1)}, SELLS, 설정)[0]
        assert "목표 +7.5% 도달" in r.verdict_text
        assert "손절 -12.5% 유지" in r.verdict_text

    def test_점수가_없으면_그_조각이_빠진다(self) -> None:
        r = rv.build_reviews([lot(1, 10, 1, 100.0, 130.0, 5)], {1: buy(1, 1, score=None)}, SELLS)[0]
        assert "점수" not in r.verdict_text
        assert "목표 +25% 도달" in r.verdict_text

    def test_기간이_없으면_목표_손절이_빠진다(self) -> None:
        r = rv.build_reviews([lot(1, 10, 1, 100.0, 90.0, 5)], {1: buy(1, 1, horizon=None)}, SELLS)[0]
        assert "목표" not in r.verdict_text
        assert r.outcome == "none"


def reviews_fixture(n_win: int, n_loss: int) -> list[rv.Review]:
    lots = []
    buys = {}
    tid = 1
    for _ in range(n_win):
        lots.append(lot(tid, 10, 1, 100.0, 110.0, 30))
        buys[tid] = buy(tid, 1)
        tid += 1
    for _ in range(n_loss):
        lots.append(lot(tid, 10, 1, 100.0, 95.0, 10))
        buys[tid] = buy(tid, 1, signal=None)  # 신호 없이 산 것
        tid += 1
    return rv.build_reviews(lots, buys, SELLS)


class Test통계:
    def test_원가_가중_평균과_중앙값(self) -> None:
        lots = [lot(1, 10, 1, 100.0, 120.0, 10), lot(2, 10, 1, 900.0, 900.0, 10)]
        stats = rv.build_stats(rv.build_reviews(lots, {1: buy(1, 1), 2: buy(2, 1)}, SELLS))
        all_ = stats[0]
        # 가중: (0.2×100 + 0×900)/1000 = 0.02. 중앙값: (0.2+0)/2 = 0.1
        assert all_.avg_return_pct == pytest.approx(0.02)
        assert all_.median_return_pct == pytest.approx(0.1)
        assert all_.win_rate == pytest.approx(0.5)

    def test_나라가_섞이면_원화_원가로_가중한다(self) -> None:
        """국내 100만 원(+0%)과 미국 1,000달러×1,400원(+20%) — 원화로 100만 대 140만이다 (docs/infra.md 25.289).

        종목 통화 원가로 가중하면 1,000,000 대 1,000 이라 미국 매수가 평균에서 사라져 +0.02% 가 된다.
        """
        lots = [lot(1, 10, 1, 1_000_000.0, 1_000_000.0, 10), lot(2, 10, 1, 1_000.0, 1_200.0, 10, krw=1_400.0)]
        all_ = rv.build_stats(rv.build_reviews(lots, {1: buy(1, 1), 2: buy(2, 1)}, SELLS))[0]
        assert all_.avg_return_pct == pytest.approx(0.2 * 1_400_000 / 2_400_000)

    def test_표본이_적으면_단정하지_않는다(self) -> None:
        stats = rv.build_stats(reviews_fixture(3, 2))
        assert stats[0].n == 5
        assert stats[0].sample_ok is False

    def test_표본이_충분하면_ok(self) -> None:
        stats = rv.build_stats(reviews_fixture(8, 4))
        assert stats[0].n == 12
        assert stats[0].sample_ok is True

    def test_신호별_집단과_없음_집단(self) -> None:
        stats = {s.group_key: s for s in rv.build_stats(reviews_fixture(3, 2))}
        assert stats["signal:실적 모멘텀"].n == 3
        assert stats["signal:없음"].n == 2
        assert stats["horizon:mid"].n == 5

    def test_스냅샷_없는_행은_신호별에서_뺀다(self) -> None:
        reviews = rv.build_reviews([lot(1, 10, 1, 100.0, 110.0, 5)], {1: buy(1, 1, snapshot=False)}, SELLS)
        keys = [s.group_key for s in rv.build_stats(reviews)]
        assert "all" in keys
        assert not any(k.startswith("signal:") for k in keys)

    def test_일부_매도는_승률과_표본에_넣지_않는다(self) -> None:
        """1주만 이익에 팔고 나머지를 들고 있는 매수 10건이 "승률 100%, 표본 충분" 이었다 (docs/infra.md 25.694)."""
        lots = [lot(t, 10, 1, 100.0, 105.0, 10) for t in range(1, 11)]
        stats = rv.build_stats(rv.build_reviews(lots, {t: buy(t, 100) for t in range(1, 11)}, SELLS))
        all_ = stats[0]
        assert all_.n == 0 and all_.sample_ok is False and all_.win_rate is None
        assert "일부 매도 10건 제외" in all_.label
        assert all_.total_pnl_krw == pytest.approx(50.0)  # 오간 돈은 센다
        assert len(all_.members) == 10

    def test_비율의_분모가_작으면_내지_않는다(self) -> None:
        """10건 중 3건만 기간이 있으면 표본 충분 집단의 목표 도달률이 3건으로 났다 (25.694)."""
        lots = [lot(t, 10, 1, 100.0, 130.0, 10) for t in range(1, 11)]
        buys = {t: buy(t, 1, horizon="mid" if t <= 3 else None) for t in range(1, 11)}
        all_ = rv.build_stats(rv.build_reviews(lots, buys, SELLS))[0]
        assert all_.sample_ok is True
        assert all_.target_rate is None and all_.stop_rate is None
        # 분모가 집단과 같으면 낸다 (작은 집단은 sample_ok 가 말한다)
        mid = {s.group_key: s for s in rv.build_stats(rv.build_reviews(lots, buys, SELLS))}["horizon:mid"]
        assert mid.n == 3 and mid.target_rate == pytest.approx(1.0)

    def test_빈_입력(self) -> None:
        stats = rv.build_stats([])
        assert len(stats) == 1 and stats[0].n == 0 and stats[0].win_rate is None
