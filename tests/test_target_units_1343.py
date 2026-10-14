"""분할·병합을 건넌 증권사 목표가를 뺀다 (docs/infra.md 25.1343, 21회차 P1).

5:1 분할(09-01) 앞에 낸 목표가 100,000 은 지금 주가(약 20,000)와 단위가 다르다 — 예전에는 컨센서스 +400%,
같은 증권사의 분할 뒤 22,000 을 −78% "내림" 으로 셌다."""

from __future__ import annotations

from datetime import date, timedelta

from batch.core import db
from batch.services import target_units as tu
from batch.services import verdict as vd
from tests.test_portfolio_job import MemClient


def _mem(분할: bool = True) -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, source, fetched_at)"
              " VALUES (1, '005930', 'KOSPI', 'KR', '가', 'KRW', 't', 't')")  # fmt: skip
    d = date(2026, 7, 1)
    while d <= date(2026, 10, 13):
        if d.weekday() < 5:
            앞 = 분할 and d < date(2026, 9, 1)
            close = 100_000.0 if 앞 else 20_000.0
            adj = close / 5 if 앞 else None  # 국내: 기업행위 뒤 행은 수정종가가 비어 있다
            c.execute("INSERT INTO prices (stock_id, date, close, adj_close, currency, source, fetched_at)"
                      " VALUES (1, ?, ?, ?, 'KRW', 't', 't')", [d.isoformat(), close, adj])  # fmt: skip
        d += timedelta(days=1)
    return mem


의견들 = [{"stock_id": 1, "date": "2026-08-10", "broker": "가증권", "target_price": 100_000},
         {"stock_id": 1, "date": "2026-09-20", "broker": "가증권", "target_price": 22_000},
         {"stock_id": 1, "date": "2026-10-01", "broker": "나증권", "target_price": 24_000}]  # fmt: skip


def _빼기(mem: MemClient) -> tuple[list[dict], dict[int, int]]:
    return tu.drop_across_actions(mem, 의견들, "2026-10-14", lambda r: int(r["stock_id"]), lambda r: str(r["date"]))


def test_분할_앞의_목표가는_빠진다() -> None:
    남김, 뺌 = _빼기(_mem())
    assert [r["date"] for r in 남김] == ["2026-09-20", "2026-10-01"] and 뺌 == {1: 1}
    c = vd.consensus(남김, date(2026, 10, 14))
    assert c is not None and c["median"] == 23_000  # 예전: 가증권 최신(22,000)·나증권 → 같지만, 분할 앞 의견만 있으면 +400%


def test_수정종가가_비어_있는_보통_종목은_그대로() -> None:
    남김, 뺌 = _빼기(_mem(분할=False))
    assert len(남김) == 3 and 뺌 == {}  # 검증 B — 비면 1 로 읽어야 국내 전 종목 의견이 사라지지 않는다


def test_분할_앞_의견만_있으면_컨센서스가_없다() -> None:
    남김, _ = tu.drop_across_actions(_mem(), 의견들[:1], "2026-10-14", lambda r: int(r["stock_id"]),
                                    lambda r: str(r["date"]))  # fmt: skip
    assert 남김 == [] and vd.consensus(남김, date(2026, 10, 14)) is None  # 예전: 100,000 — 종가 대비 +400%


def test_그날_이전_시세가_없으면_빼고_오늘을_모르면_그대로() -> None:
    assert tu.same_units(None, 1.0) is False
    assert tu.same_units(0.2, None) is True
    assert tu.same_units(1.0, 1.01) is True and tu.same_units(0.2, 1.0) is False


def test_근거에_뺀_수를_남긴다() -> None:
    o = vd.outlook(close=20_000.0, close_date="2026-10-13", currency="KRW", momentum=None, risk=None, band=None,
                   opinions=의견들[1:], today=date(2026, 10, 14), opinions_dropped=1)  # fmt: skip
    assert any(e["label"] == "기업행위 전후 증권사 의견" and e["display"] == "1건 제외" for e in o["evidence"])


def test_반대_목소리에_분할_건넌_거짓_내림이_없다() -> None:
    """배선 — 종목 의견의 반대 목소리(`verdicts.against_kr`)가 같은 함수로 거른다."""
    from batch.jobs import verdicts as job

    mem = _mem()
    for o in 의견들[:2]:
        mem.conn.execute("INSERT INTO kr_opinions (stock_id, date, broker, target_price, source, fetched_at)"
                         " VALUES (?, ?, ?, ?, 'kis', 't')", [1, o["date"], o["broker"], o["target_price"]])  # fmt: skip
    경고: list[str] = []
    out = job.against_kr(mem, [1], date(2026, 10, 14), 경고)  # type: ignore[arg-type]
    assert 경고 == []
    assert not any("내" in a["text"] and "목표가" in a["text"] for a in out.get(1, []))  # 예전: 목표가 내림 1곳
