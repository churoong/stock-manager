"""주간 운영 요약 (docs/health.md 7장, docs/infra.md 25.943).

손으로 `batch_runs` 를 훑던 네 가지가 한 메시지에 다 있는가, 그리고 예약 워크플로의 작업 이름이 기대 주기 표에 빠지지 않았는가.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from batch.jobs import weekly_summary as ws

지금 = datetime(2026, 10, 4, 22, 41, tzinfo=UTC)


def _run(job: str, status: str, at: str, *warnings: str, market: str | None = "KR", error: str | None = None) -> ws.Run:
    return ws.Run(job, market, status, at, error, tuple(warnings))


def test_전부_성공이면_한_줄이고_되풀이_절이_없다() -> None:
    runs = [_run("daily_kr", "success", "2026-10-01T00:00:00+00:00"), _run("backup", "success", "2026-10-02T00:00:00+00:00")]
    글 = ws.compose(runs, {j: "2026-10-04T00:00:00+00:00" for j in ws.EXPECTED_DAYS}, 지금, 10_000_000)
    assert "실행 2건 — 전부 성공" in 글
    assert "되풀이된 경고" not in 글 and "기대 주기를 넘긴" not in 글 and "수정주가" not in 글


def test_부분_실패를_작업별로_세고_되풀이된_사유만_묶는다() -> None:
    runs = [
        _run("daily_kr", "partial", "2026-10-01T00:00:00+00:00", "portfolio: 005930 종가 또는 환율이 없어 평가하지 못했습니다"),
        _run("daily_kr", "partial", "2026-10-02T00:00:00+00:00", "portfolio: 005930 종가 또는 환율이 없어 평가하지 못했습니다"),
        _run("daily_us", "partial", "2026-10-02T12:00:00+00:00", "시세를 받지 못한 종목 1개 (예: SVA)", market="US"),
        _run("etf", "failed", "2026-10-02T01:00:00+00:00", market="US", error="야후 차단"),
    ]
    글 = ws.compose(runs, {j: "2026-10-04T00:00:00+00:00" for j in ws.EXPECTED_DAYS}, 지금, 10_000_000)
    assert "daily_kr: 성공 0 · 부분 2" in 글 and "etf: 성공 0 · 실패 1" in 글
    assert "×2 portfolio: 005930" in 글
    assert "SVA" not in 글, "한 번 난 경고는 그날 알림이 말했다 — 되풀이만 싣는다"


def test_기업행위_알림_뒤_수정주가가_안_돌았으면_종목을_말한다() -> None:
    경고 = '기업행위로 보이는 종목 2개: 007110, 005930(정지 뒤 재개, 앞 종가 1000). **국내 수정주가를 다시 내야 합니다** — Actions → "국내 수정주가"'
    runs = [_run("daily_kr", "partial", "2026-10-02T02:22:28+00:00", 경고)]
    assert ws.adjust_needed(runs) == ["007110", "005930"]
    assert "다시 내야 할 종목 2개: 007110, 005930" in ws.compose(runs, {}, 지금, None)
    # 그 뒤 수정주가가 돌았으면 빼고, 돌렸다는 사실만 말한다
    runs.append(_run("adjust_kr", "success", "2026-10-03T14:58:11+00:00"))
    assert ws.adjust_needed(runs) == []
    assert "이번 주 1번 돌렸고" in ws.compose(runs, {}, 지금, None)
    # 수정주가가 알림보다 **앞에** 돌았으면 그 알림은 아직 남은 것이다
    assert ws.adjust_needed([_run("adjust_kr", "success", "2026-10-01T00:00:00+00:00"), runs[0]]) == ["007110", "005930"]


def test_기대_주기를_넘긴_작업과_한_번도_안_돈_작업() -> None:
    last = {j: "2026-10-04T00:00:00+00:00" for j in ws.EXPECTED_DAYS}
    last["backup"] = "2026-09-20T00:00:00+00:00"  # 14일 전 > 10
    del last["backtest"]
    묵은 = dict(ws.stale_jobs(last, 지금))
    assert 묵은 == {"backup": 14, "backtest": None}
    글 = ws.compose([], last, 지금, None)
    assert "backup: 마지막 실행 14일 전 (기대 10일 안)" in 글 and "backtest: 돈 기록 없음" in 글


def test_부분_성공도_돈_것이다() -> None:
    """일일 배치는 경고 하나로 거의 매일 partial 이다 — 성공만 세면 매일 돈 작업이 멈춘 것으로 실린다 (25.946 첫 실행에서 겪음)."""
    src = (뿌리 / "batch/jobs/weekly_summary.py").read_text(encoding="utf-8")
    assert "status IN ('success', 'partial')" in src


def test_읽기_줄은_진도와_견준다() -> None:
    # 10월 4일 = 달의 13%. 12% 는 괜찮고 30% 는 앞선다
    assert ws.reads_line(61_000_000, 500_000_000, 지금) == "Turso 월 읽기: 61.0M / 500M (12%, 달 진도 13%)"
    assert ws.reads_line(150_000_000, 500_000_000, 지금).endswith("⚠ 진도보다 앞섭니다")
    assert ws.reads_line(None, 500_000_000, 지금) == "Turso 월 읽기: 재지 못했습니다"


def test_한_조각에_들어간다() -> None:
    """절마다 줄 수를 자르므로 작업이 많아도 텔레그램 한 조각(3,900자)을 넘지 않는다."""
    runs = [_run(f"job{i}", "failed", "2026-10-02T00:00:00+00:00", *[f"경고 {i} 번 {k}" for k in range(3)], error=f"오류 {i}")
            for i in range(40)]  # fmt: skip
    글 = ws.compose(runs, {}, 지금, 1)
    assert len(글) < 3_900


# ----------------------------------------------------------------------
# 예약 워크플로의 작업 이름이 기대 주기 표에 있는가
# ----------------------------------------------------------------------

뿌리 = Path(__file__).resolve().parent.parent
_모듈 = re.compile(r"python -m batch\.jobs\.([a-z_]+)")
#: 예약은 있지만 `batch_runs` 에 그 이름으로 남지 않거나 주기 표에 넣지 않기로 한 것과 **왜**
표에_없는_이유 = {
    "d1-catchup.yml": "제 일이 없다 — 굶은 다른 작업을 대신 돌린다(25.20). 그 작업들의 이름으로 남는다",
    "turso-return.yml": "Turso 가 풀릴 때까지 아무것도 안 하는 것이 정상이다(25.12)",
    "backfill-kr.yml": "구멍이 없으면 안 돌아도 정상이다 — 주기가 없다",
    "backfill-us.yml": "위와 같다",
    "refresh-us-adjusted.yml": "대기열이 비면 안 돈다 — 주기가 없다",
    "us-shares.yml": "미국 유니버스의 재료. 분기마다라 7일 창의 요약에 넣을 뜻이 없다(handoff 4.1)",
    "disclosures-us.yml": "D1 운영 중 쉰다(25.14). 신선도는 /status 가 본다",
    "insider-kr.yml": "근거표 참고 행뿐이라 점수·신호를 바꾸지 않는다",
    "weekly-summary.yml": "이 요약 자신이다",
    "restore-drill.yml": "분기 1회 리허설 — 7일 창의 요약에 넣을 뜻이 없다. /status 신선도 칸이 본다 (25.946)",
    "tests.yml": "코드 검사. 운영 작업이 아니다",
    "tests-web.yml": "위와 같다",
}


def test_예약_워크플로의_작업이_기대_주기_표에_있다() -> None:
    from tests.test_schedule_visible import 예약_워크플로

    빠진 = []
    for 파일 in 예약_워크플로():
        if 파일 in 표에_없는_이유:
            continue
        글 = (뿌리 / ".github/workflows" / 파일).read_text(encoding="utf-8")
        줄기 = 파일.removesuffix(".yml").replace("-", "_")  # daily-kr → daily_kr
        모듈들 = set(_모듈.findall(글)) or {줄기}
        # 워크플로 안의 모듈 가운데 **하나라도** 표에 있으면 된다 — 일일 배치(`batch.jobs.daily`)는
        # `daily_kr`·`daily_us` 로 남는다
        if not any(m in ws.EXPECTED_DAYS or (m == "daily" and 줄기 in ws.EXPECTED_DAYS) for m in 모듈들):
            빠진.append((파일, sorted(모듈들)))
    assert not 빠진, f"예약은 있는데 주간 요약의 기대 주기 표(EXPECTED_DAYS)에 없다 — 넣거나 `표에_없는_이유` 에 사유를 적어라: {빠진}"


def test_사유_목록이_낡지_않았다() -> None:
    있는 = {p.name for p in (뿌리 / ".github/workflows").glob("*.yml")}
    assert set(표에_없는_이유) <= 있는


@pytest.mark.parametrize("job", sorted(ws.EXPECTED_DAYS))
def test_기대_주기는_주기보다_넉넉하다(job: str) -> None:
    assert ws.EXPECTED_DAYS[job] >= 4, "거래일 작업도 주말·휴장을 넘겨야 한다"


def test_일일_배치가_바로_다시_낸_종목은_말하지_않는다() -> None:
    """25.1009 의 바로 다시 내기 문장에도 걸려 끝난 종목을 매주 다시 돌리라 했다 (docs/infra.md 25.1270, 리포트 감사 재현)."""
    from batch.jobs import daily

    바로 = "기업행위로 보이는 종목 2개: 111110, 222220. 그 종목만 국내 수정주가를 바로 다시 냈습니다(바뀐 행 3)"
    assert ws.바로_냄 in 바로 and ws.바로_냄 in __import__("inspect").getsource(daily._detect_kr_actions)
    runs = [_run("daily_kr", "partial", "2026-10-02T02:22:28+00:00", 바로)]
    assert ws.adjust_needed(runs) == []


def test_머리_날짜는_한국_날짜이고_확인_실행은_미완이_아니다() -> None:
    """UTC 날짜로 하루 늦었고, 손으로 돌린 dryrun 이 "미완" 으로 세었다 (docs/infra.md 25.1274, 리포트 감사 재현)."""
    runs = [_run("daily_kr", "success", "2026-10-08T00:00:00+00:00"), _run("daily_kr", "dryrun", "2026-10-09T05:00:00+00:00"),
            _run("daily_kr", "failed", "2026-10-09T00:00:00+00:00")]  # fmt: skip
    글 = ws.compose(runs, {j: "2026-10-10T00:00:00+00:00" for j in ws.EXPECTED_DAYS}, 지금, None)
    assert 글.startswith("[주간 운영 요약] 2026-09-28 ~ 2026-10-05")  # 지금 = 10-04 22:41 UTC = 10-05 07:41 KST
    assert "daily_kr: 성공 1 · 실패 1 · 확인 실행 1" in 글 and "미완" not in 글


def test_국내_재무는_주_1회_기준으로_본다() -> None:
    """월 1회 칸(40일)에 있어 다섯 번 내리 빠져도 몰랐다 (docs/infra.md 25.1285, 재무 감사 재현)."""
    from pathlib import Path

    wf = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "financials.yml"
    assert "* * 0" in wf.read_text(encoding="utf-8")  # 주 1회 예약
    assert ws.EXPECTED_DAYS["financials"] <= 10


def test_배당은_연_2회라_가을에_묵음이_아니다() -> None:
    """dividends.yml 은 4·5월 5일만 돈다 — "기대 20일" 이라 한 해 열한 달 묵음으로 실렸다 (25.1371, 주간 요약 감사)."""
    last = {j: "2026-10-04T00:00:00+00:00" for j in ws.EXPECTED_DAYS}
    last["dividends"] = "2026-05-05T19:00:00+00:00"
    assert "dividends" not in dict(ws.stale_jobs(last, 지금))
    # 한 해를 넘기면(4·5월 실행이 둘 다 빠짐) 묵음이다
    last["dividends"] = "2025-09-01T00:00:00+00:00"
    assert "dividends" in dict(ws.stale_jobs(last, 지금))
    cron = (뿌리 / ".github/workflows/dividends.yml").read_text(encoding="utf-8")
    assert '"41 18 5 4,5 *"' in cron  # 일정이 바뀌면 기대 주기도 다시 본다
