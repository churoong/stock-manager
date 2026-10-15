"""모의 투자사 소급 재생 — 저장된 과거 신호에 회사별 투자위원회를 돌려 가결률·부서 입장 분포를 본다.

docs/company.md 2·6장, docs/infra.md 25.1464. 판 2 헌장(`services/company.CHARTERS`)의 가중·문턱은 추정이다.
판단식이 결정적이라 과거 신호 날짜마다 같은 회의를 다시 열 수 있다 — **운영에 헌장을 적용하기 전에**
"가결이 0 인 회사" · "늘 가결인 회사" · "늘 자료 없음인 부서" 를 먼저 걸러 낸다. 쓰지 않는다(읽기만).
결과는 운영 출력(`scripts/ops_tee.py` → 비공개 운영 저장소 이슈)으로만 낸다 — 종목 이름을 찍지 않고 수만.

    python scripts/company_replay.py --market KR --days 60 --every 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.services import company as co  # noqa: E402


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
        out.append(f"{aid} {co.CHARTERS.get(aid, {}).get('name', '')}: 회의 {x['meetings']} · 안건 {x['agenda']}"
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
    a = p.parse_args()

    from batch.core.client import TursoClient

    # 닫아야 읽기 수가 api_usage 에 남는다 (25.1465 검증 권장)
    with TursoClient() as client:
        회의, 본, 성적 = replay(client, a.market, a.days, a.every)
        끝 = [f"(신호 날짜 {len(본)}개: {min(본, default='-')} ~ {max(본, default='-')}."
             " 신호는 지금 상장 중인 종목만 읽힌다 — 그 사이 폐지된 종목은 빠진다)",
             f"읽기 행: {getattr(getattr(client, 'reads', None), 'total', '모름')}"]  # fmt: skip
    for 줄 in lines(a.market, tally(회의)) + score_lines(co.scorecard(성적)) + 끝:
        print(줄)
    return 0


def replay(client: Any, market: str, days: int, every: int) -> tuple[list[Any], set[str], list[dict[str, Any]]]:
    """며칠 간격의 신호 날짜마다 네 회사의 회의를 연다. 점수 부서 백분위는 그날 신호 종목 전체 안에서(25.1465).

    (회의들, 신호 날짜들, 부서 성적표 행) — 성적표 행은 안건마다 그 뒤 20 거래일 지수 대비 초과
    (25.1470, 최근 신호는 빠진다)
    """
    from batch.core import calendar as cal
    from batch.jobs import company as job
    from batch.jobs import daily

    오늘 = cal.local_today(market)
    본: set[str] = set()
    회의: list[tuple[str, str, list[co.Item], list[co.Item]]] = []
    성적: list[dict[str, Any]] = []
    크기 = max(int(ch["agenda"]) for ch in co.CHARTERS.values())
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
        뒤성과 = job.forward_excess(client, market, 안건)
        for aid, ch in co.CHARTERS.items():
            items, approved = co.meet(자료[: int(ch["agenda"])], ch, 모집단)
            회의.append((aid, as_of, items, approved))
            성적 += co.records_from(as_of, items, ch, 뒤성과)
    return 회의, 본, 성적


if __name__ == "__main__":
    raise SystemExit(main())
