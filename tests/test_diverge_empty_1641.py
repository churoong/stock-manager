"""25.1641 — 어제 켜졌는데 알림이 실패한 괴리 칸이 오늘 표본 0 이어도 글이 나온다.

`diverge_active` 는 표본이 모자라면 어제 켜진 칸을 그대로 두고, 보내지 못한 칸은 다음 실행이 다시 글로 만든다.
그날 값이 None 이면 `diverge()` 가 `float(None)` 으로 죽어 `diverge_error` 만 남고, 상태는 그대로라 다음 날도 되풀이됐다.
"""

from batch.services import broker_mirror as bm


def _빈_요약() -> dict:
    # 28일 창에 끝난 베낀 주문이 하나도 없는 날 — summarize 가 내는 꼴 그대로
    return {"days": 28, "n": 0, "match_pct": None, "kept_pct": None, "both": 0, "internal_only": 0, "kis_only": 0,
            "neither": 0, "slip_buy": {"n": 0, "avg": None}, "slip_sell": {"n": 0, "avg": None}}  # fmt: skip


def test_어제_켜진_칸은_표본_0_에도_그대로_켜진다() -> None:
    켜짐 = bm.diverge_active(_빈_요약(), {"match", "kept", "slip_buy", "slip_sell"})
    assert 켜짐 == {"match", "kept", "slip_buy", "slip_sell"}


def test_값이_없는_칸도_알림_글이_나온다() -> None:
    글 = bm.diverge(_빈_요약(), ["kept", "match", "slip_buy", "slip_sell"], "KR")
    assert 글 is not None
    assert "체결 일치 오늘 값 없음(표본 부족 — 어제 켜진 그대로, 문턱 80%)." in 글
    assert "비율) 오늘 값 없음(표본 부족 — 어제 켜진 그대로, 문턱 85%):" in 글
    assert "매수 가격 차이 평균(n=0) 오늘 값 없음(표본 부족 — 어제 켜진 그대로, 문턱 +0.5%):" in 글
    assert "매도 가격 차이 평균(n=0)" in 글


def test_가격_칸이_통째로_없어도_죽지_않는다() -> None:
    요약 = _빈_요약()
    del 요약["slip_sell"]
    assert "매도 가격 차이 평균(n=0) 오늘 값 없음" in (bm.diverge(요약, ["slip_sell"], "US") or "")


def test_값이_있으면_예전_글_그대로() -> None:
    요약 = _빈_요약() | {"n": 30, "match_pct": 70.0}
    assert "체결 일치 70% — 문턱 80% 아래." in (bm.diverge(요약, ["match"], "KR") or "")
