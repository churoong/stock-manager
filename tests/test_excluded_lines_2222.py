"""표본 밖 끝 줄이 이름 붙여 센 까닭(`EXCLUDED_NAMED`, 25.2220)을 읽는다 (docs/infra.md 25.2222, 25.2220 교차검증 [권고]).

25.2220 이 `compare` 에 `excluded_late_send`·`excluded_no_bar` 를 더했지만 주간 요약(`compare_lines`)·웹 /kis 끝 줄은
읽지 않았고 괄호의 까닭 목록에 "일봉 없음" 도 없었다. 배치와 웹이 같은 글자여야 한다(웹은 kisExcluded2222 이 본다)"""

from pathlib import Path

from batch.services import broker_mirror as bm

_웹 = (Path(__file__).resolve().parents[1] / "web" / "lib" / "paper.ts").read_text(encoding="utf-8")


def _끝(s: dict) -> str:
    return bm.compare_lines(s, None, None)[-1]


def _요약(**더) -> dict:
    s = {"days": 28, "n": 0, "rejected": 0, "unknown": 0, "cleanup": 0, "slip_buy": {"n": 0}, "slip_sell": {"n": 0}}
    s.update(더)
    return s


def test_까닭_목록에_일봉_없음() -> None:
    assert bm.EXCLUDED_WHY.endswith("·일봉 없음")
    assert _끝(_요약(excluded=1)) == f"  표본 밖 1({bm.EXCLUDED_WHY})"


def test_센_까닭을_괄호_안에_붙인다() -> None:
    끝 = _끝(_요약(excluded=5, excluded_late_send=2, excluded_no_bar=1))
    assert 끝 == ("  표본 밖 5(가상의 판정 아닌 만료·시세·환율 없어 미룬 매도·장 열린 뒤 시장가·일봉 없음"
                 " — 개장 뒤 보낸 국내 시장가 2 · 일봉 없음 1)")  # fmt: skip
    assert _끝(_요약(excluded=1, excluded_no_bar=1)).endswith(" — 일봉 없음 1)")
    assert " — " not in _끝(_요약(excluded=2, excluded_late_send=0))


def test_compare_가_낸_열쇠를_그대로_읽는다() -> None:
    """`compare` 출력 열쇠(`excluded_<열쇠>`)와 `excluded_detail` 이 읽는 열쇠가 같다 — 이름만 바뀌어 0 으로 읽히지 않게"""
    for k, 이름 in bm.EXCLUDED_NAMED.items():
        assert bm.excluded_detail({f"excluded_{k}": 4}).endswith(f" — {이름} 4")


def test_웹과_같은_글() -> None:
    assert f'EXCLUDED_WHY = "{bm.EXCLUDED_WHY}"' in _웹
    쌍 = ", ".join(f'["{k}", "{v}"]' for k, v in bm.EXCLUDED_NAMED.items())
    assert f"EXCLUDED_NAMED = [{쌍}] as const" in _웹
    for k in bm.EXCLUDED_NAMED:
        assert f"excluded_{k}?: number;" in _웹
