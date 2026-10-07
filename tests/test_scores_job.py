"""점수 적재 테스트.

파싱은 촘촘히 테스트했는데 적재는 안 해서 재무 배치가 운영에서 죽은 적이
있다. 여기서는 실제로 만드는 값 묶음이 스키마와 맞는지, 입력이 없을 때
값을 지어내지 않는지를 본다. 네트워크를 타지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.core import db
from batch.jobs import scores as job
from batch.services import scoring as sc

ROOT = Path(__file__).resolve().parent.parent


class FakeClient:
    def __init__(self) -> None:
        self.batched: list[list[tuple[str, list]]] = []

    def batch(self, statements):
        self.batched.append(statements)
        return []


class Test열개수:
    """값 묶음과 열 개수가 어긋나면 그 자리에서 멈춰야 한다."""

    def test_factors_열_개수(self) -> None:
        assert db.column_count(job._FACTOR_COLS) == 11

    def test_scores_열_개수(self) -> None:
        assert db.column_count(job._SCORE_COLS) == 12

    def test_값이_모자라면_무엇을_고칠지_알려준다(self) -> None:
        client = FakeClient()
        with pytest.raises(ValueError) as exc:
            job._bulk(client, "factors", job._FACTOR_COLS, "stock_id", [(1, 2, 3)])
        assert "열" in str(exc.value)

    def test_빈_목록은_넣지_않는다(self) -> None:
        client = FakeClient()
        assert job._bulk(client, "factors", job._FACTOR_COLS, "stock_id", []) == 0
        assert client.batched == []

    def test_여러_행을_한_요청으로_보낸다(self) -> None:
        client = FakeClient()
        rows = [tuple(range(11)) for _ in range(500)]
        job._bulk(client, "factors", job._FACTOR_COLS, "stock_id", rows)
        assert len(client.batched) == 1


class Test이익안정성:
    def test_최근_5년만_본다(self) -> None:
        years = {
            2019: {"operating_income": -10},  # 창 밖
            2021: {"operating_income": 10},
            2022: {"operating_income": -5},
            2023: {"operating_income": 10},
            2024: {"operating_income": 10},
            2025: {"operating_income": 10},
        }
        profitable, observed = job._stability_counts(years, 2025)
        assert observed == 5
        assert profitable == 4

    def test_영업이익이_없는_해는_세지_않는다(self) -> None:
        years = {
            2024: {"operating_income": None},
            2025: {"operating_income": 10},
        }
        profitable, observed = job._stability_counts(years, 2025)
        assert observed == 1
        assert profitable == 1

    def test_재무가_없으면_없다(self) -> None:
        assert job._stability_counts({}, None) == (None, None)


def universe_row(stock_id: int, market: str = "KOSPI", market_cap: int | None = 1000):
    return {
        "stock_id": stock_id,
        "ticker": f"{stock_id:06d}",
        "market": market,
        "sector": None,
        "market_cap": market_cap,
    }


class Test입력조립:
    def test_재무와_시세가_있으면_지표가_채워진다(self) -> None:
        financials = {
            1: {
                2025: {
                    "net_income": 100,
                    "total_equity": 500,
                    "total_assets": 1000,
                    "total_liabilities": 500,
                    "revenue": 800,
                    "operating_income": 120,
                },
                2024: {
                    "net_income": 80,
                    "total_equity": 400,
                    "total_assets": 900,
                    "total_liabilities": 500,
                    "revenue": 640,
                    "operating_income": 100,
                },
            }
        }
        # 모멘텀은 **종목 자신의 계열**에서 뒤로 세어 잡는다 (2026-09-21, docs/infra.md 25.100).
        # 64개면 3개월(63 거래일 전)까지 닿는다
        계열 = [100.0] * 63 + [110.0]

        inputs = job.build_inputs(
            [universe_row(1)], financials, {}, series={1: (_날짜(len(계열)), 계열, [])}
        )

        m = inputs[0].metrics
        assert m["ep"] == pytest.approx(0.1)  # 100 / 1000
        assert m["roe"] == pytest.approx(0.2)  # 100 / 500
        assert m["revenue_growth"] == pytest.approx(0.25)  # 800 / 640 - 1
        assert m["momentum_3m"] == pytest.approx(0.1)  # 110 / 100 - 1

    def test_재무가_없으면_지어내지_않는다(self) -> None:
        """미국이 지금 이 상태다. 버그가 아니라 데이터가 없는 것이다."""
        inputs = job.build_inputs([universe_row(1, "NASDAQ", None)], {}, {})

        m = inputs[0].metrics
        assert m["ep"] is None
        assert m["roe"] is None
        assert m["revenue_growth"] is None
        assert m["momentum_3m"] is None

    def test_시가총액이_없으면_밸류가_비고_나머지는_산다(self) -> None:
        financials = {
            1: {
                2025: {
                    "net_income": 100,
                    "total_equity": 500,
                    "total_assets": 1000,
                    "total_liabilities": 500,
                    "revenue": 800,
                    "operating_income": 120,
                }
            }
        }
        inputs = job.build_inputs([universe_row(1, "NASDAQ", None)], financials, {})

        m = inputs[0].metrics
        assert m["ep"] is None
        assert m["roe"] == pytest.approx(0.2)

    def test_거래일이_모자라면_모멘텀이_비어_있다(self) -> None:
        """없는 구간을 있는 데이터로 늘려 계산하지 않는다 (docs/factors.md 3.4)."""
        inputs = job.build_inputs([universe_row(1)], {}, {}, series={1: (_날짜(10), [100.0] * 10, [])})
        assert inputs[0].metrics["momentum_12_1"] is None

    def test_성과지표의_미회복은_결측으로_넘어간다(self) -> None:
        metrics = {
            1: {
                "mdd": -0.4,
                "volatility_ann": 0.3,
                "sharpe": 1.0,
                "sortino": 1.2,
                "beta": -0.5,
                "mdd_recovery_days": None,
                "cagr": 0.2,
            }
        }
        inputs = job.build_inputs([universe_row(1)], {}, metrics)

        m = inputs[0].metrics
        assert m["mdd_abs"] == pytest.approx(0.4)
        assert m["beta_abs"] == pytest.approx(0.5)
        assert m["mdd_recovery_days"] is None


class Test순위:
    def test_집단별로_따로_매긴다(self) -> None:
        totals = {
            1: sc.TotalScore(total=90.0, weights_used={}, sentiment_weight_used=0.0),
            2: sc.TotalScore(total=95.0, weights_used={}, sentiment_weight_used=0.0),
            3: sc.TotalScore(total=50.0, weights_used={}, sentiment_weight_used=0.0),
        }
        grouping = {1: "KOSPI", 2: "NASDAQ", 3: "KOSPI"}
        ranks = job._ranks_by(totals, grouping)

        # 시장이 다르면 서로의 순위에 끼어들지 않는다
        assert ranks[1] == 1
        assert ranks[2] == 1
        assert ranks[3] == 2

    def test_점수가_없으면_순위가_없다(self) -> None:
        totals = {
            1: sc.TotalScore(
                total=None,
                weights_used={},
                sentiment_weight_used=0.0,
                skip_reason=sc.SKIP_TOO_MANY_MISSING,
            )
        }
        assert job._ranks_by(totals, {1: "KOSPI"}) == {}


class Test저장형태:
    def test_빠진_지표를_JSON으로_남긴다(self) -> None:
        """왜 이 종목이 점수가 없는지 DB만 보고 답할 수 있어야 한다."""
        inputs = [
            sc.StockInput(stock_id=1, market="KOSPI", metrics={"momentum_3m": 0.1})
        ]
        results = [r for r in sc.score_factors(inputs) if r.factor == "value"]
        encoded = json.dumps(results[0].missing_fields, ensure_ascii=False)

        # 2026-09-21: 밸류가 3개에서 4개가 됐다 (배당수익률, docs/factors.md 11.1)
        assert json.loads(encoded) == ["ep", "bp", "sp", "dividend_yield"]


# ----------------------------------------------------------------------
# 계열 지표 (docs/factors.md 10.1). 2026-09-17 추가
# ----------------------------------------------------------------------


def _rising_series(days: int = 274, value: float = 1_000_000_000.0):
    """매일 1% 오르는 (날짜, 종가, 거래대금 10억). 계열 지표가 모두 계산되는 최소 길이.

    2026-09-21 에 **날짜가 더해졌다** — 잔차 변동성이 지수와 날짜를 맞춰야 한다
    (docs/factors.md 11.3).
    """
    from datetime import date, timedelta

    기준 = date(2025, 1, 1)
    dates = [(기준 + timedelta(days=i)).isoformat() for i in range(days)]
    closes = [100.0 * (1.01**i) for i in range(days)]
    return dates, closes, [value] * days


class Test계열지표조립:
    def test_계열이_있으면_네_지표가_채워진다(self) -> None:
        series = {1: _rising_series()}
        inputs = job.build_inputs([universe_row(1)], {}, {}, series=series)

        m = inputs[0].metrics
        # 매일 올랐으니 오늘이 52주 고점 = 1, 꾸준함 = 1
        assert m["high_52w_proximity"] == pytest.approx(1.0)
        assert m["momentum_consistency"] == pytest.approx(1.0)
        # 매일 똑같이 올라 변동성이 0 → 수익률/0 은 없다
        assert m["momentum_vol_adjusted"] is None
        # |r| = 0.01, 거래대금/10억 = 1 → 0.01
        assert m["amihud_illiquidity"] == pytest.approx(0.01, rel=1e-6)

    def test_흔들리면_변동성_조정_모멘텀이_생긴다(self) -> None:
        dates, closes, values = _rising_series()
        noisy = [c + (0.5 if i % 2 else 0.0) for i, c in enumerate(closes)]
        inputs = job.build_inputs([universe_row(1)], {}, {}, series={1: (dates, noisy, values)})
        assert inputs[0].metrics["momentum_vol_adjusted"] > 0

    def test_계열이_짧으면_긴_창_지표만_빈다(self) -> None:
        """64개면 3개월(63 거래일)까지만 닿는다. 252일이 필요한 것은 None 이다."""
        계열 = [100.0] * 63 + [110.0]
        inputs = job.build_inputs([universe_row(1)], {}, {}, series={1: (_날짜(len(계열)), 계열, [])})

        m = inputs[0].metrics
        assert m["momentum_3m"] == pytest.approx(0.1)
        for key in ("high_52w_proximity", "momentum_consistency", "momentum_vol_adjusted", "amihud_illiquidity"):
            assert key in m and m[key] is None

    def test_거래대금이_없으면_아미후드만_빈다(self) -> None:
        dates, closes, _values = _rising_series()
        inputs = job.build_inputs([universe_row(1)], {}, {}, series={1: (dates, closes, [])})
        m = inputs[0].metrics
        assert m["high_52w_proximity"] == pytest.approx(1.0)
        assert m["amihud_illiquidity"] is None


def _sqlite_client():
    """실제 마이그레이션을 적용한 메모리 SQLite. 종목 1(KR)·2(US)이 들어 있다."""
    import sqlite3
    from typing import Any

    from batch.core.turso import ResultSet

    class Sqlite:
        def __init__(self) -> None:
            self.conn = sqlite3.connect(":memory:")

        def execute(self, sql: str, args: list[Any] | None = None) -> ResultSet:
            cur = self.conn.execute(sql, args or [])
            cols = [d[0] for d in cur.description or []]
            return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=cur.lastrowid)

        def batch(self, statements: list[tuple[str, list[Any]]]) -> list[ResultSet]:
            return [self.execute(sql, args) for sql, args in statements]

        def close(self) -> None:
            pass

    client = Sqlite()
    db.apply_migrations(client)  # type: ignore[arg-type]
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (1, 'A', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
    )
    client.conn.execute(
        "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
        " VALUES (2, 'B', 'NASDAQ', 'US', 'USD', 'active', 't', 't')"
    )
    return client


class Test계열읽기:
    """load_series: 최근 days 거래일만, 수정주가로, 거래대금은 저장값 우선."""

    def _client(self):
        client = _sqlite_client()
        rows = [
            # (date, close, adj_close, volume, value)
            ("2026-01-02", 100, 50, 10, 7000),  # 저장된 거래대금이 있으면 그것을 쓴다
            ("2026-01-05", 110, 55, 10, None),  # 없으면 원 종가 × 거래량 = 1100
            ("2026-01-06", 120, None, None, None),  # adj_close 없으면 close, 거래대금은 모른다
        ]
        for date, close, adj, vol, value in rows:
            client.conn.execute(
                "INSERT INTO prices (stock_id, date, close, adj_close, volume, value, currency, source, fetched_at)"
                " VALUES (1, ?, ?, ?, ?, ?, 'KRW', 't', 't')",
                [date, close, adj, vol, value],
            )
        # 다른 나라 종목은 섞이지 않는다
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (2, '2026-01-06', 999, 'USD', 't', 't')"
        )
        # 계열은 그 시점 유니버스 편입 종목만 읽는다 — 점수를 내는 종목이 그것뿐이다 (docs/infra.md 25.865)
        for sid in (1, 2):
            client.conn.execute(
                "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                " VALUES ('2026-01-01', ?, 1, 'KRW', 't')",
                [sid],
            )
        return client

    def test_최근_거래일만_수정주가로_읽는다(self) -> None:
        client = self._client()
        series = job.load_series(client, "KR", "2026-01-06", days=2)  # type: ignore[arg-type]
        assert set(series) == {1}
        dates, closes, values = series[1]
        assert dates == ["2026-01-05", "2026-01-06"]  # 날짜도 들고 온다 (docs/factors.md 11.3)
        assert closes == [55.0, 120.0]  # adj_close 우선, 없으면 close
        assert values == [1100.0, None]  # close × volume, 둘 다 없으면 None

    def test_저장된_거래대금이_우선이다(self) -> None:
        client = self._client()
        _dates, _closes, values = job.load_series(client, "KR", "2026-01-06", days=3)[1]  # type: ignore[arg-type]
        assert values[0] == 7000.0

    def test_기준일_뒤는_읽지_않는다(self) -> None:
        client = self._client()
        _dates, closes, _values = job.load_series(client, "KR", "2026-01-05", days=10)[1]  # type: ignore[arg-type]
        assert closes == [50.0, 55.0]

    def test_구멍_난_종목은_제_거래일로_창을_채운다(self) -> None:
        """시장 날짜 days 번째부터만 읽어 정지일이 있는 종목은 행이 모자라 12-1 모멘텀이 NULL 이었다 (docs/infra.md 25.556)."""
        client = self._client()
        client.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (3, '000003', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
        )
        client.conn.execute(
            "INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
            " VALUES ('2026-01-01', 3, 1, 'KRW', 't')"
        )
        for d in ("2026-01-02", "2026-01-06"):  # 01-05 정지
            client.conn.execute(
                "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (3, ?, 10, 'KRW', 't', 't')",
                [d],
            )
        series = job.load_series(client, "KR", "2026-01-06", days=2)  # type: ignore[arg-type]
        assert series[3][0] == ["2026-01-02", "2026-01-06"]  # 두 행 — 구멍만큼 더 앞으로
        assert series[1][0] == ["2026-01-05", "2026-01-06"]  # 구멍 없는 종목은 그대로 끝 days 행

    def test_유니버스_밖_종목은_읽지_않는다_25_865(self) -> None:
        """시세 표 전체를 읽던 것을 편입 종목만 읽게 바꿨다 — 점수는 편입 종목에만 낸다."""
        client = self._client()
        client.conn.execute("UPDATE universe_members SET included = 0 WHERE stock_id = 1")
        assert job.load_series(client, "KR", "2026-01-06", days=2) == {}  # type: ignore[arg-type]

    def test_거래일이_없으면_빈_사전(self) -> None:
        client = self._client()
        assert job.load_series(client, "KR", "2025-12-31", days=10) == {}  # type: ignore[arg-type]


class Test재무제표지표조립:
    """자산 성장률·유동비율·피오트로스키가 적재 경로에서 채워진다 (docs/factors.md 10.2)."""

    FIN = {
        2025: {
            "net_income": 100, "total_equity": 500, "total_assets": 1000, "total_liabilities": 400,
            "noncurrent_liabilities": 100, "current_assets": 300, "current_liabilities": 150,
            "revenue": 800, "operating_income": 120,
        },
        2024: {
            "net_income": 50, "total_equity": 400, "total_assets": 900, "total_liabilities": 400,
            "noncurrent_liabilities": 150, "current_assets": 250, "current_liabilities": 150,
            "revenue": 600, "operating_income": 60,
        },
    }

    def test_두_해가_있으면_세_지표가_채워진다(self) -> None:
        inputs = job.build_inputs([universe_row(1)], {1: self.FIN}, {})
        m = inputs[0].metrics
        assert m["asset_growth"] == pytest.approx(1000 / 900 - 1)
        assert m["current_ratio"] == pytest.approx(2.0)
        assert m["piotroski_lite"] == 6.0

    def test_한_해뿐이면_유동비율만_남는다(self) -> None:
        inputs = job.build_inputs([universe_row(1)], {1: {2025: self.FIN[2025]}}, {})
        m = inputs[0].metrics
        assert m["current_ratio"] == pytest.approx(2.0)
        assert m["asset_growth"] is None
        assert m["piotroski_lite"] is None

    def test_적재_질의가_새_컬럼을_읽는다(self) -> None:
        client = _sqlite_client()
        for year, v in self.FIN.items():
            client.conn.execute(
                "INSERT INTO financials (stock_id, fiscal_year, report_code, period_type, consolidated,"
                " report_date, receipt_no, currency, unit, current_assets, current_liabilities,"
                " noncurrent_liabilities, total_assets, total_liabilities, total_equity, revenue,"
                " operating_income, net_income, source, fetched_at)"
                " VALUES (1, ?, ?, 'A', 1, ?, ?, 'KRW', '원', ?, ?, ?, ?, ?, ?, ?, ?, ?, 't', 't')",
                [
                    year, job.ANNUAL_REPORT_CODE, f"{year + 1}-03-10", f"r{year}",
                    v["current_assets"], v["current_liabilities"], v["noncurrent_liabilities"],
                    v["total_assets"], v["total_liabilities"], v["total_equity"], v["revenue"],
                    v["operating_income"], v["net_income"],
                ],
            )
        financials = job.load_financials(client, "KR", "2030-01-01")  # type: ignore[arg-type]
        inputs = job.build_inputs([universe_row(1)], financials, {})
        assert inputs[0].metrics["piotroski_lite"] == 6.0
        assert inputs[0].metrics["current_ratio"] == pytest.approx(2.0)


def _날짜(n: int) -> list[str]:
    """`load_series` 는 날짜와 종가를 **늘 함께** 담는다 (길이가 같다).

    시험 자료도 그래야 한다. 빈 날짜에 종가만 주면 `zip(..., strict=True)` 가 막는데,
    **그 막는 것이 옳다** — 길이가 어긋난 채로 지나가면 잔차 변동성이 엉뚱한 날을 맞춘다.
    """
    from datetime import date, timedelta

    끝 = date(2026, 9, 18)
    return [(끝 - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


class Test범위_밖_설정은_기본값으로_되돌린다:
    """**되돌리고 말한다** (docs/infra.md 25.169).

    점수를 아예 안 내면 그날 추천이 통째로 빈다. 설정 한 줄 때문에 치르기에는 큰 값이다.
    대신 실행 기록(`step_log.setting_warnings`)과 화면에 남긴다.
    """

    class _설정:
        def __init__(self, 값: dict) -> None:
            self.값 = 값

    @staticmethod
    def _읽기(monkeypatch, 값: dict):
        from batch.jobs import scores as job

        monkeypatch.setattr(job.db, "get_setting", lambda _c, key, default=None, **_k: 값.get(key, default))
        return job.load_weights(object())  # type: ignore[arg-type]

    def test_멀쩡하면_조용하다(self, monkeypatch) -> None:
        w, sw, 경고 = self._읽기(
            monkeypatch,
            {"factor_weights": {"value": 30, "quality": 20, "growth": 20, "momentum": 20, "risk": 10}, "sentiment_weight": 10},
        )
        assert w["value"] == 30 and sw == 10
        assert 경고 == []

    def test_센티먼트_가중치가_범위를_넘으면_기본값으로(self, monkeypatch) -> None:
        """기본값은 CLAUDE.md 의 10% 다. 예전 기본값 0 이 여기 박혀 있었다 (docs/infra.md 25.230)."""
        _w, sw, 경고 = self._읽기(monkeypatch, {"sentiment_weight": 150})

        assert sw == job.DEFAULT_SENTIMENT_WEIGHT == 10.0
        assert len(경고) == 1
        assert "sentiment_weight" in 경고[0]
        assert "설정 화면에서 고치세요" in 경고[0], "무엇을 해야 하는지 말해야 한다"

    def test_음수_팩터_가중치는_기본값으로(self, monkeypatch) -> None:
        w, _sw, 경고 = self._읽기(monkeypatch, {"factor_weights": {"value": -50}})

        assert w["value"] == 20.0
        assert any("설정 value" in 줄 for 줄 in 경고), 경고

    def test_칸이_빠지면_말한다(self, monkeypatch) -> None:
        """말없이 기본값 20 을 써 화면("읽지 못한 칸")과 배치가 갈렸다 (docs/infra.md 25.765, 설정 감사)."""
        w, _sw, 경고 = self._읽기(monkeypatch, {"factor_weights": {"value": 100}})
        assert w["quality"] == 20.0
        assert any("'quality' 칸이 없어" in 줄 for 줄 in 경고), 경고
        # 빈 표·목록도 말한다 (25.767)
        for 모양 in ({}, [], "x"):
            _w, _sw, 경고 = self._읽기(monkeypatch, {"factor_weights": 모양})
            assert any("읽지 못해 기본값" in 줄 for 줄 in 경고), (모양, 경고)
        # 저장된 표가 아예 없으면(처음 쓰는 사람) 말하지 않는다
        _w, _sw, 경고 = self._읽기(monkeypatch, {})
        assert not any("칸이 없어" in 줄 for 줄 in 경고), 경고

    def test_다섯이_모두_0이면_기본값으로(self, monkeypatch) -> None:
        """하나하나는 범위 안이라 통과하고 전 종목이 빠지던 것 (docs/infra.md 25.559)."""
        w, _sw, 경고 = self._읽기(
            monkeypatch, {"factor_weights": {"value": 0, "quality": 0, "growth": 0, "momentum": 0, "risk": 0}}
        )
        assert w == job.DEFAULT_WEIGHTS
        assert any("모두 0" in 줄 for 줄 in 경고), 경고

    def test_배치가_그_경고를_남기고_말한다(self) -> None:
        글 = (ROOT / "batch" / "jobs" / "scores.py").read_text(encoding="utf-8")

        assert "setting_warnings" in 글, "실행 기록에 없으면 나중에 알 길이 없다"
        assert "for 줄 in 설정경고" in 글, "사람이 보는 줄에도 나와야 한다"


class Test점수_재료를_못_읽으면_남긴다:
    """배당·지수를 못 읽으면 온 시장이 그 재료 없이 계산된다 — 조용히 넘어가지 않는다 (docs/infra.md 25.221)."""

    @staticmethod
    def _막힌():
        class 막힘:
            def execute(self, sql, args=None):
                raise RuntimeError("D1 일일 읽기 한도 초과")

        return 막힘()

    @staticmethod
    def _표없음():
        class 없음:
            def execute(self, sql, args=None):
                raise RuntimeError("no such table: stock_dividends")

        return 없음()

    def test_다른_실패는_남긴다(self) -> None:
        from batch.jobs import scores as job

        못읽음: list[str] = []
        assert job.load_dividends(self._막힌(), "KR", "2026-09-16", 못읽음) == {}  # type: ignore[arg-type]
        assert job.load_benchmark_closes(self._막힌(), "KR", 10, "2026-09-16", 못읽음) == {}  # type: ignore[arg-type]
        assert len(못읽음) == 2 and "배당" in 못읽음[0] and "지수" in 못읽음[1]

    def test_표가_없으면_조용하다(self) -> None:
        from batch.jobs import scores as job

        못읽음: list[str] = []
        assert job.load_dividends(self._표없음(), "KR", "2026-09-16", 못읽음) == {}  # type: ignore[arg-type]
        assert 못읽음 == []


def test_미국_무배당과_배당을_끊은_종목은_0이다() -> None:
    """10-K 가 있는데 그해 배당 행이 없으면 무배당 — NULL·옛 배당이 남았다 (docs/infra.md 25.557·25.561, 감사 재현)."""
    def 연(날: str) -> dict:
        return {"report_date": 날}

    배당 = {2: 50.0, 3: 30.0, 5: 40.0}
    연도 = {2: 2025, 3: 2023, 5: 2024}  # 3 은 2024 부터 배당을 끊었다
    재무 = {1: {2025: 연("2026-02-20")}, 2: {2024: 연("2025-02-20"), 2025: 연("2026-02-20")},
            3: {2024: 연("2025-02-20"), 2025: 연("2026-02-20")}, 4: {},
            # 5 는 오늘(기준일) 2025 10-K 를 냈다 — 배당 행은 다음 거래일부터 보여 아직 2024 다. 0 이 아니다 (25.561)
            5: {2024: 연("2025-02-20"), 2025: 연("2026-03-02")}}  # fmt: skip
    assert job.fill_us_no_dividend(배당, 연도, 재무, "2026-03-02") == 2
    assert 배당 == {1: 0.0, 2: 50.0, 3: 0.0, 5: 40.0}  # 4 는 재무가 없어 모름으로 남는다
    import inspect

    assert "fill_us_no_dividend(dividends, 배당연도, financials, as_of)" in inspect.getsource(job.run)


def test_백테스트도_같은_규칙으로_미국_무배당을_0으로() -> None:
    """25.557 은 적재에만 넣어 백테스트와 운영이 갈렸다 (docs/infra.md 25.561, 교차검증)."""
    from batch.jobs import backtest as bj
    from batch.services import scoring as sc

    assert sc.us_dividend_or_zero(None, None, 2025) == 0.0
    assert sc.us_dividend_or_zero(30.0, 2023, 2025) == 0.0
    assert sc.us_dividend_or_zero(40.0, 2025, 2025) == 40.0
    assert sc.us_dividend_or_zero(40.0, 2025, None) == 40.0
    assert bj.pit_dividend_row([("2025-02-21", 2024, 40.0)], "2026-01-01") == (2024, 40.0)
    import inspect

    원본 = inspect.getsource(bj.build_pit_inputs)
    assert "sc.us_dividend_or_zero(" in 원본 and "latest_10k_year(" in 원본
    # 미국 스냅샷의 as_of_date 는 제출 다음 거래일 — 배당 행과 같은 날이라 그날 바로 센다 (25.565, 교차검증)
    스냅 = [{"as_of_date": "2025-02-21", "fiscal_year": 2024}, {"as_of_date": "2026-02-23", "fiscal_year": 2025}]
    assert bj.latest_10k_year(스냅, "2026-02-23") == 2025
    assert bj.latest_10k_year(스냅, "2026-02-20") == 2024


def test_미회복이면_바닥_뒤_행_수를_하한으로_넘긴다() -> None:
    """적재와 백테스트가 같은 하한을 쓴다 (docs/infra.md 25.691)."""
    날 = [f"2026-09-{d:02d}" for d in range(1, 11)]
    계열 = {1: (날, [100.0] * 10, [None] * 10)}
    지표 = {1: {"mdd": -0.1, "mdd_recovery_days": None, "mdd_trough_date": "2026-09-04"}}
    inputs = job.build_inputs([universe_row(1)], {}, 지표, 계열)  # type: ignore[arg-type]
    assert inputs[0].metrics[sc.UNRECOVERED_ROWS] == 6.0
    지표[1]["mdd_recovery_days"] = 3  # type: ignore[assignment]
    inputs = job.build_inputs([universe_row(1)], {}, 지표, 계열)  # type: ignore[arg-type]
    assert sc.UNRECOVERED_ROWS not in inputs[0].metrics


def test_바닥_뒤_행_수는_점수_계열이_아니라_시세_전체에서_센다() -> None:
    """점수 계열은 274행뿐이라 750행 전 바닥의 하한이 274 에서 멈춰 백테스트와 순위가 뒤집혔다 (docs/infra.md 25.693)."""
    from datetime import date, timedelta

    client = _sqlite_client()
    시작 = date(2024, 1, 1)
    for k in range(600):
        client.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, 100, 'KRW', 't', 't')",
            [(시작 + timedelta(days=k)).isoformat()],
        )
    바닥 = (시작 + timedelta(days=149)).isoformat()
    기준일 = (시작 + timedelta(days=599)).isoformat()
    지표 = {1: {"mdd": -0.6, "mdd_recovery_days": None, "mdd_trough_date": 바닥}, 2: {"mdd_recovery_days": 5}}
    job.attach_unrecovered_rows(client, 지표, 기준일)  # type: ignore[arg-type]
    assert 지표[1]["unrecovered_rows"] == 450.0
    assert "unrecovered_rows" not in 지표[2]
    # 계열이 짧아도(274) 붙인 값을 쓴다
    날 = [(시작 + timedelta(days=k)).isoformat() for k in range(326, 600)]
    inputs = job.build_inputs([universe_row(1)], {}, 지표, {1: (날, [100.0] * len(날), [None] * len(날))})
    assert inputs[0].metrics[sc.UNRECOVERED_ROWS] == 450.0


def test_묵은_사업보고서의_연간_재무는_점수에_쓰지_않는다() -> None:
    """감사 지연·의견거절로 새 사업보고서가 없는 회사의 2년 묵은 재무로 밸류·퀄리티를 매겼다 (docs/infra.md 25.804)."""
    from batch.jobs import scores as job

    재무 = {
        1: {2024: {"report_date": "2025-03-11", "net_income": 1.0}},  # 2026-09-30 기준 568일 — 묵었다
        2: {2025: {"report_date": "2026-03-15", "net_income": 1.0}},  # 정상
    }
    사유 = job.drop_stale_annual(재무, "2026-09-30")
    assert list(사유) == [1] and "FY2024" in 사유[1]
    assert 재무[1] == {} and 재무[2]


def test_백테스트도_같은_문턱() -> None:
    from batch.jobs import backtest as bt_job

    묵음 = [{"as_of_date": "2025-03-11", "fiscal_year": 2024, "values": {}}]
    assert bt_job.pit_financials(묵음, "2026-09-30") == (None, None, None)
    assert bt_job.pit_financials(묵음, "2025-12-31")[0] is not None


def test_정정공시로_새로워진_접수일도_점수·백테스트에서_묵음이다() -> None:
    """25.804 가 접수일 문턱만 옮겨 25.663 의 정정공시 구멍이 점수·백테스트에서 다시 열렸다 (25.808, 교차검증)."""
    from batch.jobs import backtest as bt_job
    from batch.jobs import scores as job
    from batch.services import scoring as sc

    재무 = {1: {2024: {"report_date": "2025-12-01", "net_income": 1.0}}}  # FY2024 정정, FY2025 없음
    사유 = job.drop_stale_annual(재무, "2026-09-30")
    assert 재무[1] == {} and "2025 회계연도" in 사유[1]
    정정 = [{"as_of_date": "2025-12-01", "fiscal_year": 2024, "values": {}}]
    assert bt_job.pit_financials(정정, "2026-09-30") == (None, None, None)
    assert bt_job.pit_financials(정정, "2026-06-30")[0] is not None  # 7월 전은 아직 기다린다
    # 접수일 문턱의 경계: 457일은 신선, 458일은 묵음
    assert sc.annual_stale_reason("2025-06-30", "2026-09-30", 2025) is None
    assert sc.annual_stale_reason("2025-06-29", "2026-09-30", 2025) is not None
