"""미국 무작위 대조 R1·R2 갈림을 못박는다 (25.2068, 25.2067 교차검증 [권고]).

국내 R 틀(`tests/test_replay_parity_r_1929.py`)을 미국 틀 위에 실으면(25.2065 의 `_싣기`) 리플레이와 운영이 **결정적으로**
갈린다 — 운영 `jobs/paper.held_substitutes` 가 든 대체 종목의 옛 보유 자리를 함께 잇는 25.1929 의 차이에, 미국 합성 자료에선
옛 자리 신호가 나오기 때문이다. 고치는 것은 R 판 올림 때(25.1700 고르기 규칙 지문). 이 시험은 첫 갈림 자리와 그날 운영의
`substitute_overlap_hit` 를 단언한다 — 운영을 고치거나 자료가 바뀌면 깨져 알린다(그때 25.1929 의 xfail 과 함께 정리).
"""

from __future__ import annotations

from tests import test_replay_parity_ac_us_2065 as acu

r_us = acu._싣기("test_replay_parity_r_1929.py", "tests._parity_r_us_2068")
t = acu.t


def test_미국_R1_은_옛_자리_신호_날에_다른_대체_종목을_고른다() -> None:
    틀림 = t._L2("core_r1", "zero")
    assert 틀림 and 틀림[0].startswith("2024-08-15:"), 틀림[:1]
    assert "(9, 'buy', '무작위 대조'" in 틀림[0] and "(14, 'skip', '종목 비중 상한 도달'" in 틀림[0], 틀림[0]
    rp = t._리플레이("core_r1", "zero")
    # 운영 든 대체 장부: 종목 14 가 옛 자리 13 과 지금 자리 27 두 곳에
    assert r_us.운영든대체[(id(rp), "2024-08-15")].get(13) == 14 == r_us.운영든대체[(id(rp), "2024-08-15")].get(27)
    live = t._운영("core_r1", "zero")
    assert live["2024-08-15"].record.get("substitute_overlap_hit"), live["2024-08-15"].record.get("substitute_overlap")
    # 그 전날까지는 같다 — 첫 갈림이 옛 자리 신호 날
    assert not [x for x in 틀림 if x[:10] < "2024-08-15"]


def test_미국_R2_도_같은_갈래로_06_05_에_갈린다() -> None:
    """R2 L2 첫 갈림 2024-06-05 — 리플레이는 새 종목 22, 운영은 이미 든 6 을 고른다(옛 자리 12 의 신호). 슬리피지 판 L0 의
    02-14 원가 차(상대 약 8e-6)는 든 대체 장부가 처음 갈리는 03-20 보다 앞서 **이 갈래가 아니다** — 원인 [확인필요](25.2068)"""
    틀림 = t._L2("core_r2", "zero")
    assert 틀림 and 틀림[0].startswith("2024-06-05:") and "(22, 'buy', '무작위 대조'" in 틀림[0], 틀림[:1]
