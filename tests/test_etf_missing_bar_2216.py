"""국내 ETF 의 그날 행만 빠지고 더 뒤 날 행이 있을 때 — 정지로 보지 않고 기다렸다가 "시세 못 받음" 으로 닫는다
(docs/infra.md 25.2216, 체결 판정 [필수]).

국내 ETF 시세는 `daily._store_kr_etf_day` 가 그 실행의 날 하나만 받고 다시 받는 길이 없다. 예전엔 뒤 날 행이 있으면
그날을 정지로 봐(25.1713 의 지름길) 매도는 정지 미룸(`deferrals` 가 정지 청산 쪽으로 셈), 매수는 정지 만료가 됐다.
주식(`asset_type='stock'`)은 그 지름길이 그대로다 — 아래 대조 시험과 tests/test_missing_bar_1713.py
`test_더_뒤_행이_생기면_그날은_정지로`."""

from __future__ import annotations

import pytest

from tests.test_missing_bar_1713 import _3번_주문, _시세3
from tests.test_paper_job_1445 import Client, _시세, _하루, client, 설정  # noqa: F401 — 픽스처


def _종류(c: Client, asset_type: str) -> None:
    c.conn.execute("UPDATE stocks SET asset_type = ? WHERE id = 3", [asset_type])


def _매도_만들기(c: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """3번을 10-15 에 사고 10-16 종가 −9% 손절 → 10-19 매도 (test_missing_bar_1713 `test_기다림_끝난_매도…` 와 같은 길)"""
    _3번_주문(c, monkeypatch)
    _시세(c, "2026-10-15", 10_000, 10_000)
    _시세3(c, "2026-10-15", 10_100, 9_900)
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 10_000, 10_000)
    _시세3(c, "2026-10-16", 9_100, 9_000)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    assert c.q("SELECT DISTINCT fill_session FROM paper_orders WHERE side = 'sell'") == [("2026-10-19",)]


def _매도(c: Client) -> list[tuple]:
    return c.q("SELECT status, fill_session, deferrals, close_note FROM paper_orders WHERE side = 'sell'")


def test_ETF_매도_뒤_행이_있어도_기다림_안에선_열어_둔다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, "etf")
    _시세(c, "2026-10-19", 10_000, 10_000)  # 10-19 3번 행은 끝내 안 온다
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    _시세(c, "2026-10-20", 10_000, 10_000)
    _시세3(c, "2026-10-20", 9_000, 9_000)  # 뒤 날 행은 있다
    _, 기록 = _하루(c, monkeypatch, "2026-10-20", signals=False)
    assert 기록.get("waiting_bar", 0) >= 1
    assert set(_매도(c)) == {("open", "2026-10-19", 0, None)}
    # 기다림(2장) 끝 — 정지가 아니라 못 받은 날로: 오늘 판정일 다음 장(10-22)으로, 까닭에 "정지" 없음
    _시세(c, "2026-10-21", 10_000, 10_000)
    _시세3(c, "2026-10-21", 9_000, 9_000)
    _하루(c, monkeypatch, "2026-10-21", signals=False)
    assert set(_매도(c)) == {("open", "2026-10-22", 1, "그날 시세를 받지 못함 — 다음 장으로")}


def test_주식_매도는_뒤_행이_있으면_그날_정지로_미룬다(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """대조 — 같은 꼴의 주식은 예전 그대로 10-20 실행에서 정지 미룸"""
    c = client
    _매도_만들기(c, monkeypatch)
    _시세(c, "2026-10-19", 10_000, 10_000)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    _시세(c, "2026-10-20", 10_000, 10_000)
    _시세3(c, "2026-10-20", 9_000, 9_000)
    _하루(c, monkeypatch, "2026-10-20", signals=False)
    매도 = _매도(c)
    assert 매도 and all((s, f, d) == ("open", "2026-10-21", 1) and "정지" in n for s, f, d, n in 매도)


@pytest.mark.parametrize("asset_type", ["etf", "stock"])
def test_매수_뒤_행이_있을_때_ETF는_기다렸다_못받음_주식은_정지(
    client: Client, monkeypatch: pytest.MonkeyPatch, asset_type: str,  # noqa: F811
) -> None:  # fmt: skip
    c = client
    _3번_주문(c, monkeypatch)
    _종류(c, asset_type)
    _시세(c, "2026-10-15", 10_100, 9_900)  # 10-15 3번 행은 끝내 안 온다
    _하루(c, monkeypatch, "2026-10-15", signals=False)
    _시세(c, "2026-10-16", 9_900, 9_900)
    _시세3(c, "2026-10-16", 9_500, 9_500)
    _하루(c, monkeypatch, "2026-10-16", signals=False)
    상태 = c.q("SELECT DISTINCT status, close_note FROM paper_orders WHERE stock_id = 3 AND fill_session = '2026-10-15'")
    if asset_type == "stock":
        assert 상태 and all(s == "expired" and "정지" in n for s, n in 상태)
        return
    assert 상태 == [("open", None)]
    _시세(c, "2026-10-19", 9_900, 9_900)
    _시세3(c, "2026-10-19", 9_500, 9_500)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    상태 = c.q("SELECT DISTINCT status, close_note FROM paper_orders WHERE stock_id = 3 AND fill_session = '2026-10-15'")
    assert 상태 == [("expired", "그날 시세를 받지 못해 판정 못 함")]
    assert c.q("SELECT COUNT(*) FROM paper_decisions WHERE kind = 'missed' AND stock_id = 3") == [(0,)]


def test_ETF_수집_실패_경고는_다시_받는다고_하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.jobs import daily
    from batch.sources import krx

    class _R:
        ok, data, error, attempts, source = False, None, "시간 초과", 1, "krx"

    class _C:
        def execute(self, sql: str, *_a: object) -> object:
            class _Rs:
                rows = [("069500", 3)] if "asset_type = 'etf'" in sql else []
            return _Rs()

    monkeypatch.setattr(daily.db, "usage_blocked_today", lambda *_a, **_k: False)
    monkeypatch.setattr(daily.db, "record_api_call", lambda *_a, **_k: None)
    monkeypatch.setattr(krx, "fetch_etf_daily", lambda _d: _R())
    [경고] = daily._store_kr_etf_day(_C(), "20261019", "2026-10-19")  # type: ignore[arg-type]
    assert "다음 실행이 받습니다" not in 경고
    assert "다시 받지 않습니다" in 경고 and "시세 못 받음" in 경고


def test_ETF_매도_뒤_행도_없이_기다림이_끝나면_못받음으로(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """순수한 빠짐(뒤 날 행도 없음) — 기다림 끝에 정지 메모 없이 못 받은 날로 닫는다(25.2217 교차검증)"""
    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, "etf")
    for d in ("2026-10-19", "2026-10-20", "2026-10-21"):
        _시세(c, d, 10_000, 10_000)  # 3번 행은 끝내 없다
        _하루(c, monkeypatch, d, signals=False)
    [(s, f, n, 메모)] = set(_매도(c))
    assert (s, f, n) == ("open", "2026-10-22", 1) and 메모 == "그날 시세를 받지 못함 — 다음 장으로"


def test_ETF_매도_20번째_미룸이면_마지막_종가로_청산(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """시세가 끝내 안 오는 ETF(폐지·비활성)도 매도가 영영 열린 채 남지 않는다 — 예전 정지 길의 청산(25.2217)"""
    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, "etf")
    c.conn.execute("UPDATE paper_orders SET deferrals = 19 WHERE side = 'sell'")
    for d in ("2026-10-19", "2026-10-20", "2026-10-21"):
        _시세(c, d, 10_000, 10_000)
        _하루(c, monkeypatch, d, signals=False)
    상태 = c.q("SELECT DISTINCT status, close_note FROM paper_orders WHERE side = 'sell'")
    assert 상태 and all(s == "filled" and "마지막 종가로 청산" in (n or "") for s, n in 상태), 상태


def test_거래량_0_행으로_실린_ETF_정지는_예전_정지_길(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """행이 있으면(진짜 정지 — 거래량 0) 새 분기를 타지 않는다: 정지 미룸 그대로"""
    c = client
    _매도_만들기(c, monkeypatch)
    _종류(c, "etf")
    _시세(c, "2026-10-19", 10_000, 10_000)
    _시세3(c, "2026-10-19", 9_000, 9_000, volume=0)
    _하루(c, monkeypatch, "2026-10-19", signals=False)
    매도 = _매도(c)
    assert 매도 and all(s == "open" and d == 1 and "정지" in (n or "") for s, _f, d, n in 매도), 매도
