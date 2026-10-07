"""D1 오늘 쓰기·읽기 사용량을 한 줄씩 찍는다 (docs/infra.md 25.26·25.122).

**왜 있나.** 따라잡기의 여유(`--budget-reserve`)가 **근거 없이 잡힌 숫자**였다. 뒤 단계와
아침 배치가 얼마를 쓰는지 아무도 재 본 적이 없다. 어림으로는 하루 예산을 다 쓰고도 모자라는데
(infra 25.26), 어림은 어림이다. 단계와 단계 사이에서 이것을 불러 **각 단계가 실제로 얼마를
썼는지** 로그에 남긴다. 그 로그는 이슈로 올라가므로(25.21) 클라우드 세션이 읽는다.

**읽기만 한다.** 카운터 한 줄을 SELECT 할 뿐이고 아무것도 쓰지 않는다. 실패해도 0 으로 끝난다 —
재는 일이 재어지는 일을 망치면 안 된다.

실행
  python scripts/d1_usage.py "4. 과거 시세 끝"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import d1, db  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402


def 적힌_용량(client) -> int | None:
    """일일 배치가 적어 둔 DB 용량. 없으면 `None` — 여기서 재지 않는다(읽기 전용 약속)."""
    try:
        rs = client.execute(
            "SELECT call_count FROM api_usage WHERE api_name = 'd1_db_size'"
            " ORDER BY window_start DESC LIMIT 1"
        )
        값 = rs.scalar()
        return int(값) if 값 is not None else None
    except Exception:  # noqa: BLE001 — **모르면 None 이다.** 0 으로 두면 "빈 DB" 로 보인다(25.30)
        return None

#: 로그 머리표. **`scripts/return_check.py` 가 이 글자로 줄을 고른다** — 두 스크립트를 잇는
#: 글자라 한 곳에서 온다(docs/infra.md 25.113 의 교훈). 쓰기와 읽기를 **다른 머리표**로 나눈다:
#: 같은 머리표를 쓰면 되살아나는 날의 예산 판정이 읽은 행(수백만)을 쓴 행으로 읽는다.
WRITE_MARK = "[D1 사용량]"
READ_MARK = "[D1 읽기량]"
SIZE_MARK = "[D1 용량]"


def 행으로(n: int) -> str:
    return f"{n:,}행"


def 메가바이트로(n: int) -> str:
    """**용량은 행이 아니다** (2026-09-23, docs/infra.md 25.151).

    웹의 `/status` 게이지는 이 값을 MB 로 보여 준다(`web/lib/limits.usageAmount`).
    같은 수를 한쪽은 MB, 한쪽은 "행" 으로 적고 있었다 — 되살아나는 날 이 줄을 읽는
    사람이 "3억 행을 썼다" 로 읽는다.
    """
    return f"{n / 1_048_576:.0f}MB"


def 한줄(
    label: str,
    쓴: int | None,
    한도: int,
    무엇: str = "썼다",
    mark: str = WRITE_MARK,
    단위=행으로,
    창: str = "오늘",
) -> str:
    """**못 읽었으면 0 이 아니라 '모름' 이다.** 0 으로 적으면 "아직 아무것도 안 썼다" 로 읽히고,
    그 한 줄을 보고 "더 돌려도 되겠다" 고 판단하게 된다.

    `단위`·`창` 을 받는 이유 (25.151): 쓰기·읽기는 **오늘 센 행**이고 용량은
    **지금 차 있는 바이트**다. 셋을 한 문장 틀로 찍으면서 단위와 기간을 안 갈랐더니
    용량 줄이 "오늘 314,572,800행 찼다" 가 됐다. 둘 다 틀렸다.
    """
    머리 = f"{mark} {label}:"
    if 쓴 is None:
        return f"{머리} 카운터를 읽지 못했다 (한도 {단위(한도)})"
    비율 = (쓴 * 100) // 한도 if 한도 else 0
    남은 = max(0, 한도 - 쓴)
    앞 = f"{창} " if 창 else ""
    return f"{머리} {앞}{단위(쓴)} {무엇} ({비율}%), 남은 {단위(남은)} / 한도 {단위(한도)}"


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "지금"
    try:
        client = TursoClient()
        try:
            print(한줄(label, db.d1_writes_today(client), db.D1_DAILY_WRITE_LIMIT))
            # **읽기도 찍는다** (2026-09-22, docs/infra.md 25.122). infra 의 여러 결정이
            # "읽기는 넉넉하다(하루 500만 행)" 를 근거로 삼았는데 재 본 적이 없었다
            print(한줄(label, db.d1_reads_today(client), db.D1_DAILY_READ_LIMIT, "훑었다", READ_MARK))
            # 용량은 **리셋이 없는 한도**다 (25.123). 여기서는 적지 않고 적힌 것을 읽기만 한다 —
            # 재는 쪽은 하루 한 번 도는 일일 배치다
            # 용량은 **바이트**이고 **오늘 센 것이 아니다** — 단위와 기간을 따로 준다 (25.151)
            print(한줄(label, 적힌_용량(client), d1.FREE_DB_BYTES, "찼다", SIZE_MARK, 메가바이트로, ""))
        finally:
            client.close()
    except Exception as error:  # noqa: BLE001 — 재는 일이 재어지는 일을 망치면 안 된다
        print(f"{WRITE_MARK} {label}: 재지 못했다 ({error})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
