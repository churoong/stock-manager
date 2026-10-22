"""텔레그램 리포트 가독성 (docs/infra.md 25.1721, 2026-10-22 사용자 지시 "텔레그램 리포트가 너무 가독성이 떨어진다")."""

from __future__ import annotations

from batch.notify import formatter
from batch.services import desk
from batch.services import paper as pp


def test_미국_이름은_법인_꼬리를_떼고_티커를_붙인다() -> None:
    assert formatter.short_name("LTC Properties, Inc. Common Stock", "LTC", "USD") == "LTC Properties(LTC)"
    assert formatter.short_name("Kennametal Inc. Common Stock", "KMT", "USD") == "Kennametal(KMT)"
    assert formatter.short_name("Alphabet Inc. Class A Common Stock", "GOOGL", "USD") == "Alphabet(GOOGL)"
    assert formatter.short_name("Fastenal Company", "FAST", "USD") == "Fastenal(FAST)"
    # 너무 길면 자른다, 국내 이름은 그대로
    assert formatter.short_name("Taiwan Semiconductor Manufacturing Company Ltd.", "TSM", "USD").endswith("…(TSM)")
    assert formatter.short_name("삼성전자", "005930", "KRW") == "삼성전자"


def test_큰_원화는_억_만_단위() -> None:
    assert formatter.won_short(996_547_801) == "9억 9,655만원"
    assert formatter.won_short(1_000_692_802) == "10억 69만원"
    assert formatter.won_short(100_000_000) == "1억원"
    assert formatter.won_short(99_990_000) == "9,999만원"
    assert formatter.won_short(5_000) == "5,000원"
    assert formatter.won_short(-2_500_000) == "-250만원"
    assert formatter.won_short(None) == "-"


def test_같은_종목_회차는_한_줄로_묶는다() -> None:
    """스크린샷 그대로 — ONEOK 세 회차·Innovex 두 회차가 다섯 줄을 채웠다"""
    주문 = [{"side": "buy", "name": "ONEOK, Inc. Common Stock", "ticker": "OKE", "quantity": q, "order_type": "limit",
             "limit_price": p, "currency": "USD"} for q, p in ((161, 93.12), (124, 90.80), (127, 88.47))]  # fmt: skip
    주문 += [{"side": "buy", "name": "Innovex International, Inc. Common Stock", "ticker": "INVX", "quantity": q,
              "order_type": "limit", "limit_price": p, "currency": "USD"} for q, p in ((570, 29.45), (438, 28.72))]  # fmt: skip
    주문 += [{"side": "sell", "name": "삼성전자", "ticker": "005930", "quantity": 3, "order_type": "market",
              "limit_price": None, "currency": "KRW"}]  # fmt: skip
    assert desk.order_groups(주문) == [
        "매수 ONEOK(OKE) 3회차 412주 $88.47~$93.12",
        "매수 Innovex International(INVX) 2회차 1,008주 $28.72~$29.45",
        "매도 삼성전자 3주 시장가",
    ]


def test_실제_투자_비교는_여러_줄이고_가상모의투자라고_쓴다() -> None:
    글 = pp.compare_line({"start": "2026-10-16", "end": "2026-10-21", "real_pct": 0.3,
                          "paper_pct": {"A": -0.3, "B": -0.6}, "paper_start": {}})  # fmt: skip
    줄 = str(글).split("\n")
    assert 줄[0] == "실제 투자와 비교 (2026-10-16~2026-10-21, 시간가중)"
    assert 줄[1] == "  실제 +0.3%" and 줄[2] == "  가상 A -0.3% · B -0.6%"
    assert "가상모의투자는" in 줄[3] and "모의는" not in 글
