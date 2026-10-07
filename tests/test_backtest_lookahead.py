"""**미래가 든 자료를 넣어도 답이 같아야 한다** (docs/infra.md 25.187).

CLAUDE.md 절대 규칙: 백테스트는 look-ahead 방지 필수.

엔진은 그 규칙을 **구조로** 지킨다. `services/backtest.PriceView` 가 cutoff 뒤를 아예
안 들고 있고, 그 자리 주석이 이렇게 적는다.

> "조심해서 안 본다" 가 아니라 **"볼 것이 없다"** 여야 규칙이다.

그런데 판단 함수가 받는 것은 가격만이 아니다. `jobs/backtest.build_pit_inputs` 는
**재무 스냅샷·배당·벤치마크 지수**를 통째로 받아 쓰는 자리마다 손으로 자른다 —
`pit_financials(…, cutoff)` · `pit_dividend(…, cutoff)` ·
`{d: c for d, c in benchmark_closes.items() if d <= cutoff}`. 즉 셋은 **조심해서 안 보는**
쪽이고, 자르기를 한 군데 빠뜨리면 아무도 모른다. 새 팩터가 새 입력을 들고 오면 더 그렇다.

여기서는 그 셋을 **행동으로** 잠근다. 미래가 든 자료와 안 든 자료로 각각 지표를 내고
**같은지** 본다. 어떤 입력이 새로 생겨도 그것이 미래를 흘리면 여기서 깨진다.

이 그물이 공짜로 통과하지 않는다는 것도 **입력마다** 본다 — 같은 미래 자료를 **cutoff 를
뒤로 미뤄** 보이게 하면 지표가 실제로 달라져야 한다. 안 달라지면 미끼가 싱거운 것이고,
그 입력에 대한 검사는 아무것도 지키지 않는다. 지금 하나(벤치마크)가 그렇고,
**왜 지금은 샐 수 없는지**를 사유로 적어 두었다 — 모른 채 통과하면 거짓 안도가 남는다.
"""

from __future__ import annotations

import pytest

from batch.jobs import backtest as bj
from batch.services import backtest as bt

#: 판단 시점. 이 뒤의 자료는 "그때 알 수 없었던 것" 이다
기준일 = "2026-06-30"
#: 미래 자료가 실제로 보이는 시점. 미끼가 싱겁지 않은지 재는 데 쓴다
나중 = "2026-12-01"

날짜들 = [f"2026-{달:02d}-{일:02d}" for 달 in range(1, 13) for 일 in (1, 15)]

유니버스 = [{"stock_id": 1, "market": "KOSPI", "sector": "전기전자", "listed_shares": 1_000}]


def 가격(미래포함: bool) -> dict[int, dict[str, float]]:
    """기준일까지는 100원. 미래를 넣으면 그 뒤가 1,000원이 된다."""
    뒤 = 1_000.0 if 미래포함 else 100.0
    return {1: {d: (100.0 if d <= 기준일 else 뒤) for d in 날짜들}}


def 스냅샷(미래포함: bool) -> dict[int, list[dict]]:
    """2024 사업보고서는 2025-09-10 접수(보인다 — 기준일에서 457일 안, 25.804). 2025 것은 2026-09-01 접수(미래다)."""
    행들 = [
        {
            "as_of_date": "2025-09-10", "fiscal_year": 2024,
            "values": {
                "net_income": 100.0, "total_equity": 1_000.0, "revenue": 5_000.0,
                "total_assets": 2_000.0, "operating_income": 200.0, "total_liabilities": 1_000.0,
            },
        },
    ]  # fmt: skip
    if 미래포함:
        행들.append({
            "as_of_date": "2026-09-01", "fiscal_year": 2025,
            "values": {
                "net_income": 9_999.0, "total_equity": 10.0, "revenue": 99_999.0,
                "total_assets": 20.0, "operating_income": 9_999.0, "total_liabilities": 1.0,
            },
        })  # fmt: skip
    return {1: 행들}


def 배당(미래포함: bool) -> dict[int, list[tuple[str, int, float]]]:
    행들 = [("2025-09-10", 2024, 50.0)]
    if 미래포함:
        행들.append(("2026-09-01", 2025, 99_999.0))
    return {1: 행들}


def 벤치마크(미래포함: bool) -> dict[str, float]:
    값 = {d: 2_000.0 for d in 날짜들 if d <= 기준일}
    if 미래포함:
        값 |= {d: 999_999.0 for d in 날짜들 if d > 기준일}
    return 값


def 지표(미래포함: bool, cutoff: str = 기준일) -> dict[str, float | None]:
    view = bt.PriceView(가격(미래포함), cutoff)
    나온것 = bj.build_pit_inputs(유니버스, 스냅샷(미래포함), view, 배당(미래포함), 벤치마크(미래포함))
    assert 나온것, "입력을 하나도 못 만들었다 — 이 파일의 표본이 잘못됐다"
    return 나온것[0].metrics


def test_표본이_실제로_지표를_만든다() -> None:
    """지표가 전부 None 이면 아래 비교가 공짜로 통과한다."""
    값들 = 지표(미래포함=False)

    아는것 = [k for k, v in 값들.items() if v is not None]
    assert len(아는것) >= 8, f"표본으로 낸 지표가 {len(아는것)}개뿐이다: {sorted(값들)}"


def test_미래가_든_자료를_넣어도_답이_같다() -> None:
    """**이 파일의 이유다.**

    재무 스냅샷·배당·벤치마크는 통째로 넘겨지고 쓰는 자리마다 손으로 잘린다.
    자르기를 한 군데 빠뜨리면 백테스트 성적이 실제보다 좋게 나오고,
    그것으로 규칙을 확정하면 돈을 잃는다.
    """
    있음, 없음 = 지표(미래포함=True), 지표(미래포함=False)

    다른것 = {k: (있음.get(k), 없음.get(k)) for k in set(있음) | set(없음) if 있음.get(k) != 없음.get(k)}

    assert not 다른것, (
        f"미래 자료가 지표를 바꿨다 — look-ahead 다: {다른것}\n"
        "`build_pit_inputs` 에서 그 입력을 cutoff 로 자르는지 보라 (docs/backtest.md 1.1)"
    )


#: `build_pit_inputs` 가 받는 입력들. 하나씩 미래를 넣어 본다
입력들 = {"스냅샷": 스냅샷, "배당": 배당, "벤치마크": 벤치마크, "가격": 가격}

#: **미끼가 물리지 않는 입력과 그 사유.**
#: 미래를 보이게 해도 지표가 안 바뀌면, 그 입력에 대한 아래 검사는 지금 아무것도 지키지
#: 않는다. 그것을 모른 채 통과하면 "지키고 있다" 는 거짓 안도가 남는다
지금은_샐_수_없는_입력 = {
    "벤치마크": "잔차 변동성이 **가격 창과 교집합**으로만 쓴다"
    " (`aligned_returns(dict(closes), benchmark)`). `closes` 가 이미 cutoff 로 잘려 있어"
    " 지수만 안 자르는 것으로는 미래가 못 샌다 — `build_pit_inputs` 의 손 자르기는 덧방어다."
    " 지수를 **가격과 맞추지 않고** 쓰는 팩터가 생기면 그때부터 아래 검사가 물기 시작한다",
}


def 한_입력만(그것만: str | None, cutoff: str = 기준일) -> dict[str, float | None]:
    """그 입력에만 미래를 넣고 지표를 낸다."""
    고른것 = {이름: 만들기(그것만 == 이름) for 이름, 만들기 in 입력들.items()}
    view = bt.PriceView(고른것["가격"], cutoff)
    나온것 = bj.build_pit_inputs(
        유니버스, 고른것["스냅샷"], view, 고른것["배당"], 고른것["벤치마크"]
    )
    assert 나온것, "입력을 하나도 못 만들었다 — 이 파일의 표본이 잘못됐다"
    return 나온것[0].metrics


@pytest.mark.parametrize("이름", sorted(입력들))
def test_미끼가_싱겁지_않다(이름: str) -> None:
    """**아래 검사가 공짜로 통과하지 않는다는 증거.**

    같은 미래 자료를 cutoff 를 뒤로 미뤄 **보이게** 하면 지표가 달라져야 한다.
    안 달라지면 넣은 값이 애초에 아무 지표에도 안 닿는다는 뜻이다.
    """
    기준 = 한_입력만(None, cutoff=나중)
    보임 = 한_입력만(이름, cutoff=나중)
    달라진것 = sorted(k for k in set(기준) | set(보임) if 기준.get(k) != 보임.get(k))

    사유 = 지금은_샐_수_없는_입력.get(이름)
    if 사유:
        assert len(사유.strip()) > 30, f"{이름} 의 사유가 너무 짧다"
        assert not 달라진것, (
            f"`{이름}` 은 지금 샐 수 없다고 적어 두었는데 실제로는 지표가 바뀐다: {달라진것}\n"
            "사유가 낡았다 — `지금은_샐_수_없는_입력` 에서 빼라"
        )
        return

    assert 달라진것, (
        f"`{이름}` 에 미래를 넣고 보이게 해도 지표가 하나도 안 바뀐다.\n"
        "표본의 미래 값이 지표에 안 닿는다 — 아래 검사가 헛돈다."
        " 표본을 고치거나 `지금은_샐_수_없는_입력` 에 사유를 적어라"
    )


@pytest.mark.parametrize("이름", sorted(입력들))
def test_입력_하나씩_미래를_넣어도_답이_같다(이름: str) -> None:
    """**어느 입력이 샜는지 이름으로 말한다.** 한꺼번에 보면 어디를 고칠지 모른다."""
    있음, 없음 = 한_입력만(이름), 한_입력만(None)
    다른것 = {k for k in set(있음) | set(없음) if 있음.get(k) != 없음.get(k)}

    assert not 다른것, f"`{이름}` 이 미래를 흘린다: {sorted(다른것)}"


def test_사유_목록이_낡지_않았다() -> None:
    없는것 = sorted(set(지금은_샐_수_없는_입력) - set(입력들))

    assert not 없는것, f"`build_pit_inputs` 가 안 받는 입력의 사유가 남아 있다: {없는것}"


def test_판단_창이_잘려_있다() -> None:
    """가격은 **구조로** 막혀 있다. 그 구조가 살아 있는지 함께 본다."""
    view = bt.PriceView(가격(미래포함=True), 기준일)

    assert all(d <= 기준일 for d, _c in view.closes(1)), "PriceView 가 cutoff 뒤를 들고 있다"
