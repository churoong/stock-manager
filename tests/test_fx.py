"""환율 환산 테스트 (docs/signals.md 3.4). 실제 마이그레이션을 적용한 메모리 SQLite 에 돌린다."""

from __future__ import annotations

import json
from datetime import date

from batch.jobs import fx as fx_job
from batch.jobs import signals as signals_job
from batch.notify import report_sections as rs
from batch.services import fx
from batch.services import report_picks as rp
from batch.services import signals as sg
from batch.sources.yfinance_src import DailyBar
from tests.test_report_picks import SqliteClient, row

RATE = fx.FxRate("USDKRW", "2026-09-15", 1400.0, "yfinance")


class Test환산:
    def test_원화는_그대로(self) -> None:
        assert fx.convert_krw(50_000_000, "KRW", None) == 50_000_000

    def test_달러는_환율로_나눈다(self) -> None:
        assert fx.convert_krw(50_000_000, "USD", RATE) == 50_000_000 / 1400

    def test_환율이_없거나_모르는_통화면_None(self) -> None:
        assert fx.convert_krw(50_000_000, "USD", None) is None
        assert fx.convert_krw(50_000_000, "JPY", RATE) is None

    def test_7일_넘은_환율과_미래_환율은_쓰지_않는다(self) -> None:
        assert fx.usable(RATE, date(2026, 9, 22)) is RATE
        assert fx.usable(RATE, date(2026, 9, 23)) is None
        assert fx.usable(RATE, date(2026, 9, 14)) is None


def _client_with_rates(*rates: tuple[str, float]) -> SqliteClient:
    client = SqliteClient()
    for day, value in rates:
        client.conn.execute(
            "INSERT INTO fx_rates (pair, date, rate, source, fetched_at) VALUES ('USDKRW', ?, ?, 'yfinance', 't')",
            [day, value],
        )
    client.conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('total_investable_amount', ?, 't')",
        [json.dumps(50_000_000)],
    )
    return client


class Test설정_읽기:
    def test_기준일_이전_최신_환율로_달러_환산(self) -> None:
        client = _client_with_rates(("2026-09-14", 1390.0), ("2026-09-15", 1400.0), ("2026-09-17", 9999.0))
        s = signals_job.load_settings(client, "USD", "2026-09-16")
        assert s["total_investable"] == 50_000_000 / 1400
        assert s["min_order_amount"] == signals_job.DEFAULT_MIN_ORDER / 1400
        assert s["fx"] == {"pair": "USDKRW", "date": "2026-09-15", "rate": 1400.0, "source": "yfinance"}
        assert not s["fx_missing"]
        assert "50,000,000원 ÷ 1,400.00원/달러" in s["fx_note"]

    def test_최소_주문_0_은_0_그대로_환산한다(self) -> None:
        """**0 은 "제한 없음" 이다** (2026-09-23, docs/infra.md 25.179).

        `0.0` 이 거짓이라 `or DEFAULT_MIN_ORDER` 가 탔고, 원화 기본값 100,000 이
        **달러 자리에** 앉았다. 같은 함수가 낸 예산은 $35,714 인데 최소 주문이
        $100,000 이라 **미국 추천의 권장 금액이 전부 None 이 됐다.**
        2부는 미국 종목을 한 종목도 안 남기고 "최소 주문 단위 미만" 으로 뺐고,
        `setting_warnings` 는 비어 있어 아무 말도 안 나갔다.

        0 이 뜻 있는 값이라는 것은 `report_picks` 가 스스로 말한다 —
        `if min_order_amount > 0 and amount < min_order_amount:`
        """
        client = _client_with_rates(("2026-09-15", 1400.0))
        client.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('min_order_amount', ?, 't')",
            [json.dumps(0)],
        )

        s = signals_job.load_settings(client, "USD", "2026-09-16")

        assert s["min_order_amount"] == 0.0, "0(제한 없음)이 기본값으로 덮였다"
        assert s["min_order_amount"] < s["total_investable"], (
            "최소 주문이 예산보다 크면 권장 금액이 한 건도 안 나온다"
        )
        assert s["setting_warnings"] == [], "범위 안의 값인데 경고가 나갔다"

    def test_국내와_미국이_0_을_같은_뜻으로_읽는다(self) -> None:
        """국내는 line 323 에서 먼저 돌아가 0 이 살았다. 두 시장이 **다른 뜻**이었다."""
        client = _client_with_rates(("2026-09-15", 1400.0))
        client.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('min_order_amount', ?, 't')",
            [json.dumps(0)],
        )

        국내 = signals_job.load_settings(client, "KRW", "2026-09-16")
        미국 = signals_job.load_settings(client, "USD", "2026-09-16")

        assert 국내["min_order_amount"] == 미국["min_order_amount"] == 0.0

    def test_되돌릴_때도_통화가_안_섞인다(self) -> None:
        """환산이 안 되면 기본값으로 되돌리되 **그 기본값도 달러여야** 한다.

        원화 숫자를 달러 자리에 넣으면 1,400배 큰 문턱이 된다.
        """
        client = _client_with_rates(("2026-09-15", 1400.0))

        s = signals_job.load_settings(client, "USD", "2026-09-16")

        assert s["min_order_amount"] == signals_job.DEFAULT_MIN_ORDER / 1400
        assert s["min_order_amount"] < 1_000, "달러 자리에 원화 숫자가 앉았다"

    def test_환율이_없으면_금액을_내지_않는다(self) -> None:
        client = _client_with_rates(("2026-09-01", 1400.0))  # 15일 전 → 너무 오래됨
        s = signals_job.load_settings(client, "USD", "2026-09-16")
        assert s["total_investable"] == 0.0
        assert s["fx_missing"]

    def test_금액을_falsy_로_되돌리지_않는다(self) -> None:
        """**`or` 는 0 을 "없음" 으로 읽는다.** 돈에 닿는 자리에서는 그것이 곧 결함이다.

        값 검사만으로는 다시 `or` 를 쓰는 것을 못 막는다 — 지금 통과하는 테스트가
        있어도 다른 설정에 같은 모양을 또 쓰면 아무도 모른다.
        """
        import ast
        import inspect
        import textwrap

        나무 = ast.parse(textwrap.dedent(inspect.getsource(signals_job.load_settings))).body[0]
        if ast.get_docstring(나무) is not None:
            나무.body = 나무.body[1:]
        코드 = ast.unparse(나무)

        assert "or DEFAULT_MIN_ORDER" not in 코드, (
            "환산 결과를 `or` 로 되돌린다. 0(제한 없음)이 기본값으로 덮인다 (25.179)"
        )
        assert "is not None else" in 코드, "None 인지로 갈라야 0 이 산다"

    def test_국내는_환율을_보지_않는다(self) -> None:
        client = _client_with_rates()
        s = signals_job.load_settings(client, "KRW", "2026-09-16")
        assert s["total_investable"] == 50_000_000
        assert s["fx"] is None and not s["fx_missing"]


class Test수집_행:
    def test_환율_심볼의_양수_종가만(self) -> None:
        bars = [
            DailyBar(ticker="KRW=X", date="2026-09-15", close=1400.0, currency="KRW"),
            DailyBar(ticker="KRW=X", date="2026-09-16", close=0.0, currency="KRW"),
            DailyBar(ticker="AAPL", date="2026-09-15", close=220.0),
        ]
        assert fx_job.rows_from_bars(bars, "yfinance", "now") == [("USDKRW", "2026-09-15", 1400.0, "yfinance", "now")]


class Test근거와_리포트:
    def test_환율은_참고_행으로_남는다(self) -> None:
        rows = sg.fx_criteria(RATE.as_dict())
        assert len(rows) == 1
        assert rows[0]["passed"] is None
        assert rows[0]["as_of"] == "2026-09-15"
        assert sg.fx_criteria(None) == []

    def test_미국_2부는_달러와_환산식을_적는다(self) -> None:
        rows = [row(1, currency="USD", suggested_amount=1500.0, tranche_plan=[])]
        note = "50,000,000원 ÷ 1,400.00원/달러"
        text = rp.render(rows, total_investable=50_000_000 / 1400, signals_as_of="2026-09-16", currency="USD",
                         budget_note=note)
        assert "총 투자가능금액 $35,714.29" in text
        assert note in text
        assert "남은 여력 $" in text

    def test_국내_2부_표기는_그대로(self) -> None:
        view = rs.PortfolioView(total_budget=50_000_000, remaining_budget=40_000_000)
        text = rs.render_portfolio(view, [])
        assert "총 투자가능금액 50,000,000원" in text
        assert "남은 여력 40,000,000원" in text
