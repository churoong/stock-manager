"""운영 사고 대장 — 실패 묶음과 복구 시간 (docs/health.md 7.3, docs/infra.md 25.2289).

docs/roadmap.md 1장 #9 뒤 절반의 첫 조각. 실전 전환 근거의 하나가 "운영 무사고" 인데, 지금까지는 실패한 **실행 수**만
셌다(주간 요약의 상태 수) — 한 번의 장애가 재시도마다 실패 줄을 남겨 수가 부풀고, **얼마 만에 복구됐는지**는 어디에도
없었다. 새 표를 만들지 않는다: 이미 있는 실행 기록(`batch_runs`)에서 끌어낸다. 순수 계산 — 읽지도 쓰지도 않는다.

- **사고 한 건** = 같은 (작업, 시장)에서 실패(`failed`)로 시작해 다음 성공·부분 성공(`success`·`partial`)이 올 때까지의
  실패 묶음. 그 사이의 건너뜀·도는 중 줄은 사고를 닫지 않는다(안 돈 것이다)
- **복구 시간** = 첫 실패의 시작 시각 → 닫은 실행의 시작 시각(분). 끝 시각이 아니라 시작 시각끼리다 — 주간 요약이 이미
  읽는 칸만 쓴다(어림: 닫은 실행이 도는 시간만큼 짧게 잡힌다)
- **원인 갈래** = 첫 실패의 `error_text` 머리(첫 `:` 앞, 40자). 예외 이름을 적는 작업은 이름이, 대부분의 작업은
  `str(exc)` 를 적으므로 **문장 머리**가 온다(25.2290 정정 — 처음엔 "예외 이름, 본문 없음" 이라 적었다)
- 창 끝까지 닫히지 않은 사고는 "열림" 으로 따로 센다(복구 시간 없음)
- **걸친 날 수**(25.2295, 영향의 첫 조각) = 그 묶음의 실패·중단 줄이 선 **날짜**의 수(한국 날짜 — 시장이 미국인 줄은
  미국 동부 날짜, 25.2298). 하루 한 번 도는 작업이면 "그 작업이 제 일을 못 한 날 수" 에 가깝다(거래일 달력은 읽지 않는다
  — 어림. 달력 폭이 아니다: 금·월 이틀 실패는 2일이다)
- **같은 코드로 복구됐나**(25.2295) = 첫 실패의 코드와 닫은 실행의 코드를 견준다 — 둘 다 배치 코드 지문(`code`,
  25.2302 — 문서만 바꾼 커밋에는 안 바뀐다)이 있으면 그것으로, 아니면 커밋(`sha`, 25.2277)으로. 같으면 코드 고침 없이
  나은 것이고(바깥 까닭이 지나갔거나 깔린 판·설정·시크릿·자료가 바뀜), 다르면 그 사이에 코드가 바뀌었다(고침이 들어갔을
  수 있다 — **그 고침 덕인지는 모른다**. 커밋으로 견줄 때는 문서만 바꾼 커밋에도 선다). 어느 한쪽이라도 모르면(지문 이전
  줄·로컬 실행·정리된 줄) 적지 않는다
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from batch.core import db

#: 사고를 여는 상태와 닫는 상태 — 건너뜀(`skipped`)·도는 중(`running`)·확인 실행(`dryrun`)은 어느 쪽도 아니다.
#: 다만 **죽은 실행을 정리한 줄**(`skipped` + `db.REAPED_PREFIX` — 강제 종료·한도로 끝맺지 못한 기록)은 사고를 연다
#: (25.2290 — 관문의 무사고 칸 `desk.batch_incident_days` 가 25.2253 부터 그 줄을 사고로 센다. 같은 잣대)
OPEN_STATUS = ("failed",)
CLOSE_STATUS = ("success", "partial")
#: 원인 갈래 글의 최대 길이
CAUSE_MAX = 40
#: 요약에 따로 적는 사고 수(긴 것부터)
TOP_N = 3
#: 걸친 날 수를 줄에 적기 시작하는 값 — 하루 안의 사고(대부분)는 적지 않는다
SPAN_SHOW_DAYS = 2


def _때(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _갈래(error_text: Any) -> str:
    글 = str(error_text or "").strip().split("\n")[0]
    return (글.split(":")[0].strip() or "까닭 없음")[:CAUSE_MAX]


def _코드(r: Any) -> tuple[str | None, str | None]:
    """(배치 코드 지문, 커밋) — 줄에 그 속성이 없거나 비면 None"""
    return (getattr(r, "code", None) or None, getattr(r, "sha", None) or None)


def incidents(runs: list[Any]) -> list[dict[str, Any]]:
    """실행 줄(시작 시각 오름차순이 아니어도 된다 — 여기서 세운다) → 사고 목록.

    줄은 `job_name`·`market`·`status`·`started_at`·`error_text` 속성을 가진 것(`jobs/weekly_summary.Run`).
    줄에 `sha` 속성이 있으면(없어도 된다) 코드 견줌에 쓴다.
    돌려주는 것: [{job, market, opened, closed(없으면 None), failures, minutes(없으면 None), cause, days,
    same_code(True·False·모르면 None)}] — 연 시각 차례"""
    열린: dict[tuple[str, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    날들: dict[tuple[str, str], set[str]] = {}
    첫코드: dict[tuple[str, str], Any] = {}
    for r in sorted(runs, key=lambda x: str(x.started_at)):
        키 = (str(r.job_name), str(r.market or ""))
        죽음 = r.status == "skipped" and str(r.error_text or "").startswith(db.REAPED_PREFIX)
        if r.status in OPEN_STATUS or 죽음:
            if 키 not in 열린:
                열린[키] = {"job": 키[0], "market": 키[1] or None, "opened": str(r.started_at), "closed": None,
                           "failures": 0, "minutes": None, "cause": _갈래(r.error_text), "days": 0,
                           "same_code": None}  # fmt: skip
                out.append(열린[키])
                날들[키], 첫코드[키] = set(), _코드(r)
            열린[키]["failures"] += 1
            날들[키].add(_날(str(r.started_at), 키[1]))
            열린[키]["days"] = len(날들[키])
        elif r.status in CLOSE_STATUS and 키 in 열린:
            건 = 열린.pop(키)
            건["closed"] = str(r.started_at)
            앞, 뒤 = 첫코드.pop(키, None), _코드(r)
            # 같은 갈래끼리만 견준다 — 배치 코드 지문(`code`)이 둘 다 있으면 그것으로, 아니면 커밋(`sha`)으로(25.2302)
            if 앞 and 뒤 and 앞[0] and 뒤[0]:
                건["same_code"] = 앞[0] == 뒤[0]
            elif 앞 and 뒤 and 앞[1] and 뒤[1]:
                건["same_code"] = 앞[1] == 뒤[1]
            else:
                건["same_code"] = None
            시작, 끝 = _때(건["opened"]), _때(건["closed"])
            if 시작 is not None and 끝 is not None:
                try:
                    건["minutes"] = max(0, round((끝 - 시작).total_seconds() / 60))
                except TypeError:  # 시간대 있는 값과 없는 값이 섞임 — 복구 시간은 모름
                    건["minutes"] = None
    return out


#: 사고 줄의 시각은 **한국 시각**으로 싣는다(25.2290) — 요약 머리가 한국 날짜다(25.1274). 원문(UTC)을 그대로 실으면
#: 국내 일일 배치(08:27 KST = 전날 23:27 UTC)의 사고가 늘 하루 앞 날짜·밤 시각으로 읽혔다
SHOW_TZ = ZoneInfo("Asia/Seoul")


def _언제(s: str) -> str:
    """시작 시각 → "MM-DD HH:MM"(한국 시각). 못 읽거나 시간대가 없으면 원문 앞 16자 그대로"""
    t = _때(s)
    if t is None or t.tzinfo is None:
        return str(s)[:16]
    return t.astimezone(SHOW_TZ).strftime("%m-%d %H:%M")


#: 걸친 날 수를 세는 시간대 — 시장이 미국인 줄은 미국 동부 날짜로(25.2298). 미국 일일 배치는 13:27·14:27 UTC
#: (22:27·23:27 KST)라 한국 날짜로 세면 한국 자정을 넘긴 재시도가 한 미국 거래일을 이틀로 갈랐다
#: (25.2295 교차검증 [권고])
DAY_TZ = {"US": ZoneInfo("America/New_York")}


def _날(s: str, market: str = "") -> str:
    """시작 시각 → 그 작업 시장의 날짜("YYYY-MM-DD" — 미국은 동부, 그 밖은 한국).

    못 읽거나 시간대가 없으면 원문 앞 10자"""
    t = _때(s)
    if t is None or t.tzinfo is None:
        return str(s)[:10]
    return t.astimezone(DAY_TZ.get(market, SHOW_TZ)).strftime("%Y-%m-%d")


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
    적을것 = [*열림, *닫힌][:TOP_N]
    for x in 적을것:
        어디 = x["job"] + (f"({x['market']})" if x["market"] else "")
        언제 = "아직 복구 안 됨" if x["closed"] is None else f"복구 {_분(x['minutes'])}"
        # "실패·중단"(25.2295) — 정리된 줄(강제 종료·한도로 끝맺지 못함)도 세므로 "실패" 만으로는 틀린 말이었다
        # "N일에 걸침" 은 달력 폭으로 읽혔다(금·월 이틀 실패가 "2일에 걸침 · 복구 4.0일") — 센 것은 실패·중단 줄이 선
        # **날의 수**다(25.2298, 25.2295 교차검증 [권고])
        걸침 = f" · 실패·중단한 날 {x['days']}일" if (x.get("days") or 0) >= SPAN_SHOW_DAYS else ""
        코드 = {True: " · 같은 코드로 복구", False: " · 코드가 바뀐 뒤 복구"}.get(x.get("same_code"), "")
        앞 = f"  {어디}: {_언제(x['opened'])} 부터 실패·중단 {x['failures']}번{걸침}"
        줄.append(f"{앞} · {언제}{코드} · {x['cause']}")
    # 머리 수 − 실제로 적은 줄 수(25.2290) — 닫혔지만 복구 시간을 못 잰 사고는 목록에 없어도 여기서 센다
    if len(found) > len(적을것):
        줄.append(f"  … 그 밖 {len(found) - len(적을것)}건")
    return 줄


#: 실행 기록 밖의 사고 두 갈래(25.2300, docs/health.md 7.3) — 실행 줄만으로는 안 보인다: ① 무응답(기대 시각까지 성공
#: 기록이 없어 웹 감시가 알린 날 — 아예 안 돈 실행은 실패 줄을 남기지 않는다) ② 검산 틀린 날(가상모의투자 장부의 평가가
#: 체결 합과 안 맞은 계좌·날). 검산은 관문의 무사고 칸(`desk.gate` incidents)과 같은 재료·같은 날 단위다.
#: 무응답은 웹 감시의 알림이라 관문이 세는 "성공 없는 거래일"(`batch_runs` 를 기대 거래일과 대조)과 다를 수
#: 있다 — 늦게라도 성공한 날은 관문에서 사고가 아니다(25.2304 정정). 줄에 싣는 것은 건수와 작업·계좌 이름뿐
#: (금액·종목 없음)
OTHER_TOP_N = 5


def other_lines(missing: list[tuple[str, str, str]], recon_bad: list[tuple[str, str]]) -> list[str]:
    """`missing` = [(작업, 시장, 현지 날짜)](`health_alerts` 의 missing), `recon_bad` = [(계좌, 날짜)](`recon_ok = 0`).

    둘 다 비면 한 줄("없음"). 같은 값이 겹쳐 들어와도 한 번으로 센다"""
    무 = sorted(set(missing), key=lambda x: (x[2], x[0], x[1]))
    검 = sorted(set(recon_bad), key=lambda x: (x[1], x[0]))
    if not 무 and not 검:
        return ["무응답·검산 틀린 날: 없음"]
    줄: list[str] = []
    if 무:
        보임 = " · ".join(f"{j}({m}) {d[5:]}" for j, m, d in 무[:OTHER_TOP_N])
        꼬리 = f" … 그 밖 {len(무) - OTHER_TOP_N}건" if len(무) > OTHER_TOP_N else ""
        줄.append(f"무응답(기대 시각까지 성공 없음): {len(무)}건 — {보임}{꼬리}")
    if 검:
        날 = sorted({d for _a, d in 검})
        계좌 = ", ".join(sorted({a for a, _d in 검}))
        꼬리 = f" … 그 밖 {len(날) - OTHER_TOP_N}일" if len(날) > OTHER_TOP_N else ""
        줄.append(f"검산 틀린 날: {len(날)}일 · 계좌 {계좌} · {', '.join(d[5:] for d in 날[:OTHER_TOP_N])}{꼬리}")
    return 줄
