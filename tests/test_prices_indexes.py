"""`prices` 의 인덱스가 D1 쓰기 예산을 좌우한다 (docs/infra.md 25.22, migrations/0039).

**왜 이 테스트가 있나.** D1 은 쓰기를 셀 때 인덱스까지 센다. `prices` 한 행을 넣을 때마다
표 1 + 인덱스 수만큼 하루 한도(10만 행)를 태운다. 2026-09-20 에 `idx_prices_stock_date` 가
`UNIQUE (stock_id, date)` 자동 인덱스와 완전히 겹친다는 것을 실측하고 지웠다 — 배수가 4 → 3 이
되어 같은 예산으로 받는 거래일이 33% 늘었다.

**그래서 인덱스를 다시 더하면 `PRICE_ROW_D1_COST` 도 함께 올려야 한다.** 둘이 어긋나면 예산
계산이 틀리고, 틀린 계산이 하루 한도를 태운다. 그 관계를 여기서 고정한다.

`idx_prices_date` 는 남긴다 — 날짜만으로 찾는 질의(시장별 최신 시세일 등)가 이것 없이는
종목 수만큼 탐색한다 (migrations/0026 의 실측).
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
from pathlib import Path

from batch.jobs.backfill_kr import PRICE_ROW_D1_COST

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

#: `prices` 에 남아 있어야 하는 인덱스. 자동 인덱스(UNIQUE)는 이름이 SQLite 가 정한다
기대_인덱스 = {"sqlite_autoindex_prices_1", "idx_prices_date"}

#: 시세 질의가 인덱스를 타는지 보는 대표 질의들. 하나라도 표를 통째로 훑으면 읽기 예산이 샌다
대표_질의 = [
    "SELECT close FROM prices WHERE stock_id = 1 AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
    "SELECT date, close FROM prices WHERE stock_id = 1 ORDER BY date DESC LIMIT 200",
    "SELECT date, close FROM prices WHERE stock_id = 1 AND date >= '2025-06-01' ORDER BY date",
    "SELECT MAX(date) FROM prices WHERE stock_id = 1",
    "SELECT DISTINCT date FROM prices WHERE date BETWEEN '2026-01-01' AND '2026-09-01'",
]


def _적용된_스키마() -> sqlite3.Connection:
    """마이그레이션을 순서대로 적용한 빈 DB. D1 전용 구문은 건너뛴다."""
    con = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        with contextlib.suppress(sqlite3.Error):  # 이미 있는 것/방언 차이는 넘어간다
            con.executescript(path.read_text(encoding="utf-8"))
    return con


def test_prices_인덱스가_기대와_같다() -> None:
    con = _적용된_스키마()
    있는_것 = {
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='prices'")
    }
    con.close()
    assert 있는_것 == 기대_인덱스, (
        f"prices 인덱스가 바뀌었다: {있는_것}. 늘었으면 PRICE_ROW_D1_COST 도 올려야 한다"
        " (docs/infra.md 25.22)"
    )


def test_인덱스_수와_쓰기_배수가_맞는다() -> None:
    """표 1행 + 인덱스 수 = D1 이 세는 행 수. 어긋나면 예산 계산이 틀린다."""
    기대_배수 = 1 + len(기대_인덱스)
    assert 기대_배수 == PRICE_ROW_D1_COST, (
        f"인덱스 {len(기대_인덱스)}개면 배수는 {기대_배수} 여야 한다 (지금 {PRICE_ROW_D1_COST})"
    )


def test_지운_인덱스는_다시_들어오지_않는다() -> None:
    """0039 로 지운 뒤 누군가 초기 스키마를 고쳐 되살릴 수 있다. 그건 되돌리는 것이 아니라 예산을 태우는 것이다."""
    con = _적용된_스키마()
    남았나 = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_prices_stock_date'"
    ).fetchone()
    con.close()
    assert 남았나 is None, "idx_prices_stock_date 는 UNIQUE(stock_id, date) 와 겹친다 (migrations/0039)"


def test_대표_질의가_표를_통째로_훑지_않는다() -> None:
    """인덱스를 지운 대가로 전체 훑기가 생기면 안 된다. 읽기도 무료 한도가 있다."""
    con = _적용된_스키마()
    # 질의 목록이 비면 아래가 0번 돌고 통과한다 — 인덱스 회귀를 아무도 못 잡는다
    assert len(대표_질의) >= 3, f"대표 질의가 {len(대표_질의)}개뿐이다"
    for sql in 대표_질의:
        계획 = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql))
        assert "SCAN prices" not in 계획, f"표를 통째로 훑는다: {sql}\n  {계획}"
        assert "TEMP B-TREE" not in 계획, f"정렬용 임시 B-TREE 가 생긴다: {sql}\n  {계획}"
    con.close()


def test_0039_가_인덱스를_지운다() -> None:
    """마이그레이션 파일이 실제로 그 일을 하는지. 주석만 남고 문장이 빠진 적이 있다."""
    본문 = (MIGRATIONS / "0039_drop_redundant_prices_index.sql").read_text(encoding="utf-8")
    문장 = [line for line in 본문.splitlines() if line.strip() and not line.strip().startswith("--")]
    assert re.search(r"DROP\s+INDEX\s+IF\s+EXISTS\s+idx_prices_stock_date", " ".join(문장), re.IGNORECASE)
