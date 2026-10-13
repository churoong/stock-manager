"""매수 근거가 아직 참인가 — 보유 사실 줄.

(docs/analysis.md 56장, docs/factors.md 12.21 19회차 A4, docs/infra.md 25.1312)

중기·장기 신호로 산 보유 종목에서, **산 날의 신호가 통과시킨 가격 밖 근거**(성장·퀄리티 점수, 매출·영업이익 증가)를
오늘 판정표(`signal_checks`)의 같은 이름표와 견준다. 결론·매도 플래그·알림에는 넣지 않는다 — 종목 분석 상세의 사실 줄만.

- 값(밸류·밴드)은 견주지 않는다: 가격이 오른 것은 매수 논리가 깨진 것이 아니라 목표다
- 단기는 견주지 않는다: 기술적 조건은 날마다 뒤집히고 시간 손절이 따로 있다
- **"모름" 을 "깨짐" 으로 적지 않는다**: 오늘 판정표에 그 이름표가 없거나 "값 없음" 이면, 사업보고서 신선도
  탈락이면(성장률이 비었다), 점수 이름표인데 산 날과 오늘의 계산 판이 다르면 "모름"
- 문턱·형용사를 더하지 않는다 — 통과·탈락은 신호 규칙(`services/signals.py`)이 낸 것을 그대로 옮긴다
"""

from __future__ import annotations

from typing import Any

#: 견주는 이름표 — 가격을 보지 않는 기준만 (검증 A·B 조건)
COMPARED: dict[str, tuple[str, ...]] = {
    "mid": ("성장 점수", "퀄리티 점수", "매출 증가", "영업이익 증가"),
    "long": ("퀄리티 점수",),
}
#: 점수 기준 — 계산 판이 같을 때만 통과→탈락을 적는다 (검증 B 조건 ②)
SCORE_LABELS = frozenset({"성장 점수", "퀄리티 점수"})
#: 성장률 기준 — 사업보고서가 묵어 성장률이 비면 "모름" (검증 A 조건 ③)
GROWTH_LABELS = frozenset({"매출 증가", "영업이익 증가"})
FRESHNESS_LABEL = "사업보고서 신선도"
HORIZON_KO = {"mid": "중기", "long": "장기"}


def compare(
    horizon: str,
    then_criteria: list[dict[str, Any]],
    then_version: int | None,
    today_checks: list[dict[str, Any]] | None,
    today_version: int | None,
) -> dict[str, list[tuple[str, str]]] | None:
    """{"kept": [(이름표, 오늘 값)], "broken": [...], "unknown": [(이름표, 까닭)]}. 견줄 근거가 없으면 None."""
    labels = [str(c.get("label")) for c in then_criteria if str(c.get("label")) in COMPARED.get(horizon, ())]
    if not labels:
        return None
    out: dict[str, list[tuple[str, str]]] = {"kept": [], "broken": [], "unknown": []}
    if today_checks is None:
        out["unknown"] = [(lb, "오늘 판정표 없음(유니버스 밖이거나 계산 전)") for lb in labels]
        return out
    오늘 = {str(r.get("label")): r for r in today_checks}
    묵음 = FRESHNESS_LABEL in 오늘 and 오늘[FRESHNESS_LABEL].get("passed") is False
    for lb in labels:
        r = 오늘.get(lb)
        if lb in GROWTH_LABELS and 묵음:
            out["unknown"].append((lb, "사업보고서가 묵어 성장률을 내지 않음"))
        elif r is None or r.get("display") == "값 없음":
            out["unknown"].append((lb, "오늘 값 없음"))
        elif lb in SCORE_LABELS and then_version != today_version:
            out["unknown"].append((lb, f"계산 판이 달라 견주지 않음(그때 {then_version}, 오늘 {today_version})"))
        elif r.get("passed") is True:
            out["kept"].append((lb, str(r.get("display") or "")))
        else:
            out["broken"].append((lb, str(r.get("display") or "")))
    return out


def line(horizon: str, bought_on: str, today_date: str, result: dict[str, list[tuple[str, str]]]) -> str:
    """사실 줄 한 줄. 숫자는 판정표에 적힌 글자를 그대로 옮긴다."""
    n = sum(len(v) for v in result.values())
    글 = (f"{HORIZON_KO.get(horizon, horizon)} 매수 근거 {n}개 중 {len(result['kept'])}개 유지"
          f" (산 날 신호 {bought_on} → 오늘 판정표 {today_date})")  # fmt: skip
    if result["broken"]:
        글 += " · 지금 탈락: " + ", ".join(f"{lb}({v})" if v else lb for lb, v in result["broken"])
    if result["unknown"]:
        글 += " · 모름: " + ", ".join(f"{lb}({까닭})" for lb, 까닭 in result["unknown"])
    return 글
