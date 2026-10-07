"""보유 종목 시간외 단일가 알림 (docs/intraday.md 1.2, docs/infra.md 25.992)."""

from __future__ import annotations

from batch.services import after_hours as ah


def test_문턱은_장중과_같은_설정_없거나_범위밖이면_기본() -> None:
    assert ah.spike_pct('{"spike_pct": 3, "volume_multiple": 3}') == 3.0
    assert ah.spike_pct(None) == ah.DEFAULT_SPIKE_PCT
    assert ah.spike_pct('{"spike_pct": 0.001}') == ah.DEFAULT_SPIKE_PCT
    assert ah.spike_pct("깨짐") == ah.DEFAULT_SPIKE_PCT


def test_문턱_이상만_체결_없으면_안_본다() -> None:
    보유 = [(1, "005930", "삼성전자"), (2, "000660", "SK하이닉스"), (3, "035420", "NAVER")]
    시세 = {"005930": {"price": 255000.0, "change_pct": -5.0, "volume": 1200},
            "000660": {"price": 1700000.0, "change_pct": 4.9, "volume": 10}, "035420": None}  # fmt: skip
    got = ah.hits(보유, 시세, 5.0)
    assert [h["stock_id"] for h in got] == [1]
    assert got[0]["message"] == "삼성전자(005930) 시간외 단일가 -5.0% · 255,000원 · 거래량 1,200주"
    assert got[0]["data"]["threshold_pct"] == 5.0
