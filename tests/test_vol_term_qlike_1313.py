"""19회차 A3 사전 등록 검증 스크립트 — 손실·판정 규칙이 문서대로인가 (docs/infra.md 25.1313). 실행 결과는 보지 않는다."""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import vol_term_qlike as q  # noqa: E402


def test_qlike_는_맞으면_0_이고_과소예측을_더_벌한다() -> None:
    assert q.qlike(0.3, 0.3) == 0
    assert q.qlike(0.2, 0.3) > q.qlike(0.45, 0.3) > 0  # 같은 비율이면 과소예측(실현이 큼)이 더 아프다


def test_사전_등록_상수() -> None:
    assert q.LONG_DAYS == 756 and q.YEARS == 5 and q.DM_T == 1.96
    assert q.HORIZONS == (("short", 92 / 365, 63), ("mid", 1.0, 252))
    from batch.services import patterns, verdict

    assert patterns.EWMA_LAMBDA == 0.94 and verdict.VOL_HALF_LIFE_MONTHS == 2.0


def test_8칸_모두_이겨야_바꾼다() -> None:
    이김 = {"won": True}
    cells = {f"{c}/{h}/{half}": dict(이김) for c in q.COUNTRIES for h, _, _ in q.HORIZONS for half in ("first", "second")}
    assert q.decide(cells)["switch"]
    cells["US/mid/second"] = {"won": False}
    assert not q.decide(cells)["switch"]
    assert not q.decide({k: v for k, v in list(cells.items())[:7]})["switch"]  # 칸이 모자라도 안 바꾼다
    assert "min(L 기준, σ(t) 기준)" in q.decide(cells)["rule"]


def test_변동성이_뭉치면_σt_가_더_잘_맞힌다() -> None:
    """조용하다가 마지막 석 달 시끄러워진 계열 — 기준일 뒤도 시끄럽다. 단기 σ(t) 가 3년 평균보다 실현에 가깝다."""
    rnd = random.Random(7)
    closes, p = [], 100.0
    for k in range(756 + 63 + 64):
        vol = 0.005 if k < 756 - 63 else 0.03
        p *= math.exp(rnd.gauss(0, vol))
        closes.append(p)
    dates = [f"d{k:05d}" for k in range(len(closes))]
    i = 756 + 10
    L손실, σ손실 = q.pair_losses(dates, closes, i, 92 / 365, 63)  # type: ignore[misc]
    assert σ손실 < L손실


def test_자료가_모자라면_빼고_DM_은_시차를_쓴다() -> None:
    assert q.pair_losses(["d"] * 100, [1.0] * 100, 50, 1.0, 252) is None
    assert q.dm_t([0.1, 0.1], 3) is None
    t = q.dm_t([0.2, 0.1, 0.3, 0.2, 0.25, 0.15], 2)
    assert t is not None and t > 0
    v = q.cell_verdict([0.2, 0.1, 0.3, 0.2, 0.25, 0.15], 63)
    assert v["dates"] == 6 and v["won"] == (v["dm_t"] >= q.DM_T)


def test_사본_스키마로_돈다(tmp_path: Path) -> None:
    """시세 사본(`price_replica.open_replica` + 통째로 복사한 stocks)과 같은 모양에서 SQL 이 돈다 — 자료가 짧아 칸은 빈다."""
    import sqlite3

    from batch.core import price_replica

    conn = price_replica.open_replica(tmp_path / "r.sqlite")
    conn.execute("CREATE TABLE stocks (id INTEGER, country TEXT, asset_type TEXT)")
    conn.execute("INSERT INTO stocks VALUES (1, 'KR', 'stock')")
    conn.executemany("INSERT INTO prices (id, stock_id, date, close, adj_close) VALUES (?, 1, ?, 100, 100)",
                     [(k, f"2026-0{1 + k // 28}-{1 + k % 28:02d}") for k in range(100)])  # fmt: skip
    conn.commit()
    conn.close()
    out = q.run(str(tmp_path / "r.sqlite"), None)
    assert out["switch"] is False and all(c["dates"] == 0 for c in out["cells"].values())
    sqlite3.connect(str(tmp_path / "r.sqlite")).close()
