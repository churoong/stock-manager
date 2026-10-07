"""백필이 날짜 단위라 새로 든 종목의 과거를 채우지 않았다 (docs/infra.md 25.459, 스케줄 감사 #8)."""

from __future__ import annotations

from datetime import date

from batch.jobs import backfill_kr as bf

날들 = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8)]


def test_첫_시세_앞이_구멍이다() -> None:
    got = bf.front_gaps(날들, {1: "2026-09-07", 2: "2026-09-01"}, {1: "2020-01-02", 2: "2020-01-02"})
    assert got == {1: [date(2026, 9, d) for d in (1, 2, 3, 4)]}  # 2번은 처음부터 있다


def test_상장_전은_구멍이_아니다() -> None:
    assert bf.front_gaps(날들, {1: "2026-09-07"}, {1: "2026-09-04"}) == {1: [date(2026, 9, 4)]}


def test_상장일을_모르면_보지_않는다() -> None:
    """모르면 상장 전 날을 구멍으로 착각해 매번 다시 부른다."""
    assert bf.front_gaps(날들, {1: "2026-09-07"}, {1: None}) == {}


def test_실행이_앞쪽_구멍의_날을_다시_받는다(monkeypatch) -> None:  # noqa: ANN001
    """날짜로는 모두 채워졌지만 새로 든 3번 종목은 9/7 부터만 있다 → 9/1~9/4 를 다시 받는다."""
    받은날: list[date] = []

    class 가짜:
        def close(self) -> None: ...

    monkeypatch.setattr(bf, "TursoClient", lambda: 가짜())
    monkeypatch.setattr(bf.db, "apply_migrations", lambda c: None)
    monkeypatch.setattr(bf.db, "start_batch_run", lambda *a, **k: 1)
    monkeypatch.setattr(bf.db, "finish_batch_run", lambda *a, **k: None)
    monkeypatch.setattr(bf.db, "remaining_write_budget", lambda c: 10**9)
    monkeypatch.setattr(bf, "sessions_between", lambda s, e: 날들)
    monkeypatch.setattr(bf, "stock_id_map", lambda c, m: {"A": 1, "B": 3} if m == "KOSPI" else {})
    monkeypatch.setattr(bf, "already_stored", lambda c, m, s, e: {d.isoformat() for d in 날들})
    monkeypatch.setattr(bf, "first_seen_and_listed", lambda c, m, s, e: (
        {1: "2026-09-01", 3: "2026-09-07"}, {1: "2000-01-04", 3: "2000-01-04"}))
    받은종목: list[set[int]] = []
    monkeypatch.setattr(bf, "store_day", lambda c, m, d, ids: (받은날.append(d), 받은종목.append(set(ids.values())),
                                                                (1, None))[2])  # fmt: skip
    bf.run(날들[0], 날들[-1])
    assert sorted(받은날) == [date(2026, 9, d) for d in (1, 2, 3, 4)]
    # 다시 받는 날에는 구멍 난 종목만 쓴다 — 1번(이미 있다)을 또 쓰면 D1 예산을 태운다 (25.465)
    assert 받은종목 == [{3}] * 4
