"""미국 수정주가 어긋남 감지 (docs/adjust.md 8장).

야후 Adj Close 는 배당·분할이 생기면 **그 이전 날짜 전부**가 다시 조정된다. 같은 날짜의
adj_close / close 비율이 저장된 것과 새로 받은 것에서 다르면 그 종목의 옛 행은 새 계열과
기준이 어긋난 것이다. 비율이 같은 종목은 건드릴 이유가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 비율 차이가 이보다 크면 어긋난 것으로 본다. 야후가 소수 넷째 자리에서 반올림해 주므로
# 0.5% 면 배당(보통 0.3%~2%)은 잡고 반올림 잡음은 넘긴다. 추정 [확인필요: 실측으로 조정]
TOLERANCE = 0.005


@dataclass(frozen=True)
class Sample:
    """한 종목의 어느 날짜 (종가, 수정종가)."""

    date: str
    close: float
    adj_close: float | None


@dataclass(frozen=True)
class Drift:
    stock_id: int
    date: str
    ratio_before: float
    ratio_after: float


def ratio(sample: Sample) -> float | None:
    if sample.adj_close is None or sample.close <= 0:
        return None
    return sample.adj_close / sample.close


def detect(stored: dict[int, Sample], incoming: dict[int, Sample], tolerance: float = TOLERANCE) -> list[Drift]:
    """같은 종목·같은 날짜의 비율을 견준다. 한쪽에 수정종가가 없으면 판단하지 않는다."""
    out: list[Drift] = []
    for stock_id, new in incoming.items():
        old = stored.get(stock_id)
        if old is None or old.date != new.date:
            continue
        before, after = ratio(old), ratio(new)
        # **분할은 종가 자체로 본다** (docs/adjust.md 8장, docs/infra.md 25.498). 야후 Close 는 받을 때 분할을 반영해
        # 옛 날짜의 종가·수정종가가 같은 배수로 바뀐다 — 비율은 그대로라 아래 검사로는 못 잡았다(차트에 분할일 절벽)
        if old.close > 0 and new.close > 0 and abs(new.close / old.close - 1) > tolerance:
            # 두 비율을 **같게** 적는다 — 대기열에서 분할의 표시다(비율 어긋남은 정의상 0.5% 넘게 다르다).
            # 재수집이 분할을 배당보다 먼저 받는 근거가 된다 (docs/infra.md 25.503, 교차검증)
            기준 = after if after is not None else 1.0
            out.append(Drift(stock_id, new.date, 기준, 기준))
            continue
        if before is None or after is None or before <= 0:
            continue
        if abs(after / before - 1) > tolerance:
            out.append(Drift(stock_id, new.date, before, after))
    return out


#: 분할 감지 기록을 두는 설정 키 (docs/infra.md 25.535). 대기열의 표시(두 비율이 같음)는 재수집 순서만 정한다 —
#: 감지일까지 거기에 얹으면 배당 감지와 뒤엉켜 새 분할을 놓치거나 옛 분할이 영구히 남았다(25.531 교차검증)
SPLIT_LOG_KEY = "us_split_detections"
#: 기록을 두는 기간(일). 분기 보고서 한 번(주식수 갱신)과 장중 보호 기간을 넉넉히 넘는다
SPLIT_LOG_DAYS = 200


def merge_split_log(log: dict, stock_ids: list[int], now: str) -> dict[str, str]:
    """{종목번호(문자열): 마지막 분할 감지 시각}. 새로 감지된 종목은 지금으로, 오래된 것은 버린다."""
    from datetime import datetime, timedelta

    out = {str(k): str(v) for k, v in (log or {}).items()}
    for sid in stock_ids:
        out[str(sid)] = now
    try:
        경계 = (datetime.fromisoformat(now) - timedelta(days=SPLIT_LOG_DAYS)).isoformat()
    except ValueError:
        return out
    return {k: v for k, v in out.items() if v >= 경계}
