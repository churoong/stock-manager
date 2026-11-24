"""위험판의 지수 충격 — 각 시장 지수가 5% 빠지는 날의 예상 손익 (docs/desk.md 6장, docs/infra.md 25.2281).

docs/roadmap.md 1장 #4 1단계의 나머지. 표시만 한다 — 주문·사이징을 바꾸지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.jobs import desk as jd
from batch.services import desk

손절 = {"short": -7.0, "mid": -15.0, "long": -25.0}
뿌리 = Path(__file__).resolve().parent.parent


def _판(계좌: str, 종목: list[dict], betas: dict[int, float] | None, 보유: float = 0) -> dict:
    return desk.risk_board({계좌: {"equity": 1_000_000, "holdings": 보유, "positions": 종목}}, {}, 손절, betas)[0]


def _q(sid: int | None, 비중: float | None) -> dict:
    return {"stock_id": sid, "name": f"종목{sid}", "horizon": "long", "avg_price": 100.0, "close": 100.0, "weight_pct": 비중}


def test_손으로_센_지수_충격() -> None:
    r = _판("H", [_q(1, 40.0), _q(2, 20.0), _q(3, 10.0)], {1: 1.5, 2: 0.5, 3: -0.2})
    기대 = (40.0 * 1.5 + 20.0 * 0.5 + 10.0 * -0.2) * -desk.RISK_INDEX_SHOCK_PCT / 100
    assert r["index_shock_pct"] == pytest.approx(기대) and 기대 == pytest.approx(-3.4)
    assert "index_shock_assumed" not in r and "index_shock_skipped" not in r
    assert desk.RISK_INDEX_SHOCK_PCT == 5.0 and desk.RISK_BETA_UNKNOWN == 1.0


def test_베타를_모르면_1_로_보고_수를_알린다() -> None:
    """빼면 위험이 작게 보인다 — 시장만큼 움직인다고 보고 가정한 종목 수를 싣는다"""
    r = _판("H", [_q(1, 40.0), _q(2, 20.0), _q(None, 10.0)], {1: 2.0})
    assert r["index_shock_pct"] == pytest.approx((40.0 * 2.0 + 20.0 * 1.0 + 10.0 * 1.0) * -0.05)
    assert r["index_shock_assumed"] == 2
    # 베타 0 은 "모름" 이 아니다(지수와 무관한 보유)
    assert _판("H", [_q(1, 50.0)], {1: 0.0})["index_shock_pct"] == 0.0
    assert "index_shock_assumed" not in _판("H", [_q(1, 50.0)], {1: 0.0})


def test_비중이_없는_보유는_빼고_수를_알린다() -> None:
    r = _판("H", [_q(1, 40.0), _q(2, None)], {1: 1.0, 2: 1.0})
    assert r["index_shock_pct"] == pytest.approx(-2.0) and r["index_shock_skipped"] == 1
    하나도 = _판("H", [_q(2, None)], {2: 1.0})
    assert 하나도["index_shock_pct"] is None and 하나도["index_shock_skipped"] == 1


def test_보유가_없으면_0_이고_보유_글이_깨진_날은_모름이다() -> None:
    assert _판("H", [], {})["index_shock_pct"] == 0.0
    assert _판("H", [], {}, 보유=500_000)["index_shock_pct"] is None


@pytest.mark.parametrize("계좌", ["X", "Y"])
def test_ETF_전술_계좌는_값이_없다(계좌: str) -> None:
    """인버스를 드는 계좌 — 베타 1 가정이면 부호가 뒤집힌다"""
    from batch.services import etf_tactical

    # 목록의 원천은 ETF 전술 계좌 정의 — 계좌가 늘면 여기서 깨진다(25.2282, 25.2281 교차검증 [권고])
    assert set(desk.ETF_TACTICAL_IDS) == set(etf_tactical.ACCOUNTS) and desk.ETF_TACTICAL_IDS == ("X", "Y")
    for 종목 in ([_q(1, 90.0)], []):
        r = _판(계좌, 종목, {1: 1.0})
        assert r["index_shock_pct"] is None and "index_shock_assumed" not in r


@pytest.mark.parametrize("계좌", ["S", "T", "D", "ZL", "R1"])
def test_손절_규칙과_무관하게_주식_계좌는_잰다(계좌: str) -> None:
    """지수 충격은 손절선과 무관하다 — 열린 위험이 값 없음인 스윙·단타·장기보유에도 나온다"""
    assert _판(계좌, [_q(1, 50.0)], {1: 1.2})["index_shock_pct"] == pytest.approx(-3.0)


def test_베타를_못_읽은_날은_칸이_없고_나머지는_그대로다() -> None:
    종목 = [_q(1, 40.0)]
    없음, 있음 = _판("H", 종목, None), _판("H", 종목, {1: 1.0})
    assert "index_shock_pct" not in 없음
    assert {k: v for k, v in 있음.items() if not k.startswith("index_shock")} == 없음
    assert 없음["open_risk_pct"] == pytest.approx(40.0 * 0.25)


class _C:
    """`BETA_SQL` 을 실제 SQLite 로 돌린다 — 열 이름만 맞춘 손 표(색인 없음). 마이그레이션 스키마 그대로의 경로는
    `test_desk_gate_1508.test_write_puts_gate_risk_champion` 이 묶는다(25.2282)"""

    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE performance_metrics (stock_id INTEGER, as_of_date TEXT, window TEXT, beta REAL, calc_version INTEGER)"
        )
        self.본: list[tuple[str, list]] = []

    def execute(self, sql: str, params: list | None = None):
        self.본.append((sql, list(params or [])))
        cur = self.conn.execute(sql, params or [])
        이름 = [d[0] for d in cur.description or []]
        행 = cur.fetchall()
        return type("R", (), {"rows": 행, "dicts": lambda self: [dict(zip(이름, r, strict=True)) for r in 행]})()


def test_베타는_창_안의_가장_늦은_1Y_행_하나다() -> None:
    c = _C()
    c.conn.executemany("INSERT INTO performance_metrics VALUES (?, ?, ?, ?, ?)", [
        (1, "2026-11-10", "1Y", 1.1, 1), (1, "2026-11-17", "1Y", 1.3, 1), (1, "2026-11-17", "3Y", 9.0, 1),
        (1, "2026-11-17", "1Y", 1.4, 2),  # 같은 날이면 계산 판이 높은 쪽
        (2, "2026-11-17", "1Y", None, 1), (2, "2026-11-10", "1Y", 0.7, 1),  # 최근 행이 비면 그 앞 행
        (3, "2026-09-01", "1Y", 2.0, 1),  # 창(45일) 밖 — 묵은 베타는 쓰지 않는다
        (4, "2026-11-17", "3Y", 0.9, 1),  # 1Y 행 없음
    ])  # fmt: skip
    now = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
    assert jd._betas(c, [1, 2, 3, 4, 5], now) == {1: 1.4, 2: 0.7}
    sql, params = c.본[-1]
    assert sql == jd.BETA_SQL and params == ["2026-10-10", json.dumps([1, 2, 3, 4, 5])] and jd.BETA_MAX_AGE_DAYS == 45
    # 보유가 없으면 읽지 않는다
    assert jd._betas(c, [], now) == {} and len(c.본) == 1


def test_베타_읽기가_깨져도_위험판은_나오고_한도_오류는_올린다() -> None:
    class _깨짐:
        def __init__(self, 글: str) -> None:
            self.글 = 글

        def execute(self, sql, params=None):
            raise RuntimeError(self.글)

    now = datetime(2026, 11, 24, tzinfo=UTC)
    assert jd._betas(_깨짐("no such table: performance_metrics"), [1], now) is None
    한도글 = "D1 HTTP 400: exceeded D1's free tier daily row read limit"
    from batch.core import db

    assert db.quota_reason(RuntimeError(한도글))
    with pytest.raises(RuntimeError):
        jd._betas(_깨짐(한도글), [1], now)


def test_운용_요약의_위험판이_베타를_받는다() -> None:
    """이음매 — `_관문판` 은 다른 시험이 통째로 바꿔 끼워 돌린다. 베타를 넘기는 줄이 빠지면 칸이 말없이 사라진다"""
    import inspect

    assert "desk.risk_board(latest, 계열, 손절, _betas(client, ids, now))" in inspect.getsource(jd._관문판)


def test_문서가_식과_가정을_적는다() -> None:
    글 = (뿌리 / "docs" / "desk.md").read_text()
    for 조각 in ("**지수 충격**(`index_shock_pct`, 25.2281", "Σ 비중(%) × 베타 × (−충격 ÷ 100)",
               f"지수가 {desk.RISK_INDEX_SHOCK_PCT:g}%(`RISK_INDEX_SHOCK_PCT`) 빠지는 날",
               f"베타 {desk.RISK_BETA_UNKNOWN:g}(`RISK_BETA_UNKNOWN`)", "`index_shock_assumed`", "선형 어림",
               f"최근 {jd.BETA_MAX_AGE_DAYS}일(`BETA_MAX_AGE_DAYS`)", "주문·사이징을 바꾸지 않는다"):  # fmt: skip
        assert 조각 in 글, 조각
