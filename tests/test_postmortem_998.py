"""틀린 추천 사후 분석 (docs/reports.md 3.8, docs/infra.md 25.998)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from batch.jobs import weekly_summary as ws
from batch.services import postmortem as pm


def test_시장_업종_종목_몫의_합이_수익이다() -> None:
    x = pm.Loss("가", "short", -0.08, -0.03, -0.05, 12, None)
    p = pm.parts(x)
    assert abs(sum(p.values()) - x.ret) < 1e-12
    assert round(p["market"], 4) == -0.03 and round(p["sector"], 4) == -0.02 and round(p["idio"], 4) == -0.03
    assert pm.cause(x) in ("market", "idio")


def test_업종_표본이_적으면_시장과_같다고_본다() -> None:
    x = pm.Loss("나", "mid", -0.10, -0.01, -0.30, pm.MIN_SECTOR_PEERS - 1, "주요사항보고서(유상증자결정)")
    assert pm.parts(x)["sector"] == 0.0 and pm.cause(x) == "idio"
    [_, 원인, 예] = pm.render(1, [x])
    assert "종목 고유 1" in 원인 and "공시 있음 1" in 원인
    assert "업종 표본 4개" in 예 and '공시 "주요사항보고서(유상증자결정)"' in 예


def test_손실이_없거나_신호가_없으면() -> None:
    assert pm.render(0, []) == []
    assert pm.render(5, []) == ["지난 추천 사후 분석 — 20거래일 성적이 이번 주 나온 신호 5건 중 손실 0건"]
    assert "나쁜 순 1건만" in pm.render(9, [pm.Loss("다", "long", -0.2, 0.0, None, 0, None)], 4)[0]


def test_질의가_실제_스키마에서_돈다() -> None:
    """마이그레이션 전부를 메모리 DB 에 올려 `load_losses` 를 그대로 돌린다 — 업종 평균·20거래일째·공시."""
    con = sqlite3.connect(":memory:")
    for f in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        con.executescript(f.read_text(encoding="utf-8"))

    class C:
        def execute(self, sql, args=None):
            cur = con.execute(sql, args or [])

            class R:
                rows = cur.fetchall()

                def scalar(self):
                    return self.rows[0][0] if self.rows else None

            return R()

    now = datetime(2026, 10, 7, tzinfo=UTC)
    assert ws.load_losses(C(), now) == (0, 0, [])

    # 손실 종목 1(반도체) + 같은 업종 5종목. 진입 09-04, 20거래일째는 그 뒤 20번째 날짜
    for i in range(1, 7):
        con.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, sector, name_ko)"
                    " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '반도체', ?)", [i, f"00000{i}", f"종목{i}"])  # fmt: skip
    날짜 = [f"2026-09-{d:02d}" for d in range(4, 30)]
    for i in range(1, 7):
        for k, d in enumerate(날짜):
            종가 = 100.0 if k == 0 else (90.0 if i == 1 else 95.0)  # 종목1 −10%, 동종 −5%
            con.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (?, ?, ?, 'KRW', 't', 't')",
                        [i, d, 종가])  # fmt: skip
    con.execute("INSERT INTO signal_outcomes (stock_id, as_of_date, horizon, entry_date, entry_close, ret_20d, bench_ret_20d,"
                " days_available, computed_at) VALUES (1, '2026-09-03', 'short', '2026-09-04', 100, -0.10, -0.02, 25, 't')")
    con.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
                " VALUES (1, 'c', 'r1', '주요사항보고서(유상증자결정)', '2026-09-10', 'dart_opendart', 't')")
    총, 손실수, [x] = ws.load_losses(C(), now)
    assert (총, 손실수) == (1, 1)
    assert x.peers == 5 and round(x.sector_ret, 4) == -0.05 and x.market == -0.02
    assert x.disclosure == "주요사항보고서(유상증자결정)"
    assert {k: round(v, 4) for k, v in pm.parts(x).items()} == {"market": -0.02, "sector": -0.03, "idio": -0.05}


def test_끊긴_신호의_손실도_센다() -> None:
    """성적표가 마지막 종가로 채운 끊긴 신호(`days_available` < 20)를 걸러 가장 나쁜 손실이 빠졌다 (docs/infra.md 25.1149)."""
    con = sqlite3.connect(":memory:")
    for f in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        con.executescript(f.read_text(encoding="utf-8"))

    class C:
        def execute(self, sql, args=None):
            cur = con.execute(sql, args or [])

            class R:
                rows = cur.fetchall()

                def scalar(self):
                    return self.rows[0][0] if self.rows else None

            return R()

    con.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, sector, name_ko)"
                " VALUES (1, '000001', 'KOSDAQ', 'KR', 'KRW', 'delisted', 't', 't', '바이오', '끊긴종목')")
    for d, c in (("2026-09-04", 100.0), ("2026-09-07", 70.0), ("2026-09-08", 40.0)):
        con.execute("INSERT INTO prices (stock_id, date, close, currency, source, fetched_at) VALUES (1, ?, ?, 'KRW', 't', 't')",
                    [d, c])  # fmt: skip
    con.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, source, fetched_at)"
                " VALUES (1, 'c', 'r1', '상장폐지결정', '2026-09-07', 'dart_opendart', 't')")
    con.execute("INSERT INTO signal_outcomes (stock_id, as_of_date, horizon, entry_date, entry_close, ret_20d, bench_ret_20d,"
                " days_available, computed_at) VALUES (1, '2026-09-03', 'short', '2026-09-04', 100, -0.60, -0.01, 2, 't')")
    총, 손실수, [x] = ws.load_losses(C(), datetime(2026, 10, 7, tzinfo=UTC))
    assert (총, 손실수) == (1, 1) and x.ret == -0.60
    assert x.disclosure == "상장폐지결정"  # 공시 창은 마지막 종가 날까지


def test_업종_평균은_분할만_반영한_가격으로_낸다() -> None:
    """동종 하나가 1:5 분할한 주에 원 종가로 낸 업종 평균이 −16% 가 되었다 (docs/infra.md 25.1150)."""
    con = sqlite3.connect(":memory:")
    for f in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        con.executescript(f.read_text(encoding="utf-8"))
    for i in range(1, 7):
        con.execute("INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at, sector)"
                    " VALUES (?, ?, 'KOSPI', 'KR', 'KRW', 'active', 't', 't', '반도체')", [i, f"00000{i}"])  # fmt: skip
    for i in range(2, 7):
        # 진입일 100 → 끝날 95. 6번은 그 사이 1:5 분할 — 원 종가 19, 수정주가는 진입일 20(=100/5)
        첫, 끝 = ((100.0, 20.0), (19.0, None)) if i == 6 else ((100.0, None), (95.0, None))
        for d, (c, a) in (("2026-09-04", 첫), ("2026-10-02", 끝)):
            con.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                        " VALUES (?, ?, ?, ?, 'KRW', 't', 't')", [i, d, c, a])  # fmt: skip
    avg, n = con.execute(ws.SECTOR_AVG, ["2026-09-04", "2026-10-02", "KR", "반도체", 1]).fetchone()
    assert n == 5 and round(avg, 4) == -0.05
