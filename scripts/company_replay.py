"""모의 투자사 소급 재생 — 저장된 과거 신호에 회사별 투자위원회를 돌려 가결률·부서 입장 분포를 본다.

docs/company.md 2·6장, docs/infra.md 25.1464. 판 2 헌장(`services/company.CHARTERS`)의 가중·문턱은 추정이다.
판단식이 결정적이라 과거 신호 날짜마다 같은 회의를 다시 열 수 있다 — **운영에 헌장을 적용하기 전에**
"가결이 0 인 회사" · "늘 가결인 회사" · "늘 자료 없음인 부서" 를 먼저 걸러 낸다. 쓰지 않는다(읽기만).
결과는 운영 출력(`scripts/ops_tee.py` → 비공개 운영 저장소 이슈)으로만 낸다 — 종목 이름을 찍지 않고 수만.

    python scripts/company_replay.py --market KR --days 60 --every 5
    python scripts/company_replay.py --market KR --days 70 --every 5 --masters --no-scorecard   # 거장 거름 (12.6)

`--masters` 는 거장 계좌(docs/company.md 12장, `services/masters.CHARTERS`)를 A·C·D 와 함께 열고 12.6 걸림
(c′)(a)(b)(d)(e)를 계좌마다 판정해 찍는다. `--no-scorecard` 는 뒤 성과(`forward_excess`)를 읽지 않는다(수익 봉인).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.services import company as co  # noqa: E402
from batch.services import masters as ms  # noqa: E402

#: 12.6 거름에 같이 여는 기존 회사 — 중복(d)의 비교 대상 (docs/company.md 12.6)
MASTER_PEERS: tuple[str, ...] = ("A", "C", "D")
#: (d) 중복 비교 대상과 문턱 — 사전 등록 값
OVERLAP_RULE: dict[str, tuple[tuple[str, ...], float]] = {
    "ZM": (("A", "D"), 0.8), "ZN": (("A", "C", "D", "ZM"), 0.5), "ZL": (("A", "C", "D", "ZM"), 0.5),
}  # fmt: skip
NO_DATA_MAX = 0.5  # (c′) 거장 부서 하나라도 안건의 50% 넘게 자료 없음
ZERO_DAYS_MAX = 0.8  # (a) 가결 0 인 회의 ≥ 80%
PASS_RATE_MAX = 0.9  # (b) 가결률 ≥ 90%
OVER_TOP_N_MAX = 0.2  # (e) ZN·ZL 가결 수 > top_n 인 회의 ≥ 20%
#: (b) 에 걸려도 열지 않음이 아니라 threshold 를 한 번 고칠 계좌 — {계좌: 고칠 threshold} (12.6)
RETUNE_B: dict[str, float] = {"ZL": 2.6}
RETUNE_MARK = " → 고침 대상(threshold 2.6 으로 한 번 다시 재생)"


def tally(meetings: list[tuple[str, str, list[co.Item], list[co.Item]]]) -> dict[str, Any]:
    """[(회사, 날짜, 안건 결과, 가결)] → 회사별 가결률·부결 까닭·부서 입장 분포. 순수 함수."""
    회사: dict[str, dict[str, Any]] = {}
    for aid, _day, items, approved in meetings:
        t = 회사.setdefault(aid, {"meetings": 0, "agenda": 0, "passed": 0, "approved": 0, "zero_days": 0,
                                 "issues": 0, "vetoes": 0, "reasons": {}, "depts": {}})  # fmt: skip
        t["meetings"] += 1
        t["agenda"] += len(items)
        t["passed"] += sum(i.passed for i in items)
        t["approved"] += len(approved)
        t["zero_days"] += not approved
        t["issues"] += sum(i.issue for i in items)
        t["vetoes"] += sum(bool(i.vetoed_by) for i in items)
        for i in items:
            if not i.passed:
                k = i.reason.split(":")[1].strip().split(" ")[0] if ":" in i.reason else i.reason
                t["reasons"][k] = t["reasons"].get(k, 0) + 1
            for dept, o in i.opinions.items():
                d = t["depts"].setdefault(dept, {"for": 0, "neutral": 0, "against": 0, "no_data": 0})
                d["no_data" if o.note and o.note.startswith("자료 없음") else
                  "for" if o.stance > 0 else "against" if o.stance < 0 else "neutral"] += 1  # fmt: skip
    return 회사


Meetings = list[tuple[str, str, list[co.Item], list[co.Item]]]


def overlap(meetings: Meetings, z: str, peers: tuple[str, ...]) -> float | None:
    """(d) 집행 종목의 같은 날 다른 계좌 집행 포함률 |Z∩X| ÷ |Z| 의 날별 최댓값 평균 (Z 가 빈 날 뺌). 순수 함수"""
    집행: dict[tuple[str, str], set[int]] = {(aid, day): {i.stock_id for i in ap} for aid, day, _it, ap in meetings}
    값 = [max((len(zs & 집행.get((x, day), set())) / len(zs) for x in peers), default=0.0)
         for (aid, day), zs in sorted(집행.items()) if aid == z and zs]  # fmt: skip
    return sum(값) / len(값) if 값 else None


def agreement(meetings: list[tuple[str, str, list[co.Item], list[co.Item]]], z: str) -> dict[tuple[str, str], float]:
    """거장 부서끼리 입장 일치율(둘 다 자료가 있는 안건에서 찬·중·반이 같은 비율) — 기록만 (12.6)"""
    셈: dict[tuple[str, str], list[int]] = {}
    for aid, _day, items, _ap in meetings:
        if aid != z:
            continue
        for i in items:
            있음 = {k: o.stance for k, o in i.opinions.items()
                  if k in co.MASTER_DEPARTMENTS and not (o.note or "").startswith("자료 없음")}  # fmt: skip
            for a in sorted(있음):
                for b in sorted(있음):
                    if a < b:
                        c = 셈.setdefault((a, b), [0, 0])
                        c[0] += 있음[a] == 있음[b]
                        c[1] += 1
    return {k: v[0] / v[1] for k, v in 셈.items() if v[1]}


def master_verdicts(
    meetings: list[tuple[str, str, list[co.Item], list[co.Item]]], charters: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """12.6 걸림을 계좌마다 위에서부터 — {계좌: [걸린 까닭…]} (빈 목록이면 통과). 순수 함수"""
    t = tally(meetings)
    out: dict[str, list[str]] = {}
    for z in ms.CHARTERS:
        x, ch, 걸림 = t.get(z), charters[z], []
        if not x or not x["meetings"]:
            out[z] = ["(c′) 회의 없음"]
            continue
        for dept in sorted(set(ch["weights"]) & set(co.MASTER_DEPARTMENTS)):
            d = x["depts"].get(dept, {})
            if d.get("no_data", 0) / max(sum(d.values()), 1) > NO_DATA_MAX:
                걸림.append(f"(c′) 자료 부족: {dept}")
        if x["zero_days"] / x["meetings"] >= ZERO_DAYS_MAX:
            걸림.append("(a) 기준 통과 종목 부족")
        if x["passed"] / max(x["agenda"], 1) >= PASS_RATE_MAX:
            # ZL 만 열지 않음이 아니라 threshold 2.6 으로 한 번 고쳐 다시 돌릴 대상 (12.6 (b))
            걸림.append(f"(b) 늘 가결{RETUNE_MARK if z in RETUNE_B else ''}")
        peers, 문턱 = OVERLAP_RULE[z]
        겹침 = overlap(meetings, z, peers)
        if 겹침 is not None and 겹침 >= 문턱:
            걸림.append(f"(d) 중복 {겹침:.2f}")
        if z in ("ZN", "ZL"):
            넘침 = [sum(i.passed for i in it) > int(ch["top_n"]) for aid, _d, it, _a in meetings if aid == z]
            if 넘침 and sum(넘침) / len(넘침) >= OVER_TOP_N_MAX:
                걸림.append("(e) 동률 고르기")
        out[z] = 걸림
    return out


def master_lines(
    meetings: list[tuple[str, str, list[co.Item], list[co.Item]]], charters: dict[str, dict[str, Any]],
) -> list[str]:
    """12.6 거름 결과 줄 — 계좌마다 판정·수치(종목 이름 없음)"""
    판 = master_verdicts(meetings, charters)
    out = ["[거장 계좌 거름 — docs/company.md 12.6, 한 시장이라도 걸리면 열지 않는다(ZL 의 (b) 만 고침 대상)]"]
    for z, 걸림 in 판.items():
        peers, _ = OVERLAP_RULE[z]
        겹침 = overlap(meetings, z, peers)
        넘침 = [sum(i.passed for i in it) > int(charters[z]["top_n"]) for aid, _d, it, _a in meetings if aid == z]
        out.append(f"{z} {charters[z]['name']}: " + ("통과" if not 걸림 else "걸림 — " + " · ".join(걸림))
                   + f" | 중복 {'—' if 겹침 is None else f'{겹침:.2f}'}"
                   + f" · top_n 넘는 회의 {sum(넘침)}/{len(넘침)}")  # fmt: skip
        일치 = agreement(meetings, z)
        if 일치:
            out.append("  부서 일치율(기록만): "
                       + ", ".join(f"{a}~{b} {v * 100:.0f}%" for (a, b), v in sorted(일치.items())))  # fmt: skip
    return out


def score_lines(card: dict[tuple[str, str], dict[str, Any]]) -> list[str]:
    """부서 성적표 줄 — 가름(찬성 평균 − 반대 평균, 20 거래일 지수 대비 %p)이 클수록 잘 가린다 (docs/company.md 6장)."""
    out = [f"[부서 성적표 — 20 거래일 지수 대비, 에피소드 {co.SCORECARD_EPISODE_DAYS}일,"
           f" 표본 {co.SCORECARD_MIN_SAMPLE} 미만은 (참고)]"]

    def p(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:+.1f}%"

    for (dept, param), x in sorted(card.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        이름 = co.DEPT_LABEL.get(dept, dept) + (f" {param}" if param else "")
        out.append(f"  {이름}: 찬성 {x['n_for']}건 평균 {p(x['avg_for'])}"
                   f" · 반대 {x['n_against']}건 평균 {p(x['avg_against'])}"
                   f" · 가름 {p(x['spread'])}" + ("" if x["enough"] else " (참고)"))  # fmt: skip
    return out


def lines(market: str, t: dict[str, Any]) -> list[str]:
    """사람이 읽을 줄 — 수만(종목 이름 없음)."""
    out = [f"[모의 투자사 소급 재생 · {market}]"]
    for aid in sorted(t):
        x = t[aid]
        n = max(x["agenda"], 1)
        이름 = {**co.CHARTERS, **ms.CHARTERS}.get(aid, {}).get("name", "")
        out.append(f"{aid} {이름}: 회의 {x['meetings']} · 안건 {x['agenda']}"
                   f" · 가결 {x['passed']} ({x['passed'] / n * 100:.0f}%) · 집행 {x['approved']}"
                   f" · 가결 0 인 날 {x['zero_days']} · 쟁점 {x['issues']} · 거부 {x['vetoes']}")  # fmt: skip
        까닭 = sorted(x["reasons"].items(), key=lambda kv: -kv[1])
        out.append("  부결 까닭: " + ", ".join(f"{k} {v}" for k, v in 까닭))
        for dept, d in sorted(x["depts"].items()):
            합 = max(sum(d.values()), 1)
            out.append(f"  {co.DEPT_LABEL[dept]}: 찬성 {d['for'] / 합 * 100:.0f}%"
                       f" · 중립 {d['neutral'] / 합 * 100:.0f}%"
                       f" · 반대 {d['against'] / 합 * 100:.0f}%"
                       f" · 자료 없음 {d['no_data'] / 합 * 100:.0f}%")  # fmt: skip
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["KR", "US"], default="KR")
    p.add_argument("--days", type=int, default=60, help="오늘부터 거슬러 갈 달력일")
    p.add_argument("--every", type=int, default=5, help="며칠마다 한 번 열까(읽기를 줄인다)")
    p.add_argument("--masters", action="store_true", help="거장 계좌(12장)를 A·C·D 와 함께 열고 12.6 거름을 찍는다")
    p.add_argument("--no-scorecard", action="store_true", help="뒤 성과를 읽지 않는다(수익 봉인, 12.6)")
    a = p.parse_args()

    from batch.core.client import TursoClient

    # 닫아야 읽기 수가 api_usage 에 남는다 (25.1465 검증 권장)
    with TursoClient() as client:
        charters = _charters(a.masters)
        회의, 본, 성적 = replay(client, a.market, a.days, a.every, charters, scorecard=not a.no_scorecard)
        끝 = [f"(신호 날짜 {len(본)}개: {min(본, default='-')} ~ {max(본, default='-')}."
             " 신호는 지금 상장 중인 종목만 읽힌다 — 그 사이 폐지된 종목은 빠진다)",
             f"읽기 행: {getattr(getattr(client, 'reads', None), 'total', '모름')}"]  # fmt: skip
    줄들 = lines(a.market, tally(회의))
    줄들 += master_lines(회의, charters) if a.masters else []
    줄들 += [] if a.no_scorecard else score_lines(co.scorecard(성적))
    for 줄 in 줄들 + 끝:
        print(줄)
    return 0


def _charters(masters: bool) -> dict[str, dict[str, Any]]:
    """열 헌장 — 기본은 코드의 판 2 헌장, `--masters` 면 A·C·D 와 거장 계좌"""
    if not masters:
        return dict(co.CHARTERS)
    return {**{k: co.CHARTERS[k] for k in MASTER_PEERS}, **ms.CHARTERS}


def replay(
    client: Any, market: str, days: int, every: int, charters: dict[str, dict[str, Any]] | None = None,
    *, scorecard: bool = True,
) -> tuple[list[Any], set[str], list[dict[str, Any]]]:
    """며칠 간격의 신호 날짜마다 회사들의 회의를 연다. 점수 부서 백분위는 그날 신호 종목 전체 안에서(25.1465).

    (회의들, 신호 날짜들, 부서 성적표 행) — 성적표 행은 안건마다 그 뒤 20 거래일 지수 대비 초과
    (25.1470, 최근 신호는 빠진다)
    """
    from batch.core import calendar as cal
    from batch.jobs import company as job
    from batch.jobs import daily, paper

    오늘 = cal.local_today(market)
    본: set[str] = set()
    회의: list[tuple[str, str, list[co.Item], list[co.Item]]] = []
    성적: list[dict[str, Any]] = []
    charters = charters or dict(co.CHARTERS)
    크기 = max(int(ch["agenda"]) for ch in charters.values())
    거장 = any(paper.uses_masters(ch) for ch in charters.values())  # 운영(`_조사_준비`)과 같은 식
    날 = 오늘 - timedelta(days=days)
    while 날 <= 오늘:
        rows, as_of = daily.load_signal_rows(client, market, 날.isoformat())
        날 += timedelta(days=every)
        if not rows or not as_of or as_of in 본:
            continue
        본.add(as_of)
        모집단 = job.population_dossiers(client, rows, market, as_of)
        안건 = job.agenda_rows(rows, 크기)
        자료 = job.load_dossiers(client, 안건, market, as_of)
        if 거장:  # 운영(`jobs/paper._조사_준비`)과 같은 길 — 모집단·안건에 거장 자료를 붙인다
            사실 = job.master_facts(client, sorted({d.stock_id for d in [*모집단, *자료]}), market, as_of)
            모집단, 자료 = job.with_masters(모집단, 사실), job.with_masters(자료, 사실)
        뒤성과 = job.forward_excess(client, market, 안건) if scorecard else {}
        for aid, ch in charters.items():
            items, approved = co.meet(자료[: int(ch["agenda"])], ch, 모집단)
            회의.append((aid, as_of, items, approved))
            if scorecard:
                성적 += co.records_from(as_of, items, ch, 뒤성과)
    return 회의, 본, 성적


if __name__ == "__main__":
    raise SystemExit(main())
