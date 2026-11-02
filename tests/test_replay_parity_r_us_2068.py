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
    02-14 원가 차(상대 약 8e-6)는 든 대체 장부가 처음 갈리는 03-20 보다 앞서 **이 갈래가 아니다** — 원인은 더 산 로트의 원가 평균
    근사 ⑦(25.2076)"""
    틀림 = t._L2("core_r2", "zero")
    assert 틀림 and 틀림[0].startswith("2024-06-05:") and "(22, 'buy', '무작위 대조'" in 틀림[0], 틀림[:1]
    # 같은 갈래인 근거(25.2069, 교차검증 note — 날짜만으론 못 묶는다: 장부 12→6 은 05-17 부터(첫 등장 03-20), `_hit` 은
    # 05-31~06-04 에도 켜졌지만 자리가 차 뽑지
    # 않았다): 그날 운영 든 대체 장부의 옛 자리 12 가 이미 든 6 을 잇고, 옛 자리 신호가 나온 날이다
    rp = t._리플레이("core_r2", "zero")
    assert r_us.운영든대체[(id(rp), "2024-06-05")].get(12) == 6
    live = t._운영("core_r2", "zero")
    assert live["2024-06-05"].record.get("substitute_overlap_hit"), live["2024-06-05"].record.get("substitute_overlap")
    # 그날로 묶는 단언(25.2070, 교차검증 note — 위 둘은 06-05 앞에도 참이다): 운영은 옛 자리 12 의 신호를 이미 든 6 으로
    # 잇고, 리플레이는 새 종목 22 로 바꾼다
    assert (live["2024-06-05"].record.get("random") or {}).get("12") == 6, live["2024-06-05"].record.get("random")
    assert (t._날별(rp)["2024-06-05"].random.get("random") or {}).get("12") == 22, t._날별(rp)["2024-06-05"].random


def test_미국_R2_슬리피지_판_원가_차는_더_산_로트의_원가_평균_근사뿐() -> None:
    """25.2068 의 "R2 slip 02-14 원가 차 — 원인 [확인필요]" 를 풀었다(25.2076). 그 로트(종목 1, 01-17 첫 매수)는 **더 산 로트**고,
    차이는 허용 ⑦(리플레이 `_reconcile` 이 더 산 몫의 원가를 산 날 종가 비중의 주식 수 비율로 근사 — 운영은 주식 수 가중
    평균)의 크기다: 한 번만 산 로트는 미국에서도 같고(2e-16), 더 산 로트만 최대 약 8.0e-6(국내 실측 최대 2.6e-6·허용 5e-6
    을 넘음). 허용은 넓히지 않는다 — R 미국 판은 갈림(옛 자리 잇기)으로 이미 미검증이다. 이 시험은 차이의 **꼴**을 못박는다"""
    rp, live = t._리플레이("core_r2", "slip"), t._운영("core_r2", "slip")
    한번, 더 = 0.0, 0.0
    for d, h in live.items():
        for sid, (_첫, _기간, 원가, 더삼) in rp.장부[d].items():
            if sid not in h.lots:
                continue
            차 = abs(원가 / h.lots[sid][2] - 1)
            if 더삼:
                더 = max(더, 차)
            else:
                한번 = max(한번, 차)
    assert 한번 <= 1e-9, 한번
    # ⑦ 꼴 — 근사 크기만 국내보다 크다. 리플레이 `_reconcile` 의 근사를 운영처럼 주식 수 가중 평균으로 고쳐 차이가 줄면
    # 이 아래쪽 경계(⑦ 보다 큼)는 지운다 — 결함 신호가 아니다(25.2082, 교차검증 note)
    assert t.ALLOW_COST_AVERAGING_REL < 더 <= 2e-5, 더
    assert rp.장부["2024-02-14"][1][3] is True  # 02-14 종목 1 은 더 산 로트
