"""**깔때기 칸 이름과 글이 두 곳에 따로 있다** (docs/infra.md 25.175).

적립 판정은 관문을 하나씩 지난다(G1~G10). 어디서 떨어졌는지는 `first_failed_gate` 에
글자로 저장되고, 화면은 `web/lib/accumulation.GATE_LABELS` 로 그 글자를 사람이 읽는
말로 바꾼다. **두 목록을 대 보는 곳이 없었다.**

무슨 일이 생기나.

* 관문을 하나 더하면(G11) 화면은 `gateLabel()` 의 폴백으로 **`G11` 이라는 글자만**
  덩그러니 보여 준다. 오류도 빈칸도 아니라서 아무도 모른다
* 관문을 지우거나 이름을 바꾸면 라벨만 남아 영영 안 쓰인다
* 라벨 글에 **숫자가 박혀 있다** — "상장 10년", "부채비율 ≤ 100%", "시총 400위 이내".
  그 숫자의 정의처는 파이썬 상수다. 상수를 고쳐도 화면 글은 그대로다.
  사용자는 **틀린 문턱을 읽는다**. 25.108 과 같은 모양이다

그래서 여기서 셋을 본다 — 이름이 양쪽에 다 있는가, 나라별로 다른 관문이 맞게 갈라졌는가,
**라벨 속 숫자가 지금의 상수와 같은가.**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from batch.services import accumulation as acc

뿌리 = Path(__file__).resolve().parent.parent
웹_소스 = 뿌리 / "web" / "lib" / "accumulation.ts"
배치_소스 = 뿌리 / "batch" / "services" / "accumulation.py"


def 배치가_내는_칸() -> set[str]:
    """`first_failed_gate` 에 실제로 들어가는 글자들.

    소스를 훑는다. 손으로 옮겨 적으면 관문이 늘어도 이 파일이 옛 목록을 지키며 통과한다.
    """
    글 = 배치_소스.read_text(encoding="utf-8")
    나온것 = set(re.findall(r'fail\(\s*"(G\d+)"', 글))
    # G1 은 입력 자체가 편입 종목이라 `fail()` 로 떨어지지 않는다. 유니버스에서 이미 걸러진다.
    # 그래도 깔때기 표의 첫 칸으로 화면에 나오므로 라벨이 있어야 한다
    나온것.add("G1")
    나온것.add(acc.NO_CRITERIA_GATE)
    return 나온것


def 웹_라벨(이름: str) -> dict[str, str]:
    글 = 웹_소스.read_text(encoding="utf-8")
    본문 = 글[글.index(f"{이름}: Record<string, string> = {{") :]
    본문 = 본문[: 본문.index("\n};")]
    # 주석 줄을 걷어낸다. 주석 안의 `G9:` 같은 글이 라벨로 잡히면 안 된다
    코드 = re.sub(r"^\s*//.*$", "", 본문, flags=re.M)
    return dict(re.findall(r'^\s{2}([\w가-힣]+):\s*"([^"]+)"', 코드, flags=re.M))


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 전부 공짜로 통과한다."""
    assert len(배치가_내는_칸()) >= 10, f"배치 관문을 {len(배치가_내는_칸())}개밖에 못 찾았다"
    assert len(웹_라벨("GATE_LABELS")) >= 10
    assert 웹_라벨("GATE_LABELS_US"), "미국 덮어쓰기 목록을 못 읽었다"


def test_배치가_내는_칸에_모두_라벨이_있다() -> None:
    """**그물은 방향이 있다.** 관문이 늘면 화면은 글자만 보여 주고 아무도 모른다."""
    빠진것 = sorted(배치가_내는_칸() - set(웹_라벨("GATE_LABELS")))

    assert not 빠진것, (
        f"배치가 내는데 화면에 라벨이 없는 칸: {빠진것}\n"
        "web/lib/accumulation.ts 의 GATE_LABELS 에 더해라. "
        "지금은 `gateLabel()` 이 폴백으로 관문 이름 그대로를 보여 준다"
    )


def test_라벨만_남은_칸이_없다() -> None:
    """반대 방향. 관문을 지웠는데 라벨이 남으면 안 쓰이는 글이 늘어간다."""
    남은것 = sorted(set(웹_라벨("GATE_LABELS")) - 배치가_내는_칸())

    assert not 남은것, f"배치가 더는 내지 않는 칸의 라벨이 남아 있다: {남은것}"


def test_미국_덮어쓰기가_실제_관문이다() -> None:
    덮어쓴것 = set(웹_라벨("GATE_LABELS_US"))
    빠진것 = sorted(덮어쓴것 - 배치가_내는_칸())

    assert not 빠진것, f"미국 라벨이 있지도 않은 관문을 덮어쓴다: {빠진것}"


def test_미국에서_안_도는_관문은_덮어쓰지_않는다() -> None:
    """G4(금융업 대리)는 국내에서만 판정한다. 미국 라벨을 두면 안 나올 줄을 설명하는 셈이다."""
    assert acc.KR.financial_proxy is True
    assert acc.US.financial_proxy is False
    assert "G4" not in 웹_라벨("GATE_LABELS_US")


#: 라벨 글에 박힌 숫자 → 그 숫자의 **정의처**. 사유가 없는 칸은 숫자가 없는 칸이다
라벨_속_숫자 = {
    ("GATE_LABELS", "G2"): acc.MIN_LISTED_YEARS,
    ("GATE_LABELS", "G3"): acc.YEARS,
    ("GATE_LABELS", "G5"): acc.YEARS,
    ("GATE_LABELS", "G7"): int(acc.MAX_DEBT_RATIO * 100),
    ("GATE_LABELS", "G8"): acc.YEARS,
    ("GATE_LABELS", "G9"): acc.KR.max_cap_rank,
    ("GATE_LABELS", "G10"): acc.YEARS,
    ("GATE_LABELS_US", "G2"): acc.MIN_LISTED_YEARS,
    ("GATE_LABELS_US", "G5"): acc.YEARS,
    ("GATE_LABELS_US", "G9"): acc.US.max_cap_rank,
}

#: 숫자가 없어도 되는 칸과 **왜 없어도 되는지**
숫자가_없는_칸 = {
    "G1": "유니버스 편입 여부다. 문턱은 `services/universe` 쪽에 있고 여기 글에는 안 적는다",
    "G4": "매출 유무로 금융업을 대신 가린다. 세는 숫자가 없다",
    "G6": "순손실·자본잠식은 0 과의 비교라 문턱이랄 것이 없다",
    acc.NO_CRITERIA_GATE: "관문이 아니다. 근거표를 못 만들어 뺀 칸이다 (docs/infra.md 25.174)",
}


@pytest.mark.parametrize(("목록", "칸"), sorted(라벨_속_숫자))
def test_라벨의_숫자가_지금_상수와_같다(목록: str, 칸: str) -> None:
    """**사용자는 화면에 적힌 문턱을 믿는다.**

    상수를 고치고 글을 안 고치면 화면이 거짓말을 한다. 그걸 알아채는 길이 없다 —
    화면은 멀쩡히 뜨고 테스트도 다 통과한다 (25.108 과 같은 모양).
    """
    글 = 웹_라벨(목록)[칸]
    숫자들 = {int(n.replace(",", "")) for n in re.findall(r"[\d,]+", 글)}

    assert 라벨_속_숫자[(목록, 칸)] in 숫자들, (
        f"{목록}[{칸}] 이 \"{글}\" 인데 지금 상수는 {라벨_속_숫자[(목록, 칸)]} 이다.\n"
        "batch/services/accumulation.py 의 상수가 정의처다. 화면 글을 맞춰라"
    )


def test_숫자를_대_보지_않는_칸에는_사유가_있다() -> None:
    """**범위를 판단으로 남긴다.** 빠뜨린 것과 일부러 뺀 것은 다르다."""
    대본것 = {칸 for _, 칸 in 라벨_속_숫자}
    안_대본것 = sorted(set(웹_라벨("GATE_LABELS")) - 대본것 - set(숫자가_없는_칸))

    assert not 안_대본것, (
        f"라벨은 있는데 숫자를 대 보지도, 사유를 적지도 않은 칸: {안_대본것}\n"
        "`라벨_속_숫자` 에 정의처를 적거나 `숫자가_없는_칸` 에 사유를 적어라"
    )


def test_사유_목록이_낡지_않았다() -> None:
    없는것 = sorted(set(숫자가_없는_칸) - set(웹_라벨("GATE_LABELS")))

    assert not 없는것, f"화면에 없는 칸의 사유가 남아 있다: {없는것}"


def test_사유가_비어_있지_않다() -> None:
    짧은것 = [칸 for 칸, 글 in 숫자가_없는_칸.items() if len(글.strip()) < 15]

    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"
