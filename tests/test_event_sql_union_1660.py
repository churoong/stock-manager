"""25.1660 — 백테스트 자사주·희석 공시 질의를 UNION ALL 로: 결과는 같고 "종목 수 × 잇지 못한 공시 수" 읽기가 없다.

11-03 판정 실행 감사 [필수] 5 — OR 조인은 종목 표를 훑으며 종목마다 stock_id 없는 공시를 통째로 다시 읽었다.
"""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from batch.jobs import backtest as job

옛_조인 = (
    "SELECT s.id AS stock_id, d.title, d.disclosed_at FROM disclosures d JOIN stocks s"
    " ON s.id = d.stock_id OR (d.stock_id IS NULL AND s.dart_corp_code = d.corp_code)"
)


def _db() -> sqlite3.Connection:
    k = sqlite3.connect(":memory:")
    for f in sorted((Path(__file__).resolve().parent.parent / "migrations").glob("*.sql")):
        k.executescript(f.read_text(encoding="utf-8"))
    return k


def _채움(k: sqlite3.Connection) -> None:
    rnd = random.Random(11)
    for i in range(1, 61):
        나라 = "KR" if i <= 45 else "US"
        # 같은 고유번호를 나눠 가진 종목도 있다(i 와 i+40)
        k.execute("INSERT INTO stocks (id, ticker, market, country, currency, dart_corp_code, source, fetched_at)"
                  " VALUES (?, ?, 'KOSPI', ?, 'KRW', ?, 't', 't')", [i, f"{i:06d}", 나라, f"{i % 40:08d}"])  # fmt: skip
    제목 = ["자기주식취득결정", "유상증자결정", "전환사채권발행결정", "단일판매공급계약", "자기주식처분결정"]
    for j in range(400):
        sid = rnd.choice([None, None, *range(1, 61)])
        k.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, url, source,"
                  " fetched_at) VALUES (?, ?, ?, ?, ?, 'u', 't', 't')",
                  [sid, f"{rnd.randrange(50):08d}", f"2026{j:010d}", rnd.choice(제목),
                   f"2026-0{1 + j % 9}-1{j % 9}"])  # fmt: skip


def test_결과가_옛_OR_조인과_같다() -> None:
    k = _db()
    _채움(k)
    for 나라 in ("KR", "US"):
        옛 = sorted(k.execute(옛_조인 + " WHERE s.country = ? AND d.title LIKE ?", [나라, "%자기주식%"]).fetchall())
        새 = sorted(k.execute(job.BUYBACK_EVENTS_SQL, [나라, "%자기주식%", 나라, "%자기주식%"]).fetchall())
        assert 옛 == 새 and 새
        옛 = sorted(k.execute(옛_조인 + " WHERE s.country = ? AND (d.title LIKE '%증자%' OR d.title LIKE '%사채%')",
                               [나라]).fetchall())  # fmt: skip
        새 = sorted(k.execute(job.DILUTION_EVENTS_SQL, [나라, 나라]).fetchall())
        assert 옛 == 새 and 새


def test_잇지_못한_공시는_색인으로만_찾는다() -> None:
    k = _db()
    for sql, 인자 in ((job.BUYBACK_EVENTS_SQL, ["KR", "%x%", "KR", "%x%"]), (job.DILUTION_EVENTS_SQL, ["KR", "KR"])):
        계획 = [str(r[-1]) for r in k.execute("EXPLAIN QUERY PLAN " + sql, 인자)]
        assert not any("MULTI-INDEX OR" in p for p in 계획), 계획
        assert not any(p.startswith("SCAN d") for p in 계획), 계획
