"""**예약해 둔 작업이 멈추면 어디서 보이나** (docs/infra.md 25.139).

`tests/test_watch_names.py` 는 무응답 감시가 셋뿐인 이유를 이렇게 적어 두었다.

    사람이 매일 여는 화면에 곧바로 드러나는 것만 지켜보고,
    **주 1회·월 1회짜리는 `/status` 의 "데이터 신선도" 로 본다(`FRESHNESS`).**

그 문장은 **약속**이다. 무응답 알림을 안 붙인 대가로 신선도 칸이 대신 본다는 것이다.
그런데 정작 신선도 칸에 없는 예약 작업이 일곱이었다 — 업종·실적일정·ETF·적립판정·
신호 성적표·내부자·미국 공시. **알림도 없고 화면도 없으면 아무 데서도 안 보인다.**
몇 달 멈춰도 모른다. 25.0 의 「문서가 "누가 한다" 를 약속하는데 그 주체가 할 수 없다」.

여기서 하는 일은 그 약속을 **깨지는 검사로** 바꾸는 것이다. 예약이 걸린 워크플로마다
셋 중 하나를 대야 한다.

  1. `WATCHES` — 무응답 알림이 지켜본다
  2. `FRESHNESS` — `/status` 의 신선도 칸이 지켜본다
  3. `안_보는_이유` — **왜 안 봐도 되는지** 적는다 (빠뜨림과 판단을 가른다, 25.76)

새 예약 워크플로를 만들면 이 검사가 먼저 깨진다. 그때 셋 중 하나를 고르게 된다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
HEALTH = (뿌리 / "web" / "lib" / "health.ts").read_text(encoding="utf-8")
워크플로 = 뿌리 / ".github" / "workflows"

#: 예약 워크플로 → 그 작업이 멈춘 것을 **어디서 보나**.
#:
#: `("watch", 이름)`     무응답 알림이 본다 (`WATCHES[].job`)
#: `("freshness", 키)`   `/status` 신선도 칸이 본다 (`FRESHNESS[].key`)
#:
#: 대표 한 칸만 적는다 — 국내·미국으로 갈린 것은 국내 쪽을 적는다(미국은 25.14 로 쉬는 중이라
#: 그 칸이 낡은 것이 정상이다). 칸이 있다는 사실이 여기서 묻는 전부다.
보는곳 = {
    "daily-kr.yml": ("watch", "daily_kr"),
    "daily-us.yml": ("watch", "daily_us"),
    "sentiment-kr.yml": ("watch", "sentiment"),
    "accumulation.yml": ("freshness", "accum_picks_kr"),
    "kis-flows.yml": ("freshness", "kr_flows"),
    "backtest.yml": ("freshness", "backtest_kr"),
    "dividends.yml": ("freshness", "dividends_kr"),
    "earnings-calendar.yml": ("freshness", "earnings_calendar_kr"),
    "etf.yml": ("freshness", "etf_profiles_kr"),
    "financials.yml": ("freshness", "financials_kr"),
    "metrics.yml": ("freshness", "metrics_kr"),
    "sectors.yml": ("freshness", "sectors_kr"),
    "signal-outcomes.yml": ("freshness", "signal_outcomes_kr"),
    "universe.yml": ("freshness", "universe_kr"),
    "us-financials.yml": ("freshness", "financials_us"),
    "valuation-bands.yml": ("freshness", "valuation_bands_kr"),
    "weekly-summary.yml": ("freshness", "weekly_summary"),
    "restore-drill.yml": ("freshness", "restore_drill"),
}

#: 알림도 신선도 칸도 두지 않기로 **정한** 것들. 사유가 곧 판단의 기록이다.
안_보는_이유 = {
    "backup.yml": "DB 에 흔적을 남기지 않는다 — 결과물은 Actions 아티팩트다."
    " 멈춘 것은 워크플로 실행 이력에서 본다 (scripts/backup_db.py)",
    "d1-catchup.yml": "제 일이 없다. 굶은 다른 작업을 대신 돌리는 것이라"
    " 멈추면 **그 작업들의 신선도 칸**이 대신 낡는다 (docs/infra.md 25.20)",
    "turso-return.yml": "Turso 가 풀릴 때까지 **아무것도 하지 않는 것이 정상**이다."
    " 신선도로 볼 산출물이 없다 (docs/infra.md 25.12)",
    "insider-kr.yml": "근거표의 **참고 행**에만 쓴다 — 점수·신호를 바꾸지 않는다."
    " 종목 상세에서 그 행의 날짜가 그대로 보인다 (docs/data-sources.md 16.1)",
    "disclosures-us.yml": "D1 임시 운영 중에는 워크플로가 스스로 쉰다 (docs/infra.md 25.14)."
    " 미국이 통째로 멈춘 것을 신선도 칸 하나로 또 알릴 이유가 없다",
    "refresh-us-adjusted.yml": "같은 이유로 D1 운영 중에는 쉰다 (docs/infra.md 25.14)."
    " 대기열도 미국 일일 배치가 채우는데 그것이 쉬고 있다",
    "us-shares.yml": "미국 유니버스 갱신의 재료다. 미국은 쉬는 중이고(25.14),"
    " 주식수를 언제 받았는지는 `[확인필요]` 로 handoff 4.1 에 남겨 두었다",
}

_예약 = re.compile(r"^\s*-\s*cron:", re.M)


def 예약_워크플로() -> list[str]:
    """`on.schedule` 에 **주석이 아닌** cron 이 한 줄이라도 있는 워크플로."""
    나온것 = []
    for 길 in sorted(워크플로.glob("*.yml")):
        줄들 = 길.read_text(encoding="utf-8").splitlines()
        안에 = False
        for 줄 in 줄들:
            벗긴 = 줄.strip()
            if 벗긴.startswith("#") or not 벗긴:
                continue  # 주석으로 꺼 둔 예약은 도는 것이 아니다
            if re.match(r"^\s{0,4}schedule:\s*$", 줄):
                안에 = True
                continue
            if 안에:
                if _예약.match(줄):
                    나온것.append(길.name)
                    break
                if not 벗긴.startswith("-"):
                    안에 = False
    return 나온것


def _목록(이름: str) -> str:
    """`health.ts` 에서 `export const <이름> ... = [` 부터 `\\n];` 까지, **주석 줄을 뺀** 몸통."""
    뒤 = HEALTH.split(f"export const {이름}", 1)[1].split(" = [", 1)[1]
    몸통 = 뒤.split("\n];", 1)[0]
    # 주석 안의 말이 항목으로 읽히면 안 된다 (25.105·25.109·25.112·25.116·25.130 에서
    # 같은 실수를 다섯 번 했다)
    return "\n".join(줄 for 줄 in 몸통.splitlines() if not 줄.strip().startswith("//"))


예약들 = 예약_워크플로()
감시이름 = set(re.findall(r'job:\s*"([^"]+)"', _목록("WATCHES")))
신선도키 = set(re.findall(r'key:\s*"([^"]+)"', _목록("FRESHNESS")))


def test_읽어_냈다() -> None:
    """정규식이 빗나가 비면 아래가 전부 공짜로 통과한다."""
    assert len(예약들) >= 20, f"예약 워크플로를 {len(예약들)}개밖에 못 찾았다: {예약들}"
    assert len(감시이름) >= 3, f"WATCHES 를 {len(감시이름)}개밖에 못 읽었다"
    assert len(신선도키) >= 20, f"FRESHNESS 를 {len(신선도키)}개밖에 못 읽었다"


@pytest.mark.parametrize("파일", 예약들)
def test_예약된_작업은_어디선가_보인다(파일: str) -> None:
    if 파일 in 안_보는_이유:
        pytest.skip(f"안 보기로 정했다: {안_보는_이유[파일]}")

    assert 파일 in 보는곳, (
        f"`{파일}` 은 예약을 걸어 두고 **아무 데서도 안 보인다**.\n"
        "무응답 알림(WATCHES)도 없고 /status 신선도 칸(FRESHNESS)도 없으면,\n"
        "몇 달 멈춰도 아무도 모른다 (docs/infra.md 25.139).\n"
        "셋 중 하나를 골라라 — 감시를 붙이거나, 신선도 칸을 만들거나,\n"
        "`안_보는_이유` 에 **왜 안 봐도 되는지** 적거나."
    )
    갈래, 이름 = 보는곳[파일]
    있는것 = 감시이름 if 갈래 == "watch" else 신선도키
    assert 이름 in 있는것, f"`{파일}` 이 가리키는 {갈래} 항목 `{이름}` 이 health.ts 에 없다"


def test_목록이_낡지_않았다() -> None:
    """예약을 떼거나 워크플로를 지웠는데 목록만 남으면, 다음 사람이 있는 줄 안다."""
    유령 = sorted((set(보는곳) | set(안_보는_이유)) - set(예약들))

    assert not 유령, f"예약이 없는 워크플로가 목록에 남아 있다: {유령}"


def test_안_보는_이유가_비어_있지_않다() -> None:
    """사유 없는 예외는 빠뜨린 것과 구별이 안 된다 (25.76)."""
    짧은것 = [이름 for 이름, 사유 in 안_보는_이유.items() if len(사유.strip()) < 20]

    assert not 짧은것, f"사유가 너무 짧다: {짧은것}"


def test_약속한_문장이_그대로_있다() -> None:
    """이 검사가 지키는 것은 **다른 파일의 한 문장**이다. 그 문장이 사라지면 근거도 사라진다."""
    글 = (뿌리 / "tests" / "test_watch_names.py").read_text(encoding="utf-8")

    assert "FRESHNESS" in 글 and "주 1회·월 1회" in 글, (
        "`test_watch_names.py` 가 '주 1회·월 1회짜리는 FRESHNESS 로 본다' 는 약속을"
        " 더는 적고 있지 않다. 약속이 바뀌었으면 이 파일도 함께 고쳐라 (25.139)"
    )
