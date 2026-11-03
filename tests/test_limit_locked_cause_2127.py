"""KIS만 참인 국내 시장가가 그날 가격제한 끝이었으면 까닭을 따로 센다 (25.2127, 11-03 결함 회차 [참고] ③).

가상은 하한가에 갇힌 시장가 매도·상한가 시장가 매수를 안 찼다고 본다(`paper.limit_locked`, 25.1553·2114). KIS 모의가 그날
찬 줄은 예전엔 "일봉으로 설명 안 됨"(`unexplained`) 으로 갔고, 괴리 알림은 "KIS만 참이 더 많음 — 가상 판정이 비관" 이라
말했다 — 모의 체결이 호가 대기열을 무시한 낙관일 수 있어 그 몫은 방향 판단에서 뺀다.
"""

from __future__ import annotations

from typing import Any

import pytest

from batch.services import broker_mirror as bm


def _줄(**kw: Any) -> dict[str, Any]:
    기본 = {"side": "sell", "i_order_type": "market", "i_limit": None, "b_limit": None, "b_market": "KR",
            "p_open": 1_405, "p_close": 1_405, "p_low": 1_405, "p_high": 1_405, "p_change": -29.93, "i_fill_basis": None}
    return 기본 | kw


@pytest.mark.parametrize(
    ("고침", "까닭"),
    [
        ({}, "limit_locked"),  # 기준가 2,005 → 하한가 1,405 시가 매도
        ({"p_open": 1_406}, "unexplained"),  # 한 단위 위 — 끝이 아니다
        ({"i_fill_basis": "close", "p_open": 1_500}, "limit_locked"),  # 장 열린 뒤 주문은 종가로
        ({"side": "buy", "p_open": 2_605, "p_close": 2_605, "p_change": 29.93}, "limit_locked"),  # 상한가 시가 매수
        ({"b_market": "US"}, "unexplained"),  # 미국은 가격제한이 없다
        ({"p_change": None}, "unexplained"),  # 등락률 없음(대체 출처) — 모름
        ({"p_change": -65.0, "p_open": 700, "p_close": 700}, "unexplained"),  # 정리매매 — 제한폭 밖
    ],
)
def test_KIS만_참_국내_시장가의_가격제한_끝(고침: dict[str, Any], 까닭: str) -> None:
    assert bm.mismatch_cause(_줄(**고침), False) == 까닭


def test_가상만_참은_이_까닭에_들지_않는다() -> None:
    assert bm.mismatch_cause(_줄(), True) == "market_unfilled"


def test_괴리_알림_방향에서_가격제한_끝은_뺀다() -> None:
    now = {"match_pct": 60.0, "internal_only": 2, "kis_only": 3, "mismatch_causes": {"limit_locked": 2}}
    글 = bm.diverge(now, ["match"], "KR")
    assert 글 is not None and "가상만 참이 더 많음" in 글 and "가격제한 끝 2건은 방향에서 뺌" in 글, 글
    없음 = bm.diverge(now | {"mismatch_causes": {}}, ["match"], "KR")
    assert 없음 is not None and "KIS만 참이 더 많음" in 없음
