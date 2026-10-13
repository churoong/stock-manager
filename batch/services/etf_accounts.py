"""장기 적립 ETF 의 계좌별 가능 여부와 우선순위 (docs/etf.md 11.1, docs/infra.md 25.966).

같은 지수라도 어느 계좌에서 사느냐에 따라 살 수 있는 상품과 세금이 다르다. 통과한 ETF 마다 연금저축·IRP·일반계좌
세 칸을 정해 판정 행의 `rationale_data.accounts` 에 적는다 — 웹은 그대로 읽어 보이기만 한다(CLAUDE.md "웹앱은
계산하지 않는다").

순위는 **과세 구조**로 정한다 — 국내 주식 ETF 는 일반계좌에서도 매매차익이 비과세라 연금에 넣는 이득이 분배금뿐이고,
국내 상장 해외 ETF 는 일반계좌에서 매매차익까지 과세되며, 미국 상장 ETF 는 연금 계좌에서 살 수 없다. 세율 값은
settings `taxes` 를 그대로 인용만 한다(비어 있으면 "미입력"). 세율 자체는 전부 `[확인필요]` 다(CLAUDE.md 세율 규칙).

위성(배당·업종·테마)은 국내·해외 지수인지 이름으로 확실히 가르지 못해(국내 테마 지수 이름이 영문인 것이 많다)
**가능 여부만** 적고 순위는 매기지 않는다.
"""

from __future__ import annotations

from typing import Any

from batch.services.tax_sim import FIN_INCOME_COMPREHENSIVE_KRW, US_CGT_DEDUCTION_KRW

#: 계좌 순서 — 화면 탭 순서와 같다
ACCOUNTS = ("pension", "irp", "taxable")
ACCOUNT_LABELS = {"pension": "연금저축", "irp": "IRP", "taxable": "일반계좌"}

#: (나라, 핵심 묶음) → 계좌별 순위. 1 이 먼저. docs/etf.md 11.1 표와 같다
_PRIORITY: dict[tuple[str, str], dict[str, int]] = {
    ("KR", "해외 주식"): {"pension": 1, "irp": 1, "taxable": 3},
    ("KR", "채권"): {"pension": 2, "irp": 2, "taxable": 3},
    ("KR", "국내 주식"): {"pension": 3, "irp": 3, "taxable": 1},
}
#: 미국 상장 핵심 ETF 는 일반계좌에서만 — 국내 주식 ETF(비과세) 다음
US_TAXABLE_PRIORITY = 2

#: IRP 위험자산 한도(%) — 주식형 합계가 이것을 넘지 못한다 `[확인필요: 제도 세부]`
IRP_RISK_LIMIT_PCT = 70

_PENSION_US = "연금저축·IRP 는 국내 상장 ETF 만 살 수 있습니다"


#: 사유가 세율 숫자 대신 쓰는 말 (docs/infra.md 25.1301, 세후 적립 감사 #1). 사유는 월 1회 ETF 잡이 써서 세율을
#: 인용하면 설정을 고쳐도 한 달 동안 옛 세율을 말했다 — 같은 탭의 세후 적립 카드는 설정 저장마다 다시 계산돼 둘이
#: 다른 세율을 보였다. 지금 세율은 그 카드가 보여 준다
_세율_글 = "세율은 설정값 — 위 세후 적립 카드에 지금 값"


def _reason(country: str, bucket: str, account: str, taxes: dict | None) -> str:
    """순위의 까닭 한 줄. 세율은 settings 값을 인용만 한다."""
    if country == "US":
        # 예전 근거("국내 상장 해외 ETF 는 매매차익 과세라 그 앞")는 미국 상장도 22% 과세임을 빠뜨렸다
        # (25.1240, 교차검증). 앞서는 까닭은 세율이 아니라 **구조**다 — 분리과세·해마다 공제·손익통산.
        # 단점(환전·상속세)도 함께 적는다
        return (
            f"미국 상장 — 양도차익 분리과세({_세율_글},"
            f" 금융소득종합과세에 합산 안 됨)·해마다 {US_CGT_DEDUCTION_KRW // 10_000}만원 기본공제(나눠 팔기)"
            "·다른 해외주식과 손익통산."
            " 단 환전 비용과 미국 상속세 대상 자산(비거주자 공제 6만 달러 `[확인필요]`) — 국내 주식 ETF 다음"
        )
    if bucket == "국내 주식":
        if account == "taxable":
            return "국내 주식 ETF 는 일반계좌에서도 매매차익이 비과세 — 연금 한도는 다른 상품에 쓰는 편이 낫습니다"
        return "국내 주식 ETF 는 일반계좌에서도 매매차익이 비과세라 연금에 넣는 이득이 분배금뿐입니다"
    if account == "taxable":
        return (
            f"국내 상장 {bucket} ETF 는 일반계좌에서 매매차익·분배금 모두 배당소득세"
            f"({_세율_글}) — 한 해에 몰아 팔면 금융소득종합과세({FIN_INCOME_COMPREHENSIVE_KRW // 10_000:,}만원 초과)"
            "될 수 있어 여러 해에 나눠 팝니다. 연금 계좌가 낫습니다"
        )
    # 해외 주식(1순위)과 채권(2순위)이 같은 문장으로 "가장 크다" 를 말해 2순위 카드가 1순위로 읽혔다 (25.1239)
    if bucket == "채권":
        return (
            "국내 상장 채권 ETF 는 일반계좌라면 이자·매매차익이 과세"
            " — 연금 계좌의 과세이연 이득이 해외 주식 다음으로 큽니다"
        )
    return f"국내 상장 {bucket} ETF 는 일반계좌라면 매매차익까지 과세 — 연금 계좌의 과세이연 이득이 가장 큽니다"


def accounts_for(country: str, bucket: str | None, *, satellite: bool, taxes: dict | None = None) -> dict[str, Any]:
    """계좌 세 칸 — {pension|irp|taxable: {eligible, priority, reason, note}}. 통과한 ETF 에만 부른다."""
    out: dict[str, Any] = {}
    for acc in ACCOUNTS:
        eligible = country == "KR" or acc == "taxable"
        cell: dict[str, Any] = {"eligible": eligible, "priority": None, "reason": None, "note": None}
        if not eligible:
            cell["reason"] = _PENSION_US
        elif satellite:
            cell["reason"] = "위성 — 계좌 우선순위는 핵심 ETF 만 매깁니다"
        else:
            priority = (
                US_TAXABLE_PRIORITY if country == "US" else _PRIORITY.get((country, bucket or ""), {}).get(acc)
            )
            cell["priority"] = priority
            cell["reason"] = _reason(country, bucket or "", acc, taxes) if priority is not None else None
        if acc == "irp" and eligible:
            주식형 = satellite or bucket in ("국내 주식", "해외 주식")
            cell["note"] = (
                f"위험자산 — IRP 에서 주식형 합계 {IRP_RISK_LIMIT_PCT}% 이하"
                if 주식형
                else f"안전자산 — IRP 의 나머지 {100 - IRP_RISK_LIMIT_PCT}% 칸에 [확인필요: 채권형 분류]"
            )
        out[acc] = cell
    return out
