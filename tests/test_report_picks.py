"""아침 리포트 추천 테스트 (Step 9).

고르는 규칙과 두 부의 관계를 고정한다. 리포트를 읽는 SQL 은 실제 마이그레이션을
적용한 SQLite 에 돌린다. 열 이름이 틀리면 운영에서 아침 리포트가 통째로 깨지기
때문이다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from batch.jobs import daily
from batch.notify import report_sections as rs
from batch.services import report_picks as rp
from batch.services import signals as sig

ROOT = Path(__file__).resolve().parent.parent


def row(stock_id: int, horizon: str = "mid", score: float | None = 70.0, **kw: Any) -> rp.SignalRow:
    base: dict[str, Any] = {
        "stock_id": stock_id,
        "ticker": f"{stock_id:06d}",
        "name": f"종목{stock_id}",
        "market": "KOSPI",
        "horizon": horizon,
        "signal_type": "실적 모멘텀",
        "currency": "KRW",
        "buy_zone_low": 9000.0,
        "buy_zone_high": 10000.0,
        "suggested_weight_pct": 10.0,
        "suggested_amount": 1_000_000.0,
        "size_reduction": 1.0,
        "tranche_plan": [{"step": 1, "ratio": 0.4, "price": 10000, "amount": 400000}],
        "rationale_text": "매출 18.2%",
        "as_of_date": "2026-09-16",
        "total_score": score,
        # 근거표가 없는 추천은 리포트에 싣지 않는다 (docs/infra.md 25.489) — 기본 행은 기준 하나를 든다
        "criteria": [{"name": "매출 증가", "label": "매출 증가", "display": "18.2%", "value": 18.2, "threshold": "> 0",
                      "source": "dart", "passed": True}],
    }
    base.update(kw)
    return rp.SignalRow(**base)


class Test고르기:
    def test_서로_다른_종목_5개를_점수순으로(self) -> None:
        rows = [row(i, score=float(i)) for i in range(1, 9)]
        chosen = rp.select_top(rows)
        assert [r.stock_id for r in chosen] == [8, 7, 6, 5, 4]

    def test_한_종목의_여러_기간_신호는_모두_싣는다(self) -> None:
        rows = [row(1, "long", 90), row(1, "short", 90)] + [row(i, score=50) for i in range(2, 8)]
        chosen = rp.select_top(rows)
        assert len({r.stock_id for r in chosen}) == 5
        assert [r.horizon for r in chosen if r.stock_id == 1] == ["short", "long"]

    def test_점수가_없는_종목은_뒤로(self) -> None:
        rows = [row(1, score=None), row(2, score=10)]
        assert [r.stock_id for r in rp.select_top(rows, n=1)] == [2]


class Test포트폴리오:
    def test_설정이_없으면_그_사유로_뺀다(self) -> None:
        view = rp.build_portfolio([row(1, suggested_amount=None)], total_investable=0)
        assert view.allocations == []
        assert view.excluded[0].reason == rp.EXCLUDED_NO_SETTING
        assert view.total_budget is None

    def test_환율이_없어_총액이_0이면_미설정이_아니라_환율_없음(self) -> None:
        """미국: 총액은 설정돼 있는데 환율이 없어 0 이 됐다. '설정에서 넣으라' 고 보내면 안 된다 (docs/infra.md 25.292)."""
        view = rp.build_portfolio([row(1, suggested_amount=None)], total_investable=0, fx_missing=True)
        assert view.excluded[0].reason == rs.EXCLUDED_NO_FX

    def test_배수가_0이면_약세장_사유(self) -> None:
        view = rp.build_portfolio([row(1, suggested_amount=None, size_reduction=0.0)], total_investable=10_000_000)
        assert view.excluded[0].reason == rs.EXCLUDED_BEAR_ZERO
        assert "약세장" in view.excluded[0].reason

    def test_종목_위험_축소만으로는_0이_되지_않는다(self) -> None:
        """0 이 되는 길은 약세장 배수 하나뿐이다 — 사유 문구가 그 원인을 짚어야 한다 (docs/infra.md 25.340)."""
        from batch.services import signals as sg

        _, 배수, _ = sg.size_weight(10.0, 5.0, -0.99)
        assert 배수 >= sg.REDUCTION_FLOOR > 0
        _, 배수, _ = sg.size_weight(10.0, 5.0, -0.99, regime_factor=0.0)
        assert 배수 == 0

    def test_최소_주문_미만이면_그_사유(self) -> None:
        # 오늘 총액 × 비중이 최소 주문보다 작다 — 주문 단위 탓
        view = rp.build_portfolio(
            [row(1, suggested_amount=None, size_reduction=0.5, suggested_weight_pct=0.5)],
            total_investable=10_000_000, min_order_amount=100_000,
        )
        assert view.excluded[0].reason == rp.EXCLUDED_BELOW_MIN_ORDER

    def test_신호_때_금액을_못_냈으면_주문_단위라_하지_않는다(self) -> None:
        # 신호 때 환율·총액이 없어 금액이 비었고 오늘은 총액이 있다 — 5천만 × 4% = 200만 ≥ 최소 10만 (docs/infra.md
        # 25.600, 감사)
        view = rp.build_portfolio(
            [row(1, suggested_amount=None, size_reduction=0.5, suggested_weight_pct=4.0)],
            total_investable=50_000_000, min_order_amount=100_000,
        )
        assert view.excluded[0].reason == "신호에 금액 없음"

    def test_종목당_배분은_하나_기간별_금액을_더하지_않는다(self) -> None:
        rows = [row(1, "short", suggested_amount=500_000.0), row(1, "mid", suggested_amount=800_000.0)]
        view = rp.build_portfolio(rows, total_investable=10_000_000)
        assert len(view.allocations) == 1
        assert view.allocations[0].amount == 800_000
        assert "더하지 않음" in view.allocations[0].note
        assert view.remaining_budget == 9_200_000

    def test_여력을_넘으면_빼고_사유를_적는다(self) -> None:
        rows = [row(1, suggested_amount=700_000.0), row(2, suggested_amount=700_000.0)]
        # 종목 상한을 풀어 여력만 본다(상한은 아래 검사가 따로 본다)
        view = rp.build_portfolio(rows, total_investable=1_000_000, max_stock_pct=100)
        assert [a.ticker for a in view.allocations] == ["000001"]
        assert view.excluded[0].reason == rs.EXCLUDED_NO_BUDGET
        assert view.excluded[0].detail == "남은 300,000원"  # 통화가 붙는다 (docs/infra.md 25.324)

    def test_미국_제외_사유의_금액은_달러로(self) -> None:
        달러 = {"currency": "USD", "suggested_amount": 700.0, "tranche_plan": [{"step": 1, "ratio": 1.0, "price": 100}]}
        rows = [row(1, **달러), row(2, **달러)]
        view = rp.build_portfolio(rows, total_investable=1_050.0, currency="USD", max_stock_pct=100)
        assert view.excluded[0].detail == "남은 $350.00"

    def test_배분_전체가_1주_값보다_작으면_뺀다(self) -> None:
        """$900 짜리 종목에 $500 이 배분으로 실렸다 (docs/design.md 3.9, docs/infra.md 25.507, 감사)."""
        비싼 = {"currency": "USD", "suggested_amount": 500.0, "tranche_plan": [{"step": 1, "ratio": 1.0, "price": 900}]}
        view = rp.build_portfolio([row(1, **비싼)], total_investable=35_000.0, currency="USD", max_stock_pct=100)
        assert view.allocations == [] and view.excluded[0].reason == rp.EXCLUDED_BELOW_MIN_ORDER
        assert view.excluded[0].detail == "$500.00 < 1주 $900.00"
        assert view.remaining_budget == 35_000  # 여력에서 빼지 않는다

    def test_반올림해_0_인_배분은_싣지_않는다(self) -> None:
        """최소 주문 0 설정에서 보유 종목 상한 여유 $0.43 이 "$0.00 (0.0%)" 로 실렸다 (25.507, 감사)."""
        싼 = {"currency": "USD", "suggested_amount": 100.0, "tranche_plan": [{"step": 1, "ratio": 1.0, "price": 0.1}]}
        보유 = [rp.Holding(stock_id=1, ticker="000001", name="종목1", sector=None, value=99.57)]
        view = rp.build_portfolio([row(1, **싼)], total_investable=1_000.0, currency="USD", holdings=보유)
        assert view.allocations == [] and view.excluded[0].reason == rp.EXCLUDED_BELOW_MIN_ORDER

    def test_오늘_총액이_없으면_묵은_신호_금액이_있어도_그_사유(self) -> None:
        """"투자 여력 부족 (남은 0원)" 이 아니라 원인을 말한다 (25.507, 감사)."""
        view = rp.build_portfolio([row(1)], total_investable=0)
        assert view.excluded[0].reason == rp.EXCLUDED_NO_SETTING
        view = rp.build_portfolio([row(1, currency="USD")], total_investable=0, currency="USD", fx_missing=True)
        assert view.excluded[0].reason == rs.EXCLUDED_NO_FX

    def test_보유하지_않은_종목도_종목_상한까지만(self) -> None:
        """신호가 묵은 채 총액을 5,000만으로 줄였다 — 1,000만은 20% 다. 상한 10% 인 500만까지만 (docs/infra.md 25.290)."""
        view = rp.build_portfolio([row(1, suggested_amount=10_000_000.0)], total_investable=50_000_000)
        assert view.allocations[0].amount == 5_000_000
        assert "종목 상한 10%까지만" in view.allocations[0].note

    def test_2부는_1부의_부분집합이고_빠진_것엔_사유가_있다(self) -> None:
        rows = [row(1), row(2, suggested_amount=None)]
        chosen = rp.select_top(rows)
        problems = rs.check_subset(rp.to_picks(chosen), rp.build_portfolio(chosen, 10_000_000))
        assert problems == []


class Test보유반영:
    """2부는 보유를 포함해 계산한다 (docs/design.md 3.9, Step 30)."""

    def _holding(self, stock_id: int, value: float, sector: str | None = None) -> rp.Holding:
        return rp.Holding(stock_id, f"{stock_id:06d}", f"종목{stock_id}", sector, value)

    def test_보유_평가액이_여력을_줄인다(self) -> None:
        view = rp.build_portfolio([row(1)], total_investable=10_000_000, holdings=[self._holding(9, 3_000_000)])
        assert view.holdings_value == 3_000_000 and view.holdings_count == 1
        assert view.remaining_budget == 6_000_000  # 1,000만 − 보유 300만 − 배분 100만

    def test_이미_상한만큼_들고_있으면_뺀다(self) -> None:
        view = rp.build_portfolio([row(1)], total_investable=10_000_000, holdings=[self._holding(1, 1_000_000)])
        assert view.allocations == []
        assert view.excluded[0].reason == rs.EXCLUDED_STOCK_CAP and "보유 10.0%" in view.excluded[0].detail

    def test_소수_상한은_반올림하지_않고_적는다(self) -> None:
        """상한 7.5% 를 "8%" 로 적으면 판정(7.5)과 사유가 어긋난다 (docs/infra.md 25.369)."""
        view = rp.build_portfolio(
            [row(1)], total_investable=10_000_000, holdings=[self._holding(1, 800_000)], max_stock_pct=7.5
        )
        assert "상한 7.5%" in view.excluded[0].detail and "보유 8.0%" in view.excluded[0].detail

    def test_일부_들고_있으면_남은_만큼만(self) -> None:
        view = rp.build_portfolio([row(1)], total_investable=10_000_000, holdings=[self._holding(1, 400_000)])
        assert view.allocations[0].amount == 600_000
        assert view.allocations[0].weight_pct == pytest.approx(6.0)
        assert "보유 포함" in view.allocations[0].note

    def test_줄인_비중만큼_들고_있으면_더_사지_않는다(self) -> None:
        """변동성으로 4.2% 로 줄인 종목을 4.2% 가진 날 또 4.2% 를 배분해 10% 까지 찼다 (docs/infra.md 25.560, 감사 재현)."""
        줄인 = row(1, suggested_weight_pct=4.2, suggested_amount=420_000.0)
        view = rp.build_portfolio([줄인], total_investable=10_000_000, holdings=[self._holding(1, 420_000)])
        assert view.allocations == []
        assert view.excluded[0].reason == rs.EXCLUDED_STOCK_CAP and "권장 비중(4.2%)" in view.excluded[0].detail
        # 일부만 들고 있으면 줄인 비중까지만 채운다
        view = rp.build_portfolio([줄인], total_investable=10_000_000, holdings=[self._holding(1, 220_000)])
        assert view.allocations[0].amount == 200_000

    def test_종목_상한까지_넘었으면_그것을_적는다(self) -> None:
        """10-06 리포트 "(종목) — 보유 33.1% — 이미 권장 비중(4.1%)만큼 보유" — 상한 10% 의 세 배를 덮었다 (25.964)."""
        줄인 = row(1, suggested_weight_pct=4.1, suggested_amount=410_000.0)
        view = rp.build_portfolio([줄인], total_investable=10_000_000, holdings=[self._holding(1, 3_310_000)])
        assert view.excluded[0].detail == "보유 33.1% — 이미 권장 비중(4.1%) 이상 보유, 종목 상한 10% 도 넘음"
        # 상한 안이면 상한을 말하지 않는다
        view = rp.build_portfolio([줄인], total_investable=10_000_000, holdings=[self._holding(1, 420_000)])
        assert view.excluded[0].detail == "보유 4.2% — 이미 권장 비중(4.1%) 이상 보유"

    def test_섹터_상한과_집중도는_보유를_포함한다(self) -> None:
        # 반도체를 이미 250만(25%) 들고 있다. 신호 100만을 더하면 35% → 상한 30% 초과
        view = rp.build_portfolio(
            [row(1, sector="반도체")], total_investable=10_000_000, holdings=[self._holding(9, 2_500_000, "반도체")]
        )
        assert view.excluded[0].reason == rs.EXCLUDED_SECTOR_CAP
        # 이 종목 몫과 합친 비율도 적는다 — "이미 25%, 상한 30%" 만으로는 왜 빠졌는지 읽히지 않았다 (25.649)
        assert re.search(r"이미 25\.0% \+ 이 종목 [\d.]+% → [\d.]+%, 상한 30%", view.excluded[0].detail or "")
        assert view.sector_concentration["반도체"] == pytest.approx(25.0)

    def test_보유가_총액보다_크면_분모는_보유_합(self) -> None:
        """총액 1,000만인데 보유가 1,200만 — 화면(25.238)과 같이 1,200만으로 나눈다 (docs/infra.md 25.294)."""
        view = rp.build_portfolio(
            [row(1)], total_investable=10_000_000,
            holdings=[self._holding(1, 1_800_000, "반도체"), self._holding(9, 10_200_000, "은행")],
        )  # fmt: skip
        assert view.excluded[0].reason == rs.EXCLUDED_STOCK_CAP and "보유 15.0%" in view.excluded[0].detail
        assert view.sector_concentration["반도체"] == pytest.approx(15.0)

    def test_보유가_없으면_이전과_같다(self) -> None:
        a = rp.build_portfolio([row(1), row(2)], total_investable=10_000_000)
        b = rp.build_portfolio([row(1), row(2)], total_investable=10_000_000, holdings=[])
        assert a.remaining_budget == b.remaining_budget == 8_000_000 and b.holdings_value is None

    def test_리포트에_보유_줄이_있다(self) -> None:
        text = rp.render([row(1)], total_investable=10_000_000, signals_as_of="2026-09-16", holdings=[self._holding(9, 3_000_000)])
        assert "현재 보유 평가 3,000,000원 (1종목)" in text
        assert "남은 여력 6,000,000원" in text


class Test렌더:
    def test_기준일과_두_부가_모두_나온다(self) -> None:
        text = rp.render([row(1), row(2)], total_investable=10_000_000, signals_as_of="2026-09-16")
        assert "신호 기준일 2026-09-16" in text
        assert "1부 개별 종목" in text
        assert "2부 포트폴리오" in text
        assert "정합성 경고" not in text
        assert "1,000,000원" in text

    def test_신호가_없으면_1부만_없다고_적는다(self) -> None:
        text = rp.render([], total_investable=0, signals_as_of=None)
        assert "오늘 추천할 종목이 없습니다" in text
        assert "2부" not in text

    def test_추천이_0건이어도_보유가_있으면_2부를_싣고_머리말은_고른_수를_적는다(self) -> None:
        """머리말이 늘 "상위 5종목" 이었고, 추천이 없으면 2부(보유 평가·여력)가 통째로 빠졌다 (docs/infra.md 25.420)."""
        h = [rp.Holding(1, "005930", "삼성", None, 3_000_000.0)]
        text = rp.render([], total_investable=10_000_000, signals_as_of="2026-09-25", holdings=h)
        assert text.splitlines()[0] == "추천 (신호 기준일 2026-09-25)"
        assert "2부 포트폴리오" in text and "현재 보유 평가 3,000,000원" in text and "남은 여력 7,000,000원" in text
        두개 = rp.render([row(1), row(2)], total_investable=10_000_000, signals_as_of="2026-09-16")
        assert "상위 2종목" in 두개.splitlines()[0]
        # 한 종목에 기간이 여럿이어도 종목 수를 센다 (docs/infra.md 25.430)
        여러기간 = [row(1, horizon="short"), row(1, horizon="mid"), row(2, horizon="short"), row(2, horizon="mid"),
                row(2, horizon="long")]  # fmt: skip
        assert "상위 2종목" in rp.render(여러기간, total_investable=10_000_000, signals_as_of="2026-09-16").splitlines()[0]

    def test_미국은_달러로_적는다(self) -> None:
        rows = [row(1, currency="USD", suggested_amount=1500.0, tranche_plan=[])]
        text = rp.render(rows, total_investable=20_000, signals_as_of="2026-09-16")
        assert "$1,500.00" in text


# ----------------------------------------------------------------------
# 실제 스키마
# ----------------------------------------------------------------------


class Test국면줄:
    def test_머리에_국면_한_줄이_붙는다(self) -> None:
        text = rp.render([], total_investable=0, signals_as_of="2026-09-16", regime_line="시장 국면: KOSPI 약세")
        assert text.splitlines()[1] == "시장 국면: KOSPI 약세"

    def test_없으면_붙지_않는다(self) -> None:
        text = rp.render([], total_investable=0, signals_as_of="2026-09-16")
        assert "시장 국면" not in text


class _Result:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.columns = [d[0] for d in cursor.description or []]
        self.rows = cursor.fetchall()
        self.last_insert_rowid = cursor.lastrowid

    def scalar(self) -> Any:
        return self.rows[0][0] if self.rows else None

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]


class SqliteClient:
    """TursoClient 와 같은 모양으로 메모리 SQLite 를 감싼다."""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.conn.executescript(path.read_text(encoding="utf-8"))

    def execute(self, sql: str, args: list[Any] | None = None) -> _Result:
        return _Result(self.conn.execute(sql, args or []))

    def batch(self, statements: list[tuple[str, list[Any]]]) -> list[_Result]:
        return [self.execute(sql, args) for sql, args in statements]


class Test리포트_질의:
    def test_가장_최근_신호와_점수_성과_종가를_읽는다(self) -> None:
        client = SqliteClient()
        c = client.conn
        c.execute(
            "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
            " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', '2026-09-16')"
        )
        c.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-09-16', 248500, 'KRW', 't', '2026-09-16')"
        )
        c.execute(
            "INSERT INTO scores (stock_id, as_of_date, total_score, factor_scores, sentiment_score,"
            " sentiment_weight_used, weights_json, rank_in_market, calc_version, created_at)"
            " VALUES (1, '2026-09-16', 78.4, '{\"value\": 83}', 42, 10, '{}', 3, 1, 't')"
        )
        c.execute(
            "INSERT INTO performance_metrics (stock_id, as_of_date, window, cagr, mdd, sharpe, data_points,"
            " calc_version, created_at) VALUES (1, '2026-09-15', '3Y', 0.12, -0.3, 0.8, 700, 1, 't')"
        )
        for as_of, text in (("2026-09-09", "지난주"), ("2026-09-16", "이번 주")):
            c.execute(
                "INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high,"
                " currency, tranche_plan, suggested_weight_pct, suggested_amount, size_reduction,"
                " sector_cap_applied, rationale_text, rationale_data, calc_version, created_at)"
                " VALUES (1, ?, 'mid', '실적 모멘텀', 236075, 248500, 'KRW', ?, 10, 1000000, 1, 0, ?, ?, 1, 't')",
                [as_of, json.dumps([{"step": 1, "ratio": 0.4, "price": 248500, "amount": 400000}]), text,
                 json.dumps({"criteria": [{"name": "매출 증가", "label": "매출 증가", "display": "1", "value": 1, "threshold": "> 0",
                                          "source": "dart", "passed": True}]})],
            )

        rows, as_of = daily.load_signal_rows(client, "KR")  # type: ignore[arg-type]

        assert as_of == "2026-09-16"
        assert len(rows) == 1
        got = rows[0]
        assert got.rationale_text == "이번 주"
        assert got.total_score == 78.4
        assert got.factor_scores == {"value": 83}
        assert got.close == 248500
        assert got.cagr == 0.12
        assert got.tranche_plan[0]["price"] == 248500
        # 센티먼트를 읽고 1부에 분리 표시한다 (docs/infra.md 25.323)
        assert got.sentiment == 42
        assert "센티먼트 +42" in rp.render(rows, total_investable=0, signals_as_of=as_of)

    def test_신호가_없으면_빈_목록(self) -> None:
        rows, as_of = daily.load_signal_rows(SqliteClient(), "US")  # type: ignore[arg-type]
        assert rows == []
        assert as_of is None


class Test비중은_적은_금액에서_낸다:
    """**"총액 2억" 아래에 "100만원 (10.0%)" 이 실렸다** (docs/infra.md 25.193).

    신호가 저장한 `suggested_weight_pct` 는 **신호를 낸 날의 총액**으로 낸 값이다.
    리포트는 그날 다시 읽은 총액을 머리에 적는다(`jobs/daily` 가 `load_settings` 를 따로
    부른다). 둘이 다르면 머리의 총액과 아래 비중이 서로 맞지 않는다.

    다시 내는 코드는 있었지만 **깎였을 때만** 돌았다. 바로 위 주석이 그 이유를
    적어 두고 있었는데 조건만 좁았다 — 25.191 과 같은 모양이다.

    언제 갈리나: **신호 단계가 실패한 날**(리포트는 그대로 나간다), 사용자가 신호와
    리포트 사이에 총액을 바꾼 날, 그리고 미국 리포트(총액을 환율로 나눈다).
    """

    @staticmethod
    def _2부(총액: float):
        # 신호는 총액 1,000만 기준으로 10% = 100만원을 저장해 두었다 (`row()` 의 기본값).
        # 업종을 넣는다 — 업종이 없으면 25.194 의 말이 붙어 이 검사가 보려는 것과 섞인다
        return rp.build_portfolio([row(1, sector="반도체")], total_investable=총액, min_order_amount=0.0)

    def test_총액이_같으면_그대로(self) -> None:
        a = self._2부(10_000_000).allocations[0]

        assert (a.amount, a.weight_pct) == (1_000_000, 10.0)
        assert a.note == "", "달라진 것이 없는데 말을 붙였다"

    def test_총액이_커지면_비중도_작아진다(self) -> None:
        a = self._2부(200_000_000).allocations[0]

        assert a.amount == 1_000_000, "금액은 신호가 낸 것을 쓴다"
        assert a.weight_pct == pytest.approx(0.5), (
            f"머리에 2억을 적고 {a.weight_pct}% 라고 적으면 두 숫자가 서로 안 맞는다"
        )

    def test_두_숫자가_늘_서로_맞는다(self) -> None:
        """**이것이 이 검사의 요지다.** 화면의 두 수가 같은 총액에서 나와야 한다."""
        for 총액 in (5_000_000, 10_000_000, 33_000_000, 200_000_000):
            a = self._2부(총액).allocations[0]
            assert a.weight_pct == pytest.approx(a.amount / 총액 * 100), 총액

    def test_왜_다른지_말한다(self) -> None:
        a = self._2부(200_000_000).allocations[0]

        assert "신호를 낸 날" in a.note, f"비중만 바꾸고 이유를 안 적었다: {a.note!r}"
        assert "2026-09-16" in a.note, "어느 날 기준인지 적어야 확인할 수 있다"
        assert "신호 때 10.0%" in a.note

    def test_총액이_0_이면_2부가_없다(self) -> None:
        """총액을 안 넣으면 배분을 낼 수 없다. 나눗셈도 하지 않는다."""
        view = self._2부(0.0)

        assert view.allocations == []
        # 사유는 `_reason()` 이 정한다 — 총액이 0 이면 "투자 여력 부족" 이다.
        # 여기서 보려는 것은 **나눗셈을 안 하고도 안 죽는다**는 것이다
        assert view.excluded and view.excluded[0].reason


class Test업종을_모르면_2부에_적는다:
    """**섹터 상한을 못 봤다는 것을 텔레그램 2부가 말하지 않았다** (docs/infra.md 25.194).

    `build_portfolio` 는 `if sector and …` 로 업종이 없는 종목의 섹터 상한을 **건너뛴다.**
    못 보는 것은 어쩔 수 없다. 그런데 그 사실이 1부 신호의 `sector_cap_note` 로 **웹에만**
    뜨고, 돈을 나누는 텔레그램 2부에는 아무 말이 없었다. `services/signals` 가 그 자리에
    "조용히 넘어가지 않고 사유를 남긴다" 고 적어 두었는데 **한 갈래에만** 남았다(25.177).
    """

    def test_업종을_모르면_그_줄에_적는다(self) -> None:
        view = rp.build_portfolio([row(1, sector=None)], total_investable=10_000_000)

        a = view.allocations[0]
        assert sig.SECTOR_CAP_UNAVAILABLE in a.note, f"섹터 상한을 못 봤는데 안 적었다: {a.note!r}"

    def test_텔레그램_본문에_나온다(self) -> None:
        """**사용자가 보는 글**에 있어야 한다. 객체에만 있으면 소용이 없다."""
        out = rp.compose([row(1, sector=None)], total_investable=10_000_000, signals_as_of="2026-09-16")

        assert sig.SECTOR_CAP_UNAVAILABLE in out.text

    def test_업종을_알면_적지_않는다(self) -> None:
        """막기만 하는 코드도 통과하면 안 된다. 모든 줄에 붙으면 아무도 안 읽는다."""
        view = rp.build_portfolio([row(1, sector="반도체")], total_investable=10_000_000)

        assert sig.SECTOR_CAP_UNAVAILABLE not in view.allocations[0].note

    def test_같은_글을_쓴다(self) -> None:
        """웹의 1부와 텔레그램 2부가 **같은 말**을 해야 한다. 문구를 두 벌 두지 않는다."""
        import inspect

        원본 = inspect.getsource(rp.build_portfolio)

        assert "sig.SECTOR_CAP_UNAVAILABLE" in 원본
        assert "업종 데이터 없음" not in 원본, "문구를 글자로 다시 적었다 — 상수를 쓴다"


def test_상관_계산은_11일_넘게_벌어진_쌍을_하루로_세지_않는다() -> None:
    """docs/infra.md 25.277 — "같은 규칙" 이라 적고 따로 구현해 거래정지 뒤 재개 하루를 셌다."""
    from batch.services import report_picks as rp_

    왼 = {"2026-08-03": 100.0, "2026-08-04": 101.0, "2026-08-25": 150.0}
    오 = {"2026-08-03": 50.0, "2026-08-04": 50.5, "2026-08-25": 40.0}
    lr, rr = rp_._aligned_returns(왼, 오)
    assert len(lr) == 1 and len(rr) == 1  # 08-04 → 08-25 (21일) 쌍은 뺀다


def test_근거표가_빈_추천은_싣지_않고_뺀_수를_적는다() -> None:
    """CLAUDE.md "근거표를 만들 수 없는 추천은 표시하지 않는다" (docs/infra.md 25.489, 텔레그램 감사)."""
    글 = rp.render([row(1), row(2, criteria=[])], total_investable=0, signals_as_of="2026-09-16")
    assert "종목1" in 글 and "종목2" not in 글
    assert "근거표를 만들 수 없는 1종목은 싣지 않았습니다" in 글


def test_과거_성과에_창과_기준일을_붙인다() -> None:
    """1Y 값이 3년 연환산처럼 읽히지 않게 (25.489)."""
    글 = rp.render([row(1, cagr=0.85, mdd=-0.12, sharpe=2.1, perf_window="1Y", perf_as_of="2026-09-15")],
                   total_investable=0, signals_as_of="2026-09-16")  # fmt: skip
    assert "과거(1Y, 기준 2026-09-15) CAGR" in 글


def test_국면_줄은_반올림으로_모순되지_않고_날짜를_단다() -> None:
    from batch.services import trend

    r = trend.Regime(index_code="KOSPI", state="bear", date="2026-09-25", close=2639.6, sma=2640.4, days=200)
    글 = r.describe()
    assert "2,639.6 < 200일선 2,640.4" in 글 and "(2026-09-25)" in 글


def test_상관용_후보는_2부와_같다() -> None:
    """근거표가 빈 1위 대신 6위가 2부에 들어오면 그 종목 시세도 읽어야 상관으로 줄인다 (docs/infra.md 25.507, 감사)."""
    import inspect

    from batch.jobs import daily

    rows = [row(i, score=100.0 - i) for i in range(1, 7)]
    rows[0] = row(1, score=99.0, criteria=[])
    뽑힌 = {r.stock_id for r in rp.select_top(rp.with_criteria(rows))}
    assert 1 not in 뽑힌 and 6 in 뽑힌
    assert "report_picks.select_top(report_picks.with_criteria(signal_rows))" in inspect.getsource(daily)


def test_회차마다_한_주도_못_사면_뺀다() -> None:
    """$1,000 을 40/30/30 으로 $1,000·$950·$900 회차에 — 세 회차 모두 1주 미만인데 통과했다 (docs/infra.md 25.514)."""
    회차 = [{"step": 1, "ratio": 0.4, "price": 1000, "amount": 400}, {"step": 2, "ratio": 0.3, "price": 950, "amount": 300},
          {"step": 3, "ratio": 0.3, "price": 900, "amount": 300}]  # fmt: skip
    view = rp.build_portfolio([row(1, currency="USD", suggested_amount=1000.0, tranche_plan=회차)], 35_000.0,
                              currency="USD", max_stock_pct=100)  # fmt: skip
    assert view.allocations == [] and view.excluded[0].reason == rp.EXCLUDED_BELOW_MIN_ORDER
    # 사유는 판정한 방식대로 (25.523, 교차검증) — "$1,000.00 < 1주 $900.00" 같은 거짓이 되지 않게
    assert view.excluded[0].detail == "$1,000.00 — 분할 회차마다 금액이 1주 값보다 작습니다"
    # 한 회차라도 한 주를 사면 싣는다
    회차[0] = {"step": 1, "ratio": 0.4, "price": 300, "amount": 400}
    view = rp.build_portfolio([row(1, currency="USD", suggested_amount=1000.0, tranche_plan=회차)], 35_000.0,
                              currency="USD", max_stock_pct=100)  # fmt: skip
    assert len(view.allocations) == 1


def test_미국_금액은_센트까지_남긴다() -> None:
    """정수로 잘라 권장 $1,234.56 이 $1,235.00, 총액 $35,273.90 이 $35,273.00 이 됐다 (docs/infra.md 25.563, 감사 재현)."""
    분할 = [{"step": 1, "ratio": 0.4, "price": 100.0, "amount": 493.824}, {"step": 2, "ratio": 0.3, "price": 97.0, "amount": 370.368},
            {"step": 3, "ratio": 0.3, "price": 95.0, "amount": 370.368}]  # fmt: skip
    미국 = row(1, currency="USD", market="NASDAQ", suggested_amount=1_234.56, suggested_weight_pct=3.5, tranche_plan=분할)
    view = rp.build_portfolio([미국], total_investable=35_273.90, currency="USD")
    assert view.total_budget == 35_273.90
    assert view.allocations[0].amount == 1_234.56
    # 남은 여력·분할 합도 같은 단위 (25.567, 교차검증)
    assert view.remaining_budget == 34_039.34
    국내 = rp.build_portfolio([row(2)], total_investable=10_000_000.4)
    assert 국내.total_budget == 10_000_000 and 국내.allocations[0].amount == 1_000_000



def test_분할_합이_표시_금액과_같다() -> None:
    """조각마다 반올림해 합이 1센트 어긋났다 — $969.82 → 387.93 + 290.95 + 290.95 (docs/infra.md 25.568, 교차검증)."""
    분할 = [{"step": 1, "ratio": 0.4, "price": 100.0, "amount": 400.0}, {"step": 2, "ratio": 0.3, "price": 97.0, "amount": 300.0},
            {"step": 3, "ratio": 0.3, "price": 95.0, "amount": 300.0}]  # fmt: skip
    미국 = row(1, currency="USD", market="NASDAQ", suggested_amount=1_000.0, suggested_weight_pct=10.0, tranche_plan=분할)
    view = rp.build_portfolio([미국], total_investable=9_698.2, currency="USD")
    배분 = view.allocations[0]
    assert 배분.amount == 969.82
    assert round(sum(t["amount"] for t in 배분.tranches), 2) == 969.82
    assert all(round(t["amount"], 2) == t["amount"] for t in 배분.tranches)



def test_끝수를_모아도_1차가_한_주_아래로_내려가지_않는다() -> None:
    """25,005원 → 1차 10,001원 < 한 주 10,002원이라 모든 회차가 0주였다 (docs/infra.md 25.570, 교차검증)."""
    분할 = [{"step": 1, "ratio": 0.4, "price": 10_002.0, "amount": 10_002.0},
            {"step": 2, "ratio": 0.3, "price": 9_000.0, "amount": 7_501.5},
            {"step": 3, "ratio": 0.3, "price": 8_000.0, "amount": 7_501.5}]  # fmt: skip
    r = row(1, suggested_amount=25_005.0, suggested_weight_pct=10.0, tranche_plan=분할)
    view = rp.build_portfolio([r], total_investable=250_050.0)
    회차 = view.allocations[0].tranches
    assert 회차[0]["amount"] >= 10_002 and sum(t["amount"] for t in 회차) == 25_005



def test_표시된_회차_그대로_한_주를_본다() -> None:
    """3차를 내림하자 검사는 통과(반올림 전 3차 1주)인데 표시는 모든 회차 0주였다 (docs/infra.md 25.572, 교차검증)."""
    분할 = [{"step": 1, "ratio": 0.4, "price": 11_000.0, "amount": 10_667.6},
            {"step": 2, "ratio": 0.3, "price": 9_500.25, "amount": 8_000.7},
            {"step": 3, "ratio": 0.3, "price": 8_000.5, "amount": 8_000.7}]  # fmt: skip
    r = row(1, suggested_amount=26_669.0, suggested_weight_pct=10.0, tranche_plan=분할)
    view = rp.build_portfolio([r], total_investable=266_690.0)
    assert view.allocations == [] and view.excluded[0].reason == rp.EXCLUDED_BELOW_MIN_ORDER


def test_넘은_합계는_반올림해도_상한을_넘어_보인다() -> None:
    """30.004% 가 "→ 30.0%, 상한 30%" 로 적혀 넘지 않은 듯 보였다 (docs/infra.md 25.653, 교차검증)."""
    assert rp._넘은_비율(30.004, 30) == "30.004%"
    assert rp._넘은_비율(30.04, 30) == "30.04%"
    assert rp._넘은_비율(33.0, 30) == "33.0%"
    assert rp._넘은_비율(30.0001, 30) == "30.000% 초과"


def test_산술로_정확히_상한이면_빼지_않는다() -> None:
    """종목 상한 10% 로 깎인 셋이 정확히 30% 인데 부동소수로 "초과" 가 되어 빠졌다 (docs/infra.md 25.656, 교차검증)."""
    rows = [row(i, sector="반도체", suggested_amount=5_000_000.0, tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10}])
            for i in (1, 2, 3)]  # fmt: skip
    view = rp.build_portfolio(rows, total_investable=12_345_678)
    assert [e.reason for e in view.excluded if e.reason == rs.EXCLUDED_SECTOR_CAP] == [], view.excluded


def test_상한으로_깎인_열_종목이_정확히_100퍼센트면_모두_들어간다() -> None:
    """아홉 번 뺀 남은 여력이 열째 금액보다 한 끗 작아 "투자 여력 부족" 으로 빠졌다 (docs/infra.md 25.658, 교차검증)."""
    rows = [row(i, sector=f"업종{i}", suggested_amount=5_000_000.0, tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10}])
            for i in range(1, 11)]  # fmt: skip
    view = rp.build_portfolio(rows, total_investable=1_001_994, max_sector_pct=100)
    assert [e.reason for e in view.excluded if e.reason == rs.EXCLUDED_NO_BUDGET] == [], view.excluded


def test_화면에_못_그리는_근거표만_있는_추천은_리포트에도_싣지_않는다() -> None:
    """웹 `parseCriteria` 가 네 칸이 문자열이 아닌 행을 조용히 버린다 — 리포트는 비었는지만 봤다 (docs/infra.md 25.821)."""
    좋음 = row(1)
    못그림 = row(2, criteria=[{"name": "매출 증가", "value": 1, "passed": True}])  # label·display·source 없음
    assert {r.stock_id for r in rp.with_criteria([좋음, 못그림])} == {1}


def test_샤프는_마이너스_영을_내지_않는다() -> None:
    from batch.notify import report_sections as rs

    pick = rp.to_picks([row(1)])[0]
    pick.sharpe = -0.004
    글 = "".join(rs.render_picks([pick]))
    assert "샤프 0.00" in 글 and "-0.00" not in 글


def test_근거_모양이_이상해도_리포트를_죽이지_않는다() -> None:
    """dict·문자열 근거가 `.get` AttributeError 로 리포트 전체를 죽였다 (docs/infra.md 25.822, 교차검증)."""
    from batch.services import criteria as crit

    for 이상한 in ({"a": 1}, ["x"], "abc", [1], None):
        assert crit.usable(이상한) is False
