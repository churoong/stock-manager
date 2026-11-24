"""운영 사고 대장 — 실패 묶음과 복구 시간 (docs/health.md 7.3, docs/infra.md 25.2289)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

from batch.jobs import weekly_summary as ws
from batch.services import incident_ledger as il

뿌리 = Path(__file__).resolve().parent.parent


def _r(job: str, status: str, when: str, market: str | None = "KR", err: str | None = None) -> ws.Run:
    return ws.Run(job, market, status, f"2026-11-{when}:00+00:00", err, ())


def test_실패_묶음_하나가_사고_한_건이고_다음_성공이_닫는다() -> None:
    runs = [
        _r("daily_kr", "success", "16T23:30"),
        _r("daily_kr", "failed", "17T23:30", err="TursoError: stream closed\n둘째 줄"),
        _r("daily_kr", "failed", "18T00:10", err="KeyError: 'x'"),
        _r("daily_kr", "skipped", "18T00:20"),  # 건너뜀은 닫지 않는다
        _r("daily_kr", "partial", "18T00:35"),
        _r("daily_kr", "success", "18T23:30"),
    ]
    found = il.incidents(runs)
    assert found == [{"job": "daily_kr", "market": "KR", "opened": "2026-11-17T23:30:00+00:00",
                      "closed": "2026-11-18T00:35:00+00:00", "failures": 2, "minutes": 65, "cause": "TursoError"}]  # fmt: skip


def test_작업과_시장마다_따로이고_차례에_기대지_않는다() -> None:
    runs = [
        _r("daily_us", "failed", "17T13:30", "US", "X: 1"),
        _r("daily_kr", "failed", "17T23:30", "KR", "Y: 2"),
        _r("daily_kr", "success", "18T00:30", "KR"),  # 국내 성공은 미국 사고를 닫지 않는다
        _r("scores", "failed", "18T01:00", None, None),
        _r("daily_us", "success", "19T13:30", "US"),
        _r("daily_kr", "failed", "20T23:30", "KR", "Z: 3"),  # 닫힌 뒤 다시 실패 — 새 사고
    ]
    뒤집힘 = il.incidents(runs[::-1])
    assert 뒤집힘 == il.incidents(runs)
    assert [(x["job"], x["market"], x["minutes"], x["cause"]) for x in 뒤집힘] == [
        ("daily_us", "US", 2880, "X"), ("daily_kr", "KR", 60, "Y"), ("scores", None, None, "까닭 없음"),
        ("daily_kr", "KR", None, "Z")]  # fmt: skip
    assert [x["closed"] is None for x in 뒤집힘] == [False, False, True, True]


def test_도는_중과_확인_실행은_열지도_닫지도_않는다() -> None:
    runs = [_r("daily_kr", "running", "17T23:00"), _r("daily_kr", "dryrun", "17T23:10"),
            _r("daily_kr", "failed", "17T23:30", err="E: 1"), _r("daily_kr", "dryrun", "17T23:40"),
            _r("daily_kr", "running", "17T23:50")]  # fmt: skip
    found = il.incidents(runs)
    assert len(found) == 1 and found[0]["closed"] is None and found[0]["failures"] == 1
    assert il.incidents([_r("daily_kr", "success", "17T23:30"), _r("daily_kr", "skipped", "18T23:30")]) == []
    assert il.OPEN_STATUS == ("failed",) and il.CLOSE_STATUS == ("success", "partial")


def test_원인은_머리만_싣는다() -> None:
    assert il._갈래("RuntimeError: 계좌 12345678 을 못 읽음") == "RuntimeError"
    assert il._갈래(None) == "까닭 없음" and il._갈래("  ") == "까닭 없음"
    assert il._갈래("가" * 100) == "가" * il.CAUSE_MAX == "가" * 40
    assert il._갈래("첫 줄\n둘째 줄: 본문") == "첫 줄"


def test_시각을_못_읽으면_복구_시간만_모름이다() -> None:
    runs = [ws.Run("x", None, "failed", "가 — 모르는 때", "E: 1", ()), ws.Run("x", None, "success", "나 — 그 뒤", None, ())]
    assert il.incidents(runs)[0]["minutes"] is None and il.incidents(runs)[0]["closed"] == "나 — 그 뒤"
    # 시간대가 섞여도 죽지 않는다
    섞임 = [ws.Run("x", None, "failed", "2026-11-17T23:30:00", "E: 1", ()),
            ws.Run("x", None, "success", "2026-11-18T00:30:00+00:00", None, ())]  # fmt: skip
    assert il.incidents(섞임)[0]["minutes"] is None


def test_요약_줄() -> None:
    assert il.lines([]) == ["운영 사고(실패 묶음 → 복구): 0건"]
    found = il.incidents([
        _r("daily_kr", "failed", "17T23:30", "KR", "TursoError: x"), _r("daily_kr", "success", "18T00:35", "KR"),
        _r("daily_us", "failed", "17T13:30", "US", "KisFailed: y"), _r("daily_us", "success", "19T13:30", "US"),
        _r("scores", "failed", "18T01:00", None, "E: z"), _r("scores", "success", "18T01:20", None),
        _r("signals", "failed", "20T01:00", None, "Open: w"),
        _r("metrics", "failed", "16T01:00", None, "M: v"), _r("metrics", "success", "16T04:00", None),
    ])  # fmt: skip
    줄 = il.lines(found)
    # 닫힌 넷: 20·65·180·2880분 → 중앙값(아래쪽) 65분, 가장 긴 것 2일
    assert 줄[0] == "운영 사고(실패 묶음 → 복구): 5건 · 복구 중앙값 65분 · 가장 긴 것 2.0일 · 아직 열림 1건"
    assert 줄[1] == "  signals: 2026-11-20T01:00 부터 실패 1번 · 아직 복구 안 됨 · Open"  # 열린 것이 맨 앞
    assert 줄[2] == "  daily_us(US): 2026-11-17T13:30 부터 실패 1번 · 복구 2.0일 · KisFailed"
    assert 줄[3] == "  metrics: 2026-11-16T01:00 부터 실패 1번 · 복구 3.0시간 · M"
    assert 줄[4] == "  … 그 밖 2건" and len(줄) == 5 and il.TOP_N == 3
    assert il._분(119) == "119분" and il._분(120) == "2.0시간" and il._분(2879) == "48.0시간" and il._분(2880) == "2.0일"


def test_주간_요약에_실린다() -> None:
    now = datetime(2026, 11, 21, 22, 41, tzinfo=UTC)
    runs = [_r("daily_kr", "failed", "17T23:30", "KR", "TursoError: x"), _r("daily_kr", "success", "18T00:35", "KR")]
    글 = ws.compose(runs=runs, last_success={}, now=now, reads_used=None,
                   incident_lines=il.lines(il.incidents(runs)))  # fmt: skip
    assert "운영 사고(실패 묶음 → 복구): 1건 · 복구 중앙값 65분 · 가장 긴 것 65분" in 글
    assert "daily_kr(KR): 2026-11-17T23:30 부터 실패 1번 · 복구 65분 · TursoError" in 글
    # 안 주면 절이 없다(예전 요약 그대로)
    assert "운영 사고" not in ws.compose(runs=runs, last_success={}, now=now, reads_used=None)
    # 이음매 — 요약 실행이 대장을 만들어 넘긴다
    assert "incident_lines=incident_ledger.lines(incident_ledger.incidents(runs))" in inspect.getsource(ws.run)


def test_읽지도_쓰지도_않는다_그리고_문서() -> None:
    글 = inspect.getsource(il)
    for 금지 in ("execute", "client", "SELECT", "INSERT", "UPDATE"):
        assert 금지 not in 글, 금지
    문서 = (뿌리 / "docs" / "health.md").read_text()
    for 조각 in ("### 7.3 운영 사고 대장", "25.2289", f"{il.CAUSE_MAX}자 `CAUSE_MAX`", f"긴 것부터 {il.TOP_N}건(`TOP_N`)", "새 표를 만들지 않는다",
               "건너뜀·도는 중·확인 실행은 사고를 열지도 닫지도 않는다", "아직 열림"):  # fmt: skip
        assert 조각 in 문서, 조각
