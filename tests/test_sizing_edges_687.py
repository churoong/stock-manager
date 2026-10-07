"""사이징의 가장자리 값 (docs/infra.md 25.687, 배분 감사 재현).

- 환율 0 이하·NaN 을 날짜만 보고 썼다 — 0 이면 `최소 주문 ÷ 환율` 에서 미국 신호 배치가 멈췄다
- MDD NaN 이 `nan != 0` 을 통과해 `round(nan)` 에서 멈췄다
- 배수 0.995~0.999 가 "비중을 0% 줄였습니다" 로 나갔다
"""

from __future__ import annotations

import inspect
import math
from datetime import date

import pytest

from batch.jobs import signals as signals_job
from batch.services import fx
from batch.services import report_picks as rp
from batch.services import signals as sg


@pytest.mark.parametrize("값", [0.0, -1300.0, math.nan, math.inf])
def test_0_이하_NaN_환율은_없는_것이다(값: float) -> None:
    assert fx.usable(fx.FxRate("USDKRW", "2026-09-28", 값, "yfinance"), date(2026, 9, 29)) is None
    assert fx.usable(fx.FxRate("USDKRW", "2026-09-28", 1400.0, "yfinance"), date(2026, 9, 29)) is not None


def test_MDD_NaN_은_모름이고_배치가_멈추지_않는다() -> None:
    비중, 배수, 근거 = sg.size_weight(10, None, math.nan)
    assert (비중, 배수) == (10, 1.0)
    assert "mdd" not in 근거
    _, 배수, 근거 = sg.size_weight(10, math.nan, -0.5)
    assert "volatility_ann" not in 근거 and 배수 < 1.0
    assert signals_job._opt_float(math.nan) is None and signals_job._opt_float(math.inf) is None
    assert signals_job._opt_float(0.5) == 0.5


def test_1퍼센트가_안_되는_축소는_적지_않는다() -> None:
    _, 배수, 근거 = sg.size_weight(10, 0.2505, None)
    assert 0.99 < 배수 < 1.0
    assert 근거["reduction_note"] is None
    _, _, 근거 = sg.size_weight(10, 0.5, None)
    assert "줄였습니다" in 근거["reduction_note"]


def test_2부_상관_문구도_1퍼센트_미만은_적지_않는다() -> None:
    원본 = inspect.getsource(rp)
    assert "if round((1 - 배수) * 100) >= 1:" in 원본
    # 문구가 없어도 금액은 줄었으니 "신호 낸 날 총액 기준" 으로 오해하지 않는다 (25.689, 교차검증)
    # 25.700 — 통째로 지우지 않고 상관 배수만큼 뺀 값과 견준다
    assert "abs(weight_pct - 신호비중 * 상관_배수) > 0.05" in 원본
    assert "if not capped_note and 신호비중 > 0" in 원본  # 25.707 — 상관 문구가 있어도 총액 안내를 막지 않는다


def test_가장_최근_환율이_0_이면_앞의_정상_환율을_쓴다() -> None:
    """25.690 — 0 인 최근 행 때문에 7일 안의 정상 환율을 두고 "환율 없음" 이 됐다."""
    import sqlite3

    from batch.core.turso import ResultSet

    class Mem:
        def __init__(self) -> None:
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE fx_rates (pair, date, rate, source)")

        def execute(self, sql: str, args: list | None = None) -> ResultSet:
            cur = self.conn.execute(sql, args or [])
            cols = [d[0] for d in cur.description or []]
            return ResultSet(columns=cols, rows=[tuple(r) for r in cur.fetchall()], last_insert_rowid=None)

    m = Mem()
    m.conn.execute("INSERT INTO fx_rates VALUES ('USDKRW', '2026-09-25', 1390.0, 'yfinance'), ('USDKRW', '2026-09-28', 0, 't')")
    r = fx.latest_rate(m, "2026-09-29")  # type: ignore[arg-type]
    assert r is not None and r.rate == 1390.0


def test_총액_안내에_상관_반영_비중을_함께_적는다() -> None:
    """"10% → 6%" 가 총액 탓으로 줄어든 것처럼 읽혔다 — 실제로는 상관 10→3, 총액 3→6 (docs/infra.md 25.710, 교차검증)."""
    원본 = inspect.getsource(rp)
    assert '상관 반영 {신호비중 * 상관_배수:.1f}%' in 원본
