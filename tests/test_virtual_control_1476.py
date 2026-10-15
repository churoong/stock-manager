"""가상 대조 — 매수마다 같은 날·같은 시장 유니버스 무작위 20종목 안의 분위 (docs/company.md 6장, docs/infra.md 25.1476).

순수 함수(고르기·분위·요약)와, 실제 마이그레이션 위에서 결정 원장을 채우는 단계(결정일 당시 유니버스 스냅숏을 쓰는가 —
그 뒤 빠진 종목도 모집단에 있는가, 20 거래일을 못 채운 대조는 빠지는가)를 본다.
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.jobs import paper as job
from batch.services import paper as pp
from tests.test_paper_job_1445 import Client


def test_control_picks_are_deterministic_and_shared() -> None:
    모집단 = list(range(100, 160))
    a = pp.control_picks(모집단, "KR", "2026-10-15", exclude=105)
    assert len(a) == pp.VIRTUAL_CONTROLS and 105 not in a
    assert a == pp.control_picks(list(reversed(모집단)), "KR", "2026-10-15", exclude=105)  # 순서와 무관
    # 같은 날 다른 매수는 산 종목만 빼고 같은 대조군
    순서 = pp.random_order(모집단, "virtual|KR|2026-10-15")[: pp.VIRTUAL_CONTROLS + 1]
    assert a == [x for x in 순서 if x != 105][: pp.VIRTUAL_CONTROLS]
    assert pp.control_picks(모집단, "KR", "2026-10-15", exclude=999) == 순서[: pp.VIRTUAL_CONTROLS]
    assert pp.control_picks(모집단, "KR", "2026-10-16", exclude=105) != a  # 날이 바뀌면 다른 대조군


def test_control_pct_ties_and_minimum() -> None:
    assert pp.control_pct(5.0, [1.0, 2.0, 5.0, 9.0] * 3) == (6 + 0.5 * 3) / 12 * 100
    assert pp.control_pct(5.0, [1.0] * (pp.VIRTUAL_MIN_CONTROLS - 1)) is None


def test_attribution_virtual_uses_episode_first_buy() -> None:
    def 매수(sid: int, day: str, pct: float | None) -> dict:
        return {"stock_id": sid, "decided_on": day, "kind": "buy", "reason": "r", "ret_20d": 1.0, "bench_20d": 0.0,
                "control_pct": pct}  # fmt: skip

    a = pp.attribution([매수(1, "2026-10-01", 80.0), 매수(1, "2026-10-02", 10.0), 매수(2, "2026-10-01", 40.0),
                        매수(3, "2026-10-01", None)], [])  # fmt: skip
    assert a["virtual"] == {"n": 2, "avg_pct": 60.0, "top_half": 50.0}
    줄 = pp.attribution_lines("C", a)
    assert any(x.startswith("  C 가상 대조: 산 종목이 같은 날 무작위 20종목 안 평균 60 분위 · 위쪽 절반 50% (n=2, 참고)")
               for x in 줄)  # fmt: skip


def _거래일(n: int, 시작: date) -> list[str]:
    out, d = [], 시작
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def test_fill_controls_on_real_schema() -> None:
    c = Client()
    대조들 = list(range(100, 130))
    for sid in 대조들:
        c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
                       " asset_type) VALUES (?, ?, 'M', 'KR', ?, 'KRW', 'active', 't', 't', 'stock')",
                       [sid, f"{sid:06d}", f"종목{sid}"])  # fmt: skip
    # 10-10 스냅숏에만 있는 종목(그 뒤 빠짐)도 결정일(10-15) 모집단이다. 10-20 스냅숏은 결정 뒤라 쓰지 않는다
    for sid in 대조들:
        c.conn.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                       " VALUES ('2026-10-10', ?, 1, 'KRW', 't')", [sid])  # fmt: skip
    c.conn.execute("INSERT INTO universe_members (snapshot_date, stock_id, included, currency, created_at)"
                   " VALUES ('2026-10-20', 100, 1, 'KRW', 't')")  # fmt: skip
    날들 = _거래일(20, date(2026, 10, 15))
    for i, sid in enumerate(대조들):
        행 = [("2026-10-15", 100.0)] + [(d, 100.0 + i) for d in 날들]  # 대조 i 의 20 거래일 수익 = i%
        if sid == 129:
            행 = 행[:5]  # 20 거래일을 못 채움(폐지) — 빠진다
        for d, v in 행:
            c.conn.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at)"
                           " VALUES (?, ?, ?, 'KRW', 't', 't')", [sid, d, v])  # fmt: skip
    c.conn.execute(
        "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " ret_20d, bench_20d, created_at) VALUES ('A', 'KR', '2026-10-15', 1, 'buy', 'r', 100, '2026-10-15', 14.5, 1, 't')"
    )  # fmt: skip
    기록 = job.fill_controls(c, "KR")
    pct, n = c.q("SELECT control_pct, control_n FROM paper_decisions")[0]
    고른 = pp.control_picks(대조들, "KR", "2026-10-15", exclude=1)
    rets = [float(s - 100) for s in 고른 if s != 129]
    assert n == len(rets) and pct == pp.control_pct(14.5, rets)
    assert 기록 == {"pending_read": 1, "filled": 1, "snapshots": 1}
    # 한 번만 — 다시 돌면 읽을 것이 없다
    assert job.fill_controls(c, "KR")["pending_read"] == 0


def test_fill_controls_without_universe_marks_zero() -> None:
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_decisions (account_id, market, decided_on, stock_id, kind, reason, ref_close, ref_date,"
        " ret_20d, bench_20d, created_at) VALUES ('A', 'KR', '2026-10-15', 1, 'buy', 'r', 100, '2026-10-15', 3, 1, 't')"
    )  # fmt: skip
    assert job.fill_controls(c, "KR")["filled"] == 0
    assert c.q("SELECT control_pct, control_n FROM paper_decisions") == [(None, 0)]
