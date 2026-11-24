"""운영 사고 대장 — 실패 묶음과 복구 시간 (docs/health.md 7.3, docs/infra.md 25.2289).

docs/roadmap.md 1장 #9 뒤 절반의 첫 조각. 실전 전환 근거의 하나가 "운영 무사고" 인데, 지금까지는 실패한 **실행 수**만
셌다(주간 요약의 상태 수) — 한 번의 장애가 재시도마다 실패 줄을 남겨 수가 부풀고, **얼마 만에 복구됐는지**는 어디에도
없었다. 새 표를 만들지 않는다: 이미 있는 실행 기록(`batch_runs`)에서 끌어낸다. 순수 계산 — 읽지도 쓰지도 않는다.

- **사고 한 건** = 같은 (작업, 시장)에서 실패(`failed`)로 시작해 다음 성공·부분 성공(`success`·`partial`)이 올 때까지의
  실패 묶음. 그 사이의 건너뜀·도는 중 줄은 사고를 닫지 않는다(안 돈 것이다)
- **복구 시간** = 첫 실패의 시작 시각 → 닫은 실행의 시작 시각(분). 끝 시각이 아니라 시작 시각끼리다 — 주간 요약이 이미
  읽는 칸만 쓴다(어림: 닫은 실행이 도는 시간만큼 짧게 잡힌다)
- **원인 갈래** = 첫 실패의 `error_text` 머리(첫 `:` 앞, 40자) — 예외 이름이 온다. 본문은 싣지 않는다
- 창 끝까지 닫히지 않은 사고는 "열림" 으로 따로 센다(복구 시간 없음)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: 사고를 여는 상태와 닫는 상태 — 건너뜀(`skipped`)·도는 중(`running`)·확인 실행(`dryrun`)은 어느 쪽도 아니다
OPEN_STATUS = ("failed",)
CLOSE_STATUS = ("success", "partial")
#: 원인 갈래 글의 최대 길이
CAUSE_MAX = 40
#: 요약에 따로 적는 사고 수(긴 것부터)
TOP_N = 3


def _때(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _갈래(error_text: Any) -> str:
    글 = str(error_text or "").strip().split("\n")[0]
    return (글.split(":")[0].strip() or "까닭 없음")[:CAUSE_MAX]


def incidents(runs: list[Any]) -> list[dict[str, Any]]:
    """실행 줄(시작 시각 오름차순이 아니어도 된다 — 여기서 세운다) → 사고 목록.

    줄은 `job_name`·`market`·`status`·`started_at`·`error_text` 속성을 가진 것(`jobs/weekly_summary.Run`).
    돌려주는 것: [{job, market, opened, closed(없으면 None), failures, minutes(없으면 None), cause}] — 연 시각 차례"""
    열린: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for r in sorted(runs, key=lambda x: str(x.started_at)):
        키 = (str(r.job_name), str(r.market or ""))
        if r.status in OPEN_STATUS:
            if 키 not in 열린:
                열린[키] = {"job": 키[0], "market": 키[1] or None, "opened": str(r.started_at), "closed": None,
                           "failures": 0, "minutes": None, "cause": _갈래(r.error_text)}  # fmt: skip
                out.append(열린[키])
            열린[키]["failures"] += 1
        elif r.status in CLOSE_STATUS and 키 in 열린:
            건 = 열린.pop(키)
            건["closed"] = str(r.started_at)
            시작, 끝 = _때(건["opened"]), _때(건["closed"])
            if 시작 is not None and 끝 is not None:
                try:
                    건["minutes"] = max(0, round((끝 - 시작).total_seconds() / 60))
                except TypeError:  # 시간대 있는 값과 없는 값이 섞임 — 복구 시간은 모름
                    건["minutes"] = None
    return out


def _분(m: int) -> str:
    return f"{m}분" if m < 120 else f"{m / 60:.1f}시간" if m < 60 * 48 else f"{m / 1440:.1f}일"


def lines(found: list[dict[str, Any]]) -> list[str]:
    """주간 요약에 싣는 절. 사고가 없으면 한 줄("사고 0건")"""
    if not found:
        return ["운영 사고(실패 묶음 → 복구): 0건"]
    닫힌 = sorted((x for x in found if x["minutes"] is not None), key=lambda x: -x["minutes"])
    열림 = [x for x in found if x["closed"] is None]
    머리 = f"운영 사고(실패 묶음 → 복구): {len(found)}건"
    if 닫힌:
        분들 = sorted(x["minutes"] for x in 닫힌)
        머리 += f" · 복구 중앙값 {_분(분들[(len(분들) - 1) // 2])} · 가장 긴 것 {_분(분들[-1])}"
    if 열림:
        머리 += f" · 아직 열림 {len(열림)}건"
    줄 = [머리]
    for x in [*열림, *닫힌][:TOP_N]:
        어디 = x["job"] + (f"({x['market']})" if x["market"] else "")
        언제 = "아직 복구 안 됨" if x["closed"] is None else f"복구 {_분(x['minutes'])}"
        줄.append(f"  {어디}: {x['opened'][:16]} 부터 실패 {x['failures']}번 · {언제} · {x['cause']}")
    if len(found) > TOP_N:
        줄.append(f"  … 그 밖 {len(found) - TOP_N}건")
    return 줄
