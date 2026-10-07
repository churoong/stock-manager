"""파이썬이 읽는 설정을 **사람이 바꿀 수 있나** (docs/infra.md 25.96).

`settings` 표는 두 쪽이 쓴다 — 웹 화면이 쓰고, 배치가 읽는다. 그런데 둘을 잇는 것은
**글자 하나**뿐이라 한쪽이 키를 더하거나 고쳐도 아무 오류가 안 난다. 배치는 기본값으로
조용히 돌고, 사용자는 그 값을 바꿀 길이 없다는 사실조차 모른다.

2026-09-21 실측: 파이썬이 읽는 키 열하나 중 **`min_order_amount` 하나가 화면에 없었다.**
같은 날 그 문턱이 2부에서 **종목을 빼는** 힘을 갖게 돼(25.96) 더 중요해졌다 —
바꿀 수 없는 값이 추천을 지운다.

25.62(설정 키가 두 곳에 따로 적혀 있었다)와 같은 줄기이고, 그때는 **이름이 갈린 것**을
봤다면 여기는 **한쪽에만 있는 것**을 본다. 그물은 방향이 있다(25.0).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 화면에 없어도 되는 키와 그 사유. **사유 없는 예외는 두지 않는다**
화면_밖_예외: dict[str, str] = {
    # 사람이 바꾸는 설정이 아니라 배치가 적는 기록이다 — 미국 분할 감지일 (docs/infra.md 25.535)
    "us_split_detections": "배치 내부 기록(분할 감지일). 사람이 고칠 값이 아니다",
}


def 파이썬이_읽는_키() -> dict[str, str]:
    """`db.get_setting`/`set_setting` 의 첫 인자(키)와 그것을 부르는 자리."""
    나온것: dict[str, str] = {}
    for path in sorted((뿌리 / "batch").rglob("*.py")):
        나무 = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(나무):
            if not isinstance(node, ast.Call):
                continue
            # `get_setting_in_range` 도 같은 문이다 (docs/infra.md 25.170) — 범위까지 보는
            # 판만 다르고 모양은 같다. 빠뜨리면 그 키들이 **훑기의 눈에서 사라진다**
            if getattr(node.func, "attr", "") not in (
                "get_setting",
                "get_setting_in_range",
                "set_setting",
            ):
                continue
            # (client, "키", 기본값) 꼴이다. 키가 변수면 훑기로 알 수 없으니 건너뛴다
            키 = node.args[1] if len(node.args) > 1 else None
            if isinstance(키, ast.Constant) and isinstance(키.value, str):
                나온것.setdefault(키.value, f"{path.name}:{node.lineno}")
    return 나온것


def 화면이_아는_키() -> set[str]:
    """`web/lib/settings.ts` 의 **스키마**가 선언한 최상위 필드.

    **기본값 객체는 안 본다.** 처음에 파일 전체를 훑었더니 기본값에 적힌 이름까지 세어,
    스키마에서 키를 지워도 이 테스트가 통과했다 — 돌연변이로 잡았다.
    화면이 값을 받으려면 **스키마에 있어야** 한다(입력을 검증하는 자리다).
    """
    본문 = (뿌리 / "web" / "lib" / "settings.ts").read_text(encoding="utf-8")
    없앤것 = re.sub(r"/\*.*?\*/", "", 본문, flags=re.S)
    자리 = 없앤것.index("z.object({")
    스키마 = 없앤것[자리 : 없앤것.index("\n});", 자리)]
    return set(re.findall(r"^\s{2}(\w+):", 스키마, flags=re.M))


def test_훑기가_실제로_읽어_냈다() -> None:
    """조용히 빈 집합을 내면 아래가 전부 공짜로 통과한다. 가장 위험한 실패다."""
    assert len(파이썬이_읽는_키()) >= 10
    assert len(화면이_아는_키()) >= 10


def test_파이썬이_읽는_설정은_화면에서_바꿀_수_있다() -> None:
    없는것 = {
        키: 자리
        for 키, 자리 in 파이썬이_읽는_키().items()
        if 키 not in 화면이_아는_키() and 키 not in 화면_밖_예외
    }

    assert not 없는것, (
        "배치가 읽는데 **화면에 없는** 설정이 있다. 사용자는 기본값에서 바꿀 길이 없고,\n"
        "  바꿀 수 없다는 사실조차 화면에 안 나온다:\n  "
        + "\n  ".join(f"{k}  ({v})" for k, v in sorted(없는것.items()))
    )


def test_예외_목록에_사유가_있다() -> None:
    assert all(화면_밖_예외.values()), "예외에는 왜 화면에 없어도 되는지를 적는다"


def test_화면에만_있는_키도_세어_둔다() -> None:
    """반대 방향. **쓰기만 하고 아무도 안 읽는 설정**은 사용자를 속인다 — 바꿔도 아무 일이 없다."""
    안읽는것 = 화면이_아는_키() - set(파이썬이_읽는_키())

    # 웹이 자기 화면에서만 쓰는 값들이 있다(표시 통화, 알림 문턱 등).
    # 늘어나는 것을 조용히 두지 않으려고 **수**만 고정한다. 2026-09-21 실측 6개
    assert len(안읽는것) <= 6, (
        f"웹만 아는 설정이 {len(안읽는것)}개로 늘었다: {sorted(안읽는것)}\n"
        "  배치가 읽어야 하는 값이 섞였는지 보라 — 바꿔도 아무 일이 없는 설정은 거짓말이다"
    )


def test_최소_주문_금액의_기본값이_양쪽에서_같다() -> None:
    """**한 규칙이 두 곳에 있다** (docs/infra.md 25.0). 갈라지면 화면과 배치가 다르게 센다."""
    파이썬 = (뿌리 / "batch" / "jobs" / "signals.py").read_text(encoding="utf-8")
    웹 = (뿌리 / "web" / "lib" / "settings.ts").read_text(encoding="utf-8")

    py = re.search(r"DEFAULT_MIN_ORDER\s*=\s*([\d_]+)", 파이썬)
    ts = re.search(r"min_order_amount:\s*([\d_]+)", 웹)

    assert py and ts, "기본값을 못 읽었다 — 이름이 바뀌었으면 이 테스트도 고쳐라"
    assert int(py.group(1).replace("_", "")) == int(ts.group(1).replace("_", ""))


def test_화면에_실제로_입력칸이_있다() -> None:
    """스키마에만 넣고 폼에 안 넣으면 **여전히 바꿀 수 없다** (25.65·25.92 와 같은 모양)."""
    본문 = (뿌리 / "web" / "components" / "SettingsForm.tsx").read_text(encoding="utf-8")

    assert "min_order_amount" in 본문, "설정 화면에 최소 주문 금액 입력칸이 없다"
