"""2부 상관 — 믿을 수 없는 시세는 "모름", 일부 못 본 보유도 말한다 (docs/infra.md 25.1348, 22회차 P5). 금액은 그대로(모르면 안 줄인다)."""

from __future__ import annotations

from batch.services import report_picks as rp
from tests.test_correlation_sizing import _N, _series
from tests.test_correlation_sizing import Test2부에_실제로_배선됐나 as T


def _note(series: dict, holdings: list, unknown: dict | None = None) -> tuple[str, float]:
    view = rp.build_portfolio([T._row(1)], total_investable=10_000_000, holdings=holdings, price_series=series,
                              series_unknown=unknown)  # fmt: skip
    a = view.allocations[0]
    return a.note, a.amount


def test_분할_재수집_대기_후보는_상관_미확인() -> None:
    계열 = _series(T._같이)
    note, amount = _note({1: 계열, 9: 계열}, [T._보유(9)], {1: "분할 재수집 대기"})
    assert "상관 미확인 (이 종목 분할 재수집 대기)" in note
    assert amount == 1_000_000  # 모르면 안 줄인다 — 예전에는 깨진 계열로 상관을 냈다


def test_분할_재수집_대기_보유는_대조에서_빼고_일부_미확인() -> None:
    계열 = _series(T._같이)
    다른 = _series([0.01, -0.01] * (_N // 2 + 1))
    note, _ = _note({1: 계열, 9: 계열, 8: 다른}, [T._보유(9), T._보유(8)], {9: "분할 재수집 대기"})
    assert "상관 일부 미확인 (보유9: 분할 재수집 대기)" in note


def test_겹친_날이_모자란_보유를_말없이_건너뛰지_않는다() -> None:
    길다 = _series([0.01, -0.01] * (_N // 2 + 1))
    짧다 = _series([0.01, -0.02, 0.03] * 14, 첫날=len(길다) - 42)  # 상장한 지 40여 일
    note, amount = _note({1: 길다, 9: 길다, 8: 짧다}, [T._보유(9), T._보유(8)])
    assert "상관 일부 미확인 (보유8: 겹친 날" in note and f"< {rp.CORR_MIN_POINTS}" in note


def test_모두_비교했으면_조용하다() -> None:
    계열 = _series(T._같이)
    note, _ = _note({1: 계열, 9: 계열}, [T._보유(9)])
    assert "미확인" not in note


def test_일일_배치가_분할_재수집_대기를_읽어_넘긴다() -> None:
    from batch.core import db
    from batch.jobs import daily
    from tests.test_portfolio_job import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at)"
                     " VALUES (7, 'X', 'NASDAQ', 'US', 'USD', 't', 't')")  # fmt: skip
    cols = [r[1] for r in mem.conn.execute("PRAGMA table_info(adjust_refresh_queue)")]
    값 = {"stock_id": 7, "detected_at": "2026-10-10T00:00:00Z", "ratio_before": 4.0, "ratio_after": 4.0}
    값 = {k: v for k, v in 값.items() if k in cols}
    for c in cols:  # 나머지 필수 칸은 아무 값
        if c not in 값 and c != "done_at" and c != "id":
            값[c] = "t"
    mem.conn.execute(f"INSERT INTO adjust_refresh_queue ({', '.join(값)}) VALUES ({', '.join('?' * len(값))})",
                     list(값.values()))  # fmt: skip
    경고: list[str] = []
    assert daily._corr_unknown(mem, "2026-10-13", 경고) == {7: "분할 재수집 대기"} and 경고 == []  # type: ignore[arg-type]
