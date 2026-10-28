"""국내 감성 채점 예약은 Actions 예약이 늦게 와도 08:27 KST 국내 일일 배치 전에 끝나는 시각이다 (docs/infra.md 25.1840).

07:40 KST(22:40 UTC)에 걸었을 때 10월 내내 00:26~03:08 UTC(1~4.5시간 늦게)에 시작해, 일일 배치가 늘 전날 채점분으로
감성을 집계했다 — 가장 새 기사(감쇠 가중이 가장 큰)가 빠지고, 하루 사이 기사가 몰린 종목은 미채점 문(25.834)이 감성을 비웠다.
"""

from __future__ import annotations

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"
#: 2026-10-13~28 실측 최대 지연(10-13 22:40 예약 → 03:08 UTC 시작) + 실행 시간(설치·모델·채점 약 10분)
MAX_DELAY_MIN = 4 * 60 + 28 + 10


def _cron(파일: str) -> tuple[int, int]:
    분, 시 = re.search(r'cron: "(\d+) (\d+) \* \* 0-4"', (WF / 파일).read_text(encoding="utf-8")).groups()  # type: ignore[union-attr]
    return int(시), int(분)


def test_늦게_와도_국내_일일_배치_전에_끝난다() -> None:
    감성 = _cron("sentiment-kr.yml")
    일일 = _cron("daily-kr.yml")
    assert 일일 == (23, 27)
    # 같은 UTC 날(일~목) 안에서 분으로 견준다 — 감성이 일일 배치보다 앞선 시각이어야 한다
    assert 감성[0] * 60 + 감성[1] + MAX_DELAY_MIN <= 일일[0] * 60 + 일일[1]


def test_창_끝_뒤에_돈다() -> None:
    """창은 직전 거래일 23:59:59 KST 까지 — 그 뒤(KST 자정 = 15:00 UTC 뒤)에 돌아야 그날 기사가 다 든다"""
    시, _ = _cron("sentiment-kr.yml")
    assert 시 >= 15
