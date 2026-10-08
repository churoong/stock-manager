"""시장 전체 공시의 "덮은 날" 과 자사주·희석 IC 판정 가능성 (docs/infra.md 25.1014, 14회차 교차검증)."""

from __future__ import annotations

from datetime import date

import pytest

from batch.core import db
from batch.jobs import backtest as bt_job
from batch.jobs import disclosure_reaction as job
from batch.services import buyback as bb
from batch.sources import dart_disclosures as dd
from tests.test_portfolio_job import MemClient


def _mem() -> MemClient:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    # 같은 티커의 옛 행(이전상장 전, 고유번호 다름)과 지금 행 — 고유번호로 지금 행에 붙어야 한다
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, status, dart_corp_code)"
              " VALUES (1, '005930', 'KOSDAQ', 'KR', 'KRW', 't', 't', 'delisted', '99999999')")  # fmt: skip
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, status, dart_corp_code)"
              " VALUES (2, '005930', 'KOSPI', 'KR', 'KRW', 't', 't', 'active', '00126380')")  # fmt: skip
    c.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, status, dart_corp_code)"
              " VALUES (3, '000660', 'KOSPI', 'KR', 'KRW', 't', 't', 'active', '00164779')")  # fmt: skip
    return mem


def _공시(no: str, title: str, code: str, corp: str, day: str) -> dd.MarketDisclosure:
    return dd.MarketDisclosure(corp, code, no, title, day, "B")


def test_주말도_부르고_오류_없이_받은_날만_덮은_날로_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    부른날: list[str] = []

    def 가짜(day: str, kind: str):  # noqa: ANN202
        부른날.append(day)
        if day == "20260415":
            return [_공시("20260415000001", "주요사항보고서(자기주식취득결정)", "005930", "00126380", "2026-04-15")], 1, None
        if day == "20260419":  # 일요일 다음 월요일 앞 — 잘림이면 덮은 날이 아니다
            return [], 2, "잘림: 20260419 B 9쪽 중 2쪽"
        return [], 1, None

    monkeypatch.setattr(dd, "fetch_market_day", 가짜)
    out = job.collect(mem, date(2026, 4, 15), date(2026, 4, 20), ("B",))  # type: ignore[arg-type]
    assert 부른날 == ["20260415", "20260416", "20260417", "20260418", "20260419"]  # 토·일 포함, 오류에서 멈춤
    assert out["covered"] == 4 and out["stopped_at"] == "2026-04-19" and out["error"].startswith("잘림")
    덮은 = [r[0] for r in mem.conn.execute("SELECT day FROM disclosure_coverage WHERE kind = 'B' ORDER BY day")]
    assert 덮은 == ["2026-04-15", "2026-04-16", "2026-04-17", "2026-04-18"]
    # 고유번호로 지금 행(2)에 붙는다 — 티커만 보면 옛 행(1)일 수 있었다
    assert mem.conn.execute("SELECT stock_id FROM disclosures").fetchall() == [(2,)]


def test_잇지_못한_공시는_버리지_않고_고유번호로_나중에_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    mem = _mem()
    monkeypatch.setattr(dd, "fetch_market_day", lambda day, kind: (
        [_공시("20260415000009", "주요사항보고서(자기주식취득결정)", "123456", "55555555", "2026-04-15")], 1, None))
    job.collect(mem, date(2026, 4, 15), date(2026, 4, 15), ("B",))  # type: ignore[arg-type]
    assert mem.conn.execute("SELECT stock_id, corp_code FROM disclosures").fetchall() == [(None, "55555555")]
    # 신규 상장이 나중에 stocks 에 들어오면 고유번호로 이어 읽힌다
    mem.conn.execute("INSERT INTO stocks (id, ticker, market, country, currency, source, fetched_at, status,"
                     " dart_corp_code) VALUES (4, '123456', 'KOSDAQ', 'KR', 'KRW', 't', 't', 'active', '55555555')")  # fmt: skip
    events, _ = bt_job.load_buyback_events(mem, "KR")  # type: ignore[arg-type]
    assert events == {4: ["2026-04-15"]}


def test_이어지는_날만_한_구간으로() -> None:
    assert bt_job.coverage_ranges(["2026-01-03", "2026-01-01", "2026-01-02", "2026-01-05"]) == [
        ("2026-01-01", "2026-01-03"), ("2026-01-05", "2026-01-05")]


def test_시장_전체_B_로_덮은_창은_국내_전_종목이_0_또는_1() -> None:
    """예전에는 `disclosures_kr --all` 의 covered_ids 만 덮은 구간이라 1년치를 모아도 전부 None 이었다."""
    mem = _mem()
    c = mem.conn
    for d in range(366):
        day = date.fromordinal(date(2025, 10, 1).toordinal() + d).isoformat()
        c.execute("INSERT INTO disclosure_coverage VALUES ('B', ?, 0, 1, 't', 't')", [day])
    c.execute("INSERT INTO disclosures (stock_id, corp_code, receipt_no, title, disclosed_at, is_material, source,"
              " fetched_at) VALUES (2, '00126380', 'r1', '주요사항보고서(자기주식취득결정)', '2026-03-02', 1, 't', 't')")  # fmt: skip
    events, windows = bt_job.load_buyback_events(mem, "KR")  # type: ignore[arg-type]
    assert bb.flag(events.get(2, []), "2026-09-30", windows.get(2, [])) == 1
    assert bb.flag(events.get(3, []), "2026-09-30", windows.get(3, [])) == 0
    # 창이 덮은 날 밖으로 나가면 모른다
    assert bb.flag(events.get(3, []), "2026-10-15", windows.get(3, [])) is None
    # 미국은 이 기록을 쓰지 않는다
    assert bt_job.load_buyback_events(mem, "US")[1] == {}
