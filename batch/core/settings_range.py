"""설정 값의 범위 — **배치 쪽 잣대** (docs/infra.md 25.170).

`web/lib/settings.ts` 머리말은 이렇게 적는다.

> 설정은 웹앱만 쓴다. 배치는 읽기만 한다. **따라서 검증은 여기 한 곳에 둔다.**

그 전제가 사실이 아니다. `scripts/restore_backup.py` 와 `scripts/move_user_data.py` 는
`settings` 행을 **검증 없이** 써 넣는다. 옛 백업에는 지금 범위를 벗어난 값이 들어 있을
수 있고, 되살리는 날 그대로 들어온다. 25.169 에서 가중치 하나가 종합 점수를 −40 으로
만드는 것을 봤다. **여기는 돈에 닿는 값들이다** — 권장 금액·목표가·손절선·수수료.

**정의처는 그래도 `settings.ts` 다.** 이 표는 그 스키마를 파이썬으로 옮긴 사본이고,
`tests/test_settings_range.py` 가 그 파일을 읽어 **한 칸씩 대 본다.** 새 숫자 칸이
생기면 여기 없다고 깨진다 — 사본이 조용히 낡는 것을 막는다.
"""

from __future__ import annotations

#: 잎 이름 → (최소, 최대). `settings.ts` 의 `z.number().min(a).max(b)` 를 그대로 옮긴다.
#: 이름은 중첩 안의 **잎**이다(`horizon_targets.short.target_pct` → `target_pct`).
#: 겹치는 잎 이름은 없다 — 테스트가 그것도 확인한다.
설정_범위: dict[str, tuple[float, float]] = {
    # 팩터·센티먼트 가중치 (`percent`)
    "value": (0, 100),
    "quality": (0, 100),
    "growth": (0, 100),
    "momentum": (0, 100),
    "risk": (0, 100),
    "sentiment_weight": (0, 100),
    # 기간별 목표·손절
    "target_pct": (0, 1000),
    "stop_pct": (-100, 0),
    # 금액과 비중
    "total_investable_amount": (0, float("inf")),
    "max_weight_per_stock": (0.1, 100),
    "max_weight_per_sector": (0.1, 100),
    "min_order_amount": (0, 100_000_000),
    # 추세 필터
    "bear_factor": (0, 1),
    # 수수료
    "kr_buy_pct": (0, 5),
    "kr_sell_pct": (0, 5),
    "us_buy_pct": (0, 5),
    "us_sell_pct": (0, 5),
    # 세금
    "kr_transaction_pct": (0, 10),
    "kr_dividend_pct": (0, 50),
    "us_dividend_pct": (0, 50),
    "us_capital_gains_pct": (0, 50),
    "pension_income_pct": (0, 20),
    "pension_credit_pct": (0, 30),
    # 무위험수익률
    "kr_pct": (0, 30),
    "us_pct": (0, 30),
    # 장중 알림 문턱
    "spike_pct": (0.1, 50),
    "volume_multiple": (1, 50),
    # 뉴스 대상 수 · 백필 연수
    "sentiment_target_top_n": (0, 500),
    "prices": (1, 30),
    "financials": (1, 30),
}


def 범위_안(이름: str, 값: object, 기본: float | None) -> tuple[float | None, str | None]:
    """(쓸 값, 경고). 범위 밖이거나 숫자가 아니면 `기본` 으로 되돌리고 왜인지 말한다.

    **되돌리는 쪽을 고른 이유** (25.169): 값 하나 때문에 그날 배치를 통째로 멈추면
    추천이 빈다. 설정 한 줄 값으로는 크다. 대신 조용히 넘어가지 않는다.

    `기본` 이 `None` 이면 "쓰지 않는다" 는 뜻이다 — 세율처럼 **모르면 안 쓰는** 값.
    """
    if 이름 not in 설정_범위:
        raise KeyError(f"{이름} 의 범위가 settings_range 에 없다 (settings.ts 와 대조한다)")
    최소, 최대 = 설정_범위[이름]

    if isinstance(값, bool) or not isinstance(값, (int, float)):
        if 값 is None:
            return 기본, None  # 안 넣은 값은 정상이다
        return 기본, f"설정 {이름} 이 숫자가 아닙니다({값!r}). {_기본표시(기본)}"

    숫자 = float(값)
    if 최소 <= 숫자 <= 최대:
        return 숫자, None
    return 기본, (
        f"설정 {이름} 이 {숫자:g} 로 {최소:g}~{_최대표시(최대)} 밖입니다."
        f" {_기본표시(기본)} — 설정 화면에서 고치세요"
    )


def 잎마다(stored: object, 잎들: tuple[str, ...]) -> tuple[dict[str, float | None], list[str]]:
    """중첩 설정(`fees`·`taxes`·`risk_free_manual`)의 잎을 한 번에 본다.

    **되돌릴 기본값이 없다.** 이 값들은 `settings.ts` 에서 `nullable()` 이고, 문서는
    "확인 전에는 null 로 두고 화면에 경고를 띄운다" 고 적는다. 그러니 범위 밖이면
    **모른다(None)** 로 둔다 — 0 으로 두면 "세금 없음"·"수수료 없음" 이 되어
    **실현손익이 좋은 쪽으로 틀린다.** 25.0 「모르는 것을 0 으로 적는다」의 반대편이다.
    """
    값들: dict[str, float | None] = {}
    경고: list[str] = []
    표 = stored if isinstance(stored, dict) else {}
    for 잎 in 잎들:
        값, 말 = 범위_안(잎, 표.get(잎), None)
        값들[잎] = 값
        if 말:
            경고.append(말)
    return 값들, 경고


#: 한 기간의 목표·손절은 **짝으로** 뜻을 갖는다. 둘 다 있어야 한다
목표_잎 = ("target_pct", "stop_pct")


def 기간별_목표(stored: object) -> tuple[dict[str, dict[str, float]] | None, list[str]]:
    """`horizon_targets` 를 검사해 **쓸 수 있는 기간만** 돌려준다 (docs/infra.md 25.180).

    한 짝(목표, 손절)이 함께 뜻을 갖는다 — 목표만 되돌리고 손절을 그대로 두면
    **손절이 목표 위에 서는** 짝이 생길 수 있다. 그래서 기간 단위로 통째로 버리고,
    버린 기간은 부르는 쪽이 기본값(`services/signals.DEFAULT_TARGETS`)을 쓴다.

    **잎이 하나 없어도 버린다** (25.180). 예전에는 `범위_안` 이 "안 넣은 값은 정상"
    이라며 `(None, None)` 을 돌려주는 것을 그대로 통과시키고 **원래 dict 을** 넘겼다.
    그러면 `{"short": {"target_pct": 10}}` 같은 반쪽 짝이 살아남아
    `target_and_stop()` 이 `KeyError: 'stop_pct'` 로 **신호 배치를 통째로 죽였다.**
    옛 백업을 되살리거나 손으로 고친 설정에서 나올 수 있는 모양이다(25.170 과 같은 길).

    **검사한 값으로 다시 짓는다.** 원래 dict 을 넘기면 검사와 쓰는 값이 갈라진다.
    """
    if not isinstance(stored, dict):
        return None, []

    좋은것: dict[str, dict[str, float]] = {}
    경고: list[str] = []
    for 기간, 짝 in stored.items():
        if not isinstance(짝, dict):
            경고.append(f"설정 horizon_targets.{기간} 이 표가 아닙니다. 기본값으로 냈습니다")
            continue
        값들: dict[str, float] = {}
        말들: list[str] = []
        for 잎 in 목표_잎:
            값, 말 = 범위_안(잎, 짝.get(잎), None)
            if 말:
                말들.append(f"{기간} {말}")
            elif 값 is None:
                말들.append(f"설정 horizon_targets.{기간}.{잎} 이 없습니다. 기본값으로 냈습니다")
            else:
                값들[잎] = 값
        # **목표는 0 보다 크고 손절은 0 보다 작아야 한다** (docs/infra.md 25.276) —
        # `settings.ts` 의 두 refine 과 같은 규칙.
        # 범위(`0~1000`, `-100~0`)는 끝을 포함해서, 되살린 백업의 `{target 0, stop 0}` 이 통과해 목표가·손절가가 둘 다
        # 매수 기준가와 같아졌다(모든 보유가 곧바로 손절·목표 "도달"). 웹은 그 짝을 저장하지 못한다
        if not 말들 and 값들.get("target_pct", 1) <= 0:
            말들.append(f"설정 horizon_targets.{기간}.target_pct 가 0 이하입니다. 기본값으로 냈습니다")
        if not 말들 and 값들.get("stop_pct", -1) >= 0:
            말들.append(f"설정 horizon_targets.{기간}.stop_pct 가 0 이상입니다. 기본값으로 냈습니다")
        if 말들:
            경고 += 말들
            continue
        좋은것[기간] = 값들
    return (좋은것 or None), 경고


def _최대표시(최대: float) -> str:
    return "무제한" if 최대 == float("inf") else f"{최대:g}"


def _기본표시(기본: float | None) -> str:
    return "쓰지 않았습니다" if 기본 is None else f"기본값 {기본:g} 로 냈습니다"


#: 센티먼트 가중치 상한. 웹 `checkWeightConsistency` 가 저장을 막는 값과 같다(50 초과면 뉴스가 재무보다 커진다)
SENTIMENT_WEIGHT_MAX = 50.0


def 센티먼트_가중치(값: float, 기본: float) -> tuple[float, str | None]:
    """웹이 저장을 막는 50 초과를 배치도 받지 않는다 (docs/infra.md 25.300).

    웹은 저장 때 막지만 배치는 잎 범위(0~100)만 봤다 — 옛 백업을 되살리면(25.170) 80 이 그대로 들어가
    종합 점수의 80% 가 뉴스 감성이 됐다. 다른 범위 밖 값과 같이 **기본으로 되돌리고 말한다**(25.169).
    """
    if 값 > SENTIMENT_WEIGHT_MAX:
        return 기본, (
            f"설정 sentiment_weight={값:g} 이 {SENTIMENT_WEIGHT_MAX:g} 을 넘어(웹이 저장을 막는 값)"
            f" 기본 {기본:g} 을 썼다"
        )
    return 값, None


def 비중_상한_맞추기(종목: float, 섹터: float) -> tuple[float, str | None]:
    """종목 상한이 섹터 상한보다 크면 섹터 상한까지로 줄인다 (docs/infra.md 25.300).

    웹은 이 조합을 저장하지 않는다. 배치가 받으면 한 종목이 섹터 상한을 넘는 금액을 받을 수 있다 —
    **작은 쪽**으로 맞추는 것이 돈을 덜 쓰는 쪽이다.
    """
    if 종목 > 섹터:
        return 섹터, (
            f"설정 종목 상한 {종목:g}% 가 섹터 상한 {섹터:g}% 보다 커(웹이 저장을 막는 조합)"
            f" 종목 상한을 {섹터:g}% 로 썼다"
        )
    return 종목, None
