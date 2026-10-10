"""SEC 주식수 파싱·갱신 계획 테스트. 픽스처는 2026-09-17 실제 응답의 일부다. 네트워크를 타지 않는다."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from batch.jobs import us_shares
from batch.sources import sec_edgar
from tests.test_report_picks import SqliteClient

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "sec_shares.json").read_text(encoding="utf-8"))


def test_티커_매핑은_10자리_CIK_이고_클래스주는_같은_CIK() -> None:
    m = sec_edgar.parse_ticker_map(FIXTURE["tickers_sample"])
    assert m["AAPL"] == "0000320193"
    assert m["GOOGL"] == m["GOOG"] == "0001652044"
    assert m["BRK-B"] == "0001067983"  # 대시 표기 그대로 → yahoo_symbol 과 조인된다


def test_애플_가장_최근_10Q() -> None:
    found = sec_edgar.latest_shares(FIXTURE["aapl_dei"], "dei:EntityCommonStockSharesOutstanding")
    assert found is not None
    assert (found.value, found.as_of, found.filed, found.form) == (14_594_180_000, "2026-07-17", "2026-07-31", "10-Q")


def test_같은_공시의_비교기간보다_당기_기준일을_고른다() -> None:
    # 알파벳 2026 Q2 10-Q 에는 2025-12-31 과 2026-06-30 값이 함께 실린다(filed 같음)
    found = sec_edgar.latest_shares(FIXTURE["googl_us_gaap"], "us-gaap:CommonStockSharesOutstanding")
    assert found is not None
    assert (found.value, found.as_of) == (12_230_000_000, "2026-06-30")


def test_정기보고서가_아닌_공시는_늦게_나와도_쓰지_않는다() -> None:
    payload = {
        "units": {
            "shares": [
                {"end": "2026-03-31", "val": 100, "form": "10-Q", "filed": "2026-05-01"},
                {"end": "2026-04-30", "val": 999, "form": "DEF 14A", "filed": "2026-06-01"},
                {"end": "2026-04-30", "val": 888, "form": "8-K", "filed": "2026-06-02"},
            ]
        }
    }
    found = sec_edgar.latest_shares(payload, "x")
    assert found is not None and found.value == 100


def test_정기보고서_fact_가_없으면_None() -> None:
    assert (
        sec_edgar.latest_shares({"units": {"shares": [{"val": 1, "form": "8-K", "filed": "2020-01-01"}]}}, "x") is None
    )
    assert sec_edgar.latest_shares({}, "x") is None


def test_UA_에_연락처가_없으면_부르기_전에_멈춘다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "stock-manager")
    with pytest.raises(RuntimeError):
        sec_edgar.SecClient()


def test_갱신_계획_주식수_없는_종목은_상장일만() -> None:
    ids = {"AAPL": 1, "BRK-B": 2, "ZZZZ": 3}
    ciks = {"AAPL": "0000320193", "BRK-B": "0001067983"}  # ZZZZ 는 SEC 목록에 없다
    shares = {"0000320193": sec_edgar.Shares(14_594_180_000, "2026-07-17", "2026-07-31", "10-Q", "dei:x")}
    statements, missing = us_shares.plan_statements(ids, ciks, shares)

    assert missing == ["BRK-B", "ZZZZ"]
    listed = [p for sql, p in statements if "listed_date" in sql]
    caps = [p for sql, p in statements if "market_cap" in sql]
    assert listed == [[1, 1, 1], [2, 2, 2], [3, 3, 3]]
    assert caps == [[14_594_180_000, 14_594_180_000, 1, 1, 1]]


def test_주식수_기준일_뒤_분할이_감지되면_시총을_다시_내지_않는다() -> None:
    """분할 전 주식수 × 분할 뒤 종가로 1:10 병합 종목의 시총이 10배가 됐다 (docs/infra.md 25.524, 감사 재현)."""
    ids = {"REV": 1, "OK": 2}
    ciks = {"REV": "0000000001", "OK": "0000000002"}
    shares = {c: sec_edgar.Shares(1_000_000_000, "2026-06-30", "2026-08-05", "10-Q", "dei:x") for c in ciks.values()}
    건너뜀: list[str] = []
    statements, _ = us_shares.plan_statements(
        ids, ciks, shares, split_since={1: "2026-09-10T12:00:00+00:00", 2: "2026-05-01T00:00:00+00:00"},
        split_skipped=건너뜀,
    )
    caps = [p[2] for sql, p in statements if "market_cap" in sql]
    assert caps == [2] and 건너뜀 == ["REV"]  # 기준일 앞의 분할(OK)은 이미 주식수에 들어 있다


def test_분할_감지는_따로_둔_기록에서_읽는다() -> None:
    """대기열 표시는 배당 감지와 뒤엉켜 새 분할을 놓쳤다 (docs/infra.md 25.535, 교차검증 재현)."""
    from batch.core import db
    from batch.jobs import daily
    from batch.services import adjust_drift as drift
    from tests.test_report_picks import SqliteClient

    c = SqliteClient()
    for i in (1, 2):
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
            " VALUES (?, ?, 'NYSE', 'US', 'USD', 'active', 't', 't')", [i, f"T{i}"],
        )
    # 옛 분할(재수집 끝) → 배당 대기 → 새 분할. 새 분할 날이 남아야 한다
    daily.queue_drift(c, [drift.Drift(1, "d", 0.99, 0.99)], "2026-03-01T00:00:00+00:00")  # type: ignore[arg-type]
    c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 'x'")
    daily.queue_drift(c, [drift.Drift(1, "d", 0.99, 0.97)], "2026-09-08T00:00:00+00:00")  # type: ignore[arg-type]
    daily.queue_drift(c, [drift.Drift(1, "d", 0.5, 0.5), drift.Drift(2, "d", 0.9, 0.8)],  # type: ignore[arg-type]
                      "2026-09-10T00:00:00+00:00")  # fmt: skip
    assert us_shares.split_detections(c) == {1: "2026-09-10T00:00:00+00:00"}  # type: ignore[arg-type]
    # 대기 중인 새 분할은 표시를 가진다(재수집 우선). 재수집이 끝난 뒤 배당이 오면 표시는 사라진다 —
    # 옛 분할이 대기열에 영구히 남아 새 분할보다 앞서지 않게
    표시 = "SELECT ratio_before = ratio_after FROM adjust_refresh_queue WHERE stock_id = 1"
    assert c.conn.execute(표시).fetchone()[0] == 1
    c.conn.execute("UPDATE adjust_refresh_queue SET done_at = 'x'")
    daily.queue_drift(c, [drift.Drift(1, "d", 0.99, 0.97)], "2026-09-20T00:00:00+00:00")  # type: ignore[arg-type]
    assert c.conn.execute(표시).fetchone()[0] == 0
    assert db.get_setting(c, "us_split_detections", {}) == {"1": "2026-09-10T00:00:00+00:00"}  # type: ignore[arg-type]

def test_오래된_공시는_버린다() -> None:
    # BRK dei 는 2011-05-06 공시가 마지막이다
    payload = {"units": {"shares": [{"end": "2011-04-29", "val": 941_481, "form": "10-Q", "filed": "2011-05-06"}]}}
    assert sec_edgar.latest_shares(payload, "x", date(2026, 9, 17)) is None
    recent = {"units": {"shares": [{"end": "2025-12-31", "val": 5, "form": "10-K", "filed": "2026-02-20"}]}}
    assert sec_edgar.latest_shares(recent, "x", date(2026, 9, 17)) is not None


def test_가중평균은_같은_기준일이면_짧은_기간() -> None:
    # 10-Q 에는 분기(4~6월)와 반기(1~6월) 평균이 같은 end 로 함께 실린다
    payload = {
        "units": {
            "shares": [
                {"start": "2026-01-01", "end": "2026-06-30", "val": 600, "form": "10-Q", "filed": "2026-08-06"},
                {"start": "2026-04-01", "end": "2026-06-30", "val": 592, "form": "10-Q", "filed": "2026-08-06"},
            ]
        }
    }
    found = sec_edgar.latest_shares(payload, "x")
    assert found is not None and found.value == 592


def test_개념별_집계() -> None:
    shares = {
        "1": sec_edgar.Shares(1, "", "", "10-Q", "dei:a"),
        "2": sec_edgar.Shares(1, "", "", "10-Q", "dei:a"),
        "3": sec_edgar.Shares(1, "", "", "10-Q", "us-gaap:b"),
    }
    assert us_shares.concept_counts(shares) == {"dei:a": 2, "us-gaap:b": 1}


class Test상장일을_얕은_이력으로_적지_않는다:
    """**설명문이 사람에게만 말하고 있었다** (docs/infra.md 25.166).

    `us_shares` 설명문에 "상장일 = 가격 이력의 관측 첫 거래일. 실제 상장일이 아니므로
    5년 백필 뒤에 돌려야 뜻이 있다" 고 적혀 있었다. **코드는 그것을 안 봤고**
    워크플로는 매주 토요일 저절로 돈다.

    이력이 얕은 채로 돌면 모든 미국 종목의 `listed_date` 가 며칠 전이 되고,
    유니버스가 **전 종목을 "상장 1년 미만" 으로 뺀다.** 사유가 그럴듯해서 아무도
    자료를 의심하지 않는다. 복구 직후가 딱 그 상황이다 — `essential` 백업에는
    시세가 없다(docs/backup.md 1장).
    """

    def test_이력이_깊어야_적는다(self) -> None:
        assert us_shares.may_set_listed_date(2000) is True
        assert us_shares.may_set_listed_date(us_shares.MIN_HISTORY_DAYS) is True
        assert us_shares.may_set_listed_date(us_shares.MIN_HISTORY_DAYS - 1) is False
        assert us_shares.may_set_listed_date(10) is False

    def test_모르면_적지_않는다(self) -> None:
        """**모르는 것을 좋다고 단정하지 않는다.** 한 행도 없으면 이력이 없는 것이다."""
        assert us_shares.may_set_listed_date(None) is False

    def test_문턱이_유니버스와_같은_값이다(self) -> None:
        from batch.services import universe as uni

        assert us_shares.MIN_HISTORY_DAYS is uni.DEFAULT_MIN_LISTED_DAYS
        글 = (ROOT / "batch" / "jobs" / "us_shares.py").read_text(encoding="utf-8")
        assert "MIN_HISTORY_DAYS = uni.DEFAULT_MIN_LISTED_DAYS" in 글, (
            "값을 손으로 다시 적으면 둘이 갈라진다 (25.153 의 모양)"
        )

    def test_적지_않기로_하면_문을_아예_안_만든다(self) -> None:
        statements, _ = us_shares.plan_statements(
            {"AAPL": 1}, {}, {}, set_listed_date=False
        )
        assert [sql for sql, _ in statements if "listed_date" in sql] == []


class Test상장일은_앞으로만_당긴다:
    """관측 첫 거래일은 실제 상장일의 **상한**이다 (docs/infra.md 25.166).

    더 이른 값을 만나면 당기는 것이 맞다 — 그날 값이 있었으니 그날 이전에 상장했다.
    **늦추거나 비우는 것은 언제나 틀리다.**
    """

    def _db(self) -> SqliteClient:
        c = SqliteClient()
        c.conn.execute(
            "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, yahoo_symbol,"
            " listed_date) VALUES (1, 'AAA', 'NASDAQ', 'US', 'USD', 'active', 't', 't', 'AAA', '2020-01-02')"
        )
        return c

    def _적용(self, c: SqliteClient) -> str | None:
        statements, _ = us_shares.plan_statements({"AAA": 1}, {}, {})
        for sql, args in statements:
            c.conn.execute(sql, args)
        return c.conn.execute("SELECT listed_date FROM stocks WHERE id = 1").fetchone()[0]

    def test_더_이른_관측이_있으면_당긴다(self) -> None:
        c = self._db()
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2018-03-05', 10, 'USD', 't', 't')"
        )
        assert self._적용(c) == "2018-03-05"

    def test_더_늦은_관측으로는_늦추지_않는다(self) -> None:
        """복구 직후에는 며칠치만 있다. 그것으로 상장일을 늦추면 유니버스에서 빠진다."""
        c = self._db()
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2026-09-20', 10, 'USD', 't', 't')"
        )
        assert self._적용(c) == "2020-01-02"

    def test_가격이_없으면_지우지_않는다(self) -> None:
        c = self._db()
        assert self._적용(c) == "2020-01-02"

    def test_비어_있으면_관측값으로_채운다(self) -> None:
        c = self._db()
        c.conn.execute("UPDATE stocks SET listed_date = NULL WHERE id = 1")
        c.conn.execute(
            "INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
            " VALUES (1, '2019-05-02', 10, 'USD', 't', 't')"
        )
        assert self._적용(c) == "2019-05-02"


def test_후보는_유니버스_문턱의_절반으로_넓혀_고른다(monkeypatch: pytest.MonkeyPatch) -> None:
    """거래대금이 막 오른 종목이 후보에서 빠져 '데이터없음' 이 됐다 (docs/infra.md 25.530, 감사 재현)."""
    import inspect

    # 최근 20일 520만, 그 전 10일 100만 → 30일 평균 380만. 문턱 500만의 절반(250만)은 넘는다
    평균 = (5_200_000 * 20 + 1_000_000 * 10) / 30
    assert 평균 >= 5_000_000 * us_shares.CANDIDATE_MARGIN
    assert "min_turnover * CANDIDATE_MARGIN" in inspect.getsource(us_shares.run)


def test_5xx_와_깨진_본문은_없음이_아니라_실패다(monkeypatch: pytest.MonkeyPatch) -> None:
    """None·[] 로 삼켜 success 로 닫고, 주식수는 가중평균으로 내려가 시총을 덮었다 (docs/infra.md 25.601, 감사)."""
    from types import SimpleNamespace

    monkeypatch.setenv("SEC_USER_AGENT", "stock-manager me@example.com")
    c = sec_edgar.SecClient()
    monkeypatch.setattr(c, "_get", lambda url: SimpleNamespace(status_code=503, json=lambda: {}))
    for 부르기 in (lambda: c.company_facts("1"), lambda: c.filings("1"), lambda: c.shares("1")):
        with pytest.raises(sec_edgar.SecFailed):
            부르기()
    monkeypatch.setattr(c, "_get", lambda url: SimpleNamespace(status_code=404, json=lambda: {}))
    assert c.company_facts("1") is None and c.filings("1") == [] and c.shares("1") is None


def test_외국_발행사의_20F·40F_주식수도_쓴다() -> None:
    """10-K·10-Q 만 인정해 외국 발행사 시총이 늘 비어 '데이터없음' 이었다 (docs/infra.md 25.1117, 유니버스 감사 재현)."""
    for form in ("40-F", "20-F"):
        payload = {"units": {"shares": [
            {"val": 1_400_000_000, "end": "2026-03-31", "filed": "2026-06-20", "form": form},
            {"val": 9, "end": "2026-04-30", "filed": "2026-07-01", "form": "DEF 14A"},  # 정기 보고서가 아니다
        ]}}  # fmt: skip
        found = sec_edgar.latest_shares(payload, "dei:EntityCommonStockSharesOutstanding")
        assert found is not None and found.value == 1_400_000_000 and found.form == form
