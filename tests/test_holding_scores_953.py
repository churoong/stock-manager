"""보유 종목 점수 영수증 (docs/reports.md 3.6, docs/infra.md 25.953) — 샀을 때 점수와 지금 점수를 2부에."""

from __future__ import annotations

from batch.notify import report_sections as rs
from batch.services import holding_scores as hs
from batch.services import report_picks as rp


def h(**kw) -> hs.HoldingScore:
    base = dict(name="코스맥스", market="KOSPI", score_now=55.0, rank_now=120, as_of="2026-10-01",
                score_at_trade=62.0, first_buy="2026-09-20")  # fmt: skip
    base.update(kw)
    return hs.HoldingScore(**base)


class Test글:
    def test_한_줄(self) -> None:
        assert hs.line(h()) == "보유 코스맥스: 지금 55점 (KOSPI 120위, 10-01) · 매수 때 62점 (09-20) → -7점"
        assert hs.line(h(score_at_trade=50.0)) == "보유 코스맥스: 지금 55점 (KOSPI 120위, 10-01) · 매수 때 50점 (09-20) → +5점"

    def test_없는_값은_없다고_적는다(self) -> None:
        assert hs.line(h(score_now=None, rank_now=None, as_of=None)) == "보유 코스맥스: 지금 점수 없음 · 매수 때 62점 (09-20)"
        assert hs.line(h(score_at_trade=None, first_buy=None)) == "보유 코스맥스: 지금 55점 (KOSPI 120위, 10-01) · 매수 때 점수 없음"
        assert hs.line(h(score_now=55.4, score_at_trade=55.0)) .endswith("→ +0점")  # "-0점" 이 아니다
        # 보이는 두 숫자의 차이 (25.959, 10-06 리포트 "지금 60점 · 매수 때 61점 → +0점")
        assert hs.line(h(score_now=60.4, score_at_trade=60.6)).endswith("지금 60점 (KOSPI 120위, 10-01) · 매수 때 61점 (09-20) → -1점")

    def test_여덟_줄까지(self) -> None:
        out = hs.render([h(name=f"종목{i}") for i in range(10)])
        assert out[0].startswith("보유 종목 점수") and len(out) == 1 + 8 + 1 and out[-1] == "  … 외 2종목"
        assert hs.render([]) == []

    def test_행에서_꺼낸다(self) -> None:
        [x] = hs.from_rows([{"name": "코스맥스", "market": "KOSPI", "score_now": 55, "rank_in_market": 120,
                             "as_of_date": "2026-10-01", "score_at_trade": None, "first_buy": None},
                            {"name": None}])  # fmt: skip
        assert x.score_now == 55.0 and x.score_at_trade is None


class Test2부:
    def test_compose_가_받으면_보유_평가_아래에_붙는다(self) -> None:
        from tests.test_report_picks import row as srow

        holdings = [rp.Holding(stock_id=9, ticker="192820", name="코스맥스", sector="화장품", value=1_000_000.0)]
        composed = rp.compose([srow(1)], 50_000_000.0, "2026-10-02", holdings=holdings, holding_scores=[h()])
        text = composed.text
        assert "보유 종목 점수 — 샀을 때와 지금 (표시일 뿐 매도 신호가 아닙니다)" in text
        assert text.index("현재 보유 평가") < text.index("보유 코스맥스: 지금 55점")
        # 안 넘기면 전과 같다
        assert "보유 종목 점수" not in rp.compose([srow(1)], 50_000_000.0, "2026-10-02", holdings=holdings).text

    def test_payload_에는_남지_않고_글에만(self) -> None:
        view = rs.PortfolioView(holdings_value=1.0, holdings_count=1, holding_scores=[h()])
        assert "보유 코스맥스" in rs.render_portfolio(view, [])


class Test잣대와_보유분:
    """25.962 — 잣대가 다르면 차이를 적지 않고, 다 팔고 다시 산 종목에 옛 매수 점수를 붙이지 않는다."""

    def test_잣대가_다르면_차이를_적지_않는다(self) -> None:
        글 = hs.line(h(score_now=60.0, score_at_trade=61.0, same_yardstick=False))
        assert 글.endswith("매수 때 61점 (09-20) (계산 판·가중치가 달라 차이는 적지 않음)") and "→" not in 글

    def _읽기(self, 그때_판: int, 옛_매수: bool = False) -> hs.HoldingScore:
        from batch.core import db
        from batch.jobs import daily
        from tests.test_portfolio_job import MemClient

        mem = MemClient()
        db.apply_migrations(mem)  # type: ignore[arg-type]
        c = mem.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, 'A', 'KOSPI', 'KR', '에이', 'KRW', 'active', 't', 't')"
        )
        c.execute(
            "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw, first_buy_date,"
            " horizon, price_date, close, market_value, updated_at)"
            " VALUES (1, 10, 'KRW', 100, 1, 1000, 1000, '2026-09-01', 'long', '2026-09-16', 100, 1000, 't')"
        )
        매수 = [("2026-09-01", "2026-08-31", 70)]
        if 옛_매수:  # 다 팔아 닫힌 로트 — 지금 보유분이 아니다
            매수.append(("2026-07-01", "2026-06-30", 90))
        for d, snap, sc in 매수:
            c.execute(
                "INSERT INTO trades (stock_id, side, trade_date, price, quantity, currency, fx_rate, fx_rate_source,"
                " snapshot_as_of, score_at_trade, created_at, updated_at)"
                " VALUES (1, 'buy', ?, 100, 10, 'KRW', 1, 'none', ?, ?, 't', 't')",
                [d, snap, sc],
            )
        for day, total, 판 in (("2026-06-30", 90, 그때_판), ("2026-08-31", 70, 그때_판), ("2026-09-15", 60, 10)):
            c.execute(
                "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_weight_used,"
                " weights_json, calc_version, created_at) VALUES (1, ?, ?, '{}', 0, '{\"value\": 50}', ?, 't')",
                [day, total, 판],
            )
        경고: list[str] = []
        [x] = daily._holding_scores(mem, "2026-09-16", 경고)  # type: ignore[arg-type]
        assert 경고 == []
        return x

    def test_같은_잣대면_차이를_적는다(self) -> None:
        x = self._읽기(10)
        assert x.same_yardstick and hs.line(x).endswith("→ -10점")

    def test_판이_다르면_차이를_적지_않는다(self) -> None:
        x = self._읽기(8)
        assert not x.same_yardstick and "→" not in hs.line(x)

    def test_닫힌_옛_매수의_점수는_쓰지_않는다(self) -> None:
        x = self._읽기(10, 옛_매수=True)
        assert (x.score_at_trade, x.first_buy) == (70.0, "2026-09-01")
