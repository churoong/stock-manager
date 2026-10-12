"""전환사채 잠재 물량 — 사실 줄 (docs/analysis.md 55장, docs/factors.md 12.20 후보 3, docs/infra.md 25.1192).

**점수·결론·IC·알림에 넣지 않는다.** 18회차 검증이 붙인 조건 그대로다.

- 지난 `WINDOW_DAYS` 안에 결정했고 **전환청구 기간이 아직 남은** 것만 말한다
- 날짜는 청구 **시작일**만 적는다(±90일 같은 창으로 "곧 풀린다" 고 말하지 않는다)
- 값은 **발행 결정 당시 기준**이다 — 그 뒤 전환가 조정(리픽싱)·조기상환·전환·철회는 모른다고 적는다
- 현재가와 견주지 않는다 — 분할·병합을 보정하지 못하면 거짓 비교가 된다
- 사건이 없으면 아무 말도 하지 않는다("없음" 은 덮은 기간 안에서만 참이다)

순수 함수만. 네트워크·DB 는 `jobs/disclosure_reaction` 과 `jobs/verdicts` 가 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: B 목록 제목에서 고르는 낱말. 정정 공시는 고르지 않는다 — 결정 당시 값을 말하는 줄이라 첫 공시면 된다
#: `[확인필요: 정정 반영]`
TITLE_WORD = "전환사채권발행결정"
#: 결정일이 이만큼 안이어야 한다
WINDOW_DAYS = 365
#: 한 종목 줄에 나열하는 최대 건수 — 넘으면 "외 N건"
MAX_LISTED = 3


def is_cb_title(title: str) -> bool:
    t = (title or "").replace(" ", "")
    return TITLE_WORD in t and "정정" not in t


def num(value: object) -> float | None:
    """"10,000,000,000"·"5.23"·"-"·"" → 수. 수가 아니면 None."""
    s = str(value if value is not None else "").replace(",", "").strip()
    if not s or s in {"-", "–"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def kdate(value: object) -> str | None:
    """"2027년 11월 07일"·"2027-11-07"·"20271107" → "2027-11-07". 못 읽으면 None."""
    s = str(value or "")
    m = re.search(r"(\d{4})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        return None


@dataclass(frozen=True)
class CbIssue:
    face_amount: float | None
    conv_price: float | None
    conv_shares: float | None
    shares_pct: float | None
    claim_start: str | None
    claim_end: str | None
    refix_floor: float | None


def parse_row(rows: list[dict], receipt_no: str) -> CbIssue | None:
    """`cvbdIsDecsn` 응답 행들 → 그 접수번호의 값. 그 접수번호 행이 없으면 None(한 번 부르면 여러 결정이 올 수 있다)."""
    for r in rows:
        if str(r.get("rcept_no") or "").strip() == receipt_no:
            return CbIssue(
                face_amount=num(r.get("bd_fta")),
                conv_price=num(r.get("cv_prc")),
                conv_shares=num(r.get("cvisstk_cnt")),
                shares_pct=num(r.get("cvisstk_tisstk_vs")),
                claim_start=kdate(r.get("cvrqpd_bgd")),
                claim_end=kdate(r.get("cvrqpd_edd")),
                refix_floor=num(r.get("act_mktprcfl_cvprc_lwtrsprc")),
            )
    return None


def live(events: list[dict], today: date) -> list[dict]:
    """줄에 실을 것 — 결정이 `WINDOW_DAYS` 안이고 청구 종료일이 오늘 이후. 종료일을 모르면 싣지 않는다."""
    since = date.fromordinal(today.toordinal() - WINDOW_DAYS).isoformat()
    return sorted(
        (e for e in events
         if e.get("status") == "ok" and str(e["decided_at"]) >= since and e.get("claim_end")
         and str(e["claim_end"]) >= today.isoformat()),
        key=lambda e: str(e["decided_at"]), reverse=True,
    )  # fmt: skip


def _원(v: float | None) -> str:
    if v is None:
        return "?"
    if v >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    return f"{v:,.0f}원"


def line(events: list[dict], today: date) -> str | None:
    """한 종목의 발행결정들 → 사실 한 줄. 실을 것이 없으면 None."""
    xs = live(events, today)
    if not xs:
        return None
    조각 = []
    for e in xs[:MAX_LISTED]:
        말 = f"{e['decided_at']} 결정 권면 {_원(e.get('face_amount'))}"
        if e.get("conv_price"):
            말 += f" · 전환가 {_원(e['conv_price'])}"
            if e.get("refix_floor"):
                말 += f"(조정 하한 {_원(e['refix_floor'])})"
        if e.get("shares_pct") is not None:
            말 += f" · 발행주식의 {float(e['shares_pct']):.1f}%"
        if e.get("claim_start"):
            말 += f" · 청구 시작 {e['claim_start']}"
        조각.append(말)
    꼬리 = f" 외 {len(xs) - MAX_LISTED}건" if len(xs) > MAX_LISTED else ""
    합 = sum(float(e["shares_pct"]) for e in xs if e.get("shares_pct") is not None)
    합말 = f", 합 발행주식의 {합:.1f}%" if len(xs) > 1 and 합 > 0 else ""
    return (
        f"전환사채 잠재 물량 {len(xs)}건{합말}: {' / '.join(조각)}{꼬리}"
        " — 발행 결정 당시 기준(그 뒤 전환가 조정·상환·전환·철회는 모름), 점수·결론에 넣지 않음"
    )
